"""Released results from DKU OJ (contract §4 results), stored per attempt (external_result).

Polled for every DKU OJ exam whose end time has passed (and for RESULTS_FOR after it,
so regrades and withdrawn releases arrive too). A withdrawn release comes back with
"released": false and hides the result again.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import false, select

from ...models import Attempt, Exam, User
from ...timeutil import parse_iso
from ..sanitize import clean_markdown
from .client import DkuojClient

log = logging.getLogger("proctora.dkuoj")

MAX_PAGES = 100


def _time(v):
    try:
        return parse_iso(v) if isinstance(v, str) and v else None
    except ValueError:
        return None


def result_of(r: dict) -> dict:
    qs = []
    for q in r.get("questions") or []:
        if isinstance(q, dict) and isinstance(q.get("question_id"), str):
            try:
                pts = float(q.get("points") or 0)
            except (TypeError, ValueError):
                pts = 0.0
            qs.append({"question_id": q["question_id"], "points": pts,
                       # security review 2026-10-24: cleaned on arrival (app.integrations.sanitize)
                       "feedback_md": clean_markdown(str(q.get("feedback_md") or "")[:20000])})

    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    return {"released": bool(r.get("released")), "total": f(r.get("total")),
            "total_points": f(r.get("total_points")), "updated_at": r.get("updated_at"), "questions": qs}


async def sync_results(sessionmaker, client: DkuojClient, now: datetime) -> int:
    from .sync import RESULTS_FOR
    async with sessionmaker() as session:
        exams = (await session.execute(select(Exam.id, Exam.external_key, Exam.external_meta).where(
            Exam.source == "dkuoj", Exam.external_removed == false()))).all()
    stored = 0
    for exam_id, key, meta in exams:
        meta = meta or {}
        end = _time(meta.get("end_time"))
        if not key or end is None or now < end or now > end + RESULTS_FOR:
            continue
        stored += await _sync_exam(sessionmaker, client, exam_id, key, meta.get("results_cursor"))
    return stored


async def _sync_exam(sessionmaker, client: DkuojClient, exam_id: str, key: str, cursor: str | None) -> int:
    newest, n, page = cursor, 0, 1
    server_time = None
    for _ in range(MAX_PAGES):
        data = await client.results(key, cursor, page)
        if server_time is None and _time(data.get("server_time")):
            server_time = data["server_time"]     # DKU OJ: changed_since is strictly later
        rows = [r for r in data.get("results") or [] if isinstance(r, dict) and isinstance(r.get("username"), str)]
        if rows:
            async with sessionmaker() as session:
                # by NetID only (v2 §4): an account without one is never matched
                names = [r["username"] for r in rows]
                users = dict((await session.execute(select(User.netid, User.id).where(
                    User.netid.in_(names)))).all())
                for r in rows:
                    uid = users.get(r["username"])
                    if uid is None:
                        continue
                    a = (await session.execute(select(Attempt).where(
                        Attempt.exam_id == exam_id, Attempt.student_id == uid))).scalar_one_or_none()
                    if a is None:
                        continue
                    res = result_of(r)
                    a.external_result = res
                    # the stored score feeds the teacher's lists and gradebook
                    a.score = res["total"] if res["released"] else None
                    a.needs_grading = 0
                    n += 1
                    up = r.get("updated_at")
                    if _time(up) and (newest is None or _time(up) > (_time(newest) or datetime.min)):
                        newest = up
                await session.commit()
        nxt = data.get("next_page")
        if not nxt:
            break
        page = int(nxt) if str(nxt).isdigit() else page + 1
    if server_time is not None:
        newest = server_time          # the next poll asks for what changed after this one
    if newest != cursor:
        async with sessionmaker() as session:
            e = await session.get(Exam, exam_id)
            if e is not None:
                e.external_meta = {**(e.external_meta or {}), "results_cursor": newest}
                await session.commit()
    return n

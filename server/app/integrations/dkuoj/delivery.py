"""Outbox: hand-ins of DKU OJ exams sent to DKU OJ (contract §4 submissions, §5 outbox).

A row is written in the same transaction as the hand-in (submit, sealed submit, close).
The worker sends due rows, throttled to DKUOJ_DELIVERY_RPS: 200 -> sent (receipt kept);
409 / 422 (and other refusals) -> dead + a teacher-visible event; 5xx / 429 / timeout ->
failed, retried with backoff 30 s, 1, 2, 4, 8 min, then every 15 min, for 14 days. The
student's hand-in receipt never waits for DKU OJ.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ...models import Attempt, Exam, User
from ...timeutil import iso, utcnow
from .adapter import answer_to_oj
from .client import DkuojClient, OjError, sleep
from ..models import ExternalDelivery as OjDelivery
from ...models import AnswerFile

log = logging.getLogger("proctora.dkuoj")

BACKOFF_FIRST = timedelta(seconds=30)
BACKOFF_MAX = timedelta(minutes=15)
GIVE_UP_AFTER = timedelta(days=14)
BATCH = 50


def backoff(tries: int) -> timedelta:
    """Wait after the n-th failed try (1-based): 30 s, 60 s, 120 s ... at most 15 min."""
    return min(BACKOFF_FIRST * (2 ** max(0, tries - 1)), BACKOFF_MAX)


class Throttle:
    """At most `rps` requests a second from this worker."""

    def __init__(self, rps: int):
        self.gap = 1.0 / max(1, int(rps or 10))
        self.last = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        if self.last and now - self.last < self.gap:
            await sleep(self.gap - (now - self.last))
        self.last = time.monotonic()


def report_url(cfg, exam_id: str, aid: str) -> str:
    """The teacher page of one student's attempt (answers, integrity events)."""
    path = f"/teacher/exams/{exam_id}/submissions/{aid}"
    return (cfg.public_url + path) if getattr(cfg, "public_url", "") else path


async def _payload(session: AsyncSession, client: DkuojClient, throttle: Throttle, cfg,
                   d: OjDelivery, a: Attempt, e: Exam, username: str) -> dict:
    """The submission body, built once and kept on the row (every retry sends the same
    bytes). File answers are uploaded first; each DKU OJ upload id is stored as soon as
    it is known, so a retry never uploads the same file twice."""
    if d.payload:
        return d.payload
    questions = e.questions or []
    answers = a.answers or {}
    uploads = dict(d.uploads or {})
    for q in questions:
        if q.get("type") != "file_upload":
            continue
        for fid in answers.get(q["id"]) or []:
            if not isinstance(fid, str) or fid in uploads:
                continue
            f = (await session.execute(select(AnswerFile).options(undefer(AnswerFile.data))
                                       .where(AnswerFile.id == fid))).scalar_one_or_none()
            if f is None or f.attempt_id != a.id:
                continue
            await throttle.wait()
            uploads[fid] = await client.upload(e.external_key, username, (q.get("external_ref") or {}).get("question_id"),
                                               f.name, f.data, f.sha256, attempts=1)
            d.uploads = dict(uploads)
            await session.commit()
    out_answers = []
    for q in questions:
        item = answer_to_oj(q, answers.get(q["id"]), uploads)
        if item is not None:
            out_answers.append(item)
    body = {
        "username": username, "attempt_id": a.id,
        "package_version": e.external_frozen_version or e.external_version,
        "started_at": _z(a.started_at), "deadline_at": _z(a.deadline_at),
        "handed_in_at": _z(a.submitted_at or a.updated_at),
        "handed_in_evidence": "sealed" if a.sealed_proof else "server",
        "answers": out_answers,
        "integrity_report_url": report_url(cfg, e.id, a.id),
    }
    d.payload = body
    await session.commit()
    return body


def _z(dt) -> str | None:
    """ISO-8601 UTC with Z, whole seconds (contract §4)."""
    return dt.replace(microsecond=0).isoformat() + "Z" if dt is not None else None


async def deliver_due(sessionmaker, client: DkuojClient, cfg, now=None, limit: int = BATCH,
                      throttle: Throttle | None = None) -> dict:
    """Send every due DKU OJ row (pending / failed with next_at <= now). Returns counts."""
    now = now or utcnow()
    throttle = throttle or Throttle(cfg.delivery_rps)
    counts = {"sent": 0, "failed": 0, "dead": 0}
    async with sessionmaker() as session:
        ids = (await session.execute(
            select(OjDelivery.id).where(OjDelivery.source == "dkuoj",
                                        or_(OjDelivery.status == "pending", OjDelivery.status == "failed"),
                                        OjDelivery.next_at <= now)
            .order_by(OjDelivery.next_at, OjDelivery.id).limit(limit))).scalars().all()
    for did in ids:
        async with sessionmaker() as session:
            outcome = await _deliver_one(session, client, throttle, cfg, did, now)
        if outcome:
            counts[outcome] += 1
    return counts


async def _deliver_one(session: AsyncSession, client: DkuojClient, throttle: Throttle, cfg,
                       did: int, now) -> str | None:
    d = await session.get(OjDelivery, did)
    if d is None or d.status not in ("pending", "failed"):
        return None
    a = await session.get(Attempt, d.attempt_id)
    e = await session.get(Exam, d.exam_id)
    if a is None or e is None or not e.external_key:
        return await _dead(session, d, "the attempt or exam is gone", now)
    username = (await session.execute(select(User.username).where(User.id == a.student_id))).scalar()
    try:
        body = await _payload(session, client, throttle, cfg, d, a, e, username or "")
        await throttle.wait()
        resp = await client.submit(e.external_key, body, attempts=1)
    except OjError as err:
        detail = _err_text(err)
        if err.retryable:
            d.tries = int(d.tries or 0) + 1
            if now - d.created_at > GIVE_UP_AFTER:
                return await _dead(session, d, f"gave up after 14 days: {detail}", now)
            d.status, d.last_error = "failed", detail
            d.next_at = now + backoff(d.tries)
            await session.commit()
            log.warning("DKU OJ delivery of %s failed (try %d): %s", d.attempt_id, d.tries, detail)
            return "failed"
        d.tries = int(d.tries or 0) + 1
        return await _dead(session, d, detail, now)
    d.tries = int(d.tries or 0) + 1
    d.status, d.sent_at, d.last_error = "sent", now, None
    d.receipt = str(resp.get("receipt") or "")[:200] or None
    await session.commit()
    return "sent"


def _err_text(err: OjError) -> str:
    detail = err.body.get("detail") if isinstance(err.body, dict) else None
    qid = err.body.get("question_id") if isinstance(err.body, dict) else None
    text = err.message + (f": {detail}" if detail else "") + (f" (question {qid})" if qid else "")
    return text[:500]


async def _dead(session: AsyncSession, d: OjDelivery, detail: str, now) -> str:
    """Admin alert: logged as an error and shown to the exam's teachers as an event of the
    attempt (it stays open in their review list until looked at)."""
    d.status, d.last_error, d.next_at = "dead", detail[:500], now
    await session.commit()
    log.error("DKU OJ delivery of attempt %s is dead: %s", d.attempt_id, detail)
    try:
        from ... import app_client as ac
        from ...store import get_store
        if await session.get(Attempt, d.attempt_id) is not None:
            await ac.add_event(session, get_store(), d.attempt_id, d.exam_id, "external_delivery_failed",
                               f"the answers could not be sent to DKU OJ: {detail}"[:500])
    except Exception:  # noqa: BLE001 - the alert must not break the worker
        log.exception("could not record the delivery alert")
    return "dead"


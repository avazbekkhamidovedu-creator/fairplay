"""Outbox: hand-ins of DKU OJ exams sent to DKU OJ (contract §4 submissions, §5 outbox).

A row is written in the same transaction as the hand-in (submit, sealed submit, close).
The worker sends due rows, throttled to DKUOJ_DELIVERY_RPS: 200 -> sent (receipt kept);
409 / 422 (and other refusals) -> dead + a teacher-visible event; 5xx / 429 / timeout ->
failed, retried with backoff 30 s, 1, 2, 4, 8 min, then every 15 min, for 14 days. The
student's hand-in receipt never waits for DKU OJ. 401 / 403 / 404 (clock skew, token
rotation mismatch) are retried the same way for DKUOJ_AUTH_RETRY_HOURS (6), then dead. Every
worker process runs the loop, so a row is claimed first with a lease (claimed_by, locked_until);
a crashed worker's lease runs out and the row is picked up again.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ...models import Attempt, Exam, User
from ...services import exam_closes_at
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
LEASE = timedelta(minutes=5)


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
        revision = int(d.revision or 1)
        supersedes = d.payload.get("supersedes")
        if (d.status in ("pending", "failed") and revision > 1
                and d.payload.get("revision") == revision and d.payload.get("attempt_id") == a.id
                and type(supersedes) is int and supersedes == revision - 1):
            d.payload = {**d.payload, "supersedes": a.id}
            await session.commit()
        return d.payload
    questions = e.questions or []
    snap = d.snapshot or {}
    answers = snap["answers"] if snap else (a.answers or {})
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
        "package_version": e.external_version,
        "started_at": _snap_z(snap, "started_at", a.started_at),
        "deadline_at": _snap_z(snap, "deadline_at", a.deadline_at),
        "handed_in_at": _snap_z(snap, "handed_in_at", a.submitted_at or a.updated_at),
        "handed_in_evidence": snap.get("evidence") or ("sealed" if a.sealed_proof else "server"),
        "answers": out_answers,
        "integrity_report_url": report_url(cfg, e.id, a.id),
        # 2026-10-24 (v2 §11): when the exam closes in FairPlay (DKU OJ's end time + the minutes
        # added). DKU OJ judges lateness by its own count of added minutes and uses this only
        # when it has lost that count (bounded there).
        "exam_closes_at": _z(exam_closes_at(e)),
    }
    if int(d.revision or 1) > 1:
        # a reopened attempt handed in again: this one counts, the earlier one stays on file
        body["revision"] = int(d.revision)
        body["supersedes"] = a.id
    d.payload = body
    await session.commit()
    return body


def _snap_z(snap: dict, name: str, live) -> str | None:
    from ...timeutil import parse_iso
    return _z(parse_iso(snap[name])) if snap.get(name) else _z(live)


def _z(dt) -> str | None:
    """ISO-8601 UTC with Z, whole seconds (contract §4)."""
    return dt.replace(microsecond=0).isoformat() + "Z" if dt is not None else None


def _due(now):
    return (OjDelivery.source == "dkuoj", or_(OjDelivery.status == "pending", OjDelivery.status == "failed"),
            OjDelivery.next_at <= now,
            or_(OjDelivery.locked_until.is_(None), OjDelivery.locked_until <= now))


async def claim_due(sessionmaker, now, limit: int = BATCH, worker: str | None = None,
                    lease: timedelta = LEASE) -> list[int]:
    """Lease up to `limit` due rows to `worker`; no row is handed to two claimers."""
    worker = worker or uuid.uuid4().hex[:16]
    until = now + lease
    async with sessionmaker() as session:
        if session.bind.dialect.name == "postgresql":
            ids = (await session.execute(
                select(OjDelivery.id).where(*_due(now)).order_by(OjDelivery.next_at, OjDelivery.id)
                .limit(limit).with_for_update(skip_locked=True))).scalars().all()
            if ids:
                await session.execute(update(OjDelivery).where(OjDelivery.id.in_(ids))
                                      .values(locked_until=until, claimed_by=worker))
            await session.commit()
            return list(ids)
        cand = (await session.execute(
            select(OjDelivery.id).where(*_due(now)).order_by(OjDelivery.next_at, OjDelivery.id)
            .limit(limit))).scalars().all()
        got = []
        for did in cand:
            res = await session.execute(update(OjDelivery).where(OjDelivery.id == did, *_due(now))
                                        .values(locked_until=until, claimed_by=worker))
            await session.commit()
            if res.rowcount == 1:
                got.append(did)
        return got


async def deliver_due(sessionmaker, client: DkuojClient, cfg, now=None, limit: int = BATCH,
                      throttle: Throttle | None = None) -> dict:
    """Send every due DKU OJ row (pending / failed with next_at <= now). Returns counts."""
    now = now or utcnow()
    throttle = throttle or Throttle(cfg.delivery_rps)
    counts = {"sent": 0, "failed": 0, "dead": 0}
    worker = uuid.uuid4().hex[:16]
    ids = await claim_due(sessionmaker, now, limit, worker)
    for did in ids:
        async with sessionmaker() as session:
            outcome = await _deliver_one(session, client, throttle, cfg, did, now, worker)
        if outcome:
            counts[outcome] += 1
    return counts


async def _deliver_one(session: AsyncSession, client: DkuojClient, throttle: Throttle, cfg,
                       did: int, now, worker: str | None = None) -> str | None:
    d = await session.get(OjDelivery, did)
    if d is None or d.status not in ("pending", "failed"):
        return None
    if worker is not None and (d.claimed_by != worker or d.locked_until is None or d.locked_until <= now):
        return None
    if int(d.revision or 1) > 1 and (await session.execute(select(OjDelivery.id).where(
            OjDelivery.attempt_id == d.attempt_id, OjDelivery.revision < d.revision,
            OjDelivery.status.in_(("pending", "failed"))).limit(1))).first() is not None:
        d.next_at, d.locked_until, d.claimed_by = now + BACKOFF_FIRST, None, None     # after the earlier one
        await session.commit()
        return None
    a = await session.get(Attempt, d.attempt_id)
    e = await session.get(Exam, d.exam_id)
    if a is None or e is None or not e.external_key:
        return await _dead(session, d, "the attempt or exam is gone", now)
    # DKU OJ knows the student by NetID (v2 §4); the FairPlay username is their own choice
    row = (await session.execute(select(User.netid, User.username).where(User.id == a.student_id))).first()
    if row is None or not row.netid:
        return await _dead(session, d, "the student's account has no NetID, so DKU OJ cannot match the hand-in "
                                       "(link the account to its NetID, then resend)", now)
    try:
        body = await _payload(session, client, throttle, cfg, d, a, e, row.netid)
        await throttle.wait()
        resp = await client.submit(e.external_key, body, attempts=1)
    except OjError as err:
        detail = _err_text(err)
        d.last_status = err.status
        if err.retryable or err.auth_class:
            d.tries = int(d.tries or 0) + 1
            if now - d.created_at > GIVE_UP_AFTER:
                return await _dead(session, d, f"gave up after 14 days: {detail}", now)
            if err.auth_class:
                if d.auth_failed_at is None:
                    d.auth_failed_at = now
                hours = max(1, int(getattr(cfg, "auth_retry_hours", 6) or 6))
                if now - d.auth_failed_at > timedelta(hours=hours):
                    return await _dead(session, d, f"refused for {hours} hours: {detail}", now)
            d.status, d.last_error = "failed", detail
            d.next_at = now + backoff(d.tries)
            d.locked_until = d.claimed_by = None
            await session.commit()
            log.warning("DKU OJ delivery of %s failed (try %d): %s", d.attempt_id, d.tries, detail)
            return "failed"
        d.tries = int(d.tries or 0) + 1
        return await _dead(session, d, detail, now)
    d.tries = int(d.tries or 0) + 1
    d.status, d.sent_at, d.last_error, d.last_status = "sent", now, None, 200
    d.locked_until = d.claimed_by = None
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
    d.locked_until = d.claimed_by = None
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


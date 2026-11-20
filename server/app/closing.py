"""Closing an exam: the teacher's Close and the automatic close at the exam's end time
share one path (close_exam_now). 2026-10-23.

Automatic close: an exam may have a close time (exam_closes_at: a FairPlay exam's own
setting, or what its external source says, e.g. the synced end time). Every attempt's
deadline is capped at close time + the student's extra time (routes_student.start). A
background loop closes a live exam once close time + the largest extra time + LATE_GRACE
has passed (and every attempt still writing is past its own deadline + LATE_GRACE, so an
"Add 10 min" is honoured). One worker runs it at a time (Lease "autoclose"); the close
itself is a conditional UPDATE, so two runners, or a runner and the teacher, close once.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import exists, false, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .grading import handin_grade
from .integrations.registry import FAIRPLAY, integration_for
from .models import Attempt, Exam, Lease
from .services import attempt_key, exam_closes_at, invalidate_exam, new_receipt
from .store import get_store
from .timeutil import utcnow

log = logging.getLogger("fairplay.closing")

LATE_GRACE = timedelta(seconds=60)
AUTO_CLOSE_EVENT = "exam_closed_automatically"
LEASE_NAME = "autoclose"
LEASE_TTL = timedelta(seconds=60)
TICK_S = 15
# Pilot 0.8.8: an attempt still writing this long after ITS OWN deadline is handed in with what
# was saved (before, only the exam's close did that, so a student who left and never came back
# stayed "in progress" until the teacher closed the exam). The long wait leaves room for sealed
# answers an app uploads late (routes_sealed judges them by their sealed time) and for "Add time".
AUTO_SUBMIT_AFTER = timedelta(minutes=15)
AUTO_SUBMIT_BATCH = 200
DEADLINE_EVENT = "auto_submitted_deadline"
BACKGROUND = True            # tests switch the loop off and call run_once themselves


# ---------------------------------------------------------------- the one close path
async def close_exam_now(session: AsyncSession, e: Exam, now: datetime | None = None,
                         automatic: bool = False) -> bool:
    """Close `e` (live, or a paper draft for the teacher) and auto-submit everyone still
    writing. True = this call closed it; False = it was closed already (by someone else)."""
    now = now or utcnow()
    from .services import exam_delivery
    allowed = ["live"]
    if not automatic and exam_delivery(e) == "paper":
        allowed.append("draft")
    # Flip the exam first so no new attempts start, then auto-submit stragglers.
    res = await session.execute(
        update(Exam).where(Exam.id == e.id, Exam.status.in_(allowed)).values(
            status="closed", start_code=None, invigilator_code=None,
            package_secret=None,         # audit S-03: offline packages of this run stop opening
            closed_at=now, updated_at=now, auto_closed=automatic))
    await session.commit()
    await session.refresh(e)
    if res.rowcount != 1:
        return False
    store = get_store()
    await invalidate_exam(store, e.id)
    rows = await sweep_in_progress(session, e, now, automatic)
    # Audit S-07: a start that raced the status change commits its INSERT after the first
    # SELECT; one more pass catches it.
    rows += await sweep_in_progress(session, e, now, automatic)
    if rows:
        await store.delete(*[attempt_key(r[0]) for r in rows])
    return True


async def sweep_in_progress(session: AsyncSession, e: Exam, now, automatic: bool = False) -> list:
    """Auto-submit every in-progress attempt of a closed exam; returns the rows."""
    from . import app_client as ac
    integ = integration_for(e)
    rows = (await session.execute(
        select(Attempt.id, Attempt.answers, Attempt.deadline_at)
        .where(Attempt.exam_id == e.id, Attempt.status == "in_progress"))).all()
    swept = []
    for aid, answers, deadline in rows:
        score, pending = handin_grade(e, answers or {}, None)   # externally graded: not here
        # Conditional: a student submit racing with close keeps its own receipt.
        res = await session.execute(
            update(Attempt).where(Attempt.id == aid, Attempt.status == "in_progress").values(
                status="submitted", submitted_at=now, auto_submitted=True, receipt=new_receipt(),
                score=score, needs_grading=pending, late=now > deadline + LATE_GRACE,
                updated_at=now))
        if res.rowcount == 1:
            swept.append(aid)
            if integ is not None:        # e.g. outbox row, in this transaction
                await integ.on_handin(session, aid, e.id, now, "closed")
    await session.commit()
    if automatic:
        for aid in swept:
            await ac.add_event(session, get_store(), aid, e.id, AUTO_CLOSE_EVENT,
                               "handed in when the exam closed at its end time")
    return list(rows)


# ---------------------------------------------------------------- automatic close
async def max_extra(session: AsyncSession, e: Exam) -> timedelta:
    integ = integration_for(e)
    return await integ.max_extra_time(session, e.id) if integ is not None else timedelta(0)


async def auto_close_due(session: AsyncSession, e: Exam, now: datetime) -> bool:
    """Close time + the largest extra time + LATE_GRACE has passed, and nobody still
    writing has time left (deadline + LATE_GRACE)."""
    closes = exam_closes_at(e)
    if closes is None or e.status != "live":
        return False
    if now < closes + await max_extra(session, e) + LATE_GRACE:
        return False
    latest = (await session.execute(select(func.max(Attempt.deadline_at)).where(
        Attempt.exam_id == e.id, Attempt.status == "in_progress"))).scalar()
    return latest is None or now >= latest + LATE_GRACE


def _candidates():
    """Live exams that can have a close time: FairPlay exams with one set, external ones."""
    return (Exam.status == "live", Exam.is_trial == false(),
            or_(Exam.closes_at.is_not(None), Exam.source != FAIRPLAY))


# A close that stopped between the status flip and the hand-in (worker crash, database
# error) leaves attempts writing in a closed exam; the loop hands them in later. Only exams
# closed in the last LEFTOVER_WINDOW (cheap: the exam_id index), and not ones closing now.
LEFTOVER_WINDOW = timedelta(days=2)
LEFTOVER_AFTER = timedelta(seconds=30)


def _leftovers(now: datetime):
    return (Exam.status == "closed", Exam.is_trial == false(),
            Exam.closed_at.is_not(None), Exam.closed_at >= now - LEFTOVER_WINDOW,
            Exam.closed_at <= now - LEFTOVER_AFTER,
            exists().where(Attempt.exam_id == Exam.id, Attempt.status == "in_progress"))


def _expired(now: datetime):
    return (Attempt.status == "in_progress", Attempt.deadline_at <= now - AUTO_SUBMIT_AFTER)


async def sweep_expired_attempts(session: AsyncSession, now: datetime) -> list[str]:
    """Hand in attempts whose own deadline (+ AUTO_SUBMIT_AFTER) passed, with the saved answers,
    marked late and auto-submitted. Conditional UPDATE: a racing student submit keeps its receipt."""
    from . import app_client as ac
    rows = (await session.execute(
        select(Attempt.id, Attempt.exam_id, Attempt.answers, Attempt.deadline_at)
        .join(Exam, Exam.id == Attempt.exam_id)
        .where(*_expired(now), Exam.is_trial == false())
        .order_by(Attempt.deadline_at).limit(AUTO_SUBMIT_BATCH))).all()
    done: list[str] = []
    exams: dict[str, Exam | None] = {}
    for aid, exam_id, answers, _deadline in rows:
        if exam_id not in exams:
            exams[exam_id] = await session.get(Exam, exam_id)
        e = exams[exam_id]
        if e is None:
            continue
        score, pending = handin_grade(e, answers or {}, None)
        res = await session.execute(
            update(Attempt).where(Attempt.id == aid, Attempt.status == "in_progress",
                                  Attempt.deadline_at <= now - AUTO_SUBMIT_AFTER).values(
                status="submitted", submitted_at=now, auto_submitted=True, deadline_swept=True,
                receipt=new_receipt(), score=score, needs_grading=pending, late=True, updated_at=now))
        if res.rowcount == 1:
            done.append(aid)
            integ = integration_for(e)
            if integ is not None:
                await integ.on_handin(session, aid, e.id, now, "closed")
    await session.commit()
    store = get_store()
    for aid, exam_id, _a, _d in rows:
        if aid in done:
            await ac.add_event(session, store, aid, exam_id, DEADLINE_EVENT,
                               "handed in by the server: this student's own time ran out and nobody came back")
    if done:
        await store.delete(*[attempt_key(a) for a in done])
    return done


async def any_candidate(session: AsyncSession, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if (await session.execute(select(Attempt.id).where(*_expired(now)).limit(1))).first() is not None:
        return True
    if (await session.execute(select(Exam.id).where(*_candidates()).limit(1))).first() is not None:
        return True
    return (await session.execute(select(Exam.id).where(*_leftovers(now)).limit(1))).first() is not None


async def run_once(sessionmaker, now: datetime | None = None) -> list[str]:
    """Close every live exam whose automatic close is due. Returns the ids this call closed."""
    now = now or utcnow()
    closed = []
    async with sessionmaker() as session:
        candidates = (await session.execute(
            select(Exam).where(*_candidates()).order_by(Exam.id))).scalars().all()
        for e in candidates:
            try:
                if await auto_close_due(session, e, now) and await close_exam_now(session, e, now, automatic=True):
                    closed.append(e.id)
                    log.info("exam %s closed automatically", e.id)
            except Exception:  # noqa: BLE001 -- one exam's trouble never stops the others
                await session.rollback()
                log.exception("automatic close of %s", e.id)
        leftovers = (await session.execute(
            select(Exam).where(*_leftovers(now)).order_by(Exam.id))).scalars().all()
        for e in leftovers:
            try:
                rows = await sweep_in_progress(session, e, now, automatic=bool(e.auto_closed))
                if rows:
                    await get_store().delete(*[attempt_key(r[0]) for r in rows])
                    log.warning("exam %s: %d attempt(s) still writing after the close, handed in",
                                e.id, len(rows))
            except Exception:  # noqa: BLE001
                await session.rollback()
                log.exception("hand-in after the close of %s", e.id)
        try:
            handed = await sweep_expired_attempts(session, now)
            if handed:
                log.info("%d attempt(s) handed in after their own deadline", len(handed))
        except Exception:  # noqa: BLE001
            await session.rollback()
            log.exception("hand-in after the deadline")
    return closed


async def acquire_lease(session: AsyncSession, name: str, holder: str, now: datetime,
                        ttl: timedelta = LEASE_TTL) -> bool:
    """Take or renew a named lease. True = this worker holds it now."""
    res = await session.execute(
        update(Lease).where(Lease.name == name,
                            or_(Lease.holder == holder, Lease.expires_at.is_(None), Lease.expires_at < now))
        .values(holder=holder, expires_at=now + ttl))
    await session.commit()
    if res.rowcount == 1:
        return True
    session.add(Lease(name=name, holder=holder, expires_at=now + ttl))
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def release_lease(session: AsyncSession, name: str, holder: str) -> None:
    await session.execute(update(Lease).where(Lease.name == name, Lease.holder == holder)
                          .values(holder=None, expires_at=None))
    await session.commit()


async def run_forever() -> None:
    """Started from main.py's lifespan. Every TICK_S: if this worker holds the lease, close
    what is due."""
    from .db import get_database
    holder = f"{uuid.uuid4().hex[:12]}-{secrets.token_hex(4)}"
    try:
        while True:
            try:
                db = get_database()
                async with db.sessionmaker() as session:
                    # read first: most of the time nothing can close and nothing is written
                    mine = await any_candidate(session) and await acquire_lease(
                        session, LEASE_NAME, holder, utcnow())
                if mine:
                    await run_once(db.sessionmaker)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("automatic close loop")
            await asyncio.sleep(TICK_S)
    finally:
        try:
            async with get_database().sessionmaker() as session:
                await release_lease(session, LEASE_NAME, holder)
        except Exception:  # noqa: BLE001
            pass

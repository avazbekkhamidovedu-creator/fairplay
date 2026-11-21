"""Running an exam: open, who may start, new start code, more time, close, approve a
resume. One implementation shared by the FairPlay teacher routes (routes_teacher) and
the DKU OJ bridge (integrations/dkuoj/routes_v2), so both behave exactly the same.
Each function checks the exam's state, commits, and drops the caches it changed;
permission checks stay with the caller."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import app_client as ac
from .canvas_mode import scope_of
from .access import eligible_students
from .closing import close_exam_now, max_extra
from .integrations.registry import integration_for
from .models import Attempt, Exam
from .services import (attempt_key, exam_closes_at, exam_delivery, invalidate_exam, unique_invigilator_code,
                       new_session_id, new_start_code)
from .store import get_store
from .timeutil import iso, utcnow

# S3: the wait for the teacher's OK is given back. A FairPlay exam gives back at most this much;
# an exam from an external system (DKU OJ) all of it: its source decides about time (2026-11).
MAX_RESUME_EXTENSION = timedelta(minutes=15)
PAPER_NOT_OPENED = "Paper exams are printed, not opened in the app"


async def checked_open_to(session: AsyncSession, e: Exam, value) -> list[int] | None:
    """"all" -> None; a list -> the sorted ids, each one a student of this exam (422 else)."""
    if value == "all" or value is None:
        return None
    wanted = sorted({int(x) for x in value})
    eligible = {sid for sid, _, _ in await eligible_students(session, e)}
    unknown = [x for x in wanted if x not in eligible]
    if unknown:
        raise HTTPException(422, f"Not a student of this exam: {', '.join(map(str, unknown[:10]))}")
    return wanted


async def open_exam(session: AsyncSession, e: Exam, open_to="all") -> None:
    """Make the exam live with a new start code and invigilator code (409 when it can't)."""
    if e.status == "live":
        raise HTTPException(409, "Exam is already live")
    if exam_delivery(e) == "paper":  # slice 6: printed, scanned and graded; never opened
        raise HTTPException(409, PAPER_NOT_OPENED)
    integ = integration_for(e)
    if integ is not None:            # integrations decide who takes it (their roster)
        if integ.open_refused_message(e):
            raise HTTPException(409, integ.open_refused_message(e))
    elif e.class_id is None and e.course_link_id is None:   # audit S-03
        raise HTTPException(409, "Choose the class that takes this exam before opening it")
    now = utcnow()
    closes = exam_closes_at(e)
    clear_closes = False
    if closes is not None and now >= closes + await max_extra(session, e):
        if integ is not None:        # the source owns the end time
            raise HTTPException(409, f"The end time set in {integ.name} has passed. "
                                     f"Change it in {integ.name} to open the exam again.")
        if e.status == "draft":      # never opened: the setting is a mistake, not a past run
            raise HTTPException(409, "The automatic close time has passed. Change or clear "
                                     "\"Close automatically at\" in the exam settings.")
        clear_closes = True          # re-opened after its close time: the teacher closes it
    if e.external_kind == "canvas" and scope_of(e.external_url) is None:
        raise HTTPException(409, "The Canvas link does not open one quiz or assignment. Fix the link "
                                 "in the exam settings before opening the exam.")
    open_to_value = await checked_open_to(session, e, open_to)
    if clear_closes:
        e.closes_at = None
    e.open_to = open_to_value
    e.status = "live"
    e.start_code = new_start_code()
    e.invigilator_code = await unique_invigilator_code(session, e.id)
    e.closed_at = None
    e.auto_closed = False
    e.updated_at = now
    await session.commit()
    await invalidate_exam(get_store(), e.id)


async def set_open_to(session: AsyncSession, e: Exam, value) -> None:
    if exam_delivery(e) == "paper":
        raise HTTPException(409, PAPER_NOT_OPENED)
    e.open_to = await checked_open_to(session, e, value)
    e.updated_at = utcnow()
    await session.commit()
    await invalidate_exam(get_store(), e.id)


async def close_exam(session: AsyncSession, e: Exam) -> None:
    """The teacher's Close (idempotent once closed): the same path as the automatic close."""
    if e.status == "closed":
        return
    # slice 6: a paper exam is never live; closing it (from draft) ends collecting scans
    # so grades can be released like any other exam.
    if e.status != "live" and not (e.status == "draft" and exam_delivery(e) == "paper"):
        raise HTTPException(409, "Exam is not live")
    await close_exam_now(session, e, utcnow())


async def rotate_start_code(session: AsyncSession, e: Exam) -> None:
    """Audit #13: new start code. Students already writing are not affected."""
    if e.status != "live":
        raise HTTPException(409, "Exam is not live")
    e.start_code = new_start_code()
    e.updated_at = utcnow()
    await session.commit()
    await invalidate_exam(get_store(), e.id)


async def _back_to_live(session: AsyncSession, e: Exam, now: datetime) -> None:
    """An exam the clock closed (auto_closed) is live again: new codes, the same students.
    The end-time check of open_exam is skipped: the caller adds the time that makes it valid."""
    e.status = "live"
    e.start_code = new_start_code()
    e.invigilator_code = await unique_invigilator_code(session, e.id)
    e.closed_at = None
    e.auto_closed = False
    e.updated_at = now


async def extend_exam(session: AsyncSession, e: Exam, minutes: int, claim=None) -> bool:
    """More time for everyone: the duration, every attempt still writing and, for an
    external exam with a close time, that close time. Any positive number of minutes.
    An exam of an external source that its clock closed (auto_closed) is opened again by it."""
    reopen = False
    reopened: list[str] = []
    if e.status != "live":
        if not (e.status == "closed" and e.auto_closed and integration_for(e) is not None):
            raise HTTPException(409, "Exam is not live")
        reopen = True
    # `claim` records the caller's idempotency id in THIS transaction (committed with the time below).
    # False: that id was applied already, nothing changes (True = go on).
    exam_id = e.id                              # read before claim(): its rollback expires `e`
    if claim is not None and not await claim():
        await forget_time_caches(session, exam_id)       # a retry repairs a cache step the first call lost
        return False
    delta = timedelta(minutes=minutes)
    e.duration_min += minutes
    e.updated_at = utcnow()
    if integration_for(e) is not None:
        # Also without a close time: the source's sync recomputes the duration from its own
        # value plus these minutes, so they are never lost on the next listing.
        # External exams close at the source's end time (e.g. DKU OJ's): the added minutes
        # move that close time too (integration closes_at adds external_meta
        # extra_close_minutes), so students starting now get the time and the countdown
        # and automatic close follow.
        meta = dict(e.external_meta or {})
        try:
            before = max(0, int(meta.get("extra_close_minutes") or 0))
        except (TypeError, ValueError):
            before = 0
        meta["extra_close_minutes"] = before + minutes
        e.external_meta = meta
    if reopen:
        closes = exam_closes_at(e)
        if closes is not None and utcnow() >= closes + await max_extra(session, e):
            await session.rollback()
            raise HTTPException(409, "The exam is still past its end after adding this time; add more")
        closed_at = e.closed_at
        await _back_to_live(session, e, utcnow())
        reopened = await _reopen_closed_out(session, e, closed_at, utcnow() + delta)
        # The reopen events decide the delivery revision (outbox.enqueue counts them), so they are
        # written in this transaction, not after the commit.
        from .integrations.outbox import REOPENED
        from .models import IntegrityEvent
        for aid in reopened:
            session.add(IntegrityEvent(
                attempt_id=aid, exam_id=e.id, type=REOPENED, server_time=utcnow(),
                detail=f"handed in when the exam closed by itself; given back with {minutes} more minutes"))
    rows = (await session.execute(
        select(Attempt.id, Attempt.deadline_at)
        .where(Attempt.exam_id == e.id, Attempt.status == "in_progress",
               Attempt.id.not_in(reopened) if reopened else True))).all()
    if rows:
        # Bulk UPDATE by primary key (dialect-neutral; no interval arithmetic in SQL).
        await session.execute(update(Attempt), [{"id": aid, "deadline_at": dl + delta}
                                                for aid, dl in rows])
    await session.commit()
    # Committed (the reopen events with it): the time is added. What follows is cache and live-room
    # bookkeeping only; a failure must not fail the request, or a caller that retries (same request
    # id) would be let through to add the time again. The entries expire on their own.
    import logging
    log = logging.getLogger("proctora")
    store = get_store()

    async def best_effort(what: str, step) -> None:
        try:
            await step()
        except Exception:  # noqa: BLE001
            log.exception("extend_exam: %s failed for exam %s", what, e.id)

    for aid in reopened:
        async def live_room(aid=aid):
            await store.hincrby(f"evc:{e.id}", aid, 1, ac.LIVE_TTL)
            await store.hset(f"evl:{e.id}", aid, f"{REOPENED} handed in when the exam closed by itself;"
                             f" given back with {minutes} more minutes", ac.LIVE_TTL)

        async def forget(aid=aid):
            await store.delete(attempt_key(aid), f"abt:{aid}", f"entry:{aid}")
            await store.hdel(f"ab:{e.id}", aid)
        await best_effort("the live-room note", live_room)
        await best_effort("the cache of a reopened attempt", forget)
    await forget_time_caches(session, exam_id, [aid for aid, _ in rows])
    return True


async def forget_time_caches(session: AsyncSession, exam_id: str, attempt_ids: list[str] | None = None) -> None:
    """Best effort: drop the exam cache (new starts get the new duration) and the cached deadlines
    (heartbeats re-read them from the DB). attempt_ids None = every attempt still writing.
    Also called for a repeated time grant (same request id): a cache step that failed after the
    first grant is done again, instead of students keeping the old deadline until the entry expires."""
    import logging
    log = logging.getLogger("proctora")
    store = get_store()
    try:
        await invalidate_exam(store, exam_id)
    except Exception:  # noqa: BLE001
        log.exception("time grant: the exam cache failed for exam %s", exam_id)
    try:
        if attempt_ids is None:
            attempt_ids = list((await session.execute(select(Attempt.id).where(
                Attempt.exam_id == exam_id, Attempt.status == "in_progress"))).scalars().all())
        if attempt_ids:
            await store.delete(*[attempt_key(aid) for aid in attempt_ids])
    except Exception:  # noqa: BLE001
        log.exception("time grant: the deadline cache failed for exam %s", exam_id)


async def _reopen_closed_out(session: AsyncSession, e: Exam, closed_at: datetime | None,
                             deadline: datetime) -> list[str]:
    """The attempts the automatic close handed in (not graded or released) go back to writing until
    `deadline`, with their answers: "Add time" after an automatic close gives the time to the students
    who were writing, not only to new starters. Their extension below is skipped (deadline is final)."""
    from .integrations.outbox import freeze_unsent
    if closed_at is None or e.grades_released:
        return []
    rows = (await session.execute(select(Attempt).where(
        Attempt.exam_id == e.id, Attempt.status == "submitted", Attempt.auto_submitted.is_(True),
        Attempt.submitted_at >= closed_at, Attempt.external_result.is_(None), Attempt.score.is_(None)))).scalars().all()
    out = []
    for a in rows:
        if a.grades:
            continue
        await freeze_unsent(session, a)
        a.status, a.submitted_at, a.receipt = "in_progress", None, None
        a.auto_submitted, a.late = False, False
        a.needs_grading = 0
        a.deadline_at = deadline
        a.deadline_fixed = True                   # set by hand: DKU OJ time changes leave it alone
        a.deadline_swept = False
        a.device_id, a.device_pub, a.session_id = None, None, new_session_id()
        a.exited_at = None
        a.pending_resume = False
        a.updated_at = utcnow()
        out.append(a.id)
    await session.flush()
    return out


async def approve_resume(session: AsyncSession, a: Attempt) -> dict:
    """S3: let a student who came back after FairPlay closed continue. The time spent
    waiting is given back (at most 15 minutes for a FairPlay exam, all of it for an exam from DKU OJ)."""
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    if not a.pending_resume:
        raise HTTPException(409, "This student is not waiting for approval")
    now = utcnow()
    added = max(now - (a.pending_since or now), timedelta(0))
    exam = await session.get(Exam, a.exam_id)
    if exam is None or integration_for(exam) is None:       # a FairPlay exam keeps the 15 minute ceiling
        added = min(added, MAX_RESUME_EXTENSION)
    added = timedelta(seconds=int(added.total_seconds()))     # whole seconds: exactly what the formula adds
    a.deadline_at = a.deadline_at + added
    a.deadline_extra_s = int(a.deadline_extra_s or 0) + int(added.total_seconds())   # part of the formula
    a.pending_resume = False                  # pending_since stays: approval = "approved"
    a.updated_at = now
    await session.commit()
    store = get_store()
    await store.delete(attempt_key(a.id))     # beats and heartbeats re-read approval + deadline
    minutes = round(added.total_seconds() / 60, 1)
    await ac.add_event(session, store, a.id, a.exam_id, "resume_approved",
                       f"teacher let the student continue; {minutes:g} min added")
    return {"ok": True, "minutes_added": minutes, "deadline_at": iso(a.deadline_at)}


async def release_attempt_device(session: AsyncSession, a: Attempt, swap: bool = False,
                                 clear_pending: bool = False) -> tuple[bool, str | None]:
    """The old computer is cut off and the next start from any FairPlay app binds the attempt;
    answers and timer stay. With `swap`, a pending swap request is approved the way
    approve-swap does (the old computer may send one final save). Returns (moved a pending
    swap, the device it asked from)."""
    now = utcnow()
    moved = swap and a.swap_requested_at is not None
    target = a.swap_device_id if moved else None
    if moved:
        a.swap_old_session, a.swap_approved_at = a.session_id, now
        a.swap_requested_at = None
    a.device_id = a.device_pub = None
    a.exited_at = None
    a.session_id = new_session_id()   # nobody holds this one: the old device is cut off
    if clear_pending:
        a.pending_resume = False
    a.updated_at = now
    await session.commit()
    store = get_store()
    if moved:
        await store.set(f"swapbase:{a.id}", json.dumps(a.answers or {}), ac.SWAP_FINAL_SAVE_S + 10)
    await store.delete(attempt_key(a.id), f"abt:{a.id}")
    await store.hdel(f"ab:{a.exam_id}", a.id)       # audit S-13: old device's beats go too
    return moved, target


async def reopen_attempt(session: AsyncSession, e: Exam, a: Attempt, minutes: int | None,
                         cap: datetime | None, once_only: bool = False) -> datetime:
    """Put a handed-in attempt back to in_progress with its answers, for `minutes` (at most
    until `cap` when one is given); without `minutes` until `cap` (the exam's close + extra time).
    The device is freed. The caller has checked who may do it and writes the event; this refuses
    what must never be reopened: not handed in, graded or released, a closed exam (unless the
    clock closed an exam from an external source: it opens again), and, with `once_only` (the
    FairPlay teacher's rule; DKU OJ has none), one reopened before. Returns the new deadline."""
    from .integrations.outbox import REOPENED, freeze_unsent
    from .models import IntegrityEvent
    if a.status != "submitted":
        raise HTTPException(409, "This student has not handed in")
    back = False
    if e.status != "live":
        if not (e.status == "closed" and e.auto_closed and integration_for(e) is not None):
            raise HTTPException(409, "Exam is not live")
        back = True
    if a.external_result is not None or e.grades_released or a.grades or a.score is not None:
        raise HTTPException(409, "This hand-in is already graded or released and cannot be reopened")
    if once_only and (await session.execute(select(IntegrityEvent.id).where(
            IntegrityEvent.attempt_id == a.id, IntegrityEvent.type == REOPENED).limit(1))).first() is not None:
        raise HTTPException(409, "This attempt was reopened once already")
    now = utcnow()
    if minutes is not None:
        deadline = now + timedelta(minutes=minutes)
        if cap is not None:
            deadline = min(deadline, cap)
    elif cap is not None:
        deadline = cap
    else:
        raise HTTPException(400, "This exam has no end time: send how many minutes")
    if deadline <= now:
        raise HTTPException(409, "This exam has ended: send how many minutes to reopen it for")
    if back:
        await _back_to_live(session, e, now)
    await freeze_unsent(session, a)
    a.status, a.submitted_at, a.receipt = "in_progress", None, None
    a.auto_submitted, a.late, a.sealed_proof = False, False, None
    a.score, a.needs_grading = None, 0
    a.deadline_at = deadline
    a.deadline_fixed = True                       # set by hand: DKU OJ time changes leave it alone
    a.deadline_swept = False
    a.device_id, a.device_pub, a.session_id = None, None, new_session_id()
    a.exited_at = None
    a.pending_resume = False
    a.swap_device_id = a.swap_requested_at = a.swap_old_session = a.swap_approved_at = None
    a.updated_at = now
    await session.commit()
    store = get_store()
    await store.delete(attempt_key(a.id), f"abt:{a.id}", f"entry:{a.id}")
    await store.hdel(f"ab:{a.exam_id}", a.id)
    if back:
        await invalidate_exam(store, e.id)     # start reads the exam cache: not the closed one
    return deadline


def offline_exit_code(settings, a: Attempt, mac_code_raw: str) -> tuple[str, str]:
    """(exit code, normalised Mac code). The code a locked Mac that cannot reach the server accepts (OfflineExit.swift): the
    invigilator types the Mac code shown on the student's screen. Only works on that
    student's Mac for its current app session, and is logged."""
    mac_code = ac.normalize_mac_code(mac_code_raw)
    if mac_code is None:
        raise HTTPException(422, "A Mac code has 8 letters and digits, like K7QM-3XPA")
    if not a.session_id:
        raise HTTPException(409, "This student's app has no session yet. Restart their Mac; answers are saved on it.")
    code = ac.offline_exit_code(ac.offline_key(settings.secret, a.id, a.session_id), mac_code)
    return code, mac_code

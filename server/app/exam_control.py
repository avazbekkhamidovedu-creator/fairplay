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

# S3: the wait for the teacher's OK is given back, up to this much.
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


async def extend_exam(session: AsyncSession, e: Exam, minutes: int) -> None:
    """More time for everyone: the duration, every attempt still writing and, for an
    external exam with a close time, that close time."""
    if e.status != "live":
        raise HTTPException(409, "Exam is not live")
    delta = timedelta(minutes=minutes)
    e.duration_min += minutes
    e.updated_at = utcnow()
    if integration_for(e) is not None and exam_closes_at(e) is not None:
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
    rows = (await session.execute(
        select(Attempt.id, Attempt.deadline_at)
        .where(Attempt.exam_id == e.id, Attempt.status == "in_progress"))).all()
    if rows:
        # Bulk UPDATE by primary key (dialect-neutral; no interval arithmetic in SQL).
        await session.execute(update(Attempt), [{"id": aid, "deadline_at": dl + delta}
                                                for aid, dl in rows])
    await session.commit()
    store = get_store()
    await invalidate_exam(store, e.id)  # new starts get the longer duration
    if rows:  # heartbeats re-read the new deadline from the DB on their next call
        await store.delete(*[attempt_key(aid) for aid, _ in rows])


async def approve_resume(session: AsyncSession, a: Attempt) -> dict:
    """S3: let a student who came back after FairPlay closed continue. The time spent
    waiting is given back (at most 15 minutes)."""
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    if not a.pending_resume:
        raise HTTPException(409, "This student is not waiting for approval")
    now = utcnow()
    added = min(max(now - (a.pending_since or now), timedelta(0)), MAX_RESUME_EXTENSION)
    a.deadline_at = a.deadline_at + added
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


async def reopen_attempt(session: AsyncSession, e: Exam, a: Attempt, minutes: int,
                         cap: datetime | None) -> datetime:
    """Put a handed-in attempt back to in_progress for `minutes` (at most until `cap`), with
    its answers. The device is freed. The caller has checked who may do it and writes the
    event; this refuses what must never be reopened: not handed in, exam not live, graded or
    released, or reopened before. Returns the new deadline."""
    from .integrations.outbox import REOPENED, freeze_unsent
    from .models import IntegrityEvent
    from .security import forget_scope
    if a.status != "submitted":
        raise HTTPException(409, "This student has not handed in")
    if e.status != "live":
        raise HTTPException(409, "Exam is not live")
    if a.external_result is not None or e.grades_released or a.grades or a.score is not None:
        raise HTTPException(409, "This hand-in is already graded or released and cannot be reopened")
    if (await session.execute(select(IntegrityEvent.id).where(
            IntegrityEvent.attempt_id == a.id, IntegrityEvent.type == REOPENED).limit(1))).first() is not None:
        raise HTTPException(409, "This attempt was reopened once already")
    now = utcnow()
    deadline = now + timedelta(minutes=minutes)
    if cap is not None:
        deadline = min(deadline, cap)
    if deadline <= now:
        raise HTTPException(409, "This exam has ended")
    await freeze_unsent(session, a)
    a.status, a.submitted_at, a.receipt = "in_progress", None, None
    a.auto_submitted, a.late, a.sealed_proof = False, False, None
    a.score, a.needs_grading = None, 0
    a.deadline_at = deadline
    a.device_id, a.device_pub, a.session_id = None, None, new_session_id()
    a.pending_resume = False
    a.swap_device_id = a.swap_requested_at = a.swap_old_session = a.swap_approved_at = None
    a.updated_at = now
    await session.commit()
    store = get_store()
    await store.delete(attempt_key(a.id), f"abt:{a.id}", f"entry:{a.id}")
    await store.hdel(f"ab:{a.exam_id}", a.id)
    await forget_scope(a.student_id, a.exam_id)
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

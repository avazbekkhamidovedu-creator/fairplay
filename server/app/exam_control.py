"""Running an exam: open, who may start, new start code, more time, close, approve a
resume. One implementation shared by the FairPlay teacher routes (routes_teacher) and
the DKU OJ bridge (integrations/dkuoj/routes_v2), so both behave exactly the same.
Each function checks the exam's state, commits, and drops the caches it changed;
permission checks stay with the caller."""
from __future__ import annotations

import asyncio

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import app_client as ac
from . import canvas_mode
from .access import eligible_students
from .closing import close_exam_now, max_extra
from .integrations.registry import integration_for
from .models import Attempt, Exam
from .services import (attempt_key, exam_closes_at, exam_delivery, invalidate_exam, new_invigilator_code,
                       new_start_code)
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


async def open_exam(session: AsyncSession, e: Exam, open_to="all",
                    canvas_access_code: str | None = None) -> None:
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
    open_to_value = await checked_open_to(session, e, open_to)
    canvas = e.external_kind == "canvas"
    if canvas:
        if not await canvas_mode.claim_open(session, e):
            if e.status == "live":
                raise HTTPException(409, "Exam is already live")
            raise HTTPException(409, "This exam is already being opened. Try again in a moment.")
        await canvas_mode.prepare_open(session, e, canvas_access_code)
        e.canvas_open_claim = None
    if clear_closes:
        e.closes_at = None
    e.open_to = open_to_value
    e.status = "live"
    e.start_code = new_start_code()
    e.invigilator_code = new_invigilator_code()
    e.closed_at = None
    e.auto_closed = False
    e.updated_at = now
    try:
        await session.commit()
    except BaseException:
        if canvas:
            await asyncio.shield(canvas_mode.undo_open(session, e))
        raise
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

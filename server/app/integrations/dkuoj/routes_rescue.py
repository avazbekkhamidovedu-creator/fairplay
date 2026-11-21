"""Professor rescue actions from DKU OJ (signed oj->fp, same checks as routes_v2): free a
student's device, reopen a handed-in attempt, add time for one
student, give a locked Mac its offline exit code. Prefix /api/integrations/dkuoj/v1.

2026-11 (owner decision "FairPlay just shows DKU OJ"): FairPlay adds no limits of its own here: no
count or time limits on releasing a device, reopening, extra time or offline exit codes. DKU OJ
decides; every action is still audited (class event + integrity event).

Every call needs an editor of the exam as acting user, answers `Cache-Control: no-store`
and leaves a class event plus, when the student has an attempt, an integrity event naming
the acting user and, when one was sent, the reason. Only exams of source "dkuoj" can be
reached (routes_v2._exam_by_key), and only this exam's attempt of the student.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ... import app_client as ac
from ... import exam_control as control
from ...closing import max_extra
from ...db import get_session
from ...models import Attempt, Exam, User
from ...services import attempt_key, exam_closes_at
from ...store import get_store
from ...timeutil import iso, utcnow
from .models import OjRosterEntry, OjStudentGrant
from .routes_v2 import (PREFIX, BridgeCall, _attempt_of, _editor_exam, _event_class, claim_request, request_used,
                        positive_minutes, request_id_of, signed_call)
from .setup import add_event

log = logging.getLogger("proctora.dkuoj")

router = APIRouter(prefix=PREFIX, tags=["dkuoj-bridge"])

REASON_MAX = 200
NO_STORE = {"Cache-Control": "no-store"}


def _out(body: dict) -> JSONResponse:
    return JSONResponse(body, headers=NO_STORE)


def _reason(data: dict, required: bool = False) -> str:
    """The reason is optional since 2026-11: a missing or empty one is "" (the audit line already names the
    acting user, the student and the minutes); a given one is kept for old clients. `required` is kept only for
    callers that still want to insist."""
    v = data.get("reason")
    if v is None or (isinstance(v, str) and not v.strip() and not required):
        return ""
    if not isinstance(v, str) or not v.strip():
        raise HTTPException(400, "reason is required (1 to 200 characters)")
    v = v.strip()
    if len(v) > REASON_MAX:
        raise HTTPException(400, "reason is at most 200 characters")
    return v


async def _on_list(session: AsyncSession, e: Exam, netid: str) -> tuple[User, Attempt | None]:
    """The student's account and attempt in THIS exam; 404 unless they are on its list or
    already have an attempt."""
    u, a = await _attempt_of(session, e, netid)
    if u is None:
        raise HTTPException(404, "No FairPlay account for this NetID")
    listed = (await session.execute(select(OjRosterEntry.id).where(
        OjRosterEntry.exam_id == e.id, OjRosterEntry.user_id == u.id, OjRosterEntry.revoked_at.is_(None)).limit(1))).first() is not None
    if not listed and a is None:
        raise HTTPException(404, "This student is not on this exam's list")
    return u, a


async def _record(session: AsyncSession, e: Exam, call: BridgeCall, kind: str, u: User, a: Attempt | None,
                  text: str, reason: str) -> None:
    detail = f"{text}: {reason}" if reason else text
    class_id = _event_class(e)
    if class_id is not None:
        await add_event(session, class_id, kind, netid=u.netid, actor=call.acting_user,
                        detail=f"exam {e.external_key}: {detail}")
        await session.commit()
    if a is not None:
        await ac.add_event(session, get_store(), a.id, e.id, kind, f"{call.acting_user} (DKU OJ): {detail}")


@router.post("/exams/{key}/students/{netid}/release-device")
async def release_device(key: str, netid: str, call: BridgeCall = Depends(signed_call),
                         session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    reason = _reason(call.data)
    u, a = await _on_list(session, e, netid)
    if a is not None and a.status == "submitted":
        raise HTTPException(409, "This student has already handed in")
    moved = target = None
    if a is not None:
        moved, target = await control.release_attempt_device(session, a, swap=True, clear_pending=True)
    else:
        await session.commit()
    text = "device released" if a is None else "attempt freed for another device, answers and timer kept"
    if moved and target:
        text += f" (moved to the computer that asked, {target[:8]})"
    await _record(session, e, call, "device_released_oj", u, a, text, reason)
    log.info("DKU OJ exam %s: device released by %s", key, call.acting_user)
    return _out({"ok": True, "released": "attempt" if a is not None else "none"})


@router.post("/exams/{key}/students/{netid}/reopen")
async def reopen(key: str, netid: str, call: BridgeCall = Depends(signed_call),
                 session: AsyncSession = Depends(get_session)):
    from ..outbox import ensure_revision_schema
    await ensure_revision_schema(session)
    e = await _editor_exam(session, key, call)
    minutes = positive_minutes(call.data, required=False)
    reason = _reason(call.data)
    u, a = await _on_list(session, e, netid)
    if a is None:
        raise HTTPException(409, "This student has not handed in")
    # No minutes: until the exam's close (+ the largest extra time). With minutes: exactly that
    # long, whatever the end time is (DKU OJ decides).
    closes = exam_closes_at(e)
    cap = closes + await max_extra(session, e) if closes is not None and minutes is None else None
    receipt, handed_in = a.receipt, a.submitted_at
    deadline = await control.reopen_attempt(session, e, a, minutes, cap)
    await session.commit()
    how = f"{minutes} min" if minutes is not None else "the rest of the exam"
    await _record(session, e, call, "attempt_reopened", u, a,
                  f"reopened for {how} until {iso(deadline)}; first hand-in receipt {receipt or '-'} "
                  f"at {iso(handed_in)} stays on file", reason)
    log.info("DKU OJ exam %s: attempt reopened by %s", key, call.acting_user)
    return _out({"ok": True, "deadline_at": iso(deadline)})


@router.post("/exams/{key}/students/{netid}/extra-time")
async def extra_time(key: str, netid: str, call: BridgeCall = Depends(signed_call),
                     session: AsyncSession = Depends(get_session)):
    """Extra time for one student, any positive number of minutes, no total cap. An optional
    "request_id" makes it idempotent (a repeat of the same id adds nothing)."""
    e = await _editor_exam(session, key, call)
    minutes = positive_minutes(call.data)
    rid = request_id_of(call)
    reason = _reason(call.data)
    u, a = await _on_list(session, e, netid)
    scope = f"extratime:{e.id}:{u.id}"
    repeated = {"ok": True, "repeated": True,
                "deadline_at": iso(a.deadline_at) if a is not None and a.status == "in_progress" else None}
    # A repeat of a grant that was applied (its answer lost) is answered as a repeat, even if the
    # student handed in or the exam closed since.
    if await request_used(session, scope, rid):
        log.info("DKU OJ exam %s: repeated extra-time %s ignored", key, rid)
        await _forget_student_caches(e, a)              # a retry repairs a cache step the first call lost
        return _out(repeated)
    if e.status == "closed" and not e.auto_closed:      # closed by its clock: extra time still counts (like add-time)
        raise HTTPException(409, "Exam is closed")
    if a is not None and a.status == "submitted":
        raise HTTPException(409, "This student has already handed in")
    back = False
    if e.status == "closed":
        # Closed by its clock (checked above). The grant only helps this student when their own end
        # (the close + their extra time, DKU OJ's and the professors') is still ahead: then the exam
        # comes back live the way "Add time" brings it back; otherwise there is nothing to give.
        roster = (await session.execute(select(OjRosterEntry.extra_time_seconds).where(
            OjRosterEntry.exam_id == e.id, OjRosterEntry.user_id == u.id,
            OjRosterEntry.revoked_at.is_(None)))).scalar() or 0
        granted = (await session.execute(select(OjStudentGrant.extra_seconds).where(
            OjStudentGrant.exam_id == e.id, OjStudentGrant.user_id == u.id))).scalar() or 0
        closes = exam_closes_at(e)
        end = closes + timedelta(seconds=int(roster) + int(granted) + minutes * 60) if closes is not None else None
        if end is None or end <= utcnow():
            raise HTTPException(409, "The exam has ended; add time for everyone or reopen")
        back = True
    # The request id is stored in the grant's own transaction (_grant_once): applied once.
    if back:
        await control._back_to_live(session, e, utcnow())     # committed together with the grant
    granted_now = await _grant(session, e, u, a, minutes, call, scope, rid)
    if granted_now is None:                                   # a concurrent twin with this id won
        await session.refresh(e)
        if a is not None:
            await session.refresh(a)
        log.info("DKU OJ exam %s: repeated extra-time %s ignored", key, rid)
        await _forget_student_caches(e, a)
        return _out({"ok": True, "repeated": True,
                     "deadline_at": iso(a.deadline_at) if a is not None and a.status == "in_progress" else None})
    total, deadline = granted_now
    if back and e.status != "live":                           # the grant's retry rolled it back
        await control._back_to_live(session, e, utcnow())
        await session.commit()
    # Committed: a failure below must not make a retry (same request id) add the time again
    try:
        if back:
            from ...services import invalidate_exam
            await invalidate_exam(get_store(), e.id)           # start reads the exam cache: not the closed one
        if a is not None and a.status == "in_progress":
            await get_store().delete(attempt_key(a.id))
        await _record(session, e, call, "extra_time_granted", u, a,
                      f"{minutes} extra minutes (now {total} in all)", reason)
    except Exception:  # noqa: BLE001
        log.exception("DKU OJ exam %s: extra time of %s granted, but the cache / event step failed", key, netid)
    log.info("DKU OJ exam %s: %d extra minutes by %s", key, minutes, call.acting_user)
    return _out({"ok": True, "deadline_at": iso(deadline)})


async def _forget_student_caches(e: Exam, a: Attempt | None) -> None:
    """Best effort: the exam cache and this student's cached deadline, so a repeated grant
    redoes a cache step that failed after the first one."""
    from ...services import invalidate_exam
    try:
        await invalidate_exam(get_store(), e.id)
        if a is not None and a.status == "in_progress":
            await get_store().delete(attempt_key(a.id))
    except Exception:  # noqa: BLE001
        log.exception("DKU OJ exam %s: the cache step of a repeated extra time failed", e.id)


async def _grant(session: AsyncSession, e: Exam, u: User, a: Attempt | None, minutes: int,
                 call: BridgeCall, scope: str | None = None, rid: str | None = None) -> tuple[int, object] | None:
    """Add `minutes` to the student's grant (and running deadline). Two first grants at once: the
    loser rolls back and adds to the winner's row (one retry)."""
    try:
        return await _grant_once(session, e, u, a, minutes, call, scope, rid)
    except IntegrityError:
        await session.rollback()
        if a is not None:
            await session.refresh(a)
        return await _grant_once(session, e, u, a, minutes, call, scope, rid)


async def _grant_once(session: AsyncSession, e: Exam, u: User, a: Attempt | None, minutes: int,
                      call: BridgeCall, scope: str | None = None, rid: str | None = None
                      ) -> tuple[int, object] | None:
    """None = the request id was applied already (nothing changed)."""
    if scope is not None and not await claim_request(session, scope, rid):
        return None
    grant = (await session.execute(select(OjStudentGrant).where(
        OjStudentGrant.exam_id == e.id, OjStudentGrant.user_id == u.id))).scalar_one_or_none()
    total = int(grant.extra_seconds if grant else 0) // 60 + minutes
    if grant is None:
        grant = OjStudentGrant(exam_id=e.id, user_id=u.id)
        session.add(grant)
    grant.extra_seconds, grant.by, grant.at = total * 60, call.acting_user[:150], utcnow()
    deadline = None
    if a is not None and a.status == "in_progress":
        a.deadline_at = a.deadline_at + timedelta(minutes=minutes)
        a.updated_at = utcnow()
        deadline = a.deadline_at
    await session.commit()
    return total, deadline


@router.post("/exams/{key}/students/{netid}/offline-exit")
async def offline_exit(key: str, netid: str, request: Request, call: BridgeCall = Depends(signed_call),
                       session: AsyncSession = Depends(get_session)):
    mac_code = call.data.get("mac_code")
    if not isinstance(mac_code, str) or not mac_code.strip():
        raise HTTPException(400, "mac_code is required")
    e = await _editor_exam(session, key, call)
    reason = _reason(call.data, required=False)
    u, a = await _on_list(session, e, netid)
    if a is None:
        raise HTTPException(404, "This student has not started the exam")
    code, mac = control.offline_exit_code(ac.settings_of(request), a, mac_code)
    await _record(session, e, call, "offline_exit_code_issued", u, a,
                  f"offline exit code given for Mac code {mac[:4]}-{mac[4:]}", reason)
    return _out({"code": code})

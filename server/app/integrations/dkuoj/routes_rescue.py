"""Professor rescue actions from DKU OJ (signed oj->fp, same checks as routes_v2): free a
student's device, reopen a handed-in attempt, add time for one
student, give a locked Mac its offline exit code. Prefix /api/integrations/dkuoj/v1.

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
from .routes_v2 import PREFIX, BridgeCall, _attempt_of, _editor_exam, _event_class, signed_call
from .setup import add_event

log = logging.getLogger("proctora.dkuoj")

router = APIRouter(prefix=PREFIX, tags=["dkuoj-bridge"])

REASON_MAX = 200
MONTH_S = 30 * 24 * 3600
HOUR_S = 3600
RELEASES_PER_STUDENT = 3
OFFLINE_EXITS_PER_STUDENT_HOUR = 20
REOPEN_MINUTES = (5, 60)
EXTRA_MINUTES = (1, 120)
EXTRA_CAP_MIN = 240
NO_STORE = {"Cache-Control": "no-store"}
TOO_OFTEN = "This was done too often for this student. Ask FairPlay support if it is really needed."


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


def _minutes(data: dict, lo: int, hi: int) -> int:
    m = data.get("minutes")
    if not isinstance(m, int) or isinstance(m, bool) or not lo <= m <= hi:
        raise HTTPException(400, f"minutes must be a whole number from {lo} to {hi}")
    return m


async def _limit(key: str, limit: int, window_s: int) -> None:
    if await get_store().incr(key, window_s) > limit:
        raise HTTPException(429, TOO_OFTEN, headers={"Retry-After": "3600"})


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
    await _limit(f"oj:release:{e.id}:{u.id}", RELEASES_PER_STUDENT, MONTH_S)
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
    minutes = _minutes(call.data, *REOPEN_MINUTES)
    reason = _reason(call.data)
    u, a = await _on_list(session, e, netid)
    if a is None:
        raise HTTPException(409, "This student has not handed in")
    closes = exam_closes_at(e)
    cap = closes + await max_extra(session, e) if closes is not None else None
    receipt, handed_in = a.receipt, a.submitted_at
    deadline = await control.reopen_attempt(session, e, a, minutes, cap)
    await session.commit()
    await _record(session, e, call, "attempt_reopened", u, a,
                  f"reopened for {minutes} min until {iso(deadline)}; first hand-in receipt {receipt or '-'} "
                  f"at {iso(handed_in)} stays on file", reason)
    log.info("DKU OJ exam %s: attempt reopened by %s", key, call.acting_user)
    return _out({"ok": True, "deadline_at": iso(deadline)})


@router.post("/exams/{key}/students/{netid}/extra-time")
async def extra_time(key: str, netid: str, call: BridgeCall = Depends(signed_call),
                     session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    minutes = _minutes(call.data, *EXTRA_MINUTES)
    reason = _reason(call.data)
    u, a = await _on_list(session, e, netid)
    if e.status == "closed":
        raise HTTPException(409, "Exam is closed")
    if a is not None and a.status == "submitted":
        raise HTTPException(409, "This student has already handed in")
    for attempt in (0, 1):
        grant = (await session.execute(select(OjStudentGrant).where(
            OjStudentGrant.exam_id == e.id, OjStudentGrant.user_id == u.id))).scalar_one_or_none()
        total = int(grant.extra_seconds if grant else 0) // 60 + minutes
        if total > EXTRA_CAP_MIN:
            left = max(0, EXTRA_CAP_MIN - total + minutes)
            raise HTTPException(409, f"A student can get at most {EXTRA_CAP_MIN} extra minutes; {left} left")
        if grant is None:
            grant = OjStudentGrant(exam_id=e.id, user_id=u.id)
            session.add(grant)
        grant.extra_seconds, grant.by, grant.at = total * 60, call.acting_user[:150], utcnow()
        deadline = None
        if a is not None and a.status == "in_progress":
            a.deadline_at = a.deadline_at + timedelta(minutes=minutes)
            a.updated_at = utcnow()
            deadline = a.deadline_at
        try:
            await session.commit()
            break
        except IntegrityError:          # two grants for a first time at once: add to the winner's row
            await session.rollback()
            if attempt:
                raise
            if a is not None:
                await session.refresh(a)
    if a is not None and a.status == "in_progress":
        await get_store().delete(attempt_key(a.id))
    await _record(session, e, call, "extra_time_granted", u, a,
                  f"{minutes} extra minutes (now {total} in all)", reason)
    log.info("DKU OJ exam %s: %d extra minutes by %s", key, minutes, call.acting_user)
    return _out({"ok": True, "deadline_at": iso(deadline)})


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
    await _limit(f"oj:offexit:{e.id}:{u.id}", OFFLINE_EXITS_PER_STUDENT_HOUR, HOUR_S)
    await _record(session, e, call, "offline_exit_code_issued", u, a,
                  f"offline exit code given for Mac code {mac[:4]}-{mac[4:]}", reason)
    return _out({"code": code})

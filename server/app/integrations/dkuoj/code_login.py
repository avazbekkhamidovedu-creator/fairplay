"""Student sign-in with a per-student access code (docs/dkuoj-bridge-v2.md §12).

1. GET  /api/student/code-login/challenge: a single-use nonce the app signs (the same
   signature as an exam start, so the app needs no change) to prove which device types.
2. POST /api/student/code-login {code, client}: finds the student the code was printed for
   and answers with their name, NetID and exam plus a 5-minute CONFIRM token. Nothing is
   stored. A wrong, revoked, expired, other-exam or second-device code all give the same 400.
3. POST /api/student/code-login/confirm {confirm_token}: "This is me". Binds the code to the
   device, finds or creates the student account (no usable password) and answers like a
   normal student login: {token, role, name}.

Failures are counted per IP and per device and answered with 429 + Retry-After. The code
itself is never stored in an event or a log line.
"""
from __future__ import annotations

import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ... import app_client as ac
from ...db import get_session
from ...models import Attempt, Exam, User
from ...routes_auth import client_ip
from ...schemas import ClientProofIn
from ...security import make_token
from ...store import get_store
from ...timeutil import utcnow
from . import access_codes
from .models import ExamAccessCode
from .setup import add_event, make_kind_token, parse_kind_token

log = logging.getLogger("proctora.dkuoj")

router = APIRouter(prefix="/api/student", tags=["student"])

NOT_VALID = "That code is not valid."
TOO_MANY = "Too many tries. Wait a moment and try again."
CONFIRM_EXPIRED = "This confirmation has expired. Enter your code again."
CONFIRM_TTL_S = 300
KIND = "codeconfirm"
# Failed tries only. A lab behind one campus NAT shares an IP, so that limit is wide; the
# per-device limits (a device id is signed by the app) are the tight ones.
IP_FAILS, IP_WINDOW_S = 30, 60
DEVICE_MIN_FAILS, DEVICE_MIN_WINDOW_S = 5, 60
DEVICE_HOUR_FAILS, DEVICE_HOUR_WINDOW_S = 20, 3600
CHALLENGES_PER_MIN = 60
CONFIRMS_PER_MIN = 30


class CodeLoginIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str = Field(default="", max_length=64)
    client: ClientProofIn | None = None


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    confirm_token: str = Field(min_length=1, max_length=2000)


def _counters(ip: str, device: str | None) -> list[tuple[str, int, int]]:
    out = [(f"codelogin:ip:{ip}", IP_FAILS, IP_WINDOW_S)]
    if device:
        out += [(f"codelogin:dev1:{device}", DEVICE_MIN_FAILS, DEVICE_MIN_WINDOW_S),
                (f"codelogin:dev2:{device}", DEVICE_HOUR_FAILS, DEVICE_HOUR_WINDOW_S)]
    return out


async def _check_not_blocked(store, counters) -> None:
    wait = 0
    for key, limit, _ in counters:
        if int(await store.get(key) or 0) >= limit:
            wait = max(wait, int(await store.ttl(key)), 1)
    if wait:
        raise HTTPException(429, {"message": TOO_MANY, "code": "too_many_codes", "retry_after": wait},
                            headers={"Retry-After": str(wait)})


async def _count_failure(store, counters) -> None:
    for key, _, window in counters:
        await store.incr(key, window)


def _no_store(body: dict) -> JSONResponse:
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/code-login/challenge")
async def challenge(request: Request):
    store = get_store()
    if await store.incr(f"codelogin:chal:{client_ip(request)}", 60) > CHALLENGES_PER_MIN:
        raise HTTPException(429, "Too many tries, wait a minute")
    nonce = secrets.token_urlsafe(24)
    await store.set(f"codelogin:nonce:{nonce}", "1", ac.CHALLENGE_TTL_S)
    return _no_store({"nonce": nonce, "expires_in": ac.CHALLENGE_TTL_S})


async def _device_of(request: Request, client: ClientProofIn | None) -> str | None:
    """The app's device id when its signature over a fresh nonce checks out; None only on a
    server that does not require the app (demo)."""
    settings = ac.settings_of(request)
    if client is None:
        if settings.require_app:
            raise HTTPException(403, ac.APP_REQUIRED_DETAIL)
        return None
    owner = await get_store().getdel(f"codelogin:nonce:{client.nonce}")
    if owner != "1" or not ac.verify(settings, ac.start_message(client.nonce, client.device_id), client.signature):
        raise HTTPException(403, ac.APP_PROOF_DETAIL)
    if client.build == "dev" and not settings.dev:
        raise HTTPException(403, ac.APP_PROOF_DETAIL)
    return client.device_id


def _class_of(e: Exam) -> int | None:
    from .routes_v2 import _event_class
    return _event_class(e)


async def _audit(session: AsyncSession, e: Exam | None, kind: str, netid: str | None, detail: str) -> None:
    class_id = _class_of(e) if e is not None else None
    if class_id is not None:
        await add_event(session, class_id, kind, netid=netid, detail=detail)
        await session.commit()


async def _freed(session: AsyncSession, exam_id: str, netid: str) -> bool:
    """The teacher released the student's device (release-device or an approved swap): the
    attempt has no device, so the code may bind to the next one."""
    return (await session.execute(
        select(Attempt.id).join(User, User.id == Attempt.student_id)
        .where(Attempt.exam_id == exam_id, User.netid == netid, Attempt.status == "in_progress",
               Attempt.device_id.is_(None)).limit(1))).first() is not None


async def _second_device(session: AsyncSession, e: Exam, row: ExamAccessCode, device: str) -> None:
    await _audit(session, e, "access_code_second_device", row.netid,
                 f"exam {e.id}: code used on device {device[:8]}, bound to {(row.bound_device or '')[:8]}")
    a = (await session.execute(
        select(Attempt.id).join(User, User.id == Attempt.student_id)
        .where(Attempt.exam_id == e.id, User.netid == row.netid))).scalar_one_or_none()
    if a is not None:
        await ac.add_event(session, get_store(), a, e.id, "access_code_second_device",
                           f"the student's access code was entered on another device ({device[:8]})")


async def _pick(session: AsyncSession, secret: str, code: str, device: str | None):
    """(row, exam, reason). reason is None for a usable code, else why not (for the audit
    trail only: the student always gets the same answer)."""
    found = await access_codes.matches(session, secret, code)
    if not found:
        return None, None, "unknown"
    now = utcnow()
    usable, why = [], "revoked"
    for row, e in found:
        if row.revoked_at is not None:
            continue
        if not await access_codes.in_window(session, e, now):
            why = "outside_window"
            continue
        usable.append((row, e))
    if not usable:
        return found[0][0], found[0][1], why
    mine = [x for x in usable if device and x[0].bound_device == device]
    pool = mine or [x for x in usable if x[0].bound_device is None] or usable
    return pool[0][0], pool[0][1], None


@router.post("/code-login")
async def code_login(body: CodeLoginIn, request: Request, session: AsyncSession = Depends(get_session)):
    s = ac.settings_of(request)
    store = get_store()
    ip = client_ip(request)
    device = await _device_of(request, body.client)
    counters = _counters(ip, device)
    await _check_not_blocked(store, counters)

    code = access_codes.normalise(body.code)
    row = e = None
    reason: str | None = "malformed"
    if re.fullmatch(r"[A-Z0-9]{%d}" % access_codes.CODE_LENGTH, code):
        row, e, reason = await _pick(session, s.secret, code, device)
    if reason is None and device and row.bound_device and row.bound_device != device \
            and not await _freed(session, row.exam_id, row.netid):
        await _second_device(session, e, row, device)
        reason = "second_device"
    if reason is not None:
        await _count_failure(store, counters)
        if row is not None and reason in ("revoked", "outside_window"):
            await _audit(session, e, "access_code_failed", row.netid, f"exam {e.id}: {reason}")
        log.info("access code refused (%s) from %s device %s", reason, ip, (device or "-")[:8])
        raise HTTPException(400, NOT_VALID)

    token = make_kind_token(s.secret, KIND, {"row": row.id, "dev": device or "", "bound": row.bound_device or ""},
                            CONFIRM_TTL_S)
    return _no_store({"first_name": row.first_name, "last_name": row.last_name, "netid": row.netid,
                      "exam_title": e.title, "confirm_token": token})


@router.post("/code-login/confirm")
async def confirm(body: ConfirmIn, request: Request, session: AsyncSession = Depends(get_session)):
    from ...security import remember_user
    from ..registry import integration_for
    from .sync import ensure_user
    s = ac.settings_of(request)
    store = get_store()
    if await store.incr(f"codelogin:confirm:{client_ip(request)}", 60) > CONFIRMS_PER_MIN:
        raise HTTPException(429, TOO_MANY)
    tok = parse_kind_token(s.secret, KIND, body.confirm_token.strip())
    if tok is None or not isinstance(tok.get("row"), int):
        raise HTTPException(400, CONFIRM_EXPIRED)
    if not await store.setnx(f"codelogin:used:{tok['jti']}", "1", CONFIRM_TTL_S + 60):
        raise HTTPException(400, CONFIRM_EXPIRED)

    row = await session.get(ExamAccessCode, tok["row"])
    e = await session.get(Exam, row.exam_id) if row is not None else None
    device = str(tok.get("dev") or "") or None
    if row is None or e is None or row.revoked_at is not None or not await access_codes.in_window(session, e, utcnow()):
        raise HTTPException(400, NOT_VALID)
    bound = str(tok.get("bound") or "") or None
    if row.bound_device != bound:       # another device bound it since the code was entered
        raise HTTPException(400, NOT_VALID)
    if device and bound and bound != device and not await _freed(session, e.id, row.netid):
        raise HTTPException(400, NOT_VALID)

    u = await ensure_user(session, {"username": row.netid, "first_name": row.first_name,
                                    "last_name": row.last_name}, "student")
    if u is None or not await integration_for(e).can_take(session, e.id, u.id):
        await session.rollback()
        raise HTTPException(400, NOT_VALID)
    now = utcnow()
    took = await session.execute(
        update(ExamAccessCode).where(
            ExamAccessCode.id == row.id, ExamAccessCode.revoked_at.is_(None),
            ExamAccessCode.bound_device.is_(None) if bound is None else ExamAccessCode.bound_device == bound)
        .values(bound_device=device if device else ExamAccessCode.bound_device,
                used_at=row.used_at or now))
    if not took.rowcount:
        await session.rollback()
        raise HTTPException(400, NOT_VALID)
    await session.commit()
    await _audit(session, e, "access_code_signin", row.netid,
                 f"exam {e.id}: signed in on device {(device or '-')[:8]}")
    await remember_user(u.id, u.role, int(u.token_version or 0))
    return _no_store({"token": make_token(s.secret, u.id, u.role, s.student_token_ttl_s,
                                          version=u.token_version or 0),
                      "role": u.role, "name": u.name})

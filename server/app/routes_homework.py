"""Homework sign-in (app/integrations/homework_login.py), mounted only when
FAIRPLAY_HOMEWORK_LOGIN names a provider.

POST /api/homework/login/start   {}                  -> {state, start_url, allowed_hosts, finish_url, resume_paths, expires_at}
POST /api/homework/login/finish  {state, ticket}     -> {token, name, netid, expires_at, absolute_expires_at, idle_seconds}
GET  /api/homework/me                                -> {name, netid, expires_at, absolute_expires_at}
POST /api/homework/session/renew                     -> {token, expires_at, absolute_expires_at}
POST /api/homework/remember/{create,exchange,forget,forget-all}   (see CONTRACT.md)

States are single use (deleted on the first finish, right or wrong). Tickets, verifiers and
states are never logged; a short sha256 prefix is.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from . import homework_remember as rem
from .integrations import homework_login as hw
from .models import User
from .routes_auth import client_ip
from .security import (HOMEWORK_ABSOLUTE_S, HOMEWORK_IDLE_S, HOMEWORK_SCOPE, Principal, current_principal, homework_expiry, homework_times,
                       make_token, remember_user, require_homework)
from .store import get_store
from .timeutil import iso, utcnow

log = logging.getLogger("proctora.homework")

router = APIRouter(prefix="/api/homework", tags=["homework"])

STATE_TTL_S = 600
START_PER_MIN = 20
FINISH_PER_MIN = 10
FINISH_PER_NETID_HOUR = 20
RENEW_PER_MIN = 12
EXCHANGE_PER_MIN = 20
EXCHANGE_PER_USER_HOUR = 10
SESSION_OVER = "This session has ended. Sign in again."
REMEMBER_REFUSED = "This device is no longer remembered. Sign in again."
EXPIRED = "This sign-in has expired. Start again."
REFUSED = "This account cannot sign in here."
UNAVAILABLE = "The sign-in service is not available. Try again in a minute."
TOO_MANY = "Too many tries. Wait a moment and try again."
STATE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
NETID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class RememberIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    remember_token: str = Field(max_length=256)


class RememberCreateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str | None = Field(default=None, max_length=200)


class FinishIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    state: str = Field(max_length=128)
    ticket: str = Field(min_length=1, max_length=512)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _tag(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _prefix(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    return str(ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False))


def _provider(request: Request) -> hw.Provider:
    p = hw.active(request.app.state.settings)
    if p is None:
        raise HTTPException(404, "Not Found")
    return p


def _no_store(body: dict) -> JSONResponse:
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def _new_session(settings, u: User, login_at: int, now: float | None = None) -> dict:
    exp, absolute = homework_expiry(login_at, now)
    token = make_token(settings.secret, u.id, "student", 0, version=u.token_version or 0, scope=HOMEWORK_SCOPE,
                       login_at=login_at, exp=exp)
    return {"token": token, "expires_at": _epoch_iso(exp), "absolute_expires_at": _epoch_iso(absolute)}


def _epoch_iso(ts: int) -> str:
    return iso(datetime.fromtimestamp(ts, timezone.utc))


@router.post("/login/start")
async def start(request: Request):
    provider = _provider(request)
    settings = request.app.state.settings
    store = get_store()
    ip = client_ip(request)
    if await store.incr(f"hwl:rl:start:{ip}", 60) > START_PER_MIN:
        raise HTTPException(429, TOO_MANY)
    state = _b64(secrets.token_bytes(32))
    verifier = _b64(secrets.token_bytes(32))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    await store.set(f"hwl:{state}", json.dumps({"verifier": verifier, "ip": ip, "used": False}), STATE_TTL_S)
    out = provider.start(settings, state, challenge)
    return _no_store({"state": state, "start_url": out["start_url"], "allowed_hosts": out["allowed_hosts"],
                      "finish_url": out["finish_url"], "resume_paths": list(settings.homework_login_resume_paths),
                      "expires_at": iso(utcnow() + timedelta(seconds=STATE_TTL_S))})


@router.post("/login/finish")
async def finish(body: FinishIn, request: Request, session: AsyncSession = Depends(get_session)):
    provider = _provider(request)
    settings = request.app.state.settings
    store = get_store()
    ip = client_ip(request)
    if await store.incr(f"hwl:rl:finish:{ip}", 60) > FINISH_PER_MIN:
        raise HTTPException(429, TOO_MANY)
    tag = _tag(body.state)
    raw = await store.getdel(f"hwl:{body.state}") if STATE_RE.fullmatch(body.state) else None
    if raw is None:
        log.info("homework sign-in refused (state %s unknown or used)", tag)
        raise HTTPException(400, EXPIRED)
    saved = json.loads(raw)
    if _prefix(saved.get("ip") or "") != _prefix(ip):
        log.info("homework sign-in %s finished from another network", tag)

    try:
        person = await provider.redeem(body.ticket, saved["verifier"])
    except hw.HomeworkError as err:
        log.info("homework sign-in %s: provider answered %s", tag, err.status)
        raise HTTPException(err.status, EXPIRED if err.status == 400 else UNAVAILABLE) from None
    netid = person.netid
    if not NETID_RE.fullmatch(netid):
        log.warning("homework sign-in %s: unusable NetID from the provider", tag)
        raise HTTPException(503, UNAVAILABLE)
    if await store.incr(f"hwl:rl:netid:{_tag(netid)}", 3600) > FINISH_PER_NETID_HOUR:
        raise HTTPException(429, TOO_MANY)

    existing = (await session.execute(select(User).where(User.netid == netid))).scalar_one_or_none()
    if existing is not None and existing.role != "student":
        log.info("homework sign-in %s refused: not a student account", tag)
        raise HTTPException(403, REFUSED)
    await session.commit()           # no database connection held while the provider answers
    try:
        has_classes = await provider.has_classes(netid)
    except hw.HomeworkError:
        raise HTTPException(503, UNAVAILABLE) from None
    if not has_classes:
        log.info("homework sign-in %s refused: no classes", tag)
        raise HTTPException(403, REFUSED)

    from .integrations.dkuoj.sync import ensure_user
    u = await ensure_user(session, {"username": netid, "first_name": person.first_name,
                                    "last_name": person.last_name}, "student")
    if u is None:
        await session.rollback()
        raise HTTPException(403, REFUSED)
    await session.commit()
    await remember_user(u.id, u.role, int(u.token_version or 0))
    log.info("homework sign-in %s ok for user %s", tag, u.id)
    return _no_store({**_new_session(settings, u, int(time.time())), "name": u.name, "netid": u.netid or netid,
                      "idle_seconds": HOMEWORK_IDLE_S})


@router.get("/me")
async def me(request: Request, p: Principal = Depends(require_homework), session: AsyncSession = Depends(get_session)):
    u = await session.get(User, p.user_id)
    if u is None:
        raise HTTPException(401, "Invalid or expired token")
    out = {"name": u.name, "netid": u.netid or ""}
    times = homework_times(request.app.state.settings.secret, _bearer(request)) if p.scope == HOMEWORK_SCOPE else None
    if times:
        out["expires_at"] = _epoch_iso(times[0])
        out["absolute_expires_at"] = _epoch_iso(times[1] + HOMEWORK_ABSOLUTE_S)
    return out


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "").partition(" ")[2].strip()


@router.post("/session/renew")
async def renew(request: Request, p: Principal = Depends(current_principal),
                session: AsyncSession = Depends(get_session)):
    _provider(request)
    settings = request.app.state.settings
    times = homework_times(settings.secret, _bearer(request)) if p.scope == HOMEWORK_SCOPE and p.role == "student" else None
    if times is None:
        raise HTTPException(403, "Homework sign-in required")
    if await get_store().incr(f"hwl:rl:renew:{p.user_id}", 60) > RENEW_PER_MIN:
        raise HTTPException(429, TOO_MANY)
    login_at = times[1]
    if time.time() >= login_at + HOMEWORK_ABSOLUTE_S:
        raise HTTPException(401, SESSION_OVER)
    u = await session.get(User, p.user_id)
    if u is None:
        raise HTTPException(401, SESSION_OVER)
    return _no_store(_new_session(settings, u, login_at))


@router.post("/remember/create")
async def remember_create(body: RememberCreateIn, request: Request, p: Principal = Depends(require_homework),
                          session: AsyncSession = Depends(get_session)):
    _provider(request)
    if p.scope != HOMEWORK_SCOPE:
        raise HTTPException(403, "Homework sign-in required")
    token, expires = await rem.create(session, p.user_id, body.label)
    log.info("homework remember created for user %s (%s)", p.user_id, rem.tag(token))
    return _no_store({"remember_token": token, "expires_at": iso(expires)})


@router.post("/remember/exchange")
async def remember_exchange(body: RememberIn, request: Request, session: AsyncSession = Depends(get_session)):
    provider = _provider(request)
    settings = request.app.state.settings
    store = get_store()
    if await store.incr(f"hwl:rl:rexch:{client_ip(request)}", 60) > EXCHANGE_PER_MIN:
        raise HTTPException(429, TOO_MANY)
    row = await rem.find(session, body.remember_token)
    tag = rem.tag(body.remember_token)
    if row is None:
        log.info("homework remember %s refused: unknown", tag)
        raise HTTPException(401, REMEMBER_REFUSED)
    if row.replaced_by is not None:
        await rem.revoke_family(session, row.family_id)
        await session.commit()
        log.warning("homework remember %s reused: family revoked for user %s", tag, row.user_id)
        raise HTTPException(401, REMEMBER_REFUSED)
    if row.revoked_at is not None or row.expires_at <= utcnow():
        log.info("homework remember %s refused: revoked or expired", tag)
        raise HTTPException(401, REMEMBER_REFUSED)
    user_id, family_id = row.user_id, row.family_id
    if await store.incr(f"hwl:rl:ruser:{user_id}", 3600) > EXCHANGE_PER_USER_HOUR:
        raise HTTPException(429, TOO_MANY)
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(401, REMEMBER_REFUSED)
    if u.role != "student":
        raise HTTPException(403, REFUSED)
    netid = u.netid or ""
    await session.commit()
    try:
        has_classes = bool(netid) and await provider.has_classes(netid)
    except hw.HomeworkError:
        raise HTTPException(503, UNAVAILABLE) from None
    if not has_classes:
        log.info("homework remember %s refused: no classes", tag)
        raise HTTPException(403, REFUSED)
    row = await rem.find(session, body.remember_token)
    await session.refresh(u)
    rotated = await rem.rotate(session, row) if row is not None else None
    if rotated is None:
        await rem.revoke_family(session, family_id)
        await session.commit()
        log.warning("homework remember %s lost a race: family revoked for user %s", tag, user_id)
        raise HTTPException(401, REMEMBER_REFUSED)
    new_token, remember_expires = rotated
    await remember_user(u.id, u.role, int(u.token_version or 0))
    log.info("homework remember %s exchanged for user %s (new %s)", tag, u.id, rem.tag(new_token))
    return _no_store({**_new_session(settings, u, int(time.time())), "remember_token": new_token,
                      "remember_expires_at": iso(remember_expires), "name": u.name, "netid": netid})


@router.post("/remember/forget")
async def remember_forget(body: RememberIn, request: Request, session: AsyncSession = Depends(get_session)):
    _provider(request)
    if await get_store().incr(f"hwl:rl:rforget:{client_ip(request)}", 60) > EXCHANGE_PER_MIN:
        raise HTTPException(429, TOO_MANY)
    await rem.forget(session, body.remember_token)
    return _no_store({"ok": True})


@router.post("/remember/forget-all")
async def remember_forget_all(request: Request, p: Principal = Depends(require_homework),
                              session: AsyncSession = Depends(get_session)):
    _provider(request)
    n = await rem.revoke_all(session, p.user_id)
    return _no_store({"ok": True, "revoked": n})

"""Homework sign-in (app/integrations/homework_login.py), mounted only when
FAIRPLAY_HOMEWORK_LOGIN names a provider.

POST /api/homework/login/start   {}                  -> {state, start_url, allowed_hosts, finish_url, resume_paths, expires_at}
POST /api/homework/login/finish  {state, ticket}     -> {token, name, netid, expires_at}
GET  /api/homework/me                                -> {name, netid}

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
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .integrations import homework_login as hw
from .models import User
from .routes_auth import client_ip
from .security import HOMEWORK_SCOPE, HOMEWORK_TTL_S, Principal, make_token, remember_user, require_homework
from .store import get_store
from .timeutil import iso, utcnow

log = logging.getLogger("proctora.homework")

router = APIRouter(prefix="/api/homework", tags=["homework"])

STATE_TTL_S = 600
START_PER_MIN = 20
FINISH_PER_MIN = 10
FINISH_PER_NETID_HOUR = 20
EXPIRED = "This sign-in has expired. Start again."
REFUSED = "This account cannot sign in here."
UNAVAILABLE = "The sign-in service is not available. Try again in a minute."
TOO_MANY = "Too many tries. Wait a moment and try again."
STATE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
NETID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


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
    token = make_token(settings.secret, u.id, "student", HOMEWORK_TTL_S, version=u.token_version or 0,
                       scope=HOMEWORK_SCOPE)
    log.info("homework sign-in %s ok for user %s", tag, u.id)
    return _no_store({"token": token, "name": u.name, "netid": u.netid or netid,
                      "expires_at": iso(utcnow() + timedelta(seconds=HOMEWORK_TTL_S))})


@router.get("/me")
async def me(p: Principal = Depends(require_homework), session: AsyncSession = Depends(get_session)):
    u = await session.get(User, p.user_id)
    if u is None:
        raise HTTPException(401, "Invalid or expired token")
    return {"name": u.name, "netid": u.netid or ""}

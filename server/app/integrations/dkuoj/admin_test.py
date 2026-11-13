"""Operator preview sign-in: one code from the server environment (FAIRPLAY_ADMIN_TEST_CODE)
that lists every DKU OJ exam on the student dashboard, without a per-student code.

The code is never stored, logged or put in an event. Unset or empty = the feature does not
exist. The session may only read the exam list (security.require_student refuses the rest)
and the list never carries questions, answers, keys or start codes.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ... import app_client as ac
from ...db import get_session
from ...models import Exam, User
from ...routes_auth import client_ip
from ...security import ADMIN_SCOPE, ADMIN_TESTER_USER, make_token, remember_user
from ...store import get_store
from ...timeutil import iso, utcnow
from .setup import add_event

log = logging.getLogger("proctora.dkuoj")

router = APIRouter(prefix="/api/student", tags=["student"])

NOT_VALID = "That code is not valid."
TOO_MANY = "Too many tries. Wait a moment and try again."
FAILS_PER_MIN = 10

EXAM_TITLE = "All exams (preview)"
MAX_TTL_S = 4 * 3600


def normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if not ch.isspace() and ch != "-")


def matches(configured: str, entered: str) -> bool:
    want = normalise(configured)
    if not want:
        return False
    return hmac.compare_digest(want.encode(), normalise(entered).encode())


def _network_of(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(a, ipaddress.IPv6Address):
        if a.ipv4_mapped is not None:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


class AdminTestIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str = Field(default="", max_length=128)


async def record_use(session: AsyncSession, ip: str) -> None:
    await add_event(session, 0, "admin_test_login", actor="admin-test", detail=f"from {ip}")
    await session.commit()
    log.warning("admin test code used from %s", ip)


async def tester_user(session: AsyncSession) -> User:
    u = (await session.execute(select(User).where(User.username == ADMIN_TESTER_USER))).scalar_one_or_none()
    if u is None:
        session.add(User(username=ADMIN_TESTER_USER, name="Admin Tester", role="system", password_hash="!",
                         created_at=utcnow()))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        u = (await session.execute(select(User).where(User.username == ADMIN_TESTER_USER))).scalar_one()
    return u


async def preview_exams(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        select(Exam.id, Exam.title, Exam.course, Exam.status, Exam.scheduled_at, Exam.duration_min,
               Exam.question_count)
        .where(Exam.source == "dkuoj", Exam.is_trial.is_(False))
        .order_by(Exam.scheduled_at.desc(), Exam.id))).all()
    return [{"id": r.id, "title": r.title, "course": r.course or "", "status": r.status,
             "scheduled_at": iso(r.scheduled_at), "duration_min": r.duration_min,
             "question_count": r.question_count, "preview": True,
             "mode": "questions", "delivery": "computer", "open_for_me": False, "attempt": None,
             "package_id": None, "tools": {}, "upload_window": None, "class_ids": [],
             "source": "dkuoj"} for r in rows]


@router.post("/admin-test")
async def admin_test_login(body: AdminTestIn, request: Request, session=Depends(get_session)):
    """The operator's own sign-in for the exam-list preview (a hidden form in the web app).
    Answers like a student login: {token, role, name, resume}. Wrong guesses are counted per
    source network and answered with the same 400 whether or not the feature is configured."""
    s = ac.settings_of(request)
    store = get_store()
    ip = client_ip(request)
    key = f"admintest:fail:{_network_of(ip)}"
    if int(await store.get(key) or 0) >= FAILS_PER_MIN:
        wait = max(1, int(await store.ttl(key)))
        raise HTTPException(429, {"message": TOO_MANY, "code": "too_many_codes", "retry_after": wait},
                            headers={"Retry-After": str(wait)})
    if not (s.admin_test_code and matches(s.admin_test_code, body.code)):
        await store.incr(key, 60)
        raise HTTPException(400, NOT_VALID)
    await record_use(session, ip)
    u = await tester_user(session)
    await remember_user(u.id, "student", int(u.token_version or 0))
    ttl = min(s.student_token_ttl_s, MAX_TTL_S)
    return JSONResponse({"token": make_token(s.secret, u.id, "student", ttl, version=u.token_version or 0,
                                             scope=ADMIN_SCOPE),
                         "role": "student", "name": u.name, "resume": False},
                        headers={"Cache-Control": "no-store"})

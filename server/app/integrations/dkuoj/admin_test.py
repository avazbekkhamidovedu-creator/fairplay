"""Operator preview sign-in: one code from the server environment (FAIRPLAY_ADMIN_TEST_CODE)
that lists every DKU OJ exam on the student dashboard, without a per-student code.

The code is never stored, logged or put in an event. Unset or empty = the feature does not
exist. The session may only read the exam list (security.require_student refuses the rest)
and the list never carries questions, answers, keys or start codes.
"""
from __future__ import annotations

import hmac
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Exam, User
from ...security import ADMIN_TESTER_USER
from ...timeutil import iso, utcnow
from . import access_codes
from .setup import add_event

log = logging.getLogger("proctora.dkuoj")

EXAM_TITLE = "All exams (preview)"
MAX_TTL_S = 4 * 3600


def matches(configured: str, entered: str) -> bool:
    want = access_codes.normalise(configured)
    if not want:
        return False
    return hmac.compare_digest(want.encode(), access_codes.normalise(entered).encode())


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

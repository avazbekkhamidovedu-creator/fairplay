"""Per-student exam access codes (docs/dkuoj-bridge-v2.md §12): made for the students on the
exam's synced DKU OJ list, kept sealed so the professor can print the same sheet again."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...lms import secretbox
from ...models import Exam, User
from ...timeutil import utcnow
from .models import ExamAccessCode, OjRosterEntry

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if not ch.isspace() and ch != "-")


def code_hmac(secret: str, code: str) -> str:
    key = hashlib.sha256(("fairplay-exam-access-code:" + secret).encode()).digest()
    return hmac.new(key, normalise(code).encode(), hashlib.sha256).hexdigest()


def split_name(full: str) -> tuple[str, str]:
    first, _, last = " ".join((full or "").split()).partition(" ")
    return first, last


@dataclass
class Sheet:
    rows: list[tuple[ExamAccessCode, str]] = field(default_factory=list)      # (row, plain code)
    skipped: list[str] = field(default_factory=list)                          # roster accounts without a NetID


async def _roster(session: AsyncSession, exam: Exam) -> list[User]:
    return list((await session.execute(select(User).join(OjRosterEntry, OjRosterEntry.user_id == User.id)
                                       .where(OjRosterEntry.exam_id == exam.id).order_by(User.id))).scalars())


async def _rows(session: AsyncSession, exam: Exam) -> dict[str, ExamAccessCode]:
    return {r.netid: r for r in (await session.execute(
        select(ExamAccessCode).where(ExamAccessCode.exam_id == exam.id))).scalars()}


def _new_code(secret: str, taken: set[str]) -> tuple[str, str]:
    while True:
        code = generate_code()
        h = code_hmac(secret, code)
        if h not in taken:
            taken.add(h)
            return code, h


async def ensure_codes(session: AsyncSession, secret: str, exam: Exam) -> Sheet:
    """A code for every roster student who has none; existing codes never change."""
    for attempt in (0, 1):
        try:
            return await _ensure(session, secret, exam)
        except IntegrityError:
            await session.rollback()
            if attempt:
                raise
    raise AssertionError("unreachable")


async def _ensure(session: AsyncSession, secret: str, exam: Exam) -> Sheet:
    users = await _roster(session, exam)
    have = await _rows(session, exam)
    taken = {r.code_hmac for r in have.values()}
    sheet = Sheet()
    for u in users:
        if not u.netid:
            sheet.skipped.append(u.username)
            continue
        first, last = split_name(u.name)
        row = have.get(u.netid)
        if row is None:
            code, h = _new_code(secret, taken)
            row = ExamAccessCode(exam_id=exam.id, netid=u.netid, first_name=first, last_name=last, code_hmac=h,
                                 code_sealed=secretbox.seal(secret, code), created_at=utcnow())
            session.add(row)
        else:
            row.first_name, row.last_name = first, last
            code = secretbox.open_(secret, row.code_sealed)
        sheet.rows.append((row, code))
    await session.commit()
    return sheet


async def reissue(session: AsyncSession, secret: str, exam: Exam, netid: str) -> bool:
    """Replace the student's code (the old one stops matching); False when the NetID is not
    on the exam's list."""
    if netid not in {u.netid for u in await _roster(session, exam) if u.netid}:
        return False
    await ensure_codes(session, secret, exam)
    row = (await session.execute(select(ExamAccessCode).where(
        ExamAccessCode.exam_id == exam.id, ExamAccessCode.netid == netid))).scalar_one()
    taken = {r.code_hmac for r in (await _rows(session, exam)).values()}
    code, h = _new_code(secret, taken)
    row.code_hmac, row.code_sealed = h, secretbox.seal(secret, code)
    row.created_at, row.revoked_at, row.used_at, row.bound_device = utcnow(), None, None, None
    await session.commit()
    return True


async def lookup(session: AsyncSession, secret: str, exam_id: str, code: str) -> ExamAccessCode | None:
    return (await session.execute(select(ExamAccessCode).where(
        ExamAccessCode.exam_id == exam_id, ExamAccessCode.code_hmac == code_hmac(secret, code),
        ExamAccessCode.revoked_at.is_(None)))).scalar_one_or_none()

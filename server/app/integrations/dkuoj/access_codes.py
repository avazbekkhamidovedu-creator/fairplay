"""Per-student exam access codes (docs/dkuoj-bridge-v2.md §12): made for the students on the
exam's synced DKU OJ list, kept sealed so the professor can print the same sheet again."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...lms import secretbox
from ...models import Exam, User
from ...timeutil import utcnow
from .models import ExamAccessCode, OjRosterEntry

WINDOW_LEAD = timedelta(minutes=30)
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


async def _roster(session: AsyncSession, exam: Exam) -> list[tuple[User, str, str]]:
    """(account, first name, last name): the names DKU OJ sent, else the account name split."""
    out = []
    for u, first, last in (await session.execute(
            select(User, OjRosterEntry.first_name, OjRosterEntry.last_name).join(
                OjRosterEntry, OjRosterEntry.user_id == User.id)
            .where(OjRosterEntry.exam_id == exam.id).order_by(User.id))).all():
        if not first and not last:
            first, last = split_name(u.name)
        out.append((u, first or "", last or ""))
    return out


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
    for attempt in (0, 1, 2):
        try:
            return await _ensure(session, secret, exam)
        except IntegrityError:
            await session.rollback()
            await session.refresh(exam)
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


async def _ensure(session: AsyncSession, secret: str, exam: Exam) -> Sheet:
    users = await _roster(session, exam)
    have = await _rows(session, exam)
    taken = {r.code_hmac for r in have.values()}
    sheet = Sheet()
    for u, first, last in users:
        if not u.netid:
            sheet.skipped.append(u.username)
            continue
        row = have.get(u.netid)
        if row is None:
            code, h = _new_code(secret, taken)
            row = ExamAccessCode(exam_id=exam.id, netid=u.netid, first_name=first, last_name=last, code_hmac=h,
                                 code_sealed=secretbox.seal(secret, code), created_at=utcnow())
            session.add(row)
        elif row.revoked_at is not None:
            code, h = _new_code(secret, taken)      # back on the list: a revoked code never comes back
            row.code_hmac, row.code_sealed = h, secretbox.seal(secret, code)
            row.first_name, row.last_name = first, last
            row.created_at, row.revoked_at, row.used_at, row.bound_device = utcnow(), None, None, None
        else:
            row.first_name, row.last_name = first, last
            code = secretbox.open_(secret, row.code_sealed)
        sheet.rows.append((row, code))
    await session.commit()
    return sheet


async def reissue(session: AsyncSession, secret: str, exam: Exam, netid: str) -> bool:
    """Replace the student's code (the old one stops matching); False when the NetID is not
    on the exam's list."""
    if netid not in {u.netid for u, _, _ in await _roster(session, exam) if u.netid}:
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


async def revoke_missing(session: AsyncSession, exam_id: str, netids: set[str]) -> int:
    """Revoke the codes of students who are no longer on the exam's list (no commit)."""
    q = update(ExamAccessCode).where(ExamAccessCode.exam_id == exam_id, ExamAccessCode.revoked_at.is_(None))
    if netids:
        q = q.where(ExamAccessCode.netid.not_in(netids))
    return (await session.execute(q.values(revoked_at=utcnow()))).rowcount or 0


async def lookup(session: AsyncSession, secret: str, exam_id: str, code: str) -> ExamAccessCode | None:
    return (await session.execute(select(ExamAccessCode).where(
        ExamAccessCode.exam_id == exam_id, ExamAccessCode.code_hmac == code_hmac(secret, code),
        ExamAccessCode.revoked_at.is_(None)))).scalar_one_or_none()


async def in_window(session: AsyncSession, e: Exam, now: datetime) -> bool:
    """A code works while the exam is open, or from 30 minutes before its scheduled start,
    and never after it closed (its close time plus the largest extra time)."""
    from ...closing import max_extra
    from ...services import exam_closes_at
    if e.status not in ("draft", "live") or e.delivery == "paper":
        return False
    closes = exam_closes_at(e)
    if closes is not None and now >= closes + await max_extra(session, e):
        return False
    if e.status == "live":
        return True
    return e.scheduled_at is not None and now >= e.scheduled_at - WINDOW_LEAD


def not_yet_open(e: Exam, now: datetime) -> bool:
    """A scheduled draft exam whose codes start working later than now."""
    return (e.status == "draft" and e.delivery != "paper" and e.scheduled_at is not None
            and now < e.scheduled_at - WINDOW_LEAD)


async def matches(session: AsyncSession, secret: str, code: str) -> list[tuple[ExamAccessCode, Exam]]:
    """Every row with this code (revoked ones too) on a draft or live exam, newest exam first."""
    h = code_hmac(secret, code)
    rows = (await session.execute(
        select(ExamAccessCode, Exam).join(Exam, Exam.id == ExamAccessCode.exam_id)
        .where(ExamAccessCode.code_hmac == h, Exam.status.in_(("draft", "live")))
        .order_by(Exam.created_at.desc()))).all()
    return [(r, e) for r, e in rows]


async def exam_has_codes(session: AsyncSession, exam_id: str) -> bool:
    return (await session.execute(select(ExamAccessCode.id).where(
        ExamAccessCode.exam_id == exam_id, ExamAccessCode.revoked_at.is_(None)).limit(1))).first() is not None


async def exams_with_codes(session: AsyncSession, exam_ids: list[str]) -> set[str]:
    if not exam_ids:
        return set()
    return set((await session.execute(select(ExamAccessCode.exam_id).where(
        ExamAccessCode.exam_id.in_(exam_ids), ExamAccessCode.revoked_at.is_(None)).distinct())).scalars())


async def signed_in_here(session: AsyncSession, exam_id: str, user_id: int, device_id: str | None) -> bool:
    """The student's code for this exam is bound to this device: the code sign-in already
    authorised the start (no start code needed)."""
    if not device_id:
        return False
    return (await session.execute(select(ExamAccessCode.id).where(
        ExamAccessCode.exam_id == exam_id, ExamAccessCode.revoked_at.is_(None),
        ExamAccessCode.bound_device == device_id,
        ExamAccessCode.netid == select(User.netid).where(User.id == user_id).scalar_subquery()
    ).limit(1))).first() is not None

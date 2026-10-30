"""Remember me for the homework sign-in: one opaque, rotating, revocable token per device.

Only the sha256 of a token is stored. Each exchange replaces the presented token with a new
one in the same family; the family never lives past 14 days from its creation. Presenting a
token that was already replaced revokes the whole family.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .timeutil import utcnow

REMEMBER_DAYS = 14
MAX_DEVICES = 5
LABEL_MAX = 40
TOKEN_LEN = 43


class HomeworkRemember(Base):
    __tablename__ = "homework_remember"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    family_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(LABEL_MAX), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replaced_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tag(token: str) -> str:
    return hash_token(token)[:8]


def new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def well_formed(token: str) -> bool:
    return len(token) == TOKEN_LEN and all(c.isalnum() or c in "-_" for c in token)


def clean_label(label: str | None) -> str | None:
    label = " ".join((label or "").split())[:LABEL_MAX]
    return label or None


def _row(user_id: int, family_id: str, absolute: datetime, label: str | None, now: datetime) -> tuple[HomeworkRemember, str]:
    token = new_token()
    return HomeworkRemember(id=uuid.uuid4().hex, user_id=user_id, family_id=family_id, token_hash=hash_token(token),
                            label=label, created_at=now, expires_at=absolute, absolute_expires_at=absolute), token


async def create(session: AsyncSession, user_id: int, label: str | None) -> tuple[str, datetime]:
    """A new family for this user; the oldest families beyond MAX_DEVICES are revoked."""
    now = utcnow()
    active = (await session.execute(
        select(HomeworkRemember).where(HomeworkRemember.user_id == user_id, HomeworkRemember.revoked_at.is_(None),
                                       HomeworkRemember.replaced_by.is_(None),
                                       HomeworkRemember.expires_at > now)
        .order_by(HomeworkRemember.absolute_expires_at, HomeworkRemember.created_at))).scalars().all()
    for old in active[:max(0, len(active) - (MAX_DEVICES - 1))]:
        await revoke_family(session, old.family_id, now)
    row, token = _row(user_id, uuid.uuid4().hex, now + timedelta(days=REMEMBER_DAYS), clean_label(label), now)
    session.add(row)
    await session.commit()
    return token, row.expires_at


async def revoke_family(session: AsyncSession, family_id: str, now: datetime | None = None) -> int:
    res = await session.execute(update(HomeworkRemember).where(
        HomeworkRemember.family_id == family_id, HomeworkRemember.revoked_at.is_(None))
        .values(revoked_at=now or utcnow()))
    return res.rowcount or 0


async def revoke_all(session: AsyncSession, user_id: int) -> int:
    fams = (await session.execute(select(HomeworkRemember.family_id).where(
        HomeworkRemember.user_id == user_id, HomeworkRemember.revoked_at.is_(None),
        HomeworkRemember.replaced_by.is_(None), HomeworkRemember.expires_at > utcnow()).distinct())).scalars().all()
    now = utcnow()
    for f in fams:
        await revoke_family(session, f, now)
    await session.commit()
    return len(fams)


async def find(session: AsyncSession, token: str) -> HomeworkRemember | None:
    if not well_formed(token):
        return None
    return (await session.execute(select(HomeworkRemember).where(
        HomeworkRemember.token_hash == hash_token(token)))).scalar_one_or_none()


async def forget(session: AsyncSession, token: str) -> None:
    row = await find(session, token)
    if row is not None:
        await revoke_family(session, row.family_id)
        await session.commit()


async def rotate(session: AsyncSession, row: HomeworkRemember) -> tuple[str, datetime] | None:
    """Replace `row` by a new token of the same family. None when `row` was replaced or revoked
    in the meantime (the family is then revoked by the caller)."""
    now = utcnow()
    new, token = _row(row.user_id, row.family_id, row.absolute_expires_at, row.label, now)
    res = await session.execute(update(HomeworkRemember).where(
        HomeworkRemember.id == row.id, HomeworkRemember.replaced_by.is_(None),
        HomeworkRemember.revoked_at.is_(None)).values(replaced_by=new.id, last_used_at=now))
    if not res.rowcount:
        await session.rollback()
        return None
    session.add(new)
    await session.commit()
    return token, new.expires_at

"""DKU OJ-specific tables (the generic outbox is integrations.models.ExternalDelivery,
answer files are core: models.AnswerFile)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column

from ...db import Base
from ...timeutil import utcnow

BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class OjEditor(Base):
    """A DKU OJ editor of an exam: may open it in FairPlay (exam list, live room)."""
    __tablename__ = "oj_editors"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_oj_editor"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)


class OjRosterEntry(Base):
    """A student DKU OJ lists for an exam: only they may see and start it."""
    __tablename__ = "oj_roster"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_oj_roster"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    extra_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class OjAsset(Base):
    """A file referenced by a statement (fp-asset:<name>), checked against its sha256.
    Bytes in the database (the host's disk is not kept across deploys)."""
    __tablename__ = "oj_assets"
    __table_args__ = (UniqueConstraint("exam_id", "name", name="uq_oj_asset"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OjState(Base):
    """Small named values: the sync cursor and the one-runner lock (holder + expiry)."""
    __tablename__ = "oj_state"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

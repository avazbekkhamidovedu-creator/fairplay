"""Tables of the DKU OJ bridge. Plain (non-FK to new tables) columns where an older
database gets them through create_all; every table here is new, so create_all makes it."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (BigInteger, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, deferred, mapped_column

from ..db import Base
from ..timeutil import utcnow

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


class AnswerFile(Base):
    """A file a student hands in for a file_upload question (bytes in the database)."""
    __tablename__ = "answer_files"
    __table_args__ = (Index("ix_answer_files_attempt", "attempt_id", "question_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OjDelivery(Base):
    """Outbox: one row per handed-in attempt of a DKU OJ exam, written in the same
    transaction as the hand-in. status pending | sent | failed | dead. `payload` is built
    once (so every retry sends the same body: DKU OJ is idempotent on attempt_id and
    answers 409 to a different body); `uploads` maps answer file id -> DKU OJ upload id."""
    __tablename__ = "oj_deliveries"
    __table_args__ = (Index("ix_oj_deliveries_due", "status", "next_at"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    exam_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    tries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    receipt: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploads: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OjState(Base):
    """Small named values: the sync cursor and the one-runner lock (holder + expiry)."""
    __tablename__ = "oj_state"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

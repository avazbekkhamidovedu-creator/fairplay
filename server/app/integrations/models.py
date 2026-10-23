"""Generic integration tables."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..timeutil import utcnow

BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class ExternalDelivery(Base):
    """Outbox: one row per handed-in attempt of an exam from an external system, written in
    the same transaction as the hand-in. status pending | sent | failed | dead. `payload`
    is the body built once (every retry sends the same bytes); `uploads` maps answer file
    id -> the external system's upload id. Sent by the integration's worker."""
    __tablename__ = "external_deliveries"
    __table_args__ = (Index("ix_external_deliveries_due", "source", "status", "next_at"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
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


# Each integration's own tables (registered with Base for create_all)
from .dkuoj import models as _dkuoj_models  # noqa: E402,F401

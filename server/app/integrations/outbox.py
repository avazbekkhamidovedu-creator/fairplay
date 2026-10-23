"""Generic outbox helpers (rows of integrations.models.ExternalDelivery)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..timeutil import iso
from .models import ExternalDelivery


async def enqueue(session: AsyncSession, source: str, aid: str, exam_id: str, now) -> None:
    """Add the outbox row inside the caller's transaction (no commit). A row that is there
    already is left alone, so the hand-in itself can never fail on it."""
    values = {"source": source, "attempt_id": aid, "exam_id": exam_id, "status": "pending", "tries": 0,
              "next_at": now, "created_at": now}
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    await session.execute(insert(ExternalDelivery).values(**values)
                          .on_conflict_do_nothing(index_elements=["attempt_id"]))


def delivery_out(d: ExternalDelivery) -> dict:
    return {"status": d.status, "tries": int(d.tries or 0), "last_error": d.last_error,
            "receipt": d.receipt, "next_at": iso(d.next_at) if d.status in ("pending", "failed") else None,
            "sent_at": iso(d.sent_at)}


async def delivery_status(session: AsyncSession, exam_id: str) -> dict[str, dict]:
    rows = (await session.execute(select(ExternalDelivery).where(ExternalDelivery.exam_id == exam_id))).scalars().all()
    return {d.attempt_id: delivery_out(d) for d in rows}

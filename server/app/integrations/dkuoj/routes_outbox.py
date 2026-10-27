"""Teacher view of the outbox: list stuck hand-ins and send one again."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Exam
from ...security import Principal, require_teacher
from ...timeutil import iso, utcnow
from ..models import ExternalDelivery

router = APIRouter(tags=["integrations"])

LIST_MAX = 200


def _row(d: ExternalDelivery) -> dict:
    return {"id": d.id, "source": d.source, "attempt_id": d.attempt_id, "exam_id": d.exam_id,
            "status": d.status, "tries": int(d.tries or 0), "last_status": d.last_status,
            "last_error": d.last_error, "next_at": iso(d.next_at), "created_at": iso(d.created_at)}


@router.get("/api/deliveries")
async def list_deliveries(status: str = Query(pattern="^(dead|pending)$"), p: Principal = Depends(require_teacher),
                          session: AsyncSession = Depends(get_session)):
    from .source import SOURCE
    states = ("pending", "failed") if status == "pending" else ("dead",)
    rows = (await session.execute(
        select(ExternalDelivery).join(Exam, Exam.id == ExternalDelivery.exam_id)
        .where(ExternalDelivery.status.in_(states), Exam.is_trial.is_(False),
               or_(Exam.teacher_id == p.user_id, Exam.id.in_(SOURCE.teacher_exam_ids(p.user_id))))
        .order_by(ExternalDelivery.id).limit(LIST_MAX))).scalars().all()
    return {"deliveries": [_row(d) for d in rows]}


@router.post("/api/deliveries/{delivery_id}/resend")
async def resend(delivery_id: int, p: Principal = Depends(require_teacher),
                 session: AsyncSession = Depends(get_session)):
    from ...routes_teacher import _own_exam
    d = await session.get(ExternalDelivery, delivery_id)
    if d is None:
        raise HTTPException(404, "Delivery not found")
    await _own_exam(session, d.exam_id, p)
    if d.status == "sent":
        raise HTTPException(409, "This hand-in was already delivered.")
    now = utcnow()
    d.status, d.tries, d.next_at, d.created_at = "pending", 0, now, now
    d.last_error = d.last_status = d.auth_failed_at = d.locked_until = d.claimed_by = None
    await session.commit()
    return _row(d)

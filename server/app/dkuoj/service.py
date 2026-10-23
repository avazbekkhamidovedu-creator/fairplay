"""Small DB helpers the student / teacher routes call for DKU OJ exams."""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Exam
from .models import OjEditor, OjRosterEntry

NOT_READY_DETAIL = "This exam has not arrived from DKU OJ yet. Try again in a minute."
REMOVED_DETAIL = "FairPlay was switched off for this exam in DKU OJ."


async def is_oj_editor(session: AsyncSession, exam_id: str, user_id: int) -> bool:
    return (await session.execute(select(OjEditor.id).where(
        OjEditor.exam_id == exam_id, OjEditor.user_id == user_id).limit(1))).first() is not None


async def check_startable(session: AsyncSession, exam_id: str) -> None:
    """A DKU OJ exam starts only with its questions here, and not after DKU OJ switched
    FairPlay off (unless it is already running: then it finishes as it started)."""
    row = (await session.execute(select(Exam.oj_removed, Exam.oj_frozen_version, Exam.oj_package_version,
                                        Exam.question_count).where(Exam.id == exam_id))).first()
    if row is None:
        raise HTTPException(404, "Exam not found")
    if row.oj_removed and not row.oj_frozen_version:
        raise HTTPException(409, REMOVED_DETAIL)
    if not row.oj_package_version or not row.question_count:
        raise HTTPException(409, NOT_READY_DETAIL)


async def freeze_and_extra_time(session: AsyncSession, exam_id: str, user_id: int) -> timedelta:
    """In the transaction of a new attempt: the first start freezes the package version
    (sync never applies another package after that), and the student's DKU OJ extra time."""
    await session.execute(update(Exam).where(Exam.id == exam_id, Exam.oj_frozen_version.is_(None))
                          .values(oj_frozen_version=Exam.oj_package_version))
    extra = (await session.execute(select(OjRosterEntry.extra_time_seconds).where(
        OjRosterEntry.exam_id == exam_id, OjRosterEntry.user_id == user_id))).scalar()
    return timedelta(seconds=max(0, int(extra or 0)))

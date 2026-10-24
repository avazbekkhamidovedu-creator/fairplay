"""DKU OJ as an ExternalSource (docs/dkuoj-bridge.md). Everything DKU OJ-specific the
core needs goes through these hooks."""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ...models import Exam
from ..base import ExternalSource
from .config import DkuojConfig
from .models import OjAsset, OjEditor, OjRosterEntry, OjState

NOT_READY = "This exam has not arrived from DKU OJ yet. Try again in a minute."
REMOVED = "FairPlay was switched off for this exam in DKU OJ."


class DkuojSource(ExternalSource):
    id = "dkuoj"
    name = "DKU OJ"
    default_review = "nothing"     # 2026-10-24: until DKU OJ says otherwise (its default too)

    def __init__(self):
        self.config = DkuojConfig()

    # ------------------------------------------------------------ setup
    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def configure(self, settings) -> None:
        self.config = DkuojConfig.from_env(getattr(settings, "public_url", ""))

    def routers(self) -> list:
        from .routes_v2 import router as bridge_router
        from .setup import router as setup_router
        return [bridge_router, setup_router]

    def background_tasks(self, settings) -> list:
        from . import sync
        return [sync.run_forever] if sync.BACKGROUND else []

    # ------------------------------------------------------------ teacher side
    def external_url(self, exam) -> str | None:
        return (exam.external_meta or {}).get("url")

    def delete_refused_message(self, exam) -> str | None:
        if exam.external_removed:
            return None
        return "This exam comes from DKU OJ. Switch the FairPlay lock off there to remove it."

    def open_refused_message(self, exam) -> str | None:
        if exam.external_removed:
            return REMOVED
        if not self.package_ready(exam):
            return "The questions arrive from DKU OJ 24 hours before the start. Try again then."
        return super().open_refused_message(exam)

    async def is_teacher(self, session: AsyncSession, exam, user_id: int) -> bool:
        return (await session.execute(select(OjEditor.id).where(
            OjEditor.exam_id == exam.id, OjEditor.user_id == user_id).limit(1))).first() is not None

    def teacher_exam_ids(self, user_id: int):
        return select(OjEditor.exam_id).where(OjEditor.user_id == user_id)

    def closes_at(self, exam):
        """DKU OJ's end time from the last sync (no call to DKU OJ), plus the minutes added
        with "Add time" (external_meta extra_close_minutes, exam_control.extend_exam)."""
        from ...timeutil import parse_iso
        from .sync import extra_close_minutes
        end = (getattr(exam, "external_meta", None) or {}).get("end_time")
        if not isinstance(end, str) or not end:
            return None
        try:
            return parse_iso(end) + timedelta(minutes=extra_close_minutes(exam))
        except ValueError:
            return None

    # ------------------------------------------------------------ student side
    def roster_user_ids(self, exam_id: str):
        return select(OjRosterEntry.user_id).where(OjRosterEntry.exam_id == exam_id)

    async def max_extra_time(self, session: AsyncSession, exam_id: str) -> timedelta:
        top = (await session.execute(select(func.max(OjRosterEntry.extra_time_seconds)).where(
            OjRosterEntry.exam_id == exam_id))).scalar()
        return timedelta(seconds=max(0, int(top or 0)))

    def roster_condition(self, user_id: int):
        return exists().where(OjRosterEntry.exam_id == Exam.id, OjRosterEntry.user_id == user_id)

    async def can_take(self, session: AsyncSession, exam_id: str, user_id: int) -> bool:
        return (await session.execute(select(OjRosterEntry.id).where(
            OjRosterEntry.exam_id == exam_id, OjRosterEntry.user_id == user_id).limit(1))).first() is not None

    async def check_start(self, session: AsyncSession, exam_id: str) -> None:
        """Starts only with its questions here, and not after DKU OJ switched FairPlay off
        (unless already running: then it finishes as it started)."""
        row = (await session.execute(select(Exam.external_removed, Exam.external_frozen_version,
                                            Exam.external_version, Exam.question_count)
                                     .where(Exam.id == exam_id))).first()
        if row is None:
            raise HTTPException(404, "Exam not found")
        if row.external_removed and not row.external_frozen_version:
            raise HTTPException(409, REMOVED)
        if not row.external_frozen_version and not self.enabled:
            await super().check_start(session, exam_id)
        if not row.external_version or not row.question_count:
            raise HTTPException(409, NOT_READY)

    async def on_start(self, session: AsyncSession, exam_id: str, user_id: int) -> timedelta:
        """The first start freezes the package version (sync never applies another one
        after that); the student's DKU OJ extra time."""
        await session.execute(update(Exam).where(Exam.id == exam_id, Exam.external_frozen_version.is_(None))
                              .values(external_frozen_version=Exam.external_version))
        extra = (await session.execute(select(OjRosterEntry.extra_time_seconds).where(
            OjRosterEntry.exam_id == exam_id, OjRosterEntry.user_id == user_id))).scalar()
        return timedelta(seconds=max(0, int(extra or 0)))

    async def on_handin(self, session: AsyncSession, attempt_id: str, exam_id: str, now, evidence: str) -> None:
        from ..outbox import enqueue
        await enqueue(session, self.id, attempt_id, exam_id, now)

    async def asset(self, session: AsyncSession, exam_id: str, name: str):
        a = (await session.execute(select(OjAsset).options(undefer(OjAsset.data))
                                   .where(OjAsset.exam_id == exam_id, OjAsset.name == name))).scalar_one_or_none()
        return (a.data, a.content_type, a.sha256) if a else None

    async def status(self, session: AsyncSession, user_id: int) -> dict:
        """Last sync, outbox counts, and failed hand-ins of this teacher's DKU OJ exams.
        Never the token."""
        from ..models import ExternalDelivery as D
        if not self.enabled:
            return {"enabled": False, "source": self.id, "name": self.name}
        state = {r.name: r.value for r in (await session.execute(select(OjState))).scalars()}
        counts = dict((await session.execute(select(D.status, func.count()).where(D.source == self.id)
                                             .group_by(D.status))).all())
        mine = select(Exam.id).where(Exam.source == self.id,
                                     or_(Exam.teacher_id == user_id, Exam.id.in_(self.teacher_exam_ids(user_id))))
        bad = (await session.execute(select(D).where(D.exam_id.in_(mine), D.status.in_(("dead", "failed")))
                                     .order_by(D.created_at.desc()).limit(100))).scalars().all()
        exams = (await session.execute(select(func.count(Exam.id)).where(Exam.source == self.id))).scalar_one()
        return {"enabled": True, "source": self.id, "name": self.name, "url": self.config.url,
                "last_run": state.get("last_run"), "last_error": state.get("last_error"), "exams": int(exams),
                "deliveries": {k: int(counts.get(k, 0)) for k in ("pending", "failed", "sent", "dead")},
                "problems": [{"attempt_id": d.attempt_id, "exam_id": d.exam_id, "status": d.status,
                              "tries": d.tries, "last_error": d.last_error} for d in bad]}


SOURCE = DkuojSource()

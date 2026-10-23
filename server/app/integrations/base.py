"""The interface the core uses for exams that come from an external system.

FairPlay exams have no integration (registry.integration_for -> None) and the core runs
exactly as without this layer. An external exam's content and grades are owned by its
system: FairPlay runs it (start code, lock, live room, offline / sealed hand-in) and asks
these hooks where the core would otherwise decide by itself.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import false
from sqlalchemy.ext.asyncio import AsyncSession

from ..timeutil import iso


class ExternalSource:
    """Defaults = a read-only archive: content and grades come from elsewhere, nobody can
    start the exam, hand-ins go nowhere. Integrations override what they support. Also
    used for exams whose integration is switched off or no longer installed."""
    id: str = "external"
    name: str = "the external system"

    # ------------------------------------------------------------ setup
    @property
    def enabled(self) -> bool:
        return False

    def configure(self, settings) -> None:
        """Called by create_app; integrations read their own configuration here."""

    def routers(self) -> list[APIRouter]:
        return []

    def background_tasks(self, settings) -> list:
        """Coroutine functions started in the app lifespan while enabled."""
        return []

    # ------------------------------------------------------------ teacher side
    def exam_is_read_only(self, exam) -> bool:
        return True

    def grades_externally(self, exam) -> bool:
        return True

    def edit_refused_message(self, exam) -> str:
        return f"This exam comes from {self.name}. Edit this exam in {self.name}."

    def graded_message(self, exam) -> str:
        return f"This exam is graded in {self.name}. Grade it and release results there."

    def delete_refused_message(self, exam) -> str | None:
        return None

    def open_refused_message(self, exam) -> str | None:
        return None if self.enabled else f"{self.name} is not connected any more; this exam is an archive."

    def external_url(self, exam) -> str | None:
        return (getattr(exam, "external_meta", None) or {}).get("url")

    def package_ready(self, exam) -> bool:
        return bool(getattr(exam, "external_version", None) and getattr(exam, "questions", None))

    def external_block(self, exam) -> dict:
        """The "external" block of teacher views (exam, results, grading queue)."""
        meta = getattr(exam, "external_meta", None) or {}
        return {"source": self.id, "name": self.name, "url": self.external_url(exam),
                "read_only": self.exam_is_read_only(exam), "graded_externally": self.grades_externally(exam),
                "package_ready": self.package_ready(exam),
                "start_time": meta.get("start_time"), "end_time": meta.get("end_time"),
                "key": getattr(exam, "external_key", None),
                "version": getattr(exam, "external_version", None),
                "frozen_version": getattr(exam, "external_frozen_version", None),
                "synced_at": iso(getattr(exam, "external_synced_at", None)),
                "removed": bool(getattr(exam, "external_removed", False)),
                "connected": self.enabled}

    async def is_teacher(self, session: AsyncSession, exam, user_id: int) -> bool:
        """A teacher other than the owner who may work with the exam (e.g. its editors)."""
        return False

    def teacher_exam_ids(self, user_id: int):
        """SQL select of exam ids such teachers see in their list (None = none)."""
        return None

    async def delivery_status(self, session: AsyncSession, exam_id: str) -> dict[str, dict]:
        from .outbox import delivery_status
        return await delivery_status(session, exam_id)

    def closes_at(self, exam):
        """When the exam closes by itself (naive UTC), from the source; None = never."""
        return None

    # ------------------------------------------------------------ student side
    def roster_user_ids(self, exam_id: str):
        """SQL select of the user ids the source lists for the exam (None = nobody)."""
        return None

    async def max_extra_time(self, session: AsyncSession, exam_id: str) -> timedelta:
        """The largest extra time any listed student gets (the automatic close waits for it)."""
        return timedelta(0)

    def roster_condition(self, user_id: int):
        """SQL condition on Exam: the student may see / take it."""
        return false()

    async def can_take(self, session: AsyncSession, exam_id: str, user_id: int) -> bool:
        return False

    def take_refused_message(self) -> str:
        return f"You are not on this exam's list in {self.name}. Ask your teacher."

    async def check_start(self, session: AsyncSession, exam_id: str) -> None:
        """Raise HTTPException when the exam cannot start now."""
        from fastapi import HTTPException
        if not self.enabled:
            raise HTTPException(409, f"{self.name} is not connected any more; this exam is an archive.")

    async def on_start(self, session: AsyncSession, exam_id: str, user_id: int) -> timedelta:
        """In the transaction of a new attempt. Returns extra time for this student."""
        return timedelta(0)

    async def on_handin(self, session: AsyncSession, attempt_id: str, exam_id: str, now, evidence: str) -> None:
        """In the hand-in's transaction (no commit). evidence "server" | "sealed" | "closed"."""

    async def asset(self, session: AsyncSession, exam_id: str, name: str) -> Any | None:
        """(bytes, content_type, sha256) of a statement asset (fp-asset:<name>), or None."""
        return None

    # ------------------------------------------------------------ results
    def result_of(self, attempt) -> dict | None:
        r = getattr(attempt, "external_result", None)
        return r if isinstance(r, dict) else None

    def released(self, attempt, total: float) -> tuple[bool, float, float]:
        """(released, score, total points) for the student and teacher views."""
        r = self.result_of(attempt) if not isinstance(attempt, dict) else attempt
        if not r or not r.get("released"):
            return False, 0.0, total
        try:
            return True, float(r.get("total") or 0), float(r.get("total_points") or total or 0)
        except (TypeError, ValueError):
            return False, 0.0, total

    def grades_for(self, questions: list[dict], result: dict | None) -> dict:
        """A released result as teacher-style grades {qid: {points, comment, ...}} so the
        core's grade_attempt fills the per-question rows. Questions are matched by
        question["external_ref"]["question_id"]."""
        if not result or not result.get("released"):
            return {}
        by_ref = {(q.get("external_ref") or {}).get("question_id"): q["id"] for q in questions}
        out = {}
        for r in result.get("questions") or []:
            qid = by_ref.get(r.get("question_id"))
            if qid is not None:
                out[qid] = {"points": r.get("points") or 0, "comment": r.get("feedback_md") or "",
                            "rubric_scores": None, "graded_at": result.get("updated_at")}
        return out

    # ------------------------------------------------------------ live-room link
    async def live_login(self, session: AsyncSession, params: dict) -> tuple[int, str]:
        """Check a signed link from the external system: (teacher user id, exam id) or
        HTTPException(403)."""
        from fastapi import HTTPException
        raise HTTPException(404, "Not Found")

    async def status(self, session: AsyncSession, user_id: int) -> dict:
        return {"enabled": self.enabled, "source": self.id, "name": self.name}


class ArchiveSource(ExternalSource):
    """Exams of a source that is not installed here: readable, never started."""

    def __init__(self, source: str):
        self.id = source
        self.name = source

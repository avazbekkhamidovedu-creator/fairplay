"""Which integration an exam belongs to. `integration_for(exam)` is None for normal
FairPlay exams (source "fairplay"), so the core runs exactly as without integrations."""
from __future__ import annotations

from .base import ArchiveSource, ExternalSource

FAIRPLAY = "fairplay"
_sources: dict[str, ExternalSource] | None = None


def _load() -> dict[str, ExternalSource]:
    global _sources
    if _sources is None:
        from .dkuoj.source import SOURCE as dkuoj
        _sources = {dkuoj.id: dkuoj}
    return _sources


def sources() -> list[ExternalSource]:
    return list(_load().values())


def teacher_exam_condition(user_id: int):
    from sqlalchemy import and_, or_
    from ..models import Exam
    external = [and_(Exam.source == source.id, Exam.id.in_(ids)) for source in sources()
                if (ids := source.teacher_exam_ids(user_id)) is not None]
    return or_(and_(Exam.source == FAIRPLAY, Exam.teacher_id == user_id), *external)


def get(source_id: str | None) -> ExternalSource | None:
    return _load().get(source_id or "")


def source_of(exam) -> str:
    src = exam.get("source") if isinstance(exam, dict) else getattr(exam, "source", None)
    return src or FAIRPLAY


def integration_for(exam) -> ExternalSource | None:
    """`exam` is an Exam row or the cached student view. A source that is switched off
    still answers (its exams are read-only archives); an unknown one gets ArchiveSource."""
    src = source_of(exam)
    if src == FAIRPLAY:
        return None
    return _load().get(src) or ArchiveSource(src)


def configure(settings) -> None:
    for s in sources():
        s.configure(settings)


def routers() -> list:
    return [r for s in sources() for r in s.routers()]


def background_tasks(settings) -> list:
    return [t for s in sources() if s.enabled for t in s.background_tasks(settings)]

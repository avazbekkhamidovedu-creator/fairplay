"""Generic integration routes.

- `GET /api/integrations/<source>/status`: health of one integration for teachers.
- Statement assets (`fp-asset:<name>` in prompts): `GET /api/student/attempts/{aid}/assets/<name>`
  (attempt owner, with the app session, exam live or attempt in progress),
  `GET /api/student/grades/{exam_id}/assets/<name>` (a student whose result the external
  system released: the pictures of their grade page) and `GET /api/exams/{id}/assets/<name>`
  (the exam's teachers).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .. import app_client as ac
from ..db import get_session
from ..models import Attempt
from ..security import Principal, require_student, require_teacher
from ..services import get_exam_cached
from ..store import get_store
from . import registry

router = APIRouter(tags=["integrations"])

SAFE_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"


def _enabled(source: str):
    integ = registry.get(source)
    if integ is None or not integ.enabled:
        raise HTTPException(404, "Not Found")
    return integ


@router.get("/api/integrations/{source}/status")
async def status(source: str, p: Principal = Depends(require_teacher),
                 session: AsyncSession = Depends(get_session)):
    integ = registry.get(source)
    if integ is None:
        raise HTTPException(404, "Not Found")
    return await integ.status(session, p.user_id)


def _asset_response(found, request: Request) -> Response:
    data, ctype, sha = found
    etag = f'"{sha}"'
    headers = {"ETag": etag, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
               "Content-Security-Policy": SAFE_CSP}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    media = ctype if ctype.startswith("image/") and "svg" not in ctype else "application/octet-stream"
    return Response(data, media_type=media, headers=headers)


@router.get("/api/student/attempts/{aid}/assets/{name}")
async def attempt_asset(aid: str, name: str, request: Request, p: Principal = Depends(require_student),
                        session: AsyncSession = Depends(get_session)):
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    await ac.check_session(request, session, get_store(), aid,
                           {"exam_id": a.exam_id, "session_id": a.session_id, "status": a.status})
    exam = await get_exam_cached(get_store(), session, a.exam_id)
    integ = registry.integration_for(exam) if exam else None
    if integ is None or (exam.get("status") != "live" and a.status != "in_progress"):
        raise HTTPException(404, "Not found")
    if a.status != "in_progress":
        # 2026-10-24 (app.review): handed in while the exam is still live: the pictures are
        # part of the questions, shown only when the review level shows them
        from sqlalchemy import select
        from ..models import Exam
        from ..review import shows_answers
        value = (await session.execute(select(Exam.review_after_handin).where(Exam.id == a.exam_id))).scalar()
        if not shows_answers(integ.review_level(value)):
            raise HTTPException(404, "Not found")
    found = await integ.asset(session, a.exam_id, name)
    if found is None:
        raise HTTPException(404, "Not found")
    return _asset_response(found, request)


@router.get("/api/student/grades/{exam_id}/assets/{name}")
async def grade_asset(exam_id: str, name: str, request: Request, p: Principal = Depends(require_student),
                      session: AsyncSession = Depends(get_session)):
    """Pictures of a student's review / grade page (after the exam: no app session needed).
    2026-10-24 (app.review): whenever the teacher lets students see their answers (the
    prompts are shown with them: "answers", "answers_and_results"), released or not; never
    with "nothing", "score" or "breakdown" (those show no question text)."""
    from sqlalchemy import select
    from ..models import Exam
    from ..review import shows_answers
    e = await session.get(Exam, exam_id)
    integ = registry.integration_for(e) if e is not None else None
    a = (await session.execute(select(Attempt).where(
        Attempt.exam_id == exam_id, Attempt.student_id == p.user_id,
        Attempt.status == "submitted"))).scalar_one_or_none() if integ else None
    if a is None or not shows_answers(integ.review_level(e.review_after_handin)):
        raise HTTPException(404, "Not found")
    found = await integ.asset(session, e.id, name)
    if found is None:
        raise HTTPException(404, "Not found")
    return _asset_response(found, request)


@router.get("/api/exams/{exam_id}/assets/{name}")
async def exam_asset(exam_id: str, name: str, request: Request, p: Principal = Depends(require_teacher),
                     session: AsyncSession = Depends(get_session)):
    from ..routes_teacher import _own_exam
    e = await _own_exam(session, exam_id, p)
    integ = registry.integration_for(e)
    found = await integ.asset(session, e.id, name) if integ else None
    if found is None:
        raise HTTPException(404, "Not found")
    return _asset_response(found, request)

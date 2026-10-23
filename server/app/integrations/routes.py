"""Generic integration routes.

- `GET /integrations/<source>/live?...`: a signed "open the live room" link from the
  external system. The integration checks it (live_login); the browser then goes to
  `/teacher/external-live?code=<one-time, 60 s>` and the page trades the code for a
  normal teacher sign-in at `POST /api/integrations/live/exchange`. No token in a URL.
- `GET /api/integrations/<source>/status`: health of one integration for teachers.
- Statement assets (`fp-asset:<name>` in prompts): `GET /api/student/attempts/{aid}/assets/<name>`
  (attempt owner, with the app session, exam live or attempt in progress) and
  `GET /api/exams/{id}/assets/<name>` (the exam's teachers).
"""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import app_client as ac
from ..db import get_session
from ..models import Attempt, User
from ..security import Principal, make_token, require_student, require_teacher
from ..services import get_exam_cached
from ..store import get_store
from . import registry

router = APIRouter(tags=["integrations"])

CODE_TTL_S = 60
BAD_LINK = "This FairPlay link is not valid any more. Open the live room again."
SAFE_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"


def _enabled(source: str):
    integ = registry.get(source)
    if integ is None or not integ.enabled:
        raise HTTPException(404, "Not Found")
    return integ


@router.get("/integrations/{source}/live", include_in_schema=False)
async def live_link(source: str, request: Request, session: AsyncSession = Depends(get_session)):
    integ = _enabled(source)
    uid, exam_id = await integ.live_login(session, dict(request.query_params))
    code = secrets.token_urlsafe(24)
    await get_store().set(f"extlive:{code}", json.dumps({"uid": uid, "exam_id": exam_id}), CODE_TTL_S)
    return RedirectResponse(f"/teacher/external-live?code={code}", status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class ExchangeIn(BaseModel):
    code: str = Field(min_length=8, max_length=128)


@router.post("/api/integrations/live/exchange")
async def live_exchange(body: ExchangeIn, request: Request, session: AsyncSession = Depends(get_session)):
    """One-time code -> a teacher sign-in (same shape as /api/auth/login) + the exam id."""
    raw = await get_store().getdel(f"extlive:{body.code}")
    if not raw:
        raise HTTPException(403, BAD_LINK)
    data = json.loads(raw)
    u = await session.get(User, data["uid"])
    if u is None or u.role != "teacher":
        raise HTTPException(403, BAD_LINK)
    s = request.app.state.settings
    token = make_token(s.secret, u.id, u.role, s.token_ttl_s, version=u.token_version or 0)
    return {"token": token, "role": "teacher", "name": u.name, "username": u.username,
            "exam_id": data["exam_id"]}


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
    found = await integ.asset(session, a.exam_id, name)
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

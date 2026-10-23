"""DKU OJ exams: files students hand in (file_upload questions) and statement assets
(fp-asset:<name> images) served to the attempt's owner and to the exam's teachers."""
from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from .. import app_client as ac
from ..db import get_session
from ..models import Attempt, Exam
from ..security import Principal, require_student, require_teacher
from ..services import get_exam_cached
from ..store import get_store
from ..timeutil import iso, utcnow
from ..uploads import MULTIPART_OVERHEAD, form_files, read_capped_form
from .models import AnswerFile, OjAsset

student_router = APIRouter(prefix="/api/student/attempts", tags=["dkuoj"])
teacher_router = APIRouter(prefix="/api/exams", tags=["dkuoj"])

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_QUESTION = 10
SAFE_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"


def file_out(f: AnswerFile) -> dict:
    return {"id": f.id, "question_id": f.question_id, "name": f.name, "size": int(f.size),
            "sha256": f.sha256, "created_at": iso(f.created_at)}


async def files_of_attempt(session: AsyncSession, aid: str) -> list[dict]:
    rows = (await session.execute(select(AnswerFile).where(AnswerFile.attempt_id == aid)
                                  .order_by(AnswerFile.created_at, AnswerFile.id))).scalars().all()
    return [file_out(f) for f in rows]


async def file_ids_of_attempt(session: AsyncSession, aid: str) -> dict[str, set[str]]:
    rows = (await session.execute(select(AnswerFile.question_id, AnswerFile.id)
                                  .where(AnswerFile.attempt_id == aid))).all()
    out: dict[str, set[str]] = {}
    for qid, fid in rows:
        out.setdefault(qid, set()).add(fid)
    return out


def _clean_name(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '"<>|:*?')
    return base[:200] or "file"


async def _attempt_and_exam(request: Request, session: AsyncSession, aid: str,
                            p: Principal) -> tuple[Attempt, dict]:
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    await ac.check_session(request, session, get_store(), aid,
                           {"exam_id": a.exam_id, "session_id": a.session_id, "status": a.status})
    exam = await get_exam_cached(get_store(), session, a.exam_id)
    if exam is None or exam.get("source") != "dkuoj":
        raise HTTPException(404, "Not found")
    return a, exam


@student_router.post("/{aid}/files", status_code=201)
async def upload_answer_file(aid: str, question_id: str, request: Request,
                             p: Principal = Depends(require_student),
                             session: AsyncSession = Depends(get_session)):
    """One file for a file_upload question (multipart field `file`). The returned id goes
    into the question's answer list (PUT .../answers)."""
    a, exam = await _attempt_and_exam(request, session, aid, p)
    q = next((x for x in exam.get("questions") or [] if x.get("id") == question_id), None)
    if q is None or q.get("type") != "file_upload":
        raise HTTPException(404, "Question not found")
    if a.status != "in_progress" or utcnow() >= a.deadline_at:
        raise HTTPException(409, "Time is up")
    if await get_store().incr(f"ansfile:{aid}", 60) > 30:
        raise HTTPException(429, "Too many uploads, wait a minute")
    limit = min(int(q.get("max_file_size") or MAX_FILE_BYTES), MAX_FILE_BYTES)
    too_big = f"This question takes files up to {limit // (1024 * 1024) or 1} MB"
    form = await read_capped_form(request, limit + MULTIPART_OVERHEAD, too_big)
    try:
        files = form_files(form, "file")
        if not files:
            raise HTTPException(422, "Choose a file to upload")
        name = _clean_name(files[0].filename or "")
        data = await files[0].read(limit + 1)
    finally:
        await form.close()
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > limit:
        raise HTTPException(413, too_big)
    exts = [e.lower() for e in q.get("allowed_extensions") or []]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if exts and ext not in exts:
        raise HTTPException(422, "Allowed file types: " + ", ".join(exts))
    count = (await session.execute(select(func.count(AnswerFile.id)).where(
        AnswerFile.attempt_id == aid, AnswerFile.question_id == question_id))).scalar_one()
    if count >= MAX_FILES_PER_QUESTION:
        raise HTTPException(409, f"At most {MAX_FILES_PER_QUESTION} files per question")
    f = AnswerFile(id=secrets.token_hex(16), attempt_id=aid, student_id=p.user_id,
                   question_id=question_id, name=name, size=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), data=data, created_at=utcnow())
    session.add(f)
    await session.commit()
    return file_out(f)


@student_router.get("/{aid}/files/{fid}")
async def get_answer_file(aid: str, fid: str, request: Request,
                          p: Principal = Depends(require_student),
                          session: AsyncSession = Depends(get_session)):
    await _attempt_and_exam(request, session, aid, p)
    f = (await session.execute(select(AnswerFile).options(undefer(AnswerFile.data))
                               .where(AnswerFile.id == fid))).scalar_one_or_none()
    if f is None or f.attempt_id != aid:
        raise HTTPException(404, "Not found")
    return Response(f.data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{_ascii(f.name)}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


def _ascii(name: str) -> str:
    return "".join(ch if 32 <= ord(ch) < 127 and ch != '"' else "_" for ch in name) or "file"


def _asset_response(asset: OjAsset, request: Request) -> Response:
    etag = f'"{asset.sha256}"'
    headers = {"ETag": etag, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
               "Content-Security-Policy": SAFE_CSP}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    media = asset.content_type if asset.content_type.startswith("image/") and "svg" not in asset.content_type \
        else "application/octet-stream"
    return Response(asset.data, media_type=media, headers=headers)


async def _asset(session: AsyncSession, exam_id: str, name: str) -> OjAsset:
    a = (await session.execute(select(OjAsset).options(undefer(OjAsset.data))
                               .where(OjAsset.exam_id == exam_id, OjAsset.name == name))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "Not found")
    return a


@student_router.get("/{aid}/assets/{name}")
async def attempt_asset(aid: str, name: str, request: Request,
                        p: Principal = Depends(require_student),
                        session: AsyncSession = Depends(get_session)):
    """fp-asset:<name> of the attempt's exam: the attempt's owner, while the exam is live
    (or the attempt still in progress)."""
    a, exam = await _attempt_and_exam(request, session, aid, p)
    if exam.get("status") != "live" and a.status != "in_progress":
        raise HTTPException(404, "Not found")
    return _asset_response(await _asset(session, a.exam_id, name), request)


@teacher_router.get("/{exam_id}/assets/{name}")
async def exam_asset(exam_id: str, name: str, request: Request,
                     p: Principal = Depends(require_teacher),
                     session: AsyncSession = Depends(get_session)):
    from ..routes_teacher import _own_exam
    e: Exam = await _own_exam(session, exam_id, p)
    return _asset_response(await _asset(session, e.id, name), request)

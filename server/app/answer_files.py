"""Files students hand in for file_upload questions (app.question_types). Bytes in the
database (the host's disk is not kept across deploys)."""
from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from . import app_client as ac
from .db import get_session
from .models import AnswerFile, Attempt
from .security import Principal, require_student
from .services import get_exam_cached
from .store import get_store
from .timeutil import iso, utcnow
from .uploads import MULTIPART_OVERHEAD, form_files, read_capped_form

router = APIRouter(prefix="/api/student/attempts", tags=["student"])

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_QUESTION = 10


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
    if exam is None:
        raise HTTPException(404, "Not found")
    return a, exam


@router.post("/{aid}/files", status_code=201)
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


@router.get("/{aid}/files/{fid}")
async def get_answer_file(aid: str, fid: str, request: Request,
                          p: Principal = Depends(require_student),
                          session: AsyncSession = Depends(get_session)):
    a, exam = await _attempt_and_exam(request, session, aid, p)
    if a.status != "in_progress":
        # Security review 2026-10-24: an external-source exam after hand-in shows the
        # student's answers only at a review level that shows them (like attempt_asset).
        from .integrations import registry
        integ = registry.integration_for(exam)
        if integ is not None:
            from .models import Exam
            from .review import shows_answers
            value = (await session.execute(select(Exam.review_after_handin).where(Exam.id == a.exam_id))).scalar()
            if not shows_answers(integ.review_level(value)):
                raise HTTPException(404, "Not found")
    f = (await session.execute(select(AnswerFile).options(undefer(AnswerFile.data))
                               .where(AnswerFile.id == fid))).scalar_one_or_none()
    if f is None or f.attempt_id != aid:
        raise HTTPException(404, "Not found")
    return Response(f.data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{_ascii(f.name)}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


def _ascii(name: str) -> str:
    return "".join(ch if 32 <= ord(ch) < 127 and ch != '"' else "_" for ch in name) or "file"

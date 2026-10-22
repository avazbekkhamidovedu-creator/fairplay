"""2026-10-23 (exam tools): open-book materials.

On an exam with open_book on, each student uploads up to MAX_FILES files (MAX_BYTES in all)
BEFORE they start: PDF, PNG, JPEG, TXT or MD, checked by their bytes. Once the attempt
exists the list is fixed. During the attempt the exam page fetches the files (with the
attempt's app session) and shows them in its own viewer. The teacher can list and read
every student's files.

Files are served so they can never run script on our origin: a fixed media type from the
sniffed kind (text is always text/plain), `X-Content-Type-Options: nosniff`, `inline` and a
sandbox CSP.

2026-10-23 (review): downloads are streamed from the database in CHUNK pieces (SQL substr
on the bytes, a short session per piece), never loaded whole; the browser may keep a file
for the attempt (`private, max-age=3600`) and re-asks with If-None-Match (the sha256 taken
at upload is the ETag). The page fetches a file only when the student opens it. Uploads
are read only after every check has passed (see upload_material).

Routes:
  GET    /api/student/exams/{exam_id}/materials          own files + limits (before the exam)
  POST   /api/student/exams/{exam_id}/materials          multipart "file" -> 201 file
  DELETE /api/student/exams/{exam_id}/materials/{mid}    204
  GET    /api/student/attempts/{aid}/materials           own files during the attempt
  GET    /api/student/attempts/{aid}/materials/{mid}     the bytes, during the attempt
  GET    /api/exams/{exam_id}/materials                  teacher: every student's files
  GET    /api/exams/{exam_id}/attempts/{aid}/materials   teacher: one student's files
  GET    /api/exams/{exam_id}/materials/{mid}            teacher: the bytes
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import LargeBinary, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import app_client as ac
from .access import require_member
from .db import get_database, get_session
from .models import Attempt, ExamMaterial, User
from .security import Principal, require_student, require_teacher
from .services import find_attempt, get_exam_cached, new_id
from .store import get_store
from .timeutil import iso, utcnow
from .uploads import MULTIPART_OVERHEAD, form_files, read_capped_form

student_router = APIRouter(prefix="/api/student", tags=["materials"])
teacher_router = APIRouter(prefix="/api/exams", tags=["materials"])

MAX_FILES = 5
MAX_BYTES = 20 * 1024 * 1024          # 2026-10-23 (review): 20 MB in all per student and exam (was 50)
CHUNK = 512 * 1024                    # download piece read from the database at a time
MAX_NAME = 120
UPLOADS_PER_MIN = 30
UPLOAD_LOCK_S = 120
MEDIA_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
               "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8"}
TEXT_EXTENSIONS = {".txt": "txt", ".md": "md", ".markdown": "md"}
TYPES_DETAIL = "Use a PDF, PNG, JPEG, TXT or MD file"
LOCKED_DETAIL = "Materials can't be changed once you have started the exam"
TOO_BIG = "Files can be at most 20 MB in all. Remove a file or upload a smaller one."
NOT_OPEN_BOOK = "This exam is not open book"


def sniff_material(data: bytes, filename: str) -> str | None:
    """Kind of an uploaded material from its bytes ("pdf" | "png" | "jpg" | "txt" | "md"),
    or None. Text needs a .txt / .md name, valid UTF-8 and no NUL bytes."""
    if not data:
        return None
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    ext = ""
    m = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
    if m:
        ext = m.group(1).lower()
    kind = TEXT_EXTENSIONS.get(ext)
    if kind is None or b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return kind


KIND_EXTENSIONS = {"pdf": (".pdf",), "png": (".png",), "jpg": (".jpg", ".jpeg"),
                   "txt": (".txt",), "md": (".md", ".markdown")}


def clean_name(filename: str | None, kind: str) -> str:
    """A safe display name: no folders, no control characters, at most MAX_NAME long,
    ending with an extension of the sniffed kind (added when the name has another one)."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in unicodedata.normalize("NFC", name)
                   if unicodedata.category(c)[0] != "C").strip()
    stem, dot, ext = name.rpartition(".")
    if dot and ("." + ext.lower()) in KIND_EXTENSIONS[kind]:
        ext = "." + ext
    else:
        stem, ext = name, KIND_EXTENSIONS[kind][0]
    stem = stem.strip().strip(".") or "file"
    return stem[: MAX_NAME - len(ext)] + ext


def file_out(m: ExamMaterial) -> dict:
    return {"id": m.id, "name": m.name, "kind": m.kind, "media_type": MEDIA_TYPES[m.kind].split(";")[0],
            "size": int(m.size), "created_at": iso(m.created_at)}


def limits_out() -> dict:
    return {"max_files": MAX_FILES, "max_bytes": MAX_BYTES, "types": ["pdf", "png", "jpg", "txt", "md"]}


async def _files(session: AsyncSession, exam_id: str, student_id: int) -> list[ExamMaterial]:
    res = await session.execute(
        select(ExamMaterial).where(ExamMaterial.exam_id == exam_id, ExamMaterial.student_id == student_id)
        .order_by(ExamMaterial.created_at, ExamMaterial.id))
    return list(res.scalars())


def _etag(m: ExamMaterial) -> str:
    # A material never changes once stored, so its id also names its bytes (rows from
    # before the sha256 column have '' there).
    return f'"{m.sha256 or m.id}"'


def _not_modified(request: Request, etag: str) -> bool:
    given = request.headers.get("if-none-match")
    if not given:
        return False
    tags = {t.strip().removeprefix("W/") for t in given.split(",")}
    return "*" in tags or etag in tags


async def _chunks(mid: str, total: int):
    """The bytes of one material, CHUNK at a time. substr(blob, start, n) is 1-based on both
    SQLite (bytes of a BLOB) and PostgreSQL (substr(bytea, int, int) -> bytea), so only one
    piece is ever in memory. Each piece gets its own short session: a slow download never
    holds a pooled connection."""
    pos = 1
    while pos <= total:
        async with get_database().sessionmaker() as s:
            part = (await s.execute(
                select(func.substr(ExamMaterial.data, pos, CHUNK, type_=LargeBinary))
                .where(ExamMaterial.id == mid))).scalar_one_or_none()
        if not part:
            return
        yield bytes(part)
        pos += len(part)


async def file_response(request: Request, session: AsyncSession, m: ExamMaterial) -> Response:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", m.name) or "file"
    etag = _etag(m)
    headers = {
        "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(m.name)}",
        "X-Content-Type-Options": "nosniff",
        # The attempt's own browser may keep it (re-opens cost nothing); never a shared cache.
        "Cache-Control": "private, max-age=3600",
        "ETag": etag,
        # Even opened directly, nothing in it may run or load anything.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    total = (await session.execute(
        select(func.length(ExamMaterial.data)).where(ExamMaterial.id == m.id))).scalar_one()
    headers["Content-Length"] = str(int(total))
    # Hand the request's connection back now: the pieces use their own sessions.
    await session.close()
    return StreamingResponse(_chunks(m.id, int(total)), media_type=MEDIA_TYPES[m.kind], headers=headers)


# ---------------------------------------------------------------- student, before the exam
async def _student_exam(session: AsyncSession, exam_id: str, p: Principal) -> dict:
    exam = await get_exam_cached(get_store(), session, exam_id)
    if exam is None or exam.get("is_trial") or exam.get("delivery") == "paper":
        raise HTTPException(404, "Exam not found")
    await require_member(session, exam, p.user_id)
    return exam


def _open_book(exam: dict) -> bool:
    return bool(exam.get("open_book")) and (exam.get("mode") or "questions") != "external"


async def _can_change(session: AsyncSession, exam: dict, p: Principal) -> tuple[bool, str]:
    if not _open_book(exam):
        return False, NOT_OPEN_BOOK
    if exam.get("status") not in ("draft", "live"):
        return False, "This exam is closed"
    if await find_attempt(session, exam["id"], p.user_id) is not None:
        return False, LOCKED_DETAIL
    return True, ""


@student_router.get("/exams/{exam_id}/materials")
async def my_materials(exam_id: str, p: Principal = Depends(require_student),
                       session: AsyncSession = Depends(get_session)):
    exam = await _student_exam(session, exam_id, p)
    files = await _files(session, exam_id, p.user_id)
    can, why = await _can_change(session, exam, p)
    return {"open_book": _open_book(exam), "can_change": can, "reason": None if can else why,
            "files": [file_out(m) for m in files], "used_bytes": sum(int(m.size) for m in files),
            **limits_out()}


async def _usage(session: AsyncSession, exam_id: str, student_id: int) -> tuple[int, int]:
    count, used = (await session.execute(
        select(func.count(ExamMaterial.id), func.coalesce(func.sum(ExamMaterial.size), 0))
        .where(ExamMaterial.exam_id == exam_id, ExamMaterial.student_id == student_id))).one()
    return int(count), int(used)


@student_router.post("/exams/{exam_id}/materials", status_code=201)
async def upload_material(exam_id: str, request: Request, p: Principal = Depends(require_student),
                          session: AsyncSession = Depends(get_session)):
    """Multipart "file". 2026-10-23 (review): takes the raw request, not a File(...) parameter,
    so FastAPI does not parse the body before require_student: the student, the exam, open
    book, "not started yet", the room left and the rate limit are all checked first, and the
    body is then read with a hard cap of the room left."""
    store = get_store()
    if await store.incr(f"matrate:{p.user_id}", 60) > UPLOADS_PER_MIN:
        raise HTTPException(429, "Too many uploads, wait a minute")
    exam = await _student_exam(session, exam_id, p)
    can, why = await _can_change(session, exam, p)
    if not can:
        raise HTTPException(409, why)
    count, used = await _usage(session, exam_id, p.user_id)
    if count >= MAX_FILES:
        raise HTTPException(409, f"You can upload at most {MAX_FILES} files. Remove one first.")
    room = MAX_BYTES - used
    if room <= 0:
        raise HTTPException(413, TOO_BIG)
    # One upload at a time per student and exam, so two at once can't pass the limits.
    lock = f"matup:{exam_id}:{p.user_id}"
    if not await store.setnx(lock, "1", UPLOAD_LOCK_S):
        raise HTTPException(429, "Another file is still uploading. Wait for it to finish.")
    try:
        form = await read_capped_form(request, room + MULTIPART_OVERHEAD, TOO_BIG)
        try:
            files = form_files(form, "file")
            if not files:
                raise HTTPException(422, "Choose a file to upload")
            file = files[0]
            data = await file.read(room + 1)
        finally:
            await form.close()
        if not data:
            raise HTTPException(422, "The file is empty")
        if len(data) > room:
            raise HTTPException(413, TOO_BIG)
        kind = sniff_material(data, file.filename or "")
        if kind is None:
            raise HTTPException(422, TYPES_DETAIL)
        m = ExamMaterial(id=new_id("m"), exam_id=exam_id, student_id=p.user_id,
                         name=clean_name(file.filename, kind), kind=kind, size=len(data), data=data,
                         sha256=hashlib.sha256(data).hexdigest(), created_at=utcnow())
        session.add(m)
        await session.flush()
        # 2026-10-23 (review): the exam may have been started while the body was arriving;
        # checked again in the same transaction as the insert, so the list stays fixed.
        if await find_attempt(session, exam_id, p.user_id) is not None:
            await session.rollback()
            raise HTTPException(409, LOCKED_DETAIL)
        await session.commit()
        return file_out(m)
    finally:
        await store.delete(lock)


@student_router.delete("/exams/{exam_id}/materials/{mid}", status_code=204)
async def delete_material(exam_id: str, mid: str, p: Principal = Depends(require_student),
                          session: AsyncSession = Depends(get_session)):
    exam = await _student_exam(session, exam_id, p)
    can, why = await _can_change(session, exam, p)
    if not can:
        raise HTTPException(409, why)
    res = await session.execute(delete(ExamMaterial).where(
        ExamMaterial.id == mid, ExamMaterial.exam_id == exam_id, ExamMaterial.student_id == p.user_id))
    if not res.rowcount:
        raise HTTPException(404, "File not found")
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- student, during the exam
async def _attempt_for_materials(request: Request, session: AsyncSession, aid: str,
                                 p: Principal) -> Attempt:
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    await ac.check_session(request, session, get_store(), aid,
                           {"exam_id": a.exam_id, "session_id": a.session_id, "status": a.status})
    if a.status != "in_progress":
        raise HTTPException(409, "The exam is over")
    return a


@student_router.get("/attempts/{aid}/materials")
async def attempt_materials(aid: str, request: Request, p: Principal = Depends(require_student),
                            session: AsyncSession = Depends(get_session)):
    a = await _attempt_for_materials(request, session, aid, p)
    return {"files": [file_out(m) for m in await _files(session, a.exam_id, p.user_id)]}


@student_router.get("/attempts/{aid}/materials/{mid}")
async def attempt_material_file(aid: str, mid: str, request: Request,
                                p: Principal = Depends(require_student),
                                session: AsyncSession = Depends(get_session)):
    a = await _attempt_for_materials(request, session, aid, p)
    m = await session.get(ExamMaterial, mid)
    if m is None or m.exam_id != a.exam_id or m.student_id != p.user_id:
        raise HTTPException(404, "File not found")
    return await file_response(request, session, m)


# ---------------------------------------------------------------- teacher (read only)
async def _teacher_exam(session: AsyncSession, exam_id: str, p: Principal):
    from .routes_teacher import _own_exam
    return await _own_exam(session, exam_id, p)


@teacher_router.get("/{exam_id}/materials")
async def exam_materials(exam_id: str, p: Principal = Depends(require_teacher),
                         session: AsyncSession = Depends(get_session)):
    await _teacher_exam(session, exam_id, p)
    rows = (await session.execute(
        select(ExamMaterial, User.username, User.name).join(User, User.id == ExamMaterial.student_id)
        .where(ExamMaterial.exam_id == exam_id)
        .order_by(User.username, ExamMaterial.created_at, ExamMaterial.id))).all()
    out: dict[str, dict] = {}
    for m, username, name in rows:
        s = out.setdefault(username, {"username": username, "name": name, "files": []})
        s["files"].append(file_out(m))
    return list(out.values())


@teacher_router.get("/{exam_id}/attempts/{aid}/materials")
async def attempt_materials_teacher(exam_id: str, aid: str, p: Principal = Depends(require_teacher),
                                    session: AsyncSession = Depends(get_session)):
    await _teacher_exam(session, exam_id, p)
    a = await session.get(Attempt, aid)
    if a is None or a.exam_id != exam_id:
        raise HTTPException(404, "Attempt not found")
    return {"files": [file_out(m) for m in await _files(session, exam_id, a.student_id)]}


@teacher_router.get("/{exam_id}/materials/{mid}")
async def material_file_teacher(exam_id: str, mid: str, request: Request,
                                p: Principal = Depends(require_teacher),
                                session: AsyncSession = Depends(get_session)):
    await _teacher_exam(session, exam_id, p)
    m = await session.get(ExamMaterial, mid)
    if m is None or m.exam_id != exam_id:
        raise HTTPException(404, "File not found")
    return await file_response(request, session, m)

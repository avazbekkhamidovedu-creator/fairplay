"""Calls from DKU OJ to FairPlay (docs/dkuoj-bridge-v2.md §3), signed oj->fp (§1).

Prefix /api/integrations/dkuoj/v1. DKU OJ has already checked that the professor
(X-FP-Acting-User) may manage the class / assignment; exam endpoints check again
against the editors from FairPlay's last sync (403 otherwise). The exam is keyed by its
DKU OJ assignment key (Exam.external_key, source "dkuoj"). Running the exam goes through
app.exam_control, the same code as the FairPlay teacher's buttons.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import exam_control as control
from ...db import get_session
from ...models import Attempt, Exam, IntegrityEvent, SchoolClass, ClassMember, User
from ...services import exam_closes_at
from ...store import get_store
from ...timeutil import iso, utcnow
from . import signing
from .models import ClassLogin, ClassLoginEvent, OjEditor, OjRosterEntry
from .setup import add_event, create_class_login, disable_logins, is_set_up, login_active, open_login_password

log = logging.getLogger("proctora.dkuoj")

PREFIX = "/api/integrations/dkuoj/v1"
router = APIRouter(prefix=PREFIX, tags=["dkuoj-bridge"])

MAX_BODY = 64 * 1024
MAX_OPEN_TO = 2000
ONLINE_WINDOW_S = 15
MAX_REPORT_EVENTS = 500
MAX_WARNINGS = 5
NOT_EDITOR = "You are not an editor of this exam in FairPlay yet. Wait two minutes and try again."
NOT_SYNCED = "This exam has not arrived in FairPlay yet. Try again in two minutes."
BAD_SIGNATURE = "bad signature"
NOT_IN_CLASS = ("This student is not linked to this class in FairPlay: not set up with its class login "
                "and not on the student list of one of its exams.")


# ---------------------------------------------------------------- signed calls
@dataclass
class BridgeCall:
    acting_user: str
    data: dict = field(default_factory=dict)


def _raw_path(request: Request) -> str:
    """The path exactly as sent, with its query string (what DKU OJ signed)."""
    scope = request.scope
    raw = scope.get("raw_path")
    path = raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) else scope.get("path", "")
    qs = scope.get("query_string") or b""
    return path + ("?" + qs.decode("latin-1") if qs else "")


async def signed_call(request: Request) -> BridgeCall:
    """FastAPI dependency: 404 while the bridge is off, 413 for big bodies (before hashing),
    401 for anything not signed right (also a replayed nonce or a stale timestamp)."""
    from .source import SOURCE
    if not SOURCE.enabled:
        raise HTTPException(404, "Not Found")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY:
                raise HTTPException(413, "Request body too large")
        except ValueError:
            raise HTTPException(400, "Bad Content-Length") from None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise HTTPException(413, "Request body too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        acting = await signing.verify(SOURCE.config.token, signing.OJ_TO_FP, request.method, _raw_path(request),
                                      request.headers, body, get_store())
    except signing.BadSignature:
        raise HTTPException(401, BAD_SIGNATURE) from None
    data: dict = {}
    if body:
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(400, "The body is not JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "The body must be a JSON object")
    return BridgeCall(acting, data)


def _str(data: dict, name: str, max_len: int = 200) -> str:
    v = data.get(name)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise HTTPException(400, f"{name} must be text")
    return v.strip()[:max_len]


async def _user_by_netid(session: AsyncSession, netid: str, role: str = "student") -> User | None:
    """The account of a NetID. Fallback for a v1 bridge account not linked by a sync yet
    (sync.legacy_account), and only with `role`: a student NetID can never land on a
    teacher account by its username (and the other way round)."""
    from .sync import legacy_account
    if not netid:
        return None
    u = (await session.execute(select(User).where(User.netid == netid))).scalar_one_or_none()
    if u is None:
        u = await legacy_account(session, netid)
        if u is not None and u.role != role:
            u = None
    return u


# ---------------------------------------------------------------- class logins / students
@router.post("/classes/{class_id}/login")
async def class_login_create(class_id: int, request: Request, call: BridgeCall = Depends(signed_call),
                             session: AsyncSession = Depends(get_session)):
    class_name = _str(call.data, "class_name") or f"Class {class_id}"
    course_name = _str(call.data, "course_name")
    cl, password = await create_class_login(session, request.app.state.settings, class_id, class_name,
                                            course_name, call.acting_user)
    log.info("DKU OJ class login created for class %s by %s", class_id, call.acting_user or "-")
    return JSONResponse({"username": cl.username, "password": password, "expires_at": iso(cl.expires_at)},
                        headers={"Cache-Control": "no-store"})


@router.delete("/classes/{class_id}/login")
async def class_login_delete(class_id: int, call: BridgeCall = Depends(signed_call),
                             session: AsyncSession = Depends(get_session)):
    n = await disable_logins(session, class_id, "stopped", utcnow())
    if n:
        await add_event(session, class_id, "login_stopped", actor=call.acting_user)
    await session.commit()
    return {"ok": True}


HISTORY_MAX = 20
_REASONS = {"replaced": "replaced", "stopped": "stopped", "too_many_failures": "locked"}


def _login_out(cl: ClassLogin | None, secret: str) -> dict | None:
    """The active login, with its password (None for a login made before passwords were
    kept encrypted: the professor creates a new one to see it)."""
    if cl is None:
        return None
    return {"username": cl.username, "password": open_login_password(secret, cl.username, cl.password_enc),
            "created_at": iso(cl.created_at), "expires_at": iso(cl.expires_at), "used": int(cl.used or 0),
            "created_by": cl.created_by or None}


def _history_out(cl: ClassLogin, now) -> dict:
    """One past or current login, never its password. A login that ran out before it was
    replaced or stopped counts as expired (at its expiry time)."""
    ended_at, reason = None, None
    if cl.disabled_at is not None and cl.disabled_at < cl.expires_at:
        ended_at, reason = cl.disabled_at, _REASONS.get(cl.disabled_reason or "", "stopped")
    elif cl.expires_at <= now or cl.disabled_at is not None:
        ended_at, reason = cl.expires_at, "expired"
    return {"username": cl.username, "created_at": iso(cl.created_at), "expires_at": iso(cl.expires_at),
            "ended_at": iso(ended_at), "end_reason": reason, "used": int(cl.used or 0),
            "created_by": cl.created_by or None}


def _student_out(u: User, via: set[str]) -> dict:
    up = is_set_up(u)
    return {"netid": u.netid, "set_up": up, "username": u.username if up else None,
            "set_up_at": iso(u.set_up_at), "via_this_class": (u.netid or "") in via}


EXAM_LINK_DAYS = 180     # exams that still link a student to a class: not closed, or changed lately


async def setup_netids(session: AsyncSession, class_id: int) -> set[str]:
    """NetIDs that set up through one of this class's logins (setup_ok events)."""
    return set((await session.execute(select(ClassLoginEvent.netid).where(
        ClassLoginEvent.class_id == class_id, ClassLoginEvent.kind == "setup_ok",
        ClassLoginEvent.netid.is_not(None)))).scalars())


async def class_user_ids(session: AsyncSession, class_id: int, netids: set[str] | None = None) -> set[int]:
    """Accounts linked to this class: set up through its logins, members of its FairPlay
    mirror class, or on a synced exam roster of an exam for this class (exams not closed
    or changed in the last EXAM_LINK_DAYS days: the scan of external_meta stays bounded)."""
    if netids is None:
        netids = await setup_netids(session, class_id)
    ids = set((await session.execute(select(User.id).where(User.netid.in_(netids)))).scalars()) if netids else set()
    ids |= set((await session.execute(select(ClassMember.user_id).join(
        SchoolClass, SchoolClass.id == ClassMember.class_id).where(
        SchoolClass.external_ref == f"dkuoj:{class_id}"))).scalars())
    since = utcnow() - timedelta(days=EXAM_LINK_DAYS)
    exams = (await session.execute(select(Exam.id, Exam.external_meta).where(
        Exam.source == "dkuoj", or_(Exam.status != "closed", Exam.updated_at >= since)))).all()
    mine = [eid for eid, meta in exams
            if any(isinstance(c, dict) and c.get("id") == class_id for c in (meta or {}).get("classes") or [])]
    if mine:
        ids |= set((await session.execute(select(OjRosterEntry.user_id).where(
            OjRosterEntry.exam_id.in_(mine)))).scalars())
    return ids


@router.get("/classes/{class_id}/status")
async def class_status(class_id: int, request: Request, call: BridgeCall = Depends(signed_call),
                       session: AsyncSession = Depends(get_session)):
    """The class's login (with its password: the professor may copy it again), the login
    history (newest first, at most HISTORY_MAX, the active one included) and its students.
    no-store: the answer carries a password."""
    now = utcnow()
    logins = (await session.execute(select(ClassLogin).where(ClassLogin.class_id == class_id)
                                    .order_by(ClassLogin.id.desc()).limit(HISTORY_MAX))).scalars().all()
    active = next((cl for cl in logins if login_active(cl, now)), None)
    via = await setup_netids(session, class_id)
    ids = await class_user_ids(session, class_id, via)
    users = (await session.execute(select(User).where(User.id.in_(ids), User.role == "student",
                                                      User.netid.is_not(None))
                                   .order_by(User.netid))).scalars().all() if ids else []
    recent = (await session.execute(select(ClassLoginEvent).where(ClassLoginEvent.class_id == class_id)
                                    .order_by(ClassLoginEvent.id.desc()).limit(50))).scalars().all()
    return JSONResponse(
        {"login": _login_out(active, request.app.state.settings.secret),
         "history": [_history_out(cl, now) for cl in logins],
         "students": [_student_out(u, via) for u in users],
         "recent": [{"at": iso(ev.at), "kind": ev.kind, "netid": ev.netid, "actor": ev.actor or None,
                     "detail": ev.detail or ""} for ev in recent]},
        headers={"Cache-Control": "no-store"})


@router.post("/students/{netid}/reset")
async def student_reset(netid: str, call: BridgeCall = Depends(signed_call),
                        session: AsyncSession = Depends(get_session)):
    from ...routes_auth import bump_token_version
    from .sync import IMPORTED_PASSWORD, placeholder_username
    class_id = call.data.get("class_id")
    if not isinstance(class_id, int) or isinstance(class_id, bool):
        raise HTTPException(400, "class_id must be a number")
    netid = netid.strip()[:64]
    u = await _user_by_netid(session, netid)
    if u is None:
        return {"ok": True}                       # nothing to reset (idempotent)
    if u.role != "student":                       # a teacher's NetID (the fallback never finds one)
        raise HTTPException(400, "Only student accounts can be reset")
    # Defence in depth (DKU OJ checks too): only a student linked to THIS class.
    if u.id not in await class_user_ids(session, class_id):
        raise HTTPException(403, NOT_IN_CLASS)
    u.netid = netid
    u.username = placeholder_username(netid)
    u.password_hash = IMPORTED_PASSWORD
    u.set_up_at = None
    await add_event(session, class_id, "student_reset", netid=netid, actor=call.acting_user)
    await session.commit()
    await bump_token_version(session, u.id)       # every session of the account ends now
    log.info("DKU OJ reset of a student account (class %s) by %s", class_id, call.acting_user or "-")
    return {"ok": True}


# ---------------------------------------------------------------- exams
async def _exam_by_key(session: AsyncSession, key: str) -> Exam | None:
    return (await session.execute(select(Exam).where(Exam.external_key == key, Exam.source == "dkuoj"))
            ).scalar_one_or_none()


async def _editor_exam(session: AsyncSession, key: str, call: BridgeCall, required: bool = True) -> Exam | None:
    """The exam (None / 409 when not synced yet); 403 unless the acting user edits it."""
    e = await _exam_by_key(session, key)
    if e is None:
        if required:
            raise HTTPException(409, NOT_SYNCED)
        return None
    u = await _user_by_netid(session, call.acting_user, role="teacher")
    allowed = u is not None and (await session.execute(select(OjEditor.id).where(
        OjEditor.exam_id == e.id, OjEditor.user_id == u.id).limit(1))).first() is not None
    if not allowed:
        raise HTTPException(403, NOT_EDITOR)
    return e


def _num(x) -> int | float:
    x = float(x or 0)
    return int(x) if x.is_integer() else x


async def exam_view(session: AsyncSession, e: Exam) -> dict:
    """The FairPlay tab's data (v2 §3 GET /exams/<key>)."""
    from ...routes_teacher import NON_FLAG_TYPES
    from .source import SOURCE
    now = utcnow()
    nq = int(e.question_count or 0)
    roster = (await session.execute(select(User).join(OjRosterEntry, OjRosterEntry.user_id == User.id)
                                    .where(OjRosterEntry.exam_id == e.id).order_by(User.name, User.id))
              ).scalars().all()
    attempts = {a.student_id: a for a in (await session.execute(
        select(Attempt).where(Attempt.exam_id == e.id))).scalars()}
    extra = [uid for uid in attempts if uid not in {u.id for u in roster}]
    if extra:      # took the exam and left the roster since: still shown
        roster = list(roster) + list((await session.execute(select(User).where(User.id.in_(extra)))).scalars())
    hb, _, _ = await get_store().live_snapshot(e.id)
    flags = (await session.execute(
        select(IntegrityEvent.attempt_id, IntegrityEvent.type, IntegrityEvent.detail)
        .where(IntegrityEvent.exam_id == e.id, IntegrityEvent.review == "open",
               IntegrityEvent.type.not_in(NON_FLAG_TYPES))
        .order_by(IntegrityEvent.id.desc()).limit(2000))).all()
    warnings: dict[str, list[str]] = {}
    for aid, typ, detail in flags:
        w = warnings.setdefault(aid, [])
        if len(w) < MAX_WARNINGS:
            w.append((detail or typ.replace("_", " "))[:200])
    t = time.time()
    counts = {"roster": len(roster), "set_up": 0, "not_started": 0, "writing": 0, "offline": 0,
              "waiting": 0, "handed_in": 0}
    students = []
    for u in roster:
        a = attempts.get(u.id)
        up = is_set_up(u)
        counts["set_up"] += int(up)
        if a is None:
            status = "not_started"
        elif a.status == "submitted":
            status = "handed_in"
        elif a.pending_resume:
            status = "waiting"
        else:
            h = None
            if a.id in hb:
                try:
                    h = json.loads(hb[a.id])
                except ValueError:
                    h = None
            status = "writing" if h and t - float(h.get("t") or 0) <= ONLINE_WINDOW_S else "offline"
        counts[status] += 1
        students.append({
            "netid": u.netid or u.username, "name": u.name, "set_up": up, "status": status,
            "answered": min(int((a.answered if a else 0) or 0), nq), "questions": nq,
            "seconds_left": (max(0, int((a.deadline_at - now).total_seconds()))
                             if a is not None and a.status == "in_progress" else None),
            "warnings": list(reversed(warnings.get(a.id, []))) if a else []})
    open_to: str | list[str] = "all"
    if e.open_to is not None:
        ids = sorted({int(x) for x in e.open_to})
        open_to = sorted(n for n in (await session.execute(
            select(User.netid).where(User.id.in_(ids)))).scalars() if n) if ids else []
    live = e.status == "live"
    return {"synced": True, "package_ready": SOURCE.package_ready(e), "questions": nq,
            "points": _num(e.total_points), "status": e.status,
            "start_code": e.start_code if live else None,
            "invigilator_code": e.invigilator_code if live else None,
            "closes_at": iso(exam_closes_at(e)), "open_to": open_to,
            "settings": {"calculator": e.calculator or "off", "open_book": bool(e.open_book),
                         "websites": list(e.websites or []), "strong_lock": bool(e.require_strong_lock),
                         "resume_needs_approval": bool(e.resume_needs_approval),
                         # 2026-10-24: student review after hand-in (app.review)
                         "review": SOURCE.review_level(e.review_after_handin)},
            "counts": counts, "students": students, "server_time": iso(now)}


def _not_synced() -> dict:
    return {"synced": False, "package_ready": False, "questions": 0, "points": 0, "status": None,
            "start_code": None, "invigilator_code": None, "closes_at": None, "open_to": "all",
            "settings": None, "counts": None, "students": [], "server_time": iso(utcnow())}


async def _view(session: AsyncSession, e: Exam) -> JSONResponse:
    await session.refresh(e)
    return JSONResponse(await exam_view(session, e), headers={"Cache-Control": "no-store"})


@router.get("/exams/{key}")
async def exam_get(key: str, call: BridgeCall = Depends(signed_call), session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call, required=False)
    if e is None:
        return _not_synced()
    return await _view(session, e)


async def _open_to_ids(session: AsyncSession, e: Exam, value) -> str | list[int]:
    if value is None or value == "all":
        return "all"
    if not isinstance(value, list) or len(value) > MAX_OPEN_TO or not all(isinstance(x, str) for x in value):
        raise HTTPException(400, "open_to must be \"all\" or a list of NetIDs")
    wanted = {x.strip() for x in value if x.strip()}
    rows = dict((await session.execute(select(User.netid, User.id).join(
        OjRosterEntry, OjRosterEntry.user_id == User.id).where(
        OjRosterEntry.exam_id == e.id, User.netid.in_(wanted)))).all()) if wanted else {}
    unknown = sorted(wanted - set(rows))
    if unknown:
        raise HTTPException(400, f"Not on this exam's list in FairPlay yet: {', '.join(unknown[:10])}")
    return sorted(rows.values())


@router.post("/exams/{key}/open")
async def exam_open(key: str, call: BridgeCall = Depends(signed_call), session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    ids = await _open_to_ids(session, e, call.data.get("open_to", "all"))
    if e.status == "live":                         # idempotent: only who may start changes
        await control.set_open_to(session, e, ids)
    else:
        await control.open_exam(session, e, ids)
    log.info("DKU OJ exam %s opened by %s", key, call.acting_user)
    return await _view(session, e)


@router.post("/exams/{key}/close")
async def exam_close(key: str, call: BridgeCall = Depends(signed_call), session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    await control.close_exam(session, e)
    log.info("DKU OJ exam %s closed by %s", key, call.acting_user)
    return await _view(session, e)


@router.post("/exams/{key}/add-time")
async def exam_add_time(key: str, call: BridgeCall = Depends(signed_call),
                        session: AsyncSession = Depends(get_session)):
    minutes = call.data.get("minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 120:
        raise HTTPException(400, "minutes must be a whole number from 1 to 120")
    e = await _editor_exam(session, key, call)
    await control.extend_exam(session, e, minutes)
    log.info("DKU OJ exam %s: %d min added by %s", key, minutes, call.acting_user)
    return await _view(session, e)


@router.post("/exams/{key}/new-code")
async def exam_new_code(key: str, call: BridgeCall = Depends(signed_call),
                        session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    await control.rotate_start_code(session, e)
    return await _view(session, e)


@router.post("/exams/{key}/review")
async def exam_review(key: str, call: BridgeCall = Depends(signed_call),
                      session: AsyncSession = Depends(get_session)):
    """2026-10-24: the professor changed "Student review after hand-in" in DKU OJ (any time:
    before, during or after the exam). Body {"review": "nothing|answers|answers_and_results",
    "updated_at": "<iso>"}; an updated_at older than the value FairPlay has is ignored (the
    newer DKU OJ value wins, whichever of this call and the exam list arrives first)."""
    from ...review import LEVELS
    from .sync import apply_review
    value = call.data.get("review")
    if value not in LEVELS:
        raise HTTPException(400, "review must be one of: " + ", ".join(LEVELS))
    updated_at = call.data.get("updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        raise HTTPException(400, "updated_at must be an ISO-8601 time")
    e = await _editor_exam(session, key, call)
    if apply_review(e, value, updated_at):
        e.updated_at = utcnow()
    await session.commit()
    log.info("DKU OJ exam %s: student review set to %s by %s", key, value, call.acting_user)
    return await _view(session, e)


async def _attempt_of(session: AsyncSession, e: Exam, netid: str) -> tuple[User | None, Attempt | None]:
    u = await _user_by_netid(session, netid.strip()[:64])
    if u is None:
        return None, None
    a = (await session.execute(select(Attempt).where(Attempt.exam_id == e.id, Attempt.student_id == u.id))
         ).scalar_one_or_none()
    return u, a


@router.post("/exams/{key}/approve")
async def exam_approve(key: str, call: BridgeCall = Depends(signed_call),
                       session: AsyncSession = Depends(get_session)):
    netid = call.data.get("netid")
    if not isinstance(netid, str) or not netid.strip():
        raise HTTPException(400, "netid is required")
    e = await _editor_exam(session, key, call)
    _, a = await _attempt_of(session, e, netid)
    if a is None:
        raise HTTPException(404, "This student has not started the exam")
    await control.approve_resume(session, a)
    return await _view(session, e)


@router.get("/exams/{key}/students/{netid}/report")
async def exam_report(key: str, netid: str, call: BridgeCall = Depends(signed_call),
                      session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    u, a = await _attempt_of(session, e, netid)
    if u is None:
        raise HTTPException(404, "No FairPlay account for this NetID")
    on_roster = (await session.execute(select(OjRosterEntry.id).where(
        OjRosterEntry.exam_id == e.id, OjRosterEntry.user_id == u.id).limit(1))).first() is not None
    if a is None and not on_roster:
        raise HTTPException(404, "This student is not on this exam's list")
    events = []
    status = "not_started"
    if a is not None:
        status = "handed_in" if a.status == "submitted" else ("waiting" if a.pending_resume else "writing")
        rows = (await session.execute(select(IntegrityEvent).where(IntegrityEvent.attempt_id == a.id)
                                      .order_by(IntegrityEvent.server_time, IntegrityEvent.id)
                                      .limit(MAX_REPORT_EVENTS))).scalars().all()
        events = [{"at": iso(ev.server_time), "kind": ev.type, "detail": ev.detail or ""} for ev in rows]
    return {"name": u.name, "netid": u.netid or u.username, "status": status,
            "started_at": iso(a.started_at) if a else None,
            "handed_in_at": iso(a.submitted_at) if a else None, "events": events}


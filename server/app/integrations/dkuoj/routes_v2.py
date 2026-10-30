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
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
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
        acting = await signing.verify(SOURCE.config.verify_tokens, signing.OJ_TO_FP, request.method, _raw_path(request),
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


@router.post("/sync")
async def sync_ping(call: BridgeCall = Depends(signed_call)):
    """DKU OJ saved an assignment: sync it now instead of at the next loop pass. Any acting
    user: it only triggers a refresh. Answers at once, the work runs in the background."""
    from . import guard, ping
    body = ping.parse(call.data)
    await ping.check_rate(body.exam_key)
    queued = await ping.start(body.exam_key or guard.ALL)
    return {"ok": True, "queued": queued}


NOT_CLASS_STAFF = "You are not linked to this class in FairPlay."


async def _class_exam_ids(session: AsyncSession, class_id: int) -> list[str]:
    since = utcnow() - timedelta(days=EXAM_LINK_DAYS)
    exams = (await session.execute(select(Exam.id, Exam.external_meta).where(
        Exam.source == "dkuoj", or_(Exam.status != "closed", Exam.updated_at >= since)))).all()
    return [eid for eid, meta in exams
            if any(isinstance(c, dict) and c.get("id") == class_id for c in (meta or {}).get("classes") or [])]


async def _require_class_staff(session: AsyncSession, class_id: int, call: BridgeCall) -> None:
    """The acting DKU OJ user must be tied to the class. A class FairPlay knows (a mirror
    class, an exam of it, a login or log entry made by someone) admits only its owner, an
    editor of one of its exams, or someone who already acted on it. A class FairPlay has
    never seen is open to any acting user that is not a student account (the first login
    is how a class reaches FairPlay); that user is then recorded on it."""
    acting = (call.acting_user or "").strip()
    if not acting:
        raise HTTPException(403, NOT_CLASS_STAFF)
    who = await _user_by_netid(session, acting, role="teacher")
    if who is not None and who.role == "student":
        raise HTTPException(403, NOT_CLASS_STAFF)
    teacher = who if who is not None and who.role == "teacher" else None
    mirror = (await session.execute(select(SchoolClass.teacher_id).where(
        SchoolClass.external_ref == f"dkuoj:{class_id}"))).scalars().all()
    if teacher is not None and teacher.id in mirror:
        return
    exam_ids = await _class_exam_ids(session, class_id)
    if teacher is not None and exam_ids and (await session.execute(select(OjEditor.id).where(
            OjEditor.exam_id.in_(exam_ids), OjEditor.user_id == teacher.id).limit(1))).first():
        return
    made = set((await session.execute(select(ClassLogin.created_by).where(ClassLogin.class_id == class_id))).scalars())
    acted = set((await session.execute(select(ClassLoginEvent.actor).where(
        ClassLoginEvent.class_id == class_id))).scalars())
    if acting in made or acting in acted:
        return
    if mirror or exam_ids or (made | acted) - {None, ""}:
        raise HTTPException(403, NOT_CLASS_STAFF)


# ---------------------------------------------------------------- class logins / students
@router.post("/classes/{class_id}/login")
async def class_login_create(class_id: int, request: Request, call: BridgeCall = Depends(signed_call),
                             session: AsyncSession = Depends(get_session)):
    await _require_class_staff(session, class_id, call)
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
    await _require_class_staff(session, class_id, call)
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
    mine = await _class_exam_ids(session, class_id)
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
    await _require_class_staff(session, class_id, call)
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


ALSO_NATIVE = "This account is also used for FairPlay classes; reset it in FairPlay."


async def uses_fairplay_natively(session: AsyncSession, user_id: int) -> bool:
    """A member of a class made in FairPlay (not a DKU OJ mirror), of an LMS course, or with
    an attempt on an exam that is not from DKU OJ."""
    from ...models import CourseMember
    native_class = (await session.execute(select(ClassMember.id).join(
        SchoolClass, SchoolClass.id == ClassMember.class_id).where(
        ClassMember.user_id == user_id,
        or_(SchoolClass.external_ref.is_(None), ~SchoolClass.external_ref.like("dkuoj:%"))).limit(1))).first()
    if native_class is not None:
        return True
    if (await session.execute(select(CourseMember.id).where(CourseMember.user_id == user_id).limit(1))).first():
        return True
    return (await session.execute(select(Attempt.id).join(Exam, Exam.id == Attempt.exam_id).where(
        Attempt.student_id == user_id, Exam.is_trial.is_not(True),      # the sample exam doesn't count
        or_(Exam.source.is_(None), Exam.source != "dkuoj")).limit(1))).first() is not None


@router.post("/students/{netid}/reset")
async def student_reset(netid: str, call: BridgeCall = Depends(signed_call),
                        session: AsyncSession = Depends(get_session)):
    from ...routes_auth import bump_token_version
    from .sync import IMPORTED_PASSWORD, placeholder_username
    class_id = call.data.get("class_id")
    if not isinstance(class_id, int) or isinstance(class_id, bool):
        raise HTTPException(400, "class_id must be a number")
    await _require_class_staff(session, class_id, call)
    netid = netid.strip()[:64]
    u = await _user_by_netid(session, netid)
    if u is None:
        return {"ok": True}                       # nothing to reset (idempotent)
    if u.role != "student":                       # a teacher's NetID (the fallback never finds one)
        raise HTTPException(400, "Only student accounts can be reset")
    # Defence in depth (DKU OJ checks too): only a student linked to THIS class.
    if u.id not in await class_user_ids(session, class_id):
        raise HTTPException(403, NOT_IN_CLASS)
    # Security review 2026-10-24: DKU OJ may reset only what DKU OJ gave the account. One
    # that also belongs to FairPlay's own classes / LMS courses or took a FairPlay exam is
    # reset in FairPlay (else a compromised DKU OJ could take it over, native data and all).
    if await uses_fairplay_natively(session, u.id):
        await add_event(session, class_id, "student_reset_refused", netid=netid, actor=call.acting_user,
                        detail="also used for FairPlay classes")
        await session.commit()
        raise HTTPException(403, ALSO_NATIVE)
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


async def _require_not_student(session: AsyncSession, call: BridgeCall) -> None:
    """An exam not synced yet has no editors to check. The acting user must at least be named
    and must not be a student account."""
    acting = (call.acting_user or "").strip()
    who = await _user_by_netid(session, acting, role="teacher") if acting else None
    if not acting or (who is not None and who.role == "student"):
        raise HTTPException(403, NOT_EDITOR)


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
    from . import access_codes
    from ..outbox import REOPENED
    code_states = await access_codes.states(session, e.id)
    reopens = dict((await session.execute(
        select(IntegrityEvent.attempt_id, func.count()).where(
            IntegrityEvent.exam_id == e.id, IntegrityEvent.type == REOPENED)
        .group_by(IntegrityEvent.attempt_id))).all())
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
            "swap_requested": bool(a is not None and a.status == "in_progress" and a.swap_requested_at),
            "code_state": code_states.get(u.netid or "", "none"),
            "reopened": bool(a is not None and reopens.get(a.id)),
            "hand_in_count": (int(reopens.get(a.id) or 0) + int(a.status == "submitted")) if a else 0,
            "waiting_since": iso(a.pending_since) if a is not None and a.status == "in_progress"
            and a.pending_resume else None,
            "seconds_left": (max(0, int((a.deadline_at - now).total_seconds()))
                             if a is not None and a.status == "in_progress" else None),
            "warnings": list(reversed(warnings.get(a.id, []))) if a else []})
    open_to: str | list[str] = "all"
    if e.open_to is not None:
        ids = sorted({int(x) for x in e.open_to})
        open_to = sorted(n for n in (await session.execute(
            select(User.netid).where(User.id.in_(ids)))).scalars() if n) if ids else []
    live = e.status == "live"
    # Security review 2026-10-24: the sites FairPlay lets the lock open (app.integrations.
    # site_policy) and the ones it refused, with why, for the FairPlay tab in DKU OJ.
    from ..site_policy import filter_sites
    from ...schemas import clean_website_links
    from ...services import upload_days
    from ...site_companions import companions
    from ...box_profiles import checked_link, without_box_hosts
    websites, refused = filter_sites(list(e.websites or []))
    box_link, box_error = checked_link(e.box_link)
    if box_link:
        # the Box page opens in its own pane (tools.box); framing it never worked
        refused += [{"site": w, "reason": "Opened with Duke Box (box_link) instead"}
                    for w in websites if w not in without_box_hosts(websites)]
        websites = without_box_hosts(websites)
    blocked = [b for b in (e.external_meta or {}).get("websites_blocked") or []
               if isinstance(b, dict) and isinstance(b.get("site"), str)]
    seen = {b["site"] for b in blocked}
    blocked += [b for b in refused if b["site"] not in seen]
    return {"synced": True, "package_ready": SOURCE.package_ready(e), "questions": nq,
            "points": _num(e.total_points), "status": e.status,
            "start_code": e.start_code if live else None,
            "invigilator_code": e.invigilator_code if live else None,
            "closes_at": iso(exam_closes_at(e)), "open_to": open_to,
            "settings": {"calculator": e.calculator or "off", "open_book": bool(e.open_book),
                         "websites": websites,
                         "box_link": box_link, "box_link_error": box_error,
                         # 2026-10-24: start pages, sites opened so a service works (Duke Box:
                         # sign-in, scripts, previews; app.site_companions), early uploads
                         "website_links": clean_website_links(e.website_links or [], websites),
                         "companions": companions(websites),
                         "upload_opens_days": upload_days(e),
                         # which of these were set in FairPlay (POST /exams/<key>/settings) and
                         # so win over DKU OJ's exam list (§11: all of them, with a current DKU OJ)
                         "set_in_fairplay": [f for f in SET_IN_FAIRPLAY_ORDER
                                             if f in ((e.external_meta or {}).get("fairplay_settings") or {})],
                         # §11: the allowed websites as DKU OJ last sent them (for its form)
                         **({"websites_input": list(((e.external_meta or {}).get("fairplay_settings") or {})
                                                    ["websites_input"])}
                            if isinstance(((e.external_meta or {}).get("fairplay_settings") or {})
                                          .get("websites_input"), list) else {}),
                         "websites_blocked": [{"site": b["site"], "reason": str(b.get("reason") or "")}
                                              for b in blocked],
                         "strong_lock": bool(e.require_strong_lock),
                         "resume_needs_approval": bool(e.resume_needs_approval),
                         # 2026-10-24: student review after hand-in (app.review)
                         "review": SOURCE.review_level(e.review_after_handin)},
            "counts": counts, "students": students, "server_time": iso(now)}


SET_IN_FAIRPLAY_ORDER = ("upload_opens_days", "websites", "box_link", "calculator", "open_book", "strong_lock",
                         "resume_needs_approval")


def _pending_settings(pending: dict | None) -> dict | None:
    """§11: the GET shape's `settings` of an exam not synced yet: FairPlay's defaults with the
    settings DKU OJ already saved for it (None when it saved none)."""
    from ...site_companions import companions
    if not isinstance(pending, dict):
        return None
    from ...box_profiles import checked_link
    sites = [w for w in pending.get("websites") or [] if isinstance(w, str)]
    box_link, box_error = checked_link(pending.get("box_link"))
    out = {"calculator": pending.get("calculator") if pending.get("calculator") in ("off", "scientific", "graphing")
           else "off",
           "open_book": bool(pending.get("open_book", False)), "websites": sites,
           "website_links": [x for x in pending.get("website_links") or [] if isinstance(x, dict)],
           "companions": companions(sites), "upload_opens_days": int(pending.get("upload_opens_days") or 0),
           "set_in_fairplay": [f for f in SET_IN_FAIRPLAY_ORDER if f in pending],
           "websites_blocked": [b for b in pending.get("websites_blocked") or [] if isinstance(b, dict)],
           "strong_lock": bool(pending.get("strong_lock", False)),
           "resume_needs_approval": pending.get("resume_needs_approval") is not False,
           "review": pending.get("review") or "nothing", "box_link": box_link, "box_link_error": box_error}
    if isinstance(pending.get("websites_input"), list):
        out["websites_input"] = pending["websites_input"]
    return out


def _not_synced(pending: dict | None = None) -> dict:
    return {"synced": False, "package_ready": False, "questions": 0, "points": 0, "status": None,
            "start_code": None, "invigilator_code": None, "closes_at": None, "open_to": "all",
            "settings": _pending_settings(pending), "counts": None, "students": [], "server_time": iso(utcnow())}


async def _pending(session: AsyncSession, key: str) -> dict | None:
    from .models import OjState
    from .sync import PENDING_PREFIX
    row = await session.get(OjState, PENDING_PREFIX + key)
    if row is None:
        return None
    try:
        data = json.loads(row.value or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _view(session: AsyncSession, e: Exam) -> JSONResponse:
    await session.refresh(e)
    return JSONResponse(await exam_view(session, e), headers={"Cache-Control": "no-store"})


@router.get("/exams/{key}")
async def exam_get(key: str, call: BridgeCall = Depends(signed_call), session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call, required=False)
    if e is None:
        await _require_not_student(session, call)
        return JSONResponse(_not_synced(await _pending(session, key)), headers={"Cache-Control": "no-store"})
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
    before, during or after the exam). Body {"review": "nothing|score|breakdown|answers|answers_and_results",
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


MAX_SETTINGS_SITES = 100       # lines in one settings call (at most MAX_WEBSITES hosts kept)
SETTINGS_FIXED = "The exam has started: its settings can't change any more."
MAX_PENDING = 2000             # exams not synced yet that may hold settings at once
PENDING_FOR = timedelta(days=180)
SETTING_FIELDS = ("calculator", "open_book", "websites", "strong_lock", "resume_needs_approval",
                  "upload_opens_days", "review", "box_link")


def _settings_body(data: dict, key: str) -> tuple[list[str], dict]:
    """Validate a settings body: (fields sent, {field: value}) where a value None hands the
    field back (to DKU OJ's exam list value or the default). Websites are parsed and checked
    against the site policy here (website_links / websites_blocked / websites_input added)."""
    from ...review import LEVELS
    from .sync import parse_sites
    sent = [f for f in SETTING_FIELDS if f in data]
    if not sent:
        raise HTTPException(400, "Send at least one of: " + ", ".join(SETTING_FIELDS))
    ov: dict = {}
    for f in sent:
        v = data[f]
        if f == "box_link" and isinstance(v, str) and not v.strip():
            v = None
        if v is None:
            if f == "review":
                raise HTTPException(400, "review must be one of: " + ", ".join(LEVELS))
            ov[f] = None
            continue
        if f == "calculator":
            if v not in ("off", "scientific", "graphing"):
                raise HTTPException(400, "calculator must be off, scientific or graphing")
            ov[f] = v
        elif f in ("open_book", "strong_lock", "resume_needs_approval"):
            if not isinstance(v, bool):
                raise HTTPException(400, f"{f} must be true or false")
            ov[f] = v
        elif f == "upload_opens_days":
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 7:
                raise HTTPException(400, "upload_opens_days must be a whole number from 0 to 7")
            ov[f] = v
        elif f == "review":
            if v not in LEVELS:
                raise HTTPException(400, "review must be one of: " + ", ".join(LEVELS))
            ov[f] = v
        elif f == "box_link":
            from ...box_profiles import BoxLinkError, normalize_link
            try:
                ov[f] = normalize_link(v)
            except BoxLinkError as exc:
                raise HTTPException(400, f"box_link: {exc}") from None
        elif f == "websites":
            if not isinstance(v, list) or len(v) > MAX_SETTINGS_SITES or not all(
                    isinstance(x, str) and len(x) <= 2100
                    or isinstance(x, dict) and isinstance(x.get("url"), str) and len(x["url"]) <= 2100
                    and (x.get("title") is None or isinstance(x.get("title"), str))
                    for x in v):
                raise HTTPException(400, "websites must be a list of site names or links (at most "
                                         f"{MAX_SETTINGS_SITES})")
            sites, links, blocked = parse_sites(v, None, key)
            ov.update(websites=sites, website_links=links, websites_blocked=blocked,
                      websites_input=[x if isinstance(x, str) else
                                      (f"{x.get('title') or ''} {x['url']}".strip()) for x in v])
    return sent, ov


def _current(e: Exam, f: str):
    if f == "websites":
        return list(e.websites or []), list(e.website_links or [])
    return getattr(e, sync_field(f))


def sync_field(f: str) -> str:
    from .sync import EXAM_FIELD
    return EXAM_FIELD[f]


async def _store_pending(session: AsyncSession, key: str, sent: list[str], ov: dict) -> dict:
    """§11: settings for an assignment FairPlay has not synced yet, applied when it arrives."""
    from sqlalchemy import func
    from .models import OjState
    from .sync import PENDING_PREFIX
    if not key or len(key) > 64 - len(PENDING_PREFIX):
        raise HTTPException(400, "Unknown exam key")
    now = utcnow()
    row = await session.get(OjState, PENDING_PREFIX + key)
    if row is None:
        # Old ones (an assignment that never arrived) go; a cap keeps the table small.
        for old in (await session.execute(select(OjState).where(OjState.name.like(PENDING_PREFIX + "%"))
                                          )).scalars().all():
            try:
                at = json.loads(old.value or "{}").get("updated_at")
            except (ValueError, AttributeError):
                at = None
            if not isinstance(at, str) or at < iso(now - PENDING_FOR):
                await session.delete(old)
        await session.flush()
        count = (await session.execute(select(func.count()).select_from(OjState).where(
            OjState.name.like(PENDING_PREFIX + "%")))).scalar_one()
        if count >= MAX_PENDING:
            raise HTTPException(429, "Too many exams waiting for their first sync; try again later")
        row = OjState(name=PENDING_PREFIX + key, value="{}")
        session.add(row)
        have: dict = {}
    else:
        try:
            have = json.loads(row.value or "{}")
        except ValueError:
            have = {}
        if not isinstance(have, dict):
            have = {}
    for f in sent:
        keys = (f,) if f != "websites" else ("websites", "website_links", "websites_blocked", "websites_input")
        for k in keys:
            have.pop(k, None)
        if ov.get(f) is not None:
            for k in keys:
                if k in ov:
                    have[k] = ov[k]
    if "review" in sent:
        have["review_updated_at"] = iso(now)
    have["updated_at"] = iso(now)
    row.value = json.dumps(have)
    await session.commit()
    return have


@router.post("/exams/{key}/settings")
async def exam_settings(key: str, call: BridgeCall = Depends(signed_call),
                        session: AsyncSession = Depends(get_session)):
    """The exam settings of a DKU OJ exam, kept in FairPlay (v2 §3, §10, §11). Body, any of:
    {"calculator": "off|scientific|graphing", "open_book": bool, "strong_lock": bool,
    "resume_needs_approval": bool, "upload_opens_days": 0..7, "review": "<level>",
    "box_link": "https://duke.box.com/s/<token>" (stored as https://duke.app.box.com/s/<token>; "" clears),
    "websites": ["docs.python.org", "https://duke.app.box.com/folder/0",
    "Course folder https://duke.app.box.com/folder/123", {"url": "...", "title": "..."}]}; null
    hands a field back (the exam list's value or the default; not for review).
    Websites pass the same parsing and site policy as synced ones (refused ones are in
    settings.websites_blocked). An assignment not synced yet: kept and applied when it arrives
    (the answer is the not-synced shape with these settings). Once the first student started
    only `review` may change (any other field that differs from the exam's: 409); `review`
    changes at any time. A field equal to what the exam has is no change."""
    from ...services import invalidate_exam
    from .sync import FAIRPLAY_SETTINGS, apply_fairplay_settings, apply_review
    sent, ov = _settings_body(call.data, key)
    e = await _exam_by_key(session, key)
    if e is None:
        await _require_not_student(session, call)
        pending = await _store_pending(session, key, sent, ov)
        log.info("DKU OJ exam %s (not synced yet): FairPlay settings %s kept by %s", key, ", ".join(sent),
                 call.acting_user)
        return JSONResponse(_not_synced(pending), headers={"Cache-Control": "no-store"})
    e = await _editor_exam(session, key, call)
    lock_fields = [f for f in sent if f != "review"]
    meta = dict(e.external_meta or {})
    if lock_fields and (e.external_frozen_version or e.status == "closed"):
        changed = []
        for f in lock_fields:
            if ov.get(f) is None:
                changed.append(f)
            elif f == "websites":
                if (ov["websites"], ov["website_links"]) != _current(e, f):
                    changed.append(f)
            elif ov[f] != _current(e, f):
                changed.append(f)
        if changed:
            raise HTTPException(409, SETTINGS_FIXED)
        lock_fields = []
    if lock_fields:
        have = dict(meta.get(FAIRPLAY_SETTINGS) or {})
        for f in lock_fields:     # null: back to DKU OJ's value (from the stored listing, applied below)
            for k in ((f,) if f != "websites" else ("websites", "website_links", "websites_blocked",
                                                    "websites_input")):
                have.pop(k, None)
            if ov.get(f) is not None:
                for k in ((f,) if f != "websites" else ("websites", "website_links", "websites_blocked",
                                                        "websites_input")):
                    have[k] = ov[k]
        have["updated_at"] = iso(utcnow())
        meta[FAIRPLAY_SETTINGS] = have
        handed_back = [f for f in lock_fields if ov.get(f) is None]
        if handed_back:
            # a field handed back: DKU OJ's value from the last exam list (or the default)
            from .sync import EXAM_FIELD, _settings
            synced = _settings({"key": key, "settings": meta.get("settings") or {}})
            for f in handed_back:
                if f == "websites":
                    e.websites, e.website_links = synced["websites"], synced["website_links"]
                    meta["websites_blocked"] = synced["websites_blocked"]
                else:
                    setattr(e, EXAM_FIELD[f], synced[EXAM_FIELD[f]])
        apply_fairplay_settings(e, meta)
        e.external_meta = meta
    if "review" in sent:
        apply_review(e, ov["review"], iso(utcnow()))
    e.updated_at = utcnow()
    await session.commit()
    await invalidate_exam(get_store(), e.id)
    log.info("DKU OJ exam %s: FairPlay settings %s set by %s", key, ", ".join(sent), call.acting_user)
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



NO_ROSTER = "This exam has no student list in FairPlay yet, so there are no access codes to print."


def _event_class(e: Exam) -> int | None:
    for c in (e.external_meta or {}).get("classes") or []:
        if isinstance(c, dict) and isinstance(c.get("id"), int) and not isinstance(c.get("id"), bool):
            return c["id"]
    return None


@router.get("/exams/{key}/access-codes.pdf")
async def exam_access_codes_pdf(key: str, request: Request, call: BridgeCall = Depends(signed_call),
                                layout: str = Query("both", pattern="^(both|list|slips)$"),
                                session: AsyncSession = Depends(get_session)):
    from . import access_codes, access_codes_pdf
    e = await _editor_exam(session, key, call, required=False)
    if e is None:
        raise HTTPException(404, "Unknown exam")
    sheet = await access_codes.ensure_codes(session, request.app.state.settings.secret, e)
    if not sheet.rows:
        raise HTTPException(409, NO_ROSTER)
    students = sorted(({"last": r.last_name, "first": r.first_name, "netid": r.netid, "code": code}
                       for r, code in sheet.rows),
                      key=lambda s: (s["last"].casefold(), s["first"].casefold(), s["netid"]))
    pdf = access_codes_pdf.render(e.title, e.course, utcnow(), students, layout)
    class_id = _event_class(e)
    if class_id is not None:
        await add_event(session, class_id, "access_codes_downloaded", actor=call.acting_user,
                        detail=f"exam {key}: {len(students)} codes"[:300])
        await session.commit()
    log.info("DKU OJ exam %s: access codes downloaded by %s (%d students, %d without a NetID)", key,
             call.acting_user, len(students), len(sheet.skipped))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="access-codes-{name}.pdf"',
                             "Cache-Control": "no-store"})

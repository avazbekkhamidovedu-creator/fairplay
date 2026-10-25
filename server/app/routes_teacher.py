from __future__ import annotations

import hashlib
import json
import statistics
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, case, delete, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .grading import MANUAL_TYPES, exam_questions, auto_points, external_question, grade_attempt, handin_grade, is_answered, num, percent, word_count
from .integrations import registry
from .integrations.registry import integration_for
from .models import Attempt, ClassMember, DeviceCheck, Exam, ExamMaterial, IntegrityEvent, User
from .schemas import (QID_RE, CommentIn, ExamIn, ExtendIn, GradeIn, OfflineExitIn, OpenIn, OpenToIn,
                      ReleaseIn, ReviewIn)
from .access import eligible_students, open_to_out
from .security import Principal, require_teacher
from .services import (external_block, attempt_key, code_fields, code_language, exam_closes_at, exam_default_language, exam_delivery, exam_out, exam_out_full, invalidate_exam,
                       new_id, new_receipt, new_session_id, pending_by_exam)
from . import app_client as ac
from . import canvas_mode
from . import exam_control as control
from .routes_classes import check_class_for_exam
from .store import get_store
from .timeutil import iso, iso_from_epoch, utcnow
from .closing import AUTO_CLOSE_EVENT, LATE_GRACE

router = APIRouter(prefix="/api/exams", tags=["teacher"])

ONLINE_WINDOW_S = 15
# Integrity events that are bookkeeping, not "flags" a teacher should look at.
NON_FLAG_TYPES = ("lock_started", "lock_ended", "device_released", "device_bound",
                  "offline_exit_code_issued", "canvas_code_issued",
                  "resume_approved", "swap_approved",     # S3 / S4: the teacher's own actions
                  AUTO_CLOSE_EVENT)                       # 2026-10-23: the automatic close
MAX_RESUME_EXTENSION = control.MAX_RESUME_EXTENSION
# The app's own signal counts as "app online" for this long (beats every 5 s).
APP_ONLINE_WINDOW_S = 15
MAX_LIVE_FLAGS = 50
EXTERNAL_GRADES_DETAIL = "This exam is graded on the other website"


def is_flag(event_type: str) -> bool:
    return event_type not in NON_FLAG_TYPES


def _questions_from(body: ExamIn) -> list[dict]:
    seen: set[str] = set()
    out = []
    for q in body.questions:
        qid = q.id if q.id and QID_RE.match(q.id) and q.id not in seen else new_id("q")
        seen.add(qid)
        d = q.to_dict()
        out.append({"id": qid, **d})
    return out


def _apply(e: Exam, body: ExamIn, creating: bool = False) -> None:
    qs = _questions_from(body)
    if body.mode == "external":
        qs = [external_question(body.external_points)]
    e.title = body.title
    e.duration_min = body.duration_min
    e.questions = qs
    e.question_count = len(qs)
    e.total_points = float(sum(q["points"] for q in qs))
    e.updated_at = utcnow()
    # Optional slice-2 fields: on PUT, a field the client did not send keeps its value.
    for f in ("course", "room", "scheduled_at", "closes_at", "shuffle_questions", "class_id",
              "resume_needs_approval", "require_strong_lock",
              "calculator", "open_book", "websites",             # 2026-10-23: exam tools
              "upload_opens_days",                               # 2026-10-24
              "default_language"):                               # phase 4
        if creating or f in body.model_fields_set:
            setattr(e, f, getattr(body, f))
    if creating or "websites" in body.model_fields_set:
        e.website_links = list(body.website_links)            # 2026-10-24: derived from websites
    # 2026-10-23: no outside apps any more (calculator, materials and websites are built
    # in). Older editors still send allowed_apps; it is accepted and dropped, so the apps
    # close everything when they lock.
    e.allowed_apps = []
    if body.mode == "external":
        e.calculator, e.open_book, e.websites = "off", False, []
        e.website_links, e.upload_opens_days = [], 0
    # Slice 3: mode and its fields always come from the body (already normalised by
    # ExamIn: null/[] for "questions" mode, so switching back clears them).
    e.mode = body.mode
    e.external_url = body.external_url
    e.allowed_domains = list(body.allowed_domains)
    e.finish_url_contains = body.finish_url_contains
    # Slice 6: delivery (ExamIn keeps it consistent with mode). The hidden website-exam
    # grading item gets a topic so insights can place it: the course, else "general".
    e.delivery = body.delivery
    default_topic = (e.course or "").strip().lower()[:60] or "general"
    for q in qs:
        if body.mode == "external" or not q.get("topics"):   # 2026-10-13: topics optional
            q["topics"] = [default_topic]


async def _own_exam(session: AsyncSession, exam_id: str, p: Principal) -> Exam:
    e = await session.get(Exam, exam_id)
    if e is None or e.is_trial:                                 # never the sample exam
        raise HTTPException(404, "Exam not found")
    if e.teacher_id != p.user_id:
        # integrations: other teachers the external system names (e.g. its editors)
        integ = integration_for(e)
        if not (integ and await integ.is_teacher(session, e, p.user_id)):
            raise HTTPException(404, "Exam not found")
    return e


def _not_read_only(e: Exam) -> None:
    """An exam from an external system may be read-only here (edited there)."""
    integ = integration_for(e)
    if integ and integ.exam_is_read_only(e):
        raise HTTPException(409, integ.edit_refused_message(e))


def _not_graded_externally(e: Exam) -> None:
    integ = integration_for(e)
    if integ and integ.grades_externally(e):
        raise HTTPException(409, integ.graded_message(e))


@router.get("")
async def list_exams(p: Principal = Depends(require_teacher),
                     session: AsyncSession = Depends(get_session)):
    from .materials import maybe_cleanup_materials
    await maybe_cleanup_materials()      # 2026-10-23 (review 2): open-book retention, hourly
    others = [and_(Exam.source == src.id, Exam.id.in_(ids)) for src in registry.sources()
              if (ids := src.teacher_exam_ids(p.user_id)) is not None]
    res = await session.execute(
        select(Exam).where(or_(Exam.teacher_id == p.user_id, *others),
                           Exam.is_trial == false())
        .order_by(Exam.created_at.desc(), Exam.id))
    exams = list(res.scalars())
    pending = await pending_by_exam(session, [e.id for e in exams if e.status != "draft"])
    return [exam_out(e, pending.get(e.id, 0)) for e in exams]


def _check_delivery(request: Request, body: ExamIn) -> None:
    """2026-10-13: paper exams need Scan & Grade, which is off unless switched on."""
    if body.delivery == "paper" and not ac.settings_of(request).feature("scan"):
        raise HTTPException(400, "Paper exams are switched off on this server")


@router.post("", status_code=201)
async def create_exam(body: ExamIn, request: Request, p: Principal = Depends(require_teacher),
                      session: AsyncSession = Depends(get_session)):
    _check_delivery(request, body)
    await check_class_for_exam(session, body.class_id, p)
    now = utcnow()
    e = Exam(id=new_id("e"), teacher_id=p.user_id, status="draft", created_at=now)
    _apply(e, body, creating=True)
    canvas_mode.apply_settings(e, body)
    session.add(e)
    await session.commit()
    return exam_out(e)


@router.get("/allowed-apps")
async def allowed_apps_catalog(p: Principal = Depends(require_teacher)):
    """2026-10-14: the apps a teacher may allow during an exam (exam editor list)."""
    from .allowed_apps import catalog_out
    return catalog_out()


@router.get("/{exam_id}")
async def get_exam(exam_id: str, p: Principal = Depends(require_teacher),
                   session: AsyncSession = Depends(get_session)):
    return await exam_out_full(session, await _own_exam(session, exam_id, p))


@router.put("/{exam_id}")
async def update_exam(exam_id: str, body: ExamIn, request: Request,
                      p: Principal = Depends(require_teacher),
                      session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    _not_read_only(e)
    if body.delivery == "paper" and exam_delivery(e) != "paper":
        _check_delivery(request, body)
    if e.status != "draft":
        raise HTTPException(409, "Exam is not a draft")
    if "class_id" in body.model_fields_set:
        await check_class_for_exam(session, body.class_id, p)
    await canvas_mode.release_before_change(session, e, body)
    prev_domains = list(e.allowed_domains or [])
    _apply(e, body)
    canvas_mode.apply_settings(e, body, prev_domains)
    await session.commit()
    await invalidate_exam(get_store(), exam_id)
    return exam_out(e)


@router.delete("/{exam_id}", status_code=204)
async def delete_exam(exam_id: str, p: Principal = Depends(require_teacher),
                      session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    integ = integration_for(e)
    if integ and integ.delete_refused_message(e):
        raise HTTPException(409, integ.delete_refused_message(e))
    if e.status not in ("draft", "closed"):
        raise HTTPException(409, "Exam is live; close it first")
    if canvas_mode.canvas_exam(e) and e.canvas_code_source == "managed" and e.canvas_code_enc:
        await canvas_mode.clear_in_canvas(session, e)
    aids = (await session.execute(select(Attempt.id).where(Attempt.exam_id == exam_id))).scalars().all()
    await session.execute(delete(IntegrityEvent).where(IntegrityEvent.exam_id == exam_id))
    await session.execute(delete(ExamMaterial).where(ExamMaterial.exam_id == exam_id))   # 2026-10-23
    await session.execute(delete(Attempt).where(Attempt.exam_id == exam_id))
    await session.delete(e)
    await session.commit()
    store = get_store()
    await invalidate_exam(store, exam_id)
    await store.delete(f"hb:{exam_id}", f"evc:{exam_id}", f"evl:{exam_id}", f"ab:{exam_id}",
                       *[attempt_key(a) for a in aids])
    return Response(status_code=204)


@router.post("/{exam_id}/open")
async def open_exam(exam_id: str, body: OpenIn | None = None, p: Principal = Depends(require_teacher),
                    session: AsyncSession = Depends(get_session)):
    """Body (optional): {"open_to": "all" | [student ids]} -- who may start (default all)."""
    e = await _own_exam(session, exam_id, p)
    given = body or OpenIn()
    await control.open_exam(session, e, given.open_to, given.canvas_access_code)
    return await exam_out_full(session, e)


_checked_open_to = control.checked_open_to


@router.get("/{exam_id}/students")
async def exam_students(exam_id: str, p: Principal = Depends(require_teacher),
                        session: AsyncSession = Depends(get_session)):
    """2026-10-23: the students who may take the exam (class / LMS course members or the
    external roster), for the "Selected students" checklist. started = has an attempt."""
    e = await _own_exam(session, exam_id, p)
    started = set((await session.execute(select(Attempt.student_id).where(Attempt.exam_id == exam_id))).scalars())
    return [{"id": sid, "username": u, "name": n, "started": sid in started}
            for sid, u, n in await eligible_students(session, e)]


@router.put("/{exam_id}/open-to")
async def update_open_to(exam_id: str, body: OpenToIn, p: Principal = Depends(require_teacher),
                         session: AsyncSession = Depends(get_session)):
    """2026-10-23: change who may start, any time (usually while live). {"open_to": ...}
    replaces the selection; {"add": [...], "remove": [...]} changes it. Removing a student
    who already started never stops their attempt; it only blocks a new start."""
    e = await _own_exam(session, exam_id, p)
    if exam_delivery(e) == "paper":
        raise HTTPException(409, control.PAPER_NOT_OPENED)
    if body.open_to is not None:
        value = body.open_to
    else:
        if e.open_to is None and not body.remove:
            value = "all"            # adding to "everyone" (or nothing) changes nothing
        else:
            base = set(e.open_to or []) if e.open_to is not None else \
                {sid for sid, _, _ in await eligible_students(session, e)}
            value = sorted((base | set(body.add)) - set(body.remove))
    await control.set_open_to(session, e, value)
    return await exam_out_full(session, e)


@router.post("/{exam_id}/close")
async def close_exam(exam_id: str, p: Principal = Depends(require_teacher),
                     session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    # 2026-10-23: the same path as the automatic close (app.closing); idempotent
    await control.close_exam(session, e)
    return await exam_out_full(session, e)


@router.post("/{exam_id}/rotate-code")
async def rotate_code(exam_id: str, p: Principal = Depends(require_teacher),
                      session: AsyncSession = Depends(get_session)):
    """Audit #13: new start code on demand (e.g. after it was seen outside the room).
    Students already writing are not affected; offline packages need the new code."""
    e = await _own_exam(session, exam_id, p)
    await control.rotate_start_code(session, e)
    return await exam_out_full(session, e)


@router.post("/{exam_id}/attempts/{aid}/offline-exit")
async def offline_exit(exam_id: str, aid: str, body: OfflineExitIn, request: Request,
                       p: Principal = Depends(require_teacher),
                       session: AsyncSession = Depends(get_session)):
    """Answer for a locked Mac that cannot reach the server (OfflineExit.swift). The
    invigilator picks the student and types the Mac code shown on their screen. The code
    only works on that student's Mac for its current app session, and is logged."""
    e = await _own_exam(session, exam_id, p)    # any of the teacher's exams, also just closed
    a = await session.get(Attempt, aid)
    if a is None or a.exam_id != e.id:
        raise HTTPException(404, "Attempt not found")
    mac_code = ac.normalize_mac_code(body.mac_code)
    if mac_code is None:
        raise HTTPException(422, "A Mac code has 8 letters and digits, like K7QM-3XPA")
    if not a.session_id:
        raise HTTPException(409, "This student's app has no session yet. Restart their Mac; answers are saved on it.")
    code = ac.offline_exit_code(ac.offline_key(ac.settings_of(request).secret, aid, a.session_id), mac_code)
    await ac.add_event(session, get_store(), aid, exam_id, "offline_exit_code_issued",
                       f"teacher gave an offline exit code for Mac code {mac_code[:4]}-{mac_code[4:]}")
    return {"code": code}


@router.post("/{exam_id}/attempts/{aid}/release-device")
async def release_device(exam_id: str, aid: str, p: Principal = Depends(require_teacher),
                         session: AsyncSession = Depends(get_session)):
    """Let a student continue on another computer (broken laptop): the old app session
    stops working and the next start from any FairPlay app binds the attempt again."""
    e = await _own_exam(session, exam_id, p)
    a = await session.get(Attempt, aid)
    if a is None or a.exam_id != e.id:
        raise HTTPException(404, "Attempt not found")
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    a.device_id = None
    a.session_id = new_session_id()   # nobody holds this one: the old device is cut off
    a.updated_at = utcnow()
    await session.commit()
    store = get_store()
    await store.delete(attempt_key(aid), f"abt:{aid}")
    await store.hdel(f"ab:{exam_id}", aid)       # audit S-13: old device's beats go too
    await ac.add_event(session, store, aid, exam_id, "device_released",
                       "teacher allowed this exam to move to another computer")
    return {"ok": True}


@router.post("/{exam_id}/attempts/{aid}/approve-swap")
async def approve_swap(exam_id: str, aid: str, p: Principal = Depends(require_teacher),
                       session: AsyncSession = Depends(get_session)):
    """S4: move the exam to the computer the student asked from. Like release-device (the
    next start from any FairPlay app binds it), but the old computer may send one final
    save for SWAP_FINAL_SAVE_S, and its hand-in check answers "moved"."""
    _, a = await _exam_attempt(session, exam_id, aid, p)
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    now = utcnow()
    if now > a.deadline_at + LATE_GRACE:
        raise HTTPException(409, ac.SWAP_TOO_LATE_DETAIL)
    target = a.swap_device_id
    a.swap_old_session, a.swap_approved_at = a.session_id, now
    a.swap_requested_at = None
    a.device_id = None
    a.session_id = new_session_id()   # nobody holds this one: the old device is cut off
    a.updated_at = now
    await session.commit()
    store = get_store()
    # The answers as of now: the old computer's final save only fills what the new one
    # has not changed since (routes_student._final_save_after_swap).
    await store.set(f"swapbase:{aid}", json.dumps(a.answers or {}), ac.SWAP_FINAL_SAVE_S + 10)
    await store.delete(attempt_key(aid), f"abt:{aid}")
    await store.hdel(f"ab:{exam_id}", aid)
    await ac.add_event(session, store, aid, exam_id, "swap_approved",
                       "teacher moved this exam to another computer"
                       + (f" (device {target[:8]})" if target else ""))
    return {"ok": True}


@router.post("/{exam_id}/attempts/{aid}/approve-resume")
async def approve_resume(exam_id: str, aid: str, p: Principal = Depends(require_teacher),
                         session: AsyncSession = Depends(get_session)):
    """S3: let a student who came back after FairPlay closed continue. The time spent
    waiting is given back (at most 15 minutes)."""
    _, a = await _exam_attempt(session, exam_id, aid, p)
    return await control.approve_resume(session, a)


@router.get("/{exam_id}/readiness")
async def readiness(exam_id: str, p: Principal = Depends(require_teacher),
                    session: AsyncSession = Depends(get_session)):
    """S1: each student's latest FairPlay device check. The roster is the exam's class;
    an exam without one lists the students who have attempts."""
    e = await _own_exam(session, exam_id, p)
    if e.class_id is not None:
        roster = select(User.id, User.name).join(ClassMember, ClassMember.user_id == User.id) \
            .where(ClassMember.class_id == e.class_id)
    else:
        roster = select(User.id, User.name).join(Attempt, Attempt.student_id == User.id) \
            .where(Attempt.exam_id == exam_id)
    students = (await session.execute(roster.order_by(User.name, User.id))).all()
    checks = (await session.execute(
        select(DeviceCheck).where(DeviceCheck.student_id.in_([sid for sid, _ in students]))
        .order_by(DeviceCheck.checked_at))).scalars().all() if students else []
    latest = {c.student_id: c for c in checks}      # ordered by time: the newest wins
    return [_readiness_row(sid, name, latest.get(sid)) for sid, name in students]


def _readiness_row(sid: int, name: str, c: DeviceCheck | None) -> dict:
    if c is None:
        return {"student_id": sid, "name": name, "platform": None, "app_version": None,
                "os_version": None, "ok": None, "failed": None, "checked_at": None}
    failed = [str(x.get("id")) for x in c.checks or [] if not x.get("info") and not x.get("ok")]
    return {"student_id": sid, "name": name, "platform": c.platform, "app_version": c.app_version,
            "os_version": c.os_version, "ok": not failed, "failed": failed,
            "checked_at": iso(c.checked_at)}


@router.get("/{exam_id}/live")
async def live(exam_id: str, p: Principal = Depends(require_teacher),
               session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    qids = [q["id"] for q in exam_questions(e)]
    rows = (await session.execute(
        select(Attempt.id, Attempt.status, Attempt.started_at, Attempt.submitted_at,
               Attempt.deadline_at, Attempt.answered, User.username, User.name,
               Attempt.client, Attempt.entries, Attempt.late, Attempt.pending_resume,
               Attempt.pending_since, Attempt.swap_requested_at, Attempt.swap_device_id,
               Attempt.device_id)
        .join(User, User.id == Attempt.student_id)
        .where(Attempt.exam_id == exam_id)
        .order_by(User.name, User.username))).all()
    store = get_store()
    hb, evc, evl = await store.live_snapshot(exam_id)
    ab = await store.hgetall(f"ab:{exam_id}")   # signed app beats (audit #2)
    # Open flags: indexed on (exam_id, review); only this exam's open events are touched.
    open_flag = (IntegrityEvent.exam_id == exam_id, IntegrityEvent.review == "open",
                 IntegrityEvent.type.not_in(NON_FLAG_TYPES))
    needs_look = {aid for aid, in (await session.execute(
        select(IntegrityEvent.attempt_id).where(*open_flag).distinct())).all()}
    flag_rows = (await session.execute(
        select(IntegrityEvent.id, IntegrityEvent.attempt_id, IntegrityEvent.type,
               IntegrityEvent.detail, IntegrityEvent.server_time, User.username, User.name)
        .join(Attempt, Attempt.id == IntegrityEvent.attempt_id)
        .join(User, User.id == Attempt.student_id)
        .where(*open_flag)
        .order_by(IntegrityEvent.id.desc()).limit(MAX_LIVE_FLAGS))).all()
    now = time.time()
    students = []
    legacy = [aid for aid, _, _, _, _, n, *_ in rows if n is None]
    old_answers = dict((await session.execute(
        select(Attempt.id, Attempt.answers).where(Attempt.id.in_(legacy)))).all()) if legacy else {}
    for (aid, status, started, submitted, deadline, answered, username, name, client, entries, late,
         pending, pending_since, swap_at, swap_dev, device) in rows:
        h = json.loads(hb[aid]) if aid in hb else None
        b = json.loads(ab[aid]) if aid in ab else None
        app_online = bool(b) and now - b["t"] <= APP_ONLINE_WINDOW_S
        checks = ac.parse_checks(b.get("checks", "")) if b else {}
        if answered is None:            # rows saved before S-10: count once from the answers
            answers = old_answers.get(aid) or {}
            answered = sum(1 for q in qids if is_answered(answers.get(q)))
        students.append({
            "attempt_id": aid, "username": username, "name": name, "status": status,
            "started_at": iso(started), "submitted_at": iso(submitted), "deadline_at": iso(deadline),
            "answered": min(int(answered), len(qids)),
            "question_count": len(qids),
            "current_question": h["cq"] if h else 0,
            # 2026-10-23: from the student page's heartbeat; None = not known
            "battery": h.get("bat") if h else None,
            "charging": h.get("chg") if h else None,
            # Only a fresh, signed beat from the app counts as locked (audit #2).
            "locked": app_online and bool(b.get("locked")),
            "app_online": app_online,
            "last_app_signal_at": iso_from_epoch(b["t"]) if b else None,
            "app_checks": b.get("checks", "") if b else "",
            "client": client or "app",
            "entries": int(entries or 0),
            "last_heartbeat_at": iso_from_epoch(h["t"]) if h else None,
            "online": bool(h) and now - h["t"] <= ONLINE_WINDOW_S,
            "event_count": int(evc.get(aid, 0)),
            "last_event": evl.get(aid),
            "needs_look": aid in needs_look,
            "late": bool(late),
            # S5: the app holds sealed answers for this student not uploaded yet (beat sealed=1)
            "sealed_pending": checks.get("sealed") == "1",
            # S3: came back after FairPlay closed; waits for "Approve"
            "approval": "pending" if pending else ("approved" if pending_since else None),
            "pending_since": iso(pending_since) if pending else None,
            # S4: asked from another computer; "Approve swap" moves the exam there
            "swap_requested": status == "in_progress" and swap_at is not None and swap_dev != device,
            # S6: the exam requires the strongest lock and the latest beat lacks sep=1
            "weak_lock": bool(e.require_strong_lock) and status == "in_progress" and bool(b)
            and checks.get("sep") != "1",
        })
    counts = {"writing": 0, "submitted": 0, "offline": 0, "needs_look": 0}
    for st in students:
        if st["status"] == "submitted":
            counts["submitted"] += 1
        elif st["online"]:
            counts["writing"] += 1
        else:
            counts["offline"] += 1
        counts["needs_look"] += int(st["needs_look"])
    flags = [{"event_id": eid, "attempt_id": aid, "username": username, "name": name,
              "type": typ, "detail": detail, "server_time": iso(st_time)}
             for eid, aid, typ, detail, st_time, username, name in flag_rows]
    # perf 0.6.2: JSONResponse directly (plain values only): FastAPI's generic re-encoding
    # of a 100-student room cost more CPU than building it.
    return JSONResponse({"exam_id": e.id, "status": e.status,
                         "start_code": e.start_code if e.status == "live" else None,
                         "invigilator_code": e.invigilator_code if e.status == "live" else None,
                         "duration_min": e.duration_min, "students": students,
                         "flags": flags, "counts": counts,
                         # 2026-10-23: who may start ("all" or ids) with names; the automatic close time
                         "open_to": open_to_out(e.open_to),
                         "selected": await _selected_out(session, e),
                         "closes_at": iso(exam_closes_at(e)),
                         "server_time": iso(utcnow())})      # audit W-13: countdown on the server clock


async def _selected_out(session: AsyncSession, e: Exam) -> list[dict] | None:
    """The selected students ({id, username, name, started}), None when open to all."""
    if e.open_to is None:
        return None
    ids = sorted({int(x) for x in e.open_to})
    if not ids:
        return []
    started = set((await session.execute(select(Attempt.student_id).where(
        Attempt.exam_id == e.id, Attempt.student_id.in_(ids)))).scalars())
    rows = (await session.execute(select(User.id, User.username, User.name).where(User.id.in_(ids))
                                  .order_by(User.name, User.username))).all()
    return [{"id": int(i), "username": u, "name": n, "started": int(i) in started} for i, u, n in rows]


RESULTS_EVENTS_PER_ATTEMPT = 200


def _event_out(ev: IntegrityEvent) -> dict:
    return {"event_id": ev.id, "type": ev.type, "detail": ev.detail,
            "client_time": ev.client_time, "server_time": iso(ev.server_time),
            "review": ev.review or "open", "note": ev.note or ""}


@router.get("/{exam_id}/attempts/{aid}/events")
async def attempt_events(exam_id: str, aid: str, offset: int = Query(default=0, ge=0),
                         limit: int = Query(default=200, ge=1, le=1000),
                         p: Principal = Depends(require_teacher),
                         session: AsyncSession = Depends(get_session)):
    """Every event of one attempt, oldest first, a page at a time (audit S-06)."""
    e = await _own_exam(session, exam_id, p)
    a = await session.get(Attempt, aid)
    if a is None or a.exam_id != e.id:
        raise HTTPException(404, "Attempt not found")
    q = select(IntegrityEvent).where(IntegrityEvent.attempt_id == aid)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(IntegrityEvent.server_time, IntegrityEvent.id)
                                  .offset(offset).limit(limit))).scalars().all()
    return {"total": int(total), "offset": offset, "events": [_event_out(ev) for ev in rows]}


@router.get("/{exam_id}/results")
async def results(exam_id: str, p: Principal = Depends(require_teacher),
                  session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    rows = (await session.execute(
        select(Attempt, User.username, User.name)
        .join(User, User.id == Attempt.student_id)
        .where(Attempt.exam_id == exam_id)
        .order_by(User.name, User.username))).all()
    # Audit S-06: counts come from SQL; each attempt lists only its newest
    # RESULTS_EVENTS_PER_ATTEMPT events (all of them: GET .../attempts/{aid}/events).
    is_open = or_(IntegrityEvent.review.is_(None), IntegrityEvent.review == "open")
    flag_q = IntegrityEvent.type.not_in(NON_FLAG_TYPES)
    counts = (await session.execute(
        select(IntegrityEvent.attempt_id, func.count(),
               func.sum(case((flag_q, 1), else_=0)),
               func.sum(case((and_(flag_q, is_open), 1), else_=0)))
        .where(IntegrityEvent.exam_id == exam_id)
        .group_by(IntegrityEvent.attempt_id))).all()
    event_total = {aid: int(n) for aid, n, _, _ in counts}
    flag_count = {aid: int(f or 0) for aid, _, f, _ in counts}
    flags_total = sum(int(f or 0) for _, _, f, _ in counts)
    flags_open = sum(int(o or 0) for _, _, _, o in counts)
    rn = func.row_number().over(partition_by=IntegrityEvent.attempt_id,
                                order_by=(IntegrityEvent.server_time.desc(), IntegrityEvent.id.desc())).label("rn")
    newest = (select(IntegrityEvent.id, rn).where(IntegrityEvent.exam_id == exam_id)).subquery()
    evrows = (await session.execute(
        select(IntegrityEvent).join(newest, newest.c.id == IntegrityEvent.id)
        .where(newest.c.rn <= RESULTS_EVENTS_PER_ATTEMPT)
        .order_by(IntegrityEvent.server_time, IntegrityEvent.id))).scalars().all()
    events: dict[str, list] = {}
    for ev in evrows:
        events.setdefault(ev.attempt_id, []).append(_event_out(ev))

    qs = exam_questions(e)
    total = e.total_points
    external = False  # slice 4: external exams are graded through their hidden item
    attempts, percents = [], []
    # per question over submitted attempts, excluding pending essay answers
    q_n = {q["id"]: 0 for q in qs}
    q_sum = {q["id"]: 0.0 for q in qs}
    q_correct = {q["id"]: 0 for q in qs}
    n_submitted = pending_total = 0
    integ = integration_for(e)
    ext = bool(integ and integ.grades_externally(e))
    deliveries = await integ.delivery_status(session, e.id) if integ else {}
    for a, username, name in rows:
        # externally graded: points only from the released external result
        score, pending, ans_rows = grade_attempt(
            qs, a.answers, integ.grades_for(qs, a.external_result) if ext else a.grades)
        if ext:
            released, ext_score, _ = integ.released(a.external_result, total)
            score = ext_score if released else score
            pending = 0
        pct = percent(score, total)
        if external:
            ans_rows, pending = [], 0
        if a.status == "submitted":
            n_submitted += 1
            pending_total += pending
            if not external:
                percents.append(pct)
            for r in ans_rows:
                if r["pending"]:
                    continue
                qid = r["question_id"]
                q_n[qid] += 1
                q_sum[qid] += float(r["points_awarded"])
                q_correct[qid] += bool(r["correct"])
        attempts.append({
            "attempt_id": a.id, "username": username, "name": name, "status": a.status,
            "auto_submitted": a.auto_submitted,
            # Audit S-02: handed in after time; answers sent after time are shown, not graded.
            "late": bool(a.late), "late_answers": a.late_answers or {},
            "late_sealed": bool(a.late_sealed),    # S5: sealed offline after the time was up
            "score": None if external else num(score), "total_points": num(total),
            "percent": None if external else pct,
            "submitted_at": iso(a.submitted_at), "receipt": a.receipt,
            "needs_grading": pending, "overall_comment": a.overall_comment or "",
            "flag_count": flag_count.get(a.id, 0),
            "answers": ans_rows, "events": events.get(a.id, []),
            "events_total": event_total.get(a.id, 0),
        })
        if integ is not None:   # what was sent to the external system and what came back
            attempts[-1]["external_result"] = a.external_result
            attempts[-1]["external_delivery"] = deliveries.get(a.id)

    buckets = [0] * 10
    for pct in percents:
        buckets[min(max(int(pct // 10), 0), 9)] += 1
    labels = [f"{i * 10}\u2013{i * 10 + 9}" for i in range(9)] + ["90\u2013100"]

    def q_stats(q: dict) -> dict:
        qid, pts = q["id"], q.get("points", 1)
        n = q_n[qid]
        if q["type"] in MANUAL_TYPES:
            pc = round(q_sum[qid] / n / pts * 100, 1) if n and pts else None
        else:
            pc = round(q_correct[qid] / n_submitted * 100, 1) if n_submitted else 0.0
        return {"question_id": qid, "prompt": q["prompt"], "type": q["type"], "points": pts,
                "avg_points": round(q_sum[qid] / n, 2) if n else None, "percent_correct": pc}

    return {
        "exam_id": e.id, "title": e.title, "course": e.course or "",
        "total_points": num(total),
        "grades_released": bool(e.grades_released), "released_at": iso(e.released_at),
        "average_percent": (None if external else
                            round(sum(percents) / len(percents), 1) if percents else 0.0),
        "median_percent": (None if external else
                           round(statistics.median(percents), 1) if percents else 0.0),
        "pending_grading": pending_total, "submitted_count": n_submitted,
        "attempt_count": len(attempts), "flags_total": flags_total, "flags_open": flags_open,
        "score_buckets": [{"label": lab, "count": c} for lab, c in zip(labels, buckets)],
        "attempts": attempts,
        "questions": [q_stats(q) for q in qs],
        "external": external_block(e),                         # integrations: null = FairPlay
    }


# ---------------------------------------------------------------- slice 2
@router.post("/{exam_id}/extend")
async def extend_exam(exam_id: str, body: ExtendIn, p: Principal = Depends(require_teacher),
                      session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    await control.extend_exam(session, e, body.minutes)
    return await exam_out_full(session, e)


@router.post("/{exam_id}/events/{event_id}/review")
async def review_event(exam_id: str, event_id: int, body: ReviewIn,
                       p: Principal = Depends(require_teacher),
                       session: AsyncSession = Depends(get_session)):
    await _own_exam(session, exam_id, p)
    ev = await session.get(IntegrityEvent, event_id)
    if ev is None or ev.exam_id != exam_id:
        raise HTTPException(404, "Event not found")
    ev.review = body.status
    if body.note is not None:
        ev.note = body.note
    await session.commit()
    return {"event_id": ev.id, "review": ev.review, "note": ev.note or ""}


def anon_id(attempt_id: str) -> str:
    return "#" + hashlib.sha256(attempt_id.encode()).hexdigest()[:4].upper()


@router.get("/{exam_id}/grading")
async def grading_queue(exam_id: str, p: Principal = Depends(require_teacher),
                        session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    qs = exam_questions(e)
    integ = integration_for(e)
    if integ and integ.grades_externally(e):     # graded there: nothing to grade here
        return {"exam_id": e.id, "title": e.title, "course": e.course or "", "status": e.status,
                "grades_released": False, "external": integ.external_block(e), "questions": [], "items": []}
    rows = (await session.execute(
        select(Attempt, User.username, User.name)
        .join(User, User.id == Attempt.student_id)
        .where(Attempt.exam_id == exam_id, Attempt.status == "submitted")
        .order_by(Attempt.started_at, Attempt.id))).all()
    flag_count = dict((await session.execute(
        select(IntegrityEvent.attempt_id, func.count())
        .where(IntegrityEvent.exam_id == exam_id, IntegrityEvent.type.not_in(NON_FLAG_TYPES))
        .group_by(IntegrityEvent.attempt_id))).all())
    stats = {q["id"]: {"to_grade": 0, "graded": 0} for q in qs}
    items_by_q: dict[str, list] = {q["id"]: [] for q in qs}
    for a, username, name in rows:
        grades = a.grades or {}
        _, _, ans_rows = grade_attempt(qs, a.answers, grades)
        for q, r in zip(qs, ans_rows):
            qid, t = q["id"], q["type"]
            given = r["given"]
            g = grades.get(qid)
            if g is not None:
                stats[qid]["graded"] += 1
            if t in MANUAL_TYPES:
                include = True
                stats[qid]["to_grade"] += int(r["pending"])
            elif t == "short":
                include = given is not None and auto_points(q, given) == 0
                stats[qid]["to_grade"] += int(include and g is None)
            else:
                include = False
            if include:
                items_by_q[qid].append({
                    "attempt_id": a.id, "anon_id": anon_id(a.id), "username": username,
                    "name": name, "question_id": qid, "answer": given,
                    "word_count": word_count(given) if t == "essay" else None,
                    "auto_points": None if t in MANUAL_TYPES else num(auto_points(q, given)),
                    "grade": g, "flag_count": int(flag_count.get(a.id, 0)),
                })
    return {
        "exam_id": e.id, "title": e.title, "course": e.course or "", "status": e.status,
        "grades_released": bool(e.grades_released), "external": None,
        "questions": [{"question_id": q["id"], "index": i, "type": q["type"],
                       "prompt": q["prompt"], "points": q.get("points", 1),
                       "rubric": [dict(x) for x in q.get("rubric") or []],
                       "images": [dict(x) for x in q.get("images") or []],  # slice 6
                       **grading_code_fields(q, e),                          # phase 4
                       **stats[q["id"]]} for i, q in enumerate(qs)],
        "items": [it for q in qs for it in items_by_q[q["id"]]],
    }


def grading_code_fields(q: dict, e: Exam) -> dict:
    """Phase 4: what the grading page needs to run a coding answer's tests (all of them,
    hidden ones included: this is the teacher's view)."""
    if q.get("type") != "code":
        return {}
    f = code_fields(q)
    return {"language": code_language(q, exam_default_language(e)), "tests": f["tests"],
            "time_limit_ms": f["time_limit_ms"], "write_only": f["write_only"]}


async def _exam_attempt(session: AsyncSession, exam_id: str, aid: str,
                        p: Principal) -> tuple[Exam, Attempt]:
    e = await _own_exam(session, exam_id, p)
    # FOR UPDATE on Postgres serialises two teachers grading the same attempt (the grades
    # JSON is read-modify-write); SQLite ignores it and serialises writers anyway.
    a = (await session.execute(
        select(Attempt).where(Attempt.id == aid, Attempt.exam_id == exam_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "Attempt not found")
    return e, a


def _question(e: Exam, qid: str) -> dict:
    for q in exam_questions(e):
        if q["id"] == qid:
            return q
    raise HTTPException(404, "Question not found")


async def _save_grades(session: AsyncSession, e: Exam, a: Attempt, qid: str,
                       grade: dict | None) -> dict:
    grades = dict(a.grades or {})
    if grade is None:
        grades.pop(qid, None)
    else:
        grades[qid] = grade
    score, pending, _ = grade_attempt(exam_questions(e), a.answers, grades)
    a.grades = grades
    a.score = score
    a.needs_grading = pending
    a.updated_at = utcnow()
    await session.commit()
    return {"attempt_id": a.id, "question_id": qid, "grade": grade, "score": num(score),
            "total_points": num(e.total_points), "percent": percent(score, e.total_points),
            "needs_grading": pending}


@router.put("/{exam_id}/attempts/{aid}/grades/{qid}")
async def put_grade(exam_id: str, aid: str, qid: str, body: GradeIn,
                    p: Principal = Depends(require_teacher),
                    session: AsyncSession = Depends(get_session)):
    e, a = await _exam_attempt(session, exam_id, aid, p)
    _not_graded_externally(e)
    q = _question(e, qid)
    if a.status != "submitted":
        raise HTTPException(409, "Attempt is not submitted")
    max_points = q.get("points", 1)
    eps = 1e-9
    if not -eps <= body.points <= max_points + eps:
        raise HTTPException(422, f"Points must be between 0 and {num(max_points)}")
    scores = None
    if body.rubric_scores is not None:
        rubric = q.get("rubric") or []
        if len(body.rubric_scores) != len(rubric):
            raise HTTPException(422, "rubric_scores must have one score per rubric criterion")
        for s, crit in zip(body.rubric_scores, rubric):
            if not -eps <= s <= float(crit["points"]) + eps:
                raise HTTPException(
                    422, f"Rubric score for '{crit['label']}' must be between 0 and "
                         f"{num(float(crit['points']))}")
        if abs(sum(body.rubric_scores) - body.points) > 1e-6:
            raise HTTPException(422, "points must equal the sum of rubric_scores")
        scores = [num(s) for s in body.rubric_scores]
    grade = {"points": num(body.points), "comment": body.comment, "rubric_scores": scores,
             "graded_at": iso(utcnow())}
    return await _save_grades(session, e, a, qid, grade)


@router.delete("/{exam_id}/attempts/{aid}/grades/{qid}")
async def delete_grade(exam_id: str, aid: str, qid: str, p: Principal = Depends(require_teacher),
                       session: AsyncSession = Depends(get_session)):
    e, a = await _exam_attempt(session, exam_id, aid, p)
    _not_graded_externally(e)
    _question(e, qid)
    if a.status != "submitted":
        raise HTTPException(409, "Attempt is not submitted")
    return await _save_grades(session, e, a, qid, None)


@router.put("/{exam_id}/attempts/{aid}/comment")
async def put_comment(exam_id: str, aid: str, body: CommentIn,
                      p: Principal = Depends(require_teacher),
                      session: AsyncSession = Depends(get_session)):
    e, a = await _exam_attempt(session, exam_id, aid, p)
    _not_graded_externally(e)
    a.overall_comment = body.comment
    a.updated_at = utcnow()
    await session.commit()
    return {"attempt_id": a.id, "overall_comment": a.overall_comment}


@router.post("/{exam_id}/release")
async def release_grades(exam_id: str, body: ReleaseIn | None = None,
                         p: Principal = Depends(require_teacher),
                         session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    _not_graded_externally(e)
    if e.status != "closed":
        raise HTTPException(409, "Close the exam first")
    pending = (await pending_by_exam(session, [e.id])).get(e.id, 0)
    if pending > 0 and not (body and body.force):
        raise HTTPException(409, f"{pending} answers still need grading")
    if not e.grades_released:
        e.grades_released = True
        e.released_at = utcnow()
        e.updated_at = e.released_at
        await session.commit()
    return exam_out(e, pending)


@router.post("/{exam_id}/unrelease")
async def unrelease_grades(exam_id: str, p: Principal = Depends(require_teacher),
                           session: AsyncSession = Depends(get_session)):
    e = await _own_exam(session, exam_id, p)
    _not_graded_externally(e)
    e.grades_released = False
    e.released_at = None
    e.updated_at = utcnow()
    await session.commit()
    return await exam_out_full(session, e)

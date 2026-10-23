from __future__ import annotations

import json
import time
from datetime import timedelta

import math
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, false, func, insert, or_, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .grading import exam_questions, grade_attempt, is_answered, num, oj_grades, percent
from .models import Attempt, DeviceCheck, Exam, IntegrityEvent
from . import app_client as ac
from .access import member_of_exam, require_member
from .app_version import check_app_version
from .schemas import (SCALAR_ONLY_DETAIL, AnswersIn, AppBeatIn, DeviceCheckIn, EventsIn, ExitCodeIn,
                      HeartbeatIn, OfflineKeyIn, StartIn, SwapRequestIn)
from .security import Principal, require_student
from .services import (LIVE_TTL, attempt_view, cache_attempt, find_attempt, get_attempt_meta,
                       get_exam_cached, new_id, new_receipt, new_session_id, student_tools,
                       code_language, exam_default_language)
from .store import get_store
from .timeutil import iso, utcnow

router = APIRouter(prefix="/api/student", tags=["student"])

MAX_WRONG_CODES = 5
WRONG_CODE_LOCK_S = 60
WRONG_CODE_WINDOW_S = 600
LATE_GRACE = timedelta(seconds=60)
# Audit S-06
EVENT_REQUESTS_PER_MIN = 60
MAX_EVENTS_PER_ATTEMPT = 5000
EVENTS_AFTER_SUBMIT = timedelta(minutes=10)
OFFLINE_START_MAX = timedelta(minutes=30)
# Invigilator exit code (checked here, never sent to the app): 5 wrong -> 5 min pause.
MAX_WRONG_EXIT = 5
WRONG_EXIT_LOCK_S = 300
# A second start on the same Mac within this window is a double click, not a re-entry.
REENTRY_QUIET_S = 15
# Audit 2026-10-13 (hardening): per-student / per-attempt request budgets.
CHALLENGES_PER_MIN = 30
ANSWER_WRITES_PER_MIN = 240
SUBMIT_RETRIES = 3
OFFLINE_KEY_ASKS = 10
DEVICE_CHECKS_PER_MIN = 30
SWAP_REQUESTS_PER_MIN = 10


async def take_code_try(store, lock_key: str, count_key: str, limit: int, window_s: int,
                        detail: str, retry_s: int = 60) -> int:
    """Audit 2026-10-13 (wrong-code lockouts were check-then-act): reserve the try FIRST
    (one atomic INCR), then look at the lock. However many requests race, at most `limit`
    of them get to compare a code before the lock is set. Returns the try number.
    2026-10-23: the 429 says how many seconds are left (Retry-After header and
    "retry_after" in the body) so the page can count down."""
    n = await store.incr(count_key, window_s)
    locked = await store.get(lock_key)
    if n > limit or locked:
        wait = retry_s
        try:
            ends = float(locked) if locked else 0.0
        except (TypeError, ValueError):
            ends = 0.0
        # An old lock value ("1", before 2026-10-23) has no end time: say the full wait.
        if ends > 1_000_000_000:
            wait = min(retry_s, max(1, math.ceil(ends - time.time())))
        raise HTTPException(429, {"message": detail, "code": "too_many_codes", "retry_after": wait},
                            headers={"Retry-After": str(wait)})
    return n


async def wrong_code(store, lock_key: str, count_key: str, n: int, limit: int,
                     lock_s: int) -> None:
    """The n-th reserved try was wrong: the last allowed one sets the lock. The lock goes
    on before the counter is cleared, so a try reserved in between still sees it.
    The lock's value is the time it ends (epoch seconds), for the countdown."""
    if n >= limit:
        await store.set(lock_key, f"{time.time() + lock_s:.3f}", lock_s)
        await store.delete(count_key)


def same_code(given: str, code: str | None) -> bool:
    """Constant-time compare of bytes (compare_digest raises on non-ASCII str)."""
    return bool(code) and secrets.compare_digest(given.strip().encode(), str(code).encode())


def count_answered(answers: dict) -> int:
    """Audit S-10: answered questions in an answer map (any non-empty value)."""
    return sum(1 for v in (answers or {}).values() if is_answered(v))


def offline_start(offline_started_at, now):
    """Slice 6: start time for an attempt begun offline. Up to 30 min in the past is
    accepted; older values are clamped to 30 min, future ones to now."""
    if offline_started_at is None:
        return now
    return min(now, max(offline_started_at, now - OFFLINE_START_MAX))


@router.get("/exams")
async def my_exams(request: Request, p: Principal = Depends(require_student),
                   session: AsyncSession = Depends(get_session)):
    from .materials import maybe_cleanup_materials
    from .offline import package_id
    await maybe_cleanup_materials()      # 2026-10-23 (review 2): open-book retention, hourly
    secret = ac.settings_of(request).secret
    rows = (await session.execute(
        select(Exam.id, Exam.title, Exam.duration_min, Exam.question_count, Exam.status,
               Attempt.id, Attempt.status, Attempt.submitted_at, Attempt.receipt,
               Exam.course, Exam.room, Exam.scheduled_at, Exam.grades_released,
               Exam.total_points, Attempt.score, Exam.mode, Exam.delivery, Exam.start_code,
               Exam.calculator, Exam.open_book, Exam.websites,       # 2026-10-23: exam tools
               Exam.source, Attempt.oj_result)                        # DKU OJ bridge
        .outerjoin(Attempt, and_(Attempt.exam_id == Exam.id, Attempt.student_id == p.user_id))
        # Audit S-03: live exams of the student's classes / LMS courses, plus their own attempts.
        .where(or_(and_(Exam.status == "live", member_of_exam(p.user_id)), Attempt.id.is_not(None)))
        # slice 6: paper exams are printed; students never see them in the app
        .where(or_(Exam.delivery.is_(None), Exam.delivery != "paper"))
        .where(Exam.is_trial == false())     # the sample exam lives only in its own card
        .order_by(Exam.created_at.desc(), Exam.id))).all()
    out = []
    for (eid, title, dur, qc, status, aid, astatus, sub, receipt, course, room, sched, released,
         total, score, mode, delivery, code, calc, open_book, websites, source, oj_result) in rows:
        mode = mode or "questions"
        delivery = delivery or ("website" if mode == "external" else "computer")
        attempt = None
        if aid is not None:
            released = bool(released)
            if source == "dkuoj":        # released per student by DKU OJ
                released, score, total = _oj_released(oj_result, total)
            show = released and astatus == "submitted"
            attempt = {"attempt_id": aid, "status": astatus, "submitted_at": iso(sub),
                       "receipt": receipt, "released": released,
                       "percent": percent(score or 0.0, total) if show else None}
        row = {"id": eid, "title": title, "duration_min": dur, "question_count": qc,
                    "status": status, "course": course or "", "room": room or "",
                    "scheduled_at": iso(sched), "mode": mode, "delivery": delivery,
                    "attempt": attempt,
                    "package_id": package_id(secret, eid, code, dur) if status == "live" else None,
                    # 2026-10-23: shown on "Before you start"; open_book -> the upload area
                    "tools": student_tools({"mode": mode, "calculator": calc, "open_book": open_book,
                                            "websites": websites})}
        if source == "dkuoj":
            row["source"] = "dkuoj"
        out.append(row)
    return out


def _oj_released(oj_result: dict | None, total: float) -> tuple[bool, float, float]:
    """DKU OJ bridge: (released, score, total points) from the attempt's DKU OJ result."""
    if not oj_result or not oj_result.get("released"):
        return False, 0.0, total
    try:
        return True, float(oj_result.get("total") or 0), float(oj_result.get("total_points") or total or 0)
    except (TypeError, ValueError):
        return False, 0.0, total


@router.get("/exams/{exam_id}/challenge")
async def challenge(exam_id: str, p: Principal = Depends(require_student),
                    session: AsyncSession = Depends(get_session)):
    """Single-use nonce the FairPlay app signs to prove it starts the attempt (audit #1)."""
    exam = await get_exam_cached(get_store(), session, exam_id)
    if exam is None or exam.get("is_trial"):     # the sample exam: /api/student/trial/...
        raise HTTPException(404, "Exam not found")
    await require_member(session, exam, p.user_id)
    return await issue_challenge(p.user_id, exam_id)


async def issue_challenge(user_id: int, exam_id: str) -> dict:
    """Single-use nonce bound to student + exam (also used by the sample exam, app.trial)."""
    if await get_store().incr(f"chalrate:{user_id}", 60) > CHALLENGES_PER_MIN:
        raise HTTPException(429, "Too many tries, wait a minute")
    nonce = secrets.token_urlsafe(24)
    await get_store().set(f"chal:{nonce}", f"{user_id}:{exam_id}", ac.CHALLENGE_TTL_S)
    return {"nonce": nonce, "expires_in": ac.CHALLENGE_TTL_S}


async def verify_start_proof(store, settings, user_id: int, exam_id: str, client) -> str | None:
    """Audit #1: the app proves itself with a signed, single-use challenge. Returns the
    app's device id, or None for a browser start (only with FAIRPLAY_REQUIRE_APP=0).
    Shared by a real start and the sample exam's start (app.trial)."""
    if client is not None:
        # Audit S-09: read-and-delete in one step, so one proof starts at most once.
        owner = await store.getdel(f"chal:{client.nonce}")
        if owner != f"{user_id}:{exam_id}" or not ac.verify(
                settings, ac.start_message(client.nonce, client.device_id), client.signature):
            raise HTTPException(403, ac.APP_PROOF_DETAIL)
        # iPad review M6 (2026-10-20): dev builds (soft lock, fixed exit code, inspector)
        # only ever take exams on a FAIRPLAY_DEV=1 server.
        if client.build == "dev" and not settings.dev:
            raise HTTPException(403, ac.APP_PROOF_DETAIL)
        return client.device_id
    if settings.require_app:
        raise HTTPException(403, ac.APP_REQUIRED_DETAIL)
    return None


@router.post("/exams/{exam_id}/start")
async def start(exam_id: str, body: StartIn, request: Request,
                p: Principal = Depends(require_student),
                session: AsyncSession = Depends(get_session)):
    store = get_store()
    settings = ac.settings_of(request)
    min_version = check_app_version(request, settings)   # S2: 426 for an outdated app
    exam = await get_exam_cached(store, session, exam_id)
    # slice 6: paper is never started; the sample exam starts at /api/student/trial/start
    if exam is None or exam.get("delivery") == "paper" or exam.get("is_trial"):
        raise HTTPException(404, "Exam not found")
    if exam["status"] != "live":
        raise HTTPException(409, "Exam is not open")
    await require_member(session, exam, p.user_id)   # audit S-03: class / LMS course only
    if exam.get("source") == "dkuoj":
        from .dkuoj.service import check_startable
        await check_startable(session, exam_id)
    device_id = await verify_start_proof(store, settings, p.user_id, exam_id, body.client)
    lock_key, count_key = f"codelock:{p.user_id}", f"wrongcode:{p.user_id}"
    n = await take_code_try(store, lock_key, count_key, MAX_WRONG_CODES, WRONG_CODE_WINDOW_S,
                            "Too many wrong codes, wait 60 seconds", WRONG_CODE_LOCK_S)
    if not same_code(body.start_code, exam["start_code"]):
        await wrong_code(store, lock_key, count_key, n, MAX_WRONG_CODES, WRONG_CODE_LOCK_S)
        raise HTTPException(403, "Wrong start code")
    await store.delete(count_key)

    now = utcnow()
    a = await find_attempt(session, exam_id, p.user_id)
    created = False
    if a is None:
        # Audit S-07: the cached exam may be up to 120 s old. Check the real status in the
        # same transaction as the INSERT (FOR SHARE on Postgres): a close that commits
        # first makes this start fail; a close that comes after waits, then sweeps it.
        row = (await session.execute(
            select(Exam.status, Exam.duration_min).where(Exam.id == exam_id)
            .with_for_update(read=True))).first()
        if row is None or row.status != "live":
            await session.rollback()
            raise HTTPException(409, "Exam is not open")
        # T-02 (extend vs start): the duration comes from the same locked read, not the
        # cache -- an "Add 10 min" that commits first is included, one that comes after
        # waits for this INSERT (FOR SHARE) and then extends it like everyone else.
        exam = {**exam, "duration_min": row.duration_min}
        started = offline_start(body.offline_started_at, now)
        extra = timedelta(0)
        if exam.get("source") == "dkuoj":
            # DKU OJ bridge: freeze the package this exam runs from (same transaction as
            # the first attempt: later packages never change a running exam) and give the
            # student's extra time from the DKU OJ roster.
            from .dkuoj.service import freeze_and_extra_time
            extra = await freeze_and_extra_time(session, exam_id, p.user_id)
        a = Attempt(id=new_id("a"), exam_id=exam_id, student_id=p.user_id, status="in_progress",
                    started_at=started,
                    deadline_at=started + timedelta(minutes=exam["duration_min"]) + extra,
                    answers={}, client_seq=-1, updated_at=now,
                    device_id=device_id, session_id=new_session_id(), entries=1,
                    client="app" if device_id else "browser")
        session.add(a)
        try:
            await session.commit()
            created = True
        except IntegrityError:  # double-click / two tabs: someone else inserted it first
            await session.rollback()
            a = await find_attempt(session, exam_id, p.user_id)
            if a is None:
                raise
    if a.status == "submitted":
        raise HTTPException(409, "Already submitted")
    if created:
        # (client "browser" -- only possible with FAIRPLAY_REQUIRE_APP=0 -- shows in the live room)
        await store.set(f"entry:{a.id}", "1", REENTRY_QUIET_S)
    else:
        await _resume(session, store, a, device_id, exam)
    await cache_attempt(store, a)
    return {**attempt_view(a, exam, now), **await _oj_view_extra(session, a, exam),
            "session": a.session_id, "min_app_version": min_version,
            "needs_approval": bool(a.pending_resume)}     # S3


async def _oj_view_extra(session: AsyncSession, a: Attempt, exam: dict) -> dict:
    """DKU OJ exams: the files this attempt has handed in for file_upload questions."""
    if exam.get("source") != "dkuoj":
        return {}
    from .dkuoj.files import files_of_attempt
    return {"answer_files": await files_of_attempt(session, a.id)}


async def _resume(session: AsyncSession, store, a: Attempt, device_id: str | None,
                  exam: dict) -> None:
    """Start on an existing attempt (audit #3, #6): another device is refused and flagged;
    the same device gets a fresh session (the old one stops working) and is flagged as a
    re-entry, unless it is a double click.

    S3: the re-entry case is the "new app run" (FairPlay crashed / was quit and was opened
    again on the bound computer, more than REENTRY_QUIET_S after the last start). Only that
    case waits for the teacher (exam setting resume_needs_approval). A page reload never
    gets here: the page keeps its session and only GETs the attempt. A double click, a
    browser start, and the first bind on a new computer (after release / swap) don't wait."""
    exam_id = exam["id"]
    if a.device_id and device_id != a.device_id:
        if await ac.once_per(store, f"secdev:{a.id}", 60):   # one flag a minute, not per try
            await ac.add_event(session, store, a.id, exam_id, "second_device",
                               f"start refused from {'device ' + device_id[:8] if device_id else 'a browser'}")
        # S4: an object detail, so the page can offer "ask to continue here" (swap-request)
        raise HTTPException(409, ac.SWAP_NEEDED)
    if device_id is None:
        # Browser demo mode (FAIRPLAY_REQUIRE_APP=0) on an unbound attempt: keep the session
        # so a second tab does not break the first one.
        if a.session_id is None:
            a.session_id = new_session_id()
            await session.commit()
        return
    if await store.get(f"entry:{a.id}") and a.device_id == device_id:
        return  # double click within REENTRY_QUIET_S: same session
    first_bind = a.device_id is None
    now = utcnow()
    a.device_id = device_id
    a.session_id = new_session_id()
    a.entries = (a.entries or 0) + 1
    a.client = "app"
    a.updated_at = now
    ask = not first_bind and exam.get("resume_needs_approval", True) and not a.pending_resume
    if ask:
        a.pending_resume, a.pending_since = True, now
    if first_bind:
        a.swap_requested_at = None        # S4: whichever computer binds, the request is done
    await session.commit()
    await store.set(f"entry:{a.id}", "1", REENTRY_QUIET_S)
    if first_bind:
        await ac.add_event(session, store, a.id, exam_id, "device_bound",
                           f"exam moved to device {device_id[:8]}")
    else:
        await ac.add_event(session, store, a.id, exam_id, "re_entry",
                           f"started again on the same computer (entry {a.entries})")
    if ask:
        await ac.add_event(session, store, a.id, exam_id, "resume_requested",
                           "FairPlay was opened again during the exam; the student waits for your OK")


def _meta_of(a: Attempt) -> dict:
    return {"exam_id": a.exam_id, "session_id": a.session_id, "status": a.status}


async def _own_attempt(session: AsyncSession, aid: str, p: Principal) -> Attempt:
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    return a


@router.get("/attempts/{aid}")
async def get_attempt(aid: str, request: Request, p: Principal = Depends(require_student),
                      session: AsyncSession = Depends(get_session)):
    a = await _own_attempt(session, aid, p)
    await ac.check_session(request, session, get_store(), aid, _meta_of(a))
    exam = await get_exam_cached(get_store(), session, a.exam_id)
    if exam is None:
        raise HTTPException(404, "Attempt not found")
    return {**attempt_view(a, exam, utcnow()), **await _oj_view_extra(session, a, exam)}


@router.put("/attempts/{aid}/answers")
async def save_answers(aid: str, body: AnswersIn, request: Request,
                       p: Principal = Depends(require_student),
                       session: AsyncSession = Depends(get_session)):
    now = utcnow()
    store = get_store()
    if await store.incr(f"ansrate:{aid}", 60) > ANSWER_WRITES_PER_MIN:
        raise HTTPException(429, "Saving too often, slow down")
    answers = await _known_answers(store, session, aid, body.answers)
    given = ac.session_header(request)
    # Audit #3: only the attempt's current app session may write.
    if given is not None:
        sess_ok = or_(Attempt.session_id.is_(None), Attempt.session_id == given)
    elif ac.settings_of(request).require_app:
        sess_ok = Attempt.session_id.is_(None)
    else:
        sess_ok = true()
    # Hot path: exactly one conditional UPDATE, no read-modify-write.
    res = await session.execute(
        update(Attempt)
        .where(Attempt.id == aid, Attempt.student_id == p.user_id,
               Attempt.status == "in_progress", Attempt.client_seq < body.client_seq,
               Attempt.deadline_at > now, sess_ok)
        .values(answers=answers, client_seq=body.client_seq, updated_at=now,
                answered=count_answered(answers)))
    await session.commit()
    if res.rowcount == 1:
        return {"saved": True, "client_seq": body.client_seq, "server_time": iso(now)}
    # Slow path: find out why nothing was updated.
    row = (await session.execute(
        select(Attempt.student_id, Attempt.status, Attempt.deadline_at, Attempt.client_seq,
               Attempt.exam_id, Attempt.session_id, Attempt.swap_old_session,
               Attempt.swap_approved_at)
        .where(Attempt.id == aid))).first()
    if row is None or row.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    if given and given == row.swap_old_session:
        saved = await _final_save_after_swap(session, store, aid, row, answers, now)
        if saved is not None:
            return {"saved": True, "client_seq": body.client_seq, "server_time": iso(now), "merged": saved}
    await ac.check_session(request, session, get_store(), aid,
                           {"exam_id": row.exam_id, "session_id": row.session_id})
    if row.status == "submitted":
        raise HTTPException(409, "Attempt already submitted")
    if now >= row.deadline_at:
        raise HTTPException(409, "Time is up")
    return {"saved": False, "client_seq": row.client_seq, "server_time": iso(now)}


async def _final_save_after_swap(session: AsyncSession, store, aid: str, row, answers: dict,
                                 now) -> int | None:
    """S4: the computer the exam was moved away from may send ONE last save within
    SWAP_FINAL_SAVE_S of the approval (what it typed since its last save). It only fills
    questions the new computer has not changed since the approval (snapshot taken then).
    Returns how many answers were merged, or None (window over / used / not in progress)."""
    if row.status != "in_progress" or row.swap_approved_at is None \
            or (now - row.swap_approved_at).total_seconds() > ac.SWAP_FINAL_SAVE_S \
            or now >= row.deadline_at + LATE_GRACE:
        return None
    raw = await store.getdel(f"swapbase:{aid}")     # single use
    if raw is None:
        return None
    base = json.loads(raw)
    for _ in range(SUBMIT_RETRIES):
        cur = (await session.execute(select(Attempt.answers, Attempt.client_seq)
                                     .where(Attempt.id == aid))).first()
        current = dict(cur.answers or {})
        merged = {k: v for k, v in answers.items() if current.get(k) == base.get(k) and current.get(k) != v}
        if not merged:
            return 0
        current.update(merged)
        # Only if the new computer saved nothing in between (client_seq unchanged).
        res = await session.execute(
            update(Attempt).where(Attempt.id == aid, Attempt.status == "in_progress",
                                  Attempt.client_seq == cur.client_seq)
            .values(answers=current, answered=count_answered(current), updated_at=now))
        await session.commit()
        if res.rowcount == 1:
            return len(merged)
    return None


async def _known_answers(store, session: AsyncSession, aid: str, answers: dict) -> dict:
    """Audit 2026-10-13 (hardening): keep only answers to the exam's own questions, so
    made-up keys can't inflate "answered" (or the stored row). Both lookups are cache hits
    on the hot path; an unknown attempt or a question-less exam is left as it is."""
    if not answers:
        return answers
    meta = await get_attempt_meta(store, session, aid)
    exam = await get_exam_cached(store, session, meta["exam_id"]) if meta else None
    ids = set((exam or {}).get("question_ids") or [])
    if ids:
        answers = {k: v for k, v in answers.items() if k in ids}
    return await _check_answer_types(session, aid, exam, answers)


async def _check_answer_types(session: AsyncSession, aid: str, exam: dict | None,
                              answers: dict) -> dict:
    """DKU OJ bridge: lists / {language, source} only for the DKU OJ questions that take
    them (checked against the question: option range, blank count, own uploaded files,
    allowed languages). FairPlay questions keep taking numbers, strings or null."""
    oj_q = {q["id"]: q for q in (exam or {}).get("questions") or [] if q.get("source") == "dkuoj"}
    if not oj_q:
        if any(isinstance(v, (list, dict)) for v in answers.values()):
            raise HTTPException(422, SCALAR_ONLY_DETAIL)
        return answers
    from .dkuoj.adapter import AnswerError, check_answer
    file_ids = None
    if any(oj_q.get(k, {}).get("type") == "file_upload" and v for k, v in answers.items()):
        from .dkuoj.files import file_ids_of_attempt
        file_ids = await file_ids_of_attempt(session, aid)
    out = {}
    for k, v in answers.items():
        q = oj_q.get(k)
        if q is None:
            if isinstance(v, (list, dict)):
                raise HTTPException(422, SCALAR_ONLY_DETAIL)
            out[k] = v
            continue
        try:
            out[k] = check_answer(q, v, None if file_ids is None else file_ids.get(k, set()))
        except AnswerError as e:
            raise HTTPException(422, f"{q.get('label') or k}: {e}") from None
    return out


@router.post("/attempts/{aid}/heartbeat")
async def heartbeat(aid: str, body: HeartbeatIn, request: Request,
                    p: Principal = Depends(require_student),
                    session: AsyncSession = Depends(get_session)):
    store = get_store()
    meta = await get_attempt_meta(store, session, aid)  # store hit; DB only on cache miss
    if meta is None or meta["student_id"] != p.user_id:
        raise HTTPException(404, "Attempt not found")
    await ac.check_session(request, session, store, aid, meta)
    now = time.time()
    # Audit #2: body.locked is self-reported and no longer stored; the live room takes
    # the lock state from the app's signed beats (POST .../app-beat).
    beat = {"t": now, "cq": body.current_question}
    if body.battery is not None:                  # 2026-10-23: low-battery warning
        beat["bat"] = round(body.battery, 2)
        # 2026-10-23 (review): as reported; None (the browser can't tell) stays unknown
        beat["chg"] = body.charging
    await store.hset(f"hb:{meta['exam_id']}", aid, json.dumps(beat), LIVE_TTL)
    await ac.note_js_heartbeat(request, session, store, aid, meta)
    return {"ok": True, "status": meta["status"], "server_time": iso(utcnow()),
            "deadline_at": meta["deadline_at"], "approval": meta.get("approval")}   # S3


@router.post("/attempts/{aid}/events")
async def events(aid: str, body: EventsIn, request: Request,
                 p: Principal = Depends(require_student),
                 session: AsyncSession = Depends(get_session)):
    store = get_store()
    meta = await get_attempt_meta(store, session, aid)
    if meta is None or meta["student_id"] != p.user_id:
        raise HTTPException(404, "Attempt not found")
    await ac.check_session(request, session, store, aid, meta)
    if not body.events:
        return {"ok": True, "stored": 0}
    # Audit S-06: bounded per attempt, closed shortly after hand-in.
    if await store.incr(f"evrate:{aid}", 60) > EVENT_REQUESTS_PER_MIN:
        raise HTTPException(429, "Too many event reports, slow down")
    if meta["status"] == "submitted":
        sub = (await session.execute(select(Attempt.submitted_at).where(Attempt.id == aid))).scalar()
        if sub is None or utcnow() - sub > EVENTS_AFTER_SUBMIT:
            raise HTTPException(409, "Attempt already submitted")
    # Audit 2026-10-13: reserve the room first (one atomic INCRBY), so parallel batches
    # can't all read the same total and overshoot the cap.
    want = len(body.events)
    after = await store.incrby(f"evtotal:{aid}", want, LIVE_TTL)
    before = after - want
    room = MAX_EVENTS_PER_ATTEMPT - before
    if room <= 0:
        return {"ok": True, "stored": 0}
    batch = body.events[:room]
    now = utcnow()
    rows = [{"attempt_id": aid, "exam_id": meta["exam_id"], "type": ac.client_event_type(ev.type),
             "detail": (ev.detail or "")[:500],
             "client_time": (ev.client_time or None) and ev.client_time[:64],
             "server_time": now} for ev in batch]
    await session.execute(insert(IntegrityEvent), rows)  # one batched INSERT
    await session.commit()
    if before < MAX_EVENTS_PER_ATTEMPT <= after:     # exactly one batch crosses the cap
        await ac.add_event(session, store, aid, meta["exam_id"], "event_limit_reached",
                           f"more than {MAX_EVENTS_PER_ATTEMPT} events; later ones were dropped")
    exam_id = meta["exam_id"]
    await store.hincrby(f"evc:{exam_id}", aid, len(rows), LIVE_TTL)
    last = rows[-1]
    await store.hset(f"evl:{exam_id}", aid, f"{last['type']} {last['detail']}".strip(), LIVE_TTL)
    if meta["status"] == "in_progress" and any(r["type"] == "previous_run_killed" for r in rows):
        await _resume_after_kill(session, store, aid)
    return {"ok": True, "stored": len(rows)}


async def _resume_after_kill(session: AsyncSession, store, aid: str) -> None:
    """S3, Mac/Windows relaunch: after a kill or crash the app opens the exam page straight
    away and the page keeps its saved session, so start (_resume) is never called. The app
    reports previous_run_killed instead; that, too, waits for the teacher's OK."""
    a = await session.get(Attempt, aid)
    exam = await session.get(Exam, a.exam_id) if a else None
    if a is None or exam is None or a.status != "in_progress" or a.pending_resume:
        return
    if exam.resume_needs_approval is False:
        return
    a.pending_resume, a.pending_since = True, utcnow()
    a.updated_at = utcnow()
    await session.commit()
    await cache_attempt(store, a)
    await ac.add_event(session, store, aid, a.exam_id, "resume_requested",
                       "FairPlay ended while locked and was opened again; the student waits for your OK")


@router.post("/attempts/{aid}/submit")
async def submit(aid: str, body: AnswersIn, request: Request,
                 p: Principal = Depends(require_student),
                 session: AsyncSession = Depends(get_session)):
    a = await _own_attempt(session, aid, p)
    await ac.check_session(request, session, get_store(), aid, _meta_of(a))
    exam = await session.get(Exam, a.exam_id) if a.status != "submitted" else None
    sent = await _known_answers(get_store(), session, aid, body.answers)
    for _ in range(SUBMIT_RETRIES):
        if a.status == "submitted":
            break
        now = utcnow()
        late = now > a.deadline_at + LATE_GRACE
        stored = a.answers or {}
        seen_seq = a.client_seq
        # Same rule as PUT /answers: the body wins only if it is strictly newer...
        use_body = body.client_seq > seen_seq
        # ...and only in time (audit S-02). After the deadline the graded answers are the
        # ones saved in time; anything new is kept aside for the teacher and flagged.
        changed = {k: v for k, v in sent.items() if stored.get(k) != v} if use_body else {}
        answers = sent if use_body and not late else stored
        late_answers = changed if late and changed else None
        score, pending, _ = grade_attempt(exam_questions(exam) if exam else [], answers, a.grades)
        if exam is not None and (exam.is_trial or exam.source == "dkuoj"):
            score, pending = None, 0      # the sample exam / DKU OJ exams: never graded here
        # Audit 2026-10-13: only if no newer PUT landed since the read above (client_seq
        # unchanged); otherwise read again and redo the choice with the newer answers.
        res = await session.execute(
            update(Attempt).where(Attempt.id == aid, Attempt.status == "in_progress",
                                  Attempt.client_seq == seen_seq).values(
                status="submitted", submitted_at=now, receipt=new_receipt(), score=score,
                needs_grading=pending,
                answers=answers, client_seq=max(body.client_seq, seen_seq),
                answered=count_answered(answers),
                late=late, late_answers=late_answers, auto_submitted=False, updated_at=now))
        if res.rowcount == 1 and exam is not None and exam.source == "dkuoj":
            from .dkuoj.delivery import enqueue      # outbox row in the hand-in's transaction
            await enqueue(session, aid, exam.id, now)
        await session.commit()
        if res.rowcount == 1 and late:
            mins = int((now - a.deadline_at).total_seconds() // 60)
            await ac.add_event(session, get_store(), aid, a.exam_id, "late_submission",
                               f"handed in {mins} min after time"
                               + (f"; {len(changed)} answer(s) sent after time were not graded" if changed else ""))
        # rowcount 0: a concurrent submit/close won (refresh returns its receipt), or a
        # newer PUT moved client_seq (loop again with it).
        await session.refresh(a)
        if res.rowcount == 1:
            break
    if a.status != "submitted":
        raise HTTPException(409, "Answers changed while handing in, try again")
    await cache_attempt(get_store(), a)
    return {"status": "submitted", "submitted_at": iso(a.submitted_at), "receipt": a.receipt}


@router.post("/attempts/{aid}/app-beat")
async def app_beat(aid: str, body: AppBeatIn, request: Request,
                   p: Principal = Depends(require_student),
                   session: AsyncSession = Depends(get_session)):
    """Signed signal from the Mac app itself (audit #2, #6): the only source of "locked"
    in the live room. A gap in these beats is flagged (app killed / Mac asleep)."""
    store = get_store()
    settings = ac.settings_of(request)
    min_version = check_app_version(request, settings)   # S2: 426 for an outdated app
    meta = await get_attempt_meta(store, session, aid)
    if meta is None or meta["student_id"] != p.user_id:
        raise HTTPException(404, "Attempt not found")
    if meta.get("session_id") != body.session or (
            meta.get("device_id") and meta["device_id"] != body.device_id):
        raise HTTPException(409, ac.SESSION_DETAIL)
    msg = ac.beat_message(aid, body.session, body.device_id, body.ts, body.locked, body.checks)
    if not ac.verify(settings, msg, body.signature):
        raise HTTPException(403, ac.APP_PROOF_DETAIL)
    raw = await store.get(f"abt:{aid}")
    last = json.loads(raw) if raw else None
    # Replayed or reordered beat. Per session (audit S-13): a new Mac / new session with
    # a slower clock is not rejected because of the old session's timestamps.
    if last and last.get("session", body.session) == body.session and body.ts <= last["ts"]:
        raise HTTPException(409, "Old app signal")
    now = time.time()
    exam_id = meta["exam_id"]
    # Audit M-02: a Mac whose login session is in the background (fast user switching,
    # login window) is not locked, whatever the app says.
    checks = ac.parse_checks(body.checks)
    locked = body.locked and checks.get("console") != "0" and ac.ipad_lock_held(checks, settings.dev)
    if checks.get("console") == "0" and meta["status"] == "in_progress" \
            and await ac.once_per(store, f"offcon:{aid}", ac.FLAG_REPEAT_S):
        detail = ("this iPad left the exam, or its screen is recorded or mirrored"
                  if checks.get("plat") == "ipad" else
                  "this Mac switched to another user or the login window")
        await ac.add_event(session, store, aid, exam_id, "off_console", detail)
    if checks.get("secinput") == "1" and meta["status"] == "in_progress" \
            and await ac.once_per(store, f"secin:{aid}", ac.FLAG_REPEAT_S):
        await ac.add_event(session, store, aid, exam_id, "secure_input_other_app",
                           "another app holds secure keyboard input (it may see keys)")
    if meta["status"] == "in_progress":
        await _check_strong_lock(session, store, aid, exam_id, checks)
    if last and meta["status"] == "in_progress" and now - last["t"] > ac.APP_GAP_S:
        gap = f"no signal from the app for {int(now - last['t'])} s"
        # iPad re-review N8: under Assessment Mode with teacher-allowed apps, iPadOS
        # suspends FairPlay while the student is in one of them (e.g. Calculator).
        prev = ac.parse_checks(last.get("checks", ""))
        if prev.get("plat") == "ipad" and prev.get("aac") == "1" and prev.get("apps", "0") not in ("", "0"):
            gap += " (Assessment Mode was on: probably in a teacher-allowed app)"
        await ac.add_event(session, store, aid, exam_id, "app_gap", gap)
    if last and last.get("locked") and not locked and meta["status"] == "in_progress":
        await ac.add_event(session, store, aid, exam_id, "unlocked_in_exam",
                           "the app reports it is no longer locked")
    # Audit M-07 (server half): one app session whose signal moves to another network
    # address is flagged (a copied device id / session used from somewhere else shows up
    # here; so does an honest Wi-Fi switch, which is why it is a note, not a block).
    ip = request.client.host if request.client else ""
    ip_key = f"abip:{aid}:{body.session}"
    seen_ip = await store.get(ip_key)
    if ip and seen_ip and seen_ip != ip and meta["status"] == "in_progress" \
            and await ac.once_per(store, f"ipchg:{aid}", ac.FLAG_REPEAT_S):
        await ac.add_event(session, store, aid, exam_id, "network_changed",
                           "the app's signal now comes from another network address")
    if ip and seen_ip != ip:
        await store.set(ip_key, ip, LIVE_TTL)
    entry = {"t": now, "ts": body.ts, "locked": locked, "checks": body.checks,
             "session": body.session}
    await store.set(f"abt:{aid}", json.dumps(entry), LIVE_TTL)
    await store.hset(f"ab:{exam_id}", aid, json.dumps(entry), LIVE_TTL)
    return {"ok": True, "status": meta["status"], "server_time": iso(utcnow()),
            "deadline_at": meta["deadline_at"], "min_app_version": min_version,
            "approval": meta.get("approval")}   # S3


async def _check_strong_lock(session: AsyncSession, store, aid: str, exam_id: str,
                             checks: dict[str, str]) -> None:
    """S6: the exam requires the strongest lock and this beat doesn't say sep=1 (Windows:
    separate exam desktop; the Mac never sends it yet): flag weak_lock once per attempt."""
    if checks.get("sep") == "1":
        return
    exam = await get_exam_cached(store, session, exam_id)     # cache hit on the hot path
    if exam and exam.get("require_strong_lock") \
            and await ac.once_per(store, f"weak:{aid}", LIVE_TTL):
        await ac.add_event(session, store, aid, exam_id, "weak_lock",
                           "this computer is not using the strongest lock the exam requires")


@router.get("/attempts/{aid}/handin")
async def handin_status(aid: str, request: Request, nonce: str, device: str | None = None,
                        p: Principal = Depends(require_student),
                        session: AsyncSession = Depends(get_session)):
    """Audit M-04: may the app unlock? Always 200 with a signed answer, so a man in the
    middle can't fake "submitted" / "gone" (he lacks the app key). status: "submitted",
    "in_progress", "gone" (no such attempt for this student), "moved" (another session).
    proofs = one HMAC per app key over "handin|<aid>|<session>|<status>|<nonce>"."""
    if not ac.AppSafe.token(nonce, 8, 128) or (device is not None and not ac.AppSafe.token(device, 8, 64)):
        raise HTTPException(422, "bad nonce")
    sess = (request.headers.get(ac.SESSION_HEADER) or "").strip()
    # Audit 2026-10-13: the session goes into the signed message; no separators in it.
    if sess and not ac.AppSafe.token(sess, 1, 128):
        raise HTTPException(422, "bad session")
    a = await session.get(Attempt, aid)
    if a is not None and a.student_id != p.user_id:
        # Review 2026-10-14: someone else's attempt is not "gone" (which ends a lock): a page
        # that swapped in another student's token gets an unsigned 404, i.e. "not yet".
        raise HTTPException(404, "Attempt not found")
    if a is None:
        status = "gone"
    elif a.session_id and a.session_id != sess and not (device and a.device_id == device):
        # Audit 2026-10-13: "moved" means the attempt now lives on ANOTHER computer. The
        # app sends its own device id; on that same Mac a session the page made up (or an
        # old one) is just "in_progress", so it can't be used to end the lock.
        status = "moved"
    else:
        status = "submitted" if a.status == "submitted" else "in_progress"
    msg = ac.handin_message(aid, sess, status, nonce)
    return {"status": status, "proofs": [ac.sign(k, msg) for k in ac.settings_of(request).app_keys]}


@router.post("/attempts/{aid}/exit-code")
async def exit_code(aid: str, body: ExitCodeIn, request: Request,
                    p: Principal = Depends(require_student),
                    session: AsyncSession = Depends(get_session)):
    """Invigilator emergency exit (audit #5): the code lives on the server only, per exam,
    with an attempt limit. The app unlocks only after {"ok": true}."""
    store = get_store()
    a = await _own_attempt(session, aid, p)
    await ac.check_session(request, session, store, aid, _meta_of(a))
    lock_key, count_key = f"exitlock:{aid}", f"exitwrong:{aid}"
    # Audit 2026-10-13: the try is reserved before the (awaited) DB read, so parallel
    # guesses can't all slip past the lock check.
    n = await take_code_try(store, lock_key, count_key, MAX_WRONG_EXIT, 600,
                            "Too many wrong codes, wait 5 minutes", WRONG_EXIT_LOCK_S)
    exam = await session.get(Exam, a.exam_id)
    code = (exam.invigilator_code or "") if exam else ""
    if not same_code(body.code, code):      # audit S-12: compare bytes
        await wrong_code(store, lock_key, count_key, n, MAX_WRONG_EXIT, WRONG_EXIT_LOCK_S)
        await ac.add_event(session, store, aid, a.exam_id, "emergency_exit_rejected",
                           f"wrong invigilator code ({n})")
        raise HTTPException(403, "Wrong invigilator code")
    await store.delete(count_key)
    await ac.add_event(session, store, aid, a.exam_id, "emergency_exit",
                       "invigilator code accepted")
    out: dict = {"ok": True}
    if body.nonce:
        # Audit 2026-10-13: signed like the hand-in status, so the app unlocks only on a
        # reply the server really sent (a man in the middle lacks the app key).
        sess = ac.session_header(request) or ""
        keys = ac.settings_of(request).app_keys
        msg = ac.exit_message(aid, sess, body.nonce)
        out.update(nonce=body.nonce, proofs=[ac.sign(k, msg) for k in keys])
        if keys:   # Windows app (first / current key)
            out["proof"] = ac.sign(keys[0], ac.exit_verdict_message(aid, sess, body.nonce))
    return out


@router.post("/attempts/{aid}/offline-key")
async def offline_key(aid: str, body: OfflineKeyIn, request: Request,
                      p: Principal = Depends(require_student),
                      session: AsyncSession = Depends(get_session)):
    """Key for the offline invigilator exit (OfflineExit.swift), handed to the app once
    per app session. The page never sees it. A second request for the same session is
    refused and flagged: someone other than the app is asking."""
    settings = ac.settings_of(request)
    store = get_store()
    a = await _own_attempt(session, aid, p)
    await ac.check_session(request, session, store, aid, _meta_of(a))
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    if body.session != a.session_id or (a.device_id and a.device_id != body.device_id):
        raise HTTPException(409, ac.SESSION_DETAIL)
    msg = ac.offline_key_message(aid, body.session, body.device_id, body.ts)
    if not ac.verify(settings, msg, body.signature):
        raise HTTPException(403, ac.APP_PROOF_DETAIL)
    # Review 2026-10-14: the bound device may ask again (a relaunch after a crash, a lost
    # reply); the key is the same and only that signed device gets here. Unusually many asks
    # are still flagged once.
    if await store.incr(f"offkey:{aid}:{body.session}", LIVE_TTL) > OFFLINE_KEY_ASKS \
            and await ac.once_per(store, f"offkey-flag:{aid}:{body.session}", LIVE_TTL):
        await ac.add_event(session, store, aid, a.exam_id, "offline_key_requested_twice",
                           f"the offline exit key for this session was asked for more than {OFFLINE_KEY_ASKS} times")
    key_hex = ac.offline_key(settings.secret, aid, body.session).hex()
    out = {"key": key_hex}
    if settings.app_keys:
        msg = ac.offkey_response_message(aid, body.session, key_hex, body.ts)
        # Windows checks `proof` (first key); iPad checks `proofs` (one per key, so apps
        # built with any key in a rotation can verify it; review M2 2026-10-20).
        out["proof"] = ac.sign(settings.app_keys[0], msg)
        out["proofs"] = [ac.sign(k, msg) for k in settings.app_keys]
    return out


# ---------------------------------------------------------------- S4: device swap
@router.post("/attempts/{aid}/swap-request", status_code=202)
async def swap_request(aid: str, body: SwapRequestIn, p: Principal = Depends(require_student),
                       session: AsyncSession = Depends(get_session)):
    """The student's laptop broke: from the other computer (start answered 409
    swap_needed) ask the teacher to move the exam here. That computer keeps calling start
    every 5 s and binds once the teacher approved (POST .../approve-swap)."""
    store = get_store()
    if await store.incr(f"swaprate:{aid}", 60) > SWAP_REQUESTS_PER_MIN:
        raise HTTPException(429, "Too many tries, wait a minute")
    a = await _own_attempt(session, aid, p)
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    now = utcnow()
    if now > a.deadline_at + LATE_GRACE:
        raise HTTPException(409, ac.SWAP_TOO_LATE_DETAIL)
    a.swap_device_id, a.swap_requested_at = body.device_id, now
    await session.commit()
    if await ac.once_per(store, f"swapreq:{aid}:{body.device_id}", 60):
        await ac.add_event(session, store, aid, a.exam_id, "swap_requested",
                           f"wants to continue on device {body.device_id[:8]}")
    return {"ok": True}


# ---------------------------------------------------------------- S1: device check
@router.post("/device-check")
async def device_check(body: DeviceCheckIn, p: Principal = Depends(require_student),
                       session: AsyncSession = Depends(get_session)):
    """The Readiness page's "Run check": keeps the latest report per (student, platform)
    so the teacher sees before the exam who still has a problem to fix."""
    if await get_store().incr(f"devchk:{p.user_id}", 60) > DEVICE_CHECKS_PER_MIN:
        raise HTTPException(429, "Checking too often, wait a minute")
    now = utcnow()
    values = {"app_version": body.app_version, "os_version": body.os_version,
              "checks": [c.model_dump() for c in body.checks], "exam_id": body.exam_id,
              "checked_at": now}
    for _ in range(2):     # a parallel first check may insert the row first: update it then
        row = (await session.execute(select(DeviceCheck).where(
            DeviceCheck.student_id == p.user_id, DeviceCheck.platform == body.platform))).scalar_one_or_none()
        if row is None:
            session.add(DeviceCheck(student_id=p.user_id, platform=body.platform, **values))
        else:
            for k, v in values.items():
                setattr(row, k, v)
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
    return {"ok": True, "saved_at": iso(now)}


# ---------------------------------------------------------------- slice 2: grades
@router.get("/grades")
async def my_grades(p: Principal = Depends(require_student),
                    session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Exam.id, Exam.title, Exam.course, Exam.total_points, Exam.grades_released,
               Attempt.submitted_at, Attempt.score, Exam.source, Attempt.oj_result)
        .join(Attempt, Attempt.exam_id == Exam.id)
        .where(Attempt.student_id == p.user_id, Attempt.status == "submitted",
               Exam.is_trial == false())     # the sample exam never counts
        .order_by(Attempt.submitted_at.desc(), Exam.id))).all()
    out = []
    for eid, title, course, total, released, sub, score, source, oj_result in rows:
        released = bool(released)
        if source == "dkuoj":
            released, score, total = _oj_released(oj_result, total)
        out.append({"exam_id": eid, "title": title, "course": course or "",
                    **({"source": "dkuoj"} if source == "dkuoj" else {}),
                    "submitted_at": iso(sub), "released": released,
                    "score": num(score or 0.0) if released else None,
                    "total_points": num(total),
                    "percent": percent(score or 0.0, total) if released else None})
    return out


def _correct_answer(q: dict):
    t = q.get("type")
    if t == "mcq":
        return q.get("correct_index")
    if t == "numeric":
        return q.get("answer_number")
    if t == "short":
        acc = q.get("accepted_answers") or []
        return acc[0] if acc else None
    return None


@router.get("/grades/{exam_id}")
async def my_grade_detail(exam_id: str, p: Principal = Depends(require_student),
                          session: AsyncSession = Depends(get_session)):
    a = await find_attempt(session, exam_id, p.user_id)
    if a is None or a.status != "submitted":
        raise HTTPException(404, "No submitted attempt for this exam")
    e = await session.get(Exam, exam_id)
    if e is None or e.is_trial:
        raise HTTPException(404, "No submitted attempt for this exam")
    if e.source == "dkuoj":
        return await _oj_grade_detail(session, e, a)
    if not e.grades_released:
        raise HTTPException(403, "Grades not released yet")
    questions = exam_questions(e)
    score, _, rows = grade_attempt(questions, a.answers, a.grades)
    # Class average from the stored per-attempt score (kept in sync by every grade change):
    # one aggregate instead of re-grading every attempt per student request.
    avg = (await session.execute(
        select(func.avg(Attempt.score))
        .where(Attempt.exam_id == exam_id, Attempt.status == "submitted"))).scalar()
    out_q = []
    for i, (q, r) in enumerate(zip(questions, rows)):
        pts = float(r["points_awarded"])
        mx = q.get("points", 1)
        if not is_answered(r["given"]):
            status = "unanswered"
        elif abs(pts - mx) < 1e-9:
            status = "correct"
        elif pts > 0:
            status = "partial"
        else:
            status = "wrong"
        out_q.append({
            "question_id": q["id"], "index": i, "type": q["type"], "prompt": q["prompt"],
            "options": list(q.get("options") or []), "points": mx,
            "points_awarded": r["points_awarded"], "status": status, "given": r["given"],
            "correct_answer": _correct_answer(q),
            "tolerance": q.get("tolerance") if q.get("type") == "numeric" else None,
            "comment": r["comment"], "rubric": [dict(x) for x in q.get("rubric") or []],
            "images": [dict(x) for x in q.get("images") or []],  # slice 6
            "rubric_scores": r["rubric_scores"],
        })
        if q.get("type") == "code":   # phase 4: the code is shown with its language
            out_q[-1]["language"] = code_language(q, exam_default_language(e))
        if r.get("image"):  # slice 6 D: the student's scanned answer (paper exams)
            out_q[-1]["image"] = r["image"]
    return {
        "exam_id": e.id, "title": e.title, "course": e.course or "",
        "submitted_at": iso(a.submitted_at), "receipt": a.receipt, "score": num(score),
        "total_points": num(e.total_points), "percent": percent(score, e.total_points),
        "class_average_percent": percent(float(avg), e.total_points) if avg is not None else None,
        "overall_comment": a.overall_comment or "", "questions": out_q,
    }


async def _oj_grade_detail(session: AsyncSession, e: Exam, a: Attempt) -> dict:
    """DKU OJ bridge: the student's released DKU OJ result (graded there), in the same
    shape as a FairPlay grade page. No answer keys exist: correct_answer is always null."""
    released, score, total = _oj_released(a.oj_result, e.total_points)
    if not released:
        raise HTTPException(403, "Grades not released yet")
    from .services import student_question
    questions = exam_questions(e)
    _, _, rows = grade_attempt(questions, a.answers, oj_grades(questions, a.oj_result))
    out_q = []
    for i, (q, r) in enumerate(zip(questions, rows)):
        pts, mx = float(r["points_awarded"]), q.get("points", 1)
        if not is_answered(r["given"]) and not pts:
            status = "unanswered"
        elif not r["manual"]:
            status = "pending"            # DKU OJ sent no points for it (yet)
        elif abs(pts - mx) < 1e-9:
            status = "correct"
        elif pts > 0:
            status = "partial"
        else:
            status = "wrong"
        sq = student_question(q)
        out_q.append({
            **sq, "question_id": q["id"], "index": i, "type": q["type"], "prompt": q["prompt"],
            "options": list(q.get("options") or []), "points": mx,
            "points_awarded": r["points_awarded"], "status": status, "given": r["given"],
            "correct_answer": None, "tolerance": None,
            "comment": r["comment"], "comment_format": "oj_md",
            "rubric": [], "images": [], "rubric_scores": None,
        })
    others = (await session.execute(select(Attempt.oj_result).where(
        Attempt.exam_id == e.id, Attempt.status == "submitted"))).scalars().all()
    pcts = [percent(float(r.get("total") or 0), float(r.get("total_points") or total or 0))
            for r in others if r and r.get("released")]
    return {
        "exam_id": e.id, "title": e.title, "course": e.course or "", "source": "dkuoj",
        "submitted_at": iso(a.submitted_at), "receipt": a.receipt, "score": num(score),
        "total_points": num(total), "percent": percent(score, total),
        "class_average_percent": round(sum(pcts) / len(pcts), 1) if pcts else None,
        "overall_comment": "", "questions": out_q,
        "graded_in": "dkuoj", "oj_updated_at": (a.oj_result or {}).get("updated_at"),
    }

"""2026-10-22: the built-in sample exam ("Try a sample exam").

Every student sees one sample exam on their dashboard and can take it any time, without a
class or a start code, to feel how an exam works: the same app proof, lock, apps-to-close
step, heartbeats and app beats as a real locked exam. "Stop practice" ends it at once
(POST /api/student/attempts/{aid}/trial-stop): the attempt becomes `submitted`, so the
apps unlock through the usual signed hand-in (GET .../handin). Nothing else can end a lock.

The exam (Exam.is_trial) belongs to a system account that cannot sign in, is in no class,
and is excluded from every teacher view and from the student's exams and grades. One
attempt per student (the attempts table allows one per exam and student): starting again
continues one still running on the same computer, otherwise replaces it, so retakes are
unlimited.

No teacher and no invigilator: if the server can't be reached, Stop can't end the lock.
GET .../trial-key hands the PAGE this attempt's offline-exit key (sample exam only), so the
page can turn the code the locked app shows into the 8-digit exit code itself, the same
derivation as the teacher's live room (CONTRACT "Offline exit").

Not to be confused with "practice assignments" (routes_practice.py, homework sets).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import app_client as ac
from .app_version import check_app_version
from .db import Database, get_session
from .models import Attempt, Exam, IntegrityEvent, User
from .schemas import ClientProofIn, QuestionIn
from .security import Principal, require_student
from .services import (attempt_key, attempt_view, cache_attempt, find_attempt, get_exam_cached,
                       invalidate_exam, new_id, new_invigilator_code, new_session_id)
from .store import get_store
from .timeutil import iso, utcnow

log = logging.getLogger("proctora")

TRIAL_EXAM_ID = "e_sample_exam"
# System owner of the sample exam. Role "system" never gets a token (security.parse_token
# accepts teacher/student only) and "!" is no password hash, so nobody can sign in as it.
TRIAL_OWNER = "__fairplay_sample__"
TRIAL_ROLE = "system"
TRIAL_TITLE = "Sample questions"
TRIAL_DURATION_MIN = 10
# A second start within this window is a double click: the same attempt and session.
TRIAL_REENTRY_QUIET_S = 15
STOP_DETAIL = "Only the sample exam can be stopped this way."

# "Test my device": five easy multiple-choice questions, so students see how questions look
# in the locked app. Nothing is graded; "Stop" works at any time.
def _mcq(n: int, prompt: str, options: list[str], correct: int) -> dict:
    return {"id": f"q_sample_{n}", "type": "mcq", "points": 1, "topics": ["sample"],
            "prompt": prompt, "options": options, "correct_index": correct}


TRIAL_QUESTIONS = [
    _mcq(1, "2 + 2 = ?", ["3", "4", "5", "22"], 1),
    _mcq(2, "5 × 3 = ?", ["8", "12", "15", "53"], 2),
    _mcq(3, "10 − 4 = ?", ["6", "7", "14", "4"], 0),
    _mcq(4, "Which number is the largest?", ["19", "91", "9", "11"], 1),
    _mcq(5, "How many days are in a week?", ["5", "6", "7", "10"], 2),
]


def trial_questions() -> list[dict]:
    """The sample questions, validated and stored exactly like a teacher's questions."""
    return [{"id": q["id"], **QuestionIn(**q).to_dict()} for q in TRIAL_QUESTIONS]


async def seed_trial_exam(db: Database) -> None:
    """Idempotent: create (or bring up to date) the system owner and the sample exam.
    Runs at every startup, so several workers may race: the loser's insert fails and the
    winner's rows are used."""
    qs = trial_questions()
    total = float(sum(q["points"] for q in qs))
    async with db.sessionmaker() as session:
        owner = (await session.execute(select(User.id).where(User.username == TRIAL_OWNER))).scalar()
        if owner is None:
            session.add(User(username=TRIAL_OWNER, name="FairPlay", role=TRIAL_ROLE, password_hash="!"))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            owner = (await session.execute(select(User.id).where(User.username == TRIAL_OWNER))).scalar()
        e = await session.get(Exam, TRIAL_EXAM_ID)
        now = utcnow()
        if e is None:
            session.add(Exam(
                id=TRIAL_EXAM_ID, teacher_id=owner, title=TRIAL_TITLE, duration_min=TRIAL_DURATION_MIN,
                status="live", start_code=None, questions=qs, question_count=len(qs), total_points=total,
                created_at=now, updated_at=now, course="", room="", mode="questions", delivery="computer",
                class_id=None, course_link_id=None, allowed_apps=[], invigilator_code=new_invigilator_code(),
                # No teacher is there to approve a comeback or watch for a weak lock.
                resume_needs_approval=False, require_strong_lock=False, is_trial=True))
            try:
                await session.commit()
                log.info("created the sample exam")
            except IntegrityError:          # another worker created it
                await session.rollback()
            return
        changed = False
        want = {"teacher_id": owner, "title": TRIAL_TITLE, "duration_min": TRIAL_DURATION_MIN,
                "status": "live", "questions": qs, "question_count": len(qs), "total_points": total,
                "class_id": None, "course_link_id": None, "resume_needs_approval": False,
                "require_strong_lock": False, "is_trial": True, "mode": "questions",
                "delivery": "computer", "start_code": None}
        for k, v in want.items():
            if getattr(e, k) != v:
                setattr(e, k, v)
                changed = True
        if not e.invigilator_code:
            e.invigilator_code = new_invigilator_code()
            changed = True
        if changed:
            e.updated_at = now
            await session.commit()


# ---------------------------------------------------------------- student API
router = APIRouter(prefix="/api/student", tags=["student"])


class TrialStartIn(BaseModel):
    client: ClientProofIn | None = None


async def _trial_exam(session: AsyncSession) -> dict:
    exam = await get_exam_cached(get_store(), session, TRIAL_EXAM_ID)
    if exam is None or not exam.get("is_trial"):
        raise HTTPException(404, "The sample exam is not available")
    return exam


@router.get("/trial")
async def trial_info(p: Principal = Depends(require_student),
                     session: AsyncSession = Depends(get_session)):
    """For the dashboard card: the sample exam and this student's current attempt."""
    exam = await _trial_exam(session)
    a = await find_attempt(session, TRIAL_EXAM_ID, p.user_id)
    return {"exam_id": exam["id"], "title": exam["title"], "duration_min": exam["duration_min"],
            "question_count": exam["question_count"],
            "attempt": {"attempt_id": a.id, "status": a.status} if a else None}


@router.get("/trial/challenge")
async def trial_challenge(p: Principal = Depends(require_student),
                          session: AsyncSession = Depends(get_session)):
    """Same single-use nonce as a real exam's challenge (the app signs it)."""
    from .routes_student import issue_challenge
    await _trial_exam(session)
    return await issue_challenge(p.user_id, TRIAL_EXAM_ID)


@router.post("/trial/start")
async def trial_start(body: TrialStartIn, request: Request, p: Principal = Depends(require_student),
                      session: AsyncSession = Depends(get_session)):
    """Start the sample exam: no class, no start code, but the same app proof as a real
    start (FAIRPLAY_REQUIRE_APP, dev builds, version gate). Starting again replaces the
    previous attempt once it is finished; one still running on this computer continues."""
    from .routes_student import verify_start_proof_keyed
    store = get_store()
    settings = ac.settings_of(request)
    min_version = check_app_version(request, settings)
    exam = await _trial_exam(session)
    proof = await verify_start_proof_keyed(store, settings, p.user_id, TRIAL_EXAM_ID, body.client)
    device_id = proof.device_id if proof else None
    device_pub = proof.pub if proof else None
    if proof is not None and device_pub and not proof.derived:
        raise HTTPException(403, ac.APP_PROOF_DETAIL)
    now = utcnow()
    for _ in range(3):
        old = await find_attempt(session, TRIAL_EXAM_ID, p.user_id)
        if old is not None and old.status == "in_progress" and old.deadline_at > now \
                and old.device_id == device_id and old.device_pub == device_pub:
            # Still running on this computer (or this browser): continue the same attempt,
            # so an app that is still locked for it is not asked to lock another one. A
            # double click keeps the session; a later start is a new app run (new session).
            a = old
            if not await store.get(f"entry:{a.id}"):
                a.session_id = new_session_id()
                a.entries = (a.entries or 0) + 1
                a.updated_at = now
                await session.commit()
                await store.set(f"entry:{a.id}", "1", TRIAL_REENTRY_QUIET_S)
            break
        if old is not None:          # finished, timed out or on another computer: replace
            await discard_trial_attempt(session, store, old)
        a = Attempt(id=new_id("a"), exam_id=TRIAL_EXAM_ID, student_id=p.user_id, status="in_progress",
                    started_at=now, deadline_at=now + timedelta(minutes=exam["duration_min"]),
                    answers={}, client_seq=-1, updated_at=now, device_id=device_id, device_pub=device_pub,
                    session_id=new_session_id(), entries=1, client="app" if device_id else "browser")
        session.add(a)
        try:
            await session.commit()
        except IntegrityError:        # a parallel start inserted first: use that one
            await session.rollback()
            session.expunge_all()
            continue
        await store.set(f"entry:{a.id}", "1", TRIAL_REENTRY_QUIET_S)
        break
    else:
        raise HTTPException(409, "Try again")
    await cache_attempt(store, a)
    return {**attempt_view(a, exam, now), "session": a.session_id, "min_app_version": min_version,
            "needs_approval": False}


async def discard_trial_attempt(session: AsyncSession, store, a: Attempt) -> None:
    """Remove a sample-exam attempt with its events and live-room entries. An app still
    locked on it hears "gone" from GET .../handin, which is a signed end like submitted."""
    aid = a.id
    await session.execute(delete(IntegrityEvent).where(IntegrityEvent.attempt_id == aid))
    await session.execute(delete(Attempt).where(Attempt.id == aid, Attempt.exam_id == TRIAL_EXAM_ID))
    await session.commit()
    session.expunge_all()
    await store.delete(attempt_key(aid), f"abt:{aid}", f"entry:{aid}", f"evtotal:{aid}")
    for h in ("hb", "ab", "evc", "evl"):
        await store.hdel(f"{h}:{TRIAL_EXAM_ID}", aid)


@router.post("/attempts/{aid}/trial-stop")
async def trial_stop(aid: str, request: Request, p: Principal = Depends(require_student),
                     session: AsyncSession = Depends(get_session)):
    """"Stop practice": end this student's sample-exam attempt now. Refused (403) for any
    other exam. The attempt becomes submitted without grading (answers dropped); the page
    then asks the app to unlock, and the app unlocks on the signed hand-in as always."""
    store = get_store()
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    exam = await session.get(Exam, a.exam_id)
    if exam is None or not exam.is_trial:
        raise HTTPException(403, STOP_DETAIL)
    await ac.check_session(request, session, store, aid,
                           {"exam_id": a.exam_id, "session_id": a.session_id, "status": a.status})
    if a.status != "submitted":
        now = utcnow()
        await session.execute(
            update(Attempt).where(Attempt.id == aid, Attempt.status == "in_progress").values(
                status="submitted", submitted_at=now, answers={}, answered=0, score=None,
                needs_grading=0, grades={}, auto_submitted=False, receipt=None, updated_at=now))
        await session.commit()
        await session.refresh(a)
    await cache_attempt(store, a)
    return {"status": a.status, "submitted_at": iso(a.submitted_at)}


@router.get("/attempts/{aid}/trial-key")
async def trial_key(aid: str, request: Request, p: Principal = Depends(require_student),
                    session: AsyncSession = Depends(get_session)):
    """The offline-exit key of this student's running sample-exam attempt for the current
    session (sent as X-FairPlay-Session). Refused (403) for any other exam: a real exam's
    key exists only in the app and with the invigilator."""
    store = get_store()
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    exam = await session.get(Exam, a.exam_id)
    if exam is None or not exam.is_trial:
        raise HTTPException(403, "Only for the sample exam.")
    await ac.check_session(request, session, store, aid,
                           {"exam_id": a.exam_id, "session_id": a.session_id, "status": a.status})
    if a.status != "in_progress" or not a.session_id:
        raise HTTPException(409, "The sample exam has ended")
    settings = ac.settings_of(request)
    return {"key": ac.offline_key(settings.secret, aid, a.session_id).hex()}


async def refresh_trial_cache() -> None:
    """Drop the cached sample exam (after the seed changed it)."""
    await invalidate_exam(get_store(), TRIAL_EXAM_ID)

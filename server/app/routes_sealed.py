"""S5 (2026-10-20): sealed offline hand-in.

When the submit at time-up fails (no network), the page asks the app to seal the answers
({action:"seal"}). The app signs them with the attempt's offline key and keeps the file
until it can upload it here, possibly after a restart, a new token and a rotated session.

The offline key is derived from the server secret and the session named in the payload,
so a seal stays verifiable after that session was rotated away. It lives on the student's
computer, so a determined student could extract it: every sealed hand-in therefore goes
to the teacher's review list (event "sealed_submit"), whatever the outcome.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from . import app_client as ac
from .db import get_session
from .grading import handin_grade
from .models import Attempt, Exam
from .routes_student import LATE_GRACE, _known_answers, _own_attempt, count_answered
from .schemas import AnswersIn, SealedSubmitIn
from .security import Principal, require_student
from .services import cache_attempt, new_receipt
from .store import get_store
from .timeutil import iso, utcnow

router = APIRouter(prefix="/api/student", tags=["student"])

SEALED_PER_MIN = 20
SEAL_CLOCK_SLACK = timedelta(minutes=2)    # the app's sealed_at may run a little ahead
SEAL_EVENT_TTL = 26 * 3600


def open_seal(secret: str, aid: str, body: SealedSubmitIn) -> tuple[ac.Seal, dict]:
    """Check the seal: payload shape, HMAC (constant time), answers hash. Returns the seal
    and its answers. 403 = not sealed by this attempt's key; 422 = malformed."""
    seal = ac.parse_seal(body.payload)
    if seal is None:
        raise HTTPException(422, "The sealed answers are damaged")
    if seal.aid != aid:
        raise HTTPException(422, "The sealed answers are for another exam")
    if not hmac.compare_digest(ac.seal_proof(secret, aid, seal.session, body.payload), body.proof):
        raise HTTPException(403, "The sealed answers could not be verified")
    if hashlib.sha256(body.answers_json.encode("utf-8")).hexdigest() != seal.sha:
        raise HTTPException(422, "The sealed answers do not match their seal")
    try:
        answers = AnswersIn(answers=json.loads(body.answers_json), client_seq=seal.client_seq).answers
    except (ValueError, ValidationError, RecursionError):
        raise HTTPException(422, "The sealed answers are damaged")
    return seal, answers


@router.post("/attempts/{aid}/sealed-submit")
async def sealed_submit(aid: str, body: SealedSubmitIn, request: Request,
                        p: Principal = Depends(require_student),
                        session: AsyncSession = Depends(get_session)):
    """No X-FairPlay-Session needed: the session may have been rotated (app restart,
    device swap) since the answers were sealed. The proof names its own session."""
    store = get_store()
    if await store.incr(f"sealrate:{aid}", 60) > SEALED_PER_MIN:
        raise HTTPException(429, "Too many tries, wait a minute")
    a = await _own_attempt(session, aid, p)
    seal, answers = open_seal(ac.settings_of(request).secret, aid, body)
    now = utcnow()
    if seal.sealed_at < a.started_at:
        raise HTTPException(422, "The sealed time is outside this exam")
    await _note_late(session, store, a, seal, body.proof, now)
    ahead = seal.sealed_at - now
    if ahead > timedelta(0):      # the student's clock runs fast: judge the seal as of arrival, tell the teacher
        seal.sealed_at = now
    answers = await _known_answers(store, session, aid, answers)
    seen = await _seen(store, aid, now)
    if a.status != "submitted":
        await _hand_in(session, a, seal, answers, body.proof, now)
        await session.refresh(a)
        if a.sealed_proof == body.proof:      # ours won (not a concurrent submit / close)
            await _note(session, store, a, seal, body.proof, _seal_detail(a, seal, seen))
            if ahead > SEAL_CLOCK_SLACK:
                await _note_ahead(session, store, a, body.proof, ahead)
            await cache_attempt(store, a)
            return _receipt(a)
    return await _after_submit(session, store, a, seal, answers, body.proof, seen)


async def _hand_in(session: AsyncSession, a: Attempt, seal: ac.Seal, answers: dict,
                   proof: str, now) -> None:
    """Saved as of sealed_at: in time -> the sealed answers are graded; later -> graded
    stays what was saved in time and the sealed ones go to late_answers (like submit)."""
    late = seal.sealed_at > a.deadline_at + LATE_GRACE
    stored = a.answers or {}
    changed = {k: v for k, v in answers.items() if stored.get(k) != v}
    final = stored if late else answers
    exam = await session.get(Exam, a.exam_id)
    # the sample exam and externally graded exams are never graded here
    score, pending = handin_grade(exam, final, a.grades)
    res = await session.execute(
        update(Attempt).where(Attempt.id == a.id, Attempt.status == "in_progress").values(
            status="submitted", submitted_at=min(seal.sealed_at, now), receipt=new_receipt(),
            score=score, needs_grading=pending, answers=final, answered=count_answered(final),
            client_seq=max(seal.client_seq, a.client_seq), late=late,
            late_answers=(changed or None) if late else None, late_sealed=late,
            sealed_proof=proof, auto_submitted=False, updated_at=now))
    from .integrations.registry import integration_for
    integ = integration_for(exam) if exam is not None else None
    if res.rowcount == 1 and integ is not None:   # e.g. outbox row, in this transaction
        await integ.on_handin(session, a.id, exam.id, now, "sealed")
    await session.commit()


async def _after_submit(session: AsyncSession, store, a: Attempt, seal: ac.Seal,
                        answers: dict, proof: str, seen: str):
    """The attempt was handed in already (an earlier upload of this seal, the page's own
    submit once online again, or the teacher closing the exam)."""
    if a.sealed_proof == proof:
        return _receipt(a)                    # the same seal again: same receipt
    stored = a.answers or {}
    diff = {k: v for k, v in answers.items() if stored.get(k) != v}
    if not diff:
        await _note(session, store, a, seal, proof, _seal_detail(a, seal, seen) + "; same as the handed-in answers")
        return _receipt(a)
    # Different: keep them aside for the teacher (never graded) and let the teacher decide.
    kept = dict(a.late_answers or {})
    for k, v in diff.items():
        kept.setdefault(k, v)
    a.late_answers = kept
    await session.commit()
    await _note(session, store, a, seal, proof,
                f"{_seal_detail(a, seal, seen)}; arrived after the hand-in and differs in "
                f"{len(diff)} answer(s), kept with the late answers (not graded): you decide")
    raise HTTPException(409, {"code": "already_submitted"})


async def _note(session: AsyncSession, store, a: Attempt, seal: ac.Seal, proof: str,
                detail: str) -> None:
    """One sealed_submit event per seal (repeated uploads don't pile up). Not in
    NON_FLAG_TYPES, so it always waits in the teacher's review list."""
    if await ac.once_per(store, f"sealev:{a.id}:{proof[:32]}", SEAL_EVENT_TTL):
        await ac.add_event(session, store, a.id, a.exam_id, "sealed_submit", detail)


async def _note_ahead(session: AsyncSession, store, a: Attempt, proof: str, ahead: timedelta) -> None:
    if await ac.once_per(store, f"sealahead:{a.id}:{proof[:32]}", SEAL_EVENT_TTL):
        await ac.add_event(session, store, a.id, a.exam_id, "sealed_clock_ahead",
                           f"the app's clock was {int(ahead.total_seconds())} s ahead of the server when it sealed; "
                           "the seal time was set to the arrival time")


async def _note_late(session: AsyncSession, store, a: Attempt, seal: ac.Seal, proof: str, now) -> None:
    """A verified seal that arrived after the exam's close plus the largest extra time."""
    from .closing import max_extra
    from .services import exam_closes_at
    exam = await session.get(Exam, a.exam_id)
    if exam is None:
        return
    ends = [x for x in (exam_closes_at(exam), exam.closed_at) if x is not None]
    if not ends:
        return
    window_end = min(ends) + await max_extra(session, exam)
    if now <= window_end:
        return
    if await ac.once_per(store, f"sealate:{a.id}:{proof[:32]}", SEAL_EVENT_TTL):
        await ac.add_event(session, store, a.id, a.exam_id, "sealed_received_late",
                           f"received {iso(now)}, sealed at {iso(seal.sealed_at)}, "
                           f"{int((now - window_end).total_seconds() // 60)} min after the close and extra time ended "
                           f"({iso(window_end)})")


async def _seen(store, aid: str, now) -> str:
    raw = await store.get(f"abt:{aid}")
    last = datetime.fromtimestamp(json.loads(raw)["t"], timezone.utc).replace(tzinfo=None) if raw else None
    return f"received {iso(now)}, last app signal {iso(last) if last else 'never'}"


def _seal_detail(a: Attempt, seal: ac.Seal, seen: str) -> str:
    when = ("after the time was up" if seal.sealed_at > a.deadline_at + LATE_GRACE
            else "in time by the seal's own clock")
    return (f"answers sealed offline at {iso(seal.sealed_at)} on device {seal.device_id[:8]} "
            f"({when}; {seen}) and uploaded later by the app; the seal key is on the student's computer, so check")


def _receipt(a: Attempt) -> dict:
    return {"ok": True, "receipt": a.receipt, "late_sealed": bool(a.late_sealed)}

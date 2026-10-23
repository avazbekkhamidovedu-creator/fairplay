"""DKU OJ bridge routes: the signed "Open FairPlay live room" link and a status JSON.

GET /oj/live?exam=<key>&user=<username>&exp=<unix>&sig=<hex HMAC-SHA256(token,
"live|<key>|<username>|<exp>")> (contract §1). The link is checked (signature, expiry
at most 5 minutes ahead, single use, user = an editor of that exam from the last sync),
then the browser goes to /teacher/oj-live?code=<one-time code>; the page trades the code
for a normal teacher sign-in at POST /api/oj/live/exchange and opens the live room. The
token never appears in a URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Exam, User
from ..security import Principal, make_token, require_teacher
from ..store import get_store
from .models import OjDelivery, OjEditor, OjState

router = APIRouter(tags=["dkuoj"])

LINK_MAX_AHEAD_S = 300        # DKU OJ makes links valid for 120 s; allow clock skew
CODE_TTL_S = 60
BAD_LINK = "This FairPlay link is not valid any more. Open the live room again from DKU OJ."


def live_signature(token: str, key: str, user: str, exp: str) -> str:
    return hmac.new(token.encode(), f"live|{key}|{user}|{exp}".encode(), hashlib.sha256).hexdigest()


def _settings(request: Request):
    return request.app.state.settings


@router.get("/oj/live", include_in_schema=False)
async def oj_live(request: Request, exam: str = "", user: str = "", exp: str = "", sig: str = "",
                  session: AsyncSession = Depends(get_session)):
    s = _settings(request)
    if not s.dkuoj_enabled:
        raise HTTPException(404, "Not Found")
    try:
        exp_i = int(exp)
    except ValueError:
        raise HTTPException(403, BAD_LINK) from None
    now = int(time.time())
    if not exam or not user or len(sig) != 64 or exp_i < now or exp_i > now + LINK_MAX_AHEAD_S:
        raise HTTPException(403, BAD_LINK)
    if not hmac.compare_digest(live_signature(s.dkuoj_token, exam, user, exp), sig.lower()):
        raise HTTPException(403, BAD_LINK)
    e = (await session.execute(select(Exam).where(Exam.oj_key == exam, Exam.source == "dkuoj"))).scalar_one_or_none()
    u = (await session.execute(select(User).where(User.username == user))).scalar_one_or_none()
    if e is None or u is None or u.role != "teacher":
        raise HTTPException(403, "You are not an editor of this exam in FairPlay yet. Wait two minutes and try again.")
    is_editor = (await session.execute(select(OjEditor.id).where(
        OjEditor.exam_id == e.id, OjEditor.user_id == u.id))).first() is not None
    if not is_editor:
        raise HTTPException(403, "You are not an editor of this exam in FairPlay yet. Wait two minutes and try again.")
    store = get_store()
    if not await store.setnx(f"ojlive-used:{sig.lower()}", "1", LINK_MAX_AHEAD_S + 60):
        raise HTTPException(403, BAD_LINK)          # each link signs in once
    code = secrets.token_urlsafe(24)
    await store.set(f"ojlive:{code}", json.dumps({"uid": u.id, "exam_id": e.id}), CODE_TTL_S)
    return RedirectResponse(f"/teacher/oj-live?code={code}", status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class ExchangeIn(BaseModel):
    code: str = Field(min_length=8, max_length=128)


@router.post("/api/oj/live/exchange")
async def oj_live_exchange(body: ExchangeIn, request: Request, session: AsyncSession = Depends(get_session)):
    """One-time code from /oj/live -> a teacher sign-in (same shape as /api/auth/login)
    plus the FairPlay exam id to open."""
    s = _settings(request)
    if not s.dkuoj_enabled:
        raise HTTPException(404, "Not Found")
    raw = await get_store().getdel(f"ojlive:{body.code}")
    if not raw:
        raise HTTPException(403, BAD_LINK)
    data = json.loads(raw)
    u = await session.get(User, data["uid"])
    if u is None or u.role != "teacher":
        raise HTTPException(403, BAD_LINK)
    token = make_token(s.secret, u.id, u.role, s.token_ttl_s, version=u.token_version or 0)
    return {"token": token, "role": "teacher", "name": u.name, "username": u.username,
            "exam_id": data["exam_id"]}


@router.get("/api/dkuoj/status")
async def status(request: Request, p: Principal = Depends(require_teacher),
                 session: AsyncSession = Depends(get_session)):
    """Bridge health for teachers: last sync, outbox counts, and the failed hand-ins of
    their own DKU OJ exams. Never the token."""
    s = _settings(request)
    if not s.dkuoj_enabled:
        return {"enabled": False}
    state = {r.name: r.value for r in (await session.execute(select(OjState))).scalars()}
    counts = dict((await session.execute(select(OjDelivery.status, func.count()).group_by(OjDelivery.status))).all())
    edited = select(OjEditor.exam_id).where(OjEditor.user_id == p.user_id)
    mine = select(Exam.id).where(Exam.source == "dkuoj", or_(Exam.teacher_id == p.user_id, Exam.id.in_(edited)))
    bad = (await session.execute(select(OjDelivery).where(
        OjDelivery.exam_id.in_(mine), OjDelivery.status.in_(("dead", "failed")))
        .order_by(OjDelivery.created_at.desc()).limit(100))).scalars().all()
    exams = (await session.execute(select(func.count(Exam.id)).where(Exam.source == "dkuoj"))).scalar_one()
    return {"enabled": True, "url": s.dkuoj_url, "last_run": state.get("last_run"),
            "last_error": state.get("last_error"), "exams": int(exams),
            "deliveries": {k: int(counts.get(k, 0)) for k in ("pending", "failed", "sent", "dead")},
            "problems": [{"attempt_id": d.attempt_id, "exam_id": d.exam_id, "status": d.status,
                          "tries": d.tries, "last_error": d.last_error} for d in bad]}

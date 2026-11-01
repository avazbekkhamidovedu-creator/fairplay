"""Class logins and the student set-up (docs/dkuoj-bridge-v2.md §3, §4).

A professor creates one temporary class login per DKU OJ class (routes_v2). A student
signs in with it at POST /api/auth/login and gets a SET-UP token (not a sign-in: it is a
separate kind with its own key, refused by every other route). With it:

1. POST /api/setup/identify {first_name, last_name, netid}: FairPlay fetches the class
   roster from DKU OJ (signed) and checks the person is on it and not set up yet.
   Success -> an IDENTITY token (10 min, single use, bound to that NetID and login).
2. POST /api/setup/account {username, password, password2}: creates or completes the
   NetID's account and returns the normal sign-in answer.

Every failure of step 1 gives ONE generic message. Limits: 5 tries a minute per set-up
token; 5 failed tries an hour for the same typed NetID refuse that NetID (same message)
for the hour; failed tries for 40 DIFFERENT NetIDs in an hour (someone guessing who is in
the class) disable the class login. A NetID that is already set up is refused with the
same message but is not counted. Passwords and tokens are never logged or stored in
plain text. A class login's password is kept as a hash (for sign-in) and, since
2026-10-24, encrypted with a key derived from the server secret, so the professor can see
it again in DKU OJ (GET /classes/<id>/status); student passwords are only hashed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import User
from ...security import hash_executor, hash_password, make_token
from ...store import get_store
from ...timeutil import utcnow
from .access_codes import code_hmac
from .models import ClassLogin, ClassLoginEvent, ExamAccessCode

log = logging.getLogger("proctora.dkuoj")

router = APIRouter(prefix="/api/setup", tags=["setup"])

SETUP_TTL_S = 20 * 60
IDENTITY_TTL_S = 10 * 60
CLASS_LOGIN_DAYS = 14
TRIES_PER_MINUTE = 5
FAILS_PER_NETID_HOUR = 5        # per class login + typed NetID: then that NetID waits the hour
DISTINCT_NETIDS_HOUR = 40       # per class login: failing NetIDs that disable it (an attack)
FAIL_WINDOW_S = 3600
CODE_NEEDED = "Enter the access code from your own exam sheet to set up your account."
CODE_WRONG = "That access code does not match this NetID."
ACCOUNT_TRIES_PER_MINUTE = 10

EXPIRED = "This class login has expired. Ask your professor for a new one."
GENERIC = ("This name and NetID are not on the class list, or this NetID is already set up. "
           "Check them, or ask your professor.")
OJ_DOWN = "Can't check the class list right now, try again in a minute"
SESSION_OVER = "Your set-up has timed out. Sign in with the class login again."
TAKEN = "That username is taken"
TOO_MANY = "Too many tries. Wait a minute and try again."
USERNAME_RULE = "Choose a username of 3 to 32 characters: lower-case letters, digits, dots, dashes or underscores."
PASSWORD_MIN = 10
PASSWORD_RULE = ("Password needs at least 10 characters, a capital letter, a small letter, "
                 "a number and a symbol.")
PASSWORD_SAME = "Your password must not be your username or NetID."
PASSWORD_MISMATCH = "The two passwords are not the same."

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
NO_STORE = {"Cache-Control": "no-store"}


# ---------------------------------------------------------------- set-up tokens
def _key(secret: str, kind: str) -> bytes:
    """Own key per kind: a set-up / identity token can never pass as a sign-in token
    (security.parse_token uses the plain secret) or as the other kind."""
    return hmac.new(secret.encode(), f"fairplay setup token v1 {kind}".encode(), hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_kind_token(secret: str, kind: str, data: dict, ttl_s: int, now: float | None = None) -> str:
    body = {**data, "k": kind, "exp": int((now or time.time()) + ttl_s), "jti": secrets.token_hex(12)}
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_key(secret, kind), payload.encode(), hashlib.sha256).digest())
    return f"s{kind[0]}.{payload}.{sig}"


def parse_kind_token(secret: str, kind: str, token: str) -> dict | None:
    try:
        prefix, payload, sig = token.split(".")
        if prefix != f"s{kind[0]}":
            return None
        expected = _b64(hmac.new(_key(secret, kind), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        if not isinstance(data, dict) or data.get("k") != kind or int(data.get("exp", 0)) < time.time():
            return None
        return data
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


# ---------------------------------------------------------------- class-login password at rest
SEAL_LABEL = b"fairplay class-login password v1"
SEAL_PREFIX = "v1:"


def _seal_key(secret: str) -> bytes:
    """HKDF-SHA256(server secret, info=SEAL_LABEL) -> 32-byte AES key. Its own label, so it
    is unrelated to every other key made from the secret (tokens, signing)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=SEAL_LABEL).derive(secret.encode())


def seal_login_password(secret: str, username: str, password: str) -> str:
    """AES-256-GCM, random 96-bit nonce, the login's username as associated data (a sealed
    value copied onto another row does not open)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_seal_key(secret)).encrypt(nonce, password.encode(), username.encode())
    return SEAL_PREFIX + base64.b64encode(nonce + ct).decode()


def open_login_password(secret: str, username: str, sealed: str | None) -> str | None:
    """The plain password, or None (no copy, other secret, tampered)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not sealed or not sealed.startswith(SEAL_PREFIX):
        return None
    try:
        raw = base64.b64decode(sealed[len(SEAL_PREFIX):], validate=True)
        return AESGCM(_seal_key(secret)).decrypt(raw[:12], raw[12:], username.encode()).decode()
    except (InvalidTag, ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- class logins
WORDS = (
    "amber", "apple", "arrow", "aspen", "basil", "beach", "berry", "birch", "bloom", "brook",
    "cabin", "canal", "cedar", "chalk", "cider", "cliff", "cloud", "clover", "coral", "cotton",
    "creek", "daisy", "delta", "dune", "ember", "fable", "fern", "field", "flint", "forest",
    "frost", "garden", "glade", "grape", "grove", "harbor", "hazel", "heron", "hill", "honey",
    "island", "ivory", "jade", "juniper", "kettle", "lake", "lantern", "lemon", "lilac", "linen",
    "maple", "meadow", "melon", "mint", "moss", "nectar", "north", "oak", "ocean", "olive",
    "orbit", "otter", "pearl", "pebble", "pepper", "pine", "plum", "pond", "prairie", "quartz",
    "rain", "raven", "reef", "ridge", "river", "robin", "rose", "sage", "sand", "shore",
    "silver", "slate", "snow", "spruce", "stone", "storm", "sun", "thistle", "tide", "timber",
    "tulip", "valley", "velvet", "violet", "willow", "wind", "winter", "wren", "yarrow", "zephyr")
_DROP = {"section", "sec", "class", "group", "lab", "the", "of"}
_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"


def login_prefix(class_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", class_name or "").encode("ascii", "ignore").decode().lower()
    parts = [w for w in re.findall(r"[a-z0-9]+", ascii_name) if w not in _DROP]
    return "".join(parts)[:8] or "class"


def new_login_username(class_name: str) -> str:
    return f"{login_prefix(class_name)}-{''.join(secrets.choice(_ALNUM) for _ in range(4))}"


def new_login_password() -> str:
    while True:
        pw = "-".join(secrets.choice(WORDS) for _ in range(4)) + "-" + f"{secrets.randbelow(1000):03d}"
        if len(pw) >= 20:
            return pw


def login_active(cl: ClassLogin, now: datetime | None = None) -> bool:
    return cl.disabled_at is None and (now or utcnow()) < cl.expires_at


async def add_event(session: AsyncSession, class_id: int, kind: str, *, login_id: int | None = None,
                    netid: str | None = None, actor: str = "", detail: str = "") -> None:
    session.add(ClassLoginEvent(class_id=class_id, class_login_id=login_id, kind=kind,
                                netid=(netid or None) and netid[:64], actor=(actor or "")[:150],
                                detail=(detail or "")[:300], at=utcnow()))


async def disable_logins(session: AsyncSession, class_id: int, reason: str, now: datetime) -> int:
    res = await session.execute(update(ClassLogin).where(ClassLogin.class_id == class_id,
                                                         ClassLogin.disabled_at.is_(None))
                                .values(disabled_at=now, disabled_reason=reason))
    return res.rowcount or 0


async def create_class_login(session: AsyncSession, settings, class_id: int, class_name: str,
                             course_name: str, actor: str) -> tuple[ClassLogin, str]:
    """A new login for the class; any previous one stops at once. Returns (row, password).
    The password is kept hashed (sign-in) and sealed (the professor sees it again)."""
    now = utcnow()
    await disable_logins(session, class_id, "replaced", now)
    password = new_login_password()
    pw_hash = await asyncio.get_running_loop().run_in_executor(
        hash_executor(), hash_password, password, settings.pbkdf2_iterations)
    for _ in range(20):
        username = new_login_username(class_name)
        taken = (await session.execute(select(ClassLogin.id).where(ClassLogin.username == username))).first() \
            or (await session.execute(select(User.id).where(User.username == username))).first()
        if not taken:
            break
    else:
        raise HTTPException(409, "Could not make a unique class login; try again")
    cl = ClassLogin(class_id=class_id, class_name=class_name[:200], course_name=course_name[:200],
                    username=username, password_hash=pw_hash,
                    password_enc=seal_login_password(settings.secret, username, password), created_at=now,
                    expires_at=now + timedelta(days=CLASS_LOGIN_DAYS), created_by=actor[:150])
    session.add(cl)
    await session.flush()
    await add_event(session, class_id, "login_created", login_id=cl.id, actor=actor, detail=username)
    await session.commit()
    return cl, password


async def find_class_login(session: AsyncSession, username: str):
    """The class login row a sign-in names (only while the bridge is on), or None."""
    from .source import SOURCE
    if not class_login_enabled() or not SOURCE.enabled or not username:
        return None
    return (await session.execute(select(ClassLogin).where(ClassLogin.username == username.lower()))
            ).scalar_one_or_none()


def class_login_answer(settings, cl: ClassLogin) -> JSONResponse:
    """POST /api/auth/login with a class login's right password (routes_auth)."""
    if not login_active(cl):
        raise HTTPException(401, EXPIRED)
    token = make_kind_token(settings.secret, "setup", {"cl": cl.id}, SETUP_TTL_S)
    return JSONResponse({"setup_token": token, "class": {"name": cl.class_name, "course": cl.course_name}},
                        headers=NO_STORE)


# ---------------------------------------------------------------- names
def normalize_name(s: str) -> str:
    """Trim, collapse spaces, case-fold, strip accents (v2 §4)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split()).casefold()


def is_set_up(u: User) -> bool:
    """Has a username and password of their own (set up here, or an older FairPlay
    account with a real password)."""
    if u.set_up_at is not None:
        return True
    return not (u.username or "").startswith("oj:") and (u.password_hash or "").startswith("pbkdf2_")


def strong_password(pw: str) -> bool:
    """v2 §4: at least 10 characters with a small letter, a capital letter, a digit and a
    symbol (any character that is not a letter, digit or space)."""
    return (len(pw) >= PASSWORD_MIN and any(c.islower() for c in pw) and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw) and any(not c.isalnum() and not c.isspace() for c in pw))


# ---------------------------------------------------------------- the two set-up calls
class IdentifyIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    first_name: str = Field(max_length=150)
    last_name: str = Field(max_length=150)
    netid: str = Field(max_length=64)
    access_code: str = Field(default="", max_length=40)


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    password2: str = Field(max_length=256)


def class_login_enabled() -> bool:
    return os.environ.get("FAIRPLAY_CLASS_LOGIN", "").strip() == "1"


def require_class_login() -> None:
    if not class_login_enabled():
        raise HTTPException(404, "Not Found")


def _enabled():
    require_class_login()
    from .source import SOURCE
    if not SOURCE.enabled:
        raise HTTPException(404, "Not Found")
    return SOURCE


async def _active_login(session: AsyncSession, login_id) -> ClassLogin:
    cl = await session.get(ClassLogin, int(login_id)) if isinstance(login_id, int) else None
    if cl is None or not login_active(cl):
        raise HTTPException(401, EXPIRED)
    return cl


def _typed_netid(netid: str) -> str:
    return netid.strip().lower()[:64]


def _netid_fail_key(cl: ClassLogin, typed: str) -> str:
    return f"setup:fail:{cl.id}:nid:{hashlib.sha256(typed.encode()).hexdigest()[:32]}"


async def _netid_blocked(cl: ClassLogin, typed: str) -> bool:
    return int(await get_store().get(_netid_fail_key(cl, typed)) or 0) >= FAILS_PER_NETID_HOUR


async def _failed(session: AsyncSession, cl: ClassLogin, netid: str, attack: bool = True,
                  detail: str = "") -> None:
    """Record a failed identify (professors see every one as an event). attack=False (the
    NetID is on the list but already set up) counts nowhere. Otherwise: per typed NetID
    (FAILS_PER_NETID_HOUR in an hour block it for the hour), and the number of DIFFERENT
    failing NetIDs: the DISTINCT_NETIDS_HOUR-th in an hour disables the class login."""
    typed = _typed_netid(netid)
    await add_event(session, cl.class_id, "setup_failed", login_id=cl.id, netid=typed, detail=detail)
    if attack:
        store = get_store()
        cl.failed_count = int(cl.failed_count or 0) + 1
        n = await store.incr(_netid_fail_key(cl, typed), FAIL_WINDOW_S)
        if n == FAILS_PER_NETID_HOUR:
            await add_event(session, cl.class_id, "setup_netid_blocked", login_id=cl.id, netid=typed,
                            detail=f"{FAILS_PER_NETID_HOUR} failed set-up tries for this NetID in an hour")
        distinct = (await store.incr(f"setup:fail:{cl.id}:distinct", FAIL_WINDOW_S)
                    if n == 1 else int(await store.get(f"setup:fail:{cl.id}:distinct") or 0))
        if distinct >= DISTINCT_NETIDS_HOUR and cl.disabled_at is None:
            cl.disabled_at, cl.disabled_reason = utcnow(), "too_many_failures"
            await add_event(session, cl.class_id, "login_locked", login_id=cl.id,
                            detail=f"failed set-up tries for {DISTINCT_NETIDS_HOUR} different NetIDs in an hour")
            log.warning("DKU OJ class login %s disabled after failed set-ups for %d NetIDs", cl.id, distinct)
    await session.commit()


def code_mode() -> str:
    """DKUOJ_SETUP_ACCESS_CODE: "issued" (default: a NetID that already has an exam access
    code must give one), "always" (every set-up must give one; needs codes printed first) or
    "off". The class login and the name are shared knowledge in a class; the code is not."""
    v = os.environ.get("DKUOJ_SETUP_ACCESS_CODE", "issued").strip().lower()
    return v if v in ("off", "issued", "always") else "issued"


async def _code_state(session: AsyncSession, secret: str, netid: str, given: str) -> str:
    """"ok", "needed" (none given though required) or "wrong"."""
    mode = code_mode()
    if mode == "off":
        return "ok"
    rows = (await session.execute(select(ExamAccessCode.code_hmac).where(
        func.lower(ExamAccessCode.netid) == netid.strip().lower(), ExamAccessCode.revoked_at.is_(None)))).scalars().all()
    if not rows and mode == "issued":
        return "ok"
    if not (given or "").strip():
        return "needed"
    want = code_hmac(secret, given)
    return "ok" if any(hmac.compare_digest(want, h) for h in rows) else "wrong"


@router.post("/identify")
async def identify(body: IdentifyIn, request: Request, session: AsyncSession = Depends(get_session)):
    from .client import DkuojClient, OjError
    from .sync import find_by_netid
    source = _enabled()
    s = request.app.state.settings
    tok = parse_kind_token(s.secret, "setup", _bearer(request))
    if tok is None:
        raise HTTPException(401, SESSION_OVER)
    if await get_store().incr(f"setup:try:{tok['jti']}", 60) > TRIES_PER_MINUTE:
        raise HTTPException(429, TOO_MANY)
    cl = await _active_login(session, tok.get("cl"))
    class_id = cl.class_id
    typed = _typed_netid(body.netid)
    if await _netid_blocked(cl, typed):     # this NetID failed too often this hour (no DKU OJ call)
        await session.commit()
        raise HTTPException(400, GENERIC)
    await session.commit()      # no database connection held while DKU OJ answers
    try:
        async with DkuojClient.from_config(source.config) as client:
            roster = await client.roster(class_id)
    except OjError as err:
        if err.status != 404:
            log.warning("DKU OJ roster of class %s not available: %s", class_id, err.message)
            raise HTTPException(503, OJ_DOWN) from None
        roster = {"students": []}
    match = None
    for st in roster.get("students") or []:
        if isinstance(st, dict) and isinstance(st.get("username"), str) and st["username"].strip().lower() == typed:
            match = st
            break
    ok = (match is not None and typed
          and normalize_name(match.get("first_name")) == normalize_name(body.first_name)
          and normalize_name(match.get("last_name")) == normalize_name(body.last_name)
          and normalize_name(body.first_name) != "")
    netid = match["username"].strip()[:64] if match else ""
    if ok:
        u = await find_by_netid(session, netid)
        if u is not None and (u.role != "student" or is_set_up(u)):
            # on the list with the right name, but already set up (or not a student account
            # here): the same message, not counted as guessing
            await _failed(session, cl, netid, attack=False, detail="already set up")
            raise HTTPException(400, GENERIC)
    if not ok:
        await _failed(session, cl, body.netid)
        raise HTTPException(400, GENERIC)
    state = await _code_state(session, s.secret, netid, body.access_code)
    if state != "ok":
        await _failed(session, cl, netid, detail="access code " + state)
        raise HTTPException(400, CODE_NEEDED if state == "needed" else CODE_WRONG)
    await session.commit()      # find_by_netid may have linked an older account
    name = " ".join(x for x in (str(match.get("first_name") or "").strip(),
                                str(match.get("last_name") or "").strip()) if x) or netid
    token = make_kind_token(s.secret, "identity", {"cl": cl.id, "netid": netid, "name": name[:200]},
                            IDENTITY_TTL_S)
    return JSONResponse({"identity_token": token, "name": name, "netid": netid}, headers=NO_STORE)


@router.post("/account")
async def account(body: AccountIn, request: Request, session: AsyncSession = Depends(get_session)):
    from .sync import find_by_netid
    _enabled()
    s = request.app.state.settings
    store = get_store()
    tok = parse_kind_token(s.secret, "identity", _bearer(request))
    if tok is None or not isinstance(tok.get("netid"), str):
        raise HTTPException(401, SESSION_OVER)
    if await store.incr(f"setup:acct:{tok['jti']}", 60) > ACCOUNT_TRIES_PER_MINUTE:
        raise HTTPException(429, TOO_MANY)
    used_key = f"setup:ident-used:{tok['jti']}"
    if not await store.setnx(used_key, "1", IDENTITY_TTL_S + 60):
        raise HTTPException(401, SESSION_OVER)
    done = False
    try:
        out = await _make_account(session, s, tok, body, find_by_netid)
        done = True
        return out
    finally:
        if not done:        # a wrong username / password: the student may try again
            await store.delete(used_key)


async def _make_account(session: AsyncSession, s, tok: dict, body: AccountIn, find_by_netid) -> JSONResponse:
    cl = await _active_login(session, tok.get("cl"))
    netid, name = tok["netid"], str(tok.get("name") or tok["netid"])[:200]
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, USERNAME_RULE)
    if not strong_password(body.password):
        raise HTTPException(400, PASSWORD_RULE)
    if body.password.lower() in (username.lower(), netid.lower()):
        raise HTTPException(400, PASSWORD_SAME)
    if not hmac.compare_digest(body.password.encode(), body.password2.encode()):
        raise HTTPException(400, PASSWORD_MISMATCH)
    u = await find_by_netid(session, netid)
    if u is not None and (u.role != "student" or is_set_up(u)):
        raise HTTPException(400, GENERIC)
    taken = (await session.execute(select(User.id).where(User.username == username))).first() \
        or (await session.execute(select(ClassLogin.id).where(ClassLogin.username == username))).first()
    if taken and not (u is not None and taken[0] == u.id):
        raise HTTPException(409, TAKEN)
    pw_hash = await asyncio.get_running_loop().run_in_executor(
        hash_executor(), hash_password, body.password, s.pbkdf2_iterations)
    now = utcnow()
    if u is None:
        u = User(username=username, netid=netid, name=name, role="student", password_hash=pw_hash,
                 created_at=now, set_up_at=now)
        session.add(u)
    else:
        u.username, u.password_hash, u.set_up_at = username, pw_hash, now
        if name:
            u.name = name
    await session.execute(update(ClassLogin).where(ClassLogin.id == cl.id).values(used=ClassLogin.used + 1))
    await add_event(session, cl.class_id, "setup_ok", login_id=cl.id, netid=netid, detail=username)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, TAKEN) from None
    from ...security import remember_user
    await remember_user(u.id, u.role, int(u.token_version or 0))
    ttl = s.student_token_ttl_s
    return JSONResponse({"token": make_token(s.secret, u.id, u.role, ttl, version=u.token_version or 0),
                         "role": u.role, "name": u.name, "username": u.username}, headers=NO_STORE)


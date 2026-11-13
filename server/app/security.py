"""Password hashing (PBKDF2-SHA256) and stateless HMAC-signed tokens."""
from __future__ import annotations

import asyncio
import re
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, HTTPException, Request

from .config import Settings


def hash_password(password: str, iterations: int, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(it))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# perf 0.6.2 (5,000-student test): PBKDF2 runs in threads of their own at a lower CPU
# priority. In the default pool, with every student of a school signing in at once, the
# hash threads took most of the CPU from the event loops: heartbeats and answer saves of
# students already writing waited for seconds, and requests failed on pool timeouts. Now
# the exam keeps the CPU it needs and sign-ins use the rest (they queue instead).
HASH_THREAD_NICE = 10
_hash_pool: ThreadPoolExecutor | None = None
_hash_pool_pid: int | None = None


def _lower_thread_priority() -> None:
    try:   # Linux: a thread is its own scheduling entity; raising its nice needs no rights
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), HASH_THREAD_NICE)
    except (AttributeError, OSError, ValueError):
        pass      # other systems: normal priority (the slot limit still applies)


def hash_executor(threads: int = 4) -> ThreadPoolExecutor:
    global _hash_pool, _hash_pool_pid
    if _hash_pool is None or _hash_pool_pid != os.getpid():
        _hash_pool = ThreadPoolExecutor(max_workers=max(1, threads), thread_name_prefix="pbkdf2",
                                        initializer=_lower_thread_priority)
        _hash_pool_pid = os.getpid()
    return _hash_pool


async def verify_password_async(password: str, stored: str, threads: int = 4) -> bool:
    # PBKDF2 is CPU-bound; keep it off the event loop (and below it in priority).
    return await asyncio.get_running_loop().run_in_executor(
        hash_executor(threads), verify_password, password, stored)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def key_id(secret: str) -> str:
    """Audit S-15: which secret signed a token (8 hex chars; reveals nothing)."""
    return hashlib.sha256(b"fairplay-kid|" + secret.encode()).hexdigest()[:8]


def make_token(secret: str, user_id: int, role: str, ttl_s: int, now: float | None = None,
               version: int = 0, scope: str = "", login_at: int | None = None,
               exp: int | None = None, remembered: bool = False) -> str:
    """payload = "<uid>:<role>:<exp>:<token_version>:<key id>[:<scope>]" (audit S-15). The only
    scopes are HOMEWORK_SCOPE and ADMIN_SCOPE; current_principal refuses any other."""
    exp = int(exp) if exp is not None else int((now or time.time()) + ttl_s)
    tail = f":{scope}" if scope else ""
    if scope == HOMEWORK_SCOPE and login_at is not None:
        tail += f":{int(login_at)}"
        if remembered:
            tail += f":{HOMEWORK_REMEMBERED}"
    payload = _b64(f"{user_id}:{role}:{exp}:{int(version)}:{key_id(secret)}{tail}".encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def parse_token(secret: str, token: str) -> tuple[int, str, int] | None:
    """(user id, role, token version) or None. Tokens from before S-15 (no version /
    key id) are refused: their holders sign in once more."""
    full = parse_scoped_token(secret, token)
    return full[:3] if full else None


def parse_scoped_token(secret: str, token: str) -> tuple[int, str, int, str] | None:
    """Like parse_token plus the token's scope ("" = not scoped)."""
    try:
        payload, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        uid, role, exp, ver, kid, *rest = _unb64(payload).decode().split(":")
        if int(exp) < time.time() or role not in ("teacher", "student") or kid != key_id(secret) or len(rest) > 3:
            return None
        if len(rest) >= 2 and (rest[0] != HOMEWORK_SCOPE or not rest[1].isdigit()):
            return None
        if len(rest) == 3 and rest[2] != HOMEWORK_REMEMBERED:
            return None
        return int(uid), role, int(ver), (rest[0] if rest else "")
    except (ValueError, UnicodeDecodeError):
        return None


def homework_times(secret: str, token: str) -> tuple[int, int] | None:
    """(exp, login_at) of a valid homework token; None for any other token. A homework token
    from before login_at existed counts as signed in 8 hours before its expiry."""
    if (parse_scoped_token(secret, token) or (0, "", 0, ""))[3] != HOMEWORK_SCOPE:
        return None
    parts = _unb64(token.split(".")[0]).decode().split(":")
    exp = int(parts[2])
    return exp, (int(parts[6]) if len(parts) > 6 else exp - LEGACY_HOMEWORK_TTL_S)


def homework_remembered(secret: str, token: str) -> bool:
    """True for a valid homework token that came from a remember-me exchange."""
    if (parse_scoped_token(secret, token) or (0, "", 0, ""))[3] != HOMEWORK_SCOPE:
        return False
    return _unb64(token.split(".")[0]).decode().split(":")[-1] == HOMEWORK_REMEMBERED


# perf 0.6.2: 30 -> 300 s. Every request checks the account's token version; with 5,000
# students a 30 s cache meant ~170 database reads a second for nothing. Signing out
# everywhere and class removal still take effect at once (they delete the entry,
# bump_token_version); only a change made directly in the database waits up to 5 minutes.
USER_CACHE_TTL_S = 300


async def _current_user(uid: int) -> tuple[str, int] | None:
    """(role, token_version) of a user, cached briefly in the store (audit S-15)."""
    from .db import get_database
    from .models import User
    from .store import get_store
    from sqlalchemy import select
    store = get_store()
    raw = await store.get(f"tv:{uid}")
    if raw:
        role, _, ver = raw.partition(":")
        return role, int(ver)
    async with get_database().sessionmaker() as s:
        row = (await s.execute(select(User.role, User.token_version, User.username).where(User.id == uid))).first()
    if row is None:
        return None
    role = "student" if row.username == ADMIN_TESTER_USER else row.role
    # 0.6.2 review: only fill an EMPTY entry. A sign-out that ran after the read above has
    # already written the new version (remember_user); writing back the old one would let a
    # signed-out token keep working for USER_CACHE_TTL_S.
    if not await store.setnx(f"tv:{uid}", f"{role}:{int(row.token_version or 0)}", USER_CACHE_TTL_S):
        raw = await store.get(f"tv:{uid}")
        if raw:
            role, _, ver = raw.partition(":")
            return role, int(ver)
    return role, int(row.token_version or 0)


async def remember_user(uid: int, role: str, token_version: int) -> None:
    """After a token-version change: put the NEW version in the cache (overwrites), so a
    concurrent cache fill can't bring the old one back (0.6.2 review)."""
    from .store import get_store
    await get_store().set(f"tv:{uid}", f"{role}:{int(token_version)}", USER_CACHE_TTL_S)


async def forget_user_cache(uid: int) -> None:
    from .store import get_store
    await get_store().delete(f"tv:{uid}")


class Principal:
    __slots__ = ("user_id", "role", "scope")

    def __init__(self, user_id: int, role: str, scope: str = ""):
        self.user_id = user_id
        self.role = role
        self.scope = scope


async def current_principal(request: Request) -> Principal:
    settings: Settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Not authenticated")
    parsed = parse_scoped_token(settings.secret, token.strip())
    if not parsed:
        raise HTTPException(401, "Invalid or expired token")
    uid, role, ver, scope = parsed
    # 2026-11: the exam-scoped (access-code) and sealed-scoped tokens are gone. Nothing issues them,
    # and one still held from an older build is refused here instead of becoming a full sign-in.
    if scope and scope not in (ADMIN_SCOPE, HOMEWORK_SCOPE):
        raise HTTPException(401, "Signed out. Please sign in again.")
    # Audit S-15: signed out everywhere (version bumped), deleted, or role changed -> refused.
    cur = await _current_user(uid)
    if cur is None or cur[0] != role or cur[1] != ver:
        raise HTTPException(401, "Signed out. Please sign in again.")
    return Principal(uid, role, scope)


# perf 0.6.2: async (they never block). A plain `def` dependency runs in FastAPI's thread
# pool: a thread hop on every authenticated request for a two-line check.
async def require_teacher(p: Principal = Depends(current_principal)) -> Principal:
    if p.role != "teacher":
        raise HTTPException(403, "Teacher role required")
    return p


ADMIN_SCOPE = "admin_test"
HOMEWORK_SCOPE = "homework"
HOMEWORK_REMEMBERED = "rm"
HOMEWORK_IDLE_S = 30 * 60
HOMEWORK_ABSOLUTE_S = 4 * 3600
LEGACY_HOMEWORK_TTL_S = 8 * 3600


def homework_expiry(login_at: int, now: float | None = None) -> tuple[int, int]:
    """(idle expiry, absolute expiry) as epoch seconds; the idle one never passes the absolute one."""
    absolute = int(login_at) + HOMEWORK_ABSOLUTE_S
    return min(int((now or time.time()) + HOMEWORK_IDLE_S), absolute), absolute
ADMIN_TESTER_USER = "admin-tester"
_ADMIN_PATHS = ("/api/student/exams",)


async def require_student(request: Request, p: Principal = Depends(current_principal)) -> Principal:
    if p.role != "student":
        raise HTTPException(403, "Student role required")
    if p.scope == HOMEWORK_SCOPE:
        raise HTTPException(403, "This sign-in is for homework only.")
    if p.scope == ADMIN_SCOPE:
        if request.method != "GET" or request.url.path.rstrip("/") not in _ADMIN_PATHS:
            raise HTTPException(403, "This preview sign-in can only list the exams.")
    return p


async def require_homework(p: Principal = Depends(current_principal)) -> Principal:
    """A homework sign-in, or a plain student sign-in (no exam scope)."""
    if p.role != "student" or p.scope not in ("", HOMEWORK_SCOPE):
        raise HTTPException(403, "Homework sign-in required")
    return p

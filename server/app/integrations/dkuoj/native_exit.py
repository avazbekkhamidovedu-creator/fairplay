"""Code-only exit for a Mac locked for a DKU OJ exam (docs/native-exit-vectors.json).

The Mac escrows a per-lock key K, sealed to the server's X25519 public key, before it locks
(POST /api/native-exit/locks). Later the professor asks, signed from DKU OJ, for the 6-digit
exit code of that lock. The blob is opened only then, with the private key from
FAIRPLAY_NATIVE_EXIT_PRIVATE_KEY. K, the blob and the code are never logged or returned
by the app route.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import time
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import APIRouter, Depends, HTTPException, Request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Integer, LargeBinary, String, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ... import app_client as ac
from ...db import Base, get_session
from ...routes_auth import client_ip
from ...store import get_store
from ...timeutil import utcnow
from tools.native_exit_vector import ALPHABET, derive_code, open_blob, seal
from .models import BigIntPK, OjRosterEntry
from .routes_rescue import HOUR_S, NO_STORE, _limit, _out, _reason
from .routes_v2 import PREFIX, BridgeCall, _editor_exam, _event_class, signed_call
from .setup import add_event

log = logging.getLogger("proctora.dkuoj")

router = APIRouter(tags=["dkuoj-native-exit"])

ENV_KEY = "FAIRPLAY_NATIVE_EXIT_PRIVATE_KEY"
SIGNING_ENV_KEY = "FAIRPLAY_NATIVE_LOCK_SIGNING_KEY"
VERIFIER_PREFIX = "fairplay-native-verifier|v1|"
ITERATION_FLOOR = 600_000
ITERATION_CEILING = 20_000_000
BLOB_BYTES = 92
LOCK_TTL = timedelta(days=7)


def env_cap(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


ISSUES_PER_LOCK = 3
LOCKS_PER_DEVICE_HOUR = 20
LOCKS_PER_IP_HOUR = env_cap("FAIRPLAY_NATIVE_EXIT_LOCKS_PER_IP_HOUR", 2000)
LOCKS_PER_HOUR_ALL = env_cap("FAIRPLAY_NATIVE_EXIT_LOCKS_PER_HOUR_ALL", 20000)
LOCKS_PER_SCHOOL_IP_HOUR = env_cap("FAIRPLAY_NATIVE_EXIT_LOCKS_PER_SCHOOL_IP_HOUR", 5000)
LOCKS_IP_SHARE_PERCENT = min(env_cap("FAIRPLAY_NATIVE_EXIT_IP_SHARE_PERCENT", 50), 100)
ISSUES_PER_STUDENT_HOUR = 20
ISSUES_PER_EXAM_HOUR = env_cap("FAIRPLAY_NATIVE_EXIT_ISSUES_PER_EXAM_HOUR", 300)
MAX_BODY = 4096
NOT_CONFIGURED = "Code-only exit is not set up on this server."
LOCK_ID_RE = re.compile("^[" + ALPHABET + "]{8}$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


class NativeExitLock(Base):
    __tablename__ = "native_exit_locks"
    lock_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    device: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    blob: Mapped[bytes] = mapped_column(LargeBinary(BLOB_BYTES), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)
    exam_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exam_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    netid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_issue_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verifier: Mapped[str | None] = mapped_column(String(400), nullable=True)


def _private_key() -> bytes:
    raw = os.environ.get(ENV_KEY, "").strip()
    try:
        key = base64.b64decode(raw, validate=True) if raw else b""
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise HTTPException(503, NOT_CONFIGURED, headers=NO_STORE)
    return key


def _signing_key() -> Ed25519PrivateKey:
    raw = os.environ.get(SIGNING_ENV_KEY, "").strip()
    try:
        key = base64.b64decode(raw, validate=True) if raw else b""
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise HTTPException(503, NOT_CONFIGURED, headers=NO_STORE)
    return Ed25519PrivateKey.from_private_bytes(key)


class LockIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lock_id: str = Field(min_length=8, max_length=8)
    blob: str | None = Field(default=None, min_length=1, max_length=256)
    server_secret: bool = False
    device: str = Field(min_length=8, max_length=128)
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    verifier_salt: str | None = Field(default=None, max_length=32)
    verifier_iterations: int | None = None
    verifier_hash: str | None = Field(default=None, max_length=64)


def verifier_message_v2(lock_id: str, device: str, created: int, salt: str, iterations: int, hash_: str) -> bytes:
    return f"fairplay-native-verifier|v2|{lock_id}|{device}|{created}|{salt}|{iterations}|{hash_}".encode("utf-8")


VERIFIER_INFO = b"fairplay-native-exit-verifier|v1|"
V2_ITERATIONS = ITERATION_FLOOR


def _make_verifier(code: str, lock_id: str) -> tuple[str, str]:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), VERIFIER_INFO + lock_id.encode("utf-8") + salt, V2_ITERATIONS, 32)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


def _v2_reply(stored: str) -> JSONResponse:
    _, created, salt, iterations, hash_, sig = stored.split("|")
    return JSONResponse({"ok": True, "verifier_created": int(created), "verifier_salt": salt,
                         "verifier_iterations": int(iterations), "verifier_hash": hash_,
                         "verifier_signature": sig}, status_code=201, headers=NO_STORE)


_inflight: dict[tuple[str, str], asyncio.Task] = {}
_gate: dict = {}
_waiting = 0
WAITING_PER_CORE = 3


def _cores() -> int:
    return os.cpu_count() or 1


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if _gate.get("loop") is not loop or _gate.get("cores") != _cores():
        _gate.update(loop=loop, cores=_cores(), sem=asyncio.Semaphore(_cores()))
    return _gate["sem"]


def _build_verifier(lock_id: str, device: str, priv: bytes, signer: Ed25519PrivateKey) -> tuple[bytes, str]:
    secret = os.urandom(32)
    public = X25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    blob = base64.b64decode(seal(secret, lock_id, public))
    salt, hash_ = _make_verifier(derive_code(secret, lock_id), lock_id)
    created = int(time.time())
    sig = base64.b64encode(signer.sign(verifier_message_v2(lock_id, device, created, salt, V2_ITERATIONS, hash_))).decode("ascii")
    return blob, "|".join(["v2", str(created), salt, str(V2_ITERATIONS), hash_, sig])


async def _compute(lock_id: str, device: str, priv: bytes, signer: Ed25519PrivateKey, admit) -> tuple[bytes, str]:
    global _waiting
    await admit()
    _waiting += 1
    try:
        sem = _semaphore()
        await sem.acquire()
    finally:
        _waiting -= 1
    try:
        return await asyncio.to_thread(_build_verifier, lock_id, device, priv, signer)
    finally:
        sem.release()


async def _register_server_secret(body: "LockIn", request: Request, session: AsyncSession) -> JSONResponse:
    if body.blob is not None or any(v is not None for v in (body.verifier_salt, body.verifier_iterations, body.verifier_hash)):
        raise HTTPException(422, "Bad request", headers=NO_STORE)
    priv, signer = _private_key(), _signing_key()

    async def existing():
        row = (await session.execute(select(NativeExitLock).where(NativeExitLock.lock_id == body.lock_id))).scalar_one_or_none()
        if row is None:
            return None
        if row.device != body.device or row.verifier is None or not row.verifier.startswith("v2|"):
            raise HTTPException(409, "This lock is already registered", headers=NO_STORE)
        return _v2_reply(row.verifier)

    found = await existing()
    if found is not None:
        return found
    key = (body.lock_id, body.device)
    task = _inflight.get(key)
    if task is None:
        if _waiting >= WAITING_PER_CORE * _cores():
            raise HTTPException(503, "Busy. Try again shortly.", headers={**NO_STORE, "Retry-After": "2"})
        task = asyncio.ensure_future(_compute(body.lock_id, body.device, priv, signer,
                                              lambda: _limit_registrations(request, body)))
        _inflight[key] = task

        def done(t: asyncio.Task) -> None:
            _inflight.pop(key, None)
            if not t.cancelled():
                t.exception()
        task.add_done_callback(done)
    blob, stored = await asyncio.shield(task)
    session.add(NativeExitLock(lock_id=body.lock_id, device=body.device, blob=blob, created_at=utcnow(), verifier=stored))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        found = await existing()
        if found is None:
            raise HTTPException(409, "This lock is already registered", headers=NO_STORE) from None
        return found
    return _v2_reply(stored)


def lock_message(lock_id: str, device: str) -> str:
    return f"nativelock|{lock_id}|{device}"


async def _limit_registrations(request: Request, body: "LockIn") -> None:
    store = get_store()
    ip = client_ip(request)
    school = request.app.state.settings.in_school_net(ip)
    ip_cap = min(LOCKS_PER_SCHOOL_IP_HOUR if school else LOCKS_PER_IP_HOUR,
                 max(1, LOCKS_PER_HOUR_ALL * LOCKS_IP_SHARE_PERCENT // 100))
    if await store.incr(f"nexit:dev:{body.device}", HOUR_S) > LOCKS_PER_DEVICE_HOUR \
            or await store.incr(f"nexit:ip:{ip}", HOUR_S) > ip_cap:
        raise HTTPException(429, "Too many locks from this computer. Try again later.",
                            headers={**NO_STORE, "Retry-After": "3600"})
    if await store.incr("nexit:all", HOUR_S) > LOCKS_PER_HOUR_ALL:
        raise HTTPException(429, "Too many locks right now. Try again later.",
                            headers={**NO_STORE, "Retry-After": "3600"})


@router.post("/api/native-exit/locks", status_code=201)
async def register_lock(body: LockIn, request: Request, session: AsyncSession = Depends(get_session)):
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY:
        raise HTTPException(413, "Request body too large", headers=NO_STORE)
    if not ac.verify(request.app.state.settings, lock_message(body.lock_id, body.device), body.signature):
        raise HTTPException(403, ac.APP_PROOF_DETAIL, headers=NO_STORE)
    _private_key()
    if not LOCK_ID_RE.match(body.lock_id) or not DEVICE_RE.match(body.device):
        raise HTTPException(422, "Bad lock_id or device", headers=NO_STORE)
    if body.server_secret:
        return await _register_server_secret(body, request, session)
    if body.blob is None:
        raise HTTPException(422, "Bad blob", headers=NO_STORE)
    try:
        raw = base64.b64decode(body.blob, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Bad blob", headers=NO_STORE) from None
    if len(raw) != BLOB_BYTES:
        raise HTTPException(422, "Bad blob", headers=NO_STORE)
    try:
        open_blob(body.blob, body.lock_id, _private_key())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Bad blob", headers=NO_STORE) from None
    if any(v is not None for v in (body.verifier_salt, body.verifier_iterations, body.verifier_hash)):
        raise HTTPException(422, "Bad request", headers=NO_STORE)
    stored = None
    prior = (await session.execute(select(NativeExitLock.device).where(NativeExitLock.lock_id == body.lock_id))).scalar_one_or_none()
    if prior != body.device:
        await _limit_registrations(request, body)
    session.add(NativeExitLock(lock_id=body.lock_id, device=body.device, blob=raw, created_at=utcnow(), verifier=stored))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        row = (await session.execute(select(NativeExitLock).where(NativeExitLock.lock_id == body.lock_id))).scalar_one_or_none()
        if row is None or row.device != body.device or not hmac.compare_digest(bytes(row.blob), raw):
            raise HTTPException(409, "This lock is already registered", headers=NO_STORE) from None
    return JSONResponse({"ok": True}, status_code=201, headers=NO_STORE)


@router.post(PREFIX + "/exams/{key}/students/{netid}/native-exit")
async def native_exit(key: str, netid: str, call: BridgeCall = Depends(signed_call),
                      session: AsyncSession = Depends(get_session)):
    e = await _editor_exam(session, key, call)
    priv = _private_key()
    reason = _reason(call.data)
    lock_id = ac.normalize_mac_code(call.data.get("mac_code") if isinstance(call.data.get("mac_code"), str) else "")
    if lock_id is None:
        raise HTTPException(422, "mac_code is not a Mac code", headers=NO_STORE)
    who = netid.strip().lower()[:64]
    if not who:
        raise HTTPException(400, "netid is required")
    row = (await session.execute(select(NativeExitLock).where(NativeExitLock.lock_id == lock_id))).scalar_one_or_none()
    now = utcnow()
    if row is None or now - row.created_at > LOCK_TTL:
        raise HTTPException(404, "Unknown or expired Mac code", headers=NO_STORE)
    if row.netid is not None and (row.netid != who or row.exam_id != e.id):
        raise HTTPException(409, "This Mac code belongs to another student or exam", headers=NO_STORE)
    if row.issued_count >= ISSUES_PER_LOCK:
        raise HTTPException(429, "This Mac code already got its exit codes", headers=NO_STORE)
    await _limit(f"oj:nexit:{e.id}:{who}", ISSUES_PER_STUDENT_HOUR, HOUR_S)
    listed = (await session.execute(select(func.count()).select_from(OjRosterEntry).where(
        OjRosterEntry.exam_id == e.id, OjRosterEntry.revoked_at.is_(None)))).scalar_one()
    await _limit(f"oj:nexit:exam:{e.id}", max(ISSUES_PER_EXAM_HOUR, listed), HOUR_S)
    try:
        k = open_blob(base64.b64encode(row.blob).decode("ascii"), lock_id, priv)
    except Exception:
        log.warning("native exit: lock %s could not be opened", lock_id)
        raise HTTPException(422, "This Mac code cannot be opened", headers=NO_STORE) from None
    code = derive_code(k, lock_id)

    claimed = await session.execute(
        update(NativeExitLock)
        .where(NativeExitLock.lock_id == lock_id, NativeExitLock.issued_count < ISSUES_PER_LOCK,
               (NativeExitLock.netid.is_(None)) | ((NativeExitLock.netid == who) & (NativeExitLock.exam_id == e.id)))
        .values(netid=who, exam_id=e.id, exam_key=e.external_key, issued_count=NativeExitLock.issued_count + 1,
                last_issue_at=now))
    if not claimed.rowcount:
        await session.rollback()
        raise HTTPException(409, "This Mac code was just used", headers=NO_STORE)
    class_id = _event_class(e)
    await add_event(session, class_id if class_id is not None else 0, "native_exit_code_issued", netid=who,
                    actor=call.acting_user,
                    detail=f"exam {e.external_key}: exit code given for Mac code {lock_id[:4]}-{lock_id[4:]}: {reason}")
    await session.commit()
    log.info("DKU OJ exam %s: native exit code issued by %s", key, call.acting_user)
    return _out({"code": code})

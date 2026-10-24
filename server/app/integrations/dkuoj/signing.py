"""Signed server-to-server calls between DKU OJ and FairPlay (docs/dkuoj-bridge-v2.md §1).

One shared token, one key per direction (a captured request can't be replayed the other
way). Every request carries X-FP-Timestamp, X-FP-Nonce, X-FP-Signature and, from DKU OJ,
optionally X-FP-Acting-User. The signature is hex HMAC-SHA256(direction key, canonical):

    v2
    <METHOD>
    <path with query string, exactly as sent>
    <timestamp>
    <nonce>
    <acting user or empty>
    <hex sha256 of the raw body>
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

OJ_TO_FP = "oj->fp"          # DKU OJ signs, FairPlay checks
FP_TO_OJ = "fp->oj"          # FairPlay signs, DKU OJ checks
MAX_SKEW_S = 60
NONCE_TTL_S = 180            # > the whole window a timestamp is accepted in (2 x MAX_SKEW_S), with margin
TS_RE = re.compile(r"[0-9]{1,12}")       # ASCII digits only (str.isdigit() also takes "²")
NONCE_RE = re.compile(r"[0-9a-f]{32}")
SIG_RE = re.compile(r"[0-9a-f]{64}")
MAX_ACTING_USER = 150

H_TIMESTAMP = "X-FP-Timestamp"
H_NONCE = "X-FP-Nonce"
H_ACTING = "X-FP-Acting-User"
H_SIGNATURE = "X-FP-Signature"


def direction_key(token: str, direction: str) -> bytes:
    if direction not in (OJ_TO_FP, FP_TO_OJ):
        raise ValueError("unknown direction")
    return hmac.new(token.encode(), f"fairplay-bridge v2 {direction}".encode(), hashlib.sha256).digest()


def body_sha256(body: bytes | str | None) -> str:
    if body is None:
        body = b""
    if isinstance(body, str):
        body = body.encode()
    return hashlib.sha256(body).hexdigest()


def canonical(method: str, path: str, timestamp: int | str, nonce: str, acting_user: str | None,
              body: bytes | str | None) -> str:
    return "\n".join(("v2", method.upper(), path, str(timestamp), nonce, acting_user or "",
                      body_sha256(body)))


def signature(token: str, direction: str, method: str, path: str, timestamp: int | str, nonce: str,
              acting_user: str | None, body: bytes | str | None) -> str:
    msg = canonical(method, path, timestamp, nonce, acting_user, body)
    return hmac.new(direction_key(token, direction), msg.encode(), hashlib.sha256).hexdigest()


def sign_headers(token: str, direction: str, method: str, path: str, body: bytes | None,
                 acting_user: str | None = None, now: float | None = None,
                 nonce: str | None = None) -> dict[str, str]:
    """The headers of one signed request (a fresh nonce each call)."""
    ts = int(now if now is not None else time.time())
    nonce = nonce or secrets.token_hex(16)
    h = {H_TIMESTAMP: str(ts), H_NONCE: nonce,
         H_SIGNATURE: signature(token, direction, method, path, ts, nonce, acting_user, body)}
    if acting_user:
        h[H_ACTING] = acting_user
    return h


class BadSignature(Exception):
    pass


async def verify(token: str | list[str] | tuple[str, ...], direction: str, method: str, path: str, headers,
                 body: bytes, store=None, now: float | None = None) -> str:
    """Check a signed request; returns the acting user ("" when none). Raises BadSignature.
    `token` may be several tokens (rotation, docs/dkuoj-bridge-v2.md §1): a signature made
    with any of them is accepted (every one is compared, so the time taken doesn't tell
    which). `headers` is a case-insensitive mapping. The nonce is remembered (store.setnx)
    only after the signature is right, so nobody can burn another's nonces."""
    tokens = [token] if isinstance(token, str) else [t for t in token if t]
    if not tokens:
        raise BadSignature()
    ts_raw = (headers.get(H_TIMESTAMP) or "").strip()
    nonce = (headers.get(H_NONCE) or "").strip()
    sig = (headers.get(H_SIGNATURE) or "").strip().lower()
    acting = headers.get(H_ACTING) or ""
    if not TS_RE.fullmatch(ts_raw) or not NONCE_RE.fullmatch(nonce) or not SIG_RE.fullmatch(sig):
        raise BadSignature()
    if len(acting) > MAX_ACTING_USER or any(ord(ch) < 32 or ord(ch) == 127 for ch in acting):
        raise BadSignature()
    ts = int(ts_raw)
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_S:
        raise BadSignature()
    good = False
    for t in tokens:
        expected = signature(t, direction, method, path, ts_raw, nonce, acting, body)
        good = hmac.compare_digest(expected, sig) or good
    if not good:
        raise BadSignature()
    if store is not None and not await store.setnx(f"fpb:nonce:{nonce}", "1", NONCE_TTL_S):
        raise BadSignature()
    return acting

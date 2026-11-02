import hashlib
import hmac
import re
import time

TS_WINDOW_S = 60
NONCE_TTL_S = 300
NONCE_MAX_ENTRIES = 200_000
_NONCE_RE = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")
_SIG_RE = re.compile(r"^[0-9a-f]{64}$")


def sign(secret: str, ts: int | str, nonce: str, body: bytes) -> str:
    msg = f"{ts}.{nonce}.{hashlib.sha256(body).hexdigest()}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class NonceStore:
    def __init__(self, ttl: int = NONCE_TTL_S, max_entries: int = NONCE_MAX_ENTRIES, clock=time.monotonic):
        self._ttl = ttl
        self._max = max_entries
        self._clock = clock
        self._seen: dict[tuple[str, str], float] = {}
        self._last_prune = clock()

    def _prune(self, now: float) -> None:
        if now - self._last_prune < 5 and len(self._seen) < self._max:
            return
        self._last_prune = now
        self._seen = {k: exp for k, exp in self._seen.items() if exp > now}

    def use(self, tenant: str, nonce: str) -> bool:
        now = self._clock()
        self._prune(now)
        key = (tenant, nonce)
        if key in self._seen:
            return False
        if len(self._seen) >= self._max:
            return False
        self._seen[key] = now + self._ttl
        return True


class Authenticator:
    def __init__(self, tenants: dict[str, str], nonces: NonceStore | None = None, now=time.time):
        self._tenants = tenants
        self._nonces = nonces or NonceStore()
        self._now = now

    def verify(self, headers, body: bytes) -> str | None:
        tenant = headers.get("x-fp-tenant", "")
        ts = headers.get("x-fp-ts", "")
        nonce = headers.get("x-fp-nonce", "")
        sig = headers.get("x-fp-sig", "")
        secret = self._tenants.get(tenant)
        dummy = secret is None
        if dummy:
            secret = "x" * 32
        ok = not dummy
        if not _NONCE_RE.match(nonce) or not _SIG_RE.match(sig.lower()):
            ok = False
        if ts.isascii() and ts.isdigit() and len(ts) <= 12:
            if abs(self._now() - int(ts)) > TS_WINDOW_S:
                ok = False
        else:
            ts = "0"
            ok = False
        expected = sign(secret, ts, nonce, body)
        if not hmac.compare_digest(expected, sig.lower()) or not ok:
            return None
        if not self._nonces.use(tenant, nonce):
            return None
        return tenant

"""HMAC signing of the FairPlay bridge calls; the format is in docs/fairplay-bridge.md."""
import hashlib
import hmac
import re
import secrets
import time

from django.core.cache import cache

from judge.utils.fairplay import fairplay_token, fairplay_tokens

__all__ = [
    'DIRECTION_FP_TO_OJ', 'DIRECTION_OJ_TO_FP', 'MAX_SKEW_SECONDS', 'NONCE_TTL_SECONDS', 'SignatureError',
    'body_sha256', 'canonical_string', 'direction_key', 'sign', 'signed_headers', 'verify_signature',
]

DIRECTION_OJ_TO_FP = 'oj->fp'
DIRECTION_FP_TO_OJ = 'fp->oj'
MAX_SKEW_SECONDS = 60
NONCE_TTL_SECONDS = 180   # > the whole window a timestamp is accepted in (2 x MAX_SKEW_SECONDS)
NONCE_CACHE_PREFIX = 'fpb:nonce:'

_TIMESTAMP = re.compile(r'^[0-9]{1,12}$')
_NONCE = re.compile(r'^[0-9a-f]{32}$')
_SIGNATURE = re.compile(r'^[0-9a-fA-F]{64}$')


class SignatureError(Exception):
    """A request whose signature headers are missing, stale, wrong or replayed (answered 401)."""


def direction_key(direction, token=None):
    """Each direction has its own key, so a captured request can't be replayed the other way."""
    if direction not in (DIRECTION_OJ_TO_FP, DIRECTION_FP_TO_OJ):
        raise ValueError('Unknown direction %r' % direction)
    token = token if token is not None else fairplay_token()
    label = 'fairplay-bridge v2 %s' % direction
    return hmac.new(token.encode('utf-8'), label.encode('utf-8'), hashlib.sha256).digest()


def body_sha256(body):
    """Lowercase hex sha256 of the raw body (``b''`` for no body)."""
    if isinstance(body, str):
        body = body.encode('utf-8')
    return hashlib.sha256(body or b'').hexdigest()


def canonical_string(method, path, timestamp, nonce, acting_user, body_hash):
    return '\n'.join(('v2', method.upper(), path, str(timestamp), nonce, acting_user or '', body_hash))


def sign(key, canonical):
    return hmac.new(key, canonical.encode('utf-8'), hashlib.sha256).hexdigest()


def signed_headers(method, path, body=b'', acting_user=None, timestamp=None, nonce=None, token=None,
                   direction=DIRECTION_OJ_TO_FP):
    """Headers for an outgoing call. ``path`` is exactly what is sent, query string included."""
    timestamp = int(timestamp if timestamp is not None else time.time())
    nonce = nonce or secrets.token_hex(16)
    canonical = canonical_string(method, path, timestamp, nonce, acting_user, body_sha256(body))
    headers = {
        'X-FP-Timestamp': str(timestamp),
        'X-FP-Nonce': nonce,
        'X-FP-Signature': sign(direction_key(direction, token), canonical),
    }
    if acting_user:
        headers['X-FP-Acting-User'] = acting_user
    return headers


def parse_signature_headers(request, now=None):
    """Header and timestamp checks, done before the body is read."""
    meta = request.META
    timestamp = meta.get('HTTP_X_FP_TIMESTAMP', '')
    nonce = meta.get('HTTP_X_FP_NONCE', '')
    signature = meta.get('HTTP_X_FP_SIGNATURE', '')
    acting_user = meta.get('HTTP_X_FP_ACTING_USER', '')
    if not (_TIMESTAMP.fullmatch(timestamp) and _NONCE.fullmatch(nonce) and _SIGNATURE.fullmatch(signature)):
        raise SignatureError('missing or malformed signature headers')
    now = int(now if now is not None else time.time())
    if abs(now - int(timestamp)) > MAX_SKEW_SECONDS:
        raise SignatureError('timestamp outside the allowed window')
    return int(timestamp), nonce, acting_user, signature.lower()


def verify_signature(request, body, parsed):
    """Constant-time signature check, then the replay check."""
    timestamp, nonce, acting_user, signature = parsed
    canonical = canonical_string(request.method, request.get_full_path(), timestamp, nonce, acting_user,
                                 body_sha256(body))
    # Every token is compared, in constant time.
    matched = False
    for token in fairplay_tokens():
        expected = sign(direction_key(DIRECTION_FP_TO_OJ, token), canonical)
        matched |= hmac.compare_digest(expected.encode('ascii'), signature.encode('ascii'))
    if not matched:
        raise SignatureError('signature mismatch')
    # The cache must be shared by every worker; judge.checks refuses a per-process cache.
    if not cache.add(NONCE_CACHE_PREFIX + nonce, 1, NONCE_TTL_SECONDS):
        raise SignatureError('nonce already used')

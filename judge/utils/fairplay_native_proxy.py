"""Request-bound identity assertions from a dedicated mTLS proxy.

This is a protocol primitive, not middleware or an authorization decision.
The proxy must validate the client certificate, strip client-supplied assertion
headers, and sign the certificate fingerprint itself. Deployments must prevent
direct access to the Django listener and use a shared, atomic replay cache.
"""
import hashlib
import hmac
import json
import re
import time

_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_NONCE = re.compile(r'[0-9a-f]{32}\Z')
_METHOD = re.compile(r'[A-Z]{1,16}\Z')
_DOMAIN = b'fairplay-native-proxy-assertion-v1\0'
MAX_AGE = 10


class ProxyAssertionError(ValueError):
    pass


def _message(method, target, session_digest, fingerprint, timestamp, nonce):
    if (not isinstance(method, str) or not _METHOD.fullmatch(method)
            or not isinstance(target, str) or not target.startswith('/')
            or len(target) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in target)
            or not isinstance(session_digest, str) or not _HEX64.fullmatch(session_digest)
            or not isinstance(fingerprint, str) or not _HEX64.fullmatch(fingerprint)
            or type(timestamp) is not int or timestamp < 0
            or not isinstance(nonce, str) or not _NONCE.fullmatch(nonce)):
        raise ProxyAssertionError('Invalid proxy assertion')
    values = [method, target, session_digest, fingerprint, timestamp, nonce]
    return _DOMAIN + json.dumps(values, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sign_assertion(secret, method, target, session_digest, fingerprint, timestamp, nonce):
    """Sign only after the proxy has verified the TLS client certificate."""
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ProxyAssertionError('Proxy secret unavailable')
    return hmac.new(secret, _message(method, target, session_digest, fingerprint, timestamp, nonce),
                    hashlib.sha256).hexdigest()


def verify_assertion(*, secret, peer, trusted_peers, method, target, session_digest,
                     fingerprint, timestamp, nonce, signature, replay_cache, now=None):
    """Verify an assertion once and return its certificate fingerprint.

    replay_cache.add(key, value, timeout) must be shared and atomic across all
    web workers. The caller must derive peer and session_digest from the actual
    request, not from assertion headers, and select trusted_peers from config.
    """
    current = int(time.time()) if now is None else now
    if (not isinstance(trusted_peers, (set, frozenset)) or not trusted_peers
            or peer not in trusted_peers or type(current) is not int
            or type(timestamp) is not int or not current - MAX_AGE <= timestamp <= current
            or not isinstance(signature, str) or not _HEX64.fullmatch(signature)):
        raise ProxyAssertionError('Untrusted or expired proxy assertion')
    expected = sign_assertion(secret, method, target, session_digest, fingerprint, timestamp, nonce)
    if not hmac.compare_digest(expected, signature):
        raise ProxyAssertionError('Invalid proxy assertion signature')
    try:
        # Nonce namespace is independent of attacker-controlled fingerprint.
        fresh = replay_cache.add('fp-native-proxy-v1:' + nonce, 1, timeout=MAX_AGE + 1)
    except Exception as exc:
        raise ProxyAssertionError('Proxy replay cache unavailable') from exc
    if fresh is not True:
        raise ProxyAssertionError('Proxy assertion replayed')
    return fingerprint


def verified_request_fingerprint(request, *, secret, session_secret, trusted_peers, replay_cache, now=None):
    """Read and verify a Django request after SessionMiddleware.

    The dedicated proxy signs exactly Django's ``get_full_path()`` representation
    and must strip every incoming ``X-FairPlay-TLS-*`` header before adding its
    own. This function does not authenticate the student or authorize an exam.
    """
    from judge.utils.fairplay_native_admission import NativeAdmissionError, session_digest

    try:
        raw_timestamp = request.META.get('HTTP_X_FAIRPLAY_TLS_TIMESTAMP', '')
        if (not isinstance(raw_timestamp, str) or not raw_timestamp.isascii()
                or not raw_timestamp.isdigit() or len(raw_timestamp) > 12
                or (len(raw_timestamp) > 1 and raw_timestamp.startswith('0'))):
            raise ProxyAssertionError('Invalid proxy timestamp')
        digest = session_digest(request.session.session_key, session_secret)
        return verify_assertion(
            secret=secret, peer=request.META.get('REMOTE_ADDR'), trusted_peers=trusted_peers,
            method=request.method, target=request.get_full_path(), session_digest=digest,
            fingerprint=request.META.get('HTTP_X_FAIRPLAY_TLS_FINGERPRINT'),
            timestamp=int(raw_timestamp), nonce=request.META.get('HTTP_X_FAIRPLAY_TLS_NONCE'),
            signature=request.META.get('HTTP_X_FAIRPLAY_TLS_SIGNATURE'), replay_cache=replay_cache, now=now)
    except (AttributeError, NativeAdmissionError) as exc:
        raise ProxyAssertionError('Request identity unavailable') from exc

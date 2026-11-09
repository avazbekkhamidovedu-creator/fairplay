"""Dormant protocol adapter for the native browser pilot.

An authenticated OJ session is still a transferable bearer credential.  Until
request-level device proof is implemented, this adapter must never authorize a
protected request, even when a remote server returns an affirmative response.
Nothing in this module is wired into the existing exam access paths.
"""

import hashlib
import hmac
import re
import time
from urllib.parse import urlsplit
from uuid import UUID


_OPAQUE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
_HEX = re.compile(r'^[0-9a-f]{64}$')
_FIELDS = frozenset(('active', 'installation_id', 'user_id', 'session_digest',
                     'contest_id', 'native_session_id', 'client_cert_sha256', 'expires_at'))


class NativeAdmissionError(Exception):
    pass


def session_digest(session_key, secret):
    """A domain-separated digest; never send the browser's session cookie."""
    if not isinstance(session_key, str) or not session_key or not isinstance(secret, str) or len(secret) < 32:
        raise NativeAdmissionError('Session binding unavailable')
    key = hmac.new(secret.encode(), b'fairplay-native-oj-session-v1', hashlib.sha256).digest()
    return hmac.new(key, session_key.encode(), hashlib.sha256).hexdigest()


def make_request(installation_id, user_id, browser_session_digest, contest_id, native_session_id,
                 client_cert_sha256):
    try:
        canonical_installation = str(UUID(installation_id)) == installation_id
    except (ValueError, TypeError, AttributeError):
        canonical_installation = False
    if (not canonical_installation
            or type(user_id) is not int or user_id <= 0 or type(contest_id) is not int or contest_id <= 0
            or not isinstance(browser_session_digest, str) or not _HEX.fullmatch(browser_session_digest)
            or not isinstance(native_session_id, str) or not _OPAQUE.fullmatch(native_session_id)
            or not isinstance(client_cert_sha256, str) or not _HEX.fullmatch(client_cert_sha256)):
        raise NativeAdmissionError('Invalid admission binding')
    return {'installation_id': installation_id, 'user_id': user_id, 'session_digest': browser_session_digest,
            'contest_id': contest_id, 'native_session_id': native_session_id,
            'client_cert_sha256': client_cert_sha256}


def _validated_status(client, base_url, binding, now=None):
    """Verify all server response fields; caller must authenticate request identity.

    ``client`` is a FairPlayClient; its request method authenticates the
    backchannel and refuses redirects. ``base_url`` must be the actual configured
    client endpoint. Callers must verify that directly before wiring this adapter.
    The contract is intentionally provisional and cannot grant access.
    """
    try:
        parts = urlsplit(base_url)
        if (parts.scheme.lower() != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment):
            raise NativeAdmissionError('HTTPS FairPlay endpoint required')
        # Reject noncanonical input and ensure the binding matches its schema.
        if not isinstance(binding, dict) or binding != make_request(
                binding.get('installation_id'), binding.get('user_id'), binding.get('session_digest'),
                binding.get('contest_id'), binding.get('native_session_id'), binding.get('client_cert_sha256')):
            raise NativeAdmissionError('Invalid admission binding')
        response = client.request('POST', '/native/admission/check', binding)
        if not isinstance(response, dict) or set(response) != _FIELDS:
            raise NativeAdmissionError('Invalid admission response')
        if type(response['active']) is not bool or type(response['expires_at']) is not int:
            raise NativeAdmissionError('Invalid admission response')
        if any(response[key] != value or type(response[key]) is not type(value) for key, value in binding.items()):
            raise NativeAdmissionError('Admission binding mismatch')
        current = int(time.time()) if now is None else now
        if not current < response['expires_at'] <= current + 30:
            raise NativeAdmissionError('Invalid admission expiry')
    except Exception as exc:
        if isinstance(exc, NativeAdmissionError):
            raise
        raise NativeAdmissionError('Admission check unavailable') from exc
    return response['active'] is True


def check_admission(client, base_url, binding, now=None):
    # Legacy entry point remains deny-only, even after a valid status.
    _validated_status(client, base_url, binding, now=now)
    return False


def validated_test_status(client, base_url, binding, now=None):
    """Internal DEV pilot helper; does not verify the incoming request itself."""
    from django.conf import settings
    if not settings.DEBUG or getattr(settings, 'FAIRPLAY_NATIVE_ADMISSION_TEST', False) is not True:
        return False
    return _validated_status(client, base_url, binding, now=now)

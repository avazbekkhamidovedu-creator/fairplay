"""Identity from a private, authenticated nginx mTLS ingress.

The public HTTP virtual host must erase these headers. Only the protected TLS
virtual host may add them, after verifying the certificate against the native CA.
The Django listener must be private; a configured ingress key is not an app key.
"""
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import unquote
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID


class IngressRefused(ValueError):
    """`code` is a fixed label that is safe to log."""
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def verified_ingress_fingerprint(request, *, secret, trusted_peers):
    if not isinstance(secret, str) or len(secret) < 32 or not trusted_peers:
        raise IngressRefused('Native ingress is not configured', 'not-configured')
    supplied = request.META.get('HTTP_X_FAIRPLAY_INGRESS_KEY', '')
    if request.META.get('REMOTE_ADDR') not in trusted_peers:
        raise IngressRefused('Untrusted native ingress', 'peer-not-trusted')
    if not request.is_secure():
        raise IngressRefused('Untrusted native ingress', 'not-secure')
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, secret):
        raise IngressRefused('Untrusted native ingress', 'key-mismatch')
    if request.META.get('HTTP_X_FAIRPLAY_CLIENT_VERIFY') != 'SUCCESS':
        raise IngressRefused('Untrusted native ingress', 'verify-not-success')
    pem = request.META.get('HTTP_X_FAIRPLAY_CLIENT_CERT', '')
    if not isinstance(pem, str) or not 1 <= len(pem) <= 16384:
        raise IngressRefused('Missing native certificate', 'no-certificate')
    try:
        cert = x509.load_pem_x509_certificate(unquote(pem).encode('ascii'))
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise IngressRefused('Expired native certificate', 'cert-expired')
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.CLIENT_AUTH not in usage:
            raise IngressRefused('Not a client certificate', 'cert-not-client')
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        return hashlib.sha256(cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    except IngressRefused:
        raise
    except (ValueError, UnicodeError, x509.ExtensionNotFound) as error:
        raise IngressRefused('Invalid native certificate', 'cert-invalid') from error

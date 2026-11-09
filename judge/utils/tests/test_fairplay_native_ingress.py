from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.test import SimpleTestCase, RequestFactory
from judge.utils.fairplay_native_ingress import verified_ingress_fingerprint


class NativeIngressTests(SimpleTestCase):
    def setUp(self):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test-device')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
                .not_valid_after(now+timedelta(minutes=10))
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
                .sign(key, hashes.SHA256()))
        import hashlib
        self.fingerprint = hashlib.sha256(cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        self.pem = quote(cert.public_bytes(serialization.Encoding.PEM).decode(), safe='')

    def request(self, **changes):
        headers = dict(REMOTE_ADDR='127.0.0.1', HTTP_X_FAIRPLAY_INGRESS_KEY='s'*32,
                       HTTP_X_FAIRPLAY_CLIENT_VERIFY='SUCCESS', HTTP_X_FAIRPLAY_CLIENT_CERT=self.pem)
        headers.update(changes)
        return RequestFactory().get('/', secure=True, **headers)

    def verify(self, request):
        return verified_ingress_fingerprint(request, secret='s'*32, trusted_peers={'127.0.0.1'})

    def test_private_verified_ingress_extracts_certificate_fingerprint(self):
        self.assertEqual(self.verify(self.request()), self.fingerprint)

    def test_public_or_unverified_requests_cannot_supply_identity(self):
        for changes in [dict(REMOTE_ADDR='10.1.2.3'), dict(HTTP_X_FAIRPLAY_INGRESS_KEY=''),
                        dict(HTTP_X_FAIRPLAY_CLIENT_VERIFY='NONE'), dict(HTTP_X_FAIRPLAY_CLIENT_CERT='bad')]:
            with self.assertRaises(ValueError):
                self.verify(self.request(**changes))
        with self.assertRaises(ValueError):
            self.verify(RequestFactory().get('/'))

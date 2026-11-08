"""Native certificate provisioning using the released app-proof threat model.

The compiled app HMAC prevents ordinary browser enrollment, not reverse
engineering. Certificates identify a private key; they do not attest OS lock.
No student cookie, account identifier, or exam code is accepted here.
"""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from . import app_client
from .store import get_store

router = APIRouter(prefix='/api/native-browser/enrollment', tags=['native-browser'])

class ChallengeIn(BaseModel):
    csr_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

class CertificateIn(BaseModel):
    nonce: str = Field(pattern=r'^[0-9a-f]{64}$')
    csr: str = Field(min_length=1, max_length=8192)
    signature: str = Field(pattern=r'^[0-9a-f]{64}$')


def proof_message(nonce, digest):
    return 'native-browser-enroll-v1|' + nonce + '|' + digest


def issue_certificate(csr_der, ca_pem, key_pem, now=None, days=30):
    now = now or datetime.now(timezone.utc)
    csr = x509.load_der_x509_csr(csr_der)
    public = csr.public_key()
    if not csr.is_signature_valid or not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve, ec.SECP256R1):
        raise ValueError('A signed P-256 CSR is required')
    ca = x509.load_pem_x509_certificate(ca_pem)
    key = serialization.load_pem_private_key(key_pem, password=None)
    encode = lambda p: p.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if (encode(key.public_key()) != encode(ca.public_key())
            or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or not ca.not_valid_before_utc <= now < ca.not_valid_after_utc - timedelta(hours=1)):
        raise ValueError('Native certificate authority is unavailable')
    # Ignore all caller-supplied subject names, SANs and CSR extensions.
    cert = (x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'FairPlay native device')]))
        .issuer_name(ca.subject).public_key(public).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(min(now+timedelta(days=days), ca.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)


def configured(request):
    settings = request.app.state.settings
    if not settings.native_browser_enrollment or not settings.native_browser_ca or not settings.native_browser_ca_key:
        raise HTTPException(404)
    return settings


def response(payload):
    return JSONResponse(payload, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@router.post('/challenge')
async def challenge(body: ChallengeIn, request: Request, store=Depends(get_store)):
    settings = configured(request)
    peer = request.client.host if request.client else 'unknown'
    in_school = getattr(settings, 'in_school_net', lambda ip: False)(peer)
    limit = getattr(settings, 'native_browser_enroll_school_ip_limit', 3000) if in_school \
        else getattr(settings, 'native_browser_enroll_ip_limit', 300)
    if await store.incr('native-enroll-rate:' + peer, 60) > limit:
        raise HTTPException(429, 'Try again later')
    nonce = secrets.token_hex(32)
    if not await store.setnx('native-enroll:' + nonce, body.csr_sha256, 90):
        raise HTTPException(503)
    return response({'nonce': nonce})


@router.post('/certificate')
async def certificate(body: CertificateIn, request: Request, store=Depends(get_store)):
    settings = configured(request)
    try:
        der = base64.b64decode(body.csr, validate=True)
        if not 1 <= len(der) <= 4096:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, 'Invalid certificate request') from None
    digest = hashlib.sha256(der).hexdigest()
    if not app_client.verify(settings, proof_message(body.nonce, digest), body.signature):
        raise HTTPException(403, 'Native app proof required')
    if await store.getdel('native-enroll:' + body.nonce) != digest:
        raise HTTPException(409, 'Enrollment challenge expired or already used')
    try:
        ca_path, key_path = Path(settings.native_browser_ca), Path(settings.native_browser_ca_key)
        if key_path.stat().st_mode & 0o077:
            raise ValueError('CA key permissions must exclude group and other users')
        issued = issue_certificate(der, ca_path.read_bytes(), key_path.read_bytes(),
                                  days=getattr(settings, 'native_browser_cert_days', 30))
    except (OSError, ValueError, x509.ExtensionNotFound):
        raise HTTPException(503, 'Device enrollment is unavailable') from None
    return response({'certificate': base64.b64encode(issued).decode()})

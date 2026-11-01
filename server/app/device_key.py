"""Per-device key binding (docs/device-key.md).

The app keeps a P-256 private key that never leaves the device. At start it sends the
public key (device_pub) and a signature (device_sig) over b"fp-device-v1|" + the same
message the app-key HMAC signs. The device id is derived from the public key, so it cannot
be chosen, and a copied id string is useless without the private key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

DOMAIN = b"fp-device-v1|"
ID_BYTES = 20


@dataclass(frozen=True)
class DeviceProof:
    device_id: str
    pub: str | None = None       # canonical base64url (no padding) of the DER public key
    derived: bool = True         # device_id is the one derived from pub (always True without pub)


def _b64u(s: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        return None


def _enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def derive_ids(der: bytes) -> tuple[str, str]:
    """(hex, base32) of the first 20 bytes of sha256(der); both lowercase / unpadded."""
    d = hashlib.sha256(der).digest()[:ID_BYTES]
    return d.hex(), base64.b32encode(d).decode().rstrip("=").lower()


def _load(pub: str):
    der = _b64u(pub or "")
    if not der:
        return None, None
    try:
        key = serialization.load_der_public_key(der)
    except ValueError:
        return None, None
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        return None, None
    return key, der


def _der_sig(raw: bytes) -> bytes | None:
    if len(raw) == 64:
        return encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    if raw[:1] == b"\x30":
        return raw
    return None


def verify(device_id: str, pub: str | None, sig: str | None, message: str) -> DeviceProof | None:
    """None when pub/sig are present but do not verify. A proof without both fields is a
    legacy proof (pub None); one with only one of them is invalid."""
    if not pub and not sig:
        return DeviceProof(device_id)
    if not pub or not sig:
        return None
    key, der = _load(pub)
    raw = _b64u(sig)
    der_sig = _der_sig(raw) if raw else None
    if key is None or der_sig is None:
        return None
    try:
        key.verify(der_sig, DOMAIN + message.encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return None
    return DeviceProof(device_id, _enc(der), device_id.lower() in derive_ids(der))

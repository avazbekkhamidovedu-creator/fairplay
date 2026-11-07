import base64
import hashlib
import hmac
import json
import os
import random
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

VERSION = 1
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_PREFIX = b"fairplay-native-exit|v1|"
SEAL_INFO = b"fairplay-native-exit-seal|v1"


def derive_code(k: bytes, lock_id: str) -> str:
    mac = hmac.new(k, CODE_PREFIX + lock_id.encode("utf-8"), hashlib.sha256).digest()
    n = int.from_bytes(mac[:4], "big") & 0x7FFFFFFF
    return f"{n % 1_000_000:06d}"


def _public_bytes(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _aead_key(shared: bytes, lock_id: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=lock_id.encode("utf-8"), info=SEAL_INFO
    ).derive(shared)


def seal(k, lock_id, server_public_bytes, eph_private=None, nonce=None) -> str:
    if len(k) != 32:
        raise ValueError("K must be 32 bytes")
    eph = X25519PrivateKey.from_private_bytes(eph_private) if eph_private else X25519PrivateKey.generate()
    nonce = nonce if nonce is not None else os.urandom(12)
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    shared = eph.exchange(X25519PublicKey.from_public_bytes(server_public_bytes))
    aad = lock_id.encode("utf-8")
    ct_tag = ChaCha20Poly1305(_aead_key(shared, lock_id)).encrypt(nonce, k, aad)
    return base64.b64encode(_public_bytes(eph) + nonce + ct_tag).decode("ascii")


def open_blob(blob_b64, lock_id, server_private_bytes) -> bytes:
    blob = base64.b64decode(blob_b64, validate=True)
    if len(blob) != 92:
        raise ValueError("bad blob length")
    eph_pub, nonce, ct_tag = blob[:32], blob[32:44], blob[44:]
    priv = X25519PrivateKey.from_private_bytes(server_private_bytes)
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    return ChaCha20Poly1305(_aead_key(shared, lock_id)).decrypt(nonce, ct_tag, lock_id.encode("utf-8"))


def _fmt(lock_id: str) -> str:
    return lock_id[:4] + "-" + lock_id[4:]


def build_vector() -> dict:
    k = bytes(range(32))
    rng = random.Random(20261106)
    plain = ["K7QM3XPA", "AAAAAAAA", "23456789"]
    zero1, zero3 = None, None
    leading = []
    while len(leading) < 3 or zero3 is None:
        lid = "".join(rng.choice(ALPHABET) for _ in range(8))
        c = derive_code(k, lid)
        if c.startswith("00") and zero3 is None:
            zero3 = lid
        elif c.startswith("0") and not c.startswith("00") and len(leading) < 3 and lid not in leading:
            leading.append(lid)
    ids = plain + leading + [zero3]
    server_priv = bytes([0x11] * 32)
    eph_priv = bytes([0x22] * 32)
    nonce = bytes(range(0xA0, 0xAC))
    server_pub = _public_bytes(X25519PrivateKey.from_private_bytes(server_priv))
    lock_id = "K7QM3XPA"
    blob = seal(k, lock_id, server_pub, eph_priv, nonce)
    raw = bytearray(base64.b64decode(blob))
    raw[50] ^= 0x01
    return {
        "version": VERSION,
        "alphabet": ALPHABET,
        "code_message_prefix": CODE_PREFIX.decode(),
        "seal_info": SEAL_INFO.decode(),
        "k_hex": k.hex(),
        "codes": [{"lock_id": i, "display": _fmt(i), "code": derive_code(k, i)} for i in ids],
        "sealed": {
            "note": "TEST ONLY key pair, published in this repo, not a secret",
            "server_private_hex": server_priv.hex(),
            "server_public_hex": server_pub.hex(),
            "ephemeral_private_hex": eph_priv.hex(),
            "nonce_hex": nonce.hex(),
            "k_hex": k.hex(),
            "lock_id": lock_id,
            "blob_base64": blob,
            "code": derive_code(k, lock_id),
        },
        "negative": {
            "wrong_lock_id": "K7QM3XPB",
            "flipped_byte_index": 50,
            "flipped_blob_base64": base64.b64encode(bytes(raw)).decode("ascii"),
        },
    }


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "../docs/native-exit-vectors.json"
    with open(out, "w") as f:
        json.dump(build_vector(), f, indent=2)
        f.write("\n")

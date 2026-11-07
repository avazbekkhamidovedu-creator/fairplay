import json
import os
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from tools import native_exit_vector as v

VEC = json.loads((Path(__file__).resolve().parents[2] / "docs" / "native-exit-vectors.json").read_text())


def test_alphabet_and_version():
    assert VEC["version"] == v.VERSION
    assert VEC["alphabet"] == v.ALPHABET and len(v.ALPHABET) == 31


def test_codes():
    k = bytes.fromhex(VEC["k_hex"])
    assert any(c["code"].startswith("0") for c in VEC["codes"])
    for c in VEC["codes"]:
        assert len(c["lock_id"]) == 8 and set(c["lock_id"]) <= set(v.ALPHABET)
        assert v.derive_code(k, c["lock_id"]) == c["code"]
        assert c["display"] == c["lock_id"][:4] + "-" + c["lock_id"][4:]


def test_sealed_example_recomputes():
    s = VEC["sealed"]
    k = bytes.fromhex(s["k_hex"])
    blob = v.seal(k, s["lock_id"], bytes.fromhex(s["server_public_hex"]),
                  bytes.fromhex(s["ephemeral_private_hex"]), bytes.fromhex(s["nonce_hex"]))
    assert blob == s["blob_base64"]
    assert v.open_blob(blob, s["lock_id"], bytes.fromhex(s["server_private_hex"])) == k
    assert v.derive_code(k, s["lock_id"]) == s["code"]


def test_negative_cases():
    s, n = VEC["sealed"], VEC["negative"]
    priv = bytes.fromhex(s["server_private_hex"])
    with pytest.raises(InvalidTag):
        v.open_blob(s["blob_base64"], n["wrong_lock_id"], priv)
    with pytest.raises(InvalidTag):
        v.open_blob(n["flipped_blob_base64"], s["lock_id"], priv)


def test_random_round_trip():
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

    sk = X25519PrivateKey.generate()
    priv = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = v._public_bytes(sk)
    k = os.urandom(32)
    blob = v.seal(k, "ABCD2345", pub)
    assert v.open_blob(blob, "ABCD2345", priv) == k

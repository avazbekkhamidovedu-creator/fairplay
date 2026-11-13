import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import select, update

from app import app_client as ac
from app import device_key
from app.models import Attempt, IntegrityEvent

from .conftest import login
from .oj_helpers import APP_KEY, bearer, db, patch_exam, ready, run, sign_in, soon  # noqa: F401
from .test_dkuoj_bridge import oj_exam
from .test_dkuoj_bridge_v2 import V1, fake, oj  # noqa: F401  (fixtures)


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Dev:
    def __init__(self, raw_sig=False):
        self.key = ec.generate_private_key(ec.SECP256R1())
        der = self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        self.pub = b64u(der)
        self.id = hashlib.sha256(der).digest()[:20].hex()
        self.raw_sig = raw_sig

    def sig(self, message):
        der = self.key.sign(device_key.DOMAIN + message.encode(), ec.ECDSA(hashes.SHA256()))
        if not self.raw_sig:
            return b64u(der)
        r, s = decode_dss_signature(der)
        return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def client(nonce, device_id, key=None):
    msg = ac.start_message(nonce, device_id)
    out = {"device_id": device_id, "nonce": nonce, "signature": ac.sign(APP_KEY, msg)}
    if key is not None:
        out.update(device_pub=key.pub, device_sig=key.sig(msg))
    return out


def code_sign_in(c, netid, device_id, key=None):
    return sign_in(c, netid, device_id)


def start(c, body, eid, device_id, key=None, start_code="", mutate=None):
    nonce = c.get(f"/api/student/exams/{eid}/challenge", headers=bearer(body)).json()["nonce"]
    cl = client(nonce, device_id, key)
    if mutate:
        cl = mutate(cl)
    return c.post(f"/api/student/exams/{eid}/start", json={"start_code": start_code, "client": cl},
                  headers=bearer(body))


def attempt_row(c):
    return db(c, lambda s: s.scalar(select(Attempt)))


def event_types(c):
    async def q(s):
        return list((await s.execute(select(IntegrityEvent.type))).scalars())
    return db(c, q)


@pytest.fixture
def live(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    opened = oj.post(f"/api/exams/{eid}/open", headers=teacher).json()
    return teacher, eid, opened["start_code"]


@pytest.mark.parametrize("raw", [False, True])
def test_first_start_stores_the_key_and_derived_id(oj, ready, live, raw):
    _, eid, code = live
    k = Dev(raw_sig=raw)
    body = code_sign_in(oj, ready["alice"], k.id, k)
    r = start(oj, body, eid, k.id, k, code)
    assert r.status_code == 200, r.text
    a = attempt_row(oj)
    assert (a.device_id, a.device_pub) == (k.id, k.pub)


def test_base32_id_is_accepted(oj, ready, live):
    _, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], "DEVICE-LEGACY-0001")
    der = base64.urlsafe_b64decode(k.pub + "=" * (-len(k.pub) % 4))
    b32 = device_key.derive_ids(der)[1]
    assert start(oj, body, eid, b32, k, code).status_code == 200


def test_legacy_id_with_a_key_binds_on_first_start(oj, ready, live):
    _, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], "DEVICE-LEGACY-0001")
    r = start(oj, body, eid, "DEVICE-CHOSEN-001", k, code)
    assert r.status_code == 200, r.text
    a = attempt_row(oj)
    assert (a.device_id, a.device_pub) == ("DEVICE-CHOSEN-001", k.pub)


@pytest.mark.parametrize("how", ["bad_sig", "other_key_sig", "pub_only", "garbage_pub"])
def test_bad_key_proof_is_refused(oj, ready, live, how):
    _, eid, code = live
    k, other = Dev(), Dev()
    body = code_sign_in(oj, ready["alice"], "DEVICE-LEGACY-0001")

    def mutate(cl):
        if how == "bad_sig":
            cl["device_sig"] = b64u(b"\x30" + bytes(70))
        elif how == "other_key_sig":
            cl["device_sig"] = other.sig(ac.start_message(cl["nonce"], k.id))
        elif how == "pub_only":
            cl.pop("device_sig")
        else:
            cl["device_pub"] = b64u(b"not a key")
        return cl
    r = start(oj, body, eid, k.id, k, code, mutate)
    assert r.status_code == 403 and r.json()["detail"] == ac.APP_PROOF_DETAIL


def test_signature_must_cover_the_domain_prefix(oj, ready, live):
    _, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], "DEVICE-LEGACY-0001")

    def mutate(cl):
        cl["device_sig"] = b64u(k.key.sign(ac.start_message(cl["nonce"], k.id).encode(),
                                           ec.ECDSA(hashes.SHA256())))
        return cl
    assert start(oj, body, eid, k.id, k, code, mutate).status_code == 403


def test_same_id_with_no_or_other_key_is_a_second_device(oj, ready, live):
    _, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], k.id, k)
    assert start(oj, body, eid, k.id, k, code).status_code == 200
    r = start(oj, body, eid, k.id, None, code)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "swap_needed"
    assert start(oj, body, eid, k.id, Dev(), code).status_code == 403     # copied id + own key: id is not derived
    assert "second_device" in event_types(oj)
    a = attempt_row(oj)
    assert (a.device_id, a.device_pub) == (k.id, k.pub)


def test_other_device_with_its_own_key_is_a_second_device(oj, ready, live):
    _, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], k.id, k)
    assert start(oj, body, eid, k.id, k, code).status_code == 200
    j = Dev()
    assert start(oj, body, eid, j.id, j, code).status_code == 409


def test_same_key_resumes_and_quiet_window_needs_the_key(oj, ready, live):
    _, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], k.id, k)
    first = start(oj, body, eid, k.id, k, code)
    again = start(oj, body, eid, k.id, k, code)
    assert again.status_code == 200 and again.json()["session"] == first.json()["session"]
    assert start(oj, body, eid, k.id, None, code).status_code == 409
    assert start(oj, body, eid, k.id, Dev(), code).status_code == 403


def test_legacy_attempt_upgrades_once(oj, ready, live):
    _, eid, code = live
    legacy = "legacy-device-0001"
    body = code_sign_in(oj, ready["alice"], legacy)
    assert start(oj, body, eid, legacy, None, code).status_code == 200
    assert attempt_row(oj).device_pub is None
    k = Dev()
    r = start(oj, body, eid, legacy, k, code)
    assert r.status_code == 200, r.text
    assert attempt_row(oj).device_pub == k.pub and "device_key_upgraded" in event_types(oj)
    n = event_types(oj).count("device_key_upgraded")
    assert start(oj, body, eid, legacy, Dev(), code).status_code == 403
    assert start(oj, body, eid, legacy, k, code).status_code == 200
    assert attempt_row(oj).device_pub == k.pub
    assert event_types(oj).count("device_key_upgraded") == n
    assert start(oj, body, eid, legacy, None, code).status_code == 409


def test_non_derived_id_is_refused_without_a_legacy_bind(oj, ready, live):
    _, eid, code = live
    body = code_sign_in(oj, ready["alice"], "legacy-device-0001")
    assert start(oj, body, eid, "legacy-device-0001", None, code).status_code == 200
    assert start(oj, body, eid, "someone-elses-id-02", Dev(), code).status_code == 403


def test_release_clears_the_key(oj, ready, live):
    teacher, eid, code = live
    k = Dev()
    body = code_sign_in(oj, ready["alice"], k.id, k)
    started = start(oj, body, eid, k.id, k, code)
    aid = started.json()["attempt_id"]
    assert oj.post(f"/api/exams/{eid}/attempts/{aid}/release-device", headers=teacher).status_code == 200
    a = attempt_row(oj)
    assert (a.device_id, a.device_pub) == (None, None)
    j = Dev()
    assert start(oj, body, eid, j.id, j, code).status_code == 200
    assert attempt_row(oj).device_pub == j.pub


def test_require_flag_refuses_keyless_starts_and_logins(oj, ready, live):
    _, eid, code = live
    body = code_sign_in(oj, ready["alice"], "DEVICE-LEGACY-0001")
    oj.app.state.settings.require_device_key = True
    r = start(oj, body, eid, "legacy-device-0001", None, code)
    assert r.status_code == 426 and r.json()["detail"] == "Update FairPlay to continue"
    k = Dev()
    body = code_sign_in(oj, ready["carol"], k.id, k)
    assert start(oj, body, eid, k.id, k, code).status_code == 200


def test_flag_off_keeps_old_apps_working(oj, ready, live):
    _, eid, code = live
    body = code_sign_in(oj, ready["alice"], "legacy-device-0001")
    assert start(oj, body, eid, "legacy-device-0001", None, code).status_code == 200

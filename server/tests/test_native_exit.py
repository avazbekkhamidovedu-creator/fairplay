import asyncio
import base64
import logging
import os
import stat
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select, update

from app import app_client as ac
from app import device_key
from app.integrations.dkuoj import native_exit as ne
from app.integrations.dkuoj.models import ClassLoginEvent, OjEditor
from app.models import Exam, User
from app.store import get_store
from app.timeutil import utcnow
from tools import native_exit_vector as vec

from .oj_helpers import KEY
from .test_dkuoj_bridge import db, fake, run  # noqa: F401  (fixture)
from .test_dkuoj_bridge_v2 import V1, call, oj  # noqa: F401  (fixtures)

APP_KEY = "test-app-key"
DEVICE = "dev-0123456789"
K = bytes(range(1, 33))
STU = f"{V1}/exams/{KEY}/students"
LOCKS = "/api/native-exit/locks"


@pytest.fixture
def server_key(monkeypatch):
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes_raw()
    monkeypatch.setenv(ne.ENV_KEY, base64.b64encode(raw).decode())
    return raw, priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


@pytest.fixture
def synced(oj, server_key):
    run(oj)
    return oj


def blob_for(server_key, lock_id, k=K):
    return vec.seal(k, lock_id, server_key[1])


def reg(c, server_key, lock_id="K7QM3XPA", device=DEVICE, blob=None, sig=None, k=K):
    body = {"lock_id": lock_id, "device": device,
            "blob": blob if blob is not None else blob_for(server_key, lock_id, k),
            "signature": sig or ac.sign(APP_KEY, ne.lock_message(lock_id, device))}
    return c.post(LOCKS, json=body)


def issue(c, lock="K7QM-3XPA", netid="alice", reason="lost the laptop lid", **kw):
    return call(c, "POST", f"{STU}/{netid}/native-exit", {"mac_code": lock, "reason": reason}, **kw)


def test_register_and_issue_matches_vector_code(synced, server_key):
    r = reg(synced, server_key)
    assert r.status_code == 201 and r.json() == {"ok": True} and r.headers["cache-control"] == "no-store"
    r = issue(synced)
    assert r.status_code == 200 and r.json() == {"code": vec.derive_code(K, "K7QM3XPA")}
    assert r.headers["cache-control"] == "no-store"


def test_repeat_of_same_lock_is_idempotent(synced, server_key):
    blob = blob_for(server_key, "K7QM3XPA")
    assert reg(synced, server_key, blob=blob).status_code == 201
    r = reg(synced, server_key, blob=blob)
    assert r.status_code == 201 and r.json() == {"ok": True} and r.headers["cache-control"] == "no-store"
    assert blob not in r.text
    assert issue(synced).json()["code"] == vec.derive_code(K, "K7QM3XPA")


def test_same_lock_other_blob_or_device_is_409(synced, server_key):
    blob = blob_for(server_key, "K7QM3XPA")
    assert reg(synced, server_key, blob=blob).status_code == 201
    for kw in ({"k": bytes(32)}, {"device": "other-device-01"}):
        r = reg(synced, server_key, **kw)
        assert r.status_code == 409 and r.headers["cache-control"] == "no-store"
        assert blob not in r.text
    assert issue(synced).json()["code"] == vec.derive_code(K, "K7QM3XPA")


def test_repeat_adds_no_row_and_does_not_count_against_limits(synced, server_key):
    blob = blob_for(server_key, "K7QM3XPA")
    for _ in range(3):
        assert reg(synced, server_key, blob=blob).status_code == 201

    async def count(s):
        return (await s.execute(select(ne.func.count()).select_from(ne.NativeExitLock))).scalar_one()
    assert db(synced, count) == 1
    assert synced.portal.call(get_store().incr, f"nexit:dev:{DEVICE}", 3600) == 2


def test_app_route_auth_and_validation(synced, server_key):
    assert reg(synced, server_key, sig="0" * 64).status_code == 403
    assert synced.post(LOCKS, json={"lock_id": "K7QM3XPA", "device": DEVICE, "blob": "x"}).status_code == 422
    assert reg(synced, server_key, lock_id="K7QM3XPI").status_code in (403, 422)
    sig = ac.sign(APP_KEY, ne.lock_message("K7QM3XPI", DEVICE))
    assert reg(synced, server_key, lock_id="K7QM3XPI", sig=sig).status_code == 422
    sig = ac.sign(APP_KEY, ne.lock_message("K7QM3XPA", "bad device!"))
    assert reg(synced, server_key, device="bad device!", sig=sig).status_code == 422
    other = ac.sign(APP_KEY, ne.lock_message("K7QM3XPA", "dev-other-0001"))
    assert reg(synced, server_key, sig=other).status_code == 403


def test_blob_sealed_to_another_server_key_is_refused_at_registration(synced, server_key):
    other = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    wrong = vec.seal(K, "K7QM3XPA", other)
    assert reg(synced, server_key, blob=wrong).status_code == 422
    assert reg(synced, server_key, blob=blob_for(server_key, "K7QM3XPB")).status_code == 422
    assert issue(synced).status_code == 404


def test_expired_rows_are_ignored_but_kept(synced, server_key):
    assert reg(synced, server_key, lock_id="K7QM3XPA").status_code == 201

    async def age(s):
        await s.execute(update(ne.NativeExitLock).where(ne.NativeExitLock.lock_id == "K7QM3XPA")
                        .values(created_at=utcnow() - ne.LOCK_TTL - timedelta(hours=1)))
        await s.commit()
    db(synced, age)
    assert issue(synced).status_code == 404

    async def count(s):
        return (await s.execute(select(ne.NativeExitLock.lock_id))).scalars().all()
    assert list(db(synced, count)) == ["K7QM3XPA"]
    assert not hasattr(ne, "purge_expired_native_exit_locks")


def test_global_hourly_cap_on_registrations(synced, server_key, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_HOUR_ALL", 2)
    monkeypatch.setattr(ne, "LOCKS_IP_SHARE_PERCENT", 100)
    assert reg(synced, server_key, lock_id="K7QM3XPA", device="dev-aaaaaaaa1").status_code == 201
    assert reg(synced, server_key, lock_id="K7QM3XPB", device="dev-aaaaaaaa2").status_code == 201
    assert reg(synced, server_key, lock_id="K7QM3XPC", device="dev-aaaaaaaa3").status_code == 429


def test_oversize_and_bad_blob_rejected(synced, server_key):
    good = base64.b64decode(blob_for(server_key, "K7QM3XPA"))
    for bad in (base64.b64encode(good + b"x").decode(), base64.b64encode(good[:-1]).decode(),
                "A" * 100000, "not base64!!"):
        assert reg(synced, server_key, blob=bad).status_code in (413, 422), bad[:10]
    assert reg(synced, server_key).status_code == 201


def test_app_rate_limits(synced, server_key, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_DEVICE_HOUR", 2)
    ids = ["AAAAAAA2", "AAAAAAA3", "AAAAAAA4"]
    assert [reg(synced, server_key, lock_id=i).status_code for i in ids] == [201, 201, 429]
    monkeypatch.setattr(ne, "LOCKS_PER_IP_HOUR", 3)
    r = reg(synced, server_key, lock_id="AAAAAAA5", device="dev-other-0001")
    assert r.status_code == 201
    assert reg(synced, server_key, lock_id="AAAAAAA6", device="dev-other-0002").status_code == 429


def test_signed_route_auth(synced, server_key):
    reg(synced, server_key)
    url = f"{STU}/alice/native-exit"
    assert synced.post(url, json={"mac_code": "K7QM3XPA", "reason": "r"}).status_code == 401
    for who in ("nobody", "alice", ""):
        r = issue(synced, acting=who)
        assert r.status_code == 403, (who, r.status_code)
    assert issue(synced, acting="teacher", token="wrong").status_code == 401


def test_editor_of_another_exam_is_refused(synced, server_key):
    reg(synced, server_key)

    async def go(s):
        t = User(username="t2", name="Other", role="teacher", password_hash="x", netid="t2")
        s.add(t)
        await s.flush()
        s.add(Exam(id="e_other", teacher_id=t.id, title="Other", duration_min=10, status="live", questions=[],
                   question_count=0, total_points=0, source="dkuoj", external_key="other-exam"))
        await s.flush()
        s.add(OjEditor(exam_id="e_other", user_id=t.id))
        await s.commit()
    db(synced, go)
    assert issue(synced, acting="t2").status_code == 403


def test_unknown_malformed_and_expired_lock(synced, server_key):
    assert issue(synced, lock="K7QM-3XPA").status_code == 404
    assert issue(synced, lock="nope").status_code == 422
    reg(synced, server_key)

    async def age(s):
        await s.execute(update(ne.NativeExitLock).values(created_at=utcnow() - timedelta(days=7, minutes=1)))
        await s.commit()
    db(synced, age)
    assert issue(synced).status_code == 404

    async def fresh(s):
        await s.execute(update(ne.NativeExitLock).values(created_at=utcnow() - timedelta(days=6, hours=23)))
        await s.commit()
    db(synced, fresh)
    assert issue(synced).status_code == 200


def test_reason_is_optional(synced, server_key):
    reg(synced, server_key)
    assert call(synced, "POST", f"{STU}/alice/native-exit", {"mac_code": "K7QM3XPA"}).status_code == 200
    assert issue(synced, reason="  ").status_code == 200


def test_first_issue_binds_student(synced, server_key):
    reg(synced, server_key)
    assert issue(synced, netid="alice").status_code == 200
    assert issue(synced, netid="bl642").status_code == 409
    assert issue(synced, netid="ALICE").status_code == 200

    async def row(s):
        return (await s.execute(select(ne.NativeExitLock))).scalar_one()
    r = db(synced, row)
    assert (r.netid, r.issued_count, r.exam_key) == ("alice", 2, KEY) and r.last_issue_at is not None


def test_per_lock_cap(synced, server_key):
    reg(synced, server_key)
    assert [issue(synced).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_student_and_exam_rate_limits(synced, server_key, monkeypatch):
    for i in ("AAAAAAA2", "AAAAAAA3", "AAAAAAA4"):
        reg(synced, server_key, lock_id=i)
    monkeypatch.setattr(ne, "ISSUES_PER_STUDENT_HOUR", 2)
    assert [issue(synced, lock="AAAAAAA2", netid="alice").status_code for _ in range(3)] == [200, 200, 429]
    monkeypatch.setattr(ne, "ISSUES_PER_STUDENT_HOUR", 20)
    monkeypatch.setattr(ne, "ISSUES_PER_EXAM_HOUR", 3)
    assert issue(synced, lock="AAAAAAA3", netid="bl642").status_code == 200
    assert issue(synced, lock="AAAAAAA4", netid="ep530").status_code == 429


def test_missing_private_key_is_503(oj, server_key, monkeypatch):
    run(oj)
    reg(oj, server_key)
    monkeypatch.delenv(ne.ENV_KEY)
    r = issue(oj)
    assert r.status_code == 503 and "set up" in r.json()["detail"]
    assert reg(oj, server_key, lock_id="AAAAAAA2").status_code == 503
    monkeypatch.setenv(ne.ENV_KEY, "not-a-key")
    assert issue(oj).status_code == 503


def test_secrets_never_leak(synced, server_key, caplog):
    caplog.set_level(logging.DEBUG)
    blob = blob_for(server_key, "K7QM3XPA")
    responses = [reg(synced, server_key, blob=blob), reg(synced, server_key, blob=blob),
                 reg(synced, server_key, sig="0" * 64), issue(synced), issue(synced, netid="bl642")]
    code = vec.derive_code(K, "K7QM3XPA")
    seen = "".join(r.text + str(dict(r.headers)) for r in responses[:3] + responses[4:])
    assert blob not in seen and K.hex() not in seen and base64.b64encode(K).decode() not in seen
    assert responses[3].json() == {"code": code}
    logs = caplog.text
    assert blob not in logs and K.hex() not in logs and base64.b64encode(K).decode() not in logs
    assert f'"{code}"' not in logs and f" {code}" not in logs


def test_audit_row_written(synced, server_key):
    reg(synced, server_key)
    assert issue(synced, reason="Mac froze at 10:42").status_code == 200

    async def rows(s):
        return (await s.execute(select(ClassLoginEvent).where(
            ClassLoginEvent.kind == "native_exit_code_issued"))).scalars().all()
    ev = db(synced, rows)
    assert len(ev) == 1 and ev[0].netid == "alice" and ev[0].actor == "teacher"
    assert "Mac froze at 10:42" in ev[0].detail and "K7QM-3XPA" in ev[0].detail
    assert vec.derive_code(K, "K7QM3XPA") not in ev[0].detail


def test_keygen_prints_only_public_key_and_refuses_overwrite(tmp_path):
    out = tmp_path / "priv.b64"
    script = Path(__file__).resolve().parents[1] / "tools" / "native_exit_keygen.py"
    run1 = subprocess.run([sys.executable, str(script), str(out)], capture_output=True, text=True)
    assert run1.returncode == 0
    private = out.read_text().strip()
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert len(base64.b64decode(private)) == 32 and private not in run1.stdout + run1.stderr
    pub = base64.b64decode(run1.stdout.strip())
    assert pub == X25519PrivateKey.from_private_bytes(base64.b64decode(private)).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)
    run2 = subprocess.run([sys.executable, str(script), str(out)], capture_output=True, text=True)
    assert run2.returncode != 0 and out.read_text().strip() == private
    assert private not in run2.stdout + run2.stderr


def lid(prefix, i):
    out = ""
    for _ in range(7):
        i, d = divmod(i, len(vec.ALPHABET))
        out = vec.ALPHABET[d] + out
    return prefix + out


def test_default_caps_fit_a_class_behind_one_ip():
    assert (ne.ISSUES_PER_LOCK, ne.LOCKS_PER_DEVICE_HOUR, ne.ISSUES_PER_STUDENT_HOUR) == (3, 20, 20)
    assert (ne.LOCKS_PER_IP_HOUR, ne.ISSUES_PER_EXAM_HOUR) == (2000, 300)


def test_env_caps_override_and_fall_back(monkeypatch):
    name = "FAIRPLAY_NATIVE_EXIT_LOCKS_PER_IP_HOUR"
    monkeypatch.setenv(name, "5")
    assert ne.env_cap(name, 2000) == 5
    for bad in ("", "abc", "0", "-3", "1.5"):
        monkeypatch.setenv(name, bad)
        assert ne.env_cap(name, 2000) == 2000, bad
    monkeypatch.delenv(name)
    assert ne.env_cap(name, 2000) == 2000


def test_hundred_devices_one_ip_then_ip_cap(synced, server_key, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_DEVICE_HOUR", 1000)
    codes = [reg(synced, server_key, lock_id=lid("B", i), device=f"dev-class-{i:04d}").status_code
             for i in range(100)]
    assert codes == [201] * 100
    assert asyncio.run(get_store().incr("nexit:ip:testclient", ne.HOUR_S)) == 101
    asyncio.run(get_store().incrby("nexit:ip:testclient", 1899, ne.HOUR_S))
    r = reg(synced, server_key, lock_id=lid("B", 999), device="dev-class-0999")
    assert r.status_code == 429 and r.headers["retry-after"] == "3600"


def test_device_cap_is_twenty(synced, server_key):
    ids = [lid("C", i) for i in range(21)]
    assert [reg(synced, server_key, lock_id=i).status_code for i in ids] == [201] * 20 + [429]


def test_exam_cap_allows_all_then_refuses_next(synced, server_key, monkeypatch):
    monkeypatch.setattr(ne, "ISSUES_PER_EXAM_HOUR", 6)
    monkeypatch.setattr(ne, "ISSUES_PER_STUDENT_HOUR", 1000)
    for i in range(7):
        reg(synced, server_key, lock_id=lid("D", i), device=f"dev-exam-{i:04d}")
    got = [issue(synced, lock=lid("D", i), netid=f"s{i}").status_code for i in range(7)]
    assert got == [200] * 6 + [429]


def test_school_net_has_a_higher_ip_cap_and_the_device_cap_still_applies(synced, server_key, monkeypatch):
    synced.app.state.settings.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(ne, "client_ip", lambda request: "10.1.2.3")
    asyncio.run(get_store().incrby("nexit:ip:10.1.2.3", ne.LOCKS_PER_IP_HOUR + 5, ne.HOUR_S))
    assert reg(synced, server_key, lock_id=lid("E", 0), device="dev-school-0001").status_code == 201
    asyncio.run(get_store().incrby("nexit:ip:10.1.2.3", ne.LOCKS_PER_SCHOOL_IP_HOUR, ne.HOUR_S))
    assert reg(synced, server_key, lock_id=lid("E", 5), device="dev-school-0005").status_code == 429
    asyncio.run(get_store().incrby("nexit:ip:10.1.2.3", -ne.LOCKS_PER_SCHOOL_IP_HOUR, ne.HOUR_S))
    ids = [lid("F", i) for i in range(21)]
    assert [reg(synced, server_key, lock_id=i).status_code for i in ids] == [201] * 20 + [429]


def test_ip_outside_school_nets_is_still_capped(synced, server_key, monkeypatch):
    synced.app.state.settings.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(ne, "client_ip", lambda request: "10.9.2.3")
    asyncio.run(get_store().incrby("nexit:ip:10.9.2.3", ne.LOCKS_PER_IP_HOUR, ne.HOUR_S))
    r = reg(synced, server_key, lock_id=lid("E", 1), device="dev-school-0002")
    assert r.status_code == 429


def test_exam_cap_grows_with_roster(synced, server_key, monkeypatch):
    from sqlalchemy import func
    from app.db import get_database
    from app.integrations.dkuoj.models import OjRosterEntry

    async def roster():
        async with get_database().sessionmaker() as s:
            return (await s.execute(select(func.count()).select_from(OjRosterEntry).where(
                OjRosterEntry.revoked_at.is_(None)))).scalar_one()

    n = asyncio.run(roster())
    assert n >= 2
    monkeypatch.setattr(ne, "ISSUES_PER_EXAM_HOUR", 1)
    monkeypatch.setattr(ne, "ISSUES_PER_STUDENT_HOUR", 1000)
    for i in range(n + 1):
        reg(synced, server_key, lock_id=lid("G", i), device=f"dev-roster-{i:04d}")
    got = [issue(synced, lock=lid("G", i), netid=f"s{i}").status_code for i in range(n + 1)]
    assert got == [200] * n + [429]


def test_student_and_lock_caps_hold_with_large_roster(synced, server_key, monkeypatch):
    monkeypatch.setattr(ne, "ISSUES_PER_EXAM_HOUR", 1000)
    monkeypatch.setattr(ne, "ISSUES_PER_STUDENT_HOUR", 2)
    reg(synced, server_key, lock_id=lid("H", 0))
    reg(synced, server_key, lock_id=lid("H", 1), device="dev-roster-9999")
    assert [issue(synced, lock=lid("H", 0)).status_code for _ in range(3)] == [200, 200, 429]
    monkeypatch.setattr(ne, "ISSUES_PER_STUDENT_HOUR", 20)
    reg(synced, server_key, lock_id=lid("H", 2), device="dev-roster-9998")
    assert [issue(synced, lock=lid("H", 2), netid="bl642").status_code for _ in range(4)] == [200, 200, 200, 429]


SIGN_SEED = bytes(range(50, 82))
SALT = base64.b64encode(bytes(range(16))).decode()
VHASH = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def signing(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    monkeypatch.setenv(ne.SIGNING_ENV_KEY, base64.b64encode(SIGN_SEED).decode())
    return Ed25519PrivateKey.from_private_bytes(SIGN_SEED).public_key()


def reg_v(c, server_key, salt=SALT, iterations=600_000, hash_=VHASH, lock_id="K7QM3XPA", device=DEVICE, blob=None):
    body = {"lock_id": lock_id, "device": device, "blob": blob or blob_for(server_key, lock_id),
            "signature": ac.sign(APP_KEY, ne.lock_message(lock_id, device)),
            "verifier_salt": salt, "verifier_iterations": iterations, "verifier_hash": hash_}
    return c.post(LOCKS, json=body)


def test_client_verifier_mode_is_gone(synced, server_key, signing):
    assert reg_v(synced, server_key).status_code == 422
    assert reg_v(synced, server_key, hash_=base64.b64encode(bytes(32)).decode()).status_code == 422
    assert reg(synced, server_key).status_code == 201


def test_old_clients_without_a_verifier_are_unaffected(synced, server_key, signing):
    r = reg(synced, server_key)
    assert r.status_code == 201 and r.json() == {"ok": True}


def test_old_clients_work_when_no_signing_key_is_set(synced, server_key):
    assert reg(synced, server_key).json() == {"ok": True}


def reg_s(c, lock_id="K7QM3XPA", device=DEVICE, **extra):
    body = {"lock_id": lock_id, "device": device, "server_secret": True,
            "signature": ac.sign(APP_KEY, ne.lock_message(lock_id, device))}
    body.update(extra)
    return c.post(LOCKS, json=body)


def pbkdf2_check(code, lock_id, salt_b64, iterations, hash_b64):
    import hashlib
    d = hashlib.pbkdf2_hmac("sha256", code.encode(), b"fairplay-native-exit-verifier|v1|" + lock_id.encode()
                            + base64.b64decode(salt_b64), iterations, 32)
    return base64.b64encode(d).decode() == hash_b64


def test_server_secret_verifier_matches_the_instructor_code_and_is_signed(synced, server_key, signing):
    r = reg_s(synced)
    assert r.status_code == 201
    j = r.json()
    assert set(j) == {"ok", "verifier_created", "verifier_salt", "verifier_iterations", "verifier_hash", "verifier_signature"}
    text = (f"fairplay-native-verifier|v2|K7QM3XPA|{DEVICE}|{j['verifier_created']}|{j['verifier_salt']}|"
            f"{j['verifier_iterations']}|{j['verifier_hash']}").encode()
    signing.verify(base64.b64decode(j["verifier_signature"]), text)
    with pytest.raises(Exception):
        signing.verify(base64.b64decode(j["verifier_signature"]), text.replace(b"|v2|", b"|v1|"))
    code = issue(synced).json()["code"]
    assert pbkdf2_check(code, "K7QM3XPA", j["verifier_salt"], j["verifier_iterations"], j["verifier_hash"])
    assert code not in r.text


def test_server_secret_repeat_is_identical_and_modes_do_not_mix(synced, server_key, signing):
    first = reg_s(synced).json()
    assert reg_s(synced).json() == first
    assert reg_s(synced, device="dev-other-0001").status_code == 409
    assert reg(synced, server_key).status_code == 409
    assert reg(synced, server_key, lock_id="K7QM3XPB").status_code == 201
    assert reg_s(synced, lock_id="K7QM3XPB").status_code == 409


def test_server_secret_refuses_client_material_and_missing_keys(synced, server_key, signing, monkeypatch):
    assert reg_s(synced, blob=blob_for(server_key, "K7QM3XPA")).status_code == 422
    assert reg_s(synced, verifier_hash=VHASH, verifier_salt=SALT, verifier_iterations=600_000).status_code == 422
    monkeypatch.delenv(ne.SIGNING_ENV_KEY)
    assert reg_s(synced).status_code == 503


def test_server_secret_keeps_the_caps_and_the_app_proof(synced, server_key, signing, monkeypatch):
    assert reg_s(synced, signature="0" * 64).status_code == 403
    monkeypatch.setattr(ne, "LOCKS_PER_HOUR_ALL", 1)
    assert reg_s(synced).status_code == 201
    assert reg_s(synced, lock_id="K7QM3XPB").status_code == 429


@pytest.fixture
def counted_pbkdf2(monkeypatch):
    import time
    real = ne.hashlib.pbkdf2_hmac
    calls = []

    def slow(*a, **kw):
        calls.append(a)
        time.sleep(0.3)
        return real(*a, **kw)
    monkeypatch.setattr(ne.hashlib, "pbkdf2_hmac", slow)
    return calls


def test_concurrent_registrations_of_one_lock_hash_once(synced, server_key, signing, counted_pbkdf2):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        rs = list(pool.map(lambda _: reg_s(synced), range(4)))
    assert [r.status_code for r in rs] == [201] * 4
    assert len({r.text for r in rs}) == 1
    assert len(counted_pbkdf2) == 1
    assert synced.portal.call(get_store().incr, f"nexit:dev:{DEVICE}", 3600) == 2


def test_replay_of_a_stored_lock_does_not_hash_again(synced, server_key, signing, counted_pbkdf2):
    first = reg_s(synced).json()
    assert len(counted_pbkdf2) == 1
    assert reg_s(synced).json() == first
    assert len(counted_pbkdf2) == 1


def test_restarting_the_app_does_not_burn_the_device_cap(synced, server_key, signing):
    for _ in range(25):
        assert reg_s(synced).status_code == 201
    assert reg_s(synced, lock_id="K7QM3XPB").status_code == 201


def test_gate_answers_503_with_retry_after_when_the_queue_is_full(synced, server_key, signing, monkeypatch, counted_pbkdf2):
    monkeypatch.setattr(ne, "_waiting", ne.WAITING_PER_CORE * ne._cores())
    r = reg_s(synced)
    assert r.status_code == 503 and r.headers["retry-after"] == "2" and r.headers["cache-control"] == "no-store"
    assert counted_pbkdf2 == []
    assert synced.portal.call(get_store().incr, f"nexit:dev:{DEVICE}", 3600) == 1
    monkeypatch.setattr(ne, "_waiting", 0)
    assert reg_s(synced).status_code == 201


def test_school_net_exempts_server_secret_registrations_from_the_ip_cap(synced, server_key, signing, monkeypatch):
    synced.app.state.settings.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(ne, "client_ip", lambda request: "10.1.2.3")
    asyncio.run(get_store().incrby("nexit:ip:10.1.2.3", ne.LOCKS_PER_IP_HOUR + 5, ne.HOUR_S))
    assert reg_s(synced, lock_id=lid("G", 0), device="dev-school-0003").status_code == 201
    asyncio.run(get_store().incrby("nexit:ip:10.1.2.3", ne.LOCKS_PER_SCHOOL_IP_HOUR, ne.HOUR_S))
    assert reg_s(synced, lock_id=lid("G", 1), device="dev-school-0004").status_code == 429


def test_one_ip_cannot_use_more_than_its_share_of_the_global_cap(synced, server_key, signing, monkeypatch):
    synced.app.state.settings.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(ne, "client_ip", lambda request: "10.1.2.3")
    monkeypatch.setattr(ne, "LOCKS_PER_HOUR_ALL", 4)
    monkeypatch.setattr(ne, "LOCKS_IP_SHARE_PERCENT", 50)
    codes = [reg_s(synced, lock_id=lid("H", i), device=f"dev-share-{i:04d}").status_code for i in range(3)]
    assert codes == [201, 201, 429]
    monkeypatch.setattr(ne, "client_ip", lambda request: "10.9.9.9")
    assert reg_s(synced, lock_id=lid("H", 9), device="dev-share-0009").status_code == 201


def test_two_concurrent_first_requests_count_and_hash_once(synced, server_key, signing, counted_pbkdf2):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        rs = list(pool.map(lambda _: reg_s(synced, lock_id=lid("J", 0), device="dev-race-0001"), range(2)))
    assert [r.status_code for r in rs] == [201, 201]
    assert len(counted_pbkdf2) == 1
    assert synced.portal.call(get_store().incr, "nexit:ip:testclient", 3600) == 2
    assert synced.portal.call(get_store().incr, "nexit:all", 3600) == 2


# F-04: device-key proof, per-key caps, separate legacy bucket
def dk_body(key, lock_id, device, **over):
    msg = ne.lock_message(lock_id, device)
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    sig = key.sign(device_key.DOMAIN + msg.encode(), ec.ECDSA(hashes.SHA256()))
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    body = {"lock_id": lock_id, "device": device, "server_secret": True, "signature": ac.sign(APP_KEY, msg),
            "device_pub": b64(der), "device_sig": b64(sig)}
    body.update(over)
    return body


def new_key():
    return ec.generate_private_key(ec.SECP256R1())


def test_valid_device_proof_gets_201_when_the_legacy_bucket_is_full(synced, server_key, signing, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_HOUR_ALL", 2)
    monkeypatch.setattr(ne, "LOCKS_IP_SHARE_PERCENT", 100)
    assert [reg_s(synced, lock_id=lid("R", i), device=f"dev-leg-{i:04d}").status_code for i in range(3)] == [201, 201, 429]
    r = synced.post(LOCKS, json=dk_body(new_key(), lid("R", 9), "dev-keyed-0001"))
    assert r.status_code == 201
    assert synced.post(LOCKS, json=dk_body(new_key(), lid("R", 8), "dev-keyed-0002")).status_code == 201


def test_one_key_many_device_strings_hits_the_key_cap_not_the_ip_cap(synced, server_key, signing, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_KEY_HOUR", 5)
    k = new_key()
    codes = [synced.post(LOCKS, json=dk_body(k, lid("M", i), f"dev-self-{i:04d}")).status_code for i in range(7)]
    assert codes == [201] * 5 + [429, 429]
    assert synced.portal.call(get_store().incr, "nexit:ip:testclient", 3600) == 1
    assert synced.post(LOCKS, json=dk_body(new_key(), lid("M", 50), "dev-self-0050")).status_code == 201


def test_new_keys_are_bounded_per_ip(synced, server_key, signing, monkeypatch):
    monkeypatch.setattr(ne, "NEW_KEYS_PER_IP_HOUR", 3)
    codes = [synced.post(LOCKS, json=dk_body(new_key(), lid("N", i), f"dev-mint-{i:04d}")).status_code for i in range(5)]
    assert codes == [201, 201, 201, 429, 429]


def test_a_rejected_new_key_stays_subject_to_the_ip_limit_when_it_retries(synced, server_key, signing, monkeypatch):
    # An IP that used up its new-key allowance cannot get a key in by simply retrying the rejected request.
    monkeypatch.setattr(ne, "NEW_KEYS_PER_IP_HOUR", 2)
    assert [synced.post(LOCKS, json=dk_body(new_key(), lid("T", i), f"dev-ipfull-{i:03d}")).status_code for i in range(2)] == [201, 201]
    rejected = new_key()
    for attempt in range(4):
        assert synced.post(LOCKS, json=dk_body(rejected, lid("T", 10 + attempt), f"dev-retry-{attempt:03d}")).status_code == 429
    # A key that was admitted before the limit was reached keeps working (its own per-key cap applies).
    monkeypatch.setattr(ne, "NEW_KEYS_PER_IP_HOUR", 100)
    ok = new_key()
    assert synced.post(LOCKS, json=dk_body(ok, lid("T", 30), "dev-admit-000")).status_code == 201
    monkeypatch.setattr(ne, "NEW_KEYS_PER_IP_HOUR", 0)
    assert synced.post(LOCKS, json=dk_body(ok, lid("T", 31), "dev-admit-001")).status_code == 201


def test_keyed_global_safety_valve(synced, server_key, signing, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_HOUR_ALL_KEYED", 2)
    monkeypatch.setattr(ne, "LOCKS_IP_SHARE_PERCENT", 100)
    codes = [synced.post(LOCKS, json=dk_body(new_key(), lid("S", i), f"dev-valve-{i:04d}")).status_code for i in range(3)]
    assert codes == [201, 201, 429]


def test_invalid_device_proof_is_403(synced, server_key, signing):
    k = new_key()
    good = dk_body(k, lid("P", 0), "dev-bad-0001")
    assert synced.post(LOCKS, json={**good, "device_sig": dk_body(new_key(), lid("P", 0), "dev-bad-0001")["device_sig"]}).status_code == 403
    assert synced.post(LOCKS, json={**good, "device": "dev-bad-0002"}).status_code == 403
    only_pub = {k_: v for k_, v in good.items() if k_ != "device_sig"}
    assert synced.post(LOCKS, json=only_pub).status_code == 403
    assert synced.post(LOCKS, json={**good, "signature": "0" * 64}).status_code == 403
    assert synced.post(LOCKS, json={k_: v for k_, v in good.items() if k_ != "signature"}).status_code == 422


def test_one_ip_cannot_drain_the_keyed_bucket(synced, server_key, signing, monkeypatch):
    monkeypatch.setattr(ne, "LOCKS_PER_HOUR_ALL_KEYED", 10)
    monkeypatch.setattr(ne, "LOCKS_IP_SHARE_PERCENT", 50)
    codes = [synced.post(LOCKS, json=dk_body(new_key(), lid("U", i), f"dev-share-{i:04d}")).status_code for i in range(7)]
    assert codes == [201] * 5 + [429, 429]          # this IP gets half of the keyed bucket
    monkeypatch.setattr(ne, "client_ip", lambda request: "10.9.9.9")
    assert synced.post(LOCKS, json=dk_body(new_key(), lid("U", 20), "dev-share-0020")).status_code == 201


def test_legacy_without_proof_still_201_even_when_a_key_is_required(synced, server_key, signing):
    assert reg_s(synced, lock_id=lid("Q", 0), device="dev-old-0001").status_code == 201
    assert reg(synced, server_key, lock_id=lid("Q", 1), device="dev-old-0002").status_code == 201
    synced.app.state.settings.require_device_key = True
    # Locking is never blocked by a missing device key (a computer whose key is unavailable must still lock).
    assert reg_s(synced, lock_id=lid("Q", 2), device="dev-old-0003").status_code == 201
    assert synced.post(LOCKS, json=dk_body(new_key(), lid("Q", 3), "dev-new-0003")).status_code == 201

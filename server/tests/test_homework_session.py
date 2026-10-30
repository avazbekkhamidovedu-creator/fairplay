import re
import time
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.homework_remember import HomeworkRemember, hash_token
from app.models import User
from app.security import HOMEWORK_SCOPE, homework_remembered, homework_times, make_token, parse_scoped_token
from app.timeutil import utcnow

from .test_dkuoj_bridge import db
from .test_homework_login import (ME, SECRET, bearer, fake, hw, oj_env, settings_for,  # noqa: F401
                                  sign_in, all_api_routes)

RENEW = "/api/homework/session/renew"
CREATE, EXCHANGE = "/api/homework/remember/create", "/api/homework/remember/exchange"
FORGET, FORGET_ALL = "/api/homework/remember/forget", "/api/homework/remember/forget-all"
GENERIC = "This device is no longer remembered. Sign in again."


def signed_in(c, fake, netid="ab123"):
    r = sign_in(c, fake, netid)[2]
    assert r.status_code == 200, r.text
    return r.json()


def uid_of(c, netid="ab123"):
    return db(c, lambda s: s.execute(select(User.id).where(User.netid == netid))).scalar_one()


def token_for(c, login_at, exp=None, netid="ab123"):
    u = db(c, lambda s: s.execute(select(User).where(User.netid == netid))).scalar_one()
    exp = exp if exp is not None else int(time.time()) + 600
    return make_token(SECRET, u.id, "student", 0, version=u.token_version or 0, scope=HOMEWORK_SCOPE,
                      login_at=int(login_at), exp=exp)


def remember(c, tok):
    r = c.post(CREATE, json={}, headers=bearer(tok))
    assert r.status_code == 200, r.text
    return r.json()["remember_token"]


def write(c, stmt):
    async def go(s):
        await s.execute(stmt)
        await s.commit()
    db(c, go)


def rows(c):
    return db(c, lambda s: s.execute(select(HomeworkRemember).order_by(HomeworkRemember.created_at))).scalars().all()


# ---------------------------------------------------------------- idle and absolute expiry
def test_login_expiry_is_idle_and_absolute(hw, fake):
    body = signed_in(hw, fake)
    exp, login_at = homework_times(SECRET, body["token"])
    assert abs(login_at - time.time()) < 5 and exp - login_at == 1800
    assert body["idle_seconds"] == 1800
    assert body["absolute_expires_at"] > body["expires_at"]


def test_idle_token_expires(hw, fake):
    signed_in(hw, fake)
    old = token_for(hw, time.time() - 100, exp=int(time.time()) - 1)
    assert hw.get(ME, headers=bearer(old)).status_code == 401
    assert hw.post(RENEW, headers=bearer(old)).status_code == 401


def test_renew_keeps_login_at_and_extends_idle(hw, fake):
    body = signed_in(hw, fake)
    login_at = int(time.time()) - 3000
    tok = token_for(hw, login_at, exp=int(time.time()) + 60)
    r = hw.post(RENEW, headers=bearer(tok))
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"token", "expires_at", "absolute_expires_at"}
    exp, la = homework_times(SECRET, out["token"])
    assert la == login_at and abs(exp - (time.time() + 1800)) < 10
    assert hw.get(ME, headers=bearer(out["token"])).json()["absolute_expires_at"] == out["absolute_expires_at"]
    assert parse_scoped_token(SECRET, out["token"])[3] == HOMEWORK_SCOPE
    assert body["token"] != out["token"]


def test_exp_never_passes_the_four_hour_cap(hw, fake):
    signed_in(hw, fake)
    login_at = int(time.time()) - 4 * 3600 + 120
    r = hw.post(RENEW, headers=bearer(token_for(hw, login_at)))
    assert r.status_code == 200
    exp, la = homework_times(SECRET, r.json()["token"])
    assert la == login_at and exp == login_at + 4 * 3600
    assert exp - time.time() < 130


def test_renew_stops_at_the_cap(hw, fake):
    signed_in(hw, fake)
    tok = token_for(hw, time.time() - 4 * 3600 - 5)
    assert hw.post(RENEW, headers=bearer(tok)).status_code == 401


def test_renew_refuses_other_tokens(hw, fake):
    from .conftest import login
    assert hw.post(RENEW).status_code == 401
    assert hw.post(RENEW, headers=login(hw, "alice")).status_code == 403


def test_renew_rate_limit(hw, fake):
    signed_in(hw, fake)
    tok = bearer(token_for(hw, time.time()))
    codes = [hw.post(RENEW, headers=tok).status_code for _ in range(13)]
    assert codes == [200] * 12 + [429]


def test_old_homework_token_without_login_at_still_parses(hw, fake):
    signed_in(hw, fake)
    u = uid_of(hw)
    old = make_token(SECRET, u, "student", 8 * 3600, scope=HOMEWORK_SCOPE)
    assert hw.get(ME, headers=bearer(old)).status_code == 200
    exp, la = homework_times(SECRET, old)
    assert exp - la == 8 * 3600


# ---------------------------------------------------------------- remember: create and exchange
def test_create_shape_and_hashed_at_rest(hw, fake):
    tok = signed_in(hw, fake)["token"]
    r = hw.post(CREATE, json={"label": "  My   laptop " + "x" * 80}, headers=bearer(tok))
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {"remember_token", "expires_at"}
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", out["remember_token"])
    [row] = rows(hw)
    assert row.token_hash == hash_token(out["remember_token"]) and out["remember_token"] not in str(row.__dict__)
    assert row.label.startswith("My laptop x") and len(row.label) <= 40
    assert row.expires_at - row.created_at == timedelta(days=14) == row.absolute_expires_at - row.created_at
    assert hw.post(CREATE, json={}).status_code == 401


def test_remember_token_is_not_in_the_database_in_clear(hw, fake):
    import sqlite3
    tok = signed_in(hw, fake)["token"]
    rt = remember(hw, tok)
    path = str(hw.app.state.settings.database_url).split("///", 1)[1]
    con = sqlite3.connect(path)
    dump = "\n".join(con.iterdump())
    con.close()
    assert rt not in dump and hash_token(rt) in dump


def test_exchange_rotates_and_issues_a_fresh_session(hw, fake):
    tok = signed_in(hw, fake)["token"]
    rt = remember(hw, tok)
    first = rows(hw)[0]
    r = hw.post(EXCHANGE, json={"remember_token": rt})
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"token", "expires_at", "absolute_expires_at", "remember_token", "remember_expires_at",
                        "name", "netid"}
    assert out["netid"] == "ab123" and out["name"] == "Ada Byron" and out["remember_token"] != rt
    assert out["remember_expires_at"].startswith(first.expires_at.isoformat(timespec="milliseconds"))
    exp, la = homework_times(SECRET, out["token"])
    assert abs(la - time.time()) < 5
    assert hw.get(ME, headers=bearer(out["token"])).status_code == 200
    a, b = rows(hw)
    assert a.replaced_by == b.id and a.family_id == b.family_id and b.replaced_by is None
    assert b.absolute_expires_at == a.absolute_expires_at
    r2 = hw.post(EXCHANGE, json={"remember_token": out["remember_token"]})
    assert r2.status_code == 200 and r2.json()["remember_token"] not in (rt, out["remember_token"])


def test_reuse_of_a_rotated_token_revokes_the_family(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    new = hw.post(EXCHANGE, json={"remember_token": rt}).json()["remember_token"]
    write(hw, update(HomeworkRemember).values(last_used_at=utcnow() - timedelta(seconds=11)))
    r = hw.post(EXCHANGE, json={"remember_token": rt})
    assert r.status_code == 401 and r.json()["detail"] == GENERIC
    r = hw.post(EXCHANGE, json={"remember_token": new})
    assert r.status_code == 401 and r.json()["detail"] == GENERIC
    assert all(x.revoked_at is not None for x in rows(hw))


def test_reuse_within_ten_seconds_is_refused_without_revoking(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    out = hw.post(EXCHANGE, json={"remember_token": rt}).json()
    r = hw.post(EXCHANGE, json={"remember_token": rt})
    assert r.status_code == 401 and r.json() == {"detail": GENERIC}
    assert all(x.revoked_at is None for x in rows(hw)) and len(rows(hw)) == 2
    assert hw.post(EXCHANGE, json={"remember_token": out["remember_token"]}).status_code == 200


def test_grace_ends_after_the_successor_is_used(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    new = hw.post(EXCHANGE, json={"remember_token": rt}).json()["remember_token"]
    assert hw.post(EXCHANGE, json={"remember_token": new}).status_code == 200
    assert hw.post(EXCHANGE, json={"remember_token": rt}).status_code == 401
    assert all(x.revoked_at is not None for x in rows(hw))


def test_parallel_exchange_loser_does_not_revoke_the_family(hw, fake, monkeypatch):
    from app import homework_remember as rem
    rt = remember(hw, signed_in(hw, fake)["token"])
    real = rem.rotate

    async def second_wins(session, row):
        await real(session, row)
        return None
    monkeypatch.setattr(rem, "rotate", second_wins)
    r = hw.post(EXCHANGE, json={"remember_token": rt})
    assert r.status_code == 401 and r.json()["detail"] == GENERIC
    assert all(x.revoked_at is None for x in rows(hw))


def test_unknown_expired_revoked_all_answer_the_same_401(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    bad = [hw.post(EXCHANGE, json={"remember_token": "A" * 43}), hw.post(EXCHANGE, json={"remember_token": "short"})]
    write(hw, update(HomeworkRemember).values(expires_at=utcnow() - timedelta(seconds=1)))
    bad.append(hw.post(EXCHANGE, json={"remember_token": rt}))
    for r in bad:
        assert r.status_code == 401 and r.json()["detail"] == GENERIC


def test_family_expiry_is_never_extended(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    start = rows(hw)[0].absolute_expires_at
    for _ in range(3):
        rt = hw.post(EXCHANGE, json={"remember_token": rt}).json()["remember_token"]
    assert {x.absolute_expires_at for x in rows(hw)} == {start}


def test_exchange_rechecks_classes(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    fake.users["ab123"]["classes"] = []
    r = hw.post(EXCHANGE, json={"remember_token": rt})
    assert r.status_code == 403
    fake.users["ab123"]["classes"] = [{"id": 7, "name": "CS 101"}]
    assert hw.post(EXCHANGE, json={"remember_token": rt}).status_code == 200


def test_exchange_refuses_teacher_account(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    write(hw, update(User).where(User.netid == "ab123").values(role="teacher"))
    assert hw.post(EXCHANGE, json={"remember_token": rt}).status_code == 403


def test_exchange_after_sign_out_everywhere_still_works_with_new_version(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    write(hw, update(User).where(User.netid == "ab123").values(token_version=3))
    from app.security import forget_user_cache
    hw.portal.call(forget_user_cache, uid_of(hw))
    out = hw.post(EXCHANGE, json={"remember_token": rt}).json()
    assert hw.get(ME, headers=bearer(out["token"])).status_code == 200


def test_sign_out_everywhere_revokes_remember_me(hw, fake):
    tok = signed_in(hw, fake)["token"]
    a, b = remember(hw, tok), remember(hw, tok)
    assert hw.post("/api/auth/logout", headers=bearer(tok)).status_code == 200
    for t in (a, b):
        r = hw.post(EXCHANGE, json={"remember_token": t})
        assert r.status_code == 401 and r.json()["detail"] == GENERIC
    assert all(x.revoked_at is not None for x in rows(hw))
    fresh = signed_in(hw, fake)["token"]
    assert hw.post(EXCHANGE, json={"remember_token": remember(hw, fresh)}).status_code == 200


def test_admin_version_bump_revokes_remember_me(hw, fake):
    from app.routes_auth import bump_token_version
    rt = remember(hw, signed_in(hw, fake)["token"])
    uid = uid_of(hw)

    async def go(s):
        await bump_token_version(s, uid)
    db(hw, go)
    assert hw.post(EXCHANGE, json={"remember_token": rt}).status_code == 401


def test_session_from_exchange_cannot_create_a_remember_key(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    out = hw.post(EXCHANGE, json={"remember_token": rt}).json()
    for tok in (out["token"], hw.post(RENEW, headers=bearer(out["token"])).json()["token"]):
        assert parse_scoped_token(SECRET, tok)[3] == HOMEWORK_SCOPE and homework_times(SECRET, tok)
        r = hw.post(CREATE, json={}, headers=bearer(tok))
        assert r.status_code == 403 and r.json()["detail"] == "Sign in with DKU OJ to remember this device"
        assert hw.get(ME, headers=bearer(tok)).status_code == 200
    assert len(rows(hw)) == 2


def test_fresh_login_and_its_renewal_can_still_create(hw, fake):
    tok = signed_in(hw, fake)["token"]
    renewed = hw.post(RENEW, headers=bearer(tok)).json()["token"]
    assert not homework_remembered(SECRET, tok) and not homework_remembered(SECRET, renewed)
    assert hw.post(CREATE, json={}, headers=bearer(renewed)).status_code == 200


def test_remembered_claim_is_signed(hw, fake):
    tok = make_token(SECRET, 1, "student", 0, scope=HOMEWORK_SCOPE, login_at=int(time.time()),
                     exp=int(time.time()) + 60, remembered=True)
    assert homework_remembered(SECRET, tok)
    head, sig = tok.split(".")
    assert parse_scoped_token(SECRET, head + "x." + sig) is None
    assert parse_scoped_token(SECRET, make_token(SECRET, 1, "student", 60, scope="ex1")) is not None


def test_sixth_device_revokes_the_oldest(hw, fake):
    tok = signed_in(hw, fake)["token"]
    tokens = [remember(hw, tok) for _ in range(6)]
    assert hw.post(EXCHANGE, json={"remember_token": tokens[0]}).status_code == 401
    for t in tokens[1:]:
        assert hw.post(EXCHANGE, json={"remember_token": t}).status_code == 200
    active = [x for x in rows(hw) if x.revoked_at is None and x.replaced_by is None]
    assert len(active) == 5


def test_exchange_rate_limits(hw, fake):
    codes = [hw.post(EXCHANGE, json={"remember_token": "A" * 43}).status_code for _ in range(21)]
    assert codes == [401] * 20 + [429]


def test_exchange_rate_limit_per_user(hw, fake, monkeypatch):
    from app import routes_homework
    ip = iter(range(1, 100))
    monkeypatch.setattr(routes_homework, "client_ip", lambda request: f"10.1.{next(ip)}.1")
    rt = remember(hw, signed_in(hw, fake)["token"])
    codes = []
    for _ in range(11):
        r = hw.post(EXCHANGE, json={"remember_token": rt})
        codes.append(r.status_code)
        if r.status_code == 200:
            rt = r.json()["remember_token"]
    assert codes == [200] * 10 + [429]


# ---------------------------------------------------------------- forget
def test_forget_revokes_the_family_and_is_quiet_for_unknown(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    rt2 = hw.post(EXCHANGE, json={"remember_token": rt}).json()["remember_token"]
    assert hw.post(FORGET, json={"remember_token": rt2}).json() == {"ok": True}
    assert hw.post(EXCHANGE, json={"remember_token": rt2}).status_code == 401
    assert hw.post(FORGET, json={"remember_token": "A" * 43}).json() == {"ok": True}


def test_forget_all(hw, fake):
    tok = signed_in(hw, fake)["token"]
    a, b = remember(hw, tok), remember(hw, tok)
    assert hw.post(FORGET_ALL).status_code == 401
    assert hw.post(FORGET_ALL, headers=bearer(tok)).json() == {"ok": True, "revoked": 2}
    for t in (a, b):
        assert hw.post(EXCHANGE, json={"remember_token": t}).status_code == 401
    assert hw.post(FORGET_ALL, headers=bearer(tok)).json() == {"ok": True, "revoked": 0}


# ---------------------------------------------------------------- scope and switch
def test_remember_token_is_not_a_bearer_token(hw, fake):
    rt = remember(hw, signed_in(hw, fake)["token"])
    for path in (ME, "/api/me", "/api/student/exams", "/api/exams"):
        assert hw.get(path, headers=bearer(rt)).status_code == 401
    assert hw.post(RENEW, headers=bearer(rt)).status_code == 401


def test_homework_token_still_refused_on_exam_routes_after_renew(hw, fake):
    signed_in(hw, fake)
    tok = bearer(hw.post(RENEW, headers=bearer(token_for(hw, time.time()))).json()["token"])
    assert hw.get("/api/student/exams", headers=tok).status_code == 403
    assert hw.post("/api/student/exams/x/start", json={}, headers=tok).status_code == 403


def test_new_routes_exist_and_are_not_student_routes(hw):
    paths = {r.path for r in all_api_routes(hw.app.routes)}
    assert {RENEW, CREATE, EXCHANGE, FORGET, FORGET_ALL} <= paths


def test_off_all_routes_404(client):
    for path in (RENEW, CREATE, EXCHANGE, FORGET, FORGET_ALL):
        assert client.post(path, json={"remember_token": "A" * 43}).status_code == 404


def test_tokens_are_not_logged(hw, fake, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    body = signed_in(hw, fake)
    rt = remember(hw, body["token"])
    out = hw.post(EXCHANGE, json={"remember_token": rt}).json()
    hw.post(EXCHANGE, json={"remember_token": rt})
    for secret in (body["token"], rt, out["remember_token"], out["token"]):
        assert secret not in caplog.text


# ---------------------------------------------------------------- login/start limits
START = "/api/homework/login/start"


def test_start_limit_uses_the_ipv6_64_prefix(hw, monkeypatch):
    from app import routes_homework
    n = iter(range(1, 1000))
    monkeypatch.setattr(routes_homework, "client_ip", lambda request: f"2001:db8:1:2:{next(n):x}::1")
    codes = [hw.post(START, json={}).status_code for _ in range(21)]
    assert codes == [200] * 20 + [429]
    monkeypatch.setattr(routes_homework, "client_ip", lambda request: "2001:db8:1:3::1")
    assert hw.post(START, json={}).status_code == 200


def test_start_global_cap(hw, monkeypatch):
    from app import routes_homework
    n = iter(range(1, 1000))
    monkeypatch.setattr(routes_homework, "START_GLOBAL_PER_MIN", 5)
    monkeypatch.setattr(routes_homework, "client_ip", lambda request: f"10.{next(n)}.0.1")
    codes = [hw.post(START, json={}).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2


def test_global_cap_is_far_above_a_class(hw):
    from app import routes_homework
    assert routes_homework.START_GLOBAL_PER_MIN >= 4 * 300

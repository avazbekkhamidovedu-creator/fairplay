"""DKU OJ bridge v2 (docs/dkuoj-bridge-v2.md): signed calls both ways, the DKU OJ ->
FairPlay endpoints, class logins and the student set-up. Against a FAKE DKU OJ
(httpx.MockTransport); nothing here talks to a real DKU OJ."""
import json
import os
import re
import time
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.admission import _exempt, is_priority
from app.integrations.dkuoj import client as ojclient, signing, sync as ojsync
from app.main import create_app
from app.timeutil import iso, utcnow

from .conftest import login
from .test_dkuoj_bridge import KEY, OJ, TOKEN, FakeOJ, db, deliveries, fp_signed, oj_settings, run

VECTORS = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "dkuoj-bridge-v2-vectors.json")
V1 = "/api/integrations/dkuoj/v1"
CLASS_ID = 31
GENERIC = "not on the class list"


class RosterOJ(FakeOJ):
    """FakeOJ plus GET classes/<id>/roster (v2 §2)."""

    def __init__(self):
        super().__init__()
        self.class_students = [
            {"username": "bl642", "first_name": "Bob", "last_name": "Li"},
            {"username": "ep530", "first_name": "Élodie", "last_name": "Paré  Martin"},
            {"username": "alice", "first_name": "Alice", "last_name": "Chen"},
        ]
        self.roster = self.roster + [{"username": "bl642", "first_name": "Bob", "last_name": "Li",
                                      "email": "bl@x.edu", "extra_time_seconds": 0}]
        self.roster_down = False
        self.roster_calls = 0

    def handle(self, req):
        path = urlsplit(str(req.url)).path
        if path == f"/api/fairplay/v1/classes/{CLASS_ID}/roster":
            self.roster_calls += 1
            if not fp_signed(req):
                return httpx.Response(401, json={"detail": "bad signature"})
            if self.roster_down:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"class": {"id": CLASS_ID, "name": "CS 101 Section A"},
                                             "course": {"id": 12, "name": "CS 101 Programming"},
                                             "students": self.class_students})
        if path.startswith("/api/fairplay/v1/classes/"):
            return httpx.Response(404, json={"detail": "No such class."})
        return super().handle(req)


@pytest.fixture
def fake(monkeypatch):
    f = RosterOJ()
    monkeypatch.setenv("DKUOJ_URL", OJ)
    monkeypatch.setenv("DKUOJ_TOKEN", TOKEN)
    monkeypatch.setattr(ojsync, "BACKGROUND", False)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(f.handle))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)
    yield f
    ojclient.set_transport_factory(None)
    ojclient.set_sleep(None)


@pytest.fixture
def oj(tmp_path, static_dir, fake):
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
        yield c


def call(c, method, path, body=None, acting="teacher", token=TOKEN, direction=signing.OJ_TO_FP,
         now=None, nonce=None, raw=None, headers=None):
    """A signed DKU OJ -> FairPlay request."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    h = signing.sign_headers(token, direction, method, path, data, acting, now=now, nonce=nonce)
    h["Content-Type"] = "application/json"
    h.update(headers or {})
    return c.request(method, path, content=data, headers=h)


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def make_login(c, class_id=CLASS_ID):
    r = call(c, "POST", f"{V1}/classes/{class_id}/login",
             {"class_name": "CS 101 Section A", "course_name": "CS 101 Programming"})
    assert r.status_code == 200, r.text
    return r.json()


def setup_token(c, cl):
    r = c.post("/api/auth/login", json={"username": cl["username"], "password": cl["password"]})
    assert r.status_code == 200, r.text
    return r.json()["setup_token"]


def identify(c, tok, first="Bob", last="Li", netid="bl642"):
    return c.post("/api/setup/identify", json={"first_name": first, "last_name": last, "netid": netid},
                  headers=bearer(tok))


def account(c, ident, username="bobli", password="Correct-horse-9", password2=None):
    return c.post("/api/setup/account", json={"username": username, "password": password,
                                              "password2": password2 if password2 is not None else password},
                  headers=bearer(ident))


def set_up(c, cl, netid="bl642", first="Bob", last="Li", username="bobli", password="Correct-horse-9"):
    r = identify(c, setup_token(c, cl), first, last, netid)
    assert r.status_code == 200, r.text
    r = account(c, r.json()["identity_token"], username, password)
    assert r.status_code == 200, r.text
    return r.json()


def user_row(c, **where):
    from sqlalchemy import select
    from app.models import User

    async def q(s):
        stmt = select(User)
        for k, v in where.items():
            stmt = stmt.where(getattr(User, k) == v)
        return (await s.execute(stmt)).scalar_one_or_none()
    return db(c, q)


# ---------------------------------------------------------------- §1 signing
def test_signing_vectors():
    v = json.load(open(VECTORS))
    assert signing.direction_key(v["token"], signing.OJ_TO_FP).hex() == v["key_oj_to_fp_hex"]
    assert signing.direction_key(v["token"], signing.FP_TO_OJ).hex() == v["key_fp_to_oj_hex"]
    assert len(v["cases"]) >= 4
    for case in v["cases"]:
        args = (case["method"], case["path"], case["timestamp"], case["nonce"], case["acting_user"], case["body"])
        assert signing.canonical(*args) == case["canonical"]
        assert signing.signature(v["token"], case["direction"], *args) == case["signature"]
        h = signing.sign_headers(v["token"], case["direction"], case["method"], case["path"],
                                 case["body"].encode(), case["acting_user"], now=case["timestamp"],
                                 nonce=case["nonce"])
        assert h["X-FP-Signature"] == case["signature"] and h["X-FP-Timestamp"] == str(case["timestamp"])
        assert ("X-FP-Acting-User" in h) == bool(case["acting_user"])


def test_client_signs_every_call_fp_to_oj(oj, fake):
    seen = []
    real = fake.handle

    def spy(req):
        seen.append(req)
        return real(req)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(spy))
    out = run(oj)
    assert out["exams"] == 1 and out["packages"] == 1, out
    assert seen and all(fp_signed(r) for r in seen)
    assert all("authorization" not in r.headers for r in seen)
    assert len({r.headers["x-fp-nonce"] for r in seen}) == len(seen)       # fresh nonce per request
    # signed with the fp->oj key: the oj->fp key does not verify
    r = seen[0]
    other = signing.signature(TOKEN, signing.OJ_TO_FP, r.method, r.url.raw_path.decode(),
                              r.headers["x-fp-timestamp"], r.headers["x-fp-nonce"], "", r.content)
    assert other != r.headers["x-fp-signature"]


def test_signed_calls_are_checked(oj, fake):
    run(oj)
    path = f"{V1}/classes/{CLASS_ID}/status"
    assert call(oj, "GET", path).status_code == 200
    r = oj.get(path)                                                         # unsigned
    assert r.status_code == 401 and r.json() == {"detail": "bad signature"}
    assert oj.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401   # v1 auth is gone
    assert call(oj, "GET", path, direction=signing.FP_TO_OJ).status_code == 401   # other direction's key
    assert call(oj, "GET", path, token="wrong-token-0123456789abcdef0123456789").status_code == 401
    assert call(oj, "GET", path, now=time.time() - 61).status_code == 401    # clock
    assert call(oj, "GET", path, now=time.time() + 61).status_code == 401
    assert call(oj, "GET", path, now=time.time() - 50).status_code == 200
    # replay: the same nonce twice
    nonce = "ab" * 16
    assert call(oj, "GET", path, nonce=nonce).status_code == 200
    assert call(oj, "GET", path, nonce=nonce).status_code == 401
    # tampering: path / query, body, acting user
    h = signing.sign_headers(TOKEN, signing.OJ_TO_FP, "GET", path, b"", "teacher")
    assert oj.get(path + "?x=1", headers=h).status_code == 401
    body = json.dumps({"class_name": "A", "course_name": "B"}).encode()
    h = signing.sign_headers(TOKEN, signing.OJ_TO_FP, "POST", f"{V1}/classes/{CLASS_ID}/login", body, "teacher")
    assert oj.post(f"{V1}/classes/{CLASS_ID}/login", content=body.replace(b"A", b"Z"), headers=h).status_code == 401
    h2 = {**h, "X-FP-Acting-User": "someone-else"}
    assert oj.post(f"{V1}/classes/{CLASS_ID}/login", content=body, headers=h2).status_code == 401
    bad = {**h, "X-FP-Nonce": "not-hex"}
    assert oj.post(f"{V1}/classes/{CLASS_ID}/login", content=body, headers=bad).status_code == 401
    # signed query strings are fine
    assert call(oj, "GET", path + "?page=2&changed_since=2026-10-23T18%3A05%3A48Z").status_code == 200
    # too big: 413 before hashing
    big = b'{"class_name": "' + b"x" * 70000 + b'"}'
    assert call(oj, "POST", f"{V1}/classes/{CLASS_ID}/login", raw=big).status_code == 413
    # not JSON
    assert call(oj, "POST", f"{V1}/classes/{CLASS_ID}/login", raw=b"[1, 2").status_code == 400


def test_bridge_off_is_404(client):
    path = f"{V1}/classes/{CLASS_ID}/status"
    assert call(client, "GET", path).status_code == 404
    assert call(client, "GET", f"{V1}/exams/{KEY}").status_code == 404
    assert client.post("/api/setup/account", json={"username": "a", "password": "b", "password2": "b"}).status_code == 404


def test_admission_counts_setup_but_not_in_priority_lane():
    for p in ("/api/setup/identify", "/api/setup/account", f"{V1}/exams/{KEY}/open"):
        assert _exempt(p) is False
        assert is_priority(p, "POST") is False
    assert _exempt("/api/auth/login") is True


# ---------------------------------------------------------------- class logins
def test_class_login_lifecycle(oj, fake):
    from sqlalchemy import select
    from app.integrations.dkuoj.models import ClassLogin, ClassLoginEvent
    cl = make_login(oj)
    assert re.match(r"^cs101a-[a-z0-9]{4}$", cl["username"]), cl
    assert len(cl["password"]) >= 20 and re.match(r"^[a-z]+-[a-z]+-[a-z]+-[a-z]+-\d{3}$", cl["password"])
    assert cl["expires_at"].endswith("Z")
    rows = db(oj, lambda s: _all(s, select(ClassLogin)))
    assert len(rows) == 1 and rows[0].password_hash.startswith("pbkdf2_sha256$")
    assert all(cl["password"] not in str(getattr(rows[0], col.name)) for col in ClassLogin.__table__.columns)
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert st["login"]["username"] == cl["username"] and st["login"]["used"] == 0
    assert any(ev["kind"] == "login_created" and ev["actor"] == "teacher" for ev in st["recent"])

    # sign-in with it: a set-up token, not a sign-in
    r = oj.post("/api/auth/login", json={"username": cl["username"], "password": cl["password"]})
    assert r.status_code == 200 and "token" not in r.json()
    assert r.json()["class"] == {"name": "CS 101 Section A", "course": "CS 101 Programming"}
    tok = r.json()["setup_token"]
    for url in ("/api/student/exams", "/api/exams", "/api/me"):
        assert oj.get(url, headers=bearer(tok)).status_code == 401, url
    # a normal sign-in token is not a set-up token
    alice = login(oj, "alice")
    assert oj.post("/api/setup/identify", json={"first_name": "A", "last_name": "C", "netid": "alice"},
                   headers=alice).status_code == 401
    # wrong password: the normal answer
    r = oj.post("/api/auth/login", json={"username": cl["username"], "password": "wrong-wrong-wrong-00"})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid username or password"

    # a new login replaces (and at once disables) the old one
    cl2 = make_login(oj)
    assert cl2["username"] != cl["username"]
    r = oj.post("/api/auth/login", json={"username": cl["username"], "password": cl["password"]})
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    assert identify(oj, tok).status_code == 401          # set-up tokens of the old login stop too
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()["login"]["username"] == cl2["username"]
    setup_token(oj, cl2)

    # expiry (14 days)
    async def expire(s):
        row = (await s.execute(select(ClassLogin).where(ClassLogin.username == cl2["username"]))).scalar_one()
        assert row.expires_at - row.created_at == timedelta(days=14)
        row.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    db(oj, expire)
    r = oj.post("/api/auth/login", json={"username": cl2["username"], "password": cl2["password"]})
    assert r.status_code == 401 and r.json()["detail"] == \
        "This class login has expired. Ask your professor for a new one."
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()["login"] is None

    # stop
    cl3 = make_login(oj)
    assert call(oj, "DELETE", f"{V1}/classes/{CLASS_ID}/login").json() == {"ok": True}
    assert call(oj, "DELETE", f"{V1}/classes/{CLASS_ID}/login").json() == {"ok": True}   # idempotent
    assert oj.post("/api/auth/login", json={"username": cl3["username"],
                                            "password": cl3["password"]}).status_code == 401
    kinds = [e.kind for e in db(oj, lambda s: _all(s, select(ClassLoginEvent)))]
    assert kinds.count("login_created") == 3 and kinds.count("login_stopped") == 1


async def _all(s, stmt):
    return list((await s.execute(stmt)).scalars())


# ---------------------------------------------------------------- set-up
def test_setup_flow(oj, fake):
    cl = make_login(oj)
    tok = setup_token(oj, cl)
    r = identify(oj, tok, "  bob ", "LI", "BL642")              # trimmed, case-folded
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "Bob Li" and out["netid"] == "bl642" and out["identity_token"]
    assert r.headers.get("cache-control") == "no-store"
    ident = out["identity_token"]
    # the identity token is not a set-up token or a sign-in
    assert identify(oj, ident).status_code == 401
    assert oj.get("/api/me", headers=bearer(ident)).status_code == 401
    assert account(oj, tok).status_code == 401

    # account rules (the identity token stays usable until an account is made)
    assert account(oj, ident, username="Bob Li").status_code == 400
    assert account(oj, ident, username="ab").status_code == 400
    assert account(oj, ident, password="short").status_code == 400
    assert account(oj, ident, username="bobbybobby", password="bobbybobby").status_code == 400
    assert account(oj, ident, password="bl642").status_code == 400
    # containing the NetID is fine (only equal to it is refused)
    r = account(oj, ident, password="Bl642-and-more", username="bobli")
    assert r.status_code == 200, r.text
    assert user_row(oj, netid="bl642").set_up_at is not None


def test_setup_account_rules_and_success(oj, fake):
    cl = make_login(oj)
    ident = identify(oj, setup_token(oj, cl)).json()["identity_token"]
    assert account(oj, ident, password="Correct-horse-9", password2="Correct-horse-8").status_code == 400
    assert account(oj, ident, password="BL642").status_code == 400             # = NetID
    r = account(oj, ident, username="alice")
    assert r.status_code == 409 and r.json()["detail"] == "That username is taken"
    assert account(oj, ident, username=cl["username"]).status_code == 409      # a class login's name
    r = account(oj, ident)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["role"] == "student" and me["name"] == "Bob Li" and me["username"] == "bobli" and me["token"]
    assert oj.get("/api/me", headers=bearer(me["token"])).json()["username"] == "bobli"
    assert account(oj, ident, username="bobli2").status_code == 401           # single use
    # signs in with the own username from now on
    login(oj, "bobli", "Correct-horse-9")
    u = user_row(oj, netid="bl642")
    assert u.username == "bobli" and u.set_up_at is not None and u.role == "student"
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert st["login"]["used"] == 1
    bob = next(x for x in st["students"] if x["netid"] == "bl642")
    assert bob["set_up"] is True and bob["username"] == "bobli" and bob["set_up_at"]
    assert any(ev["kind"] == "setup_ok" and ev["netid"] == "bl642" for ev in st["recent"])
    # already set up: the generic answer
    r = identify(oj, setup_token(oj, cl))
    assert r.status_code == 400 and GENERIC in r.json()["detail"]
    # an older FairPlay account with its own password, linked to the NetID before, counts as set up
    run(oj)
    assert GENERIC in identify(oj, setup_token(oj, cl), "Alice", "Chen", "alice").json()["detail"]
    # ... and that refusal is not counted as guessing (no per-NetID count, login still active)
    for _ in range(6):
        assert identify(oj, setup_token(oj, cl), "Alice", "Chen", "alice").status_code == 400
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert st["login"] is not None and not any(ev["kind"] == "setup_netid_blocked" for ev in st["recent"])


def test_identify_failures_are_generic(oj, fake):
    cl = make_login(oj)
    tok = setup_token(oj, cl)
    msgs = set()
    for first, last, netid in (("Bob", "Lee", "bl642"), ("Rob", "Li", "bl642"), ("Bob", "Li", "zz999"),
                               ("", "Li", "bl642")):
        r = identify(oj, tok, first, last, netid)
        assert r.status_code == 400, (first, last, netid)
        msgs.add(r.json()["detail"])
    assert msgs == {"This name and NetID are not on the class list, or this NetID is already set up. "
                    "Check them, or ask your professor."}
    # accents, case and spaces are normalised
    r = identify(oj, setup_token(oj, cl), "elodie", "pare martin", "EP530")
    assert r.status_code == 200 and r.json()["netid"] == "ep530" and r.json()["name"] == "Élodie Paré  Martin"
    # a NetID FairPlay never saw gets a new account on set-up
    r = account(oj, r.json()["identity_token"], username="elodie")
    assert r.status_code == 200
    assert user_row(oj, netid="ep530").username == "elodie"


def test_identify_rate_limit_and_lockout(oj, fake):
    cl = make_login(oj)
    tok = setup_token(oj, cl)
    for _ in range(5):
        assert identify(oj, tok, "X", "Y", "bl642").status_code == 400
    r = identify(oj, tok, "Bob", "Li", "bl642")
    assert r.status_code == 429                                  # 5 tries a minute per set-up token
    # 5 failures for one NetID in an hour: that NetID is refused for the hour (same message),
    # without asking DKU OJ; other NetIDs and the class login keep working
    calls = fake.roster_calls
    r = identify(oj, setup_token(oj, cl), "Bob", "Li", " BL642 ")
    assert r.status_code == 400 and GENERIC in r.json()["detail"] and fake.roster_calls == calls
    assert identify(oj, setup_token(oj, cl), "Élodie", "Paré Martin", "ep530").status_code == 200
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert st["login"] is not None
    assert sum(ev["kind"] == "setup_failed" for ev in st["recent"]) == 5
    assert any(ev["kind"] == "setup_netid_blocked" and ev["netid"] == "bl642" for ev in st["recent"])
    # 40 DIFFERENT failing NetIDs in an hour: an attack, the class login is disabled
    t, used = setup_token(oj, cl), 0
    for i in range(1, 39):                                       # bl642 + 38 more = 39 distinct
        if used == 5:
            t, used = setup_token(oj, cl), 0
        assert identify(oj, t, "X", "Y", f"guess{i:02d}").status_code == 400
        used += 1
    assert call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()["login"] is not None
    t = setup_token(oj, cl)
    assert identify(oj, t, "X", "Y", "guess39").status_code == 400      # the 40th NetID
    assert identify(oj, t, "Alice", "Chen", "alice").status_code == 401  # the class login is disabled
    r = oj.post("/api/auth/login", json={"username": cl["username"], "password": cl["password"]})
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert st["login"] is None
    assert any(ev["kind"] == "login_locked" for ev in st["recent"])
    # the professor creates a new one; the blocked NetID is per class login
    assert identify(oj, setup_token(oj, make_login(oj))).status_code == 200


def test_class_login_is_not_locked_by_wrong_passwords(oj, fake):
    """A lab behind one NAT (or one griefer) typing the class login wrong never blocks the
    right password: no per-username counters for class logins; per IP, wrong ones get 429
    after CLASS_LOGIN_IP_FAILS."""
    from app.routes_auth import CLASS_LOGIN_IP_FAILS, USER_FAILS
    cl = make_login(oj)
    assert CLASS_LOGIN_IP_FAILS > USER_FAILS
    for i in range(CLASS_LOGIN_IP_FAILS):
        r = oj.post("/api/auth/login", json={"username": cl["username"], "password": f"wrong-{i}"})
        assert r.status_code == 401, (i, r.text)
    r = oj.post("/api/auth/login", json={"username": cl["username"], "password": "wrong-again"})
    assert r.status_code == 429
    assert setup_token(oj, cl)                                   # the right password still works
    # normal accounts keep their per-username lockout
    for _ in range(10):
        assert oj.post("/api/auth/login", json={"username": "carol", "password": "nope"}).status_code == 401
    assert oj.post("/api/auth/login", json={"username": "carol", "password": "carol123"}).status_code == 429


def test_identify_when_dkuoj_is_down(oj, fake):
    cl = make_login(oj)
    tok = setup_token(oj, cl)
    fake.roster_down = True
    r = identify(oj, tok)
    assert r.status_code == 503 and r.json()["detail"] == \
        "Can't check the class list right now, try again in a minute"
    fake.roster_down = False
    assert identify(oj, tok).status_code == 200                  # not counted as a failure
    assert fake.roster_calls == 2


def test_identify_unknown_class_is_generic(oj, fake):
    r = call(oj, "POST", f"{V1}/classes/99/login", {"class_name": "Other", "course_name": "X"})
    tok = setup_token(oj, r.json())
    r = identify(oj, tok)
    assert r.status_code == 400 and GENERIC in r.json()["detail"]


# ---------------------------------------------------------------- accounts and sync
def test_sync_matches_by_netid(oj, fake):
    run(oj)
    # an older account named like the NetID is linked to it (once)
    alice = user_row(oj, username="alice")
    assert alice.netid == "alice"
    # never-set-up accounts: placeholder username, unusable password
    ax = user_row(oj, netid="ax19")
    assert ax.username == "oj:ax19" and not ax.password_hash.startswith("pbkdf2")
    assert oj.post("/api/auth/login", json={"username": "oj:ax19", "password": "!dkuoj-import"}).status_code == 401
    bob = user_row(oj, netid="bl642")
    assert bob.username == "oj:bl642"
    # bob sets up with his own username; the next sync keeps that account
    set_up(oj, make_login(oj))
    fake.roster[-1]["first_name"] = "Bobby"
    fake.listing_updated = iso(utcnow() + timedelta(seconds=2))
    run(oj)
    again = user_row(oj, netid="bl642")
    assert again.id == bob.id and again.username == "bobli" and again.name == "Bob Li"   # own name kept
    from sqlalchemy import func, select
    from app.models import User
    assert db(oj, lambda s: _scalar(s, select(func.count()).select_from(User).where(User.netid == "bl642"))) == 1
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    by = {x["netid"]: x for x in st["students"]}
    assert by["bl642"]["set_up"] is True and by["bl642"]["via_this_class"] is True
    assert by["ax19"] == {"netid": "ax19", "set_up": False, "username": None, "set_up_at": None,
                          "via_this_class": False}
    assert by["alice"]["set_up"] is True


async def _scalar(s, stmt):
    return (await s.execute(stmt)).scalar()


def test_reset_then_set_up_again_keeps_attempts(oj, fake):
    run(oj)
    cl = make_login(oj)
    me = set_up(oj, cl)
    h = bearer(me["token"])
    e = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    ex = next(x for x in oj.get("/api/student/exams", headers=h).json() if x["source"] == "dkuoj")
    a = oj.post(f"/api/student/exams/{ex['id']}/start", json={"start_code": e["start_code"]}, headers=h)
    assert a.status_code == 200, a.text
    aid = a.json()["attempt_id"]
    # reset: sessions end at once, own username and password gone
    r = call(oj, "POST", f"{V1}/students/bl642/reset", {"class_id": CLASS_ID})
    assert r.json() == {"ok": True}
    assert oj.get("/api/me", headers=h).status_code == 401
    assert oj.post("/api/auth/login", json={"username": "bobli", "password": "Correct-horse-9"}).status_code == 401
    u = user_row(oj, netid="bl642")
    assert u.username == "oj:bl642" and u.set_up_at is None
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert next(x for x in st["students"] if x["netid"] == "bl642")["set_up"] is False
    assert any(ev["kind"] == "student_reset" for ev in st["recent"])
    assert call(oj, "POST", f"{V1}/students/bl642/reset", {"class_id": CLASS_ID}).json() == {"ok": True}
    assert call(oj, "POST", f"{V1}/students/nobody1/reset", {"class_id": CLASS_ID}).json() == {"ok": True}
    assert call(oj, "POST", f"{V1}/students/bl642/reset", {}).status_code == 400
    assert call(oj, "POST", f"{V1}/students/teacher/reset", {"class_id": CLASS_ID}).status_code == 400
    # set up again (even with the same username): the same account, the attempt is still there
    me2 = set_up(oj, cl, username="bobli", password="Another-pass-77")
    assert user_row(oj, netid="bl642").id == u.id
    view = call(oj, "GET", f"{V1}/exams/{KEY}").json()
    bob = next(x for x in view["students"] if x["netid"] == "bl642")
    assert bob["status"] in ("writing", "offline") and bob["set_up"] is True
    from sqlalchemy import select
    from app.models import Attempt
    att = db(oj, lambda s: _scalar(s, select(Attempt).where(Attempt.id == aid)))
    assert att.student_id == u.id
    assert oj.get("/api/me", headers=bearer(me2["token"])).status_code == 200


# ---------------------------------------------------------------- exam control
def test_exam_endpoints(oj, fake):
    # before the first sync: not synced (no 404); POSTs can't act yet
    r = call(oj, "GET", f"{V1}/exams/{KEY}")
    assert r.status_code == 200 and r.json()["synced"] is False
    assert call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).status_code == 409
    run(oj)
    teacher = login(oj, "teacher")
    assert oj.get(f"{V1}/exams/{KEY}").status_code == 401                          # unsigned
    # non-editors: 403 everywhere (also no acting user)
    for method, path, body in (("GET", f"/exams/{KEY}", None), ("POST", f"/exams/{KEY}/open", {"open_to": "all"}),
                               ("POST", f"/exams/{KEY}/close", None), ("POST", f"/exams/{KEY}/add-time", {"minutes": 5}),
                               ("POST", f"/exams/{KEY}/new-code", None), ("POST", f"/exams/{KEY}/approve", {"netid": "alice"}),
                               ("GET", f"/exams/{KEY}/students/alice/report", None)):
        for who in ("alice", "nobody", ""):
            r = call(oj, method, V1 + path, body, acting=who)
            assert r.status_code == 403, (path, who, r.text)
    v = call(oj, "GET", f"{V1}/exams/{KEY}").json()
    assert v["synced"] and v["package_ready"] and v["questions"] == 7 and v["points"] == 100
    assert v["status"] == "draft" and v["start_code"] is None and v["open_to"] == "all"
    assert v["settings"] == {"calculator": "scientific", "open_book": False, "websites": ["docs.python.org"],
                             "website_links": [], "companions": [], "upload_opens_days": 0, "set_in_fairplay": [],
                             "websites_blocked": [{"site": "*.com",            # refused, and shown
                                                   "reason": "'*.com' is not a website name like docs.python.org"}],
                             "strong_lock": True, "resume_needs_approval": True,
                             "review": "answers_and_results"}
    assert v["counts"] == {"roster": 4, "set_up": 2, "not_started": 4, "writing": 0, "offline": 0,
                           "waiting": 0, "handed_in": 0}
    assert {s["netid"] for s in v["students"]} == {"alice", "carol", "ax19", "bl642"}
    assert v["closes_at"] and v["server_time"]
    # sd123 is an editor too (placeholder account from the sync)
    assert call(oj, "GET", f"{V1}/exams/{KEY}", acting="sd123").status_code == 200

    # open for some students only, then for all (idempotent while live)
    assert call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": ["nobody9"]}).status_code == 400
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": ["alice", "carol"]}).json()
    assert v["status"] == "live" and re.match(r"^\d{6}$", v["start_code"]) and v["invigilator_code"]
    assert v["open_to"] == ["alice", "carol"]
    code = v["start_code"]
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    assert v["status"] == "live" and v["open_to"] == "all" and v["start_code"] == code
    # the FairPlay teacher's view agrees (same code path)
    e = next(x for x in oj.get("/api/exams", headers=teacher).json() if x.get("external"))
    assert oj.get(f"/api/exams/{e['id']}/live", headers=teacher).json()["start_code"] == code

    alice = login(oj, "alice")
    a = oj.post(f"/api/student/exams/{e['id']}/start", json={"start_code": code}, headers=alice)
    assert a.status_code == 200, a.text
    aid = a.json()["attempt_id"]
    deadline = a.json()["deadline_at"]
    oj.post(f"/api/student/attempts/{aid}/heartbeat", headers=alice, json={"current_question": 0})
    oj.post(f"/api/student/attempts/{aid}/events", headers=alice,
            json={"events": [{"type": "focus_lost", "detail": "Left the app once (8 s)"}]})
    v = call(oj, "GET", f"{V1}/exams/{KEY}").json()
    row = next(s for s in v["students"] if s["netid"] == "alice")
    assert row["status"] == "writing" and row["questions"] == 7 and row["answered"] == 0
    assert 0 < row["seconds_left"] <= (90 * 60 + 600) and row["name"] == "Alice Chen"
    assert "Left the app once (8 s)" in row["warnings"]
    assert v["counts"]["writing"] == 1 and v["counts"]["not_started"] == 3

    # add time
    for bad in ({"minutes": 0}, {"minutes": 121}, {"minutes": "10"}, {}):
        assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", bad).status_code == 400
    v2 = call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 10}).json()
    row2 = next(s for s in v2["students"] if s["netid"] == "alice")
    assert row2["seconds_left"] >= row["seconds_left"] + 590
    from app.timeutil import parse_iso
    from sqlalchemy import select
    from app.models import Attempt
    att = db(oj, lambda s: _scalar(s, select(Attempt).where(Attempt.id == aid)))
    assert abs((att.deadline_at - parse_iso(deadline)) - timedelta(minutes=10)) < timedelta(seconds=1)

    # new start code
    v = call(oj, "POST", f"{V1}/exams/{KEY}/new-code").json()
    assert v["start_code"] != code and re.match(r"^\d{6}$", v["start_code"])

    # approve a waiting student
    assert call(oj, "POST", f"{V1}/exams/{KEY}/approve", {"netid": "alice"}).status_code == 409   # not waiting
    assert call(oj, "POST", f"{V1}/exams/{KEY}/approve", {"netid": "carol"}).status_code == 404   # not started
    assert call(oj, "POST", f"{V1}/exams/{KEY}/approve", {}).status_code == 400

    async def wait(s):
        x = await s.get(Attempt, aid)
        x.pending_resume, x.pending_since = True, utcnow() - timedelta(minutes=2)
        await s.commit()
    db(oj, wait)
    from app.services import attempt_key
    oj.portal.call(_drop, attempt_key(aid))
    v = call(oj, "GET", f"{V1}/exams/{KEY}").json()
    assert next(s for s in v["students"] if s["netid"] == "alice")["status"] == "waiting"
    assert v["counts"]["waiting"] == 1
    v = call(oj, "POST", f"{V1}/exams/{KEY}/approve", {"netid": "alice"}).json()
    assert next(s for s in v["students"] if s["netid"] == "alice")["status"] in ("writing", "offline")

    # report: integrity events, oldest first
    rep = call(oj, "GET", f"{V1}/exams/{KEY}/students/alice/report").json()
    assert rep["name"] == "Alice Chen" and rep["netid"] == "alice" and rep["started_at"]
    assert rep["handed_in_at"] is None and rep["status"] == "writing"
    kinds = [ev["kind"] for ev in rep["events"]]
    assert "resume_approved" in kinds and all(set(ev) == {"at", "kind", "detail"} for ev in rep["events"])
    assert [ev["at"] for ev in rep["events"]] == sorted(ev["at"] for ev in rep["events"])
    assert call(oj, "GET", f"{V1}/exams/{KEY}/students/carol/report").json()["status"] == "not_started"
    assert call(oj, "GET", f"{V1}/exams/{KEY}/students/zz000/report").status_code == 404

    # close: writers handed in, delivered to DKU OJ through the outbox
    v = call(oj, "POST", f"{V1}/exams/{KEY}/close").json()
    assert v["status"] == "closed" and v["start_code"] is None and v["invigilator_code"] is None
    assert v["counts"]["handed_in"] == 1
    assert deliveries(oj)[aid][0] == "pending"
    assert call(oj, "POST", f"{V1}/exams/{KEY}/close").json()["status"] == "closed"     # idempotent
    rep = call(oj, "GET", f"{V1}/exams/{KEY}/students/alice/report").json()
    assert rep["status"] == "handed_in" and rep["handed_in_at"]
    assert call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 5}).status_code == 409
    run(oj)
    assert deliveries(oj)[aid][0] == "sent"
    assert fake.submissions[next(iter(fake.submissions))]["username"] == "alice"


async def _drop(key):
    from app.store import get_store
    await get_store().delete(key)


def test_delivery_and_results_use_netid(oj, fake):
    run(oj)
    me = set_up(oj, make_login(oj))
    h = bearer(me["token"])
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": ["bl642"]}).json()
    assert v["open_to"] == ["bl642"]
    ex = next(x for x in oj.get("/api/student/exams", headers=h).json() if x["source"] == "dkuoj")
    a = oj.post(f"/api/student/exams/{ex['id']}/start", json={"start_code": v["start_code"]}, headers=h).json()
    oj.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}}, headers=h)
    run(oj)
    body = fake.submissions[a["attempt_id"]]
    assert body["username"] == "bl642"                # DKU OJ's name, not "bobli"
    fake.results = [{"username": "bl642", "released": True, "total": 50, "total_points": 100,
                     "updated_at": iso(utcnow()), "questions": []}]
    fake.end = utcnow() - timedelta(minutes=1)
    fake.listing_updated = iso(utcnow() + timedelta(seconds=3))
    run(oj)
    run(oj)
    from sqlalchemy import select
    from app.models import Attempt
    att = db(oj, lambda s: _scalar(s, select(Attempt).where(Attempt.id == a["attempt_id"])))
    assert (att.external_result or {}).get("total") == 50


# ---------------------------------------------------------------- 2026-10-24 review fixes
def test_signature_timestamp_must_be_ascii_digits(oj, fake):
    """'²' and other Unicode digits pass str.isdigit() but not int(): a 401, never a 500."""
    import asyncio
    path = f"{V1}/classes/{CLASS_ID}/status"
    for ts in ("1²", "١٧٠٠٠٠٠٠٠٠", "１２３", " 12", "1" * 13, "-5", "+5"):
        h = signing.sign_headers(TOKEN, signing.OJ_TO_FP, "GET", path, b"", "teacher")
        h[signing.H_TIMESTAMP] = ts.encode("utf-8")      # sent as raw bytes
        r = oj.get(path, headers=h)
        assert r.status_code == 401, (ts, r.status_code)
        with pytest.raises(signing.BadSignature):
            asyncio.run(signing.verify(TOKEN, signing.OJ_TO_FP, "GET", path, {signing.H_TIMESTAMP: ts,
                        signing.H_NONCE: "ab" * 16, signing.H_SIGNATURE: "0" * 64}, b""))
    assert signing.NONCE_TTL_S == 180 and signing.NONCE_TTL_S > 2 * signing.MAX_SKEW_S


def test_reset_only_for_students_linked_to_the_class(oj, fake):
    run(oj)
    other = CLASS_ID + 1
    # ax19 is on the exam list of class 31's exam only: another class can't reset them
    r = call(oj, "POST", f"{V1}/students/ax19/reset", {"class_id": other})
    assert r.status_code == 403 and "not linked to this class" in r.json()["detail"]
    assert call(oj, "POST", f"{V1}/students/ax19/reset", {"class_id": CLASS_ID}).json() == {"ok": True}
    # set up through class 31's login: via_this_class there, still not resettable from another class
    set_up(oj, make_login(oj))
    assert call(oj, "POST", f"{V1}/students/bl642/reset", {"class_id": other}).status_code == 403
    st = call(oj, "GET", f"{V1}/classes/{other}/status").json()
    assert st["students"] == []
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert {s["netid"]: s["via_this_class"] for s in st["students"]}["bl642"] is True
    assert user_row(oj, netid="bl642").username == "bobli"
    # a teacher's NetID is refused whatever the class
    assert call(oj, "POST", f"{V1}/students/teacher/reset", {"class_id": other}).status_code == 400
    assert call(oj, "POST", f"{V1}/students/sd123/reset", {"class_id": CLASS_ID}).status_code == 400


def test_user_by_netid_fallback_never_lands_on_a_teacher(oj, fake):
    from app.integrations.dkuoj.routes_v2 import _user_by_netid
    from app.models import User

    async def go(s):
        s.add(User(username="prof9", name="Prof", role="teacher", password_hash=ojsync.IMPORTED_PASSWORD,
                   created_at=utcnow()))
        s.add(User(username="stud9", name="Stud", role="student", password_hash=ojsync.IMPORTED_PASSWORD,
                   created_at=utcnow()))
        await s.commit()
        return ((await _user_by_netid(s, "prof9")), (await _user_by_netid(s, "prof9", role="teacher")),
                (await _user_by_netid(s, "stud9")), (await _user_by_netid(s, "stud9", role="teacher")),
                (await _user_by_netid(s, "carol")))      # demo carol: a normal account, never matched
    t_as_student, t_as_teacher, s_as_student, s_as_teacher, carol = db(oj, go)
    assert t_as_student is None and t_as_teacher.username == "prof9"
    assert s_as_student.username == "stud9" and s_as_teacher is None
    assert carol is None
    # approve / report can't reach a teacher account through a NetID either
    run(oj)
    call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"})
    assert call(oj, "POST", f"{V1}/exams/{KEY}/approve", {"netid": "prof9"}).status_code == 404
    assert call(oj, "GET", f"{V1}/exams/{KEY}/students/prof9/report").status_code == 404


def test_sync_never_adopts_a_normal_account(oj, fake):
    """Demo accounts (real passwords, never touched by the bridge) named like DKU OJ users
    stay apart: the NetID gets its own placeholder account. v1 bridge accounts (unusable
    IMPORTED password, or roster / editor rows) are linked as before."""
    from sqlalchemy import update
    from app.models import User
    from app.integrations.dkuoj.models import OjRosterEntry

    async def v1_carol(s):      # what the v1 sync made: username = NetID, unusable password
        await s.execute(update(User).where(User.username == "carol").values(password_hash=ojsync.IMPORTED_PASSWORD))
        await s.commit()
    db(oj, v1_carol)
    run(oj, link_demo=False)
    demo_alice = user_row(oj, username="alice")
    assert demo_alice.netid is None and demo_alice.password_hash.startswith("pbkdf2")
    new_alice = user_row(oj, netid="alice")
    assert new_alice.username == "oj:alice" and new_alice.id != demo_alice.id
    assert user_row(oj, netid="carol").username == "carol"            # v1 import: linked
    teacher = user_row(oj, username="teacher")
    assert teacher.netid is None and user_row(oj, netid="teacher").username == "oj:teacher"
    # the NetID's set-up is open (not "already set up" because of the demo account)
    st = call(oj, "GET", f"{V1}/classes/{CLASS_ID}/status").json()
    assert {s["netid"]: s["set_up"] for s in st["students"]}["alice"] is False
    fake.class_students.append({"username": "legacy1", "first_name": "Leg", "last_name": "Acy"})

    # a v1 account with a real password that the v1 sync had put on an exam roster
    async def v1_legacy(s):
        from app.security import hash_password
        u = User(username="legacy1", name="Leg Acy", role="student", password_hash=hash_password("pw-legacy-12", 1000),
                 created_at=utcnow())
        s.add(u)
        await s.flush()
        exam_id = (await s.execute(__import__("sqlalchemy").select(OjRosterEntry.exam_id))).scalars().first()
        s.add(OjRosterEntry(exam_id=exam_id, user_id=u.id, extra_time_seconds=0))
        await s.commit()
        return u.id
    legacy_id = db(oj, v1_legacy)
    fake.roster.append({"username": "legacy1", "first_name": "Leg", "last_name": "Acy", "email": "l@x.edu",
                        "extra_time_seconds": 0})
    fake.listing_updated = iso(utcnow() + timedelta(seconds=2))
    run(oj, link_demo=False)
    assert user_row(oj, netid="legacy1").id == legacy_id
    r = identify(oj, setup_token(oj, make_login(oj)), "Leg", "Acy", "legacy1")
    assert r.status_code == 400                                           # has its own password: set up
    r = identify(oj, setup_token(oj, make_login(oj)), "Alice", "Chen", "alice")
    assert r.status_code == 200, r.text
    assert account(oj, r.json()["identity_token"], username="alice").status_code == 409   # demo's name
    assert account(oj, r.json()["identity_token"], username="alicechen").status_code == 200
    assert user_row(oj, netid="alice").id == new_alice.id
    login(oj, "alice", "alice123")                                        # the demo account is untouched


def test_add_time_moves_the_close_time_of_a_dkuoj_exam(oj, fake):
    """'Add 10 min' on a DKU OJ exam: the close time (DKU OJ's end time) moves too, so a
    student who starts now gets the minutes, and a sync keeps them."""
    from sqlalchemy import select
    from app.models import Attempt, Exam
    from app import closing
    now = utcnow().replace(microsecond=0)
    fake.start = now - timedelta(minutes=30)
    fake.end = now + timedelta(minutes=20)                # ends before the 90 min time limit
    run(oj)
    v = call(oj, "POST", f"{V1}/exams/{KEY}/open", {"open_to": "all"}).json()
    assert v["closes_at"] == iso(fake.end)
    v = call(oj, "POST", f"{V1}/exams/{KEY}/add-time", {"minutes": 10}).json()
    assert v["closes_at"] == iso(fake.end + timedelta(minutes=10))
    teacher = login(oj, "teacher")
    e = next(x for x in oj.get("/api/exams", headers=teacher).json() if x.get("external"))
    assert e["closes_at"] == iso(fake.end + timedelta(minutes=10))
    # a sync (DKU OJ changed something else) keeps the added minutes
    fake.listing_updated = iso(utcnow() + timedelta(seconds=2))
    run(oj)
    assert call(oj, "GET", f"{V1}/exams/{KEY}").json()["closes_at"] == iso(fake.end + timedelta(minutes=10))
    # carol (no extra time) starts now: her deadline is the moved close time
    carol = login(oj, "carol")
    a = oj.post(f"/api/student/exams/{e['id']}/start", json={"start_code": v["start_code"]}, headers=carol)
    assert a.status_code == 200, a.text
    att = db(oj, lambda s: _scalar(s, select(Attempt).where(Attempt.id == a.json()["attempt_id"])))
    assert att.deadline_at == fake.end + timedelta(minutes=10)
    # the automatic close waits for the moved time (+ the largest extra time, alice's 10 min)
    ex = db(oj, lambda s: _scalar(s, select(Exam).where(Exam.id == e["id"])))

    async def due(at):
        from app.db import get_database
        async with get_database().sessionmaker() as s:
            return await closing.auto_close_due(s, await s.get(Exam, ex.id), at)
    assert oj.portal.call(due, fake.end + timedelta(minutes=15)) is False
    assert oj.portal.call(due, fake.end + timedelta(minutes=21, seconds=1)) is True

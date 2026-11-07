import asyncio
import itertools
import logging
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.integrations.dkuoj import client as ojclient
from app.integrations.dkuoj import sync as ojsync
from app.main import create_app
from app.models import User
from app.security import ADMIN_SCOPE, HOMEWORK_SCOPE, make_token, require_student
from app.store import get_store
from app.timeutil import utcnow

from . import fake_dkuoj_login as fk
from .conftest import login
from .test_dkuoj_bridge import db

SECRET = "test-secret-0123456789abcdef"
START, FINISH, ME = "/api/homework/login/start", "/api/homework/login/finish", "/api/homework/me"
HOSTS = ["authn.example.edu", "idp.example.edu", "api-duo.example.com"]


def settings_for(tmp_path, static_dir, **kw):
    base = dict(database_url=f"sqlite+aiosqlite:///{tmp_path / 'hw.db'}", redis_url=None, secret=SECRET,
                pbkdf2_iterations=1000, require_app=False, app_keys=["test-app-key"], dev=False,
                seed_loadtest_users=0, seed_demo=True, single_process=True, static_dir=str(static_dir),
                uploads_dir=str(tmp_path / "uploads"), public_url="https://exam.test",
                homework_login="dkuoj", homework_login_hosts=list(HOSTS))
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def oj_env(monkeypatch):
    monkeypatch.setenv("DKUOJ_URL", "https://oj.test")
    monkeypatch.setenv("DKUOJ_TOKEN", fk.TOKEN)
    monkeypatch.setenv("DKUOJ_INSTALLATION_ID", fk.INSTALLATION_ID)
    monkeypatch.setenv("FAIRPLAY_CLASS_LOGIN", "1")
    monkeypatch.setenv("DKUOJ_PUBLIC_URL", fk.PUBLIC)
    monkeypatch.setattr(ojsync, "BACKGROUND", False)


@pytest.fixture
def fake(oj_env):
    f = fk.FakeDkuojLogin()
    ojclient.set_transport_factory(lambda base: fk.FakeTransport(f))

    async def no_sleep(_s):
        return None
    ojclient.set_sleep(no_sleep)
    yield f
    ojclient.set_transport_factory(None)
    ojclient.set_sleep(None)


def bind_installation(c):
    import json
    from app.db import get_database
    from app.integrations.dkuoj.isolation import BINDING, canonical_url

    async def go():
        async with get_database().sessionmaker() as s:
            await ojsync.set_state(s, BINDING, json.dumps(
                {"installation_id": fk.INSTALLATION_ID, "url": canonical_url("https://oj.test")}))
    c.portal.call(go)


@pytest.fixture
def hw(tmp_path, static_dir, fake):
    with TestClient(create_app(settings_for(tmp_path, static_dir))) as c:
        bind_installation(c)
        yield c


def browse(fake, start_url):
    """What the web view does: open start_url on the fake DKU OJ, sign in if asked, and read
    the ticket from the address it ends on. Returns (ticket, state)."""
    async def go():
        u = urlsplit(start_url)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake), base_url=fk.PUBLIC) as b:
            target = u.path + "?" + u.query
            for _ in range(5):
                r = await b.get(target)
                assert r.status_code in (200, 302), r.text
                if r.status_code == 200:
                    raise AssertionError("never reached the done page")
                loc = r.headers["location"]
                if loc.startswith("/fairplay/app-login/done#"):
                    frag = parse_qs(loc.split("#", 1)[1])
                    return frag["ticket"][0], frag["state"][0]
                assert loc.startswith("/accounts/login")
                r = await b.post("/accounts/login", content=f"netid={fake.pending}", params={"next": parse_qs(urlsplit(loc).query)["next"][0]})
                assert r.status_code == 302
                target = r.headers["location"]
        raise AssertionError("too many redirects")
    return asyncio.run(go())


def begin(c):
    r = c.post(START, json={})
    assert r.status_code == 200, r.text
    return r.json()


def sign_in(c, fake, netid="ab123"):
    s = begin(c)
    fake.pending = netid
    ticket, state = browse(fake, s["start_url"])
    assert state == s["state"]
    r = c.post(FINISH, json={"state": state, "ticket": ticket})
    return s, ticket, r


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def stored(c, state):
    return c.portal.call(lambda: get_store().get(f"hwl:{state}"))


# ---------------------------------------------------------------- off by default
def test_off_health_null_and_routes_404(client):
    assert client.get("/api/health").json()["homework_login"] is None
    assert client.post(START, json={}).status_code == 404
    assert client.post(FINISH, json={"state": "x", "ticket": "y"}).status_code == 404
    assert client.get(ME, headers=login(client, "alice")).status_code == 404


def test_health_on(hw):
    assert hw.get("/api/health").json()["homework_login"] == {"provider": "dkuoj", "label": "DKU OJ"}


# ---------------------------------------------------------------- start
def test_start_shape(hw):
    s = begin(hw)
    assert set(s) == {"state", "start_url", "allowed_hosts", "finish_url", "resume_paths", "expires_at"}
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", s["state"])
    u = urlsplit(s["start_url"])
    assert f"{u.scheme}://{u.netloc}{u.path}" == fk.PUBLIC + "/fairplay/app-login/"
    q = parse_qs(u.query)
    assert q["state"] == [s["state"]]
    assert s["finish_url"] == fk.PUBLIC + "/fairplay/app-login/done"
    assert s["allowed_hosts"] == ["oj.example.edu"] + HOSTS
    assert s["resume_paths"] == ["/"]
    import json
    saved = json.loads(stored(hw, s["state"]))
    assert fk.challenge_of(saved["verifier"]) == q["challenge"][0]
    assert saved["used"] is False
    assert saved["verifier"] not in str(s)


def test_resume_paths_setting(tmp_path, static_dir, fake):
    with TestClient(create_app(settings_for(tmp_path, static_dir, homework_login_resume_paths=["/", "/problems"]))) as c:
        assert begin(c)["resume_paths"] == ["/", "/problems"]


# ---------------------------------------------------------------- happy path
def test_full_flow_creates_account_and_token(hw, fake, caplog):
    caplog.set_level(logging.DEBUG)
    s, ticket, r = sign_in(hw, fake)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"token", "name", "netid", "expires_at", "absolute_expires_at", "idle_seconds"}
    assert body["idle_seconds"] == 1800
    assert body["name"] == "Ada Byron" and body["netid"] == "ab123"
    assert fake.last_redeem["ticket"] == ticket
    assert fk.challenge_of(fake.last_redeem["verifier"]) == fake.tickets[ticket]["challenge"]
    me = hw.get(ME, headers=bearer(body["token"]))
    assert me.status_code == 200 and me.json() == {"name": "Ada Byron", "netid": "ab123", "expires_at": body["expires_at"],
                                                 "absolute_expires_at": body["absolute_expires_at"]}
    u = db(hw, lambda s_: s_.execute(select(User).where(User.netid == "ab123")))
    row = u.scalar_one()
    assert row.username == "oj:ab123" and row.role == "student" and row.set_up_at is None
    assert not row.password_hash.startswith("pbkdf2")
    for secret in (ticket, s["state"], fake.last_redeem["verifier"]):
        assert secret not in caplog.text


def test_token_is_homework_scoped_and_30_min_idle(hw, fake):
    from app.security import parse_scoped_token
    import time
    _, _, r = sign_in(hw, fake)
    uid, role, _, scope = parse_scoped_token(SECRET, r.json()["token"])
    assert role == "student" and scope == HOMEWORK_SCOPE
    exp = int(__import__("base64").urlsafe_b64decode(r.json()["token"].split(".")[0] + "==").decode().split(":")[2])
    assert abs(exp - (time.time() + 1800)) < 10


def test_existing_account_matched_and_set_up_at_kept(hw, fake):
    when = utcnow() - timedelta(days=3)

    async def make(s):
        s.add(User(username="cdchen", netid="cd456", name="Chen D", role="student",
                   password_hash="pbkdf2_sha256$1$aa$bb", created_at=when, set_up_at=when))
        await s.commit()
    db(hw, make)
    _, _, r = sign_in(hw, fake, "cd456")
    assert r.status_code == 200 and r.json()["netid"] == "cd456"
    rows = db(hw, lambda s: s.execute(select(User).where(User.netid == "cd456"))).scalars().all()
    assert len(rows) == 1 and rows[0].username == "cdchen" and rows[0].set_up_at == when


def test_set_up_at_stays_null_after_sign_in(hw, fake):
    sign_in(hw, fake)
    sign_in(hw, fake)
    rows = db(hw, lambda s: s.execute(select(User).where(User.netid == "ab123"))).scalars().all()
    assert len(rows) == 1 and rows[0].set_up_at is None


# ---------------------------------------------------------------- refusals on finish
def test_ticket_replay(hw, fake):
    _, ticket, r = sign_in(hw, fake)
    assert r.status_code == 200
    s2 = begin(hw)
    assert hw.post(FINISH, json={"state": s2["state"], "ticket": ticket}).status_code == 400


def test_state_replay_never_reaches_the_provider(hw, fake):
    s, ticket, r = sign_in(hw, fake)
    assert r.status_code == 200
    calls = fake.redeem_calls
    again = hw.post(FINISH, json={"state": s["state"], "ticket": ticket})
    assert again.status_code == 400 and fake.redeem_calls == calls


def test_wrong_verifier_ticket(hw, fake):
    s = begin(hw)
    t = fake.mint(s["state"], fk.challenge_of("some other verifier"), "ab123")
    r = hw.post(FINISH, json={"state": s["state"], "ticket": t})
    assert r.status_code == 400


def test_ticket_older_than_60s(hw, fake):
    s = begin(hw)
    fake.pending = "ab123"
    ticket, state = browse(fake, s["start_url"])
    fake.clock = lambda: __import__("time").time() + 61
    assert hw.post(FINISH, json={"state": state, "ticket": ticket}).status_code == 400


def test_state_from_another_start(hw, fake):
    a, b = begin(hw), begin(hw)
    fake.pending = "ab123"
    ticket, _ = browse(fake, a["start_url"])
    assert hw.post(FINISH, json={"state": b["state"], "ticket": ticket}).status_code == 400


def test_unknown_and_malformed_state_and_ticket(hw, fake):
    assert hw.post(FINISH, json={"state": "A" * 43, "ticket": "t"}).status_code == 400
    assert hw.post(FINISH, json={"state": "short", "ticket": "t"}).status_code == 400
    s = begin(hw)
    assert hw.post(FINISH, json={"state": s["state"], "ticket": "nope"}).status_code == 400
    assert hw.post(FINISH, json={"state": s["state"]}).status_code == 422


def test_all_ticket_failures_look_alike(hw, fake):
    msgs = set()
    s = begin(hw)
    msgs.add(hw.post(FINISH, json={"state": "A" * 43, "ticket": "t"}).text)
    msgs.add(hw.post(FINISH, json={"state": s["state"], "ticket": "nope"}).text)
    s = begin(hw)
    t = fake.mint(s["state"], fk.challenge_of("x"), "ab123")
    msgs.add(hw.post(FINISH, json={"state": s["state"], "ticket": t}).text)
    assert len(msgs) == 1


def test_netid_with_no_classes(hw, fake):
    _, _, r = sign_in(hw, fake, "noclass")
    assert r.status_code == 403
    assert db(hw, lambda s: s.execute(select(User).where(User.netid == "noclass"))).scalar_one_or_none() is None


def test_teacher_netid(hw, fake):
    async def make(s):
        s.add(User(username="oj:prof1", netid="prof1", name="Pat Prof", role="teacher",
                   password_hash="!dkuoj-import", created_at=utcnow()))
        await s.commit()
    db(hw, make)
    _, _, r = sign_in(hw, fake, "prof1")
    assert r.status_code == 403


def test_dkuoj_unreachable(hw, fake):
    s = begin(hw)
    fake.pending = "ab123"
    ticket, state = browse(fake, s["start_url"])
    fake.down = True
    r = hw.post(FINISH, json={"state": state, "ticket": ticket})
    assert r.status_code == 503
    assert ticket not in r.text and state not in r.text


def test_classes_lookup_down_after_redeem(hw, fake, monkeypatch):
    from app.integrations.dkuoj import homework_login as dh

    async def boom(self, netid):
        raise dh.HomeworkError(503)
    monkeypatch.setattr(dh.DkuojHomework, "has_classes", boom)
    _, _, r = sign_in(hw, fake)
    assert r.status_code == 503


def test_bad_signature_is_unavailable_not_a_token(hw, fake, monkeypatch):
    monkeypatch.setattr(fk, "TOKEN", "another-token-0123456789abcdef-xyz")
    _, _, r = sign_in(hw, fake)
    assert r.status_code == 503


# ---------------------------------------------------------------- limits
def test_start_rate_limit(hw):
    codes = [hw.post(START, json={}).status_code for _ in range(21)]
    assert codes == [200] * 20 + [429]


def test_finish_rate_limit_per_ip(hw):
    codes = [hw.post(FINISH, json={"state": "A" * 43, "ticket": "t"}).status_code for _ in range(11)]
    assert codes == [400] * 10 + [429]


def test_finish_rate_limit_per_netid(hw, fake, monkeypatch):
    from app import routes_homework
    n = itertools.count(1)
    ip = {"v": "10.0.0.1"}
    monkeypatch.setattr(routes_homework, "client_ip", lambda request: ip["v"])
    codes = []
    for _ in range(21):
        ip["v"] = f"10.0.{next(n)}.9"
        codes.append(sign_in(hw, fake)[2].status_code)
    assert codes == [200] * 20 + [429]


# ---------------------------------------------------------------- token scope
def homework_token(hw, fake):
    _, _, r = sign_in(hw, fake)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def all_api_routes(routes):
    """The app's routes with included routers unpacked (the routers are mounted without a prefix)."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from all_api_routes(r.original_router.routes)
        elif hasattr(r, "routes"):
            yield from all_api_routes(r.routes)


def _needs_student(route):
    def walk(d):
        return any(x.call is require_student or walk(x) for x in d.dependencies)
    return walk(route.dependant)


def test_homework_token_refused_on_every_student_and_exam_route(hw, fake):
    tok = bearer(homework_token(hw, fake))
    open_routes = {"/api/student/code-login", "/api/student/code-login/challenge",
                   "/api/student/code-login/confirm"}
    checked = 0
    for route in all_api_routes(hw.app.routes):
        guarded = _needs_student(route)
        if route.path.startswith("/api/student/") and not guarded:
            assert route.path in open_routes, f"{route.path} is under /api/student/ without require_student"
        if not guarded:
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = hw.request(method, path, headers=tok, json={} if method in ("POST", "PUT", "PATCH") else None)
            assert r.status_code == 403, f"{method} {route.path} answered {r.status_code}"
            assert "homework" in r.text.lower()
            checked += 1
    assert checked > 25


def test_homework_token_cannot_use_setup_or_teacher_routes(hw, fake):
    tok = bearer(homework_token(hw, fake))
    bodies = {"/api/setup/identify": {"first_name": "Ada", "last_name": "Byron", "netid": "ab123"},
              "/api/setup/account": {"username": "adab", "password": "Correct-horse-9", "password2": "Correct-horse-9"}}
    for path, body in bodies.items():
        r = hw.post(path, json=body, headers=tok)
        assert r.status_code in (401, 403), (path, r.status_code)
    row = db(hw, lambda s: s.execute(select(User).where(User.netid == "ab123"))).scalar_one()
    assert row.set_up_at is None and row.username == "oj:ab123"
    assert hw.get("/api/exams", headers=tok).status_code == 403
    assert hw.get("/api/classes", headers=tok).status_code == 403


def test_homework_token_does_not_set_up_or_start_exam(hw, fake, live_exam=None):
    tok = bearer(homework_token(hw, fake))
    assert hw.get("/api/student/exams", headers=tok).status_code == 403
    assert hw.post("/api/student/exams/anything/start", json={}, headers=tok).status_code == 403
    row = db(hw, lambda s: s.execute(select(User).where(User.netid == "ab123"))).scalar_one()
    assert row.set_up_at is None


def test_me_accepts_plain_student_refuses_exam_and_other_tokens(hw, fake):
    r = hw.get(ME, headers=login(hw, "alice"))
    assert r.status_code == 200 and r.json()["name"]
    alice = db(hw, lambda s: s.execute(select(User).where(User.username == "alice"))).scalar_one()
    for scope in ("some-exam-id", ADMIN_SCOPE):
        tok = make_token(SECRET, alice.id, "student", 600, version=alice.token_version or 0, scope=scope)
        assert hw.get(ME, headers=bearer(tok)).status_code == 403
    assert hw.get(ME, headers=login(hw, "teacher")).status_code == 403
    assert hw.get(ME).status_code == 401


def test_exam_token_does_not_help_on_finish(hw, fake):
    alice = login(hw, "alice")
    r = hw.post(FINISH, json={"state": "A" * 43, "ticket": "t"}, headers=alice)
    assert r.status_code == 400


# ---------------------------------------------------------------- startup refusal
def start(tmp_path, static_dir, **kw):
    with TestClient(create_app(settings_for(tmp_path, static_dir, **kw))):
        pass


def test_refuses_unknown_provider(tmp_path, static_dir, oj_env):
    with pytest.raises(RuntimeError, match="FAIRPLAY_HOMEWORK_LOGIN"):
        start(tmp_path, static_dir, homework_login="nope")


@pytest.mark.parametrize("missing", ["DKUOJ_URL", "DKUOJ_TOKEN"])
def test_refuses_without_bridge(tmp_path, static_dir, oj_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match="bridge"):
        start(tmp_path, static_dir)


@pytest.mark.parametrize("value", ["", "http://oj.example.edu", "ftp://oj.example.edu", "oj.example.edu",
                                   "https://u:p@oj.example.edu", "https://oj.example.edu/?x=1",
                                   "http://localhost:8000"])
def test_refuses_bad_public_url(tmp_path, static_dir, oj_env, monkeypatch, value):
    monkeypatch.setenv("DKUOJ_PUBLIC_URL", value)
    with pytest.raises(RuntimeError, match="DKUOJ_PUBLIC_URL"):
        start(tmp_path, static_dir)


def test_loopback_http_only_with_dev(tmp_path, static_dir, oj_env, monkeypatch):
    monkeypatch.setenv("DKUOJ_PUBLIC_URL", "http://localhost:8000")
    start(tmp_path, static_dir, dev=True)
    monkeypatch.setenv("DKUOJ_PUBLIC_URL", "http://oj.example.edu")
    with pytest.raises(RuntimeError, match="DKUOJ_PUBLIC_URL"):
        start(tmp_path, static_dir, dev=True)


def test_off_needs_nothing(tmp_path, static_dir, monkeypatch):
    monkeypatch.delenv("DKUOJ_PUBLIC_URL", raising=False)
    start(tmp_path, static_dir, homework_login="")


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("FAIRPLAY_HOMEWORK_LOGIN", " DkuOJ ")
    monkeypatch.setenv("HOMEWORK_LOGIN_HOSTS", "a.example.edu, b.example.edu")
    monkeypatch.delenv("HOMEWORK_LOGIN_RESUME_PATHS", raising=False)
    s = Settings()
    assert s.homework_login == "dkuoj" and s.homework_login_hosts == ["a.example.edu", "b.example.edu"]
    assert s.homework_login_resume_paths == ["/"]

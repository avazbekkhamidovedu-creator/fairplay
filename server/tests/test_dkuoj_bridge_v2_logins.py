"""DKU OJ bridge v2, 2026-10-24 additions (docs/dkuoj-bridge-v2.md §2-§4): the class login
password kept encrypted so professors can see it again, the login history, strong student
passwords, and a student's DKU OJ classes on the course cards. Against a FAKE DKU OJ."""
import logging
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from sqlalchemy import select, update

from app.integrations.dkuoj import client as ojclient, setup as ojsetup, student_classes, sync as ojsync
from app.integrations.dkuoj.models import ClassLogin
from app.timeutil import utcnow

from .conftest import login
from .test_dkuoj_bridge import OJ, TOKEN, db, fp_signed, run
from .test_dkuoj_bridge_v2 import (CLASS_ID, V1, RosterOJ, account, bearer, call, identify, make_login, oj,  # noqa: F401
                                   set_up, setup_token)

RULE = "Password needs at least 10 characters, a capital letter, a small letter, a number and a symbol."


class ClassesOJ(RosterOJ):
    """RosterOJ plus GET students/<netid>/classes."""

    def __init__(self):
        super().__init__()
        self.student_classes = {"bl642": [{"id": CLASS_ID, "name": "CS 101 Section A",
                                           "course": {"id": 12, "name": "CS 101 Programming"},
                                           "teachers": ["Shuai Dong"]}]}
        self.classes_down = False
        self.classes_calls = 0

    def handle(self, req):
        path = urlsplit(str(req.url)).path
        if path.startswith("/api/fairplay/v1/students/") and path.endswith("/classes"):
            self.classes_calls += 1
            if not fp_signed(req):
                return httpx.Response(401, json={"detail": "bad signature"})
            if self.classes_down:
                raise httpx.ConnectError("down")
            netid = unquote(path.split("/")[-2])
            return httpx.Response(200, json={"classes": self.student_classes.get(netid, [])})
        return super().handle(req)


@pytest.fixture
def fake(monkeypatch):
    f = ClassesOJ()
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


def _secret(c):
    return c.app.state.settings.secret


async def _rows(s):
    return list((await s.execute(select(ClassLogin).order_by(ClassLogin.id))).scalars())


def status(c):
    r = call(c, "GET", f"{V1}/classes/{CLASS_ID}/status")
    assert r.status_code == 200, r.text
    return r


# ---------------------------------------------------------------- A: class-login password
def test_class_login_password_is_stored_encrypted(oj, fake):
    cl = make_login(oj)
    row = db(oj, _rows)[0]
    assert row.password_hash.startswith("pbkdf2_sha256$")
    assert row.password_enc.startswith("v1:") and cl["password"] not in row.password_enc
    assert ojsetup.open_login_password(_secret(oj), row.username, row.password_enc) == cl["password"]
    # bound to its row (username as associated data), its key and its bytes
    assert ojsetup.open_login_password(_secret(oj), "other-0000", row.password_enc) is None
    assert ojsetup.open_login_password("another-secret-0123456789abcdef", row.username, row.password_enc) is None
    tampered = row.password_enc[:-4] + ("AAAA" if not row.password_enc.endswith("AAAA") else "BBBB")
    assert ojsetup.open_login_password(_secret(oj), row.username, tampered) is None
    assert ojsetup.open_login_password(_secret(oj), row.username, None) is None
    # two logins never share a nonce / ciphertext
    a = ojsetup.seal_login_password(_secret(oj), "u-1", "same-password")
    assert a != ojsetup.seal_login_password(_secret(oj), "u-1", "same-password")


def test_status_shows_password_and_history(oj, fake):
    cl1 = make_login(oj)
    r = status(oj)
    assert r.headers.get("cache-control") == "no-store"
    st = r.json()
    assert st["login"]["password"] == cl1["password"] and st["login"]["username"] == cl1["username"]
    assert st["login"]["created_by"] == "teacher"
    assert st["history"] == [{"username": cl1["username"], "created_at": st["login"]["created_at"],
                              "expires_at": st["login"]["expires_at"], "ended_at": None, "end_reason": None,
                              "used": 0, "created_by": "teacher"}]
    set_up(oj, cl1)                                  # Bob uses the first login
    cl2 = make_login(oj)                             # replaced
    st = status(oj).json()
    assert st["login"]["username"] == cl2["username"] and st["login"]["password"] == cl2["password"]
    h = st["history"]
    assert [x["username"] for x in h] == [cl2["username"], cl1["username"]]
    assert h[0]["end_reason"] is None and h[0]["used"] == 0
    assert h[1]["end_reason"] == "replaced" and h[1]["used"] == 1 and h[1]["ended_at"]
    assert all("password" not in x for x in h)
    assert cl1["password"] not in status(oj).text      # an old login's password is never sent

    # stopped
    call(oj, "DELETE", f"{V1}/classes/{CLASS_ID}/login")
    st = status(oj).json()
    assert st["login"] is None and st["history"][0]["end_reason"] == "stopped"
    assert cl2["password"] not in status(oj).text

    # expired (and replaced only after it ran out: still "expired", at its expiry time)
    cl3 = make_login(oj)

    async def expire(s):
        await s.execute(update(ClassLogin).where(ClassLogin.username == cl3["username"])
                        .values(expires_at=utcnow() - timedelta(minutes=5)))
        await s.commit()
    db(oj, expire)
    st = status(oj).json()
    assert st["login"] is None and st["history"][0]["end_reason"] == "expired"
    assert st["history"][0]["ended_at"] == st["history"][0]["expires_at"]
    make_login(oj)
    assert status(oj).json()["history"][1]["end_reason"] == "expired"

    # locked after guessing
    async def lock(s):
        await s.execute(update(ClassLogin).where(ClassLogin.disabled_at.is_(None))
                        .values(disabled_at=utcnow(), disabled_reason="too_many_failures"))
        await s.commit()
    db(oj, lock)
    st = status(oj).json()
    assert st["login"] is None and st["history"][0]["end_reason"] == "locked"


def test_history_is_capped_at_20(oj, fake):
    for _ in range(22):
        make_login(oj)
    h = status(oj).json()["history"]
    assert len(h) == 20 and h[0]["end_reason"] is None and all(x["end_reason"] == "replaced" for x in h[1:])


def test_old_login_without_encrypted_copy_has_no_password(oj, fake):
    cl = make_login(oj)

    async def legacy(s):
        await s.execute(update(ClassLogin).values(password_enc=None))
        await s.commit()
    db(oj, legacy)
    st = status(oj).json()
    assert st["login"]["username"] == cl["username"] and st["login"]["password"] is None
    assert setup_token(oj, cl)                       # it still works for sign-in


def test_class_login_password_never_logged(oj, fake, caplog):
    caplog.set_level(logging.DEBUG)
    cl = make_login(oj)
    status(oj)
    set_up(oj, cl, password="Bob-own-Secret-7")
    make_login(oj)
    status(oj)
    assert cl["password"] not in caplog.text and "Bob-own-Secret-7" not in caplog.text


# ---------------------------------------------------------------- B: strong student passwords
@pytest.mark.parametrize("pw", [
    "Short-1a",              # < 10
    "alllower-123!",         # no capital
    "ALLUPPER-123!",         # no small letter
    "No-digits-here!",       # no number
    "NoSymbols123x",         # no symbol
    "Spaces Only 123a",      # a space is not a symbol
])
def test_student_password_rules(oj, fake, pw):
    cl = make_login(oj)
    ident = identify(oj, setup_token(oj, cl)).json()["identity_token"]
    r = account(oj, ident, password=pw)
    assert r.status_code == 400 and r.json()["detail"] == RULE
    assert account(oj, ident, password="Fine-pass-10").status_code == 200      # the token is still usable


def test_student_password_not_username_and_must_match(oj, fake):
    cl = make_login(oj)
    ident = identify(oj, setup_token(oj, cl)).json()["identity_token"]
    r = account(oj, ident, username="bobli.99-xy", password="Bobli.99-Xy")
    assert r.status_code == 400 and r.json()["detail"] == ojsetup.PASSWORD_SAME
    r = account(oj, ident, password="Correct-horse-9", password2="Correct-horse-8")
    assert r.status_code == 400 and r.json()["detail"] == ojsetup.PASSWORD_MISMATCH
    assert account(oj, ident, password="Ünïcode-Pässwort-1").status_code == 200
    assert ojsetup.strong_password("Aa1!aaaaaa") and not ojsetup.strong_password("Aa1!aaaaa")


# ---------------------------------------------------------------- C: student's classes
def _classes(c, h):
    r = c.get("/api/student/classes", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_student_classes_merge_dkuoj_and_fairplay(oj, fake):
    run(oj)                                          # the DKU OJ exam of class 31 (Bob on its roster)
    me = set_up(oj, make_login(oj))
    h = bearer(me["token"])
    # a FairPlay-native class too
    t = login(oj, "teacher")
    k = oj.post("/api/classes", json={"name": "Robotics Club"}, headers=t).json()
    assert oj.post("/api/student/classes/join", json={"code": k["join_code"]}, headers=h).status_code == 200
    out = _classes(oj, h)
    dk = [x for x in out if x["source"] == "dkuoj"]
    assert dk == [{"id": f"dkuoj:{CLASS_ID}", "name": "CS 101 Section A", "term": "", "teacher": "Shuai Dong",
                   "joined_at": None, "course": "CS 101 Programming", "source": "dkuoj", "upcoming_exams": 1,
                   "next_exam": dk[0]["next_exam"], "can_leave": False}]
    assert dk[0]["next_exam"]["title"] == "CS 101 Midterm" and dk[0]["next_exam"]["status"] == "draft"
    assert dk[0]["next_exam"]["starts_at"]
    # the FairPlay mirror copy of class 31 is not listed again
    assert [x["name"] for x in out].count("CS 101 Section A") == 1
    native = next(x for x in out if x["source"] == "fairplay")
    assert native["id"] == k["id"] and native["name"] == "Robotics Club" and native["can_leave"] is True
    assert native["upcoming_exams"] == 0 and native["next_exam"] is None and native["course"] is None
    # kept 60 s: no second DKU OJ call
    calls = fake.classes_calls
    _classes(oj, h)
    assert fake.classes_calls == calls == 1


def test_student_classes_native_exam_counts(oj, fake):
    from .conftest import SAMPLE_EXAM
    t = login(oj, "teacher")
    k = oj.post("/api/classes", json={"name": "Physics"}, headers=t).json()
    alice = login(oj, "alice")
    oj.post("/api/student/classes/join", json={"code": k["join_code"]}, headers=alice)
    e = oj.post("/api/exams", json={**SAMPLE_EXAM, "class_id": k["id"], "title": "Quiz 1"}, headers=t).json()
    row = next(x for x in _classes(oj, alice) if x["id"] == k["id"])
    assert row["upcoming_exams"] == 0                # a draft without a date is not upcoming
    assert oj.post(f"/api/exams/{e['id']}/open", headers=t).status_code == 200
    row = next(x for x in _classes(oj, alice) if x["id"] == k["id"])
    assert row["upcoming_exams"] == 1 and row["next_exam"]["status"] == "live" and row["next_exam"]["title"] == "Quiz 1"
    assert not any(x["source"] == "dkuoj" for x in _classes(oj, alice))     # no NetID: no DKU OJ classes


def test_student_classes_fallback_when_dkuoj_is_down(oj, fake):
    run(oj)
    me = set_up(oj, make_login(oj))
    h = bearer(me["token"])
    fake.classes_down = True
    out = _classes(oj, h)
    dk = [x for x in out if x["source"] == "dkuoj"]
    assert len(dk) == 1 and dk[0]["id"] == f"dkuoj:{CLASS_ID}" and dk[0]["name"] == "CS 101 Section A"
    assert dk[0]["course"] == "CS 101 Programming" and dk[0]["teacher"] is None and dk[0]["upcoming_exams"] == 1
    assert fake.classes_calls == 1
    _classes(oj, h)                                  # the fallback is kept a short while too
    assert fake.classes_calls == 1
    # DKU OJ back, cache gone: its answer is used (enrollment is DKU OJ's)
    fake.classes_down = False
    fake.student_classes["bl642"] = []
    user_id = db(oj, lambda s: _uid(s, "bobli"))

    async def clear(_s):
        await student_classes.forget(user_id)
    db(oj, clear)
    assert not any(x["source"] == "dkuoj" for x in _classes(oj, h))


async def _uid(s, username):
    from app.models import User
    return (await s.execute(select(User.id).where(User.username == username))).scalar_one()


def test_join_with_a_class_login_username(oj, fake):
    cl = make_login(oj)
    alice = login(oj, "alice")
    for typed in (cl["username"], cl["username"].upper(), f"  {cl['username']} "):
        r = oj.post("/api/student/classes/join", json={"code": typed}, headers=alice)
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == ("That is a class login for first sign-in. "
                                      "Your DKU OJ courses appear here by themselves.")
    make_login(oj)                                   # an old (replaced) one is recognised too
    r = oj.post("/api/student/classes/join", json={"code": cl["username"]}, headers=alice)
    assert r.status_code == 400
    # a wrong normal code is still a 404
    assert oj.post("/api/student/classes/join", json={"code": "ZZZZ-ZZZZ"}, headers=alice).status_code == 404


def test_student_exams_carry_class_ids(oj, fake):
    """GET /api/student/exams: class_ids match String(id) of /api/student/classes items."""
    from .conftest import SAMPLE_EXAM
    run(oj)
    me = set_up(oj, make_login(oj))
    h = bearer(me["token"])
    call(oj, "POST", f"{V1}/exams/cs101a-midterm/open", {"open_to": "all"})
    t = login(oj, "teacher")
    k = oj.post("/api/classes", json={"name": "Robotics Club"}, headers=t).json()
    oj.post("/api/student/classes/join", json={"code": k["join_code"]}, headers=h)
    e = oj.post("/api/exams", json={**SAMPLE_EXAM, "class_id": k["id"], "title": "Robot quiz"}, headers=t).json()
    assert oj.post(f"/api/exams/{e['id']}/open", headers=t).status_code == 200
    exams = {x["title"]: x for x in oj.get("/api/student/exams", headers=h).json()}
    assert exams["CS 101 Midterm"]["class_ids"] == [f"dkuoj:{CLASS_ID}"]
    assert exams["Robot quiz"]["class_ids"] == [str(k["id"])]
    card_ids = {str(c["id"]) for c in _classes(oj, h)}
    assert all(cid in card_ids for x in exams.values() for cid in x["class_ids"])

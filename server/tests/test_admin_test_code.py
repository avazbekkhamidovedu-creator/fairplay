import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.integrations.dkuoj.models import ClassLoginEvent
from app.main import create_app
from app.models import Exam, User
from app.security import ADMIN_SCOPE, parse_scoped_token
from app.timeutil import utcnow

from .conftest import login
from .test_dkuoj_bridge import db, oj_settings, run
from .test_dkuoj_bridge_v2 import fake  # noqa: F401  (fixture)

URL = "/api/student/admin-test"
NOT_VALID = {"detail": "That code is not valid."}
DUMMY = "QZ7M4K2XR9TD"
SECRET_START = "STARTSECRET"
SECRET_PROMPT = "PROMPT-SECRET-TEXT"
SECRET_KEY = "KEY-SECRET-INDEX"


@pytest.fixture
def adm(tmp_path, static_dir, fake):
    with TestClient(create_app(oj_settings(tmp_path, static_dir, admin_test_code=DUMMY))) as c:
        run(c)
        add_exams(c)
        yield c


@pytest.fixture
def off(tmp_path, static_dir, fake):
    with TestClient(create_app(oj_settings(tmp_path, static_dir))) as c:
        run(c)
        yield c


def add_exams(c):
    async def go(s):
        base = (await s.execute(select(Exam).where(Exam.source == "dkuoj"))).scalars().first()
        qs = [{"id": "q1", "type": "mcq", "points": 1, "prompt": SECRET_PROMPT, "options": ["a", "b"],
               "correct_index": 1}]
        for eid, status in (("e_adm_live", "live"), ("e_adm_closed", "closed"), ("e_adm_draft", "draft")):
            s.add(Exam(id=eid, teacher_id=base.teacher_id, title=f"OJ {status}", duration_min=30, status=status,
                       questions=qs, question_count=1, total_points=1, source="dkuoj", course="CS 101",
                       start_code=SECRET_START, scheduled_at=utcnow()))
        s.add(Exam(id="e_native", teacher_id=base.teacher_id, title="Native", duration_min=30, status="live",
                   questions=qs, question_count=1, total_points=1, source="fairplay"))
        await s.commit()
    db(c, go)


def enter(c, code):
    return c.post(URL, json={"code": code})


def admin_session(c):
    r = enter(c, DUMMY)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()


def test_the_old_code_login_routes_are_gone(adm):
    assert adm.post("/api/student/code-login", json={"code": DUMMY}).status_code in (404, 405)
    assert adm.get("/api/student/code-login/challenge").status_code in (404, 405)
    assert adm.post("/api/student/code-login/confirm", json={"confirm_token": "x"}).status_code in (404, 405)


def test_disabled_by_default_gives_the_generic_400(off):
    for guess in (DUMMY, "", "ADMIN", "TEST"):
        r = enter(off, guess)
        assert (r.status_code, r.json()) == (400, NOT_VALID)


def test_right_code_issues_a_scoped_student_token(adm):
    h, body = admin_session(adm)
    assert body["role"] == "student" and body["name"] == "Admin Tester"
    uid, role, _, scope = parse_scoped_token(adm.app.state.settings.secret, body["token"])
    assert (role, scope) == ("student", ADMIN_SCOPE)
    assert adm.get("/api/me", headers=h).json()["role"] == "student"


def test_code_is_normalised_like_access_codes(adm):
    assert enter(adm, "qz7m-4k2x-r9td").status_code == 200
    assert enter(adm, " qz7m 4k2x r9td ").status_code == 200


def test_token_lives_at_most_four_hours(adm):
    import time
    _, body = admin_session(adm)
    import base64
    payload = body["token"].split(".")[0]
    exp = int(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split(":")[2])
    assert exp - time.time() <= 4 * 3600 + 5


def test_list_shows_every_dkuoj_exam_in_every_status_and_no_secrets(adm):
    h, _ = admin_session(adm)
    r = adm.get("/api/student/exams", headers=h)
    assert r.status_code == 200
    rows = r.json()
    ids = {x["id"] for x in rows}
    assert {"e_adm_live", "e_adm_closed", "e_adm_draft"} <= ids and "e_native" not in ids
    assert {x["status"] for x in rows} >= {"live", "closed", "draft"}
    assert all(x["preview"] is True for x in rows)
    for k in ("title", "course", "status", "scheduled_at", "duration_min", "question_count"):
        assert all(k in x for x in rows)
    blob = json.dumps(rows)
    for secret in (SECRET_START, SECRET_PROMPT, SECRET_KEY, "correct_index"):
        assert secret not in blob
    for k in ("questions", "start_code", "answers", "key", "package_id"):
        assert all(x.get(k) is None for x in rows)
    assert DUMMY not in blob


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/student/exams/e_adm_live/start"),
    ("GET", "/api/student/exams/e_adm_live/challenge"),
    ("GET", "/api/student/exams/e_adm_live/package"),
    ("GET", "/api/student/exams/e_adm_live/package-key"),
    ("GET", "/api/student/attempts/a_x"),
    ("PUT", "/api/student/attempts/a_x/answers"),
    ("POST", "/api/student/attempts/a_x/submit"),
    ("GET", "/api/student/attempts/a_x/handin"),
    ("POST", "/api/student/attempts/a_x/offline-key"),
    ("GET", "/api/student/grades"),
    ("GET", "/api/student/grades/e_adm_live"),
    ("GET", "/api/student/classes"),
    ("GET", "/api/student/practice"),
    ("POST", "/api/student/trial/start"),
    ("POST", "/api/student/device-check"),
])
def test_everything_else_is_forbidden(adm, method, path):
    h, _ = admin_session(adm)
    r = adm.request(method, path, headers=h, json={})
    assert r.status_code == 403, (path, r.status_code, r.text)


def test_teacher_endpoints_are_forbidden(adm):
    h, _ = admin_session(adm)
    assert adm.get("/api/exams", headers=h).status_code == 403
    assert adm.get("/api/practice/students", headers=h).status_code == 403


def test_wrong_codes_are_rate_limited(adm):
    for _ in range(10):
        assert enter(adm, "AAAAAA").status_code == 400
    assert enter(adm, "AAAAAA").status_code == 429
    r = enter(adm, DUMMY)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_right_code_is_not_a_failure(adm):
    for _ in range(40):
        assert enter(adm, DUMMY).status_code == 200


def test_use_is_audited_without_the_code(adm, caplog):
    with caplog.at_level("WARNING", logger="proctora.dkuoj"):
        admin_session(adm)
    rows = db(adm, lambda s: _all(s))
    assert len(rows) == 1 and rows[0].detail.startswith("from ")
    assert DUMMY not in rows[0].detail and DUMMY not in caplog.text
    assert any(r.levelname == "WARNING" and "admin test code" in r.getMessage() for r in caplog.records)


async def _all(s):
    return list((await s.scalars(select(ClassLoginEvent).where(ClassLoginEvent.kind == "admin_test_login"))).all())


def test_tester_account_is_hidden_everywhere(adm):
    admin_session(adm)
    u = db(adm, lambda s: s.scalar(select(User).where(User.username == "admin-tester")))
    assert u.netid is None and not u.password_hash.startswith("pbkdf2_") and u.role == "system"
    assert adm.post("/api/auth/login", json={"username": "admin-tester", "password": "!"}).status_code == 401
    t = login(adm, "teacher")
    students = adm.get("/api/practice/students", headers=t).json()
    assert "admin-tester" not in json.dumps(students)
    assert db(adm, lambda s: s.scalar(select(User.id).where(User.role == "student", User.username == "admin-tester"))) is None

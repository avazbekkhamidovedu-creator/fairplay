"""Tenant isolation and account claim: a teacher only sees and adds their own students, the
DKU OJ class/exam calls check the acting user, a set-up needs the student's own access code,
and weak secrets are refused at startup."""
import pytest
from sqlalchemy import create_engine, text

from app.config import MIN_ADMIN_TEST_CODE, Settings
from app.security import hash_password

from .conftest import login
from .test_dkuoj_bridge import run
from .test_dkuoj_bridge_v2 import CLASS_ID, V1, call, fake, make_login, oj, setup_token  # noqa: F401

pytestmark = pytest.mark.no_default_class


def second_teacher(client):
    from app.db import get_database
    url = str(get_database().engine.url).replace("sqlite+aiosqlite", "sqlite")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("insert into users (username, name, role, password_hash, created_at, token_version) "
                       "values ('teacher2', 'Other Teacher', 'teacher', :h, CURRENT_TIMESTAMP, 0)"),
                  {"h": hash_password("other-pass-123", 1000)})
    eng.dispose()
    return login(client, "teacher2", "other-pass-123")


def join(client, who, klass):
    r = client.post("/api/student/classes/join", json={"code": klass["join_code"]}, headers=login(client, who))
    assert r.status_code == 200, r.text


def test_practice_students_only_lists_own_students(client):
    t, t2 = login(client, "teacher"), second_teacher(client)
    k = client.post("/api/classes", json={"name": "Mine"}, headers=t).json()
    join(client, "alice", k)
    mine = [s["username"] for s in client.get("/api/practice/students", headers=t).json()["students"]]
    assert mine == ["alice"]
    assert client.get("/api/practice/students", headers=t2).json()["students"] == []


def test_practice_generate_skips_students_of_other_teachers(client):
    t, t2 = login(client, "teacher"), second_teacher(client)
    k = client.post("/api/classes", json={"name": "Mine"}, headers=t).json()
    join(client, "alice", k)
    r = client.post("/api/practice/generate", json={"usernames": ["alice"], "topics": ["x"]}, headers=t2)
    assert r.status_code == 200, r.text
    assert r.json()["sets"] == [] and r.json()["skipped"][0]["reason"] == "No such student"


def test_add_members_cannot_probe_or_take_other_students(client):
    t, t2 = login(client, "teacher"), second_teacher(client)
    k = client.post("/api/classes", json={"name": "Mine"}, headers=t).json()
    join(client, "alice", k)
    k2 = client.post("/api/classes", json={"name": "Theirs"}, headers=t2).json()
    r = client.post(f"/api/classes/{k2['id']}/members", json={"usernames": ["alice", "bob", "nobody"]},
                    headers=t2).json()
    assert r == {"added": 0, "already": 0, "unknown": ["alice", "bob", "nobody"]}
    assert client.get(f"/api/classes/{k2['id']}", headers=t2).json()["member_count"] == 0


def test_exam_roster_refuses_students_of_other_teachers(client):
    from .conftest import SAMPLE_EXAM
    t, t2 = login(client, "teacher"), second_teacher(client)
    k = client.post("/api/classes", json={"name": "Theirs"}, headers=t2).json()
    body = {kk: v for kk, v in SAMPLE_EXAM.items() if kk != "class_id"}
    e = client.post("/api/exams", json={**body, "class_id": k["id"]}, headers=t2)
    assert e.status_code == 201, e.text
    mine = client.post("/api/classes", json={"name": "Mine"}, headers=t).json()
    join(client, "alice", mine)
    r = client.put(f"/api/exams/{e.json()['id']}/roster", json={"usernames": ["alice"]}, headers=t2)
    assert r.status_code == 422 and "alice" in r.text
    assert client.put(f"/api/exams/{e.json()['id']}/roster", json={"usernames": ["nobody"]},
                      headers=t2).status_code == 422


def test_bridge_roster_students_can_be_added_by_their_editor(oj):
    run(oj)
    t = login(oj, "teacher")
    k = oj.post("/api/classes", json={"name": "Native"}, headers=t).json()
    r = oj.post(f"/api/classes/{k['id']}/members", json={"usernames": ["alice", "nobody"]}, headers=t).json()
    assert r["added"] == 1 and r["unknown"] == ["nobody"]


# ---------------------------------------------------------------- DKU OJ bridge
def test_known_class_accepts_any_non_student_dkuoj_signs_for_it(oj):
    """2026-11: no FairPlay-side link to the class is required (instructor, TA, course staff)."""
    run(oj)
    path = f"{V1}/classes/{CLASS_ID}"
    for method, url, body in (("GET", path + "/status", None), ("POST", path + "/login", {"class_name": "x"}),
                              ("DELETE", path + "/login", None)):
        r = call(oj, method, url, body, acting="ta-of-this-class")
        assert r.status_code == 200, (method, url, r.text)
    assert call(oj, "GET", path + "/status", acting="teacher").status_code == 200


def test_student_account_and_empty_acting_user_are_refused(oj):
    run(oj)
    path = f"{V1}/classes/{CLASS_ID}/status"
    assert call(oj, "GET", path, acting="alice").status_code == 403
    assert call(oj, "GET", path, acting="").status_code == 403


def test_new_class_is_open_to_every_non_student_acting_user(oj):
    r = call(oj, "POST", f"{V1}/classes/777/login", {"class_name": "New"}, acting="prof1")
    assert r.status_code == 200, r.text
    assert call(oj, "GET", f"{V1}/classes/777/status", acting="prof1").status_code == 200
    assert call(oj, "GET", f"{V1}/classes/777/status", acting="prof2").status_code == 200
    assert call(oj, "DELETE", f"{V1}/classes/777/login", acting="prof2").status_code == 200


def test_unsynced_exam_calls_need_a_named_non_student(oj):
    key = "not-synced-yet"
    path = f"{V1}/exams/{key}"
    assert call(oj, "GET", path, acting="teacher").status_code == 200
    assert call(oj, "GET", path, acting="").status_code == 403
    body = {"calculator": "scientific"}
    assert call(oj, "POST", path + "/settings", body, acting="").status_code == 403
    run(oj)
    assert call(oj, "POST", path + "/settings", body, acting="alice").status_code == 403
    assert call(oj, "GET", path, acting="alice").status_code == 403
    assert call(oj, "POST", path + "/settings", body, acting="teacher").status_code == 200


# ---------------------------------------------------------------- set-up needs no exam code
def _identify(c, first="Bob", last="Li", netid="bl642"):
    tok = setup_token(c, make_login(c))
    body = {"first_name": first, "last_name": last, "netid": netid}
    return c.post("/api/setup/identify", json=body, headers={"Authorization": f"Bearer {tok}"})


def test_setup_never_asks_for_an_access_code(oj):
    run(oj)
    assert _identify(oj).status_code == 200


# ---------------------------------------------------------------- config
def test_short_admin_test_code_stops_startup(tmp_path):
    s = Settings(secret="test-secret-0123456789abcdef", app_keys=["k"], redis_url=None, single_process=True,
                 admin_test_code="short", database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
    with pytest.raises(RuntimeError, match="FAIRPLAY_ADMIN_TEST_CODE"):
        s.check_secure()
    s.admin_test_code = "x" * MIN_ADMIN_TEST_CODE
    s.check_secure()
    s.admin_test_code = ""
    s.check_secure()


def test_secrets_are_not_in_repr(tmp_path):
    from app.integrations.dkuoj.config import DkuojConfig
    s = Settings(secret="very-secret-value-0123456789", app_keys=["app-key-value-xyz"],
                 admin_test_code="admin-code-value-123")
    shown = repr(s) + repr(DkuojConfig(url="https://x", token="oj-token-value-abcdefghijklmnopqrstuvwxyz"))
    for v in ("very-secret-value", "app-key-value-xyz", "admin-code-value", "oj-token-value"):
        assert v not in shown

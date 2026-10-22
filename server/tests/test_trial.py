"""2026-10-22: the built-in sample exam ("Try a sample exam", app.trial)."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.app_client import beat_message, handin_message, sign, start_message
from app.config import Settings
from app.main import create_app
from app.models import Attempt, Exam, IntegrityEvent, User
from app.trial import TRIAL_EXAM_ID, TRIAL_OWNER

from .conftest import SAMPLE_EXAM, login
from .s_helpers import forget_double_click

TRIAL = TRIAL_EXAM_ID


def run_db(client, fn):
    async def go():
        eng = create_async_engine(client.app.state.settings.database_url)
        async with eng.begin() as conn:
            out = await fn(conn)
        await eng.dispose()
        return out
    return asyncio.run(go())


def trial_start(c, h, body=None):
    return c.post("/api/student/trial/start", headers=h, json=body or {})


def sess(view):
    return {"X-FairPlay-Session": view["session"]}


# ---------------------------------------------------------------- seed
def test_seeded_once_with_system_owner(client):
    rows = run_db(client, lambda conn: _seed_rows(conn))
    exams, owners = rows
    assert len(exams) == 1
    e = exams[0]
    assert e.is_trial and e.status == "live" and e.class_id is None and e.course_link_id is None
    assert e.resume_needs_approval is False and e.start_code is None
    types = {q["type"] for q in e.questions}
    assert {"mcq", "short", "essay"} <= types and e.duration_min == 10
    assert len(owners) == 1 and owners[0].role == "system"
    # the system account cannot sign in
    r = client.post("/api/auth/login", json={"username": TRIAL_OWNER, "password": "!"})
    assert r.status_code == 401


async def _seed_rows(conn):
    exams = (await conn.execute(select(Exam).where(Exam.is_trial.is_(True)))).all()
    owners = (await conn.execute(select(User).where(User.username == TRIAL_OWNER))).all()
    return exams, owners


def test_seed_is_idempotent(tmp_path, static_dir):
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'i.db'}", redis_url=None,
                 secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                 static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"), require_app=False,
                 app_keys=["k"], dev=False, seed_loadtest_users=0, seed_demo=True, single_process=True)
    for _ in range(2):
        with TestClient(create_app(s)) as c:
            exams, owners = run_db(c, _seed_rows)
            assert len(exams) == 1 and len(owners) == 1


# ---------------------------------------------------------------- start
@pytest.mark.no_default_class
def test_any_student_starts_without_class_or_code(client):
    h = login(client, "carol")       # in no class at all
    info = client.get("/api/student/trial", headers=h).json()
    assert info["exam_id"] == TRIAL and info["attempt"] is None and info["question_count"] >= 3
    r = trial_start(client, h)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["trial"] is True and v["status"] == "in_progress" and v["session"]
    assert v["questions"] and all("correct_index" not in q for q in v["questions"])
    info = client.get("/api/student/trial", headers=h).json()
    assert info["attempt"] == {"attempt_id": v["attempt_id"], "status": "in_progress"}
    # the attempt works like a real one: save, heartbeat, events
    aid = v["attempt_id"]
    qid = v["questions"][0]["id"]
    assert client.put(f"/api/student/attempts/{aid}/answers", headers={**h, **sess(v)},
                      json={"answers": {qid: 2}, "client_seq": 1}).json()["saved"] is True
    assert client.post(f"/api/student/attempts/{aid}/heartbeat", headers={**h, **sess(v)},
                       json={"current_question": 0, "locked": False}).status_code == 200
    assert client.post(f"/api/student/attempts/{aid}/events", headers={**h, **sess(v)},
                       json={"events": [{"type": "blocked_shortcut", "detail": "x"}]}).status_code == 200


def test_real_start_and_challenge_refuse_the_sample_exam(client, alice):
    assert client.get(f"/api/student/exams/{TRIAL}/challenge", headers=alice).status_code == 404
    r = client.post(f"/api/student/exams/{TRIAL}/start", headers=alice, json={"start_code": ""})
    assert r.status_code == 404
    assert client.get(f"/api/student/exams/{TRIAL}/package", headers=alice).status_code == 404
    assert client.get(f"/api/student/exams/{TRIAL}/package-key", headers=alice).status_code == 404


def test_teacher_cannot_use_student_trial_endpoints(client, teacher):
    assert trial_start(client, teacher).status_code in (401, 403)


def test_one_active_trial_continue_or_replace(client, alice):
    a1 = trial_start(client, alice).json()
    # a double click gives the same attempt and session
    again = trial_start(client, alice).json()
    assert again["attempt_id"] == a1["attempt_id"] and again["session"] == a1["session"]
    client.post(f"/api/student/attempts/{a1['attempt_id']}/events", headers={**alice, **sess(a1)},
                json={"events": [{"type": "blocked_shortcut", "detail": "x"}]})
    # later, while it still runs on this computer: the same attempt, a new session
    forget_double_click(a1["attempt_id"])
    a2 = trial_start(client, alice).json()
    assert a2["attempt_id"] == a1["attempt_id"] and a2["session"] != a1["session"]
    assert client.get(f"/api/student/attempts/{a1['attempt_id']}",
                      headers={**alice, "X-FairPlay-Session": a1["session"]}).status_code == 409
    # finished: starting again gives a fresh attempt (unlimited retakes), the old one is gone
    assert client.post(f"/api/student/attempts/{a2['attempt_id']}/trial-stop",
                       headers={**alice, **sess(a2)}).status_code == 200
    a3 = trial_start(client, alice).json()
    assert a3["attempt_id"] != a2["attempt_id"] and a3["status"] == "in_progress"
    assert client.get(f"/api/student/attempts/{a2['attempt_id']}", headers=alice).status_code == 404
    attempts, events = run_db(client, lambda conn: _attempts_and_events(conn))
    assert [r.id for r in attempts if r.student_id_name == "alice"] == [a3["attempt_id"]]
    assert all(e.attempt_id != a1["attempt_id"] for e in events)
    # timed out: replaced as well
    from .s_helpers import db_update, shift
    from app.timeutil import utcnow
    db_update(client, a3["attempt_id"], deadline_at=shift(utcnow(), minutes=-1))
    forget_double_click(a3["attempt_id"])
    a4 = trial_start(client, alice).json()
    assert a4["attempt_id"] != a3["attempt_id"]


async def _attempts_and_events(conn):
    attempts = (await conn.execute(
        select(Attempt.id, User.username.label("student_id_name"))
        .join(User, User.id == Attempt.student_id).where(Attempt.exam_id == TRIAL))).all()
    events = (await conn.execute(select(IntegrityEvent.attempt_id).where(IntegrityEvent.exam_id == TRIAL))).all()
    return attempts, events


# ---------------------------------------------------------------- stop
def test_stop_only_own_trial_attempt(client, alice, bob):
    v = trial_start(client, alice).json()
    aid = v["attempt_id"]
    # someone else's attempt: not found
    assert client.post(f"/api/student/attempts/{aid}/trial-stop", headers=bob).status_code == 404
    # wrong session (another login of the same student) is refused
    r = client.post(f"/api/student/attempts/{aid}/trial-stop",
                    headers={**alice, "X-FairPlay-Session": "not-the-session"})
    assert r.status_code == 409
    r = client.post(f"/api/student/attempts/{aid}/trial-stop", headers={**alice, **sess(v)})
    assert r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["submitted_at"]
    # idempotent
    assert client.post(f"/api/student/attempts/{aid}/trial-stop",
                       headers={**alice, **sess(v)}).json()["status"] == "submitted"
    got = client.get(f"/api/student/attempts/{aid}", headers={**alice, **sess(v)}).json()
    assert got["status"] == "submitted" and got["answers"] == {}
    hb = client.post(f"/api/student/attempts/{aid}/heartbeat", headers={**alice, **sess(v)},
                     json={"current_question": 0, "locked": False}).json()
    assert hb["status"] == "submitted"


def test_real_exam_refuses_trial_stop(client, teacher, alice, live_exam):
    r = client.post(f"/api/student/exams/{live_exam['id']}/start", headers=alice,
                    json={"start_code": live_exam["start_code"]})
    v = r.json()
    r = client.post(f"/api/student/attempts/{v['attempt_id']}/trial-stop", headers={**alice, **sess(v)})
    assert r.status_code == 403
    got = client.get(f"/api/student/attempts/{v['attempt_id']}", headers={**alice, **sess(v)}).json()
    assert got["status"] == "in_progress"


def test_submitting_the_trial_is_not_graded(client, alice):
    v = trial_start(client, alice).json()
    mcq = next(q for q in v["questions"] if q["type"] == "mcq")
    r = client.post(f"/api/student/attempts/{v['attempt_id']}/submit", headers={**alice, **sess(v)},
                    json={"answers": {mcq["id"]: 2}, "client_seq": 1})
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    score = run_db(client, lambda conn: _score(conn, v["attempt_id"]))
    assert score == (None, 0)
    assert client.get("/api/student/grades", headers=alice).json() == []
    assert client.get(f"/api/student/grades/{TRIAL}", headers=alice).status_code == 404


async def _score(conn, aid):
    row = (await conn.execute(select(Attempt.score, Attempt.needs_grading).where(Attempt.id == aid))).first()
    return tuple(row)


# ---------------------------------------------------------------- app rules
KEY = "unit-test-app-key-0123456789"
DEV = "device-aaaa-1111"


@pytest.fixture
def app_client(tmp_path, static_dir):
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}", redis_url=None,
                 secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                 static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                 require_app=True, app_keys=[KEY], dev=False, seed_loadtest_users=0, seed_demo=True,
                 single_process=True)
    with TestClient(create_app(s)) as c:
        yield c


def trial_proof(c, h, device=DEV, key=KEY, build=None):
    nonce = c.get("/api/student/trial/challenge", headers=h).json()["nonce"]
    p = {"device_id": device, "nonce": nonce, "signature": sign(key, start_message(nonce, device)),
         "version": "0.5"}
    if build:
        p["build"] = build
    return p


def test_trial_needs_the_app_like_a_real_exam(app_client):
    c = app_client
    h = login(c, "alice")
    r = trial_start(c, h)
    assert r.status_code == 403 and "FairPlay app" in r.json()["detail"]
    assert trial_start(c, h, {"client": trial_proof(c, h, key="wrong-key")}).status_code == 403
    assert trial_start(c, h, {"client": trial_proof(c, h, build="dev")}).status_code == 403
    p = trial_proof(c, h)
    r = trial_start(c, h, {"client": p})
    assert r.status_code == 200, r.text
    assert trial_start(c, h, {"client": p}).status_code == 403          # nonce is single use
    # a real exam's challenge does not start the sample exam
    t = login(c, "teacher")
    e = c.post("/api/exams", json=SAMPLE_EXAM, headers=t).json()
    e = c.post(f"/api/exams/{e['id']}/open", headers=t).json()
    nonce = c.get(f"/api/student/exams/{e['id']}/challenge", headers=h).json()["nonce"]
    bad = {"device_id": DEV, "nonce": nonce, "signature": sign(KEY, start_message(nonce, DEV)), "version": "0.5"}
    assert trial_start(c, h, {"client": bad}).status_code == 403


def test_signed_handin_says_submitted_after_stop(app_client):
    c = app_client
    h = login(c, "alice")
    v = trial_start(c, h, {"client": trial_proof(c, h)}).json()
    aid, s = v["attempt_id"], v["session"]
    beat = {"session": s, "device_id": DEV, "ts": int(time.time() * 1000), "locked": True,
            "checks": "vm=0", "signature": ""}
    beat["signature"] = sign(KEY, beat_message(aid, s, DEV, beat["ts"], True, "vm=0"))
    assert c.post(f"/api/student/attempts/{aid}/app-beat", headers={**h, "X-FairPlay-Session": s},
                  json=beat).status_code == 200

    def handin():
        r = c.get(f"/api/student/attempts/{aid}/handin", params={"nonce": "nonce-12345678", "device": DEV},
                  headers={**h, "X-FairPlay-Session": s}).json()
        assert r["proofs"] == [sign(KEY, handin_message(aid, s, r["status"], "nonce-12345678"))]
        return r["status"]
    assert handin() == "in_progress"
    assert c.post(f"/api/student/attempts/{aid}/trial-stop",
                  headers={**h, "X-FairPlay-Session": s}).status_code == 200
    assert handin() == "submitted"
    # right after the stop, the app's own beats hear "submitted" too
    beat["ts"] += 1
    beat["signature"] = sign(KEY, beat_message(aid, s, DEV, beat["ts"], True, "vm=0"))
    r = c.post(f"/api/student/attempts/{aid}/app-beat", headers={**h, "X-FairPlay-Session": s}, json=beat)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    # a replaced attempt answers "gone" (signed), never ends someone else's lock
    v2 = trial_start(c, h, {"client": trial_proof(c, h)}).json()
    assert v2["attempt_id"] != aid
    assert handin() == "gone"


def test_trial_key_gives_the_offline_exit_key_for_the_sample_only(app_client):
    from app.app_client import offline_exit_code, offline_key
    c = app_client
    h = login(c, "alice")
    v = trial_start(c, h, {"client": trial_proof(c, h)}).json()
    aid, s = v["attempt_id"], v["session"]
    hs = {**h, "X-FairPlay-Session": s}
    r = c.get(f"/api/student/attempts/{aid}/trial-key", headers=hs)
    assert r.status_code == 200
    key = bytes.fromhex(r.json()["key"])
    assert key == offline_key("test-secret-0123456789abcdef", aid, s)
    assert len(offline_exit_code(key, "K7QM3XPA")) == 8
    assert c.get(f"/api/student/attempts/{aid}/trial-key",
                 headers={**h, "X-FairPlay-Session": "other"}).status_code == 409
    assert c.get(f"/api/student/attempts/{aid}/trial-key", headers=login(c, "bob")).status_code == 404
    # a real exam never hands its key to the page
    t = login(c, "teacher")
    e = c.post("/api/exams", json=SAMPLE_EXAM, headers=t).json()
    e = c.post(f"/api/exams/{e['id']}/open", headers=t).json()
    nonce = c.get(f"/api/student/exams/{e['id']}/challenge", headers=h).json()["nonce"]
    rv = c.post(f"/api/student/exams/{e['id']}/start", headers=h, json={"start_code": e["start_code"], "client": {
        "device_id": DEV, "nonce": nonce, "signature": sign(KEY, start_message(nonce, DEV)), "version": "0.5"}}).json()
    assert c.get(f"/api/student/attempts/{rv['attempt_id']}/trial-key",
                 headers={**h, "X-FairPlay-Session": rv["session"]}).status_code == 403
    assert c.post(f"/api/student/attempts/{rv['attempt_id']}/trial-stop",
                  headers={**h, "X-FairPlay-Session": rv["session"]}).status_code == 403
    # after the stop there is no key any more
    c.post(f"/api/student/attempts/{aid}/trial-stop", headers=hs)
    assert c.get(f"/api/student/attempts/{aid}/trial-key", headers=hs).status_code == 409


def test_killed_run_never_waits_for_a_teacher(client, alice):
    v = trial_start(client, alice).json()
    aid = v["attempt_id"]
    client.post(f"/api/student/attempts/{aid}/events", headers={**alice, **sess(v)},
                json={"events": [{"type": "previous_run_killed", "detail": "x"}]})
    hb = client.post(f"/api/student/attempts/{aid}/heartbeat", headers={**alice, **sess(v)},
                     json={"current_question": 0, "locked": False}).json()
    assert hb["approval"] is None


# ---------------------------------------------------------------- invisible to teachers
def _hide_attempts(client, teacher, alice):
    v = trial_start(client, alice).json()
    client.post(f"/api/student/attempts/{v['attempt_id']}/events", headers={**alice, **sess(v)},
                json={"events": [{"type": "blocked_shortcut", "detail": "x"}]})
    client.post(f"/api/student/attempts/{v['attempt_id']}/submit", headers={**alice, **sess(v)},
                json={"answers": {}, "client_seq": 1})
    return v


TEACHER_LISTS = ["/api/exams", "/api/grading", "/api/gradebook", "/api/gradebook.csv", "/api/insights",
                 "/api/students", "/api/topics", "/api/scan/exams", "/api/practice/bank", "/api/classes"]
TEACHER_EXAM_PAGES = ["", "/live", "/results", "/grading", "/readiness", "/roster", "/scans", "/print"]


@pytest.mark.parametrize("owned", [False, True])
def test_trial_never_shows_for_teachers(client, teacher, alice, owned):
    if owned:
        # Even if the sample exam somehow belonged to this teacher, it stays hidden.
        tid = run_db(client, lambda conn: _uid(conn, "teacher"))
        run_db(client, lambda conn: conn.execute(update(Exam).where(Exam.id == TRIAL).values(teacher_id=tid)))
    v = _hide_attempts(client, teacher, alice)
    for url in TEACHER_LISTS:
        r = client.get(url, headers=teacher)
        assert r.status_code == 200, (url, r.text)
        assert TRIAL not in r.text and "Sample exam" not in r.text and v["attempt_id"] not in r.text, url
    for suffix in TEACHER_EXAM_PAGES:
        assert client.get(f"/api/exams/{TRIAL}{suffix}", headers=teacher).status_code == 404, suffix
    assert client.post(f"/api/exams/{TRIAL}/close", headers=teacher).status_code == 404
    assert client.get(f"/api/exams/{TRIAL}/attempts/{v['attempt_id']}/events",
                      headers=teacher).status_code == 404
    assert client.post(f"/api/exams/{TRIAL}/lms/push", headers=teacher).status_code == 404
    assert client.get("/api/students/alice/profile", headers=teacher).text.count(TRIAL) == 0


async def _uid(conn, username):
    return (await conn.execute(select(User.id).where(User.username == username))).scalar()


def test_trial_not_in_student_exam_list_or_grades(client, alice, live_exam):
    _hide_attempts(client, None, alice)
    ids = [e["id"] for e in client.get("/api/student/exams", headers=alice).json()]
    assert TRIAL not in ids and live_exam["id"] in ids
    assert client.get("/api/student/grades", headers=alice).json() == []

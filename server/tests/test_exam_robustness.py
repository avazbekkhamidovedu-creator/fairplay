"""2026-11 stress findings: an honest student must not be interrupted by a class rush, an
attacker on the same network, extra time, a fast clock, a stepped-back clock or a rotated session."""
import time
from datetime import timedelta

from sqlalchemy import select, update

from app.integrations.dkuoj.models import OjRosterEntry
from app.models import Attempt, Exam
from app.timeutil import utcnow

from .conftest import login
from .s_helpers import db_update
from .test_access_code_login import (DEV_A, DEV_B, bearer, enter, patch_exam, ready, sign_in,  # noqa: F401
                                     start as code_start)
from .test_access_code_scope import at
from .test_access_codes import KEY, db
from .test_app_session import app_client, beat, events, exam, start  # noqa: F401  (fixtures)
from .test_dkuoj_bridge import oj_exam
from .test_dkuoj_bridge_v2 import fake, oj  # noqa: F401  (fixtures)
from .test_s5_sealed_submit import mcq_answer, post, seal, setup


# ---------------------------------------------------------------- 1. class rush
def test_a_class_behind_one_address_is_not_rate_limited(oj, ready):
    for _ in range(70):
        assert enter(oj, ready["alice"], DEV_A).status_code == 200      # old cap: 60 challenges a minute
    for _ in range(35):
        sign_in(oj, ready["alice"], DEV_A)                              # old cap: 30 confirms a minute


def test_request_caps_answer_429_with_retry_after_and_follow_the_settings(oj, ready):
    s = oj.app.state.settings
    s.codelogin_challenges_per_min = s.codelogin_confirms_per_min = 2
    assert [oj.get("/api/student/code-login/challenge").status_code for _ in range(2)] == [200, 200]
    r = oj.get("/api/student/code-login/challenge")
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60
    assert r.json()["detail"]["code"] == "too_many_requests"
    for _ in range(2):
        oj.post("/api/student/code-login/confirm", json={"confirm_token": "x"})
    r = oj.post("/api/student/code-login/confirm", json={"confirm_token": "x"})
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60


def test_school_networks_multiply_the_code_login_caps(oj, monkeypatch):
    from app.integrations.dkuoj import code_login
    s = oj.app.state.settings
    s.codelogin_challenges_per_min, s.codelogin_school_factor = 2, 3
    s.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(code_login, "client_ip", lambda request: "10.1.2.3")
    assert all(oj.get("/api/student/code-login/challenge").status_code == 200 for _ in range(6))
    assert oj.get("/api/student/code-login/challenge").status_code == 429
    monkeypatch.setattr(code_login, "client_ip", lambda request: "10.9.2.3")
    assert [oj.get("/api/student/code-login/challenge").status_code for _ in range(3)] == [200, 200, 429]


# ---------------------------------------------------------------- 2. attacker on the same address
def test_wrong_codes_from_one_address_do_not_block_an_honest_device_below_the_class_cap(oj, ready):
    for i in range(60):
        assert enter(oj, "AAAAAA", f"ATTACKER-DEV-{i:04d}").status_code == 400
    assert enter(oj, ready["alice"], DEV_A).status_code == 200


def test_the_network_cap_still_blocks_right_codes_once_it_is_reached(oj, ready):
    oj.app.state.settings.codelogin_ip_fails = 20
    for i in range(20):
        assert enter(oj, "AAAAAA", f"ATTACKER-DEV-{i:04d}").status_code == 400
    r = enter(oj, ready["alice"], DEV_B)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------- 3 + 5. exam-scoped token window
def _open_and_start(oj, ready):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready["alice"], DEV_A)
    aid = code_start(oj, body, eid, DEV_A).json()["attempt_id"]
    return body, eid, aid


def _forget_scope(oj, body, eid):
    import asyncio
    from app import security
    uid = security.parse_scoped_token(oj.app.state.settings.secret, body["token"])[0]
    asyncio.run(security.forget_scope(uid, eid))


def _end_exam_minutes_ago(oj, minutes):
    meta = db(oj, lambda s: s.scalar(select(Exam.external_meta).where(Exam.external_key == KEY)))
    patch_exam(oj, external_meta={**meta, "end_time": (utcnow() - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")})


def test_extra_time_keeps_the_token_alive_past_close_plus_30_minutes(oj, ready, monkeypatch):
    body = sign_in(oj, ready["alice"], DEV_A)
    _end_exam_minutes_ago(oj, 20)

    async def extra(s):
        await s.execute(update(OjRosterEntry).values(extra_time_seconds=3600))
        await s.commit()
    db(oj, extra)
    at(monkeypatch, timedelta(minutes=40))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    at(monkeypatch, timedelta(minutes=95))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 401


def test_a_running_attempt_keeps_its_token_until_its_deadline_plus_30_minutes(oj, ready, monkeypatch):
    body, eid, aid = _open_and_start(oj, ready)
    db_update(oj, aid, deadline_at=utcnow() + timedelta(hours=2))
    _end_exam_minutes_ago(oj, 20)
    _forget_scope(oj, body, eid)
    at(monkeypatch, timedelta(minutes=90))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    at(monkeypatch, timedelta(hours=2, minutes=35))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 401


def test_a_hand_in_made_by_the_server_close_leaves_time_for_a_sealed_upload(oj, ready, monkeypatch):
    body, eid, aid = _open_and_start(oj, ready)
    db_update(oj, aid, deadline_at=utcnow() + timedelta(hours=1), status="submitted",
              submitted_at=utcnow(), auto_submitted=True)
    _end_exam_minutes_ago(oj, 20)
    _forget_scope(oj, body, eid)
    at(monkeypatch, timedelta(minutes=20))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    at(monkeypatch, timedelta(hours=1, minutes=35))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 401


# ---------------------------------------------------------------- 4. sealed clock
def test_a_seal_from_a_fast_clock_is_accepted_and_flagged(app_client, exam):
    h, a, qs = setup(app_client, exam)
    qid, answers = mcq_answer(qs)
    body = seal(a["attempt_id"], a["session"], answers, sealed_at=utcnow() + timedelta(minutes=5))
    r = post(app_client, h, a["attempt_id"], body)
    assert r.status_code == 200 and r.json()["late_sealed"] is False, r.text
    ev = events(app_client, login(app_client, "teacher"), exam)
    assert "sealed_clock_ahead" in ev and "sealed_submit" in ev


def test_a_seal_from_before_the_attempt_started_is_still_refused(app_client, exam):
    h, a, qs = setup(app_client, exam)
    qid, answers = mcq_answer(qs)
    body = seal(a["attempt_id"], a["session"], answers, sealed_at=utcnow() - timedelta(hours=5))
    assert post(app_client, h, a["attempt_id"], body).status_code == 422


# ---------------------------------------------------------------- 6. exit code
def test_exit_code_stops_the_old_session_on_beats_too(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, sess = a["attempt_id"], a["session"]
    hs = {**h, "X-FairPlay-Session": sess}
    assert beat(app_client, h, aid, sess, 1000).status_code == 200
    r = app_client.post(f"/api/student/attempts/{aid}/exit-code", headers=hs, json={"code": exam["invigilator_code"]})
    assert r.status_code == 200
    assert beat(app_client, h, aid, sess, 2000).status_code == 409


# ---------------------------------------------------------------- 7. clock step back
def test_a_clock_that_stepped_back_is_heard_again_but_a_quick_replay_is_not(app_client, exam, monkeypatch):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, sess = a["attempt_id"], a["session"]
    assert beat(app_client, h, aid, sess, 5000).status_code == 200
    assert beat(app_client, h, aid, sess, 5000).status_code == 409
    assert beat(app_client, h, aid, sess, 4000).status_code == 409
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 30)
    assert beat(app_client, h, aid, sess, 4000).status_code == 200
    assert beat(app_client, h, aid, sess, 4000).status_code == 409
    assert beat(app_client, h, aid, sess, 4005).status_code == 200

"""2026-11 stress findings: an honest student must not be interrupted by extra time, a fast clock, a stepped-back clock or a rotated session."""
import time
from datetime import timedelta

from app import security
from app.timeutil import utcnow

from .conftest import login
from .oj_helpers import DEV_A, bearer, ready, sign_in  # noqa: F401
from .test_app_session import app_client, beat, events, exam, start  # noqa: F401  (fixtures)
from .test_dkuoj_bridge_v2 import fake, oj  # noqa: F401  (fixtures)
from .test_s5_sealed_submit import mcq_answer, post, seal, setup


# ---------------------------------------------------------------- 3 + 5. (removed 2026-11)
# The exam-scoped token window went with the access-code sign-in: no such token is issued, and
# one from an older build is refused (test_scoped_tokens_are_refused below).
def test_scoped_tokens_are_refused(oj, ready):
    from app.security import make_token
    body = sign_in(oj, ready["alice"], DEV_A)
    uid = security.parse_scoped_token(oj.app.state.settings.secret, body["token"])[0]
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    for scope in ("e_123", "sealed-e_123"):
        tok = make_token(oj.app.state.settings.secret, uid, "student", 600, scope=scope)
        assert oj.get("/api/student/exams", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


# ---------------------------------------------------------------- 4. sealed clock
def test_a_seal_from_a_fast_clock_is_accepted_and_flagged(app_client, exam):
    h, a, qs = setup(app_client, exam)
    qid, answers = mcq_answer(qs)
    body = seal(a["attempt_id"], a["session"], answers, sealed_at=utcnow() + timedelta(minutes=5))
    r = post(app_client, h, a["attempt_id"], body)
    assert r.status_code == 200 and r.json()["late_sealed"] is False, r.text
    ev = events(app_client, login(app_client, "teacher"), exam)
    assert "sealed_clock_ahead" in ev and "sealed_submit" in ev


def test_a_seal_from_before_the_attempt_started_is_judged_on_arrival(app_client, exam):
    # Pilot 0.8.8: a wrong Mac clock is not a reason to refuse the answers (they used to get 422)
    h, a, qs = setup(app_client, exam)
    qid, answers = mcq_answer(qs)
    body = seal(a["attempt_id"], a["session"], answers, sealed_at=utcnow() - timedelta(hours=5))
    assert post(app_client, h, a["attempt_id"], body).status_code == 200


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

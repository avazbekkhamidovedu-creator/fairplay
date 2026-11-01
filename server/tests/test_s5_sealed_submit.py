"""S5 (2026-10-20): POST /api/student/attempts/{aid}/sealed-submit."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from app import app_client as ac
from app.timeutil import iso, utcnow

from .conftest import login
from .s_helpers import db_update, forget_double_click
from .test_app_session import DEV_A, app_client, beat, exam, start  # noqa: F401  (fixtures)

SECRET = "test-secret-0123456789abcdef"     # app_client's server secret


def seal(aid, session, answers, *, device=DEV_A, seq=7, sealed_at=None, secret=SECRET,
         answers_json=None, sha=None):
    """What the app writes: payload, answers_json, proof (computed here independently)."""
    answers_json = answers_json if answers_json is not None else json.dumps(answers)
    sha = sha or hashlib.sha256(answers_json.encode()).hexdigest()
    when = iso(sealed_at or utcnow())
    payload = f"{aid}|{session}|{device}|{seq}|{when}|{sha}"
    key = ac.offline_key(secret, aid, session)
    proof = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return {"payload": payload, "answers_json": answers_json, "proof": proof}


def setup(c, exam):
    h = login(c, "alice")
    a = start(c, h, exam).json()
    return h, a, {q["id"]: q for q in a["questions"]}


def results_row(c, exam):
    t = login(c, "teacher")
    return c.get(f"/api/exams/{exam['id']}/results", headers=t).json()["attempts"][0]


def post(c, h, aid, body):
    # Bearer only: no X-FairPlay-Session (the session may be gone by now)
    return c.post(f"/api/student/attempts/{aid}/sealed-submit", headers=h, json=body)


def mcq_answer(qs):
    qid = next(q for q in qs.values() if q["type"] == "mcq")["id"]
    return qid, {qid: 1}


def test_good_seal_hands_in_and_is_idempotent(app_client, exam):
    h, a, qs = setup(app_client, exam)
    qid, answers = mcq_answer(qs)
    body = seal(a["attempt_id"], a["session"], answers)
    r = post(app_client, h, a["attempt_id"], body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["late_sealed"] is False and len(out["receipt"]) == 9
    row = results_row(app_client, exam)
    assert row["status"] == "submitted" and row["receipt"] == out["receipt"] and row["score"] == 2
    assert row["late_sealed"] is False
    ev = [e for e in row["events"] if e["type"] == "sealed_submit"]
    assert len(ev) == 1 and ev[0]["review"] == "open"      # always in the review list
    assert "sealed offline" in ev[0]["detail"]
    # same file again (lost reply, app retry): same receipt, no second event
    again = post(app_client, h, a["attempt_id"], body)
    assert again.status_code == 200 and again.json() == out
    assert [e["type"] for e in results_row(app_client, exam)["events"]].count("sealed_submit") == 1


def test_bad_proof_bad_sha_bad_payload(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    wrong_key = seal(aid, a["session"], answers, secret="another-server-secret-123")
    assert post(app_client, h, aid, wrong_key).status_code == 403
    good = seal(aid, a["session"], answers)
    tampered = {**good, "proof": "0" * 64}
    assert post(app_client, h, aid, tampered).status_code == 403
    # proof fine but the answers are not the sealed ones
    other = {**good, "answers_json": json.dumps({k: 2 for k in answers})}
    assert post(app_client, h, aid, other).status_code == 422
    # payload names a hash the answers don't have (signed correctly)
    bad_sha = seal(aid, a["session"], answers, sha="a" * 64)
    assert post(app_client, h, aid, bad_sha).status_code == 422
    assert post(app_client, h, aid, {**good, "payload": "a|b|c"}).status_code == 422
    # sealed for another attempt id
    elsewhere = seal("a_other123", a["session"], answers)
    assert post(app_client, h, aid, elsewhere).status_code == 422
    # someone else's attempt: not found
    bob = login(app_client, "bob")
    assert post(app_client, bob, aid, good).status_code == 404
    assert results_row(app_client, exam)["status"] == "in_progress"


def test_sealed_at_must_lie_in_the_attempt(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    before = utcnow() - timedelta(hours=1)
    assert post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=before)).status_code == 422
    soon = utcnow() + timedelta(seconds=60)                     # a little ahead: clamped to now
    assert post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=soon)).status_code == 200


def test_sealed_after_deadline_is_late_sealed(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    qid, answers = mcq_answer(qs)
    hs = {**h, "X-FairPlay-Session": a["session"]}
    app_client.put(f"/api/student/attempts/{aid}/answers", headers=hs,
                   json={"answers": {qid: 0}, "client_seq": 1})            # saved in time (wrong)
    started = datetime.fromisoformat(a["started_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    # the time was up 5 minutes ago; the app sealed only now (well after the 60 s grace)
    db_update(app_client, aid, started_at=started - timedelta(minutes=10),
              deadline_at=started - timedelta(minutes=5))
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=utcnow()))
    assert r.status_code == 200 and r.json()["late_sealed"] is True
    row = results_row(app_client, exam)
    assert row["late"] is True and row["late_sealed"] is True
    assert row["late_answers"] == answers and row["score"] == 0           # graded: saved in time
    ev = next(e for e in row["events"] if e["type"] == "sealed_submit")
    assert "after the time was up" in ev["detail"]


def test_session_rotated_still_verifies(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    body = seal(aid, a["session"], answers)
    forget_double_click(aid)
    again = start(app_client, h, exam).json()                  # app restarted: new session
    assert again["session"] != a["session"]
    stale = {**h, "X-FairPlay-Session": a["session"]}         # even with the old header
    r = post(app_client, stale, aid, body)
    assert r.status_code == 200 and r.json()["late_sealed"] is False


def test_after_normal_submit_same_or_different(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    qid, answers = mcq_answer(qs)
    hs = {**h, "X-FairPlay-Session": a["session"]}
    sub = app_client.post(f"/api/student/attempts/{aid}/submit", headers=hs,
                          json={"answers": answers, "client_seq": 3}).json()
    same = post(app_client, h, aid, seal(aid, a["session"], answers))
    assert same.status_code == 200 and same.json()["receipt"] == sub["receipt"]
    diff = post(app_client, h, aid, seal(aid, a["session"], {qid: 2}, seq=9))
    assert diff.status_code == 409 and diff.json()["detail"] == {"code": "already_submitted"}
    row = results_row(app_client, exam)
    assert row["receipt"] == sub["receipt"] and row["score"] == 2             # grade unchanged
    assert row["late_answers"] == {qid: 2}                                     # kept for the teacher
    evs = [e["detail"] for e in row["events"] if e["type"] == "sealed_submit"]
    assert len(evs) == 2 and any("you decide" in d for d in evs)


def test_beat_sealed_shows_in_live_room(app_client, exam):
    h, a, _ = setup(app_client, exam)
    hs = {**h, "X-FairPlay-Session": a["session"]}
    assert beat(app_client, hs, a["attempt_id"], a["session"], 1, checks="vm=0,sealed=1").status_code == 200
    t = login(app_client, "teacher")
    st = app_client.get(f"/api/exams/{exam['id']}/live", headers=t).json()["students"][0]
    assert st["sealed_pending"] is True
    beat(app_client, hs, a["attempt_id"], a["session"], 2, checks="vm=0")
    st = app_client.get(f"/api/exams/{exam['id']}/live", headers=t).json()["students"][0]
    assert st["sealed_pending"] is False


def test_payload_format_vector():
    """Byte-exact example for the app side (Swift must produce the same proof)."""
    key = ac.offline_key(SECRET, "a_test", "sess1234")
    payload = "a_test|sess1234|hw-1234|5|2026-10-20T10:00:00.000Z|" + hashlib.sha256(b'{"q_1":1}').hexdigest()
    assert ac.seal_proof(SECRET, "a_test", "sess1234", payload) == \
        hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    s = ac.parse_seal(payload)
    assert s and s.client_seq == 5 and s.sealed_at == datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc).replace(tzinfo=None)


def test_sealed_event_does_not_vouch_for_the_clients_clock(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    assert post(app_client, h, aid, seal(aid, a["session"], answers)).status_code == 200
    ev = next(e for e in results_row(app_client, exam)["events"] if e["type"] == "sealed_submit")
    assert "in time by the seal's own clock" in ev["detail"] and "last app signal" in ev["detail"]

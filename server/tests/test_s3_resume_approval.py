"""S3 (2026-10-20): coming back after FairPlay closed waits for the teacher."""
from datetime import datetime, timedelta

from app.timeutil import utcnow

from .conftest import SAMPLE_EXAM, login
from .s_helpers import db_update, drop_attempt_cache, forget_double_click
from .test_app_session import DEV_A, DEV_B, app_client, beat, exam, start  # noqa: F401  (fixtures)


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def relaunch(c, h, exam):
    """FairPlay crashed and was opened again on the same computer (new app run)."""
    first = start(c, h, exam).json()
    forget_double_click(first["attempt_id"])
    return first, start(c, h, exam).json()


def live_student(c, exam):
    t = login(c, "teacher")
    return c.get(f"/api/exams/{exam['id']}/live", headers=t).json()["students"][0]


def test_new_app_run_waits_then_teacher_approves(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    first, again = relaunch(app_client, h, exam)
    assert first["needs_approval"] is False and first["approval"] is None
    assert again["needs_approval"] is True and again["approval"] == "pending"
    aid, sess = again["attempt_id"], again["session"]
    hs = {**h, "X-FairPlay-Session": sess}
    assert app_client.get(f"/api/student/attempts/{aid}", headers=hs).json()["approval"] == "pending"
    assert beat(app_client, hs, aid, sess, 1).json()["approval"] == "pending"
    hb = app_client.post(f"/api/student/attempts/{aid}/heartbeat", headers=hs, json={"current_question": 0})
    assert hb.json()["approval"] == "pending"
    # answers are still accepted while waiting: no work is lost
    qid = again["questions"][0]["id"]
    put = app_client.put(f"/api/student/attempts/{aid}/answers", headers=hs, json={"answers": {qid: 1}, "client_seq": 1})
    assert put.json()["saved"] is True
    st = live_student(app_client, exam)
    assert st["approval"] == "pending" and st["pending_since"].endswith("Z")
    # waited 5 minutes: those come back on the deadline
    db_update(app_client, aid, pending_since=utcnow() - timedelta(minutes=5))
    before = parse(again["deadline_at"])
    r = app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-resume", headers=t)
    assert r.status_code == 200 and 4.9 <= r.json()["minutes_added"] <= 5.1
    assert timedelta(minutes=4, seconds=55) <= parse(r.json()["deadline_at"]) - before <= timedelta(minutes=5, seconds=5)
    assert beat(app_client, hs, aid, sess, 2).json()["approval"] == "approved"
    view = app_client.get(f"/api/student/attempts/{aid}", headers=hs).json()
    assert view["approval"] == "approved" and view["deadline_at"] == r.json()["deadline_at"]
    assert live_student(app_client, exam)["approval"] == "approved"
    res = app_client.get(f"/api/exams/{exam['id']}/results", headers=t).json()["attempts"][0]
    types = [e["type"] for e in res["events"]]
    assert "resume_requested" in types and "resume_approved" in types
    # nothing left to approve
    again2 = app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-resume", headers=t)
    assert again2.status_code == 409


def test_extension_capped_at_15_minutes(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    _, again = relaunch(app_client, h, exam)
    aid = again["attempt_id"]
    db_update(app_client, aid, pending_since=utcnow() - timedelta(hours=1))
    r = app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-resume", headers=t).json()
    assert r["minutes_added"] == 15
    assert parse(r["deadline_at"]) - parse(again["deadline_at"]) == timedelta(minutes=15)


def test_page_reload_and_double_click_do_not_ask(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid = a["attempt_id"]
    hs = {**h, "X-FairPlay-Session": a["session"]}
    # a page reload only GETs the attempt with the session it kept
    for _ in range(3):
        assert app_client.get(f"/api/student/attempts/{aid}", headers=hs).json()["approval"] is None
    # a double click on Start within 15 s: same session, no waiting
    dbl = start(app_client, h, exam).json()
    assert dbl["session"] == a["session"] and dbl["needs_approval"] is False
    drop_attempt_cache(aid)
    assert beat(app_client, hs, aid, a["session"], 1).json()["approval"] is None


def test_moving_to_another_computer_does_not_ask(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    app_client.post(f"/api/exams/{exam['id']}/attempts/{a['attempt_id']}/release-device", headers=t)
    moved = start(app_client, h, exam, device=DEV_B).json()
    assert moved["needs_approval"] is False and moved["approval"] is None


def test_setting_off_never_asks(app_client):
    t = login(app_client, "teacher")
    e = app_client.post("/api/exams", json={**SAMPLE_EXAM, "resume_needs_approval": False}, headers=t).json()
    assert e["resume_needs_approval"] is False
    e = app_client.post(f"/api/exams/{e['id']}/open", headers=t).json()
    h = login(app_client, "alice")
    _, again = relaunch(app_client, h, e)
    assert again["needs_approval"] is False and again["approval"] is None


def test_setting_defaults_on_and_survives_put(app_client):
    t = login(app_client, "teacher")
    e = app_client.post("/api/exams", json=SAMPLE_EXAM, headers=t).json()
    assert e["resume_needs_approval"] is True
    body = {**SAMPLE_EXAM, "resume_needs_approval": False}
    e = app_client.put(f"/api/exams/{e['id']}", json=body, headers=t).json()
    assert e["resume_needs_approval"] is False
    e = app_client.put(f"/api/exams/{e['id']}", json=SAMPLE_EXAM, headers=t).json()   # omitted: kept
    assert e["resume_needs_approval"] is False


def test_relaunch_that_keeps_its_session_waits_too(app_client, exam):
    """Mac/Windows after a kill: the app reopens the exam page, which keeps its saved
    session (no start). The app's previous_run_killed event makes it wait as well."""
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, sess = a["attempt_id"], a["session"]
    hs = {**h, "X-FairPlay-Session": sess}
    ev = {"events": [{"type": "previous_run_killed", "detail": "FairPlay ended while locked last time",
                      "client_time": "2026-10-20T10:00:00Z"}]}
    assert app_client.post(f"/api/student/attempts/{aid}/events", headers=hs, json=ev).status_code == 200
    view = app_client.get(f"/api/student/attempts/{aid}", headers=hs).json()
    assert view["approval"] == "pending"
    assert beat(app_client, hs, aid, sess, 1).json()["approval"] == "pending"
    # a second report doesn't start a new wait
    app_client.post(f"/api/student/attempts/{aid}/events", headers=hs, json=ev)
    t = login(app_client, "teacher")
    res = app_client.get(f"/api/exams/{exam['id']}/results", headers=t).json()["attempts"][0]
    assert [e["type"] for e in res["events"]].count("resume_requested") == 1
    assert app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-resume", headers=t).status_code == 200
    assert app_client.get(f"/api/student/attempts/{aid}", headers=hs).json()["approval"] == "approved"


def test_kill_report_ignored_when_setting_off(app_client):
    t = login(app_client, "teacher")
    e = app_client.post("/api/exams", json={**SAMPLE_EXAM, "resume_needs_approval": False}, headers=t).json()
    exam = app_client.post(f"/api/exams/{e['id']}/open", headers=t).json()
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    hs = {**h, "X-FairPlay-Session": a["session"]}
    ev = {"events": [{"type": "previous_run_killed", "detail": "x", "client_time": "2026-10-20T10:00:00Z"}]}
    app_client.post(f"/api/student/attempts/{a['attempt_id']}/events", headers=hs, json=ev)
    assert app_client.get(f"/api/student/attempts/{a['attempt_id']}", headers=hs).json()["approval"] is None

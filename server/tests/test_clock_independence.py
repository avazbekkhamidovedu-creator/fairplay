"""Pilot 0.8.8: a student's Mac clock is not a requirement. A client whose clock is +/-1 hour
but that sends server-corrected time is accepted like any other; one that still sends wrong
times is noted for the teacher and never refused."""
import time
from datetime import timedelta

from app.timeutil import utcnow

from .conftest import login
from .test_app_session import DEV_A, app_client, beat, events, exam, start  # noqa: F401
from .test_s5_sealed_submit import mcq_answer, post, results_row, seal, setup


def _ms(offset_s=0):
    return int((time.time() + offset_s) * 1000)


def test_corrected_time_of_a_wrong_clock_is_accepted_without_a_note(app_client, exam):
    # The app's own clock is 1 h off, but it signs with ServerClock (server time): to the
    # server this is an ordinary beat. Same for 1 h behind.
    for i, mac_offset in enumerate((3600, -3600)):
        h = login(app_client, "alice" if i == 0 else "bob")
        a = start(app_client, h, exam).json()
        corrected = _ms(0)          # what the app sends: server time, whatever Date() says
        assert mac_offset != 0
        r = beat(app_client, h, a["attempt_id"], a["session"], corrected)
        assert r.status_code == 200, r.text
        r = beat(app_client, h, a["attempt_id"], a["session"], corrected + 5000)
        assert r.status_code == 200
    t = login(app_client, "teacher")
    assert "clock_off" not in events(app_client, t, exam)


def test_uncorrected_wrong_times_are_accepted_but_noted(app_client, exam):
    for who, off in (("alice", 3600), ("bob", -3600)):
        h = login(app_client, who)
        a = start(app_client, h, exam).json()
        r = beat(app_client, h, a["attempt_id"], a["session"], _ms(off))
        assert r.status_code == 200, r.text
    t = login(app_client, "teacher")
    assert "clock_off" in events(app_client, t, exam)


def test_seal_from_a_clock_one_hour_ahead_or_behind(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    # corrected seal time: nothing special
    assert post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=utcnow())).status_code == 200
    row = results_row(app_client, exam)
    assert row["status"] == "submitted" and row["late_sealed"] is False
    types = [e["type"] for e in row["events"]]
    assert "sealed_clock_off" not in types and "sealed_clock_ahead" not in types


def test_seal_from_a_clock_one_hour_ahead_is_judged_on_arrival(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=utcnow() + timedelta(hours=1)))
    assert r.status_code == 200 and r.json()["late_sealed"] is False
    assert "sealed_clock_ahead" in [e["type"] for e in results_row(app_client, exam)["events"]]


def test_seal_from_a_clock_one_hour_behind_is_judged_on_arrival(app_client, exam):
    h, a, qs = setup(app_client, exam)
    aid = a["attempt_id"]
    _, answers = mcq_answer(qs)
    r = post(app_client, h, aid, seal(aid, a["session"], answers, sealed_at=utcnow() - timedelta(hours=1)))
    assert r.status_code == 200
    assert "sealed_clock_off" in [e["type"] for e in results_row(app_client, exam)["events"]]


def test_client_event_names_are_kept_and_client_time_is_only_a_note(app_client, exam):
    from app import app_client as ac
    for t in ("win_foreign_desktop", "win_watchdog_missing", "win_session_locked", "lock_ended_by_restart",
              "deadline_unlock", "client_lock_fallback"):
        assert ac.client_event_type(t) == t
    assert ac.client_event_type("second_device") == "client_second_device"     # server-only names stay fenced
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    hs = {**h, "X-FairPlay-Session": a["session"]}
    r = app_client.post(f"/api/student/attempts/{a['attempt_id']}/events", headers=hs, json={"events": [
        {"type": "win_foreign_desktop", "detail": "x", "client_time": "2001-01-01T00:00:00Z"}]})
    assert r.status_code == 200, r.text
    t = login(app_client, "teacher")
    res = app_client.get(f"/api/exams/{exam['id']}/results", headers=t).json()
    ev = [e for x in res["attempts"] for e in x["events"] if e["type"] == "win_foreign_desktop"][0]
    assert ev["client_time"] == "2001-01-01T00:00:00Z"            # shown as reported
    assert ev["server_time"].startswith(str(utcnow().year))        # the server's own clock decides

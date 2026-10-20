"""S6 (2026-10-20): require_strong_lock, beat sep=0|1, weak_lock once per attempt."""
from .conftest import SAMPLE_EXAM, login
from .test_app_session import app_client, beat, exam, start  # noqa: F401  (fixtures)


def open_exam(c, **settings):
    t = login(c, "teacher")
    e = c.post("/api/exams", json={**SAMPLE_EXAM, **settings}, headers=t).json()
    return c.post(f"/api/exams/{e['id']}/open", headers=t).json()


def weak_events(c, e):
    t = login(c, "teacher")
    res = c.get(f"/api/exams/{e['id']}/results", headers=t).json()["attempts"][0]
    return [ev for ev in res["events"] if ev["type"] == "weak_lock"]


def live(c, e):
    t = login(c, "teacher")
    return c.get(f"/api/exams/{e['id']}/live", headers=t).json()["students"][0]


def test_setting_default_off_and_stored(app_client):
    e = open_exam(app_client)
    assert e["require_strong_lock"] is False
    e2 = open_exam(app_client, require_strong_lock=True, title="Strong")
    assert e2["require_strong_lock"] is True


def test_weak_lock_flagged_once(app_client):
    e = open_exam(app_client, require_strong_lock=True)
    h = login(app_client, "alice")
    a = start(app_client, h, e).json()
    hs = {**h, "X-FairPlay-Session": a["session"]}
    aid, sess = a["attempt_id"], a["session"]
    assert beat(app_client, hs, aid, sess, 1, checks="vm=0,sep=1").status_code == 200
    assert weak_events(app_client, e) == [] and live(app_client, e)["weak_lock"] is False
    assert beat(app_client, hs, aid, sess, 2, checks="vm=0").status_code == 200       # no sep key (Mac)
    assert beat(app_client, hs, aid, sess, 3, checks="vm=0,sep=0").status_code == 200
    evs = weak_events(app_client, e)
    assert len(evs) == 1 and evs[0]["review"] == "open"
    assert live(app_client, e)["weak_lock"] is True
    beat(app_client, hs, aid, sess, 4, checks="sep=1")
    assert live(app_client, e)["weak_lock"] is False and len(weak_events(app_client, e)) == 1


def test_no_flag_when_not_required(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    hs = {**h, "X-FairPlay-Session": a["session"]}
    beat(app_client, hs, a["attempt_id"], a["session"], 1, checks="sep=0")
    assert weak_events(app_client, exam) == [] and live(app_client, exam)["weak_lock"] is False

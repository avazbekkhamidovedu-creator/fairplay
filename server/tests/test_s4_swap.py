"""S4 (2026-10-20): moving an attempt to another computer (swap request / approve)."""
from datetime import timedelta

from app.app_client import handin_message, sign
from app.timeutil import utcnow

from .conftest import login
from .s_helpers import db_update
from .test_app_session import DEV_A, DEV_B, KEY, app_client, beat, exam, start  # noqa: F401


def live_student(c, exam):
    t = login(c, "teacher")
    return c.get(f"/api/exams/{exam['id']}/live", headers=t).json()["students"][0]


def put(c, h, aid, answers, seq):
    return c.put(f"/api/student/attempts/{aid}/answers", headers=h, json={"answers": answers, "client_seq": seq})


def handin(c, h, aid, device, nonce="nonce-swap-1234"):
    return c.get(f"/api/student/attempts/{aid}/handin", params={"nonce": nonce, "device": device}, headers=h).json()


def test_second_device_gets_object_detail_then_swaps(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, old = a["attempt_id"], a["session"]
    r = start(app_client, h, exam, device=DEV_B)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "swap_needed" and "another computer" in d["message"]
    assert app_client.post(f"/api/student/attempts/{aid}/swap-request", headers=h,
                           json={"device_id": DEV_B}).status_code == 202
    assert live_student(app_client, exam)["swap_requested"] is True
    # still refused until the teacher approves
    assert start(app_client, h, exam, device=DEV_B).status_code == 409
    ok = app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-swap", headers=t)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    new = start(app_client, h, exam, device=DEV_B)
    assert new.status_code == 200 and new.json()["session"] != old
    assert new.json()["needs_approval"] is False
    assert live_student(app_client, exam)["swap_requested"] is False
    # the old computer: beat refused with the session error; hand-in check says "moved",
    # signed with the session it sent
    hs_old = {**h, "X-FairPlay-Session": old}
    assert beat(app_client, hs_old, aid, old, 5).status_code == 409
    r = handin(app_client, hs_old, aid, DEV_A)
    assert r["status"] == "moved"
    assert r["proofs"] == [sign(KEY, handin_message(aid, old, "moved", "nonce-swap-1234"))]
    res = app_client.get(f"/api/exams/{exam['id']}/results", headers=t).json()["attempts"][0]
    types = [e["type"] for e in res["events"]]
    assert {"second_device", "swap_requested", "swap_approved", "device_bound"} <= set(types)


def test_old_session_final_save_window(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, old = a["attempt_id"], a["session"]
    q1, q2, q3 = [q["id"] for q in a["questions"]]
    hs_old = {**h, "X-FairPlay-Session": old}
    assert put(app_client, hs_old, aid, {q1: 1}, 1).json()["saved"] is True
    app_client.post(f"/api/student/attempts/{aid}/swap-request", headers=h, json={"device_id": DEV_B})
    app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-swap", headers=t)
    b = start(app_client, h, exam, device=DEV_B).json()
    hs_new = {**h, "X-FairPlay-Session": b["session"]}
    # the new computer changes q2 after the approval
    assert put(app_client, hs_new, aid, {q1: 1, q2: 3.14}, 2).json()["saved"] is True
    # the old computer's last save: its q2 must not overwrite, its new q3 and q1 change do
    r = put(app_client, hs_old, aid, {q1: 2, q2: 9.99, q3: "Paris"}, 2)
    assert r.status_code == 200 and r.json()["saved"] is True and r.json()["merged"] == 2
    view = app_client.get(f"/api/student/attempts/{aid}", headers=hs_new).json()
    assert view["answers"] == {q1: 2, q2: 3.14, q3: "Paris"}
    # only one final save
    assert put(app_client, hs_old, aid, {q3: "Lyon"}, 3).status_code == 409


def test_final_save_window_expires(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, old = a["attempt_id"], a["session"]
    q1 = a["questions"][0]["id"]
    app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-swap", headers=t)
    db_update(app_client, aid, swap_approved_at=utcnow() - timedelta(seconds=121))
    r = put(app_client, {**h, "X-FairPlay-Session": old}, aid, {q1: 2}, 5)
    assert r.status_code == 409 and "somewhere else" in r.json()["detail"]


def test_release_device_old_computer_sees_moved(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid, old = a["attempt_id"], a["session"]
    app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/release-device", headers=t)
    r = handin(app_client, {**h, "X-FairPlay-Session": old}, aid, DEV_A, nonce="nonce-rel-12345")
    assert r["status"] == "moved"
    assert r["proofs"] == [sign(KEY, handin_message(aid, old, "moved", "nonce-rel-12345"))]


def test_no_swap_after_deadline(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid = a["attempt_id"]
    now = utcnow()
    db_update(app_client, aid, started_at=now - timedelta(minutes=40), deadline_at=now - timedelta(minutes=2))
    r = app_client.post(f"/api/student/attempts/{aid}/swap-request", headers=h, json={"device_id": DEV_B})
    assert r.status_code == 409 and r.json()["detail"] == "Finish on the computer you started on"
    r = app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-swap", headers=t)
    assert r.status_code == 409 and r.json()["detail"] == "Finish on the computer you started on"
    # within the 60 s grace it still works
    db_update(app_client, aid, deadline_at=utcnow() - timedelta(seconds=30))
    assert app_client.post(f"/api/student/attempts/{aid}/swap-request", headers=h,
                           json={"device_id": DEV_B}).status_code == 202


def test_swap_request_checks(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    aid = a["attempt_id"]
    url = f"/api/student/attempts/{aid}/swap-request"
    assert app_client.post(url, headers=h, json={"device_id": "bad id!"}).status_code == 422
    bob = login(app_client, "bob")
    assert app_client.post(url, headers=bob, json={"device_id": DEV_B}).status_code == 404
    t = login(app_client, "teacher")
    hs = {**h, "X-FairPlay-Session": a["session"]}
    app_client.post(f"/api/student/attempts/{aid}/submit", headers=hs, json={"answers": {}, "client_seq": 1})
    assert app_client.post(url, headers=h, json={"device_id": DEV_B}).status_code == 409
    assert app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-swap", headers=t).status_code == 409

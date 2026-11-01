"""A reissued access code also frees the student's running attempt from its old device, and the
student rows of GET /exams/<key> tell the professor how FairPlay is doing (fp_mode, device_state,
second_device_attempts, last_beat_at)."""
import json

from app.app_client import beat_message, sign
from app.integrations.dkuoj import routes_v2
from app.integrations.dkuoj.models import ClassLoginEvent
from app.models import IntegrityEvent
from app.store import get_store

from .test_access_code_login import APP_KEY, DEV_A, DEV_B, bearer, confirm, enter, sign_in, start
from .test_access_codes import KEY
from .test_dkuoj_bridge_v2 import V1, call, oj  # noqa: F401  (fixtures)
from .test_dkuoj_rescue import (NOT_VALID, attempts_of, fake, kinds, put, qid_of, room, started, submit,  # noqa: F401
                                view)

REISSUE = f"{V1}/exams/{KEY}/access-codes/reissue"


def reissue(c, netid="alice"):
    r = call(c, "POST", REISSUE, {"netid": netid, "reason": "laptop broke"})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def write(c, body, a, answers, seq):
    return c.put(f"/api/student/attempts/{a['attempt_id']}/answers", json={"answers": answers, "client_seq": seq},
                 headers={**bearer(body), "X-FairPlay-Session": a["session"]})


def app_beat(c, body, a, device, ts, locked=True):
    checks = "vm=0,displays=1"
    return c.post(f"/api/student/attempts/{a['attempt_id']}/app-beat", headers=bearer(body), json={
        "session": a["session"], "device_id": device, "ts": ts, "locked": locked, "checks": checks,
        "signature": sign(APP_KEY, beat_message(a["attempt_id"], a["session"], device, ts, locked, checks))})


def test_new_code_on_the_same_device_resumes_the_attempt(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    q5 = qid_of(a)
    put(oj, body, a, {q5: "kept"}, 1)
    deadline = attempts_of(oj)[0].deadline_at

    code = reissue(oj)
    mine = attempts_of(oj)[0]
    assert mine.device_id is None and mine.device_pub is None and mine.session_id != a["session"]
    assert mine.status == "in_progress" and mine.answers == {q5: "kept"} and mine.deadline_at == deadline

    step = enter(oj, code, DEV_A)
    assert step.status_code == 200 and step.json()["resume"] is True
    body2 = confirm(oj, step.json()["confirm_token"]).json()
    b = started(oj, body2, eid, DEV_A)
    assert b["attempt_id"] == a["attempt_id"] and b["answers"] == {q5: "kept"} and b["needs_approval"] is False
    assert len(attempts_of(oj)) == 1


def test_new_code_on_a_new_device_resumes_without_approval(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    q5 = qid_of(a)
    put(oj, body, a, {q5: "kept"}, 1)

    code = reissue(oj)
    step = enter(oj, code, DEV_B)
    assert step.status_code == 200 and step.json()["resume"] is True
    body2 = confirm(oj, step.json()["confirm_token"]).json()
    assert body2["resume"] is True
    b = started(oj, body2, eid, DEV_B)
    assert b["attempt_id"] == a["attempt_id"] and b["answers"] == {q5: "kept"}
    assert b["needs_approval"] is False and "swap_needed" not in str(b)
    assert attempts_of(oj)[0].device_id == DEV_B
    assert not kinds(oj, IntegrityEvent, "second_device")


def test_the_old_code_stops_working_on_every_device(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    started(oj, body, eid, DEV_A)
    reissue(oj)
    for dev in (DEV_A, DEV_B):
        r = enter(oj, codes["alice"], dev)
        assert (r.status_code, r.json()) == (400, NOT_VALID)


def test_the_old_device_is_cut_off_and_a_second_device_is_flagged(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    q5 = qid_of(a)
    put(oj, body, a, {q5: "kept"}, 1)
    code = reissue(oj)
    assert write(oj, body, a, {q5: "old device"}, 2).status_code == 409

    body2 = sign_in(oj, code, DEV_B)
    b = started(oj, body2, eid, DEV_B)
    assert write(oj, body, a, {q5: "old device"}, 3).status_code == 409
    assert attempts_of(oj)[0].answers == {q5: "kept"}
    assert write(oj, body2, b, {q5: "new device"}, 4).status_code == 200

    r = start(oj, body, eid, DEV_A)
    assert r.status_code in (403, 409) and r.status_code != 200
    assert attempts_of(oj)[0].device_id == DEV_B


def test_stale_wrong_and_second_device_codes_still_leave_events(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    started(oj, body, eid, DEV_A)
    code = reissue(oj)
    body2 = sign_in(oj, code, DEV_B)
    started(oj, body2, eid, DEV_B)

    assert enter(oj, code, DEV_A).status_code == 400            # the old device types the new code
    assert enter(oj, codes["alice"], DEV_A).status_code == 400  # and the old one
    assert enter(oj, "ZZZZZZ", DEV_A).status_code == 400
    cls = kinds(oj, ClassLoginEvent, "access_code_second_device")
    assert len(cls) == 1 and cls[0][0] == "alice"
    assert len(kinds(oj, IntegrityEvent, "access_code_second_device")) == 1
    assert view(oj)["alice"]["second_device_attempts"] >= 1


def test_reissue_after_hand_in_only_changes_the_code(oj, room):
    eid, codes, _, _ = room
    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    submit(oj, body, a, {qid_of(a): "done"}, 1)
    before = attempts_of(oj)[0]
    sess, dev = before.session_id, before.device_id
    reissue(oj)
    after = attempts_of(oj)[0]
    assert after.status == "submitted" and after.session_id == sess and after.device_id == dev


def test_reissue_without_an_attempt_and_the_event_text(oj, room):
    eid, codes, _, _ = room
    reissue(oj, "carol")
    assert not attempts_of(oj)
    body = sign_in(oj, codes["alice"], DEV_A)
    started(oj, body, eid, DEV_A)
    reissue(oj)
    det = kinds(oj, IntegrityEvent, "access_code_reissued")
    assert len(det) == 1 and "freed" in det[0] and "laptop broke" in det[0]


def test_student_rows_carry_fp_mode_device_state_and_second_device_attempts(oj, room):
    eid, codes, _, _ = room
    row = view(oj)["alice"]
    assert (row["fp_mode"], row["device_state"], row["second_device_attempts"], row["last_beat_at"]) == (
        "not_started", "unused", 0, None)

    body = sign_in(oj, codes["alice"], DEV_A)
    a = started(oj, body, eid, DEV_A)
    row = view(oj)["alice"]
    assert (row["fp_mode"], row["device_state"], row["last_beat_at"]) == ("off", "bound", None)

    assert app_beat(oj, body, a, DEV_A, 1000).status_code == 200
    row = view(oj)["alice"]
    assert row["fp_mode"] == "on" and row["last_beat_at"]
    assert app_beat(oj, body, a, DEV_A, 2000, locked=False).status_code == 200
    assert view(oj)["alice"]["fp_mode"] == "off"
    assert app_beat(oj, body, a, DEV_A, 3000).status_code == 200
    assert view(oj)["alice"]["fp_mode"] == "on"

    store = get_store()

    async def age(delta):
        d = json.loads((await store.hgetall(f"ab:{eid}"))[a["attempt_id"]])
        d["t"] -= delta
        await store.hset(f"ab:{eid}", a["attempt_id"], json.dumps(d), 600)
    oj.portal.call(age, (routes_v2.FP_MODE_WINDOW_S + 5))
    assert view(oj)["alice"]["fp_mode"] == "off"
    assert view(oj)["alice"]["last_beat_at"]

    code = reissue(oj)
    assert view(oj)["alice"]["device_state"] == "unused"
    assert enter(oj, codes["alice"], DEV_B).status_code == 400
    body2 = sign_in(oj, code, DEV_B)
    started(oj, body2, eid, DEV_B)
    assert enter(oj, code, DEV_A).status_code == 400
    row = view(oj)["alice"]
    assert row["second_device_attempts"] >= 1 and row["device_state"] == "bound"

    assert view(oj)["carol"]["fp_mode"] == "not_started"
    submit(oj, body2, started(oj, body2, eid, DEV_B), {}, 1)
    assert view(oj)["alice"]["fp_mode"] == "submitted"


"""After the invigilator exit code is accepted the old session is dead and the attempt only
resumes with the exam's current start code (also for access-code students)."""
from app.models import Attempt

from .s_helpers import forget_double_click
from .conftest import login
from .test_app_session import DEV_A, app_client, exam, start  # noqa: F401  (fixtures)
from .test_dkuoj_bridge import db
from .test_dkuoj_bridge_v2 import oj  # noqa: F401  (fixture)
from .test_dkuoj_rescue import attempts_of, fake, qid_of, put, room  # noqa: F401  (fixtures)
from .test_exit_code_per_exam import exit_url


def aurl(a, tail):
    return f"/api/student/attempts/{a['attempt_id']}/{tail}"


def exit_now(c, h, a, exam):
    r = c.post(exit_url(a), headers={**h, "X-FairPlay-Session": a["session"]}, json={"code": exam["invigilator_code"]})
    assert r.status_code == 200, r.text


def restart(c, h, exam, code, device=DEV_A):
    from .test_app_session import proof
    return c.post(f"/api/student/exams/{exam['id']}/start", headers=h,
                  json={"start_code": code, "client": proof(c, h, exam["id"], device)})


def test_old_session_is_refused_everywhere_after_exit(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    hs = {**h, "X-FairPlay-Session": a["session"]}
    assert app_client.put(aurl(a, "answers"), headers=hs, json={"answers": {}, "client_seq": 1}).status_code == 200
    exit_now(app_client, h, a, exam)
    assert app_client.put(aurl(a, "answers"), headers=hs, json={"answers": {}, "client_seq": 2}).status_code == 409
    assert app_client.post(aurl(a, "heartbeat"), headers=hs, json={}).status_code == 409
    assert app_client.post(aurl(a, "submit"), headers=hs, json={"answers": {}, "client_seq": 3}).status_code == 409
    assert app_client.get(f"/api/student/attempts/{a['attempt_id']}", headers=hs).status_code == 409
    from app.app_client import beat_message, sign
    from .test_app_session import KEY
    sig = sign(KEY, beat_message(a["attempt_id"], a["session"], DEV_A, 5, True, "vm=0"))
    assert app_client.post(aurl(a, "app-beat"), headers=hs, json={
        "session": a["session"], "device_id": DEV_A, "ts": 5, "locked": True, "checks": "vm=0",
        "signature": sig}).status_code == 409


def test_a_stale_cache_entry_cannot_keep_accepting_the_exited_attempt(app_client, exam):
    from app.services import attempt_key
    from app.store import get_store
    import asyncio
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    exit_now(app_client, h, a, exam)
    assert '"exited": true' in asyncio.run(get_store().get(attempt_key(a["attempt_id"])))
    r = app_client.post(aurl(a, "heartbeat"), headers=h, json={})   # no session header at all
    assert r.status_code == 409


def test_resume_after_exit_needs_the_start_code(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    qid = a["questions"][0]["id"]
    hs = {**h, "X-FairPlay-Session": a["session"]}
    assert app_client.put(aurl(a, "answers"), headers=hs, json={"answers": {qid: 1}, "client_seq": 1}).status_code == 200
    exit_now(app_client, h, a, exam)
    forget_double_click(a["attempt_id"])

    r = restart(app_client, h, exam, "")
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "start_code_required",
                                  "message": "Ask the invigilator for the start code to continue the exam."}
    wrong = "000000" if exam["start_code"] != "000000" else "000001"
    r = restart(app_client, h, exam, wrong)
    assert r.status_code == 403 and r.json()["detail"] == "Wrong start code"

    r = restart(app_client, h, exam, exam["start_code"])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["attempt_id"] == a["attempt_id"] and b["session"] != a["session"] and b["answers"] == {qid: 1}
    assert b["needs_approval"] is False
    nb = {**h, "X-FairPlay-Session": b["session"]}
    assert app_client.put(aurl(b, "answers"), headers=nb, json={"answers": {qid: 1}, "client_seq": 2}).status_code == 200
    assert app_client.put(aurl(a, "answers"), headers=hs, json={"answers": {}, "client_seq": 3}).status_code == 409

    # not exited any more: a crash restart needs no code (the normal start code still applies to non-access students)
    forget_double_click(a["attempt_id"])
    assert restart(app_client, h, exam, exam["start_code"]).status_code == 200

    # the next exit asks again
    c = restart(app_client, h, exam, exam["start_code"]).json()
    exit_now(app_client, h, c, exam)
    forget_double_click(a["attempt_id"])
    assert restart(app_client, h, exam, "").json()["detail"]["code"] == "start_code_required"


def test_wrong_start_codes_after_exit_are_rate_limited(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    exit_now(app_client, h, a, exam)
    wrong = "000000" if exam["start_code"] != "000000" else "000001"
    codes = [restart(app_client, h, exam, wrong).status_code for _ in range(8)]
    assert codes[0] == 403 and 429 in codes
    assert restart(app_client, h, exam, exam["start_code"]).status_code == 429


def test_release_device_clears_the_exit(app_client, exam):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    exit_now(app_client, h, a, exam)
    forget_double_click(a["attempt_id"])
    r = app_client.post(f"/api/exams/{exam['id']}/attempts/{a['attempt_id']}/release-device", headers=t)
    assert r.status_code == 200, r.text
    b = restart(app_client, h, exam, exam["start_code"], DEV_A)
    assert b.status_code == 200 and b.json()["session"] != a["session"]
    assert app_client.put(aurl(b.json(), "answers"), headers={**h, "X-FairPlay-Session": b.json()["session"]},
                          json={"answers": {}, "client_seq": 1}).status_code == 200



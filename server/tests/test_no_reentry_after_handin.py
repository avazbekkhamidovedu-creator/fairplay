"""Once a student has handed in (submitted or auto-submitted) there is no way back in for the
student: no start, no questions, no answer change, no second hand-in. Only the professor's
reopen (DKU OJ POST .../students/{netid}/reopen) puts the attempt back, once, with a new deadline."""
from datetime import timedelta

import pytest
from sqlalchemy import func, inspect as sa_inspect, select

from app import app_client as ac
from app.integrations.dkuoj.models import ExamAccessCode
from app.models import Attempt, IntegrityEvent
from app.timeutil import utcnow

from .conftest import login
from .test_access_code_login import DEV_A as OJ_DEV_A, DEV_B as OJ_DEV_B, bearer, confirm, enter, patch_exam, sign_in
from .test_access_code_login import APP_KEY
from .test_app_session import DEV_A, DEV_B, KEY, app_client, beat, exam, proof, start  # noqa: F401  (fixtures)
from .test_dkuoj_bridge import db, run
from .test_dkuoj_bridge_v2 import call, oj  # noqa: F401  (fixture)
from .test_dkuoj_rescue import (STU, attempts_of, fake, hand_in, put, qid_of, revisions, room, started,  # noqa: F401
                                submit)
from .test_s5_sealed_submit import seal


def snapshot(c, aid):
    async def q(s):
        a = await s.get(Attempt, aid)
        return {col.key: getattr(a, col.key) for col in sa_inspect(Attempt).mapper.column_attrs}
    return db(c, q)


def oj_start(c, body, eid, device, start_code="", student_id=None):
    """A start with a valid signed proof whose nonce is planted directly: GET challenge is
    refused for a student who handed in, so this proves start itself refuses."""
    import secrets
    from app.store import get_store
    nonce = secrets.token_urlsafe(24)
    uid = student_id if student_id is not None else attempts_of(c)[0].student_id
    c.portal.call(get_store().set, f"chal:{nonce}", f"{uid}:{eid}", 300)
    client = {"device_id": device, "nonce": nonce, "signature": ac.sign(APP_KEY, ac.start_message(nonce, device))}
    return c.post(f"/api/student/exams/{eid}/start", json={"start_code": start_code, "client": client},
                  headers=bearer(body))


def surl(a, tail=""):
    return f"/api/student/attempts/{a['attempt_id']}" + (f"/{tail}" if tail else "")


PROOFS: list = []


def start_with(c, h, exam, device, code):
    """A start with a signed proof minted before the hand-in (challenge is refused afterwards)."""
    p = PROOFS.pop()
    return c.post(f"/api/student/exams/{exam['id']}/start", headers=h,
                  json={"start_code": code, "client": {**p, "device_id": device,
                                                       "signature": ac.sign(KEY, ac.start_message(p["nonce"], device))}})


@pytest.fixture
def handed_in(app_client, exam):
    h = login(app_client, "alice")
    a = start(app_client, h, exam).json()
    PROOFS[:] = [proof(app_client, h, exam["id"], DEV_A) for _ in range(12)]
    qid = a["questions"][0]["id"]
    hs = {**h, "X-FairPlay-Session": a["session"]}
    assert app_client.put(surl(a, "answers"), headers=hs, json={"answers": {qid: 1}, "client_seq": 1}).status_code == 200
    r = app_client.post(surl(a, "submit"), headers=hs, json={"answers": {qid: 1}, "client_seq": 2})
    assert r.status_code == 200, r.text
    return h, hs, a, qid, r.json()


def test_start_and_challenge_are_refused_after_hand_in(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    before = snapshot(app_client, a["attempt_id"])
    t = login(app_client, "teacher")
    eid = exam["id"]
    codes = [exam["start_code"], "", "000000"]
    for device in (DEV_A, DEV_B):
        for code in codes:
            r = start_with(app_client, h, exam, device, code)
            assert (r.status_code, r.json()["detail"]) == (409, "Already submitted"), (device, code)
    # a start code the professor changed since the hand-in
    assert app_client.post(f"/api/exams/{eid}/rotate-code", headers=t).status_code == 200
    new_code = app_client.get(f"/api/exams/{eid}", headers=t).json()["start_code"]
    assert new_code != exam["start_code"]
    for device in (DEV_A, DEV_B):
        r = start_with(app_client, h, exam, device, new_code)
        assert (r.status_code, r.json()["detail"]) == (409, "Already submitted")
    # without a proof (a browser / replayed start) it is the same clear answer
    r = app_client.post(f"/api/student/exams/{eid}/start", headers=h, json={"start_code": new_code})
    assert (r.status_code, r.json()["detail"]) == (409, "Already submitted")
    r = app_client.get(f"/api/student/exams/{eid}/challenge", headers=h)
    assert (r.status_code, r.json()["detail"]) == (409, "Already submitted")
    assert snapshot(app_client, a["attempt_id"]) == before


def test_start_is_refused_even_when_the_attempt_was_marked_exited(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    from .s_helpers import db_update
    db_update(app_client, a["attempt_id"], exited_at=utcnow())
    before = snapshot(app_client, a["attempt_id"])
    for code in ("", exam["start_code"]):
        r = start_with(app_client, h, exam, DEV_A, code)
        assert (r.status_code, r.json()["detail"]) == (409, "Already submitted")
    assert snapshot(app_client, a["attempt_id"]) == before


def test_attempt_reads_writes_and_resubmit_change_nothing(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    before = snapshot(app_client, a["attempt_id"])
    assert before["status"] == "submitted" and before["receipt"] == done["receipt"]

    r = app_client.get(surl(a), headers=hs)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "submitted" and body["questions"] == [] and body["answers"] == {}

    r = app_client.put(surl(a, "answers"), headers=hs, json={"answers": {qid: 2}, "client_seq": 50})
    assert r.status_code == 409 and r.json()["detail"] == "Attempt already submitted"

    again = app_client.post(surl(a, "submit"), headers=hs, json={"answers": {qid: 2}, "client_seq": 51})
    assert again.status_code == 200 and again.json() == done
    assert snapshot(app_client, a["attempt_id"]) == before
    assert before["answers"] == {qid: 1}


def test_package_and_package_key_are_refused_after_hand_in(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    for tail in ("package", "package-key"):
        r = app_client.get(f"/api/student/exams/{exam['id']}/{tail}", headers=h)
        assert (r.status_code, r.json()["detail"]) == (409, "You handed this exam in."), tail
    bob = login(app_client, "bob")        # someone who has not handed in still gets it
    assert app_client.get(f"/api/student/exams/{exam['id']}/package-key", headers=bob).status_code == 200


def test_session_calls_cannot_undo_a_hand_in(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    aid = a["attempt_id"]
    before = snapshot(app_client, aid)

    hb = app_client.post(surl(a, "heartbeat"), headers=hs, json={})
    assert hb.status_code == 200 and hb.json()["status"] == "submitted"
    ab = beat(app_client, hs, aid, a["session"], 1000)
    assert ab.status_code == 200 and ab.json()["status"] == "submitted"
    ev = app_client.post(surl(a, "events"), headers=hs, json={"events": [{"type": "blur", "detail": "x"}]})
    assert ev.status_code == 200

    inv = exam["invigilator_code"]
    r = app_client.post(surl(a, "exit-code"), headers=hs, json={"code": inv})
    assert (r.status_code, r.json()["detail"]) == (409, "Attempt already submitted")
    r = app_client.post(surl(a, "swap-request"), headers=h, json={"device_id": DEV_B})
    assert (r.status_code, r.json()["detail"]) == (409, "Attempt already submitted")
    ts = 1234
    sig = ac.sign(KEY, ac.offline_key_message(aid, a["session"], DEV_A, ts))
    r = app_client.post(surl(a, "offline-key"), headers=hs, json={
        "session": a["session"], "device_id": DEV_A, "ts": ts, "signature": sig})
    assert (r.status_code, r.json()["detail"]) == (409, "Attempt already submitted")
    assert snapshot(app_client, aid) == before

    t = login(app_client, "teacher")
    assert app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/release-device", headers=t).status_code == 409
    assert app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-swap", headers=t).status_code == 409
    assert app_client.post(f"/api/exams/{exam['id']}/attempts/{aid}/approve-resume", headers=t).status_code == 409
    assert snapshot(app_client, aid) == before
    handin = app_client.get(surl(a, "handin"), headers=hs, params={"nonce": "n" * 16})
    assert handin.json()["status"] == "submitted"


def test_a_sealed_hand_in_after_the_hand_in_is_kept_aside_never_applied(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    aid = a["attempt_id"]
    before = snapshot(app_client, aid)
    body = seal(aid, a["session"], {qid: 2})
    r = app_client.post(surl(a, "sealed-submit"), headers=h, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_submitted"
    after = snapshot(app_client, aid)
    assert after["late_answers"] == {qid: 2}
    assert {k: v for k, v in after.items() if k != "late_answers"} == {
        k: v for k, v in before.items() if k != "late_answers"}
    assert after["answers"] == {qid: 1} and after["receipt"] == done["receipt"]


def test_a_student_cannot_reopen_or_add_time(app_client, exam, handed_in):
    h, hs, a, qid, done = handed_in
    aid = a["attempt_id"]
    before = snapshot(app_client, aid)
    eid = exam["id"]
    for path, body in ((f"/api/exams/{eid}/extend", {"minutes": 30}),
                       (f"/api/exams/{eid}/rotate-code", None),
                       (f"/api/exams/{eid}/attempts/{aid}/release-device", None),
                       (f"/api/exams/{eid}/attempts/{aid}/approve-swap", None),
                       (f"/api/exams/{eid}/attempts/{aid}/approve-resume", None)):
        r = app_client.post(path, headers=h, json=body) if body else app_client.post(path, headers=h)
        assert r.status_code in (401, 403), path
    assert snapshot(app_client, aid) == before


# ---------------------------------------------------------------- access codes (DKU OJ exam)
def attempt_of(c):
    return attempts_of(c)[0]


def test_access_code_student_cannot_get_back_in_after_hand_in(oj, room, fake):
    eid, codes, start_code, _ = room
    body, a, receipt = hand_in(oj, eid, codes, "carol", OJ_DEV_A, "first answer")
    aid, q5 = a["attempt_id"], qid_of(a)
    run(oj)
    sent = revisions(oj)
    assert [r[:2] for r in sent] == [(1, "sent")]
    before = snapshot(oj, aid)

    step = enter(oj, codes["carol"], OJ_DEV_A)
    assert step.status_code == 200 and step.json()["resume"] is False
    again = confirm(oj, step.json()["confirm_token"]).json()
    assert again["resume"] is False
    for tok in (body, again):
        for code in ("", start_code, "000000"):
            r = oj_start(oj, tok, eid, OJ_DEV_A, start_code=code)
            assert (r.status_code, r.json()["detail"]) == (409, "Already submitted"), code
        assert oj.get(f"/api/student/exams/{eid}/challenge", headers=bearer(tok)).status_code == 409
        r = oj.get(surl(a), headers={**bearer(tok), "X-FairPlay-Session": a["session"]})
        assert r.status_code == 200 and r.json()["status"] == "submitted"
        hs = {**bearer(tok), "X-FairPlay-Session": a["session"]}
        assert oj.put(surl(a, "answers"), headers=hs, json={"answers": {q5: "changed"}, "client_seq": 99}).status_code == 409
        r = oj.post(surl(a, "submit"), headers=hs, json={"answers": {q5: "changed"}, "client_seq": 100})
        assert r.status_code == 200 and r.json()["receipt"] == receipt
    assert snapshot(oj, aid) == before
    assert revisions(oj) == sent


def test_reissue_gives_only_a_new_code_and_release_device_is_refused(oj, room, fake):
    eid, codes, start_code, _ = room
    body, a, receipt = hand_in(oj, eid, codes, "carol", OJ_DEV_A, "first answer")
    aid = a["attempt_id"]
    run(oj)
    before = snapshot(oj, aid)

    r = call(oj, "POST", f"{STU}/carol/release-device", {"reason": "r"})
    assert r.status_code == 409
    assert snapshot(oj, aid) == before

    r = call(oj, "POST", "/api/integrations/dkuoj/v1/exams/" + oj_key() + "/access-codes/reissue",
             {"netid": "carol", "reason": "lost it"})
    assert r.status_code == 200, r.text
    new_code = r.json()["code"]
    assert new_code != codes["carol"]
    assert snapshot(oj, aid) == before          # the attempt was not freed, moved or reset

    tok = sign_in(oj, new_code, OJ_DEV_A)
    assert tok["resume"] is False
    for code in ("", start_code):
        r = oj_start(oj, tok, eid, OJ_DEV_A, start_code=code)
        assert (r.status_code, r.json()["detail"]) == (409, "Already submitted")
    assert enter(oj, new_code, OJ_DEV_B).status_code == 400      # bound to the first computer now
    assert snapshot(oj, aid) == before
    assert call(oj, "POST", f"{STU}/carol/extra-time", {"minutes": 10, "reason": "r"}).status_code == 409
    assert snapshot(oj, aid) == before


def test_a_student_token_cannot_call_the_professor_actions(oj, room, fake):
    eid, codes, _, _ = room
    body, a, receipt = hand_in(oj, eid, codes, "carol", OJ_DEV_A, "first answer")
    before = snapshot(oj, a["attempt_id"])
    for action, data in (("reopen", {"minutes": 10, "reason": "r"}), ("extra-time", {"minutes": 10, "reason": "r"}),
                         ("release-device", {"reason": "r"})):
        r = oj.post(f"{STU}/carol/{action}", headers=bearer(body), json=data)
        assert r.status_code in (401, 403, 404), action
    r = oj.post("/api/integrations/dkuoj/v1/exams/" + oj_key() + "/access-codes/reissue",
                headers=bearer(body), json={"netid": "carol", "reason": "r"})
    assert r.status_code in (401, 403, 404)
    assert snapshot(oj, a["attempt_id"]) == before


def test_sealed_only_token_after_a_server_close_cannot_start_or_apply_a_seal(oj, room, fake):
    eid, codes, _, teacher = room
    body = sign_in(oj, codes["alice"], OJ_DEV_A)
    a = started(oj, body, eid, OJ_DEV_A)
    q2 = next(q["id"] for q in a["questions"] if q["label"] == "Q2")
    assert oj.post(f"/api/exams/{eid}/close", headers=teacher).status_code == 200
    patch_exam(oj, closed_at=utcnow() - timedelta(hours=3))
    row = snapshot(oj, a["attempt_id"])
    assert row["status"] == "submitted" and row["auto_submitted"] is True

    step = enter(oj, codes["alice"], OJ_DEV_A)
    assert step.status_code == 200 and step.json()["resume"] is False
    sealed_tok = confirm(oj, step.json()["confirm_token"]).json()
    assert sealed_tok["resume"] is False
    r = oj_start(oj, sealed_tok, eid, OJ_DEV_A)
    assert r.status_code in (403, 409)
    assert oj.get(surl(a), headers=bearer(sealed_tok)).status_code in (403, 409)
    hs = {**bearer(sealed_tok), "X-FairPlay-Session": a["session"]}
    assert oj.put(surl(a, "answers"), headers=hs, json={"answers": {q2: [1]}, "client_seq": 9}).status_code in (403, 409)

    before = snapshot(oj, a["attempt_id"])
    s = seal(a["attempt_id"], a["session"], {q2: [1]}, device=OJ_DEV_A, secret=oj.app.state.settings.secret)
    r = oj.post(surl(a, "sealed-submit"), headers=bearer(sealed_tok), json=s)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_submitted"
    after = snapshot(oj, a["attempt_id"])
    assert after["late_answers"] == {q2: [1]} and after["answers"] == before["answers"]
    assert after["status"] == "submitted" and after["receipt"] == before["receipt"]


def test_teacher_reopen_lets_the_student_in_once_and_then_it_is_locked_again(oj, room, fake):
    eid, codes, start_code, _ = room
    body, a, receipt1 = hand_in(oj, eid, codes, "carol", OJ_DEV_A, "first answer")
    aid, q5 = a["attempt_id"], qid_of(a)
    run(oj)

    r = call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "network died"})
    assert r.status_code == 200, r.text
    new_deadline = r.json()["deadline_at"]
    mine = snapshot(oj, aid)
    assert mine["status"] == "in_progress" and mine["receipt"] is None and mine["answers"] == {q5: "first answer"}

    step = enter(oj, codes["carol"], OJ_DEV_B)
    assert step.json()["resume"] is True
    body2 = confirm(oj, step.json()["confirm_token"]).json()
    b = started(oj, body2, eid, OJ_DEV_B)
    assert b["attempt_id"] == aid and b["deadline_at"] == new_deadline and b["answers"] == {q5: "first answer"}
    put(oj, body2, b, {q5: "second answer"}, 10)
    receipt2 = submit(oj, body2, b, {q5: "second answer"}, 11)
    assert receipt2 != receipt1

    locked = snapshot(oj, aid)
    assert locked["status"] == "submitted" and locked["answers"] == {q5: "second answer"}
    hs = {**bearer(body2), "X-FairPlay-Session": b["session"]}
    for tok in (body, body2):
        r = oj_start(oj, tok, eid, OJ_DEV_B, start_code=start_code)
        assert (r.status_code, r.json()["detail"]) == (409, "Already submitted")
    assert oj.put(surl(b, "answers"), headers=hs, json={"answers": {q5: "third"}, "client_seq": 40}).status_code == 409
    r = oj.post(surl(b, "submit"), headers=hs, json={"answers": {q5: "third"}, "client_seq": 41})
    assert r.status_code == 200 and r.json()["receipt"] == receipt2
    assert oj.get(surl(b), headers=hs).json()["status"] == "submitted"
    assert snapshot(oj, aid) == locked
    assert call(oj, "POST", f"{STU}/carol/reopen", {"minutes": 10, "reason": "again"}).status_code == 409
    run(oj)
    assert [r[:2] for r in revisions(oj)] == [(1, "sent"), (2, "sent")]


def oj_key():
    from .test_access_codes import KEY as EXAM_KEY
    return EXAM_KEY

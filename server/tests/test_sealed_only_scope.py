"""A student who was offline past the close can still upload the sealed hand-in, with a
token that can do nothing else (scope sealed-<exam id>)."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app import security
from app.models import Attempt, Exam, IntegrityEvent
from app.timeutil import utcnow

from .conftest import login
from .s_helpers import db_update
from .test_access_code_login import DEV_A, bearer, confirm, enter, patch_exam, ready, sign_in, start  # noqa: F401
from .test_access_codes import db
from .test_dkuoj_bridge import oj_exam
from .test_dkuoj_bridge_v2 import fake, oj  # noqa: F401  (fixtures)
from .test_s5_sealed_submit import seal

NOT_VALID = {"detail": "That code is not valid."}


def running(oj, ready, who="alice"):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready[who], DEV_A)
    a = start(oj, body, eid, DEV_A).json()
    return teacher, eid, a


def sealed_body(oj, a, answers=None, sealed_at=None):
    qs = {q["label"]: q["id"] for q in a["questions"]}
    return seal(a["attempt_id"], a["session"], answers or {qs["Q2"]: [1]}, device=DEV_A,
                secret=oj.app.state.settings.secret, sealed_at=sealed_at)


def closed_long_ago(oj):
    patch_exam(oj, status="closed", closed_at=utcnow() - timedelta(hours=3))


def stranded_token(oj, ready, who="alice"):
    return sign_in(oj, ready[who], DEV_A)


def event_rows(oj, aid, typ):
    async def q(s):
        return [e.detail for e in (await s.execute(select(IntegrityEvent).where(
            IntegrityEvent.attempt_id == aid, IntegrityEvent.type == typ))).scalars()]
    return db(oj, q)


def test_a_running_attempt_on_a_closed_exam_gets_a_sealed_only_token(oj, ready):
    _, eid, a = running(oj, ready)
    closed_long_ago(oj)
    body = stranded_token(oj, ready)
    assert body["resume"] is False
    assert security.parse_scoped_token(oj.app.state.settings.secret, body["token"])[3] == security.SEALED_SCOPE + eid
    sealed = sealed_body(oj, a)
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(body), json=sealed)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    late = event_rows(oj, a["attempt_id"], "sealed_received_late")
    assert len(late) == 1 and "received " in late[0] and "sealed at " in late[0] and "min after" in late[0]
    again = oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(body), json=sealed)
    assert again.status_code == 200 and again.json() == r.json()
    assert len(event_rows(oj, a["attempt_id"], "sealed_received_late")) == 1


def test_a_seal_inside_the_window_has_no_late_event(oj, ready):
    _, eid, a = running(oj, ready)
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(
        sign_in(oj, ready["alice"], DEV_A)), json=sealed_body(oj, a))
    assert r.status_code == 200
    assert event_rows(oj, a["attempt_id"], "sealed_received_late") == []


def test_a_server_closed_attempt_still_gets_the_token_and_the_seal_stays_aside(oj, ready):
    teacher, eid, a = running(oj, ready)
    qs = {q["label"]: q["id"] for q in a["questions"]}
    db_update(oj, a["attempt_id"], answers={qs["Q2"]: [0]})
    assert oj.post(f"/api/exams/{eid}/close", headers=teacher).status_code == 200
    patch_exam(oj, closed_at=utcnow() - timedelta(hours=3))
    body = stranded_token(oj, ready)
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(body),
                json=sealed_body(oj, a, {qs["Q2"]: [1]}))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_submitted"

    async def q(s):
        row = await s.get(Attempt, a["attempt_id"])
        return row.answers, row.late_answers
    stored, kept = db(oj, q)
    assert stored == {qs["Q2"]: [0]} and kept == {qs["Q2"]: [1]}
    assert len(event_rows(oj, a["attempt_id"], "sealed_received_late")) == 1


def test_a_seal_dated_after_the_deadline_changes_no_answer(oj, ready):
    _, eid, a = running(oj, ready)
    qs = {q["label"]: q["id"] for q in a["questions"]}
    db_update(oj, a["attempt_id"], answers={qs["Q2"]: [0]}, deadline_at=utcnow() - timedelta(minutes=30))
    closed_long_ago(oj)
    body = stranded_token(oj, ready)
    r = oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(body),
                json=sealed_body(oj, a, {qs["Q2"]: [1]}))
    assert r.status_code == 200 and r.json()["late_sealed"] is True

    async def q(s):
        row = await s.get(Attempt, a["attempt_id"])
        return row.answers, row.late_answers
    stored, kept = db(oj, q)
    assert stored == {qs["Q2"]: [0]} and kept == {qs["Q2"]: [1]}


def test_a_forged_seal_is_refused_with_the_sealed_token(oj, ready):
    _, eid, a = running(oj, ready)
    closed_long_ago(oj)
    body = stranded_token(oj, ready)
    bad = {**sealed_body(oj, a), "proof": "0" * 64}
    assert oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(body),
                   json=bad).status_code == 403
    wrong_key = seal(a["attempt_id"], a["session"], {}, device=DEV_A, secret="another-secret-0123456789")
    assert oj.post(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=bearer(body),
                   json=wrong_key).status_code == 403


def test_the_token_is_for_that_exam_and_that_student_only(oj, ready):
    _, eid, a = running(oj, ready)
    closed_long_ago(oj)
    alice = stranded_token(oj, ready)
    secret = oj.app.state.settings.secret
    uid = db(oj, lambda s: s.scalar(select(Attempt.student_id).where(Attempt.id == a["attempt_id"])))
    other_exam = security.make_token(secret, uid, "student", 600, scope=security.SEALED_SCOPE + "e_other")
    url = f"/api/student/attempts/{a['attempt_id']}/sealed-submit"
    r = oj.post(url, headers={"Authorization": f"Bearer {other_exam}"}, json=sealed_body(oj, a))
    assert r.status_code == 403
    bob = oj.post(url, headers=login(oj, "bob"), json=sealed_body(oj, a))
    assert bob.status_code == 404
    assert oj.post(url, headers=bearer(alice), json=sealed_body(oj, a)).status_code == 200


def test_the_token_is_short_lived(oj, ready):
    _, eid, a = running(oj, ready)
    closed_long_ago(oj)
    body = stranded_token(oj, ready)
    secret = oj.app.state.settings.secret
    parsed = security.parse_scoped_token(secret, body["token"])
    assert parsed is not None
    import base64
    import time
    exp = int(base64.urlsafe_b64decode(body["token"].split(".")[0] + "==").decode().split(":")[2])
    assert exp - time.time() <= security.SEALED_TTL_S + 5


@pytest.mark.parametrize("case", ["no_attempt", "own_hand_in", "wrong_code", "other_student_code", "revoked"])
def test_every_other_case_keeps_the_generic_400(oj, ready, case):
    if case == "no_attempt":
        closed_long_ago(oj)
        code = ready["alice"]
    elif case == "own_hand_in":
        teacher, eid, a = running(oj, ready)
        r = oj.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 1},
                    headers=bearer(sign_in(oj, ready["alice"], DEV_A)))
        assert r.status_code == 200
        closed_long_ago(oj)
        code = ready["alice"]
    elif case == "wrong_code":
        running(oj, ready)
        closed_long_ago(oj)
        code = "AAAAAA" if ready["alice"] != "AAAAAA" else "BBBBBB"
    elif case == "other_student_code":
        running(oj, ready)
        closed_long_ago(oj)
        code = ready["carol"]
    else:
        running(oj, ready)
        closed_long_ago(oj)

        async def revoke(s):
            from app.integrations.dkuoj.models import ExamAccessCode
            await s.execute(update(ExamAccessCode).where(ExamAccessCode.netid == "alice").values(revoked_at=utcnow()))
            await s.commit()
        db(oj, revoke)
        code = ready["alice"]
    r = enter(oj, code, DEV_A)
    assert (r.status_code, r.json()) == (400, NOT_VALID)


def test_a_second_device_cannot_get_the_sealed_token(oj, ready):
    from .test_access_code_login import DEV_B
    running(oj, ready)
    closed_long_ago(oj)
    r = enter(oj, ready["alice"], DEV_B)
    assert (r.status_code, r.json()) == (400, NOT_VALID)


def test_a_code_from_a_draft_or_paper_state_is_unchanged(oj, ready):
    r = enter(oj, ready["alice"], DEV_A)
    assert r.status_code == 200 and r.json()["resume"] is False


def test_the_sealed_token_is_refused_on_every_student_and_homework_route(oj, ready):
    _, eid, a = running(oj, ready)
    closed_long_ago(oj)
    h = bearer(stranded_token(oj, ready))
    seen = []
    for route_path, ops in oj.app.openapi()["paths"].items():
        if not route_path.startswith(("/api/student/", "/api/homework")):
            continue
        path = re.sub(r"\{aid\}", a["attempt_id"], route_path)
        path = re.sub(r"\{[^}]+\}", eid, path)
        for method in sorted(m.upper() for m in ops if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")):
            if method == "POST" and route_path == "/api/student/attempts/{aid}/sealed-submit":
                continue
            anon = oj.request(method, path)
            if anon.status_code != 401:
                continue        # a public route (challenge, code-login, ...): no token involved
            r = oj.request(method, path, headers=h, json={} if method in ("POST", "PUT", "PATCH") else None)
            assert r.status_code == 403, (method, route_path, r.status_code)
            seen.append((method, route_path))
    assert len(seen) >= 20
    assert ("POST", "/api/student/attempts/{aid}/submit") in seen
    assert ("PUT", "/api/student/attempts/{aid}/answers") in seen or any("answers" in p for _, p in seen)
    assert ("GET", "/api/student/attempts/{aid}") in seen
    for method, path in [("GET", "/api/me"), ("GET", "/api/exams"), ("POST", "/api/auth/logout")]:
        assert oj.request(method, path, headers=h).status_code == 403, path


def test_the_sealed_route_refuses_other_methods_with_the_token(oj, ready):
    _, eid, a = running(oj, ready)
    closed_long_ago(oj)
    h = bearer(stranded_token(oj, ready))
    assert oj.put(f"/api/student/attempts/{a['attempt_id']}/sealed-submit", headers=h, json={}).status_code in (403, 404, 405)
    assert oj.get(f"/api/student/attempts/{a['attempt_id']}", headers=h).status_code == 403
    assert oj.post(f"/api/student/exams/{eid}/start", headers=h, json={"start_code": ""}).status_code == 403

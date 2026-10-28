from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app import security
from app.integrations.dkuoj import access_codes
from app.integrations.dkuoj.models import ExamAccessCode
from app.integrations.dkuoj.models import OjRosterEntry
from app.models import Exam, User
from app.timeutil import utcnow

from .conftest import login
from .test_access_code_login import DEV_A, bearer, patch_exam, ready, sign_in, start  # noqa: F401
from .test_access_codes import KEY, db, secret
from .test_dkuoj_bridge import oj_exam
from .test_dkuoj_bridge_v2 import fake, oj  # noqa: F401  (fixtures)


def second_exam(c, netid="alice", code="ZZZZZZ"):
    async def go(s):
        e = (await s.execute(select(Exam).where(Exam.external_key == KEY))).scalar_one()
        s.add(Exam(id="e_other", teacher_id=e.teacher_id, title="Other", duration_min=10, status="live",
                   questions=[], question_count=0, total_points=0, source=e.source))
        await s.flush()
        uid = (await s.execute(select(User.id).where(User.netid == netid))).scalar_one()
        s.add(OjRosterEntry(exam_id="e_other", user_id=uid))
        s.add(ExamAccessCode(exam_id="e_other", netid=netid, first_name="A", last_name="C",
                             code_hmac=access_codes.code_hmac(secret(c), code), code_sealed="x",
                             created_at=utcnow()))
        await s.commit()
    db(c, go)


def at(monkeypatch, delta):
    monkeypatch.setattr(security, "_now", lambda: utcnow() + delta)


def submit(c, body, aid):
    r = c.post(f"/api/student/attempts/{aid}/submit", json={"answers": {}, "client_seq": 1}, headers=bearer(body))
    assert r.status_code == 200, r.text


def test_the_token_names_its_exam_and_only_that_exam_is_listed(oj, ready):
    body = sign_in(oj, ready["alice"], DEV_A)
    eid = db(oj, lambda s: s.scalar(select(Exam.id).where(Exam.external_key == KEY)))
    assert security.parse_scoped_token(oj.app.state.settings.secret, body["token"])[3] == eid
    second_exam(oj)
    listed = [x["id"] for x in oj.get("/api/student/exams", headers=bearer(body)).json()]
    assert "e_other" not in listed
    normal = login(oj, "alice")
    assert security.parse_scoped_token(oj.app.state.settings.secret, normal["Authorization"].split()[1])[3] == ""
    assert oj.get("/api/student/exams/e_other/challenge", headers=normal).status_code == 200


def test_a_scoped_token_cannot_reach_another_exam(oj, ready):
    body = sign_in(oj, ready["alice"], DEV_A)
    second_exam(oj)
    h = bearer(body)
    assert oj.get("/api/student/exams/e_other/challenge", headers=h).status_code == 403
    assert oj.get("/api/student/exams/e_other/package", headers=h).status_code == 403
    assert oj.post("/api/student/exams/e_other/start", json={"start_code": "123456"}, headers=h).status_code == 403
    assert oj.get("/api/student/trial", headers=h).status_code == 403
    assert oj.get("/api/student/classes", headers=h).status_code == 403
    eid = db(oj, lambda s: s.scalar(select(Exam.id).where(Exam.external_key == KEY)))
    assert oj.get(f"/api/student/exams/{eid}/challenge", headers=h).status_code != 403


def test_a_new_code_gives_a_token_for_the_second_exam(oj, ready):
    second_exam(oj)
    body = sign_in(oj, "ZZZZZZ", DEV_A)
    assert security.parse_scoped_token(oj.app.state.settings.secret, body["token"])[3] == "e_other"
    assert oj.get("/api/student/exams/e_other/challenge", headers=bearer(body)).status_code == 200


def test_the_token_stops_five_minutes_after_the_hand_in(oj, ready, monkeypatch):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready["alice"], DEV_A)
    aid = start(oj, body, eid, DEV_A).json()["attempt_id"]
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    submit(oj, body, aid)
    at(monkeypatch, timedelta(minutes=4))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    at(monkeypatch, timedelta(minutes=5, seconds=5))
    r = oj.get("/api/student/exams", headers=bearer(body))
    assert r.status_code == 401
    assert oj.get("/api/me", headers=bearer(body)).status_code == 401
    kept = db(oj, lambda s: s.scalar(select(Exam.id)))
    assert kept and oj.get("/api/student/exams", headers=login(oj, "alice")).status_code == 200


def test_the_expiry_counts_from_the_real_submit_time(oj, ready, monkeypatch):
    teacher = login(oj, "teacher")
    eid = oj_exam(oj, teacher)["id"]
    oj.post(f"/api/exams/{eid}/open", headers=teacher)
    body = sign_in(oj, ready["carol"], DEV_A)
    aid = start(oj, body, eid, DEV_A).json()["attempt_id"]
    submit(oj, body, aid)
    at(monkeypatch, timedelta(minutes=3))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    at(monkeypatch, timedelta(minutes=5, seconds=1))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 401


def test_a_student_who_never_starts_is_cut_off_30_minutes_after_the_exam_end(oj, ready, monkeypatch):
    body = sign_in(oj, ready["alice"], DEV_A)
    meta = db(oj, lambda s: s.scalar(select(Exam.external_meta).where(Exam.external_key == KEY)))
    patch_exam(oj, external_meta={**meta, "end_time": (utcnow() - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 200
    at(monkeypatch, timedelta(minutes=40))
    assert oj.get("/api/student/exams", headers=bearer(body)).status_code == 401


def test_grades_are_off_unless_the_setting_is_on(oj, ready):
    alice = login(oj, "alice")
    settings = oj.app.state.settings
    unknown = oj.get("/api/student/does-not-exist", headers=alice)
    settings.student_grades = False
    try:
        assert oj.get("/api/student/grades", headers=alice).status_code == 404
        r = oj.get("/api/student/grades/e_x", headers=alice)
        assert (r.status_code, r.json()) == (unknown.status_code, unknown.json()) == (404, {"detail": "Not Found"})
        assert oj.get("/api/health").json()["student_grades"] is False
    finally:
        settings.student_grades = True
    assert oj.get("/api/student/grades", headers=alice).status_code == 200
    assert oj.get("/api/health").json()["student_grades"] is True


def test_the_default_is_off(monkeypatch):
    monkeypatch.delenv("FAIRPLAY_STUDENT_GRADES", raising=False)
    from app.config import Settings
    assert Settings().student_grades is False
    monkeypatch.setenv("FAIRPLAY_STUDENT_GRADES", "1")
    assert Settings().student_grades is True

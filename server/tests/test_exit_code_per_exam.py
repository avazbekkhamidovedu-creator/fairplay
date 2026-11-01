"""One invigilator exit code per exam: random, never shared by two live exams, checked
against the attempt's own exam only."""
import itertools

import pytest

from app import services

from .conftest import SAMPLE_EXAM, login, make_class
from .test_app_session import DEV_A, app_client, start  # noqa: F401  (fixtures)


def feed(monkeypatch, *codes):
    it = itertools.chain(codes, itertools.repeat("999999"))
    monkeypatch.setattr(services, "new_invigilator_code", lambda: next(it))


def new_exam(c, t, class_id=None, open_it=True):
    body = {**SAMPLE_EXAM, **({"class_id": class_id} if class_id else {})}
    e = c.post("/api/exams", json=body, headers=t)
    assert e.status_code in (200, 201), e.text
    e = e.json()
    return c.post(f"/api/exams/{e['id']}/open", headers=t).json() if open_it else e


def exit_url(a):
    return f"/api/student/attempts/{a['attempt_id']}/exit-code"


def test_two_live_exams_never_share_a_code_in_one_class_or_across_classes(app_client, monkeypatch):
    t = login(app_client, "teacher")
    other = make_class(app_client, t, "Other class")
    feed(monkeypatch, "111111", "111111", "222222", "111111", "222222", "333333")
    a = new_exam(app_client, t)
    b = new_exam(app_client, t)
    c = new_exam(app_client, t, other["id"])
    assert (a["invigilator_code"], b["invigilator_code"], c["invigilator_code"]) == ("111111", "222222", "333333")


def test_a_reopened_exam_gets_a_new_code_and_a_closed_one_has_none(app_client, monkeypatch):
    t = login(app_client, "teacher")
    feed(monkeypatch, "111111", "111111", "222222")
    a = new_exam(app_client, t)
    assert a["invigilator_code"] == "111111"
    app_client.post(f"/api/exams/{a['id']}/close", headers=t)
    assert app_client.get(f"/api/exams/{a['id']}", headers=t).json()["invigilator_code"] is None
    b = new_exam(app_client, t)
    assert b["invigilator_code"] == "111111"           # a closed exam's code is free again
    app_client.post(f"/api/exams/{b['id']}/close", headers=t)
    again = app_client.post(f"/api/exams/{a['id']}/open", headers=t).json()
    assert again["invigilator_code"] == "222222"        # new code, not the one it had


def test_the_code_of_exam_a_is_refused_on_an_attempt_of_exam_b(app_client):
    t = login(app_client, "teacher")
    h = login(app_client, "alice")
    a = new_exam(app_client, t)
    b = new_exam(app_client, t, make_class(app_client, t, "Other class")["id"])
    assert a["invigilator_code"] != b["invigilator_code"]
    att = start(app_client, h, b).json()
    hs = {**h, "X-FairPlay-Session": att["session"]}
    r = app_client.post(exit_url(att), headers=hs, json={"code": a["invigilator_code"]})
    assert r.status_code == 403
    r = app_client.post(exit_url(att), headers=hs, json={"code": b["invigilator_code"]})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_five_wrong_tries_lock_that_attempt_only_and_a_right_code_rotates_the_session(app_client):
    t = login(app_client, "teacher")
    e = new_exam(app_client, t)
    ha, hb = login(app_client, "alice"), login(app_client, "bob")
    a = start(app_client, ha, e).json()
    b = start(app_client, hb, e).json()
    sa = {**ha, "X-FairPlay-Session": a["session"]}
    sb = {**hb, "X-FairPlay-Session": b["session"]}
    wrong = "000000" if e["invigilator_code"] != "000000" else "000001"
    for _ in range(5):
        assert app_client.post(exit_url(a), headers=sa, json={"code": wrong}).status_code in (403, 429)
    assert app_client.post(exit_url(a), headers=sa, json={"code": e["invigilator_code"]}).status_code == 429
    assert app_client.post(exit_url(b), headers=sb, json={"code": e["invigilator_code"]}).status_code == 200
    assert app_client.get(f"/api/student/attempts/{b['attempt_id']}", headers=sb).status_code == 409


def test_the_code_comes_from_a_csprng_not_from_ids(app_client, monkeypatch):
    seen = []
    real = services.secrets.randbelow
    monkeypatch.setattr(services.secrets, "randbelow", lambda n: seen.append(n) or real(n))
    code = services.new_invigilator_code()
    assert code.isdigit() and len(code) == 6 and seen == [10 ** 6]

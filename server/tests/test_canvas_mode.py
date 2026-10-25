import asyncio
import json
import logging
import threading
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app import canvas_mode, lms
from app.app_client import APP_PROOF_DETAIL, canvas_code_message, sign
from app.config import Settings
from app.main import create_app

from .conftest import login
from .test_app_session import DEV_A, DEV_B, KEY, app_client, proof  # noqa: F401  (fixtures)

CANVAS = "https://canvas.test"
TOKEN = "teacher-canvas-token-SECRET"
QUIZ = f"{CANVAS}/courses/12/quizzes/34"
NEW_QUIZ = f"{CANVAS}/courses/12/assignments/56"
MANUAL_CODE = "ManualCode-77"


class FakeCanvas:
    def __init__(self):
        self.codes: list[str] = []
        self.fail_with: int | None = None
        self.error_text = "no"
        self.calls: list[str] = []

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlsplit(str(req.url))
        self.calls.append(f"{req.method} {u.path}")
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
        if u.path == "/api/v1/users/self":
            return httpx.Response(200, json={"id": 1, "name": "Dr Teacher", "login_id": "teach"})
        if req.method == "PUT" and re.fullmatch(r"/api/v1/courses/12/quizzes/34", u.path):
            if self.fail_with:
                return httpx.Response(self.fail_with, json={"errors": [{"message": self.error_text}]})
            self.codes.append(parse_qs(req.content.decode(), keep_blank_values=True)["quiz[access_code]"][0])
            return httpx.Response(200, json={"id": 34})
        return httpx.Response(404, json={"errors": [{"message": "no route"}]})


@pytest.fixture(autouse=True)
def _hosts(monkeypatch):
    monkeypatch.setenv("FAIRPLAY_LMS_HOSTS", "canvas.test")


@pytest.fixture
def fake():
    f = FakeCanvas()

    async def no_sleep(_s):
        return None

    lms.set_transport_factory(lambda pid, url: httpx.MockTransport(f.handle))
    lms.set_sleep(no_sleep)
    yield f
    lms.set_transport_factory(None)
    lms.set_sleep(None)


def connect(c, t):
    r = c.post("/api/lms/connections", headers=t,
               json={"provider": "canvas", "base_url": CANVAS, "token": TOKEN})
    assert r.status_code in (200, 201), r.text


def body(url=QUIZ, canvas=None, **kw):
    b = {"title": "Canvas quiz", "duration_min": 40, "mode": "external", "external_url": url,
         "external_kind": "canvas", "questions": []}
    if canvas is not None:
        b["canvas"] = canvas
    return {**b, **kw}


def create(c, t, b):
    r = c.post("/api/exams", json=b, headers=t)
    assert r.status_code == 201, r.text
    return r.json()


def open_(c, t, e):
    return c.post(f"/api/exams/{e['id']}/open", headers=t)


def student_start(c, e, user="alice", device=DEV_A):
    h = login(c, user)
    r = c.post(f"/api/student/exams/{e['id']}/start", headers={**h, "X-FairPlay-Platform": "mac"},
               json={"start_code": e["start_code"], "client": proof(c, h, e["id"], device)})
    assert r.status_code == 200, r.text
    a = r.json()
    return h, a, {**h, "X-FairPlay-Session": a["session"]}


_last_ts = [0]


def fresh_ts():
    _last_ts[0] = max(_last_ts[0] + 1, int(time.time() * 1000))
    return _last_ts[0]


def code_req(c, hs, a, ts=None, device=DEV_A, key=KEY, session=None, send_proof=True):
    ts = ts if ts is not None else fresh_ts()
    sess = session or a["session"]
    payload = {"ts": ts}
    if send_proof:
        payload["proof"] = sign(key, canvas_code_message(a["attempt_id"], sess, device, ts))
    return c.post(f"/api/student/attempts/{a['attempt_id']}/canvas-code", json=payload, headers=hs)


def events(c, t, e):
    res = c.get(f"/api/exams/{e['id']}/results", headers=t).json()
    return [ev["type"] for a in res["attempts"] for ev in a.get("events", [])]


@pytest.fixture
def manual_live(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    e = open_(app_client, t, e).json()
    return t, e


# ---------------------------------------------------------------- settings and views
def test_create_fills_defaults_and_hides_code(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    assert e["external_kind"] == "canvas"
    assert e["canvas"] == {"quiz_type": "classic", "access_code_source": "manual",
                           "code_status": "set", "removed_hosts": [],
                           "remove_code_in_canvas": False}
    assert e["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]
    assert "canvas.test" in e["allowed_domains"]
    assert "login.microsoftonline.com" in e["allowed_domains"]
    assert not any("quiz-lti" in d for d in e["allowed_domains"])
    got = app_client.get(f"/api/exams/{e['id']}", headers=t)
    assert MANUAL_CODE not in got.text and MANUAL_CODE not in json.dumps(e)


def test_quiz_type_from_url_and_new_domains(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={"access_code_source": "manual"}))
    assert e["canvas"]["quiz_type"] == "new"
    assert e["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["new"]
    assert any(d.startswith("*.quiz-lti-iad-prod.") for d in e["allowed_domains"])
    a = create(app_client, t, body(url=NEW_QUIZ, canvas={"quiz_type": "assignment",
                                                         "access_code_source": "manual"}))
    assert a["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["assignment"]
    assert not any("quiz-lti" in d for d in a["allowed_domains"])


def test_unknown_link_needs_quiz_type(app_client):
    t = login(app_client, "teacher")
    b = body(url=f"{CANVAS}/somewhere", canvas={"access_code_source": "manual"})
    assert app_client.post("/api/exams", json=b, headers=t).status_code == 422
    b["canvas"]["quiz_type"] = "new"
    assert app_client.post("/api/exams", json=b, headers=t).status_code == 201


def test_http_link_refused(app_client):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", headers=t,
                        json=body(url="http://canvas.test/courses/1/quizzes/2"))
    assert r.status_code == 422


def test_teacher_extras_kept_and_policy_applied(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(allowed_domains=["docs.python.org"],
                                   canvas={"access_code_source": "manual"}))
    assert "docs.python.org" in e["allowed_domains"]
    r = app_client.post("/api/exams", headers=t, json=body(allowed_domains=["chatgpt.com"],
                                                           canvas={"access_code_source": "manual"}))
    assert r.status_code == 422 and "chatgpt.com" in r.text
    r = app_client.post("/api/exams", headers=t,
                        json=body(url="https://chatgpt.com/courses/1/quizzes/2"))
    assert r.status_code == 422


def test_sso_hosts_from_env(app_client, monkeypatch):
    monkeypatch.setenv("FAIRPLAY_CANVAS_SSO_HOSTS", "sso.example.edu")
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual"}))
    assert e["allowed_domains"] == ["canvas.test", "sso.example.edu"]


def test_round_trip_put_keeps_stored_code_and_resets_stale_domains(app_client, fake):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={"access_code_source": "manual",
                                                         "access_code": MANUAL_CODE}))
    again = {**body(url=NEW_QUIZ, canvas=e["canvas"]), "allowed_domains": e["allowed_domains"],
             "finish_url_regex": e["finish_url_regex"]}
    r = app_client.put(f"/api/exams/{e['id']}", json=again, headers=t)
    assert r.status_code == 200, r.text
    assert r.json()["canvas_notice"] is not None
    assert "No access code" not in r.json()["canvas_notice"]
    classic = {**again, "external_url": QUIZ, "canvas": {"quiz_type": "classic",
                                                         "access_code_source": "manual"}}
    r = app_client.put(f"/api/exams/{e['id']}", json=classic, headers=t).json()
    assert not any("quiz-lti" in d for d in r["allowed_domains"])
    assert r["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]
    opened = open_(app_client, t, e)
    assert opened.status_code == 200


def test_code_validation(app_client):
    t = login(app_client, "teacher")
    for bad in ("abc", "x" * 65, "ab\ncd"):
        r = app_client.post("/api/exams", headers=t,
                            json=body(canvas={"access_code_source": "manual", "access_code": bad}))
        assert r.status_code == 422, bad
    r = app_client.post("/api/exams", headers=t,
                        json=body(canvas={"access_code_source": "managed", "access_code": MANUAL_CODE}))
    assert r.status_code == 422


def test_canvas_fields_need_external_mode(app_client):
    t = login(app_client, "teacher")
    from .conftest import SAMPLE_EXAM
    r = app_client.post("/api/exams", headers=t, json={**SAMPLE_EXAM, "external_kind": "canvas"})
    assert r.status_code == 422


# ---------------------------------------------------------------- finish_url_regex
@pytest.mark.parametrize("rx", [
    r"(a)\1", r"(?<=a)b", r"(?<!a)b", r"a(?=b)", r"a(?!b)", r"(?P<n>a)(?P=n)", r"(?>a+)b",
    r"a++", r"a*+", r"(a+)+$", r"(.*)*x", r"([a-z]+)*", r"(", r")", r"[a-", "a" * 301,
    r"(a|aa)+$", r"(a|a)*b", r"(\d|\d\d)+x", r"(a?){30}a{30}", r"(.*a){20}",
    r"(a{1,1000}){1,1000}", "x+" * 60, ".*" * 30, "a?" * 20 + "a" * 20, r"(?:a|b){2,5}",
    r"(?P<id>\d+)/ok", r"(?<id>\d+)/ok", r"a{,5}", r"a{", r"a}", r"\Z", r"\h", r"\p{L}",
    r"[[a-z]&&[^b]]", r"[a--b]", r"(?i:a)", r"x(?i)", r"*a", r"a|*", r"((ab)+c)+", r"(\d{2}){3}",
    r"a{101}", r"a{2}{3}", r"(?(1)a|b)", "\\", r"\k<n>",
])
def test_regex_rejected(app_client, rx):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", headers=t, json=body(finish_url_regex=rx,
                                                           canvas={"access_code_source": "manual"}))
    assert r.status_code == 422, rx


@pytest.mark.parametrize("rx", [
    r"/quizzes/\d+/(submissions/\d+|history)", r"done|finished", r"^https://x\.edu/[a-z]+$",
    r"(?:ab)+c", r"a+?b", r"[\])]+x", r"(?i)thanks", r"a{2,5}b", r"(https?://)?x\.edu/\d+",
    r"/quizzes/\d+/submissions/\d+",
])
def test_regex_accepted_and_kept(app_client, rx):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(finish_url_regex=rx, canvas={"access_code_source": "manual"}))
    assert e["finish_url_regex"] == rx


def test_default_patterns_validate():
    for rx in canvas_mode.DEFAULT_FINISH.values():
        assert canvas_mode.validate_regex(rx) == rx


def test_plain_external_regex_validated(app_client):
    t = login(app_client, "teacher")
    plain = {"title": "Site", "duration_min": 20, "mode": "external",
             "external_url": "https://forms.example.org/x", "questions": []}
    r = app_client.post("/api/exams", json={**plain, "finish_url_regex": r"(a)\1"}, headers=t)
    assert r.status_code == 422
    e = create(app_client, t, {**plain, "finish_url_regex": "thanks"})
    assert e["finish_url_regex"] == "thanks" and e["external_kind"] is None and e["canvas"] is None


# ---------------------------------------------------------------- managed rotation
def test_managed_rotation_on_open_and_clear_on_close(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    assert e["canvas"]["access_code_source"] == "managed"
    e = open_(app_client, t, e).json()
    assert e["status"] == "live" and len(fake.codes) == 1
    code = fake.codes[0]
    assert len(code) == 24 and code.isalnum() and not set(code) & set("0O1lI")
    assert code not in json.dumps(e)
    h, a, hs = student_start(app_client, e)
    got = code_req(app_client, hs, a)
    assert got.status_code == 200 and got.json() == {"code": code, "valid_for_s": 60}
    assert got.headers["cache-control"] == "no-store"
    done = app_client.post(f"/api/exams/{e['id']}/close", headers=t)
    assert done.status_code == 200 and code not in done.text
    assert fake.codes[-1] == ""
    again = open_(app_client, t, e).json()
    assert again["status"] == "live" and fake.codes[-1] not in ("", code)


def test_code_status_and_blank_code_keeps_saved(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    assert e["canvas"]["code_status"] == "not_set"
    fake.fail_with = 500
    assert open_(app_client, t, e).status_code == 409
    got = app_client.get(f"/api/exams/{e['id']}", headers=t).json()
    assert got["canvas"]["code_status"] == "failed"
    fake.fail_with = None
    assert open_(app_client, t, e).json()["canvas"]["code_status"] == "set"
    m = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    for sent in ({"access_code_source": "manual"},
                 {"access_code_source": "manual", "access_code": ""}):
        r = app_client.put(f"/api/exams/{m['id']}", json=body(canvas=sent), headers=t)
        assert r.status_code == 200 and r.json()["canvas"]["code_status"] == "set"
    opened = open_(app_client, t, m).json()
    h, a, hs = student_start(app_client, opened)
    assert code_req(app_client, hs, a).json()["code"] == MANUAL_CODE
    assert "code_status" not in a["canvas"]


def test_replacing_code_needs_non_empty(app_client):
    t = login(app_client, "teacher")
    m = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    app_client.put(f"/api/exams/{m['id']}", headers=t,
                   json=body(canvas={"access_code_source": "manual", "access_code": "NewCode-99"}))
    opened = open_(app_client, t, m).json()
    h, a, hs = student_start(app_client, opened)
    assert code_req(app_client, hs, a).json()["code"] == "NewCode-99"


def test_rotation_fails_without_connection(app_client, fake):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    r = open_(app_client, t, e)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "canvas_code_failed" and "manual" in d["message"]
    assert app_client.get(f"/api/exams/{e['id']}", headers=t).json()["status"] == "draft"


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
def test_rotation_failure_409_and_draft_stays(app_client, fake, status):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    fake.fail_with = status
    r = open_(app_client, t, e)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "canvas_code_failed"
    assert app_client.get(f"/api/exams/{e['id']}", headers=t).json()["status"] == "draft"
    fake.fail_with = None
    assert open_(app_client, t, e).status_code == 200


def test_connection_for_other_host_not_used(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(url="https://other.test/courses/12/quizzes/34",
                                   canvas={"access_code_source": "managed"}))
    r = open_(app_client, t, e)
    assert r.status_code == 409 and not fake.codes


def test_close_survives_canvas_failure(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    e = open_(app_client, t, e).json()
    fake.fail_with = 500
    assert app_client.post(f"/api/exams/{e['id']}/close", headers=t).status_code == 200


def test_new_quiz_managed_falls_back_to_manual(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={"access_code_source": "managed"}))
    assert e["canvas"] == {"quiz_type": "new", "access_code_source": "manual",
                           "code_status": "not_set", "removed_hosts": [],
                           "remove_code_in_canvas": False}
    assert "Set the code in Canvas" in e["canvas_notice"] and "No access code" in e["canvas_notice"]
    r = open_(app_client, t, e)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "canvas_code_failed"
    assert not fake.codes
    ok = create(app_client, t, body(url=NEW_QUIZ, canvas={"access_code_source": "managed",
                                                          "access_code": MANUAL_CODE}))
    assert open_(app_client, t, ok).status_code == 200 and not fake.codes


def test_manual_without_code_cannot_open(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual"}))
    r = open_(app_client, t, e)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "canvas_code_failed"


# ---------------------------------------------------------------- code endpoint
def test_manual_code_delivered_with_event(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    r = code_req(app_client, hs, a)
    assert r.status_code == 200 and r.json() == {"code": MANUAL_CODE, "valid_for_s": 60}
    assert "canvas_code_issued" in events(app_client, t, e)
    flags = app_client.get(f"/api/exams/{e['id']}/live", headers=t).json()["students"][0]
    assert MANUAL_CODE not in json.dumps(flags)


def test_student_views_never_carry_code(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    assert a["external_kind"] == "canvas"
    assert a["canvas"] == {"quiz_type": "classic", "access_code_source": "manual"}
    assert a["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]
    code_req(app_client, hs, a)
    surfaces = [
        app_client.get(f"/api/student/attempts/{a['attempt_id']}", headers=hs),
        app_client.get("/api/student/exams", headers=h),
        app_client.get(f"/api/exams/{e['id']}", headers=t),
        app_client.get("/api/exams", headers=t),
        app_client.get(f"/api/exams/{e['id']}/results", headers=t),
        app_client.get(f"/api/exams/{e['id']}/live", headers=t),
        app_client.get(f"/api/exams/{e['id']}/attempts/{a['attempt_id']}/events", headers=t),
    ]
    for r in surfaces:
        assert MANUAL_CODE not in r.text, r.request.url


def test_code_never_logged(app_client, manual_live, caplog):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    with caplog.at_level(logging.DEBUG):
        assert code_req(app_client, hs, a).status_code == 200
    assert MANUAL_CODE not in caplog.text


def test_stored_code_is_encrypted(app_client, manual_live):
    t, e = manual_live
    path = app_client.app.state.settings.database_url.split("///", 1)[1]
    con = sqlite3.connect(path)
    raw = con.execute("select canvas_code_enc from exams where id=?", (e["id"],)).fetchone()[0]
    con.close()
    assert raw and MANUAL_CODE not in raw


def test_no_proof_or_bad_proof_403(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    r = code_req(app_client, hs, a, send_proof=False)
    assert r.status_code == 403 and r.json()["detail"] == APP_PROOF_DETAIL
    assert code_req(app_client, hs, a, key="some-other-key").status_code == 403
    assert MANUAL_CODE not in r.text
    assert "canvas_code_refused" in events(app_client, t, e)


def test_stale_timestamp_refused(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    old = int(time.time() * 1000) - 10 * 60 * 1000
    assert code_req(app_client, hs, a, ts=old).status_code == 403
    secs = int(time.time())
    assert code_req(app_client, hs, a, ts=secs).status_code == 403


def test_wrong_device_and_session(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    assert code_req(app_client, hs, a, device=DEV_B).status_code == 403
    other = {**h, "X-FairPlay-Session": "another-session-id"}
    assert code_req(app_client, other, a).status_code == 409
    assert code_req(app_client, h, a).status_code == 409
    assert code_req(app_client, hs, a, session="another-session-id").status_code == 403


def test_other_student_cannot_ask(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    bob = login(app_client, "bob")
    r = code_req(app_client, {**bob, "X-FairPlay-Session": a["session"]}, a)
    assert r.status_code == 404 and MANUAL_CODE not in r.text


def test_closed_exam_and_submitted_attempt(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    app_client.post(f"/api/exams/{e['id']}/close", headers=t)
    r = code_req(app_client, hs, a)
    assert r.status_code == 409 and MANUAL_CODE not in r.text


def test_submitted_attempt_refused(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    app_client.post(f"/api/student/attempts/{a['attempt_id']}/submit", headers=hs,
                    json={"answers": {}, "client_seq": 0})
    assert code_req(app_client, hs, a).status_code == 409


def test_attempt_rate_limit(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    for _ in range(canvas_mode.CODE_REQUESTS_PER_ATTEMPT):
        assert code_req(app_client, hs, a).status_code == 200
    r = code_req(app_client, hs, a)
    assert r.status_code == 429 and MANUAL_CODE not in r.text
    assert "canvas_code_refused" in events(app_client, t, e)


def test_exam_rate_limit(app_client, manual_live, monkeypatch):
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_EXAM_PER_MIN", 2)
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_STUDENT_FACTOR", 0)
    t, e = manual_live
    ha = student_start(app_client, e, "alice", DEV_A)
    hb = student_start(app_client, e, "bob", DEV_B)
    assert code_req(app_client, ha[2], ha[1]).status_code == 200
    assert code_req(app_client, hb[2], hb[1], device=DEV_B).status_code == 200
    assert code_req(app_client, ha[2], ha[1]).status_code == 429


def test_non_canvas_exam_refused_and_unchanged(app_client):
    from .conftest import SAMPLE_EXAM
    t = login(app_client, "teacher")
    e = app_client.post("/api/exams", json=SAMPLE_EXAM, headers=t).json()
    assert e["external_kind"] is None and e["canvas"] is None and e["finish_url_regex"] is None
    assert e["canvas_notice"] is None
    e = open_(app_client, t, e).json()
    h, a, hs = student_start(app_client, e)
    assert "external_kind" not in a and "canvas" not in a
    assert code_req(app_client, hs, a).status_code == 409


def test_plain_external_exam_unchanged(app_client):
    t = login(app_client, "teacher")
    plain = {"title": "Site", "duration_min": 20, "mode": "external", "questions": [],
             "external_url": "https://forms.example.org/x", "allowed_domains": ["Example.org"],
             "finish_url_contains": "done"}
    e = create(app_client, t, plain)
    assert e["allowed_domains"] == ["example.org"] and e["finish_url_contains"] == "done"
    e = open_(app_client, t, e).json()
    h, a, hs = student_start(app_client, e)
    assert a["external_kind"] is None and a["finish_url_regex"] is None
    assert code_req(app_client, hs, a).status_code == 409


def test_client_event_types():
    from app.app_client import CLIENT_EVENT_TYPES, SERVER_EVENT_TYPES, client_event_type
    for t in ("canvas_code_failed", "canvas_upload_unsupported", "canvas_sso_popup"):
        assert t in CLIENT_EVENT_TYPES and client_event_type(t) == t
    for t in ("canvas_code_issued", "canvas_code_refused"):
        assert t in SERVER_EVENT_TYPES and client_event_type(t) == f"client_{t}"


# ---------------------------------------------------------------- migration
def test_old_database_gets_new_columns(tmp_path, static_dir):
    db = tmp_path / "old.db"

    def make():
        s = Settings(database_url=f"sqlite+aiosqlite:///{db}", redis_url=None,
                     secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                     static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                     require_app=False, app_keys=[KEY], dev=False, seed_loadtest_users=0,
                     seed_demo=True, single_process=True)
        return TestClient(create_app(s))

    with make():
        pass
    con = sqlite3.connect(db)
    for col in ("finish_url_regex", "external_kind", "canvas_quiz_type", "canvas_code_source",
                "canvas_code_enc", "canvas_code_failed"):
        con.execute(f"ALTER TABLE exams DROP COLUMN {col}")
    con.commit()
    con.close()
    with make() as c:
        t = login(c, "teacher")
        e = create(c, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
        assert e["external_kind"] == "canvas"


# ---------------------------------------------------------------- helpers for the review fixes
def db_exec(c, sql, *args):
    path = c.app.state.settings.database_url.split("///", 1)[1]
    con = sqlite3.connect(path)
    rows = con.execute(sql, args).fetchall()
    con.commit()
    con.close()
    return rows


class SlowTransport(httpx.AsyncBaseTransport):
    def __init__(self, fake, delay):
        self.fake, self.delay = fake, delay

    async def handle_async_request(self, request):
        await request.aread()
        await asyncio.sleep(self.delay)
        return self.fake.handle(request)


def teacher_view(c, t, e):
    return c.get(f"/api/exams/{e['id']}", headers=t).json()


# ---------------------------------------------------------------- rate limits
def test_class_sized_burst_all_succeed(tmp_path, static_dir):
    from .conftest import make_class
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'burst.db'}", redis_url=None,
                 secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                 static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                 require_app=True, app_keys=[KEY], dev=False, seed_loadtest_users=150,
                 seed_demo=True, single_process=True)
    with TestClient(create_app(s)) as c:
        t = login(c, "teacher")
        names = [f"lt{i:04d}" for i in range(1, 151)]
        k = make_class(c, t, "Big class", names)
        e = create(c, t, body(class_id=k["id"],
                              canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
        e = open_(c, t, e).json()
        for i, name in enumerate(names):
            h = login(c, name, "loadtest")
            device = f"device-{i:04d}-burst"
            r = c.post(f"/api/student/exams/{e['id']}/start",
                       headers={**h, "X-FairPlay-Platform": "mac"},
                       json={"start_code": e["start_code"], "client": proof(c, h, e["id"], device)})
            assert r.status_code == 200, r.text
            a = r.json()
            got = code_req(c, {**h, "X-FairPlay-Session": a["session"]}, a, device=device)
            assert got.status_code == 200, (name, got.text)


def test_junk_requests_do_not_exhaust_a_student(app_client, manual_live, monkeypatch):
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_EXAM_PER_MIN", 2)
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_STUDENT_FACTOR", 0)
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    for _ in range(canvas_mode.CODE_REQUESTS_PER_ATTEMPT + 4):
        assert code_req(app_client, hs, a, key="wrong-key").status_code == 403
        assert code_req(app_client, hs, a, send_proof=False).status_code == 403
        assert code_req(app_client, {**h, "X-FairPlay-Session": "x"}, a).status_code == 409
        assert code_req(app_client, hs, a, ts=1).status_code == 403
    assert code_req(app_client, hs, a).status_code == 200
    assert code_req(app_client, hs, a).status_code == 200


def test_budget_counts_only_verified_and_is_sized_to_roster(app_client, manual_live, monkeypatch):
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_EXAM_PER_MIN", 1)
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_STUDENT_FACTOR", 1)
    t, e = manual_live
    devs = [DEV_A, DEV_B, "device-cccc-3333"]
    users = ["alice", "bob", "carol"]
    started = [student_start(app_client, e, u, d) for u, d in zip(users, devs)]
    for (h, a, hs), d in zip(started, devs):
        assert code_req(app_client, hs, a, device=d).status_code == 200
    r = code_req(app_client, started[0][2], started[0][1], device=DEV_A)
    assert r.status_code == 429


def test_429_carries_retry_after(app_client, manual_live, monkeypatch):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    for _ in range(canvas_mode.CODE_REQUESTS_PER_ATTEMPT):
        assert code_req(app_client, hs, a).status_code == 200
    r = code_req(app_client, hs, a)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= canvas_mode.CODE_ATTEMPT_WINDOW_S


def test_429_for_exam_carries_retry_after(app_client, manual_live, monkeypatch):
    t, e = manual_live
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_EXAM_PER_MIN", 1)
    monkeypatch.setattr(canvas_mode, "CODE_REQUESTS_PER_STUDENT_FACTOR", 0)
    hb = student_start(app_client, e, "bob", DEV_B)
    assert code_req(app_client, hb[2], hb[1], device=DEV_B).status_code == 200
    hc = student_start(app_client, e, "carol", "device-cccc-3333")
    r = code_req(app_client, hc[2], hc[1], device="device-cccc-3333")
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60


def test_attempt_window_is_ten_minutes():
    assert canvas_mode.CODE_ATTEMPT_WINDOW_S == 600


# ---------------------------------------------------------------- proof field, approval, replay
def test_non_ascii_and_malformed_proof_never_500(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    url = f"/api/student/attempts/{a['attempt_id']}/canvas-code"
    for bad in ("é" * 64, "zz", "A" * 64, "0" * 63, "0" * 65, "\u0000" * 64):
        r = app_client.post(url, json={"proof": bad, "ts": fresh_ts()}, headers=hs)
        assert r.status_code == 422, bad
    from app.app_client import verify
    assert verify(app_client.app.state.settings, "m", "é" * 64) is False
    assert code_req(app_client, hs, a, send_proof=False).status_code == 403


def test_refusals_logged_once_per_reason(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    for _ in range(5):
        assert code_req(app_client, hs, a, key="wrong-key").status_code == 403
    assert events(app_client, t, e).count("canvas_code_refused") == 1
    assert code_req(app_client, hs, a, ts=1).status_code == 403
    assert events(app_client, t, e).count("canvas_code_refused") == 2


def test_pending_resume_approval_blocks_code(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    db_exec(app_client, "update attempts set pending_resume=1 where id=?", a["attempt_id"])
    r = code_req(app_client, hs, a)
    assert r.status_code == 409 and MANUAL_CODE not in r.text and "teacher" in r.json()["detail"]
    db_exec(app_client, "update attempts set pending_resume=0 where id=?", a["attempt_id"])
    assert code_req(app_client, hs, a).status_code == 200


def test_replayed_proof_refused_new_ts_allowed(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    ts = fresh_ts()
    assert code_req(app_client, hs, a, ts=ts).status_code == 200
    r = code_req(app_client, hs, a, ts=ts)
    assert r.status_code == 409 and MANUAL_CODE not in r.text
    assert code_req(app_client, hs, a).status_code == 200


# ---------------------------------------------------------------- save and open
def test_put_without_canvas_block_keeps_stored_settings(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={"quiz_type": "assignment",
                                                         "access_code_source": "manual",
                                                         "access_code": MANUAL_CODE}))
    r = app_client.put(f"/api/exams/{e['id']}", json=body(url=NEW_QUIZ, title="Renamed"), headers=t)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["canvas"]["quiz_type"] == "assignment" and v["canvas"]["access_code_source"] == "manual"
    assert v["canvas"]["code_status"] == "set"
    opened = open_(app_client, t, e).json()
    h, a, hs = student_start(app_client, opened)
    assert code_req(app_client, hs, a).json()["code"] == MANUAL_CODE


def test_put_block_without_source_keeps_source(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    r = app_client.put(f"/api/exams/{e['id']}", json=body(canvas={"quiz_type": "classic"}), headers=t)
    assert r.json()["canvas"]["access_code_source"] == "manual"
    assert r.json()["canvas"]["code_status"] == "set"


def test_finish_url_contains_ignored_for_canvas(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(finish_url_contains="done",
                                   canvas={"access_code_source": "manual"}))
    assert e["finish_url_contains"] is None
    assert e["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]


def test_canvas_error_text_not_copied(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    fake.fail_with = 422
    fake.error_text = "INTERNAL-CANVAS-DETAIL"
    r = open_(app_client, t, e)
    assert r.status_code == 409
    assert "INTERNAL-CANVAS-DETAIL" not in r.text and "HTTP 422" in r.json()["detail"]["message"]


def test_failed_open_leaves_no_half_state(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    fake.fail_with = 500
    r = app_client.post(f"/api/exams/{e['id']}/open", headers=t, json={"open_to": "all"})
    assert r.status_code == 409
    db_exec(app_client, "update exams set open_to=null where id=?", e["id"])
    sid = db_exec(app_client, "select id from users where username='alice'")[0][0]
    r = app_client.post(f"/api/exams/{e['id']}/open", headers=t, json={"open_to": [sid]})
    assert r.status_code == 409
    row = db_exec(app_client, "select status, open_to, canvas_open_claim from exams where id=?", e["id"])[0]
    assert row[0] == "draft" and row[1] is None and row[2] is None


def test_open_refused_while_another_open_is_in_progress(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    db_exec(app_client, "update exams set canvas_open_claim=? where id=?",
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), e["id"])
    r = open_(app_client, t, e)
    assert r.status_code == 409 and not fake.codes
    db_exec(app_client, "update exams set canvas_open_claim=null where id=?", e["id"])
    assert open_(app_client, t, e).status_code == 200


def test_concurrent_opens_rotate_once(app_client):
    f = FakeCanvas()

    async def no_sleep(_s):
        return None

    lms.set_transport_factory(lambda pid, url: SlowTransport(f, 0.4))
    lms.set_sleep(no_sleep)
    try:
        t = login(app_client, "teacher")
        connect(app_client, t)
        e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
        out = []

        def go():
            out.append(open_(app_client, t, e).status_code)

        threads = [threading.Thread(target=go) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert sorted(out) == [200, 409]
        assert len(f.codes) == 1
    finally:
        lms.set_transport_factory(None)
        lms.set_sleep(None)


# ---------------------------------------------------------------- close and release
def test_close_failure_tells_teacher(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    e = open_(app_client, t, e).json()
    fake.fail_with = 500
    done = app_client.post(f"/api/exams/{e['id']}/close", headers=t)
    assert done.status_code == 200
    v = teacher_view(app_client, t, e)
    assert "Remove the access code in Canvas" in v["canvas_notice"]
    assert v["canvas"]["remove_code_in_canvas"] is True
    fake.fail_with = None
    again = open_(app_client, t, e).json()
    assert again["canvas"]["remove_code_in_canvas"] is False and again["canvas_notice"] is None


def test_close_with_slow_canvas_times_out(app_client, monkeypatch):
    f = FakeCanvas()
    lms.set_transport_factory(lambda pid, url: SlowTransport(f, 0))
    try:
        t = login(app_client, "teacher")
        connect(app_client, t)
        e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
        e = open_(app_client, t, e).json()
        lms.set_transport_factory(lambda pid, url: SlowTransport(f, 2))
        monkeypatch.setattr(canvas_mode, "CANVAS_CALL_TIMEOUT_S", 0.2)
        started = time.time()
        assert app_client.post(f"/api/exams/{e['id']}/close", headers=t).status_code == 200
        assert time.time() - started < 1.9
        v = teacher_view(app_client, t, e)
        assert v["status"] == "closed" and v["canvas"]["remove_code_in_canvas"] is True
    finally:
        lms.set_transport_factory(None)


def test_close_clears_manual_code_and_reopen_needs_it(app_client, manual_live):
    t, e = manual_live
    h, a, hs = student_start(app_client, e)
    assert app_client.post(f"/api/exams/{e['id']}/close", headers=t).status_code == 200
    assert teacher_view(app_client, t, e)["canvas"]["code_status"] == "not_set"
    assert db_exec(app_client, "select canvas_code_enc from exams where id=?", e["id"])[0][0] is None
    r = open_(app_client, t, e)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "canvas_code_failed"
    r = app_client.post(f"/api/exams/{e['id']}/open", headers=t,
                        json={"canvas_access_code": "Another-55"})
    assert r.status_code == 200
    h2, a2, hs2 = student_start(app_client, r.json(), "bob", DEV_B)
    assert code_req(app_client, hs2, a2, device=DEV_B).json()["code"] == "Another-55"


def test_close_sweeps_before_release(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    e = open_(app_client, t, e).json()
    h, a, hs = student_start(app_client, e)
    app_client.post(f"/api/exams/{e['id']}/close", headers=t)
    assert fake.codes[-1] == ""
    got = app_client.get(f"/api/student/attempts/{a['attempt_id']}", headers=hs).json()
    assert got["status"] == "submitted"


def test_switching_away_releases_live_managed_code(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    open_(app_client, t, e)
    db_exec(app_client, "update exams set status='draft' where id=?", e["id"])
    r = app_client.put(f"/api/exams/{e['id']}", headers=t,
                       json=body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    assert r.status_code == 200, r.text
    assert fake.codes[-1] == ""
    e2 = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    open_(app_client, t, e2)
    db_exec(app_client, "update exams set status='draft' where id=?", e2["id"])
    plain = {"title": "Site", "duration_min": 20, "mode": "external", "questions": [],
             "external_url": "https://forms.example.org/x"}
    n = len(fake.codes)
    assert app_client.put(f"/api/exams/{e2['id']}", headers=t, json=plain).status_code == 200
    assert fake.codes[n:] == [""]
    assert teacher_view(app_client, t, e2)["external_kind"] is None


def test_switch_release_failure_is_not_silent(app_client, fake):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    open_(app_client, t, e)
    db_exec(app_client, "update exams set status='draft' where id=?", e["id"])
    fake.fail_with = 500
    r = app_client.put(f"/api/exams/{e['id']}", headers=t,
                       json=body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    assert r.status_code == 200
    assert "Remove the access code in Canvas" in r.json()["canvas_notice"]


# ---------------------------------------------------------------- platform and hosts
@pytest.mark.parametrize("platform", [None, "windows", "ipad", "android"])
def test_canvas_start_refused_off_mac(app_client, manual_live, platform):
    t, e = manual_live
    h = login(app_client, "alice")
    headers = dict(h)
    if platform:
        headers["X-FairPlay-Platform"] = platform
    r = app_client.post(f"/api/student/exams/{e['id']}/start", headers=headers,
                        json={"start_code": e["start_code"],
                              "client": proof(app_client, h, e["id"], DEV_A)})
    assert r.status_code == 409
    assert r.json()["detail"] == "Canvas exams are not available on this device yet"


def test_removed_hosts_round_trip(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual",
                                           "removed_hosts": ["Login.Microsoft.com"]}))
    assert "login.microsoft.com" not in e["allowed_domains"]
    assert "login.microsoftonline.com" in e["allowed_domains"]
    assert e["canvas"]["removed_hosts"] == ["login.microsoft.com"]
    r = app_client.put(f"/api/exams/{e['id']}", headers=t,
                       json=body(allowed_domains=e["allowed_domains"],
                                 canvas={"access_code_source": "manual"})).json()
    assert r["canvas"]["removed_hosts"] == ["login.microsoft.com"]
    assert "login.microsoft.com" not in r["allowed_domains"]
    r = app_client.put(f"/api/exams/{e['id']}", headers=t,
                       json=body(canvas={"access_code_source": "manual", "removed_hosts": []})).json()
    assert "login.microsoft.com" in r["allowed_domains"] and r["canvas"]["removed_hosts"] == []


def test_canvas_host_cannot_be_removed(app_client):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", headers=t, json=body(
        canvas={"access_code_source": "manual", "removed_hosts": ["canvas.test"]}))
    assert r.status_code == 422
    e = create(app_client, t, body(allowed_domains=["docs.python.org"],
                                   canvas={"access_code_source": "manual"}))
    r = app_client.put(f"/api/exams/{e['id']}", headers=t, json=body(
        canvas={"access_code_source": "manual", "removed_hosts": ["canvas.test"]}))
    assert r.status_code == 422


def test_policy_blocked_sign_in_host_is_reported(app_client, monkeypatch):
    monkeypatch.setenv("FAIRPLAY_CANVAS_SSO_HOSTS", "sso.example.edu,chatgpt.com")
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": MANUAL_CODE}))
    assert "chatgpt.com" not in e["allowed_domains"]
    assert "chatgpt.com" in e["canvas_notice"]
    got = teacher_view(app_client, t, e)
    assert "chatgpt.com" in got["canvas_notice"]


# ---------------------------------------------------------------- code never leaks
def test_managed_code_not_in_logs_or_output(app_client, fake, caplog, capfd):
    t = login(app_client, "teacher")
    connect(app_client, t)
    e = create(app_client, t, body(canvas={"access_code_source": "managed"}))
    with caplog.at_level(logging.DEBUG):
        e = open_(app_client, t, e).json()
        h, a, hs = student_start(app_client, e)
        code = fake.codes[0]
        assert code_req(app_client, hs, a).json()["code"] == code
        code_req(app_client, hs, a, key="wrong-key")
        app_client.post(f"/api/exams/{e['id']}/close", headers=t)
    out = capfd.readouterr()
    assert code not in caplog.text and code not in out.out and code not in out.err
    assert code not in json.dumps(events(app_client, t, e))

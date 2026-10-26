import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import canvas_mode
from app.config import Settings
from app.main import create_app

from .conftest import login
from .test_app_session import DEV_A, KEY, app_client, proof  # noqa: F401  (fixtures)

CANVAS = "https://canvas.test"
QUIZ = f"{CANVAS}/courses/12/quizzes/34"
NEW_QUIZ = f"{CANVAS}/courses/12/assignments/56"


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
    r = c.post(f"/api/student/exams/{e['id']}/start", headers={**h, "X-FairPlay-Platform": "mac", "X-FairPlay-Version": "0.7.2"},
               json={"start_code": e["start_code"], "client": proof(c, h, e["id"], device)})
    assert r.status_code == 200, r.text
    a = r.json()
    return h, a, {**h, "X-FairPlay-Session": a["session"]}


def events(c, t, e):
    res = c.get(f"/api/exams/{e['id']}/results", headers=t).json()
    return [ev["type"] for a in res["attempts"] for ev in a.get("events", [])]


# ---------------------------------------------------------------- settings and views
def test_create_fills_defaults(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    assert e["external_kind"] == "canvas"
    assert e["canvas"] == {"quiz_type": "classic", "removed_hosts": [], "open_book": False,
                           "sign_in_hosts": canvas_mode.sso_hosts(),
                           "scope": {"course_id": "12", "item_kind": "quizzes", "item_id": "34"}}
    assert e["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]
    assert "canvas.test" in e["allowed_domains"]
    assert "login.microsoftonline.com" in e["allowed_domains"]
    assert not any("quiz-lti" in d for d in e["allowed_domains"])
    assert "access_code" not in str(e) and "code_status" not in str(e)


def test_quiz_type_from_url_and_new_domains(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={}))
    assert e["canvas"]["quiz_type"] == "new"
    assert e["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["new"]
    assert any(d.startswith("*.quiz-lti-iad-prod.") for d in e["allowed_domains"])
    a = create(app_client, t, body(url=NEW_QUIZ, canvas={"quiz_type": "assignment"}))
    assert a["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["assignment"]
    assert not any("quiz-lti" in d for d in a["allowed_domains"])


@pytest.mark.parametrize("path", ["/somewhere", "/courses/12", "/courses/12/quizzes",
                                  "/courses/12/pages/3", "/courses/12/quizzes/34x"])
def test_link_without_one_quiz_is_refused(app_client, path):
    t = login(app_client, "teacher")
    for quiz_type in (None, "new"):
        canvas = {}
        if quiz_type:
            canvas["quiz_type"] = quiz_type
        b = body(url=f"{CANVAS}{path}", canvas=canvas)
        r = app_client.post("/api/exams", json=b, headers=t)
        assert r.status_code == 422 and "must open one quiz or assignment" in r.text
    e = create(app_client, t, body(canvas={}))
    r = app_client.put(f"/api/exams/{e['id']}", json=body(url=f"{CANVAS}{path}"), headers=t)
    assert r.status_code == 422 and "must open one quiz or assignment" in r.text
    assert teacher_view(app_client, t, e)["external_url"] == QUIZ


def test_plain_website_exam_may_have_any_link(app_client):
    t = login(app_client, "teacher")
    b = {"title": "Site", "duration_min": 30, "mode": "external",
         "external_url": f"{CANVAS}/somewhere", "questions": []}
    assert app_client.post("/api/exams", json=b, headers=t).status_code == 201


def test_http_link_refused(app_client):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", headers=t,
                        json=body(url="http://canvas.test/courses/1/quizzes/2"))
    assert r.status_code == 422


def test_teacher_extras_kept_and_policy_applied(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(allowed_domains=["docs.python.org"],
                                   canvas={}))
    assert "docs.python.org" in e["allowed_domains"]
    r = app_client.post("/api/exams", headers=t, json=body(allowed_domains=["chatgpt.com"],
                                                           canvas={}))
    assert r.status_code == 422 and "chatgpt.com" in r.text
    r = app_client.post("/api/exams", headers=t,
                        json=body(url="https://chatgpt.com/courses/1/quizzes/2"))
    assert r.status_code == 422


def test_sso_hosts_from_env(app_client, monkeypatch):
    monkeypatch.setenv("FAIRPLAY_CANVAS_SSO_HOSTS", "sso.example.edu")
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    assert e["allowed_domains"] == ["canvas.test", "sso.example.edu"]


def test_round_trip_put_resets_stale_domains(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={}))
    again = {**body(url=NEW_QUIZ, canvas=e["canvas"]), "allowed_domains": e["allowed_domains"],
             "finish_url_regex": e["finish_url_regex"]}
    r = app_client.put(f"/api/exams/{e['id']}", json=again, headers=t)
    assert r.status_code == 200, r.text
    classic = {**again, "external_url": QUIZ, "canvas": {"quiz_type": "classic"}}
    r = app_client.put(f"/api/exams/{e['id']}", json=classic, headers=t).json()
    assert not any("quiz-lti" in d for d in r["allowed_domains"])
    assert r["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]
    assert open_(app_client, t, e).status_code == 200


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
    ".*.*.*.*.*.*.*.*x", r".*.*x", r"\w+\w+", r".+.*", r"a+a+", r"x+X+", r"\d+\d*x", r"(.*).*x",
    r"[ab]+[bc]*", r"\D+\w+", r".*x.*y.*", r"\d+\.\d+/\d+",
    r"a{101}", r"a{2}{3}", r"(?(1)a|b)", "\\", r"\k<n>",
    # the Mac app (CanvasFinish.isSafe) refuses these, so the server must too
    r"(?:(?:[ab])){2,}", r"[ab](\d(?:\.)\d){2,}\.", r"((/))*", r"(a|b){1}", r"((a)b){2}",
])
def test_regex_rejected(app_client, rx):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", headers=t, json=body(finish_url_regex=rx,
                                                           canvas={}))
    assert r.status_code == 422, rx


@pytest.mark.parametrize("rx", [
    r"/quizzes/\d+/(submissions/\d+|history)", r"done|finished", r"^https://x\.edu/[a-z]+$",
    r"(?:ab)+c", r"a+?b", r"[\])]+x", r"(?i)thanks", r"a{2,5}b", r"(https?://)?x\.edu/\d+",
    r"/quizzes/\d+/submissions/\d+", r"(?:(?:a)b)?c", r"(ab){2}", r"((a)b)c",
    r"a+b+", r"\d+\s+", r".*x.*", r"a+\d*", r"\w+\s*done",
])
def test_regex_accepted_and_kept(app_client, rx):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(finish_url_regex=rx, canvas={}))
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


# ---------------------------------------------------------------- no access code
def test_non_canvas_exam_unchanged(app_client):
    from .conftest import SAMPLE_EXAM
    t = login(app_client, "teacher")
    e = app_client.post("/api/exams", json=SAMPLE_EXAM, headers=t).json()
    assert e["external_kind"] is None and e["canvas"] is None and e["finish_url_regex"] is None
    assert e["canvas_notice"] is None
    e = open_(app_client, t, e).json()
    h, a, hs = student_start(app_client, e)
    assert "external_kind" not in a and "canvas" not in a and "canvas_scope" not in a


def test_old_code_fields_are_ignored_and_never_returned(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"access_code_source": "manual", "access_code": "Secret-Code-1"}))
    assert "Secret-Code-1" not in str(e) and "access_code_source" not in e["canvas"]
    r = app_client.put(f"/api/exams/{e['id']}", json=body(canvas={"access_code_source": "managed"}), headers=t)
    assert r.status_code == 200 and "access_code" not in str(r.json())
    opened = app_client.post(f"/api/exams/{e['id']}/open", headers=t,
                             json={"open_to": "all", "canvas_access_code": "Secret-Code-1"})
    assert opened.status_code == 200 and "Secret-Code-1" not in opened.text


def test_open_and_close_need_no_canvas_connection(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    live = open_(app_client, t, e)
    assert live.status_code == 200 and live.json()["status"] == "live"
    assert app_client.post(f"/api/exams/{e['id']}/close", headers=t).status_code == 200
    assert open_(app_client, t, e).status_code == 200


def test_code_endpoint_is_gone(app_client):
    t = login(app_client, "teacher")
    e = open_(app_client, t, create(app_client, t, body(canvas={}))).json()
    h, a, hs = student_start(app_client, e)
    r = app_client.post(f"/api/student/attempts/{a['attempt_id']}/canvas-code", headers=hs,
                        json={"proof": "0" * 64, "ts": int(time.time() * 1000)})
    assert r.status_code in (404, 405)


def test_legacy_row_with_code_columns_still_loads(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    db_exec(app_client, "UPDATE exams SET canvas_code_source='managed', canvas_code_enc='x', "
                        "canvas_code_failed=1, canvas_release_failed=1 WHERE id=?", e["id"])
    v = teacher_view(app_client, t, e)
    assert v["canvas"]["quiz_type"] == "classic" and "code" not in str(v["canvas"])
    assert open_(app_client, t, e).status_code == 200


def test_quiz_started_event_is_stored_and_not_a_flag(app_client):
    t = login(app_client, "teacher")
    e = open_(app_client, t, create(app_client, t, body(canvas={}))).json()
    h, a, hs = student_start(app_client, e)
    r = app_client.post(f"/api/student/attempts/{a['attempt_id']}/events", headers=hs,
                        json={"events": [{"type": "canvas_quiz_started", "detail": "", "client_time": None}]})
    assert r.status_code == 200
    assert "canvas_quiz_started" in events(app_client, t, e)
    from app.routes_teacher import NON_FLAG_TYPES
    assert "canvas_quiz_started" in NON_FLAG_TYPES and "client_canvas_quiz_started" in NON_FLAG_TYPES


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
    assert "canvas_scope" not in a


def test_client_event_types():
    from app.app_client import CLIENT_EVENT_TYPES, SERVER_EVENT_TYPES, client_event_type
    for t in ("canvas_upload_unsupported", "canvas_sso_popup", "canvas_quiz_started"):
        assert t in CLIENT_EVENT_TYPES and client_event_type(t) == t
    assert not any("canvas_code" in t for t in CLIENT_EVENT_TYPES | SERVER_EVENT_TYPES)


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
    for col in ("finish_url_regex", "external_kind", "canvas_quiz_type", "canvas_open_book"):
        con.execute(f"ALTER TABLE exams DROP COLUMN {col}")
    con.commit()
    con.close()
    with make() as c:
        t = login(c, "teacher")
        e = create(c, t, body(canvas={}))
        assert e["external_kind"] == "canvas"


def db_exec(c, sql, *args):
    path = c.app.state.settings.database_url.split("///", 1)[1]
    con = sqlite3.connect(path)
    rows = con.execute(sql, args).fetchall()
    con.commit()
    con.close()
    return rows


def teacher_view(c, t, e):
    return c.get(f"/api/exams/{e['id']}", headers=t).json()


# ---------------------------------------------------------------- save and open
def test_put_without_canvas_block_keeps_stored_settings(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={"quiz_type": "assignment"}))
    r = app_client.put(f"/api/exams/{e['id']}", json=body(url=NEW_QUIZ, title="Renamed"), headers=t)
    assert r.status_code == 200, r.text
    assert r.json()["canvas"]["quiz_type"] == "assignment"


def test_finish_url_contains_ignored_for_canvas(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(finish_url_contains="done",
                                   canvas={}))
    assert e["finish_url_contains"] is None
    assert e["finish_url_regex"] == canvas_mode.DEFAULT_FINISH["classic"]


# ---------------------------------------------------------------- platform and hosts
def _start_as(c, e, platform, version, user="alice"):
    h = login(c, user)
    headers = dict(h)
    if platform:
        headers["X-FairPlay-Platform"] = platform
    if version:
        headers["X-FairPlay-Version"] = version
    return c.post(f"/api/student/exams/{e['id']}/start", headers=headers,
                  json={"start_code": e["start_code"], "client": proof(c, h, e["id"], DEV_A)})


@pytest.mark.parametrize("platform,version", [
    ("windows", "9.9"), ("ipad", "9.9"), ("ios", "9.9"), (None, None), ("linux", "9.9"), (None, "9.9"),
])
def test_canvas_start_refused_where_no_minimum_is_set(app_client, platform, version):
    t = login(app_client, "teacher")
    e = open_(app_client, t, create(app_client, t, body(canvas={}))).json()
    r = _start_as(app_client, e, platform, version)
    assert r.status_code == 409 and r.json()["detail"] == "Canvas exams are not available on this device yet"


@pytest.mark.parametrize("version", ["0.7.1", "0.7.0", "0.6.9", None, "abc", ""])
def test_old_or_unreadable_mac_gets_the_update_answer(app_client, version):
    t = login(app_client, "teacher")
    e = open_(app_client, t, create(app_client, t, body(canvas={}))).json()
    r = _start_as(app_client, e, "mac", version)
    assert r.status_code == 426 and r.json()["min_app_version"] == "0.7.2"


def test_canvas_start_allowed_on_current_mac(app_client):
    t = login(app_client, "teacher")
    e = open_(app_client, t, create(app_client, t, body(canvas={}))).json()
    assert _start_as(app_client, e, "mac", "0.7.2").status_code == 200
    assert _start_as(app_client, e, "mac", "0.10", user="bob").status_code == 200


def test_canvas_min_version_per_platform_can_be_enabled(app_client):
    settings = app_client.app.state.settings
    t = login(app_client, "teacher")
    e = open_(app_client, t, create(app_client, t, body(canvas={}))).json()
    object.__setattr__(settings, "canvas_min_version_windows", "1.2")
    object.__setattr__(settings, "canvas_min_version_ipad", "2.0")
    assert _start_as(app_client, e, "windows", "1.1").status_code == 426
    assert _start_as(app_client, e, "windows", "1.2").status_code == 200
    assert _start_as(app_client, e, "ipad", "2.0", user="bob").status_code == 200
    assert _start_as(app_client, e, "ios", "2.0", user="carol").status_code == 200
    assert _start_as(app_client, e, "ios", "1.9", user="alice").status_code == 426
    object.__setattr__(settings, "canvas_min_version_mac", "")
    assert _start_as(app_client, e, "mac", "9.9", user="alice").status_code == 409


def test_scope_less_canvas_exam_is_never_started_or_opened(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    live = open_(app_client, t, e).json()
    db_exec(app_client, "UPDATE exams SET external_url = ? WHERE id = ?", f"{CANVAS}/somewhere", e["id"])
    r = _start_as(app_client, live, "mac", "0.7.2")
    assert r.status_code == 409 and "needs to be fixed by your teacher" in r.json()["detail"]
    app_client.post(f"/api/exams/{e['id']}/close", headers=t)
    refused = open_(app_client, t, e)
    assert refused.status_code == 409 and "Fix the link" in refused.json()["detail"]
    assert "Fix the link" in teacher_view(app_client, t, e)["canvas_notice"]


def test_legacy_stored_codes_are_cleared_at_startup(tmp_path, static_dir):
    db = tmp_path / "legacy.db"

    def make():
        s = Settings(database_url=f"sqlite+aiosqlite:///{db}", redis_url=None,
                     secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                     static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                     require_app=False, app_keys=[KEY], dev=False, seed_loadtest_users=0,
                     seed_demo=True, single_process=True)
        return TestClient(create_app(s))

    with make() as c:
        t = login(c, "teacher")
        e = create(c, t, body(canvas={}))
    con = sqlite3.connect(db)
    con.execute("UPDATE exams SET canvas_code_enc = 'old-sealed-code', canvas_code_source = 'managed'")
    con.commit()
    con.close()
    with make():
        pass
    con = sqlite3.connect(db)
    left = con.execute("SELECT count(*) FROM exams WHERE canvas_code_enc IS NOT NULL").fetchone()[0]
    con.close()
    assert left == 0


@pytest.mark.parametrize("bad", ["abc", "v", "0.x", ".", "0..1", "0.7.2-beta", "seven", " 0.7.2 beta"])
def test_malformed_canvas_min_version_stops_startup(monkeypatch, bad):
    monkeypatch.setenv("FAIRPLAY_CANVAS_MIN_VERSION_MAC", bad)
    with pytest.raises(ValueError, match="FAIRPLAY_CANVAS_MIN_VERSION_MAC"):
        Settings()
    monkeypatch.delenv("FAIRPLAY_CANVAS_MIN_VERSION_MAC")
    monkeypatch.setenv("FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS", bad)
    with pytest.raises(ValueError, match="FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS"):
        Settings()


def test_canvas_min_version_env_values(monkeypatch):
    monkeypatch.setenv("FAIRPLAY_CANVAS_MIN_VERSION_MAC", "v0.8.1")
    monkeypatch.setenv("FAIRPLAY_CANVAS_MIN_VERSION_IOS", " 1.0 ")
    s = Settings()
    assert s.canvas_min_version("mac") == "0.8.1" and s.canvas_min_version("ipad") == "1.0"
    assert s.canvas_min_version("ios") == "1.0" and s.canvas_min_version("windows") is None


def test_canvas_min_version_defaults(monkeypatch):
    monkeypatch.delenv("FAIRPLAY_CANVAS_MIN_VERSION_MAC", raising=False)
    monkeypatch.delenv("FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS", raising=False)
    monkeypatch.delenv("FAIRPLAY_CANVAS_MIN_VERSION_IOS", raising=False)
    s = Settings()
    assert s.canvas_min_version("mac") == "0.7.2" and s.canvas_min_version("windows") is None
    assert s.canvas_min_version("ipad") is None and s.canvas_min_version(None) is None
    monkeypatch.setenv("FAIRPLAY_CANVAS_MIN_VERSION_MAC", "")
    assert Settings().canvas_min_version("mac") is None


def test_removed_hosts_round_trip(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"removed_hosts": ["Login.Microsoft.com"]}))
    assert "login.microsoft.com" not in e["allowed_domains"]
    assert "login.microsoftonline.com" in e["allowed_domains"]
    assert e["canvas"]["removed_hosts"] == ["login.microsoft.com"]
    r = app_client.put(f"/api/exams/{e['id']}", headers=t,
                       json=body(allowed_domains=e["allowed_domains"],
                                 canvas={})).json()
    assert r["canvas"]["removed_hosts"] == ["login.microsoft.com"]
    assert "login.microsoft.com" not in r["allowed_domains"]
    r = app_client.put(f"/api/exams/{e['id']}", headers=t,
                       json=body(canvas={"removed_hosts": []})).json()
    assert "login.microsoft.com" in r["allowed_domains"] and r["canvas"]["removed_hosts"] == []


def test_canvas_host_cannot_be_removed(app_client):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", headers=t, json=body(
        canvas={"removed_hosts": ["canvas.test"]}))
    assert r.status_code == 422
    e = create(app_client, t, body(allowed_domains=["docs.python.org"],
                                   canvas={}))
    r = app_client.put(f"/api/exams/{e['id']}", headers=t, json=body(
        canvas={"removed_hosts": ["canvas.test"]}))
    assert r.status_code == 422


def test_policy_blocked_sign_in_host_is_reported(app_client, monkeypatch):
    monkeypatch.setenv("FAIRPLAY_CANVAS_SSO_HOSTS", "sso.example.edu,chatgpt.com")
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    assert "chatgpt.com" not in e["allowed_domains"]
    assert "chatgpt.com" in e["canvas_notice"]
    got = teacher_view(app_client, t, e)
    assert "chatgpt.com" in got["canvas_notice"]


# ---------------------------------------------------------------- restricted browser scope
@pytest.mark.parametrize("path,scope", [
    ("/courses/12/quizzes/34", ("12", "quizzes", "34")),
    ("/courses/12/quizzes/34/take", ("12", "quizzes", "34")),
    ("/courses/12/quizzes/34/", ("12", "quizzes", "34")),
    ("/courses/12/assignments/56", ("12", "assignments", "56")),
    ("/courses/12/quizzes/34?x=1#top", ("12", "quizzes", "34")),
    ("/courses/12/quizzes/34x", None),
    ("/courses/12/quizzes/", None),
    ("/courses/12", None),
    ("/courses/ab/quizzes/34", None),
    ("/courses/12/pages/34", None),
    ("/courses/12/quizzes/34/edit", ("12", "quizzes", "34")),
    ("/courses/12/assignments/9/submissions/3", ("12", "assignments", "9")),
    ("/courses/12/quizzes/34;x=1", None),
    ("/courses/12/quizzes/34/%2e%2e/x", None),
    ("/courses/12/../12/quizzes/34", None),
    ("/courses/12/quizzes/34\\x", None),
    ("/courses/12/quizzes/34 x", None),
    ("/courses/" + "1" * 21 + "/quizzes/34", None),
    ("/courses/12/quizzes/" + "9" * 21, None),
    ("/courses/12/quizzes/" + "9" * 20, ("12", "quizzes", "9" * 20)),
    ("/courses/\u0661\u0662/quizzes/34", None),
    ("/Courses/12/quizzes/34", None),
])
def test_scope_of_link(path, scope):
    got = canvas_mode.scope_of(CANVAS + path)
    if scope is None:
        assert got is None
    else:
        assert got == {"course_id": scope[0], "item_kind": scope[1], "item_id": scope[2]}


def test_saved_exam_without_scope_still_reads_but_cannot_open(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={}))
    assert e["canvas"]["open_book"] is False
    db_exec(app_client, "UPDATE exams SET external_url = ? WHERE id = ?", f"{CANVAS}/somewhere", e["id"])
    v = teacher_view(app_client, t, e)
    assert v["canvas"]["scope"] is None and v["canvas"]["open_book"] is False
    assert open_(app_client, t, e).status_code == 409


def test_scope_follows_link_on_every_save_and_client_cannot_set_it(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"scope": {"course_id": "999", "item_kind": "quizzes",
                                                     "item_id": "999"}}))
    assert e["canvas"]["scope"] == {"course_id": "12", "item_kind": "quizzes", "item_id": "34"}
    other = f"{CANVAS}/courses/77/quizzes/88"
    r = app_client.put(f"/api/exams/{e['id']}", json=body(url=other), headers=t)
    assert r.status_code == 200, r.text
    assert r.json()["canvas"]["scope"] == {"course_id": "77", "item_kind": "quizzes", "item_id": "88"}
    assert teacher_view(app_client, t, e)["canvas"]["scope"]["course_id"] == "77"


def test_open_book_write_and_keep(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"open_book": True}))
    assert e["canvas"]["open_book"] is True
    r = app_client.put(f"/api/exams/{e['id']}", json=body(title="Again"), headers=t)
    assert r.json()["canvas"]["open_book"] is True
    r = app_client.put(f"/api/exams/{e['id']}", json=body(canvas={"quiz_type": "classic"}), headers=t)
    assert r.json()["canvas"]["open_book"] is True
    r = app_client.put(f"/api/exams/{e['id']}", json=body(canvas={"open_book": False}), headers=t)
    assert r.json()["canvas"]["open_book"] is False
    assert teacher_view(app_client, t, e)["canvas"]["open_book"] is False
    listed = [x for x in app_client.get("/api/exams", headers=t).json() if x["id"] == e["id"]][0]
    assert listed["canvas"]["open_book"] is False


def test_open_book_reset_when_leaving_canvas(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"open_book": True}))
    plain = {**body(), "external_kind": None}
    plain.pop("canvas", None)
    r = app_client.put(f"/api/exams/{e['id']}", json=plain, headers=t)
    assert r.status_code == 200 and r.json()["canvas"] is None
    back = app_client.put(f"/api/exams/{e['id']}", json=body(canvas={}),
                          headers=t)
    assert back.json()["canvas"]["open_book"] is False


def test_student_views_carry_scope_and_open_book(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"open_book": True}))
    live = open_(app_client, t, e).json()
    h, a, hs = student_start(app_client, live)
    assert a["canvas_scope"] == {"course_id": "12", "item_kind": "quizzes", "item_id": "34",
                                 "open_book": True, "sign_in_hosts": canvas_mode.sso_hosts(),
                                 "quiz_lti_hosts": []}
    got = app_client.get(f"/api/student/attempts/{a['attempt_id']}", headers=hs).json()
    assert got["canvas_scope"] == a["canvas_scope"]
    assert got["canvas"]["open_book"] is True and got["canvas"]["scope"]["item_id"] == "34"


def test_attempt_scope_lists_the_hosts_in_force(app_client, monkeypatch):
    monkeypatch.setenv("FAIRPLAY_CANVAS_SSO_HOSTS", "login.school.edu, *.duosecurity.com, sso.other.edu")
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"removed_hosts": ["sso.other.edu"]},
                                   allowed_domains=["extra.example.org"]))
    h, a, hs = student_start(app_client, open_(app_client, t, e).json())
    assert a["canvas_scope"]["sign_in_hosts"] == ["login.school.edu", "*.duosecurity.com"]
    assert a["canvas_scope"]["quiz_lti_hosts"] == []
    assert "extra.example.org" not in str(a["canvas_scope"])
    again = app_client.get(f"/api/student/attempts/{a['attempt_id']}", headers=hs).json()
    assert again["canvas_scope"] == a["canvas_scope"]


def test_attempt_scope_lists_new_quiz_hosts(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=NEW_QUIZ, canvas={}))
    h, a, hs = student_start(app_client, open_(app_client, t, e).json())
    assert a["canvas_scope"]["quiz_lti_hosts"] == canvas_mode.quiz_lti_hosts()
    assert set(a["canvas_scope"]) == {"course_id", "item_kind", "item_id", "open_book",
                                      "sign_in_hosts", "quiz_lti_hosts"}


def test_hydrate_then_save_unchanged_keeps_open_book_and_scope(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"open_book": True}))
    got = teacher_view(app_client, t, e)
    again = {**body(canvas=got["canvas"]), "allowed_domains": got["allowed_domains"]}
    r = app_client.put(f"/api/exams/{e['id']}", json=again, headers=t)
    assert r.status_code == 200, r.text
    assert r.json()["canvas"]["open_book"] is True
    assert r.json()["canvas"]["scope"] == {"course_id": "12", "item_kind": "quizzes", "item_id": "34"}


def test_put_without_canvas_block_keeps_scope_and_open_book(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(canvas={"open_book": True}))
    r = app_client.put(f"/api/exams/{e['id']}", json=body(title="Renamed"), headers=t)
    assert r.status_code == 200, r.text
    assert r.json()["canvas"]["scope"] == {"course_id": "12", "item_kind": "quizzes", "item_id": "34"}
    assert r.json()["canvas"]["open_book"] is True


def test_plain_external_attempt_has_no_canvas_scope(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, {"title": "Site", "duration_min": 30, "mode": "external",
                               "external_url": "https://example.com/exam", "questions": []})
    live = open_(app_client, t, e).json()
    h = login(app_client, "alice")
    r = app_client.post(f"/api/student/exams/{e['id']}/start", headers=h,
                        json={"start_code": live["start_code"], "client": proof(app_client, h, e["id"], DEV_A)})
    assert r.status_code == 200, r.text
    assert "canvas_scope" not in r.json()
    assert "canvas_scope" not in app_client.get(f"/api/student/attempts/{r.json()['attempt_id']}", headers=h).json()


# ---------------------------------------------------------------- hardening round
def test_regex_rule_is_fast_on_pathological_patterns():
    started = time.monotonic()
    for rx in (".*" * 150, r"\w+" * 100, "(" * 150):
        with pytest.raises(ValueError):
            canvas_mode.validate_regex(rx)
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("url", [
    f"{CANVAS}/courses/12/quizzes/34\\x", f"{CANVAS}/courses/12/quizzes/34 x",
    f"{CANVAS}/courses/12/quizzes/34%2e", f"{CANVAS}/courses/12/quizzes/34;x=1",
    f"{CANVAS}/courses/12/../12/quizzes/34", 
    "https://teacher@canvas.test/courses/12/quizzes/34", "https://canvas.test./courses/12/quizzes/34",
    "https://canvas.test:443./courses/12/quizzes/34",
    f"{CANVAS}/courses/12/quizzes/34?a=%41",
])
def test_unclean_canvas_link_refused(app_client, url):
    t = login(app_client, "teacher")
    r = app_client.post("/api/exams", json=body(url=url, canvas={}),
                        headers=t)
    assert r.status_code == 422, url
    e = create(app_client, t, body(canvas={}))
    r = app_client.put(f"/api/exams/{e['id']}", json=body(url=url), headers=t)
    assert r.status_code == 422, url


def test_clean_links_with_query_and_fragment_accepted(app_client):
    t = login(app_client, "teacher")
    e = create(app_client, t, body(url=f"{CANVAS}/courses/12/quizzes/34?module_item_id=5#top",
                                   canvas={}))
    assert e["canvas"]["scope"]["item_id"] == "34"


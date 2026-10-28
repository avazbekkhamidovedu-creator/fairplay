import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CHROME = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
SAFARI = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"}
CURL = {"User-Agent": "curl/8.7.1"}
EDGE_WIN = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0"}
MAC_WEB = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) FairPlay/0.7.2"}
IPAD_WEB = {"User-Agent": "Mozilla/5.0 (iPad) AppleWebKit/605.1.15 FairPlay/0.7.2 FairPlay-iPad"}
MAC_NATIVE = {"X-FairPlay-Version": "0.7.2", "X-FairPlay-Platform": "mac"}
IPAD_NATIVE = {"X-FairPlay-Version": "0.7.2", "X-FairPlay-Platform": "ipad"}
WIN_NATIVE = {"User-Agent": ""}

STUDENT_PAGES = ["/student", "/student/", "/student/login", "/student/exam/x", "/student/setup",
                 "/student//login", "/student/../student/login"]
STUDENT_API = [
    ("GET", "/api/student/exams"), ("GET", "/api/student/grades"),
    ("GET", "/api/student/exams/x/package"), ("GET", "/api/student/exams/x/package-key"),
    ("GET", "/api/student/classes"), ("GET", "/api/student/trial"),
    ("POST", "/api/student/attempts/x/heartbeat"), ("PUT", "/api/student/attempts/x/answers"),
    ("POST", "/api/setup/identify"), ("POST", "/api/setup/account"),
]


def make(tmp_path, static_dir, mode):
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'g.db'}", redis_url=None,
                 secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000,
                 static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                 require_app=False, app_keys=["k"], dev=False, seed_loadtest_users=0,
                 seed_demo=True, single_process=True, browser_gate=mode)
    return TestClient(create_app(s))


@pytest.fixture
def gated(tmp_path, static_dir):
    with make(tmp_path, static_dir, "headers") as c:
        yield c


@pytest.fixture
def strict(tmp_path, static_dir):
    with make(tmp_path, static_dir, "strict") as c:
        yield c


def bare(r):
    return r.status_code == 404 and r.json() == {"detail": "Not Found"}


def student_token(c, headers=MAC_NATIVE):
    r = c.post("/api/auth/login", json={"username": "alice", "password": "alice123"}, headers=headers)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("browser", [CHROME, SAFARI, CURL])
def test_browser_sees_nothing_of_the_student_side(gated, browser):
    for path in STUDENT_PAGES:
        assert bare(gated.get(path, headers=browser)), path
    for method, path in STUDENT_API:
        assert bare(gated.request(method, path, headers=browser, json={})), (method, path)
    assert gated.head("/student/login", headers=browser).status_code == 404
    assert gated.request("OPTIONS", "/api/student/exams", headers=browser).status_code == 404


def test_strict_mode_refuses_a_browser_and_an_empty_user_agent(strict):
    for path in STUDENT_PAGES:
        assert bare(strict.get(path, headers=CHROME)), path
        assert bare(strict.get(path, headers={"User-Agent": ""})), path


def test_student_login_in_a_browser_cannot_tell_a_right_password_from_a_wrong_one(gated):
    right = gated.post("/api/auth/login", json={"username": "alice", "password": "alice123"}, headers=CHROME)
    wrong = gated.post("/api/auth/login", json={"username": "alice", "password": "nope"}, headers=CHROME)
    assert right.status_code == wrong.status_code == 401
    assert right.json() == wrong.json()
    assert "token" not in right.text


def test_student_token_is_useless_in_a_browser(gated):
    h = student_token(gated)
    assert bare(gated.get("/api/me", headers={**h, **CHROME}))
    assert bare(gated.get("/api/student/exams", headers={**h, **CHROME}))
    assert bare(gated.get("/api/student/exams", headers={**h, **CHROME, "X-FairPlay-Platform": "bogus",
                                                         "X-FairPlay-Version": "1"}))
    assert gated.get("/api/me", headers={**h, **MAC_NATIVE}).json()["role"] == "student"


@pytest.mark.parametrize("app_headers", [MAC_WEB, IPAD_WEB, MAC_NATIVE, IPAD_NATIVE,
                                         {**CHROME, **MAC_NATIVE}])
def test_apps_pass(gated, strict, app_headers):
    for c in (gated, strict):
        for path in STUDENT_PAGES[:5]:
            r = c.get(path, headers=app_headers)
            assert r.status_code == 200 and "SPA" in r.text, path
        h = student_token(c, app_headers)
        assert c.get("/api/student/exams", headers={**h, **app_headers}).status_code == 200
        assert c.get("/api/me", headers={**h, **app_headers}).status_code == 200
        assert not bare(c.post("/api/setup/identify", headers=app_headers, json={}))


def test_mac_watchdog_with_only_bearer_and_session_passes(gated, strict):
    for c in (gated, strict):
        h = student_token(c)
        r = c.post("/api/student/attempts/x/events", json={"events": []},
                   headers={**h, "X-FairPlay-Session": "s", "User-Agent": "Proctora/0.7 CFNetwork/1494"})
        assert not bare(r)
    assert bare(gated.post("/api/student/attempts/x/events", json={"events": []},
                           headers={**h, **CHROME}))


def test_released_windows_app_still_works_in_headers_mode_but_not_strict(gated, strict):
    for headers in (WIN_NATIVE, EDGE_WIN):
        assert gated.get("/student/login", headers=headers).status_code == 200
        h = student_token(gated, headers)
        assert gated.get("/api/student/exams", headers={**h, **headers}).status_code == 200
        assert bare(strict.get("/student/login", headers=headers))
        assert bare(strict.get("/api/student/exams", headers=headers))
    assert strict.get("/student/login", headers={**EDGE_WIN, "User-Agent": EDGE_WIN["User-Agent"]
                                                  + " FairPlay/0.8.0 FairPlay-Windows"}).status_code == 200


def test_off_mode_serves_everyone(tmp_path, static_dir):
    with make(tmp_path, static_dir, "off") as c:
        assert c.get("/student/login", headers=CHROME).status_code == 200
        assert student_token(c, CHROME)


def test_unknown_mode_falls_back_to_headers(tmp_path, static_dir):
    with make(tmp_path, static_dir, "bogus") as c:
        assert bare(c.get("/student/login", headers=CHROME))


@pytest.mark.parametrize("mode_client", ["gated", "strict"])
def test_teacher_side_is_unchanged(request, mode_client):
    c = request.getfixturevalue(mode_client)
    for path in ("/", "/teacher", "/teacher/login", "/teacher/exams/1"):
        r = c.get(path, headers=CHROME, follow_redirects=False)
        assert r.status_code in (200, 307) and (r.content or r.headers.get("location")), path
    assert c.get("/assets/app.js", headers=CHROME).status_code == 200
    r = c.post("/api/auth/login", json={"username": "teacher", "password": "teacher123"}, headers=CHROME)
    assert r.status_code == 200 and r.json()["role"] == "teacher"
    t = {"Authorization": f"Bearer {r.json()['token']}", **CHROME}
    assert c.get("/api/me", headers=t).json()["role"] == "teacher"
    assert c.get("/api/exams", headers=t).status_code == 200
    assert c.get("/api/classes", headers=t).status_code == 200
    assert c.get("/api/lms/providers", headers=t).status_code == 200
    assert c.get("/api/nope", headers=CHROME).json() == {"detail": "Not Found"}


@pytest.mark.parametrize("mode_client", ["gated", "strict"])
def test_health_version_and_signed_routes_unchanged(request, mode_client):
    c = request.getfixturevalue(mode_client)
    assert c.get("/api/health", headers=CHROME).json()["ok"] is True
    assert "mac" in c.get("/api/app/version", headers=CURL).json()
    for method, path, browser in (("POST", "/api/integrations/dkuoj/v1/exams/x/open", CURL),
                                  ("GET", "/api/integrations/dkuoj/v1/exams/x", CURL),
                                  ("POST", "/api/student/attempts/x/sealed-submit", CHROME)):
        plain = c.request(method, path, headers=browser, json={})
        app = c.request(method, path, headers={**MAC_NATIVE}, json={})
        assert (plain.status_code, plain.content) == (app.status_code, app.content), path


def test_refused_student_login_counts_like_a_wrong_password(gated):
    body = {"username": "alice", "password": "alice123"}
    refused = [gated.post("/api/auth/login", json=body, headers=CHROME).status_code for _ in range(12)]
    wrong = [gated.post("/api/auth/login", json={"username": "bob", "password": "x"},
                        headers=CHROME).status_code for _ in range(12)]
    assert refused == wrong and refused[0] == 401 and 429 in refused
    assert gated.post("/api/auth/login", json=body, headers=MAC_NATIVE).status_code == 429


def test_gated_answer_matches_an_unknown_api_path(gated):
    for path, twin in (("/api/student/exams", "/api/nope"), ("/student/login", "/assets/nope.js")):
        unknown = gated.get(twin, headers=CHROME)
        r = gated.get(path, headers=CHROME)
        assert (r.status_code, r.json()) == (unknown.status_code, unknown.json())
        assert set(r.headers) == set(unknown.headers)
    assert "SPA" not in gated.get("/student/login", headers=CHROME).text
    h = student_token(gated)
    me = gated.get("/api/me", headers={**h, **CHROME})
    assert (me.status_code, me.json()) == (404, {"detail": "Not Found"})


def test_session_header_is_proof_only_for_the_watchdog_event(strict):
    h = {"X-FairPlay-Session": "s", "Authorization": "Bearer x", **CHROME}
    assert not bare(strict.post("/api/student/attempts/a1/events", json={"events": []}, headers=h))
    for method, path in (("GET", "/api/student/exams"), ("POST", "/api/student/attempts/a1/heartbeat"),
                         ("GET", "/api/student/attempts/a1/events"),
                         ("POST", "/api/student/attempts/a1/events/x"),
                         ("GET", "/api/student/attempts/a1/files/1")):
        assert bare(strict.request(method, path, headers=h, json={})), (method, path)
    assert bare(strict.get("/student/login", headers=h))


def test_root_path_is_stripped_before_matching(tmp_path, static_dir):
    with make(tmp_path, static_dir, "headers") as c:
        c.app.root_path = "/fp"
        for path in ("/fp/student/login", "/fp/api/student/exams", "/student/login"):
            assert bare(c.get(path, headers=CHROME)), path


def test_student_chunks_are_assets_not_the_app_page(tmp_path, static_dir):
    chunk = static_dir / "student" / "assets"
    chunk.mkdir(parents=True)
    (chunk / "routes-abc123.js").write_text("export const x = 1;")
    with make(tmp_path, static_dir, "headers") as c:
        ok = c.get("/student/assets/routes-abc123.js", headers=MAC_WEB)
        assert ok.status_code == 200
        assert "immutable" in ok.headers["cache-control"]
        missing = c.get("/student/assets/nope.js", headers=MAC_WEB)
        assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
        assert c.get("/student/assets/routes-abc123.js", headers=CHROME).json() == {"detail": "Not Found"}


def test_health_flags_browser_demo_only_with_the_gate_off(tmp_path, static_dir):
    for mode in ("headers", "strict", "off"):
        (tmp_path / mode).mkdir()
    for mode in ("headers", "strict"):
        with make(tmp_path / mode, static_dir, mode) as c:
            assert "browser_demo" not in c.get("/api/health", headers=CHROME).json()
    with make(tmp_path / "off", static_dir, "off") as c:
        assert c.get("/api/health", headers=CHROME).json()["browser_demo"] is True

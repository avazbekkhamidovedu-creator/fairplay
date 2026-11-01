import hashlib
import hmac
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import code_runner
from app.config import Settings
from app.main import create_app

from .conftest import SAMPLE_EXAM, login
from .test_code_questions import HIDDEN_IN, HIDDEN_OUT, create, exam_body, open_and_start

SECRET = "s" * 40
TENANT = "school-1"
SRC_SECRET = "SECRET-SOURCE-TEXT-xyz"

TESTS = [
    {"input": "3\n", "expected": "6\n", "visible": True, "points": 2},
    {"input": HIDDEN_IN, "expected": HIDDEN_OUT, "visible": False, "points": 8},
]


class FakeRunner:
    """Verifies the signature like the real runner. Source "DOUBLE" prints 2*int(stdin),
    "HIDDEN" prints the hidden answer for the hidden input, "COMPILE" fails to compile,
    "SLOW" times out, "BUSY" answers 503 once."""

    def __init__(self, languages=("c", "cpp", "java")):
        self.languages = list(languages)
        self.calls = []
        self.lang_calls = 0
        self.down = False
        self.busy_left = 0
        self.seen_nonces = set()
        self.bodies = []

    def _auth(self, request: httpx.Request) -> bool:
        h = request.headers
        ts, nonce, sig = h.get("x-fp-ts"), h.get("x-fp-nonce"), h.get("x-fp-sig")
        if h.get("x-fp-tenant") != TENANT or not ts or not nonce or not sig:
            return False
        if abs(time.time() - int(ts)) > 60 or not (16 <= len(nonce) <= 64) or nonce in self.seen_nonces:
            return False
        want = hmac.new(SECRET.encode(), f"{ts}.{nonce}.{hashlib.sha256(request.content).hexdigest()}".encode(),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig):
            return False
        self.seen_nonces.add(nonce)
        return True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        path = request.url.path
        if path == "/v1/health":
            return httpx.Response(200, json={"ok": True, "languages": self.languages, "version": "t"})
        if not self._auth(request):
            return httpx.Response(401, json={"detail": "auth"})
        if path == "/v1/languages":
            self.lang_calls += 1
            return httpx.Response(200, json={"languages": [{"key": k, "label": k} for k in self.languages]})
        if path == "/v1/run":
            if self.busy_left:
                self.busy_left -= 1
                return httpx.Response(503, headers={"Retry-After": "0"})
            body = json.loads(request.content)
            self.bodies.append(body)
            self.calls.append(len(body["stdins"]))
            src = body["source"]
            if src.startswith("COMPILE"):
                return httpx.Response(200, json={"status": "compile_error", "compile_output": "error: x",
                                                 "runtime_version": "v", "results": []})
            res = []
            for s in body["stdins"]:
                if not s.strip().isdigit() and not (src.startswith("HIDDEN") and s == HIDDEN_IN):
                    res.append({"status": "runtime_error", "stdout": "", "stderr": "boom", "time_ms": 1,
                                "exit_code": 1, "truncated": False})
                    continue
                if src.startswith("SLOW"):
                    res.append({"status": "time_limit", "stdout": "", "stderr": "", "time_ms": body["time_limit_ms"],
                                "exit_code": None, "truncated": False})
                    continue
                if src.startswith("HIDDEN"):
                    out = HIDDEN_OUT + "  \r\n\r\n" if s == HIDDEN_IN else str(2 * int(s.strip())) + "  \n"
                else:
                    out = str(2 * int(s.strip())) + "\n"
                res.append({"status": "ok", "stdout": out + "x" * 3000 if src.startswith("LONG") else out,
                            "stderr": "", "time_ms": 5, "exit_code": 0, "truncated": False})
            return httpx.Response(200, json={"status": "ok", "compile_output": "", "runtime_version": "v",
                                             "results": res})
        return httpx.Response(404)


def make_settings(tmp_path, static_dir, **kw):
    return Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}", redis_url=None,
                    secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000, require_app=False,
                    app_keys=["test-app-key"], dev=False, seed_loadtest_users=0, seed_demo=True,
                    single_process=True, static_dir=str(static_dir), uploads_dir=str(tmp_path / "uploads"), **kw)


RUNNER_KW = dict(runner_url="https://runner.test", runner_tenant=TENANT, runner_secret=SECRET)


@pytest.fixture
def fake():
    return FakeRunner()


@pytest.fixture
def client(tmp_path, static_dir, fake, monkeypatch):
    monkeypatch.setattr(code_runner, "TRANSPORT", httpx.MockTransport(fake))
    with TestClient(create_app(make_settings(tmp_path, static_dir, **RUNNER_KW))) as c:
        yield c


@pytest.fixture
def rc(client):
    return client


def code_q(**kw):
    return {"type": "code", "prompt": "Double it.", "points": 10, "language": "c", "tests": TESTS, **kw}


def qid_of(exam):
    return next(q["id"] for q in exam["questions"] if q["type"] == "code")


def started(client, teacher, alice, **kw):
    e, a = open_and_start(client, teacher, alice, exam_body(code_q(**kw)))
    return e, a, f"/api/student/attempts/{a['attempt_id']}/questions/{qid_of(e)}/run"


def clear_gap(client, aid):
    from app.store import get_store
    client.portal.call(get_store().delete, f"coderun3:{aid}")


# ---------------------------------------------------------------- unit
def test_signature_construction():
    body = b'{"a":1}'
    want = hmac.new(b"k" * 32, f"1700000000.abcdefghijklmnop.{hashlib.sha256(body).hexdigest()}".encode(),
                    hashlib.sha256).hexdigest()
    assert code_runner.sign("k" * 32, 1700000000, "abcdefghijklmnop", body) == want


def test_compare_rule_matches_the_web():
    m = code_runner.outputs_match
    assert m("a  \r\nb\t\n\n\n", "a\nb")
    assert m("", "\n")
    assert not m("a\n\nb", "a\nb")
    assert not m("A", "a")
    assert not m("a b", "a  b")
    assert code_runner.normalize_output("x \ry") == "x\ny"


def test_half_configured_settings_refuse_to_start(tmp_path, static_dir):
    for kw in ({"runner_url": "https://r.test"}, {"runner_tenant": "t"}, {"runner_secret": SECRET},
               {"runner_url": "https://r.test", "runner_tenant": "t"}):
        with pytest.raises(RuntimeError, match="half configured"):
            with TestClient(create_app(make_settings(tmp_path, static_dir, **kw))):
                pass


@pytest.mark.parametrize("kw,msg", [
    ({"runner_url": "http://runner.test"}, "https"),
    ({"runner_secret": "short"}, "32"),
])
def test_bad_runner_settings_refuse_to_start(tmp_path, static_dir, kw, msg):
    with pytest.raises(RuntimeError, match=msg):
        with TestClient(create_app(make_settings(tmp_path, static_dir, **{**RUNNER_KW, **kw}))):
            pass


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://127.0.0.1", "http://localhost:8080",
                                 "http://[::1]:8080", "https://runner.test"])
@pytest.mark.parametrize("dev", [False, True])
def test_runner_url_accepted(tmp_path, static_dir, url, dev):
    s = make_settings(tmp_path, static_dir, **{**RUNNER_KW, "runner_url": url})
    s.dev = dev
    s.check_runner()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1@evil.com", "http://127.0.0.1:80@evil.com", "http://127.0.0.1.evil.com",
    "http://localhost.evil.com", "http://127.0.0.2", "http://127.evil.com", "http://localhost@evil.com",
    "http://", "http:///x", "http://runner.test", "http://10.0.0.5:8080", "ftp://127.0.0.1",
])
@pytest.mark.parametrize("dev", [False, True])
def test_runner_url_rejected(tmp_path, static_dir, url, dev):
    s = make_settings(tmp_path, static_dir, **{**RUNNER_KW, "runner_url": url})
    s.dev = dev
    with pytest.raises(RuntimeError):
        s.check_runner()


# ---------------------------------------------------------------- health
@pytest.mark.no_default_class
def test_health_off_by_default(tmp_path, static_dir):
    with TestClient(create_app(make_settings(tmp_path, static_dir))) as c:
        assert c.get("/api/health").json()["code_runner"] is None


def test_health_on_lists_languages_not_the_url(rc):
    h = rc.get("/api/health").json()
    assert h["code_runner"] == {"languages": ["c", "cpp", "java"]}
    assert "runner.test" not in json.dumps(h) and SECRET not in json.dumps(h)


def test_health_null_when_runner_answers_nothing(rc, fake):
    fake.languages = []
    assert rc.get("/api/health").json()["code_runner"] is None


def test_languages_cached_and_failures_cached_shorter(rc, fake, monkeypatch):
    rc.get("/api/health"); rc.get("/api/health"); rc.get("/api/health")
    assert fake.lang_calls == 1
    r = code_runner.get_runner()
    r._langs = (time.monotonic() - 1, ["c"])
    rc.get("/api/health")
    assert fake.lang_calls == 2
    fake.down = True
    r._langs = None
    assert rc.get("/api/health").json()["code_runner"] is None
    fake.down = False
    assert rc.get("/api/health").json()["code_runner"] is None       # failure still cached
    ttl = r._langs[0] - time.monotonic()
    assert 0 < ttl <= code_runner.LANGUAGES_FAIL_TTL_S
    r._langs = (time.monotonic() - 1, [])
    assert rc.get("/api/health").json()["code_runner"] == {"languages": ["c", "cpp", "java"]}


def test_retry_once_on_503_then_gives_up(rc, fake, teacher, alice):
    _, a, url = started(rc, teacher, alice)
    fake.busy_left = 1
    assert rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice).status_code == 200
    clear_gap(rc, a["attempt_id"])
    fake.busy_left = 2
    r = rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice)
    assert r.status_code == 503 and r.json()["detail"] == "The code runner is not available right now"


# ---------------------------------------------------------------- student route
def test_student_run_ok(rc, fake, teacher, alice):
    e, a, url = started(rc, teacher, alice)
    r = rc.post(url, json={"source": "DOUBLE", "stdins": ["3\n", "4\n"]}, headers=alice)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"status", "compile_output", "results"} and j["status"] == "ok"
    assert [x["stdout"] for x in j["results"]] == ["6\n", "8\n"]
    assert set(j["results"][0]) == {"status", "stdout", "stderr", "time_ms", "truncated"}
    body = fake.bodies[-1]
    assert body["language"] == "c" and body["time_limit_ms"] == 2000 and body["memory_limit_kb"] == 262144
    assert HIDDEN_IN not in json.dumps(fake.bodies) and HIDDEN_OUT not in json.dumps(fake.bodies)
    assert HIDDEN_IN not in r.text and HIDDEN_OUT not in r.text


def test_student_run_uses_question_limits_and_exam_default(rc, fake, teacher, alice):
    e, a = open_and_start(rc, teacher, alice, exam_body(
        {"type": "code", "prompt": "p", "points": 1, "time_limit_ms": 500}, default_language="java"))
    url = f"/api/student/attempts/{a['attempt_id']}/questions/{qid_of(e)}/run"
    assert rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice).status_code == 200
    assert fake.bodies[-1]["language"] == "java" and fake.bodies[-1]["time_limit_ms"] == 500


def test_student_run_compile_error_and_time_limit(rc, teacher, alice):
    _, a, url = started(rc, teacher, alice)
    j = rc.post(url, json={"source": "COMPILE", "stdins": ["1"]}, headers=alice).json()
    assert j["status"] == "compile_error" and j["compile_output"] == "error: x" and j["results"] == []
    clear_gap(rc, a["attempt_id"])
    j = rc.post(url, json={"source": "SLOW", "stdins": ["1"]}, headers=alice).json()
    assert j["results"][0]["status"] == "time_limit"


def test_student_run_records_event_without_source(rc, teacher, alice):
    e, a, url = started(rc, teacher, alice)
    rc.post(url, json={"source": SRC_SECRET + " DOUBLE", "stdins": ["1"]}, headers=alice)
    ev = rc.get(f"/api/exams/{e['id']}/attempts/{a['attempt_id']}/events", headers=teacher)
    assert ev.status_code == 200
    assert "code_run" in ev.text and "c ok x1" in ev.text and SRC_SECRET not in ev.text


def test_language_not_offered_or_not_remote(rc, fake, teacher, alice):
    fake.languages = ["c"]
    for lang in ("java", "python"):
        e, a = open_and_start(rc, teacher, alice, exam_body(code_q(language=lang)))
        url = f"/api/student/attempts/{a['attempt_id']}/questions/{qid_of(e)}/run"
        r = rc.post(url, json={"source": "x", "stdins": ["1"]}, headers=alice)
        assert r.status_code == 400 and r.json()["detail"] == "This language cannot be run here"
    assert fake.calls == []


@pytest.mark.no_default_class
def test_runner_off_behaves_as_before(tmp_path, static_dir):
    from .conftest import make_class
    with TestClient(create_app(make_settings(tmp_path, static_dir))) as c:
        t, al = login(c, "teacher"), login(c, "alice")
        k = make_class(c, t)
        e, a = open_and_start(c, t, al, exam_body(code_q(), class_id=k["id"]))
        url = f"/api/student/attempts/{a['attempt_id']}/questions/{qid_of(e)}/run"
        r = c.post(url, json={"source": "x", "stdins": ["1"]}, headers=al)
        assert r.status_code == 400 and r.json()["detail"] == "This language cannot be run here"
        assert c.get("/api/health").json()["code_runner"] is None


def test_non_code_and_unknown_question(rc, teacher, alice):
    e, a, url = started(rc, teacher, alice)
    mcq = next(q["id"] for q in e["questions"] if q["type"] == "mcq")
    base = f"/api/student/attempts/{a['attempt_id']}/questions/"
    assert rc.post(base + mcq + "/run", json={"source": "x", "stdins": ["1"]}, headers=alice).status_code == 400
    assert rc.post(base + "nope/run", json={"source": "x", "stdins": ["1"]}, headers=alice).status_code == 404


def test_other_students_attempt_is_refused(rc, teacher, alice, bob):
    _, a, url = started(rc, teacher, alice)
    r = rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=bob)
    assert r.status_code == 404
    assert rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=teacher).status_code == 403
    assert rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}).status_code in (401, 403)


def test_not_in_progress_refused(rc, fake, teacher, alice):
    e, a, url = started(rc, teacher, alice)
    assert rc.post(f"/api/student/attempts/{a['attempt_id']}/submit", json={"answers": {}, "client_seq": 5},
                   headers=alice).status_code == 200
    r = rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice)
    assert r.status_code == 409 and fake.bodies == []


def test_after_deadline_refused(rc, teacher, alice):
    from sqlalchemy import update
    from app.db import get_database
    from app.models import Attempt
    from app.timeutil import utcnow
    from datetime import timedelta
    e, a, url = started(rc, teacher, alice)

    async def go():
        async with get_database().sessionmaker() as s:
            await s.execute(update(Attempt).where(Attempt.id == a["attempt_id"]).values(deadline_at=utcnow() - timedelta(minutes=5)))
            await s.commit()
    rc.portal.call(go)
    assert rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice).status_code == 409


def test_rate_limit_gap_and_hourly(rc, teacher, alice):
    _, a, url = started(rc, teacher, alice)
    ok = {"source": "DOUBLE", "stdins": ["1"]}
    assert rc.post(url, json=ok, headers=alice).status_code == 200
    r = rc.post(url, json=ok, headers=alice)
    assert r.status_code == 429 and r.headers["Retry-After"] == "3"
    for _ in range(code_runner.RUNS_PER_HOUR - 1):
        clear_gap(rc, a["attempt_id"])
        assert rc.post(url, json=ok, headers=alice).status_code == 200
    clear_gap(rc, a["attempt_id"])
    r = rc.post(url, json=ok, headers=alice)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 3


def test_oversize_refused(rc, fake, teacher, alice):
    _, a, url = started(rc, teacher, alice)
    for body in ({"source": "x" * 50_001, "stdins": ["1"]}, {"source": "x", "stdins": []},
                 {"source": "x", "stdins": ["1"] * 21}, {"source": "x", "stdins": ["y" * 20_001]}):
        assert rc.post(url, json=body, headers=alice).status_code in (413, 422)
    assert rc.post(url, json={"source": "DOUBLE", "stdins": ["1"] * 20}, headers=alice).status_code == 200
    assert fake.calls == [20]


def test_runner_down_is_503(rc, fake, teacher, alice):
    _, a, url = started(rc, teacher, alice)
    fake.down = True
    code_runner.get_runner()._langs = None
    r = rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice)
    assert r.status_code == 503 and r.json()["detail"] == "The code runner is not available right now"


def test_runner_down_mid_run_is_503_message(rc, fake, teacher, alice):
    _, a, url = started(rc, teacher, alice)
    rc.get("/api/health")                      # languages cached
    fake.down = True
    r = rc.post(url, json={"source": "DOUBLE", "stdins": ["1"]}, headers=alice)
    assert r.status_code == 503 and r.json()["detail"] == "The code runner is not available right now"


# ---------------------------------------------------------------- teacher route
def teacher_url(e):
    return f"/api/exams/{e['id']}/questions/{qid_of(e)}/run-tests"


def test_teacher_scores_all_tests_with_hidden(rc, fake, teacher):
    e = create(rc, teacher, exam_body(code_q()))
    r = rc.post(teacher_url(e), json={"source": "HIDDEN"}, headers=teacher)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["earned"] == 10 and j["total"] == 10 and j["status"] == "ok"
    assert [t["index"] for t in j["tests"]] == [0, 1]
    assert [t["visible"] for t in j["tests"]] == [True, False]
    assert all(t["passed"] for t in j["tests"])
    assert fake.calls == [2]
    assert HIDDEN_OUT not in json.dumps(fake.bodies)


def test_teacher_partial_and_compare_rule(rc, teacher):
    e = create(rc, teacher, exam_body(code_q()))
    j = rc.post(teacher_url(e), json={"source": "DOUBLE"}, headers=teacher).json()
    assert [t["passed"] for t in j["tests"]] == [True, False] and j["earned"] == 2


def test_teacher_compile_error_and_time_limit(rc, teacher):
    e = create(rc, teacher, exam_body(code_q()))
    j = rc.post(teacher_url(e), json={"source": "COMPILE"}, headers=teacher).json()
    assert j["status"] == "compile_error" and j["earned"] == 0 and j["compile_output"] == "error: x"
    j = rc.post(teacher_url(e), json={"source": "SLOW"}, headers=teacher).json()
    assert j["tests"][0]["status"] == "time_limit" and not j["tests"][0]["passed"]


def test_teacher_output_truncated(rc, teacher):
    e = create(rc, teacher, exam_body(code_q()))
    j = rc.post(teacher_url(e), json={"source": "LONG"}, headers=teacher).json()
    assert len(j["tests"][0]["stdout"]) == 2000


def test_teacher_points_shared_when_tests_have_none(rc, teacher):
    tests = [{"input": "1", "expected": "2", "visible": True}, {"input": "2", "expected": "5", "visible": True}]
    e = create(rc, teacher, exam_body(code_q(tests=tests)))
    j = rc.post(teacher_url(e), json={"source": "DOUBLE"}, headers=teacher).json()
    assert j["earned"] == 5 and j["total"] == 10


def test_teacher_batches_over_30(rc, fake, teacher):
    tests = [{"input": str(i), "expected": str(2 * i), "visible": i < 3, "points": 0} for i in range(45)]
    e = create(rc, teacher, exam_body(code_q(tests=tests, points=45)))
    j = rc.post(teacher_url(e), json={"source": "DOUBLE"}, headers=teacher).json()
    assert fake.calls == [30, 15] and len(j["tests"]) == 45 and j["earned"] == 45


def test_teacher_other_teacher_refused(rc, teacher):
    e = create(rc, teacher, exam_body(code_q()))
    from app.db import get_database
    from app.models import User
    from app.security import hash_password

    async def go():
        async with get_database().sessionmaker() as s:
            s.add(User(username="teacher2", name="T2", role="teacher", password_hash=hash_password("teacher2pass", 1000)))
            await s.commit()
    try:
        rc.portal.call(go)
        other = login(rc, "teacher2", "teacher2pass")
    except Exception:
        pytest.skip("cannot create a second teacher")
    assert rc.post(teacher_url(e), json={"source": "DOUBLE"}, headers=other).status_code == 404


def test_teacher_route_refuses_students_and_non_remote(rc, teacher, alice):
    e = create(rc, teacher, exam_body(code_q()))
    assert rc.post(teacher_url(e), json={"source": "x"}, headers=alice).status_code == 403
    e2 = create(rc, teacher, exam_body(code_q(language="python")))
    r = rc.post(teacher_url(e2), json={"source": "x"}, headers=teacher)
    assert r.status_code == 400 and r.json()["detail"] == "This language cannot be run here"


def test_teacher_runner_down_503(rc, fake, teacher):
    e = create(rc, teacher, exam_body(code_q()))
    rc.get("/api/health")
    fake.down = True
    assert rc.post(teacher_url(e), json={"source": "x"}, headers=teacher).status_code == 503


# ---------------------------------------------------------------- logs
def test_no_secret_source_or_output_in_logs(rc, fake, teacher, alice, caplog):
    caplog.set_level(logging.DEBUG)
    _, a, url = started(rc, teacher, alice)
    rc.post(url, json={"source": SRC_SECRET + " DOUBLE", "stdins": ["7"]}, headers=alice)
    fake.down = True
    clear_gap(rc, a["attempt_id"])
    rc.post(url, json={"source": SRC_SECRET + " DOUBLE", "stdins": ["7"]}, headers=alice)
    text = caplog.text
    assert SECRET not in text and SRC_SECRET not in text and "runner.test" not in text

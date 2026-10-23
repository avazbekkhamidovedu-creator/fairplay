"""perf 0.6.2: static caching/compression, batched store calls, small exam facts cache.

Behaviour must not change: these check that the faster paths give the same answers."""
import asyncio
import gzip
import os

import pytest

from app.store import MemoryStore

from .test_redis_store import _redis_store


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- store batch / mget
@pytest.fixture(params=["memory", "redis"])
def store(request):
    pytest.importorskip("fakeredis")
    return MemoryStore() if request.param == "memory" else _redis_store()


def test_batch_matches_single_calls(store):
    async def go():
        await store.set("a", "1", 60)
        res = await store.batch([("get", "a"), ("get", "missing"), ("incr", "n", 60), ("incr", "n", 600),
                                 ("set", "b", "x", 30), ("hset", "h", "f", "v", 60)])
        return res, await store.get("b"), await store.hgetall("h"), await store.ttl("n"), await store.ttl("b")
    res, b, h, ttl_n, ttl_b = run(go())
    assert res == ["1", None, 1, 2, None, None]
    assert b == "x" and h == {"f": "v"}
    assert 0 < ttl_n <= 60 and 0 < ttl_b <= 30          # incr keeps its first TTL


def test_mget(store):
    async def go():
        await store.set("k1", "v1", 60)
        return await store.mget("k1", "k2"), await store.mget()
    assert run(go()) == (["v1", None], [])


def test_batch_rejects_unknown_ops(store):
    with pytest.raises(ValueError):
        run(store.batch([("del", "x")]))


# ---------------------------------------------------------------- static files
@pytest.fixture
def big_static(static_dir):
    js = "console.log('hello fairplay');\n" * 4000         # ~120 KB, compresses well
    (static_dir / "assets" / "index-abc123.js").write_text(js)
    (static_dir / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'>" + " " * 2000 + "</svg>")
    (static_dir / "pyodide").mkdir()
    (static_dir / "pyodide" / "pyodide.asm.wasm").write_bytes(b"\0asm" + b"\0" * 5000)
    (static_dir / "pyodide" / "pyodide.mjs").write_text("export const x = 1;\n" * 100)
    return static_dir, js


def test_hashed_assets_are_immutable_and_gzipped(tmp_path, big_static):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from .conftest import Settings
    static_dir, js = big_static
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}", secret="test-secret-0123456789abcdef",
                 pbkdf2_iterations=1000, require_app=False, app_keys=["k"], dev=False, seed_loadtest_users=0,
                 single_process=True, static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"))
    with TestClient(create_app(s)) as c:
        r = c.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.text == js            # the client decodes it
        assert r.headers["content-encoding"] == "gzip"
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["vary"] == "Accept-Encoding"
        assert r.headers["content-type"].startswith("text/javascript")
        assert int(r.headers["content-length"]) < len(js) / 5    # sent compressed
        etag = r.headers["etag"]
        # no gzip asked: the plain file, its own ETag
        r2 = c.get("/assets/index-abc123.js", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in r2.headers and r2.text == js and r2.headers["etag"] != etag
        # revalidation with either ETag: 304, no body
        for tag in (etag, r2.headers["etag"]):
            r3 = c.get("/assets/index-abc123.js", headers={"If-None-Match": tag, "Accept-Encoding": "gzip"})
            assert r3.status_code == 304 and r3.content == b""
            assert r3.headers["cache-control"] == "public, max-age=31536000, immutable"
        r4 = c.get("/assets/index-abc123.js", headers={"If-Modified-Since": r.headers["last-modified"]})
        assert r4.status_code == 304
        assert c.get("/assets/index-abc123.js", headers={"If-None-Match": '"other"'}).status_code == 200
        # security headers still on (GuardMiddleware)
        assert r.headers["x-content-type-options"] == "nosniff"


def test_index_is_revalidated_and_other_files_cached_an_hour(tmp_path, big_static, client):
    r = client.get("/student/exam/a_1")
    assert r.status_code == 200 and "<title>SPA</title>" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert "content-security-policy" in r.headers
    r2 = client.get("/student", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
    assert client.get("/favicon.svg").headers["cache-control"] == "public, max-age=3600"
    w = client.get("/pyodide/pyodide.asm.wasm", headers={"Accept-Encoding": "gzip"})
    assert w.headers["content-type"] == "application/wasm" and w.content.startswith(b"\0asm")
    m = client.get("/pyodide/pyodide.mjs")
    assert m.headers["content-type"].startswith("text/javascript")
    # 0.6.2 review: a missing script or pack file is a 404, never the page (and never cached)
    miss = client.get("/assets/missing-zzz.js")
    assert miss.status_code == 404 and "SPA" not in miss.text
    assert client.get("/app-assets/v/x/pyodide/nope.mjs").status_code == 404
    # unversioned pack paths (older pages) are revalidated, not kept for an hour
    assert m.headers["cache-control"] == "no-cache"


def test_precompressed_sidecar_is_used(tmp_path, big_static, client):
    static_dir, js = big_static
    p = static_dir / "assets" / "index-abc123.js"
    side = static_dir / "assets" / "index-abc123.js.gz"
    side.write_bytes(gzip.compress(js.encode(), mtime=0))
    os.utime(side, (os.stat(p).st_mtime + 5, os.stat(p).st_mtime + 5))
    r = client.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.text == js
    assert int(r.headers["content-length"]) == side.stat().st_size


def test_static_path_traversal_still_refused(client):
    r = client.get("/assets/..%2F..%2F..%2Fetc%2Fpasswd")
    assert "root:" not in r.text


# ---------------------------------------------------------------- exam facts cache
def test_exam_hot_facts_follow_the_exam_cache():
    from app.services import exam_hot_key, exam_key, get_exam_hot, invalidate_exam

    class FakeSession:
        async def get(self, *_):
            raise AssertionError("no DB read expected")

    async def go():
        st = MemoryStore()
        import json
        await st.set(exam_key("e1"), json.dumps({"question_ids": ["q1", "q2"], "require_strong_lock": True}), 120)
        hot = await get_exam_hot(st, FakeSession(), "e1")
        cached = await st.get(exam_hot_key("e1"))
        ttl = await st.ttl(exam_hot_key("e1"))
        await invalidate_exam(st, "e1")
        gone = await st.get(exam_hot_key("e1")), await st.get(exam_key("e1"))
        return hot, cached, ttl, gone
    hot, cached, ttl, gone = run(go())
    assert hot == {"question_ids": ["q1", "q2"], "require_strong_lock": True}
    assert cached is not None and 0 < ttl <= 120
    assert gone == (None, None)


# ---------------------------------------------------------------- school NAT sign-ins
def test_school_network_gets_its_own_ip_limit():
    from app.config import Settings
    s = Settings(login_ip_limit=300, login_school_nets=["203.0.113.0/24", "2001:db8::/48", "bogus"],
                 login_school_ip_limit=6000)
    assert s.login_limit_for("203.0.113.77") == 6000
    assert s.login_limit_for("2001:db8::5") == 6000
    assert s.login_limit_for("198.51.100.1") == 300
    assert s.login_limit_for("unknown") == 300 and s.login_limit_for("") == 300
    assert Settings(login_ip_limit=300).login_limit_for("203.0.113.77") == 300


def test_school_network_login_limit_applies(tmp_path, static_dir):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from .conftest import Settings, login
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}", secret="test-secret-0123456789abcdef",
                 pbkdf2_iterations=1000, require_app=False, app_keys=["k"], dev=False, seed_loadtest_users=0,
                 seed_demo=True, single_process=True, static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                 login_ip_limit=2, login_school_nets=["testclient/32", "127.0.0.0/8"], login_school_ip_limit=5,
                 login_hash_slots=1)
    with TestClient(create_app(s)) as c:     # TestClient's address is "testclient": not a school net
        login(c, "alice"), login(c, "bob")
        assert c.post("/api/auth/login", json={"username": "carol", "password": "carol123"}).status_code == 429


# ---------------------------------------------------------------- route order
def test_student_routes_overlap_no_other_route(client):
    """main.py includes the student router first (its hot paths are matched first); that is
    only safe while none of its paths could also match another route."""
    import re
    from starlette.routing import compile_path
    from app.routes_student import router as student_router
    mine = {(r.path, m) for r in student_router.routes for m in r.methods}
    others = [(p, m.upper()) for p, ops in client.app.openapi()["paths"].items() for m in ops
              if (p, m.upper()) not in mine]
    assert len(others) > 50
    for path, method in mine:
        for val in ("x1", "exams", "attempts", "trial", "classes", "grades", "materials"):
            sample = re.sub(r"\{[^}]+\}", val, path)
            for p2, m2 in others:
                assert not (m2 == method and compile_path(p2)[0].match(sample)), (path, p2)
        # and no other route could grab a student path either way round
        rx = compile_path(path)[0]
        for p2, m2 in others:
            assert not (m2 == method and rx.match(re.sub(r"\{[^}]+\}", "x1", p2))), (path, p2)


# ---------------------------------------------------------------- sign-in hashing priority
def test_password_checks_run_in_low_priority_threads():
    import sys
    import threading
    from app.security import HASH_THREAD_NICE, hash_executor, hash_password, verify_password_async
    stored = hash_password("pw", 1000)
    assert run(verify_password_async("pw", stored)) is True
    assert run(verify_password_async("nope", stored)) is False
    if sys.platform.startswith("linux"):
        base = os.getpriority(os.PRIO_PROCESS, 0)
        nice = hash_executor().submit(lambda: os.getpriority(os.PRIO_PROCESS, threading.get_native_id())).result()
        assert nice == max(base, HASH_THREAD_NICE)
        assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) == base   # the caller is untouched


def test_redis_store_waits_for_a_free_connection():
    """perf 0.6.2: more requests in flight than pooled connections wait, never fail."""
    from app.store import RedisStore
    import redis.asyncio as aioredis
    s = RedisStore("redis://127.0.0.1:1/0", max_connections=3, wait_s=7)
    pool = s.r.connection_pool
    assert isinstance(pool, aioredis.BlockingConnectionPool)
    assert pool.max_connections == 3 and pool.timeout == 7
    run(s.close())


# ---------------------------------------------------------------- admission limit
def test_admission_limit_queues_instead_of_failing():
    from app.admission import AdmissionLimit

    async def go():
        active = peak = 0
        done = []

        async def app(scope, receive, send):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            done.append(scope["path"])

        gate = AdmissionLimit(app, limit=3)
        paths = [f"/api/student/attempts/a{i}/heartbeat" for i in range(20)]
        await asyncio.gather(*(gate({"type": "http", "path": p}, None, None) for p in paths))
        # not counted: sign-in, uploads, the web app's files
        free_peak = peak
        peak = 0
        await asyncio.gather(*(gate({"type": "http", "path": p}, None, None)
                               for p in ["/api/auth/login"] * 6 + ["/assets/x.js"] * 6))
        return free_peak, peak, len(done)
    limited_peak, exempt_peak, n = run(go())
    assert limited_peak == 3 and exempt_peak == 12 and n == 32


# ---------------------------------------------------------------- 0.6.2 review fixes
def test_pack_is_served_under_its_version(tmp_path, static_dir, client):
    """Finding 1: /app-assets/v/<version>/ is immutable for the pack on disk; any other
    version (or no version) is revalidated, so an upgrade never mixes old and new files."""
    import json
    pack = static_dir / "app-assets"
    (pack / "pdfjs").mkdir(parents=True)
    (pack / "pdfjs" / "pdf.worker.min.mjs").write_text("export const worker = 1;\n" * 200)
    (pack / "manifest.json").write_text(json.dumps({"version": "abc123", "files": {"pdfjs/pdf.worker.min.mjs": "00"}}))
    r = client.get("/app-assets/v/abc123/pdfjs/pdf.worker.min.mjs")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["content-type"].startswith("text/javascript") and "worker" in r.text
    old = client.get("/app-assets/v/0ld/pdfjs/pdf.worker.min.mjs")
    assert old.status_code == 200 and old.headers["cache-control"] == "no-cache"
    plain = client.get("/app-assets/pdfjs/pdf.worker.min.mjs")
    assert plain.status_code == 200 and plain.headers["cache-control"] == "no-cache"
    # a new pack on disk: the old version's URL is no longer immutable
    (pack / "manifest.json").write_text(json.dumps({"version": "def456", "files": {}}))
    os.utime(pack / "manifest.json", (os.stat(pack / "manifest.json").st_mtime + 5,) * 2)
    assert client.get("/app-assets/v/abc123/pdfjs/pdf.worker.min.mjs").headers["cache-control"] == "no-cache"
    assert "root:" not in client.get("/app-assets/v/abc123/..%2F..%2F..%2Fetc%2Fpasswd").text
    # second review: no climbing out of app-assets/ (index.html would be cached as immutable)
    (pack / "manifest.json").write_text(json.dumps({"version": "abc123", "files": {}}))
    os.utime(pack / "manifest.json", (os.stat(pack / "manifest.json").st_mtime + 10,) * 2)
    for bad in ("/app-assets/v/abc123/%2e%2e/index.html", "/app-assets/v/abc123/pdfjs/%2e%2e/%2e%2e/index.html",
                "/app-assets/v/%2e%2e/index.html"):
        r = client.get(bad)
        assert r.status_code == 404 and "immutable" not in r.headers.get("cache-control", ""), bad


def test_arrival_time_is_recorded_before_the_line():
    """Finding 2: the time a request arrived, before it waited for a slot."""
    import time as _time
    from app.admission import AdmissionLimit
    from app.timeutil import arrived_at

    class Req:
        def __init__(self, scope):
            self.scope = scope

    async def go():
        seen = {}

        async def app(scope, receive, send):
            await asyncio.sleep(0.2)
            seen[scope["path"]] = (arrived_at(Req(scope)), _time.time())

        gate = AdmissionLimit(app, limit=1)
        t0 = _time.time()
        await asyncio.gather(*(gate({"type": "http", "path": f"/api/x{i}", "method": "GET"}, None, None)
                               for i in range(3)))
        return t0, seen
    t0, seen = run(go())
    from datetime import timezone
    for arrived, _ran in seen.values():          # all three arrived at the start...
        assert abs(arrived.replace(tzinfo=timezone.utc).timestamp() - t0) < 0.15
    # ...and the last one ran ~0.4 s later, after waiting in line
    last = max(seen.values(), key=lambda v: v[1])
    assert last[1] - last[0].replace(tzinfo=timezone.utc).timestamp() > 0.35


def test_saves_and_hand_ins_have_their_own_lane_and_others_time_out():
    """Findings 2 and 3: saves/hand-ins never wait behind other requests; others wait at
    most wait_s for a slot and then get 503 + Retry-After."""
    from app.admission import AdmissionLimit, is_priority

    assert is_priority("/api/student/attempts/a_1/submit", "POST")
    assert is_priority("/api/student/attempts/a_1/answers", "PUT")
    assert is_priority("/api/student/attempts/a_1/sealed-submit", "POST")
    assert not is_priority("/api/student/attempts/a_1/answers", "GET")
    assert not is_priority("/api/student/attempts/a_1/heartbeat", "POST")

    async def go():
        release = asyncio.Event()
        ran, sent = [], []

        async def app(scope, receive, send):
            ran.append(scope["path"])
            if scope["path"].endswith("/slow"):
                await release.wait()

        async def send(msg):
            sent.append(msg)

        gate = AdmissionLimit(app, limit=1, wait_s=0.2)
        slow = asyncio.create_task(gate({"type": "http", "path": "/api/x/slow", "method": "GET"}, None, send))
        await asyncio.sleep(0.05)
        async def body():
            return {"type": "http.request", "body": b"{}", "more_body": False}
        # the only slot is taken: a hand-in still goes through its own lane
        await gate({"type": "http", "path": "/api/student/attempts/a_1/submit", "method": "POST"}, body, send)
        # a heartbeat waits 0.2 s and is refused with 503
        await gate({"type": "http", "path": "/api/student/attempts/a_1/heartbeat", "method": "POST"}, None, send)
        release.set()
        await slow
        return ran, sent
    ran, sent = run(go())
    assert "/api/student/attempts/a_1/submit" in ran
    assert "/api/student/attempts/a_1/heartbeat" not in ran
    start = [m for m in sent if m["type"] == "http.response.start"]
    assert start and start[0]["status"] == 503
    assert (b"retry-after", b"3") in start[0]["headers"]


def test_late_hand_in_judged_by_arrival(monkeypatch):
    """Finding 2, end to end: a hand-in that arrived in time is in time even if it ran later."""
    from datetime import timedelta
    from app import timeutil
    t = timeutil.utcnow()

    class Req:
        scope = {"state": {"fairplay_arrived": (t - timedelta(seconds=90)).replace(
            tzinfo=__import__("datetime").timezone.utc).timestamp()}}
    assert (t - timeutil.arrived_at(Req())).total_seconds() > 85
    class NoState:
        scope = {}
    assert abs((timeutil.arrived_at(NoState()) - timeutil.utcnow()).total_seconds()) < 1


def test_sign_out_wins_over_a_concurrent_cache_fill(store, monkeypatch):
    """Finding 4: a cache fill that read the OLD token version before a sign-out must not
    write it back after the sign-out cached the new one."""
    from app import security
    monkeypatch.setattr("app.store.get_store", lambda: store)

    async def go():
        # sign-out ran: new version 8 in the cache
        await security.remember_user(42, "student", 8)
        # a request that read version 7 from the database just before now tries to cache it
        ok = await store.setnx("tv:42", "student:7", security.USER_CACHE_TTL_S)
        return ok, await store.get("tv:42")
    ok, raw = run(go())
    assert ok is False and raw == "student:8"


def test_priority_lane_reads_the_body_first_and_has_its_own_limits():
    """Second review: arrival time is taken once the whole body is here; a body that never
    finishes gets 408; the lane waits at most priority_wait_s, then 503; the app still gets
    the full body."""
    import time as _time
    from app.admission import AdmissionLimit, BODY_READ_S
    from app.timeutil import arrived_at

    class Req:
        def __init__(self, scope):
            self.scope = scope

    async def go():
        got, sent = {}, []

        async def app(scope, receive, send):
            chunks = []
            while True:
                m = await receive()
                chunks.append(m.get("body", b""))
                if not m.get("more_body"):
                    break
            got[scope["path"]] = (b"".join(chunks), arrived_at(Req(scope)))
            if "/b/" in scope["path"]:
                await asyncio.sleep(0.5)

        async def send(msg):
            sent.append(msg)

        def slow_body(parts, gap):
            parts = list(parts)

            async def receive():
                await asyncio.sleep(gap)
                part = parts.pop(0)
                return {"type": "http.request", "body": part, "more_body": bool(parts)}
            return receive

        gate = AdmissionLimit(app, limit=4, priority_limit=1, priority_wait_s=0.2)
        t0 = _time.time()
        await gate({"type": "http", "path": "/api/student/attempts/a/answers", "method": "PUT"},
                   slow_body([b'{"a":', b'"1"}'], 0.3), send)
        body, arrived = got["/api/student/attempts/a/answers"]
        from datetime import timezone
        waited = arrived.replace(tzinfo=timezone.utc).timestamp() - t0
        # lane full -> 503 after priority_wait_s
        hold = asyncio.create_task(gate({"type": "http", "path": "/api/student/attempts/b/submit", "method": "POST"},
                                        slow_body([b"{}"], 0), send))
        await asyncio.sleep(0.05)
        sent.clear()
        await gate({"type": "http", "path": "/api/student/attempts/c/submit", "method": "POST"},
                   slow_body([b"{}"], 0), send)
        busy = [m for m in sent if m["type"] == "http.response.start"]
        await hold
        return body, waited, busy
    body, waited, busy = run(go())
    assert body == b'{"a":"1"}'
    assert waited > 0.5                      # after the second body part, not at the headers
    assert busy and busy[0]["status"] == 503
    assert BODY_READ_S <= 45


def test_busy_lane_size_follows_the_database_pool(client):
    from app.admission import AdmissionLimit
    s = client.app.state.settings
    client.get("/api/health")                  # builds the middleware stack
    stack = client.app.middleware_stack
    # find the AdmissionLimit instance in the built middleware chain
    node, found = stack, None
    for _ in range(20):
        if isinstance(node, AdmissionLimit):
            found = node
            break
        node = getattr(node, "app", None)
        if node is None:
            break
    assert found is not None
    assert found.priority_limit <= max(4, (s.db_pool_size + s.db_max_overflow) // 2)

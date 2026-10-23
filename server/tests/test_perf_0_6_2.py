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
    # a missing hashed asset still falls back to the page, never cached as immutable
    miss = client.get("/assets/missing-zzz.js")
    assert "SPA" in miss.text and miss.headers["cache-control"] == "no-cache"


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

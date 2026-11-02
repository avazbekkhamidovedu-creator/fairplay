import json
import threading
import time

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeExecutor, Signed, make_settings

GOOD = {"language": "c", "source": "int main(){}", "stdins": ["a", "b"]}


def test_health_unsigned_lists_working_languages(api):
    r = api.client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["languages"] == ["c", "cpp"] and body["version"]


def test_languages_requires_signature(api):
    assert api.client.get("/v1/languages").status_code == 401
    r = api.get("/v1/languages")
    assert r.status_code == 200
    assert [x["key"] for x in r.json()["languages"]] == ["c", "cpp"]
    assert all("label" in x and "version" in x for x in r.json()["languages"])


def test_run_accepts_valid_signature(api, fake):
    r = api.post(GOOD)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and [x["stdout"] for x in body["results"]] == ["a", "b"]
    assert fake.calls[0][3:] == (2000, 262144)


def test_stale_timestamp_rejected(api):
    assert api.post(GOOD, ts=int(time.time()) - 61).status_code == 401
    assert api.post(GOOD, ts=int(time.time()) + 61).status_code == 401
    assert api.post(GOOD, ts=int(time.time()) - 50).status_code == 200


def test_replayed_nonce_rejected(api):
    assert api.post(GOOD, nonce="n" * 20).status_code == 200
    assert api.post(GOOD, nonce="n" * 20).status_code == 401


def test_nonce_is_per_tenant(api):
    other = Signed(api.client, "t2", "z" * 40)
    assert api.post(GOOD, nonce="q" * 20).status_code == 200
    assert other.post(GOOD, nonce="q" * 20).status_code == 200


def test_bad_signature_rejected(api):
    assert api.post(GOOD, sig="0" * 64).status_code == 401
    assert api.post(GOOD, secret="w" * 40).status_code == 401
    assert api.post(GOOD, sig="zz").status_code == 401


def test_unknown_tenant_rejected_with_generic_body(api):
    bad = api.post(GOOD, tenant="nobody")
    wrong = api.post(GOOD, sig="0" * 64)
    assert bad.status_code == wrong.status_code == 401
    assert bad.json() == wrong.json()


def test_tampered_body_rejected(api):
    body = json.dumps(GOOD).encode()
    headers = api.headers(body)
    tampered = json.dumps({**GOOD, "source": "evil"}).encode()
    assert api.client.post("/v1/run", content=tampered, headers=headers).status_code == 401


def test_failed_signature_does_not_burn_nonce(api):
    assert api.post(GOOD, nonce="b" * 20, sig="0" * 64).status_code == 401
    assert api.post(GOOD, nonce="b" * 20).status_code == 200


def test_short_nonce_rejected(api):
    assert api.post(GOOD, nonce="short").status_code == 401


def test_body_over_256kb_is_413(api):
    big = b'{"language":"c","source":"' + b"a" * (256 * 1024) + b'","stdins":[""]}'
    assert api.post(big).status_code == 413


def test_missing_headers_rejected(api):
    assert api.client.post("/v1/run", content=b"{}").status_code == 401


def _expect(api, status, **over):
    payload = {**GOOD, **over}
    r = api.post(payload)
    assert r.status_code == status, r.text
    return r


def test_validation_limits(api):
    _expect(api, 422, language="python")
    _expect(api, 422, source="x" * 100_001)
    _expect(api, 200, source="x" * 100_000)
    _expect(api, 422, stdins=[])
    _expect(api, 422, stdins=[""] * 31)
    _expect(api, 200, stdins=[""] * 30)
    _expect(api, 422, stdins=["x" * 20_001])
    _expect(api, 200, stdins=["x" * 20_000])
    _expect(api, 422, time_limit_ms=99)
    _expect(api, 422, time_limit_ms=10_001)
    _expect(api, 200, time_limit_ms=100)
    _expect(api, 200, time_limit_ms=10_000)
    _expect(api, 422, memory_limit_kb=16383)
    _expect(api, 422, memory_limit_kb=1048577)
    _expect(api, 200, memory_limit_kb=16384)
    _expect(api, 422, time_limit_ms="2000")
    _expect(api, 422, source="a\x00b")


def test_invalid_json_and_missing_fields(api):
    assert api.post(b"not json").status_code == 422
    assert api.post({"language": "c"}).status_code == 422
    assert api.post([1, 2]).status_code == 422


def test_unavailable_language(api):
    assert api.post({**GOOD, "language": "java"}).status_code == 422


def test_compile_error_shape_passes_through(api, fake):
    fake.outcome = {"status": "compile_error", "compile_output": "main.c:1: error", "runtime_version": "gcc 14.2.0",
                    "results": []}
    body = api.post(GOOD).json()
    assert body == fake.outcome


def test_queue_wait_gives_503_with_retry_after(fake):
    fake.delay = 2.0
    app = create_app(make_settings(max_concurrent=1, tenant_concurrent=1, queue_wait_s=1), fake)
    with TestClient(app) as client:
        s = Signed(client)
        first = {}
        t = threading.Thread(target=lambda: first.update(r=s.post(GOOD)))
        t.start()
        time.sleep(0.3)
        r = s.post(GOOD)
        t.join()
        assert r.status_code == 503 and r.headers["retry-after"]
        assert first["r"].status_code == 200


def test_tenant_cap_does_not_block_other_tenant(fake):
    fake.delay = 1.5
    app = create_app(make_settings(max_concurrent=4, tenant_concurrent=1, queue_wait_s=1), fake)
    with TestClient(app) as client:
        a, b = Signed(client), Signed(client, "t2", "z" * 40)
        out = {}
        t = threading.Thread(target=lambda: out.update(a=a.post(GOOD)))
        t.start()
        time.sleep(0.3)
        assert a.post(GOOD).status_code == 503
        assert b.post(GOOD).status_code == 200
        t.join()


def test_logs_do_not_contain_source_or_output(api, caplog):
    caplog.set_level("INFO", logger="fp-runner")
    api.post({**GOOD, "source": "SECRET_SOURCE_MARKER", "stdins": ["SECRET_STDIN_MARKER"]})
    text = caplog.text
    assert "SECRET_SOURCE_MARKER" not in text and "SECRET_STDIN_MARKER" not in text
    assert "tenant=t1" in text and "lang=c" in text

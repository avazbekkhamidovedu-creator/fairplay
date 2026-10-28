from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import monitoring
from app.config import Settings
from app.main import create_app
from app.timeutil import utcnow


@pytest.fixture
def mclient(tmp_path, static_dir, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "m-token")
    monitoring.METRICS.__init__()
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", redis_url=None,
                        secret="test-secret-0123456789abcdef", pbkdf2_iterations=1000, require_app=False,
                        app_keys=["test-app-key"], dev=False, seed_loadtest_users=0, seed_demo=True,
                        single_process=True, static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"))
    with TestClient(create_app(settings)) as c:
        yield c


AUTH = {"Authorization": "Bearer m-token"}


def test_metrics_is_404_without_token_env(client):
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers=AUTH).status_code == 404


def test_metrics_needs_the_bearer_token(mclient):
    assert mclient.get("/metrics").status_code == 401
    assert mclient.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = mclient.get("/metrics", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")


def test_routes_are_templates_not_raw_paths(mclient):
    mclient.get("/api/health")
    mclient.get("/api/exams/e_123/nothing-here")
    mclient.get("/some/spa/page/9f8e7d")
    body = mclient.get("/metrics", headers=AUTH).text
    assert 'route="/api/health",status="200"} 1' in body
    assert "e_123" not in body and "9f8e7d" not in body
    assert 'route="/api/{rest:path}"' in body and 'route="/{path:path}"' in body
    assert 'fairplay_http_request_duration_seconds_bucket{route="/api/health",le="+Inf"} 1' in body
    assert 'route="/metrics"' not in body


def test_5xx_and_admission_rejections_are_counted():
    m = monitoring.Metrics()
    m.observe("GET", "/api/x", 500, 0.2)
    m.observe("GET", "/api/x", 200, 0.01)
    assert m.errors_5xx == {"/api/x": 1} and sum(m.requests.values()) == 2

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 503, "headers": [(b"retry-after", b"3")]})
        await send({"type": "http.response.body", "body": b""})

    import asyncio

    async def run():
        sent = []

        async def send(msg):
            sent.append(msg)
        await monitoring.MetricsMiddleware(app, m)({"type": "http", "method": "GET", "path": "/api/y"}, None, send)
    asyncio.run(run())
    assert m.rejected == 1 and m.errors_5xx["unmatched"] == 1


def _add_delivery(client, attempt_id, status, age_min):
    from app.db import get_database
    from app.integrations.models import ExternalDelivery

    async def go():
        async with get_database().sessionmaker() as s:
            s.add(ExternalDelivery(source="x", attempt_id=attempt_id, exam_id="e1", status=status,
                                   created_at=utcnow() - timedelta(minutes=age_min)))
            await s.commit()
    client.portal.call(go)


def test_outbox_gauges_and_cache(mclient):
    body = mclient.get("/metrics", headers=AUTH).text
    assert 'fairplay_outbox_deliveries{status="dead"} 0' in body
    assert "fairplay_outbox_oldest_pending_age_seconds 0" in body
    _add_delivery(mclient, "a1", "pending", 40)
    _add_delivery(mclient, "a2", "dead", 5)
    cached = mclient.get("/metrics", headers=AUTH).text
    assert 'status="pending"} 0' in cached
    mclient.app.state.metrics_outbox.at = 0
    body = mclient.get("/metrics", headers=AUTH).text
    assert 'fairplay_outbox_deliveries{status="pending"} 1' in body
    assert 'fairplay_outbox_deliveries{status="dead"} 1' in body
    age = int([ln for ln in body.splitlines()
               if ln.startswith("fairplay_outbox_oldest_pending_age_seconds ")][0].split()[1])
    assert 39 * 60 <= age <= 41 * 60


def test_lms_sync_age_exposed_only_when_recorded(mclient):
    assert "fairplay_lms_last_sync_age_seconds" not in mclient.get("/metrics", headers=AUTH).text


def test_health_response_unchanged(mclient):
    extra = {"browser_demo"} if mclient.app.state.settings.browser_gate == "off" else set()
    assert set(mclient.get("/api/health").json()) == {"ok", "demo_logins", "features", "student_grades"} | extra


def test_sentry_is_a_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert monitoring.init_sentry() is False


def test_sentry_missing_package_is_not_an_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert monitoring.init_sentry("https://k@o0.ingest.sentry.io/1") is False


def test_sentry_event_is_scrubbed():
    event = {
        "request": {"data": {"answer": "x"}, "cookies": {"s": "1"}, "query_string": "t=abc",
                    "headers": {"Authorization": "Bearer abc.def", "X-Other": "Bearer zzz9", "Accept": "json"}},
        "extra": {"start_code": "123456", "exit_code": "9", "note": "sent Bearer tok123 ok"},
        "breadcrumbs": {"values": [{"message": "Authorization: Bearer q1w2"}]},
    }
    out = monitoring.scrub_event(event)
    assert "data" not in out["request"] and "cookies" not in out["request"]
    assert out["request"]["query_string"] == "[Filtered]"
    assert out["request"]["headers"]["Authorization"] == "[Filtered]"
    assert out["request"]["headers"]["X-Other"] == "Bearer [Filtered]"
    assert out["request"]["headers"]["Accept"] == "json"
    assert out["extra"]["start_code"] == "[Filtered]" and out["extra"]["exit_code"] == "[Filtered]"
    assert "tok123" not in out["extra"]["note"] and "q1w2" not in str(out["breadcrumbs"])


def test_sentry_init_with_package(monkeypatch):
    sentry_sdk = pytest.importorskip("sentry_sdk")
    assert monitoring.init_sentry("https://k@o0.ingest.sentry.io/1") is True
    client = sentry_sdk.get_client()
    assert client.options["send_default_pii"] is False
    sentry_sdk.init()

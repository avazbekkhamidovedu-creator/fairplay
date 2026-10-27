"""Optional Sentry and a Prometheus /metrics endpoint (no client library: plain text)."""
from __future__ import annotations

import hmac
import logging
import os
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .timeutil import utcnow

log = logging.getLogger("proctora")

BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
OUTBOX_CACHE_S = 15.0

_SECRET_KEYS = re.compile(r"authorization|cookie|token|password|secret|credential|start_?code|exit_?code|"
                          r"unlock|^code$", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def _scrub_value(value):
    if isinstance(value, str):
        return _BEARER.sub("Bearer [Filtered]", value)
    if isinstance(value, dict):
        return {k: ("[Filtered]" if isinstance(k, str) and _SECRET_KEYS.search(k) else _scrub_value(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


def scrub_event(event, hint=None):
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        if "headers" in request:
            request["headers"] = _scrub_value(request["headers"])
        if "query_string" in request:
            request["query_string"] = "[Filtered]"
    for key in ("extra", "contexts", "breadcrumbs", "exception"):
        if key in event:
            event[key] = _scrub_value(event[key])
    return event


def init_sentry(dsn: str | None = None) -> bool:
    dsn = dsn if dsn is not None else os.environ.get("SENTRY_DSN", "")
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed; error reporting is off")
        return False
    sentry_sdk.init(dsn=dsn, send_default_pii=False, max_request_body_size="never",
                    include_local_variables=False, before_send=scrub_event,
                    before_breadcrumb=lambda crumb, hint: _scrub_value(crumb),
                    integrations=[StarletteIntegration(), FastApiIntegration()],
                    traces_sample_rate=0.0,
                    release=os.environ.get("FAIRPLAY_RELEASE") or None)
    return True


class Metrics:
    def __init__(self) -> None:
        self.requests: dict[tuple[str, str, str], int] = {}
        self.latency: dict[str, list[int]] = {}
        self.latency_sum: dict[str, float] = {}
        self.errors_5xx: dict[str, int] = {}
        self.rejected = 0

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        key = (method, route, str(status))
        self.requests[key] = self.requests.get(key, 0) + 1
        counts = self.latency.setdefault(route, [0] * (len(BUCKETS) + 1))
        for i, bound in enumerate(BUCKETS):
            if seconds <= bound:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
        self.latency_sum[route] = self.latency_sum.get(route, 0.0) + seconds
        if status >= 500:
            self.errors_5xx[route] = self.errors_5xx.get(route, 0) + 1


METRICS = Metrics()


def _route_template(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class MetricsMiddleware:
    """Outermost, so it also sees the 503 the admission gate answers with."""

    def __init__(self, app, metrics: Metrics = METRICS):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status = 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                if status in (503, 408) and any(k == b"retry-after" for k, _ in message.get("headers", [])):
                    self.metrics.rejected += 1
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            if scope.get("path") != "/metrics":
                self.metrics.observe(scope.get("method", ""), _route_template(scope), status,
                                     time.perf_counter() - started)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _OutboxCache:
    def __init__(self) -> None:
        self.at = 0.0
        self.value: dict | None = None


async def _outbox_stats(db) -> dict:
    from sqlalchemy import func, select
    from .integrations.models import ExternalDelivery as D
    async with db.sessionmaker() as session:
        rows = (await session.execute(
            select(D.status, func.count(), func.min(D.created_at)).where(D.status.in_(("pending", "failed", "dead")))
            .group_by(D.status))).all()
    by_status = {status: (n, oldest) for status, n, oldest in rows}
    undelivered = [by_status[s][1] for s in ("pending", "failed") if s in by_status]
    oldest = min(undelivered) if undelivered else None
    return {"pending": by_status.get("pending", (0, None))[0], "failed": by_status.get("failed", (0, None))[0],
            "dead": by_status.get("dead", (0, None))[0],
            "oldest_age": max(0.0, (utcnow() - oldest).total_seconds()) if oldest else 0.0}


async def _lms_sync_age(db) -> float | None:
    from sqlalchemy import func, select
    from .models import LmsConnection
    async with db.sessionmaker() as session:
        last = (await session.execute(select(func.max(LmsConnection.last_sync_at)))).scalar()
    return max(0.0, (utcnow() - last).total_seconds()) if last else None


def _pool_stats(db, settings) -> tuple[int, int] | None:
    pool = db.engine.pool
    if db.is_sqlite or not hasattr(pool, "checkedout"):
        return None
    return pool.checkedout(), settings.db_pool_size + settings.db_max_overflow


async def render(app: FastAPI, metrics: Metrics = METRICS) -> str:
    from .db import get_database
    out: list[str] = []

    def family(name: str, kind: str, help_: str) -> None:
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {kind}")

    family("fairplay_http_requests_total", "counter", "Requests by method, route template and status")
    for (method, route, status), n in sorted(metrics.requests.items()):
        out.append(f'fairplay_http_requests_total{{method="{method}",route="{_label(route)}",status="{status}"}} {n}')
    family("fairplay_http_request_duration_seconds", "histogram", "Request latency by route template")
    for route, counts in sorted(metrics.latency.items()):
        r, running = _label(route), 0
        for bound, c in zip(BUCKETS, counts):
            running += c
            out.append(f'fairplay_http_request_duration_seconds_bucket{{route="{r}",le="{bound}"}} {running}')
        running += counts[-1]
        out.append(f'fairplay_http_request_duration_seconds_bucket{{route="{r}",le="+Inf"}} {running}')
        out.append(f'fairplay_http_request_duration_seconds_sum{{route="{r}"}} {metrics.latency_sum[route]:.6f}')
        out.append(f'fairplay_http_request_duration_seconds_count{{route="{r}"}} {running}')
    family("fairplay_http_5xx_total", "counter", "Responses with status 500 and up by route template")
    for route, n in sorted(metrics.errors_5xx.items()):
        out.append(f'fairplay_http_5xx_total{{route="{_label(route)}"}} {n}')
    family("fairplay_admission_rejected_total", "counter",
           "Requests the in-flight gate answered 503/408 with Retry-After")
    out.append(f"fairplay_admission_rejected_total {metrics.rejected}")

    try:
        db = get_database()
    except RuntimeError:
        db = None
    if db is not None:
        pool = _pool_stats(db, app.state.settings)
        if pool:
            family("fairplay_db_pool_checked_out", "gauge", "Database connections in use in this worker")
            out.append(f"fairplay_db_pool_checked_out {pool[0]}")
            family("fairplay_db_pool_capacity", "gauge", "Pool size plus max overflow for this worker")
            out.append(f"fairplay_db_pool_capacity {pool[1]}")
        cache: _OutboxCache = app.state.metrics_outbox
        now = time.monotonic()
        if cache.value is None or now - cache.at > OUTBOX_CACHE_S:
            try:
                cache.value = await _outbox_stats(db)
            except Exception:  # noqa: BLE001
                log.exception("metrics: outbox query failed")
                pass
            cache.at = now
        if cache.value is not None:
            family("fairplay_outbox_deliveries", "gauge", "External deliveries not yet sent, by status")
            for status in ("pending", "failed", "dead"):
                out.append(f'fairplay_outbox_deliveries{{status="{status}"}} {cache.value[status]}')
            family("fairplay_outbox_oldest_pending_age_seconds", "gauge",
                   "Age of the oldest pending or failed (retrying) delivery, 0 when none")
            out.append(f"fairplay_outbox_oldest_pending_age_seconds {cache.value['oldest_age']:.0f}")
        if "lms" in app.state.settings.features:
            try:
                age = await _lms_sync_age(db)
            except Exception:  # noqa: BLE001
                age = None
            if age is not None:
                family("fairplay_lms_last_sync_age_seconds", "gauge", "Seconds since the latest LMS roster sync")
                out.append(f"fairplay_lms_last_sync_age_seconds {age:.0f}")
    return "\n".join(out) + "\n"


def install(app: FastAPI, token: str | None = None) -> None:
    token = token if token is not None else os.environ.get("METRICS_TOKEN", "")
    app.state.metrics_outbox = _OutboxCache()

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        if not token:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), token.encode()):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401, headers={"www-authenticate": "Bearer"})
        return PlainTextResponse(await render(app), media_type="text/plain; version=0.0.4")

    app.add_middleware(MetricsMiddleware)

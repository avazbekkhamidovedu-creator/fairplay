"""perf 0.6.2: at most N API requests in progress per worker process; the rest wait in line.

Found in the 5,000-student load test: when a worker had thousands of requests in progress
at once (an exam-start rush on too few cores), each of them advanced only a little per turn
of the event loop, so a request that held a database or Redis connection held it for
seconds. The pools ran dry and requests failed after 60 s ("QueuePool limit reached",
"Too many connections"), even though nothing was really wrong.

With this gate the extra requests wait (first come, first served) before they start, the
ones in progress finish quickly and give their connections back, and nothing fails: a rush
is served a little later instead of partly refused. Long uploads (scans, open-book files,
question images) and sign-in are not counted, so they can't hold the line up.

0.6.2 review:
- Every request notes when it ARRIVED (scope["state"]["fairplay_arrived"]); saving answers
  and handing in check the deadline against that time, so waiting in line at the deadline
  never makes an answer late (timeutil.arrived_at).
- Saving answers and handing in have their own lane (a quarter of the limit on top), so
  they never wait behind a queue of heartbeats.
- Any other request waits at most QUEUE_WAIT_S for a slot, then gets 503 + Retry-After (the
  apps and the page already retry heartbeats, saves and loads). A long overload can't build
  up a backlog of heartbeats nobody is waiting for any more.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from .http_guard import MATERIAL_UPLOAD, SCAN_UPLOAD

QUEUE_WAIT_S = 10.0
RETRY_AFTER_S = 3
# Saving answers and handing in (never refused for waiting: the client would only retry).
_PRIORITY = re.compile(r"^/api/student/attempts/[^/]+/(answers|submit|sealed-submit)$")


def _exempt(path: str) -> bool:
    # Sign-in is not counted either: it waits for its own (few) password-check slots
    # (routes_auth), and a sign-in rush must not fill the line ahead of the students
    # already writing.
    return (not path.startswith("/api/") or path in ("/api/uploads", "/api/health", "/api/auth/login")
            or bool(SCAN_UPLOAD.match(path)) or bool(MATERIAL_UPLOAD.match(path)))


def is_priority(path: str, method: str) -> bool:
    return method in ("PUT", "POST") and bool(_PRIORITY.match(path))


async def _busy(send) -> None:
    body = json.dumps({"detail": "The server is busy, trying again"}).encode()
    await send({"type": "http.response.start", "status": 503, "headers": [
        (b"content-type", b"application/json"), (b"retry-after", str(RETRY_AFTER_S).encode()),
        (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


class AdmissionLimit:
    def __init__(self, app, limit: int = 128, wait_s: float = QUEUE_WAIT_S):
        self.app = app
        self.limit = max(1, int(limit))
        self.wait_s = wait_s
        self._sem: asyncio.Semaphore | None = None
        self._prio: asyncio.Semaphore | None = None

    def _gate(self) -> asyncio.Semaphore:
        if self._sem is None:          # created inside the worker's event loop
            self._sem = asyncio.Semaphore(self.limit)
        return self._sem

    def _prio_gate(self) -> asyncio.Semaphore:
        if self._prio is None:
            self._prio = asyncio.Semaphore(max(1, self.limit // 4))
        return self._prio

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["fairplay_arrived"] = time.time()
        if scope["type"] != "http" or _exempt(scope.get("path", "")):
            return await self.app(scope, receive, send)
        if is_priority(scope.get("path", ""), scope.get("method", "")):
            async with self._prio_gate():
                return await self.app(scope, receive, send)
        gate = self._gate()
        try:
            await asyncio.wait_for(gate.acquire(), self.wait_s)
        except asyncio.TimeoutError:
            return await _busy(send)
        try:
            await self.app(scope, receive, send)
        finally:
            gate.release()

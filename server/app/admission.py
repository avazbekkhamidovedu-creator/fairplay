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
- Saving answers and handing in have their own lane (about half the database pool), so
  they never wait behind a queue of heartbeats; they wait up to PRIORITY_WAIT_S (under
  nginx's 60 s), and their arrival time is taken once their whole body is here.
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
# Saves and hand-ins wait longer (but less than nginx's 60 s proxy timeout, so a request
# the proxy gave up on is not left in line); they are judged by arrival time anyway.
PRIORITY_WAIT_S = 45.0
# Their body is read in full BEFORE the arrival time is taken (a client can't send the
# headers before the deadline and the answers later); at most this long.
BODY_READ_S = 30.0
MAX_PRIORITY_BODY = 25 * 1024 * 1024      # above the largest answer body http_guard allows
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


async def _busy(send, status: int = 503, detail: str = "The server is busy, trying again") -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"retry-after", str(RETRY_AFTER_S).encode()),
        (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive) -> tuple[list[dict], bool]:
    """All of the request's body messages (and whether the client went away)."""
    messages, size = [], 0
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            return messages, True
        messages.append(msg)
        size += len(msg.get("body", b""))
        if size > MAX_PRIORITY_BODY or not msg.get("more_body", False):
            return messages, False


def _replay(messages: list[dict], receive):
    """A receive() that first gives back the body already read, then the real one."""
    queue = list(messages)

    async def again():
        if queue:
            return queue.pop(0)
        return await receive()
    return again


class AdmissionLimit:
    def __init__(self, app, limit: int = 128, wait_s: float = QUEUE_WAIT_S,
                 priority_limit: int | None = None, priority_wait_s: float = PRIORITY_WAIT_S):
        self.app = app
        self.limit = max(1, int(limit))
        self.wait_s = wait_s
        # 0.6.2 review: saves write to the database; keep their lane under about half of
        # the worker's database pool so a save burst can't take every connection.
        self.priority_limit = max(1, int(priority_limit if priority_limit is not None else self.limit // 4))
        self.priority_wait_s = priority_wait_s
        self._sem: asyncio.Semaphore | None = None
        self._prio: asyncio.Semaphore | None = None

    def _gate(self) -> asyncio.Semaphore:
        if self._sem is None:          # created inside the worker's event loop
            self._sem = asyncio.Semaphore(self.limit)
        return self._sem

    def _prio_gate(self) -> asyncio.Semaphore:
        if self._prio is None:
            self._prio = asyncio.Semaphore(self.priority_limit)
        return self._prio

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["fairplay_arrived"] = time.time()
        if scope["type"] != "http" or _exempt(scope.get("path", "")):
            return await self.app(scope, receive, send)
        if is_priority(scope.get("path", ""), scope.get("method", "")):
            try:
                messages, gone = await asyncio.wait_for(_read_body(receive), BODY_READ_S)
            except asyncio.TimeoutError:
                return await _busy(send, 408, "The request took too long to arrive")
            if gone:
                return
            # arrived = when the whole request was here (0.6.2 review)
            scope["state"]["fairplay_arrived"] = time.time()
            gate = self._prio_gate()
            try:
                await asyncio.wait_for(gate.acquire(), self.priority_wait_s)
            except asyncio.TimeoutError:
                return await _busy(send)
            try:
                return await self.app(scope, _replay(messages, receive), send)
            finally:
                gate.release()
        gate = self._gate()
        try:
            await asyncio.wait_for(gate.acquire(), self.wait_s)
        except asyncio.TimeoutError:
            return await _busy(send)
        try:
            await self.app(scope, receive, send)
        finally:
            gate.release()

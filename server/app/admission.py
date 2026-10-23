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
"""
from __future__ import annotations

import asyncio

from .http_guard import MATERIAL_UPLOAD, SCAN_UPLOAD


def _exempt(path: str) -> bool:
    # Sign-in is not counted either: it waits for its own (few) password-check slots
    # (routes_auth), and a sign-in rush must not fill the line ahead of the students
    # already writing.
    return (not path.startswith("/api/") or path in ("/api/uploads", "/api/health", "/api/auth/login")
            or bool(SCAN_UPLOAD.match(path)) or bool(MATERIAL_UPLOAD.match(path)))


class AdmissionLimit:
    def __init__(self, app, limit: int = 128):
        self.app = app
        self.limit = max(1, int(limit))
        self._sem: asyncio.Semaphore | None = None

    def _gate(self) -> asyncio.Semaphore:
        if self._sem is None:          # created inside the worker's event loop
            self._sem = asyncio.Semaphore(self.limit)
        return self._sem

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _exempt(scope.get("path", "")):
            return await self.app(scope, receive, send)
        async with self._gate():
            await self.app(scope, receive, send)

"""The student side is for the FairPlay apps only: a browser gets the same 404 an unknown path gets.

What the released apps (0.7.x) send:
  Mac, iPad  user agent "... FairPlay/<version>" on every page load, script and fetch of
             the web view; X-FairPlay-Version + X-FairPlay-Platform on their own API calls
  Windows    nothing on the web view (stock Edge user agent) and nothing on its own API
             calls (no user agent, no version header)
Strict mode needs one of those markers. The default (headers) also lets through what
Windows 0.7.x sends: no user agent at all, or the WebView2/Edge one on Windows. Every
marker is a plain header, so this stops a browser and curl, not someone who forges it.
"""
from __future__ import annotations

import re

from starlette.responses import JSONResponse

FAIRPLAY_UA = re.compile(r"FairPlay/\d")
PLATFORMS = frozenset({"mac", "windows", "ipad"})
WATCHDOG = re.compile(r"^/api/student/attempts/[^/]+/events$")
NOT_FOUND = {"detail": "Not Found"}
SEALED = re.compile(r"^/api/student/attempts/[^/]+/sealed-submit$")
MODES = ("strict", "headers", "off")


def is_student_page(path: str) -> bool:
    return path == "/student" or path.startswith("/student/")


def is_student_api(path: str) -> bool:
    return (path == "/api/student" or path.startswith("/api/student/")
            or path.startswith("/api/setup/")) and not SEALED.match(path)


def has_marker(headers: dict[str, str], watchdog: bool) -> bool:
    if FAIRPLAY_UA.search(headers.get("user-agent", "")):
        return True
    if (headers.get("x-fairplay-platform", "").strip().lower() in PLATFORMS
            and headers.get("x-fairplay-version", "").strip()):
        return True
    # The Mac watchdog reports a killed app with only the bearer token and the session id.
    # The id is bound to the attempt in the database, which a middleware does not read:
    # the path is the only restriction.
    return watchdog and bool(headers.get("x-fairplay-session", "").strip())


def from_app(headers: dict[str, str], mode: str, watchdog: bool = False) -> bool:
    if mode == "off" or has_marker(headers, watchdog):
        return True
    if mode == "strict":
        return False
    ua = headers.get("user-agent", "").strip()
    return not ua or ("Windows NT" in ua and "Edg/" in ua)


def request_from_app(request) -> bool:
    mode = getattr(request.app.state.settings, "browser_gate", "headers")
    return from_app({k.lower(): v for k, v in request.headers.items()}, mode)


class BrowserGate:
    def __init__(self, app, mode: str = "headers"):
        self.app = app
        self.mode = mode if mode in MODES else "headers"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.mode == "off":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):] or "/"
        path = re.sub(r"/{2,}", "/", path)
        if is_student_page(path) or is_student_api(path):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers") or []}
            watchdog = scope.get("method") == "POST" and bool(WATCHDOG.match(path))
            if not from_app(headers, self.mode, watchdog):
                return await JSONResponse(NOT_FOUND, status_code=404)(scope, receive, send)
        return await self.app(scope, receive, send)

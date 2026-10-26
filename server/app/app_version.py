"""S2 (2026-10-20): app version gate and the server clock header.

The apps send X-FairPlay-Version ("0.5") and X-FairPlay-Platform ("mac" | "windows" | "ipad") on
every request. Start and app-beat refuse an app older than the platform's minimum with
426, so a fixed bug can't keep running in an exam. No platform header = a browser
without the app: never gated (FAIRPLAY_REQUIRE_APP decides about browsers).
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import Settings

VERSION_HEADER = "x-fairplay-version"
PLATFORM_HEADER = "x-fairplay-platform"
SERVER_TIME_HEADER = "X-Server-Time"
UPDATE_DETAIL = "Update FairPlay to continue"


def version_tuple(v: str | None) -> tuple[int, ...]:
    """"0.10" -> (0, 10); trailing zeros dropped so 1.0 == 1.0.0. A part that is not a
    number counts by its leading digits ("5b2" -> 5), none -> 0."""
    parts = []
    for raw in (v or "").strip().split("."):
        digits = ""
        for ch in raw.strip():
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def older_than(version: str | None, minimum: str | None) -> bool:
    return bool(minimum) and version_tuple(version) < version_tuple(minimum)


def client_platform(request: Request) -> str | None:
    p = (request.headers.get(PLATFORM_HEADER) or "").strip().lower()
    return p or None


class UpgradeRequired(Exception):
    def __init__(self, minimum: str):
        self.minimum = minimum


def check_app_version(request: Request, settings: Settings) -> str | None:
    """Raise UpgradeRequired (426) for an app below its platform's minimum. Returns that
    minimum (or None) for the response's min_app_version."""
    platform = client_platform(request)
    if platform is None:
        return None
    minimum = settings.min_app_version(platform)
    if older_than(request.headers.get(VERSION_HEADER), minimum):
        raise UpgradeRequired(minimum or "")
    return minimum


async def upgrade_required_handler(_request: Request, exc: UpgradeRequired) -> JSONResponse:
    return JSONResponse({"detail": UPDATE_DETAIL, "min_app_version": exc.minimum}, status_code=426)


CANVAS_UNSUPPORTED_DETAIL = "Canvas exams are not available on this device yet"


CANVAS_LINK_DETAIL = "This Canvas exam link needs to be fixed by your teacher"


def check_canvas_platform(request: Request, settings: Settings) -> None:
    """Canvas exams start only from an app that has the restricted Canvas browser. A platform
    with no minimum set (and a browser or unknown platform) gets 409; an app below the minimum
    gets the normal 426 so it offers an update."""
    from fastapi import HTTPException
    platform = client_platform(request)
    minimum = settings.canvas_min_version(platform)
    if platform is None or not minimum:
        raise HTTPException(409, CANVAS_UNSUPPORTED_DETAIL)
    if older_than(request.headers.get(VERSION_HEADER), minimum):
        raise UpgradeRequired(minimum)

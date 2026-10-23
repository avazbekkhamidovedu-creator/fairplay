"""2026-10-23 (review): can an allowed website be shown inside the exam's websites pane?

Many sites refuse to be shown inside another page (X-Frame-Options, or CSP
frame-ancestors). The browser then shows a blank frame, so the teacher should hear about it
while choosing the exam's websites, not the student during the exam.

  GET /api/tools/site-check?host=docs.python.org   teacher only, rate limited
      -> {"host": "docs.python.org", "frameable": true | false | null, "reason": "..."}

`frameable` is null when the site could not be checked (no answer, an error page, or an
address on a private network). "*.python.org" checks python.org (what the pane opens).

The request goes only to public addresses: the host must be a website name (no IP, no
localhost), every connection resolves once and refuses private addresses (the LMS guard,
which also stops DNS rebinding), at most MAX_REDIRECTS redirects, TIMEOUT_S in all.
Answers are kept for CACHE_S per host.
"""
from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .lms.base import LmsError, public_only_transport
from .schemas import WILDCARD_PREFIX, normalize_website
from .security import Principal, require_teacher
from .store import get_store

router = APIRouter(prefix="/api/tools", tags=["tools"])

TIMEOUT_S = 5.0
MAX_REDIRECTS = 3
CHECKS_PER_MIN = 30
CACHE_S = 3600
USER_AGENT = "FairPlay-SiteCheck/1.0"

_cache: dict[str, tuple[float, dict]] = {}

# Tests route the request through httpx.MockTransport.
_transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None


def set_transport_factory(factory: Callable[[], httpx.AsyncBaseTransport] | None) -> None:
    global _transport_factory
    _transport_factory = factory
    _cache.clear()


def _host_matches(host: str, entry: str) -> bool:
    """Same rule as the apps (and web/src/lib/websites.ts entryMatches)."""
    h = host.lower().rstrip(".")
    if entry.startswith(WILDCARD_PREFIX):
        base = entry[len(WILDCARD_PREFIX):]
        return h == base or h.endswith("." + base)
    if h == entry or h == "www." + entry:
        return True
    return entry.startswith("www.") and "." in entry[4:] and h == entry[4:]


def frame_verdict(headers: httpx.Headers, own_host: str | None = None) -> tuple[bool, str]:
    """(frameable, reason) from a response's headers."""
    for csp in headers.get_list("content-security-policy"):
        for directive in csp.split(";"):
            parts = directive.strip().split()
            if not parts or parts[0].lower() != "frame-ancestors":
                continue
            sources = [s.lower() for s in parts[1:]]
            if "*" in sources or "https:" in sources:
                continue
            # the exam server itself is named (rare, but then the frame works)
            if own_host and any(s.split("://")[-1].split(":")[0].rstrip("/") == own_host for s in sources):
                continue
            return False, "The site doesn't allow being shown inside other pages (frame-ancestors)."
    xfo = (headers.get("x-frame-options") or "").strip().lower()
    if xfo:
        # DENY, SAMEORIGIN and the old ALLOW-FROM all keep our page out
        return False, f"The site doesn't allow being shown inside other pages (X-Frame-Options: {xfo.upper()})."
    return True, "The site can be shown inside the exam."


async def check_site(entry: str, own_host: str | None = None) -> dict:
    host = entry[len(WILDCARD_PREFIX):] if entry.startswith(WILDCARD_PREFIX) else entry
    hit = _cache.get(entry)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    transport = _transport_factory() if _transport_factory else public_only_transport()
    url = f"https://{host}/"
    frameable: bool | None = None
    reason = ""
    try:
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(TIMEOUT_S),
                                     follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as c:
            for hop in range(MAX_REDIRECTS + 1):
                r = await c.get(url)
                if r.is_redirect and hop < MAX_REDIRECTS:
                    nxt = r.next_request.url if r.next_request else None
                    if nxt is None or nxt.scheme not in ("http", "https"):
                        reason = "The site sends visitors somewhere else."
                        break
                    if not _host_matches(nxt.host, entry):
                        frameable = False
                        reason = (f"The site sends visitors to {nxt.host}, which is not on the list. "
                                  "Add that site instead.")
                        break
                    url = str(nxt)
                    continue
                if r.is_redirect:
                    reason = "The site redirects too many times."
                elif r.status_code >= 400:
                    # an error page tells nothing about the site itself
                    reason = f"The site answered with an error ({r.status_code})."
                else:
                    frameable, reason = frame_verdict(r.headers, own_host)
                break
    except LmsError:
        reason = "That site is on a private network."
    except httpx.TimeoutException:
        reason = "The site did not answer in time."
    except httpx.HTTPError:
        reason = "The site could not be reached."
    out = {"host": entry, "frameable": frameable, "reason": reason}
    if frameable is not None:      # only definite answers are kept
        _cache[entry] = (time.monotonic() + CACHE_S, out)
    return out


@router.get("/site-check")
async def site_check(request: Request, host: str = Query(..., max_length=260),
                     p: Principal = Depends(require_teacher)):
    store = get_store()
    if await store.incr(f"sitecheck:{p.user_id}", 60) > CHECKS_PER_MIN:
        raise HTTPException(429, "Too many website checks, wait a minute")
    try:
        entry = normalize_website(host)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    bare = entry[len(WILDCARD_PREFIX):] if entry.startswith(WILDCARD_PREFIX) else entry
    if bare == "localhost" or bare.endswith(".localhost") or bare.endswith(".local") \
            or bare.endswith(".internal"):
        raise HTTPException(422, "That is not a public website")
    own = urlsplit(str(request.base_url)).hostname
    return await check_site(entry, own)

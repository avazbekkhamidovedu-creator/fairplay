"""Pilot 0.8.8: GET /api/app/update?platform=mac|windows -- what the apps' updaters ask first.

Every Mac and Windows app used to call api.github.com itself (60 requests/hour per public
address, shared by a whole campus). Now they ask this server, which asks GitHub at most once
every REFRESH_S and keeps the last good answer.

Route: GET /api/app/update?platform=mac|windows[&version=x] (alias: /api/app/latest). No auth,
never 426, always a Date header, strong ETag + 304, Cache-Control max-age=60. The JSON shape is in
docs/APP-UPDATE-CONTRACT.md. HTTP 404 = nothing known (clients then ask GitHub themselves).
FAIRPLAY_LATEST_APP_VERSION_<P>, FAIRPLAY_APP_UPDATE_URL_<P>, FAIRPLAY_APP_UPDATE_SHA256_<P>
(and ..._SETUP_URL_WINDOWS, ..._PACKAGES_WINDOWS) win over what GitHub says.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .config import Settings
from .store import Store
from .timeutil import iso, utcnow

REFRESH_S = 600
CACHE_KEY = "appupd:gh"           # last good GitHub answer (json), no expiry that matters
TRY_KEY = "appupd:try"            # one worker asks GitHub per REFRESH_S window
ASSETS = {"mac": "FairPlay-Mac.zip", "windows": "FairPlay-Windows.zip"}
PLATFORMS = tuple(ASSETS)
_VERSION = re.compile(r"^\d{1,9}(\.\d{1,9}){2,3}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def asset_name(platform: str) -> str:
    return ASSETS[platform]


def parse_release(obj: object, repo: str, platform: str) -> dict | None:
    """GitHub's releases/latest JSON -> {version, url, sha256} for one platform, or None
    when anything is off (draft, odd tag, no single asset, URL outside the repo, no digest)."""
    if not isinstance(obj, dict) or obj.get("draft") or obj.get("prerelease"):
        return None
    tag = obj.get("tag_name")
    if not isinstance(tag, str):
        return None
    version = tag[1:] if tag.startswith("v") else tag
    if not _VERSION.match(version):
        return None
    name = asset_name(platform)
    assets = [a for a in (obj.get("assets") or []) if isinstance(a, dict) and a.get("name") == name]
    if len(assets) != 1 or assets[0].get("state", "uploaded") != "uploaded":
        return None
    url = assets[0].get("browser_download_url")
    if url != f"https://github.com/{repo}/releases/download/{tag}/{name}":
        return None
    digest = str(assets[0].get("digest") or "").lower()
    if not digest.startswith("sha256:") or not _SHA.match(digest[7:]):
        return None
    return {"version": version, "url": url, "sha256": digest[7:]}


async def _fetch_github(settings: Settings) -> dict | None:
    repo = settings.app_update_github
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "FairPlay-server"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
        if r.status_code != 200:
            return None
        obj = r.json()
    except Exception:
        return None
    out = {p: parse_release(obj, repo, p) for p in PLATFORMS}
    return out if any(out.values()) else None


async def github_copy(store: Store, settings: Settings, fetch=None) -> dict:
    """The cached GitHub answer ({platform: {...}|None}); refreshed when older than REFRESH_S.
    A failed refresh keeps the last good copy."""
    repo = settings.app_update_github
    raw = await store.get(CACHE_KEY)
    cached = json.loads(raw) if raw else None
    if not repo or repo.lower() == "off":
        return (cached or {}).get("data", {})
    if cached and time.time() - cached.get("at", 0) < REFRESH_S:
        return cached["data"]
    if not await store.setnx(TRY_KEY, "1", REFRESH_S):
        return (cached or {}).get("data", {})     # another worker is on it
    data = await (fetch or _fetch_github)(settings)
    if data is None:
        return (cached or {}).get("data", {})
    await store.set(CACHE_KEY, json.dumps({"at": time.time(), "data": data}), 30 * 86400)
    return data


def _own_host(request: Request, settings: Settings) -> set[str]:
    from urllib.parse import urlsplit
    hosts = {(request.url.hostname or "").lower()}
    if settings.public_url:
        hosts.add((urlsplit(settings.public_url).hostname or "").lower())
    hosts.discard("")
    return hosts


def own_host_url(value: str, hosts: set[str], allow_http: bool) -> str:
    """`value` when it is a path ("/app/windows/") or an https URL on this server's own host
    without credentials (http only on a dev server); else "" (the client would ignore it)."""
    from urllib.parse import urlsplit
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    u = urlsplit(value)
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not allow_http):
        return ""
    if u.username or u.password or (u.hostname or "").lower() not in hosts:
        return ""
    return value


def _sha(value: str) -> str:
    v = (value or "").strip().lower()
    return v if _SHA.match(v) else ""


async def manifest(request: Request, store: Store, settings: Settings, platform: str, fetch=None) -> dict | None:
    """The contract of docs/APP-UPDATE-CONTRACT.md. Operator env values first, then GitHub's
    (cached) release; empty fields are left out."""
    gh = (await github_copy(store, settings, fetch)).get(platform) or {}
    version = getattr(settings, f"latest_app_version_{platform}") or gh.get("version", "")
    pinned_url = getattr(settings, f"app_update_url_{platform}")
    pinned_sha = _sha(getattr(settings, f"app_update_sha256_{platform}"))
    hosts = _own_host(request, settings)
    out: dict = {"platform": platform}
    if version:
        out["latest_version"] = version
    minimum = settings.min_app_version(platform) or ""
    if minimum:
        out["min_version"] = minimum
    if platform == "windows":
        feed = own_host_url(pinned_url, hosts, settings.dev)
        setup = own_host_url(settings.app_update_setup_url_windows, hosts, settings.dev)
        if feed:
            out["feed_url"] = feed
        if setup:
            out["setup_url"] = setup
        if pinned_sha:
            out["setup_sha256"] = pinned_sha
        try:
            pk = json.loads(settings.app_update_packages_windows or "{}")
        except ValueError:
            pk = {}
        pk = {k: _sha(v) for k, v in pk.items() if isinstance(k, str) and _sha(str(v))} if isinstance(pk, dict) else {}
        if pk:
            out["package_sha256"] = pk
    else:
        # mac: setup_url is the FairPlay-Mac.zip (GitHub release path or this server) and
        # setup_sha256 its SHA-256; the app checks both again. A hand-pinned version never
        # borrows another release's file.
        borrowed = not pinned_url and not pinned_sha and (not getattr(settings, "latest_app_version_mac") or
                                                          getattr(settings, "latest_app_version_mac") == gh.get("version"))
        url = pinned_url or (gh.get("url", "") if borrowed else "")
        sha = pinned_sha or (gh.get("sha256", "") if borrowed else "")
        if url and sha:
            out["setup_url"], out["setup_sha256"] = url, sha
    return out if len(out) > 1 else None


async def update_response(request: Request, store: Store, settings: Settings, platform: str, fetch=None) -> Response:
    from email.utils import formatdate
    base = {"Date": formatdate(usegmt=True)}      # the apps' server-corrected clocks read it
    if platform not in PLATFORMS:
        return JSONResponse({"detail": "Unknown platform"}, status_code=404, headers=base)
    body = await manifest(request, store, settings, platform, fetch)
    if body is None:
        return JSONResponse({"detail": "No release known"}, status_code=404, headers=base)
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32] + '"'
    headers = {**base, "ETag": etag, "Cache-Control": "max-age=60"}
    if request.headers.get("if-none-match", "").strip() == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(body, headers=headers)

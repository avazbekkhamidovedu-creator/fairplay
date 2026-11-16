"""Pilot 0.8.8: GET /api/app/latest?platform=mac|windows -- what the apps' updaters ask first.

Every Mac and Windows app used to call api.github.com itself (60 requests/hour per public
address, shared by a whole campus). Now they ask this server, which asks GitHub at most once
every REFRESH_S and keeps the last good answer.

Answer (JSON, no auth, strong ETag, 304 on If-None-Match, Cache-Control max-age=300):
    {"platform": "mac", "version": "0.8.8", "url": "https://github.com/<owner>/<repo>/releases/download/v0.8.8/FairPlay-Mac.zip",
     "sha256": "<64 hex>", "min_version": "<MIN_APP_VERSION_MAC or empty>", "checked_at": "<ISO UTC>"}
`version` empty / HTTP 404: nothing known (clients then ask GitHub themselves). `url` or
`sha256` empty: the server knows a version but not a verified file; clients offer no install.
`checked_at` is not part of the ETag. FAIRPLAY_LATEST_APP_VERSION_<P>, FAIRPLAY_APP_UPDATE_URL_<P>
and FAIRPLAY_APP_UPDATE_SHA256_<P> override the GitHub values field by field.
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


def _override(settings: Settings, platform: str) -> dict:
    return {"version": getattr(settings, f"latest_app_version_{platform}"),
            "url": getattr(settings, f"app_update_url_{platform}"),
            "sha256": getattr(settings, f"app_update_sha256_{platform}")}


async def manifest(store: Store, settings: Settings, platform: str, fetch=None) -> dict | None:
    pinned = _override(settings, platform)
    gh = (await github_copy(store, settings, fetch)).get(platform) or {}
    out = {"platform": platform}
    for k in ("version", "url", "sha256"):
        out[k] = pinned[k] or gh.get(k, "")
    # a hand-pinned version with no hand-pinned file must not borrow a different release's file
    if pinned["version"] and pinned["version"] != gh.get("version") and not (pinned["url"] and pinned["sha256"]):
        out["url"] = pinned["url"]
        out["sha256"] = pinned["sha256"]
    if not out["version"]:
        return None
    out["min_version"] = settings.min_app_version(platform) or ""
    return out


async def latest_response(request: Request, store: Store, settings: Settings, platform: str, fetch=None) -> Response:
    if platform not in PLATFORMS:
        return JSONResponse({"detail": "Unknown platform"}, status_code=404)
    body = await manifest(store, settings, platform, fetch)
    if body is None:
        return JSONResponse({"detail": "No release known"}, status_code=404)
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300"}
    if request.headers.get("if-none-match", "").strip() == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse({**body, "checked_at": iso(utcnow())}, headers=headers)

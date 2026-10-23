"""HTTP client for DKU OJ's /api/fairplay/v1/ (Bearer token, timeouts, retries).

Tests route the traffic through a fake DKU OJ with `set_transport_factory` (an
httpx.MockTransport), like app.lms.base, and make retries instant with `set_sleep`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import httpx

log = logging.getLogger("proctora.dkuoj")

API = "/api/fairplay/v1"
TIMEOUT_S = 20.0
MAX_ATTEMPTS = 3                 # 1 try + 2 retries on 429 / 5xx / network errors
BACKOFF_BASE_S = 1.0
MAX_RETRY_AFTER_S = 10.0
MAX_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_ASSET_BYTES = 10 * 1024 * 1024

TransportFactory = Callable[[str], httpx.AsyncBaseTransport | None]
_transport_factory: TransportFactory | None = None
_sleep: Callable[[float], Any] = asyncio.sleep


def set_transport_factory(factory: TransportFactory | None) -> None:
    """Route DKU OJ traffic through factory(base_url) -> transport (tests only)."""
    global _transport_factory
    _transport_factory = factory


def set_sleep(fn: Callable[[float], Any] | None) -> None:
    global _sleep
    _sleep = fn or asyncio.sleep


async def sleep(seconds: float) -> None:
    await _sleep(seconds)


class OjError(Exception):
    """DKU OJ failed or refused. status None = no answer (network / timeout)."""

    def __init__(self, message: str, status: int | None = None, body: Any = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500 or self.status == 408


def _retry_after(resp: httpx.Response) -> float | None:
    ra = resp.headers.get("retry-after")
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except ValueError:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        try:
            return max(0.0, (parsedate_to_datetime(ra) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


class DkuojClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        transport = _transport_factory(self.base_url) if _transport_factory else None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_S), transport=transport, follow_redirects=False,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "User-Agent": "FairPlay-DKUOJ-Bridge/1.0"})

    @classmethod
    def from_settings(cls, settings) -> "DkuojClient":
        return cls(settings.dkuoj_url, settings.dkuoj_token)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      json: Any = None, content: bytes | None = None,
                      headers: dict | None = None, attempts: int = MAX_ATTEMPTS,
                      ok: tuple[int, ...] = (200,)) -> httpx.Response:
        """One call with retries on 429 / 5xx / network errors. Returns the response when
        its status is in `ok`; raises OjError otherwise."""
        url = f"{self.base_url}{API}/{path.lstrip('/')}"
        err: OjError | None = None
        for attempt in range(max(1, attempts)):
            try:
                resp = await self._client.request(method, url, params=params, json=json,
                                                  content=content, headers=headers)
            except httpx.TimeoutException:
                err = OjError("DKU OJ did not answer in time")
            except httpx.HTTPError as exc:
                err = OjError(f"Could not reach DKU OJ ({type(exc).__name__})")
            else:
                if resp.status_code in ok:
                    return resp
                body = None
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text[:300]
                err = OjError(f"DKU OJ answered HTTP {resp.status_code}", resp.status_code, body,
                              _retry_after(resp))
                if not err.retryable:
                    raise err
            if attempt + 1 < attempts:
                wait = err.retry_after if err.retry_after is not None else BACKOFF_BASE_S * (2 ** attempt)
                await sleep(min(wait, MAX_RETRY_AFTER_S))
        assert err is not None
        raise err

    @staticmethod
    def _json(resp: httpx.Response, kind: type = dict) -> Any:
        try:
            v = resp.json()
        except ValueError:
            raise OjError("DKU OJ sent something that is not JSON", resp.status_code) from None
        if not isinstance(v, kind):
            raise OjError("DKU OJ sent an unexpected answer", resp.status_code)
        return v

    # ------------------------------------------------------------ endpoints (contract §4)
    async def list_exams(self, changed_since: str | None, page: int = 1) -> dict:
        params: dict = {"page": page}
        if changed_since:
            params["changed_since"] = changed_since
        return self._json(await self.request("GET", "exams", params=params))

    async def package(self, key: str, etag: str | None = None) -> tuple[int, dict | None, float | None]:
        """(status, package or None, retry_after). 200 = new package, 304 = unchanged,
        425 = too early (retry_after seconds), 404 = not a FairPlay exam (any more)."""
        headers = {"If-None-Match": f'"{etag}"'} if etag else None
        try:
            resp = await self.request("GET", f"exams/{key}/package", headers=headers,
                                      ok=(200, 304, 404, 425))
        except OjError:
            raise
        if resp.status_code == 200:
            if len(resp.content) > MAX_PACKAGE_BYTES:
                raise OjError("The exam package is larger than 5 MB", 200)
            return 200, self._json(resp), None
        return resp.status_code, None, _retry_after(resp)

    async def asset(self, key: str, name: str) -> tuple[bytes, str]:
        resp = await self.request("GET", f"exams/{key}/assets/{name}")
        if len(resp.content) > MAX_ASSET_BYTES:
            raise OjError(f"The file {name} is larger than 10 MB", 200)
        return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()

    async def upload(self, key: str, username: str, question_id: str, filename: str,
                     data: bytes, sha256: str, attempts: int = MAX_ATTEMPTS) -> str:
        resp = await self.request(
            "POST", f"exams/{key}/uploads", content=data, attempts=attempts,
            ok=(200, 201),
            headers={"X-Username": username, "X-Question-Id": question_id,
                     "X-Filename": _header_safe(filename), "X-Sha256": sha256,
                     "Content-Type": "application/octet-stream"})
        up = self._json(resp).get("upload_id")
        if not isinstance(up, str) or not up:
            raise OjError("DKU OJ did not return an upload id", resp.status_code)
        return up

    async def submit(self, key: str, body: dict, attempts: int = MAX_ATTEMPTS) -> dict:
        resp = await self.request("POST", f"exams/{key}/submissions", json=body, attempts=attempts)
        return self._json(resp)

    async def results(self, key: str, changed_since: str | None, page: int = 1) -> dict:
        params: dict = {"page": page}
        if changed_since:
            params["changed_since"] = changed_since
        return self._json(await self.request("GET", f"exams/{key}/results", params=params))


def _header_safe(name: str) -> str:
    """HTTP header values are latin-1: keep ASCII, replace the rest (the extension stays)."""
    out = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in name)
    return out[:200] or "file"

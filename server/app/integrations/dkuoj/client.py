"""HTTP client for DKU OJ's /api/fairplay/v1/ (signed fp->oj, timeouts, retries).

Every request is signed (signing.py, docs/dkuoj-bridge-v2.md §1); a retry is signed again
with a fresh timestamp and nonce.

Tests route the traffic through a fake DKU OJ with `set_transport_factory` (an
httpx.MockTransport), like app.lms.base, and make retries instant with `set_sleep`.
"""
from __future__ import annotations

import asyncio
import logging
import zlib
from typing import Any, Callable

import httpx

from . import signing

log = logging.getLogger("proctora.dkuoj")

API = "/api/fairplay/v1"
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 15.0
MAX_ATTEMPTS = 3                 # 1 try + 2 retries on 429 / 5xx / network errors
BACKOFF_BASE_S = 1.0
MAX_RETRY_AFTER_S = 10.0
MAX_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_ASSET_BYTES = 10 * 1024 * 1024
# Security review 2026-10-24: every answer is read as a stream and cut off past a cap counted
# on the DEcompressed bytes (a 1 MiB gzip bomb used to become 1-4 GiB in memory), and every
# try has a total deadline (a body trickled one byte per 2 s used to beat the read timeout).
MAX_JSON_BYTES = 2 * 1024 * 1024      # exam list / roster / student classes / results pages
MAX_SMALL_BYTES = 256 * 1024          # upload / submission receipts
MAX_ERROR_BYTES = 64 * 1024           # the body of a refusal (only its first 300 chars are kept)
MAX_TOTAL_S = 30.0                    # per try: send + whole body, at most
DJANGO_PATH_SAFE = ":@&+$,-_.!~*'()"      # django.utils.encoding.escape_uri_path, minus "/"

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

    @property
    def auth_class(self) -> bool:
        """Refusals that clock skew or a token rotation mismatch cause and a fix on either side clears."""
        return self.status in (401, 403, 404)


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
        self._token = token
        transport = _transport_factory(self.base_url) if _transport_factory else None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S), transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json", "Accept-Encoding": "identity",
                     "User-Agent": "FairPlay-DKUOJ-Bridge/2.0"})

    @classmethod
    def from_config(cls, cfg) -> "DkuojClient":
        return cls(cfg.url, cfg.token)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      json: Any = None, content: bytes | None = None,
                      headers: dict | None = None, attempts: int = MAX_ATTEMPTS,
                      ok: tuple[int, ...] = (200,), timeout: float | None = None,
                      max_bytes: int = MAX_JSON_BYTES, too_big: str | None = None) -> httpx.Response:
        """One call with retries on 429 / 5xx / network errors. Returns the response (body
        read) when its status is in `ok`; raises OjError otherwise. `timeout` (seconds)
        replaces the default connect / read timeouts for this call (a page someone is
        waiting on). Each try ends after min(2 x timeout, MAX_TOTAL_S) whatever the server
        does; a body larger than `max_bytes` once decompressed is refused (`too_big`)."""
        url = f"{self.base_url}{API}/{path.lstrip('/')}"
        total = min(MAX_TOTAL_S, 2 * (timeout or READ_TIMEOUT_S))
        err: OjError | None = None
        for attempt in range(max(1, attempts)):
            try:
                req = self._client.build_request(
                    method, url, params=params, json=json, content=content, headers=headers,
                    timeout=(httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_S, timeout))
                             if timeout else httpx.USE_CLIENT_DEFAULT))
                body = req.read()
                path = req.url.raw_path.decode("ascii")
                req.headers.update(signing.sign_headers(self._token, signing.FP_TO_OJ, method, path, body))
                resp = await asyncio.wait_for(self._send(req, ok, max_bytes, too_big), total)
            except (httpx.TimeoutException, asyncio.TimeoutError):
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

    async def _send(self, req: httpx.Request, ok: tuple[int, ...], max_bytes: int,
                    too_big: str | None) -> httpx.Response:
        """Send and read the body as a stream, at most `max_bytes` (MAX_ERROR_BYTES for a
        refusal) after decompression. Returns a plain, fully read Response."""
        resp = await self._client.send(req, stream=True)
        try:
            cap = max_bytes if resp.status_code in ok else MAX_ERROR_BYTES
            data = await _read_capped(resp, cap)
        finally:
            await resp.aclose()
        if data is None:
            if resp.status_code in ok:
                raise OjError(too_big or "DKU OJ sent an answer that is too large", resp.status_code)
            data = b""                       # an oversized refusal: its status is enough
        hdrs = [(k, v) for k, v in resp.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(resp.status_code, headers=hdrs, content=data, request=req)

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
        resp = await self.request("GET", f"exams/{key}/package", headers=headers,
                                  ok=(200, 304, 404, 425), max_bytes=MAX_PACKAGE_BYTES,
                                  too_big="The exam package is larger than 5 MB")
        if resp.status_code == 200:
            return 200, self._json(resp), None
        return resp.status_code, None, _retry_after(resp)

    async def asset(self, key: str, name: str) -> tuple[bytes, str]:
        resp = await self.request("GET", f"exams/{key}/assets/{name}", max_bytes=MAX_ASSET_BYTES,
                                  too_big=f"The file {name} is larger than 10 MB")
        return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()

    async def upload(self, key: str, username: str, question_id: str, filename: str,
                     data: bytes, sha256: str, attempts: int = MAX_ATTEMPTS) -> str:
        # who / which question / file name go in the query string, which the signature
        # covers (v2 §1); the file's sha256 stays a header (DKU OJ checks it against the
        # signed body).
        resp = await self.request(
            "POST", f"exams/{key}/uploads", content=data, attempts=attempts,
            ok=(200, 201), max_bytes=MAX_SMALL_BYTES,
            params={"username": username, "question_id": question_id, "filename": _filename_safe(filename)},
            headers={"X-Sha256": sha256, "Content-Type": "application/octet-stream"})
        up = self._json(resp).get("upload_id")
        if not isinstance(up, str) or not up:
            raise OjError("DKU OJ did not return an upload id", resp.status_code)
        return up

    async def submit(self, key: str, body: dict, attempts: int = MAX_ATTEMPTS) -> dict:
        resp = await self.request("POST", f"exams/{key}/submissions", json=body, attempts=attempts,
                                  max_bytes=MAX_SMALL_BYTES)
        return self._json(resp)

    async def roster(self, class_id: int) -> dict:
        """GET classes/<id>/roster (v2 §2): {class, course, students: [{username, first_name,
        last_name}]}. One try: a student is waiting on the set-up screen."""
        return self._json(await self.request("GET", f"classes/{int(class_id)}/roster", attempts=1))

    async def student_classes(self, netid: str, timeout: float = 3.0) -> dict:
        """GET students/<netid>/classes (v2 §2): {classes: [{id, name, course: {id, name},
        teachers: [..]}]}. One try with a short timeout: a student's home page waits on it."""
        from urllib.parse import quote
        # the same escaping as Django's request.get_full_path() (which DKU OJ signs against)
        path = "students/" + quote(netid, safe=DJANGO_PATH_SAFE) + "/classes"
        return self._json(await self.request("GET", path,
                                             attempts=1, timeout=timeout))

    async def redeem_app_login(self, ticket: str, verifier: str, timeout: float = 10.0) -> tuple[int, dict | None]:
        """POST app-login/redeem (homework sign-in): (200, {netid, first_name, last_name,
        email}) or (404 unknown / used / expired ticket | 409 verifier mismatch, None). One
        try: a ticket works once, so a repeat after a lost answer could only fail."""
        resp = await self.request("POST", "app-login/redeem", json={"ticket": ticket, "verifier": verifier},
                                  attempts=1, ok=(200, 404, 409), timeout=timeout, max_bytes=MAX_SMALL_BYTES)
        return (200, self._json(resp)) if resp.status_code == 200 else (resp.status_code, None)

    async def results(self, key: str, changed_since: str | None, page: int = 1) -> dict:
        params: dict = {"page": page}
        if changed_since:
            params["changed_since"] = changed_since
        return self._json(await self.request("GET", f"exams/{key}/results", params=params))


async def _read_capped(resp: httpx.Response, cap: int) -> bytes | None:
    """The body, decompressed, or None once it passes `cap` bytes. Decoded here from the raw
    stream with zlib's max_length, so one small compressed chunk can never expand past the
    cap in memory either. An encoding other than gzip / deflate / identity is refused."""
    try:                      # already in memory (a test transport's prepared Response)
        done = resp.content
    except httpx.ResponseNotRead:
        pass
    else:
        return done if len(done) <= cap else None
    enc = [e.strip().lower() for e in resp.headers.get("content-encoding", "").split(",") if e.strip()]
    enc = [e for e in enc if e != "identity"]
    if len(enc) > 1 or (enc and enc[0] not in ("gzip", "x-gzip", "deflate")):
        raise OjError("DKU OJ sent an answer in an encoding FairPlay does not read", resp.status_code)
    if enc:
        wbits = 16 + zlib.MAX_WBITS if enc[0] in ("gzip", "x-gzip") else zlib.MAX_WBITS
        dec = zlib.decompressobj(wbits)
    else:
        dec = None
    out = bytearray()
    try:
        async for raw in resp.aiter_raw():
            if dec is None:
                out += raw
            else:
                buf = raw
                while buf:
                    # never more than what is left of the cap (+1, to notice going over)
                    piece = dec.decompress(buf, max(1, cap - len(out) + 1))
                    out += piece
                    if len(out) > cap:
                        return None
                    buf = dec.unconsumed_tail
                    if not piece and buf:
                        break
            if len(out) > cap:
                return None
        if dec is not None:
            out += dec.flush(max(1, cap - len(out) + 1))
    except zlib.error:
        raise OjError("DKU OJ sent an answer FairPlay could not decompress", resp.status_code) from None
    if len(out) > cap:
        return None
    return bytes(out)


def _filename_safe(name: str) -> str:
    """No control characters, at most 200 characters (the extension stays at the end)."""
    out = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127)
    return out[:200] or "file"

"""The built web app (server/static): cache headers, conditional requests and compression.

perf 0.6.2: at exam start every laptop loads the exam page (about 1.3 MB of JavaScript and
CSS, 2 MB with the code editor). Before, each start downloaded all of it again, uncompressed:
no Cache-Control, and no 304 for If-None-Match. Now:

  /assets/*   file names carry a content hash (Vite): "public, max-age=31536000, immutable",
              so a laptop that has them never asks again
  index.html  "no-cache": always revalidated (a new release is picked up at once), 304
              when unchanged
  the rest    (favicon, /app-assets/, pdfjs, pyodide) one hour, then revalidated (ETag)

Text files (JavaScript, CSS, HTML, JSON, SVG, WebAssembly, fonts without their own
compression) are sent gzip-compressed when the client accepts it: a precompressed
"<file>.br" / "<file>.gz" next to the file is used when present, otherwise the file is
compressed once per process and kept in memory (bounded). Behind nginx or a CDN these
headers let the proxy cache everything (nginx.conf).
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import mimetypes
import os
from email.utils import formatdate, parsedate_to_datetime

from starlette.requests import Request
from starlette.responses import FileResponse, Response

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
HOURLY = "public, max-age=3600"
COMPRESSIBLE = {".js", ".mjs", ".css", ".html", ".svg", ".json", ".txt", ".map", ".wasm",
                ".ttf", ".pfb", ".xml", ".webmanifest"}
MAX_COMPRESS_BYTES = 32 * 1024 * 1024        # larger files go as they are
MEMORY_BUDGET = 96 * 1024 * 1024             # compressed copies kept per process
MIN_COMPRESS_BYTES = 1024

# 2026-10-23: module scripts (pdf.js worker) need a JavaScript type, Pyodide's
# WebAssembly needs application/wasm (compiled while streaming).
TYPES = {".mjs": "text/javascript", ".wasm": "application/wasm", ".js": "text/javascript"}


def media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in TYPES:
        return TYPES[ext]
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def cache_control(rel_path: str, is_index: bool) -> str:
    if is_index:
        return REVALIDATE
    if rel_path.startswith("assets/"):
        return IMMUTABLE
    return HOURLY


class _Compressed:
    __slots__ = ("key", "body")

    def __init__(self, key: tuple, body: bytes):
        self.key, self.body = key, body


class StaticCache:
    """Per-process cache of gzip bodies, keyed by (path, size, mtime)."""

    def __init__(self, budget: int = MEMORY_BUDGET):
        self.budget = budget
        self.used = 0
        self.items: dict[str, _Compressed] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def gzip_body(self, path: str, st: os.stat_result) -> bytes | None:
        key = (st.st_size, st.st_mtime_ns)
        hit = self.items.get(path)
        if hit is not None and hit.key == key:
            return hit.body
        lock = self.locks.setdefault(path, asyncio.Lock())
        async with lock:                       # one compression per file, not one per request
            hit = self.items.get(path)
            if hit is not None and hit.key == key:
                return hit.body
            body = await asyncio.to_thread(_gzip_file, path)
            if hit is not None:
                self.used -= len(hit.body)
                del self.items[path]
            if self.used + len(body) <= self.budget:
                self.items[path] = _Compressed(key, body)
                self.used += len(body)
            return body


def _gzip_file(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    # mtime=0: the same bytes in every process and container (stable ETag, cacheable)
    return gzip.compress(data, compresslevel=9, mtime=0)


def etag_of(st: os.stat_result, variant: str = "") -> str:
    h = hashlib.md5(f"{st.st_mtime_ns}-{st.st_size}".encode(), usedforsecurity=False).hexdigest()
    return f'"{h}{"-" + variant if variant else ""}"'


def _accepts(request: Request, coding: str) -> bool:
    for part in request.headers.get("accept-encoding", "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() == coding:
            q = params.strip()
            return not (q.startswith("q=") and q[2:].strip() in ("0", "0.0", "0.00", "0.000"))
    return False


def _not_modified(request: Request, etags: list[str], st: os.stat_result) -> bool:
    inm = request.headers.get("if-none-match")
    if inm is not None:
        given = {t.strip().removeprefix("W/") for t in inm.split(",")}
        return "*" in given or any(t in given for t in etags)
    ims = request.headers.get("if-modified-since")
    if ims:
        try:
            return int(st.st_mtime) <= int(parsedate_to_datetime(ims).timestamp())
        except (TypeError, ValueError, IndexError, OverflowError):
            return False
    return False


async def serve(request: Request, path: str, rel_path: str, cache: StaticCache,
                is_index: bool = False) -> Response:
    """A file from the static directory with caching headers (see the module doc)."""
    st = os.stat(path)
    ctype = media_type(path)
    headers = {"Cache-Control": cache_control(rel_path, is_index),
               "Last-Modified": formatdate(st.st_mtime, usegmt=True)}
    ext = os.path.splitext(path)[1].lower()
    compressible = ext in COMPRESSIBLE and MIN_COMPRESS_BYTES <= st.st_size <= MAX_COMPRESS_BYTES
    if compressible:
        headers["Vary"] = "Accept-Encoding"
    variants = [etag_of(st), etag_of(st, "gzip"), etag_of(st, "br")]
    if _not_modified(request, variants, st):
        # the ETag of the variant this client would get (they validate the same file)
        return Response(status_code=304, headers={**headers, "ETag": variants[0]})
    if compressible:
        for coding, suffix in (("br", ".br"), ("gzip", ".gz")):
            side = path + suffix
            if _accepts(request, coding) and os.path.isfile(side):
                sst = os.stat(side)
                if sst.st_mtime_ns >= st.st_mtime_ns:
                    return FileResponse(side, media_type=ctype, stat_result=sst, headers={
                        **headers, "Content-Encoding": coding, "ETag": etag_of(st, coding)})
        if _accepts(request, "gzip"):
            body = await cache.gzip_body(path, st)
            if body is not None and len(body) < st.st_size:
                return Response(body, media_type=ctype, headers={
                    **headers, "Content-Encoding": "gzip", "ETag": etag_of(st, "gzip")})
    return FileResponse(path, media_type=ctype, stat_result=st, headers={**headers, "ETag": etag_of(st)})

"""Client for the external code runner (fp-runner) and the routes that use it.

The server never executes code. It sends a program and the inputs to the runner over signed
HTTP and gets back what the program printed. Hidden tests and expected outputs never leave
this server: outputs are compared here. Nothing about a program (source, input, output) is
logged.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from . import app_client as ac
from .config import Settings
from .db import get_session
from .models import Attempt, Exam
from .security import Principal, require_student, require_teacher
from .services import code_language, exam_default_language, get_attempt_meta
from .store import get_store
from .timeutil import arrived_at

log = logging.getLogger("proctora.runner")

REMOTE_LANGUAGES = ("c", "cpp", "java")
UNAVAILABLE = "The code runner is not available right now"
LANGUAGES_TTL_S = 60
LANGUAGES_FAIL_TTL_S = 10
MAX_RETRY_WAIT_S = 5.0
RUNNER_MAX_STDINS = 30
DEFAULT_TIME_LIMIT_MS = 2000
DEFAULT_MEMORY_KB = 262_144
MIN_MEMORY_KB, MAX_MEMORY_KB = 16_384, 1_048_576
MAX_SOURCE = 50_000
MAX_STDIN = 20_000
MAX_STDINS = 20
RUN_GAP_S = 3
RUNS_PER_HOUR = 120
TEACHER_OUTPUT_CHARS = 2000

# tests replace this to route the client into an in-process fake runner
TRANSPORT: httpx.AsyncBaseTransport | None = None


class RunnerUnavailable(Exception):
    pass


def sign(secret: str, ts: int, nonce: str, body: bytes) -> str:
    msg = f"{ts}.{nonce}.{hashlib.sha256(body).hexdigest()}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class Runner:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._url = settings.runner_url
        self._tenant = settings.runner_tenant
        self._secret = settings.runner_secret
        self._timeout = float(settings.runner_timeout_s)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._langs: tuple[float, list[str]] | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport, timeout=httpx.Timeout(self._timeout, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=32),
                follow_redirects=False)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self, body: bytes) -> dict[str, str]:
        ts, nonce = int(time.time()), secrets.token_urlsafe(24)
        return {"X-FP-Tenant": self._tenant, "X-FP-Ts": str(ts), "X-FP-Nonce": nonce,
                "X-FP-Sig": sign(self._secret, ts, nonce, body), "Content-Type": "application/json"}

    async def _send(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        for attempt in (1, 2):
            try:
                r = await self._http().request(method, self._url + path, content=body or None,
                                               headers=self._headers(body))
            except httpx.HTTPError as exc:
                log.warning("code runner request failed (%s)", type(exc).__name__)
                raise RunnerUnavailable() from None
            if r.status_code == 503 and attempt == 1:
                try:
                    wait = float(r.headers.get("Retry-After", "1"))
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(min(max(wait, 0.0), MAX_RETRY_WAIT_S))
                continue
            if r.status_code != 200:
                log.warning("code runner answered %s", r.status_code)
                raise RunnerUnavailable()
            try:
                data = r.json()
            except ValueError:
                raise RunnerUnavailable() from None
            if not isinstance(data, dict):
                raise RunnerUnavailable()
            return data
        raise RunnerUnavailable()

    async def available_languages(self) -> list[str]:
        now = time.monotonic()
        if self._langs and self._langs[0] > now:
            return list(self._langs[1])
        try:
            data = await self._send("GET", "/v1/languages")
            keys = [x["key"] for x in data.get("languages", [])
                    if isinstance(x, dict) and x.get("key") in REMOTE_LANGUAGES]
        except (RunnerUnavailable, KeyError, TypeError):
            self._langs = (now + LANGUAGES_FAIL_TTL_S, [])
            return []
        self._langs = (now + LANGUAGES_TTL_S, keys)
        return list(keys)

    async def run(self, language: str, source: str, stdins: list[str], time_limit_ms: int,
                  memory_limit_kb: int) -> dict:
        data = await self._send("POST", "/v1/run", {
            "language": language, "source": source, "stdins": stdins,
            "time_limit_ms": time_limit_ms, "memory_limit_kb": memory_limit_kb})
        results = data.get("results")
        if data.get("status") not in ("ok", "compile_error") or not isinstance(results, list):
            raise RunnerUnavailable()
        if data["status"] == "ok" and len(results) != len(stdins):
            raise RunnerUnavailable()
        return data


_runner: Runner | None = None


def configure(settings: Settings) -> None:
    global _runner
    _runner = Runner(settings, TRANSPORT) if settings.runner_on else None


async def shutdown() -> None:
    global _runner
    r, _runner = _runner, None
    if r is not None:
        await r.close()


def get_runner() -> Runner | None:
    return _runner


async def available_languages() -> list[str]:
    return await _runner.available_languages() if _runner else []


async def run(language: str, source: str, stdins: list[str], time_limit_ms: int,
              memory_limit_kb: int) -> dict:
    if _runner is None:
        raise RunnerUnavailable()
    return await _runner.run(language, source, stdins, time_limit_ms, memory_limit_kb)


async def health() -> dict | None:
    langs = await available_languages()
    return {"languages": langs} if langs else None


def normalize_output(s: str) -> str:
    """Same rule as web/src/lib/code/compare.ts: line endings unified, spaces and tabs at the
    end of each line ignored, empty lines at the very end ignored."""
    lines = [re.sub(r"[ \t\f\v]+\Z", "", l) for l in re.sub(r"\r\n?", "\n", s).split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)


class RunIn(BaseModel):
    source: str = Field(max_length=MAX_SOURCE)
    stdins: list[str] = Field(min_length=1, max_length=MAX_STDINS)

    def check(self) -> None:
        if any(len(s) > MAX_STDIN for s in self.stdins):
            raise HTTPException(422, f"Each input may have at most {MAX_STDIN} characters")


class RunTestsIn(BaseModel):
    source: str = Field(max_length=MAX_SOURCE)


def limits_of(q: dict) -> tuple[int, int]:
    try:
        t = int(q.get("time_limit_ms") or DEFAULT_TIME_LIMIT_MS)
    except (TypeError, ValueError):
        t = DEFAULT_TIME_LIMIT_MS
    try:
        m = int(q.get("memory_limit_kb") or DEFAULT_MEMORY_KB)
    except (TypeError, ValueError):
        m = DEFAULT_MEMORY_KB
    return min(max(t, 100), 10_000), min(max(m, MIN_MEMORY_KB), MAX_MEMORY_KB)


def _question(e: Exam, qid: str) -> dict:
    for q in e.questions or []:
        if isinstance(q, dict) and q.get("id") == qid:
            return q
    raise HTTPException(404, "Question not found")


def _remote_language(q: dict, e: Exam) -> str | None:
    if q.get("type") != "code" or isinstance(q.get("external_ref"), dict):
        return None
    lang = code_language(q, exam_default_language(e))
    return lang if lang in REMOTE_LANGUAGES else None


async def _require_language(lang: str | None) -> None:
    if lang is not None and _runner is not None:
        offered = await available_languages()
        if not offered:
            raise _unavailable()
        if lang in offered:
            return
    raise HTTPException(400, "This language cannot be run here")


def _unavailable() -> HTTPException:
    return HTTPException(503, UNAVAILABLE, headers={"Retry-After": "5"})


student_router = APIRouter(prefix="/api/student", tags=["student"])
teacher_router = APIRouter(prefix="/api/exams", tags=["teacher"])


@student_router.post("/attempts/{aid}/questions/{qid}/run")
async def student_run(aid: str, qid: str, body: RunIn, request: Request,
                      p: Principal = Depends(require_student),
                      session: AsyncSession = Depends(get_session)):
    body.check()
    now = arrived_at(request)
    store = get_store()
    meta = await get_attempt_meta(store, session, aid)
    if meta is None or meta["student_id"] != p.user_id:
        raise HTTPException(404, "Attempt not found")
    await ac.check_session(request, session, store, aid, meta)
    a = await session.get(Attempt, aid)
    if a is None or a.student_id != p.user_id:
        raise HTTPException(404, "Attempt not found")
    if a.status != "in_progress":
        raise HTTPException(409, "Attempt already submitted")
    if now >= a.deadline_at:
        raise HTTPException(409, "Time is up")
    e = await session.get(Exam, a.exam_id)
    if e is None:
        raise HTTPException(404, "Attempt not found")
    q = _question(e, qid)
    lang = _remote_language(q, e)
    await _require_language(lang)
    if not await store.setnx(f"coderun3:{aid}", "1", RUN_GAP_S):
        raise HTTPException(429, "Running too often, wait a moment", headers={"Retry-After": str(RUN_GAP_S)})
    if await store.incr(f"coderunh:{aid}", 3600) > RUNS_PER_HOUR:
        wait = max(1, await store.ttl(f"coderunh:{aid}"))
        raise HTTPException(429, "Too many runs for this exam, try again later",
                            headers={"Retry-After": str(wait)})
    t_ms, mem = limits_of(q)
    try:
        data = await run(lang, body.source, body.stdins, t_ms, mem)
    except RunnerUnavailable:
        raise _unavailable() from None
    results = [{"status": r.get("status"), "stdout": r.get("stdout", ""), "stderr": r.get("stderr", ""),
                "time_ms": r.get("time_ms", 0), "truncated": bool(r.get("truncated"))}
               for r in data["results"]]
    try:
        await ac.add_event(session, store, aid, a.exam_id, "code_run",
                           f"{lang} {data['status']} x{len(body.stdins)}")
    except Exception:  # noqa: BLE001
        log.warning("could not record a code run event")
    return JSONResponse({"status": data["status"], "compile_output": data.get("compile_output", ""),
                         "results": results})


def _clip(s: str) -> str:
    return s if len(s) <= TEACHER_OUTPUT_CHARS else s[:TEACHER_OUTPUT_CHARS]


@teacher_router.post("/{exam_id}/questions/{qid}/run-tests")
async def teacher_run_tests(exam_id: str, qid: str, body: RunTestsIn,
                            p: Principal = Depends(require_teacher),
                            session: AsyncSession = Depends(get_session)):
    from .routes_teacher import _own_exam
    e = await _own_exam(session, exam_id, p)
    q = _question(e, qid)
    lang = _remote_language(q, e)
    await _require_language(lang)
    tests = [t for t in q.get("tests") or [] if isinstance(t, dict)]
    if not tests:
        raise HTTPException(400, "This question has no tests")
    t_ms, mem = limits_of(q)
    inputs = [str(t.get("input", "")) for t in tests]
    status, compile_output, results = "ok", "", []
    try:
        for i in range(0, len(inputs), RUNNER_MAX_STDINS):
            data = await run(lang, body.source, inputs[i:i + RUNNER_MAX_STDINS], t_ms, mem)
            if data["status"] == "compile_error":
                status, compile_output, results = "compile_error", data.get("compile_output", ""), []
                break
            results += data["results"]
    except RunnerUnavailable:
        raise _unavailable() from None
    out = []
    passed_points = 0.0
    passed = 0
    for i, t in enumerate(tests):
        r = results[i] if i < len(results) else {}
        ok = bool(r) and r.get("status") == "ok" and outputs_match(r.get("stdout", ""), str(t.get("expected", "")))
        pts = t.get("points", 0) or 0
        passed += ok
        if ok:
            passed_points += pts
        out.append({"index": i, "visible": bool(t.get("visible")), "points": pts, "passed": ok,
                    "status": r.get("status") if r else "not_run",
                    "stdout": _clip(r.get("stdout", "")), "stderr": _clip(r.get("stderr", "")),
                    "time_ms": r.get("time_ms", 0)})
    total_q = q.get("points", 1)
    if status == "compile_error":
        earned = 0
    elif sum(t.get("points", 0) or 0 for t in tests) > 0:
        earned = passed_points
    else:
        earned = round(total_q * passed / len(tests), 2)
    return {"status": status, "compile_output": compile_output, "tests": out,
            "earned": min(earned, total_q), "total": total_q}

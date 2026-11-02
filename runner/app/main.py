import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import VERSION
from .auth import Authenticator
from .config import Settings
from .executor import DockerExecutor, Executor, LangInfo
from .languages import LANGUAGES

log = logging.getLogger("fp-runner")

MAX_BODY = 256 * 1024
MAX_SOURCE = 100_000
MAX_STDIN = 20_000


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str
    source: str = Field(max_length=MAX_SOURCE)
    stdins: list[str] = Field(min_length=1, max_length=30)
    time_limit_ms: int = Field(default=2000, ge=100, le=10000, strict=True)
    memory_limit_kb: int = Field(default=262144, ge=16384, le=1048576, strict=True)

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in LANGUAGES:
            raise ValueError("unsupported language")
        return v

    @field_validator("source")
    @classmethod
    def _nul(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("NUL in source")
        return v

    @field_validator("stdins")
    @classmethod
    def _stdins(cls, v: list[str]) -> list[str]:
        if any(len(s) > MAX_STDIN for s in v):
            raise ValueError("stdin too long")
        return v


def _json(status: int, body: dict, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=headers)


UNAUTH = {"error": "unauthorized"}


class Gate:
    def __init__(self, max_concurrent: int, tenant_concurrent: int, wait_s: float):
        self._global = asyncio.Semaphore(max_concurrent)
        self._tenant_cap = tenant_concurrent
        self._tenants: dict[str, asyncio.Semaphore] = {}
        self._wait = wait_s

    async def acquire(self, tenant: str) -> bool:
        sem_t = self._tenants.setdefault(tenant, asyncio.Semaphore(self._tenant_cap))
        deadline = time.monotonic() + self._wait
        try:
            await asyncio.wait_for(sem_t.acquire(), self._wait)
        except asyncio.TimeoutError:
            return False
        try:
            await asyncio.wait_for(self._global.acquire(), max(0.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            sem_t.release()
            return False
        return True

    def release(self, tenant: str) -> None:
        self._global.release()
        self._tenants[tenant].release()


def create_app(settings: Settings, executor: Executor | None = None, auth: Authenticator | None = None) -> FastAPI:
    executor = executor or DockerExecutor(settings)
    state = {"languages": {}}
    authn = auth or Authenticator(settings.tenants)
    gate = Gate(settings.max_concurrent, settings.tenant_concurrent, settings.queue_wait_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["languages"] = await executor.startup()
        log.info("ready languages=%s", ",".join(state["languages"]))
        yield
        closer = getattr(executor, "close", None)
        if closer:
            await closer()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def read_body(request: Request) -> bytes | None:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY:
            return None
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > MAX_BODY:
                return None
        return bytes(buf)

    @app.get("/v1/health")
    async def health():
        return {"ok": True, "languages": [k for k in LANGUAGES if k in state["languages"]], "version": VERSION}

    @app.get("/v1/languages")
    async def languages(request: Request):
        if authn.verify(request.headers, b"") is None:
            return _json(401, UNAUTH)
        items = [{"key": i.key, "label": i.label, "version": i.version}
                 for k in LANGUAGES if (i := state["languages"].get(k))]
        return {"languages": items}

    @app.post("/v1/run")
    async def run(request: Request):
        body = await read_body(request)
        if body is None:
            return _json(413, {"error": "payload too large"})
        tenant = authn.verify(request.headers, body)
        if tenant is None:
            return _json(401, UNAUTH)
        try:
            req = RunRequest.model_validate(json.loads(body))
        except (ValueError, ValidationError, RecursionError):
            return _json(422, {"error": "invalid request"})
        if req.language not in state["languages"]:
            return _json(422, {"error": "language unavailable"})
        if not await gate.acquire(tenant):
            return _json(503, {"error": "busy"}, {"Retry-After": "5"})
        started = time.monotonic()
        try:
            outcome = await executor.run(req.language, req.source, req.stdins,
                                         req.time_limit_ms, req.memory_limit_kb)
        finally:
            gate.release(tenant)
        log.info("run tenant=%s lang=%s status=%s runs=%d ms=%d", tenant, req.language,
                 outcome["status"], len(outcome["results"]), int((time.monotonic() - started) * 1000))
        return outcome

    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return create_app(Settings.from_env())

import asyncio
import json
import secrets
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.auth import sign
from app.config import Settings
from app.executor import LangInfo
from app.main import create_app

SECRET = "s" * 40
IMAGE = "fp-runner-runtime:1"


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.delay = 0.0
        self.outcome = None

    async def startup(self):
        return {
            "c": LangInfo("c", "C (gcc 14, C17)", "14.2.0"),
            "cpp": LangInfo("cpp", "C++ (g++ 14, C++20)", "14.2.0"),
        }

    async def run(self, language, source, stdins, time_limit_ms, memory_limit_kb):
        self.calls.append((language, source, stdins, time_limit_ms, memory_limit_kb))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.outcome:
            return self.outcome
        return {
            "status": "ok", "compile_output": "", "runtime_version": "gcc 14.2.0",
            "results": [{"status": "ok", "stdout": s, "stderr": "", "time_ms": 1, "exit_code": 0,
                         "truncated": False} for s in stdins],
        }

    async def sweep(self):
        return 0


def make_settings(**kw):
    base = dict(tenants={"t1": SECRET, "t2": "z" * 40}, max_concurrent=4, tenant_concurrent=4, queue_wait_s=1)
    base.update(kw)
    return Settings(**base)


class Signed:
    def __init__(self, client, tenant="t1", secret=SECRET):
        self.client, self.tenant, self.secret = client, tenant, secret

    def headers(self, body=b"", ts=None, nonce=None, tenant=None, secret=None, sig=None):
        ts = str(int(time.time())) if ts is None else str(ts)
        nonce = nonce or secrets.token_hex(16)
        return {
            "X-FP-Tenant": tenant or self.tenant, "X-FP-Ts": ts, "X-FP-Nonce": nonce,
            "X-FP-Sig": sig or sign(secret or self.secret, ts, nonce, body),
        }

    def post(self, payload, **kw):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.client.post("/v1/run", content=body, headers=self.headers(body, **kw))

    def get(self, path, **kw):
        return self.client.get(path, headers=self.headers(b"", **kw))


@pytest.fixture
def fake():
    return FakeExecutor()


@pytest.fixture
def api(fake):
    app = create_app(make_settings(), fake)
    with TestClient(app) as client:
        yield Signed(client)


def docker_ready():
    if not shutil.which("docker"):
        return "docker CLI not installed"
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=True)
    except Exception:
        return "docker daemon not reachable (docker info failed)"
    r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    if r.returncode != 0:
        return f"image {IMAGE} not built (docker build -t {IMAGE} runtime)"
    return None


_DOCKER_REASON = docker_ready()
needs_docker = pytest.mark.skipif(_DOCKER_REASON is not None, reason=_DOCKER_REASON or "")

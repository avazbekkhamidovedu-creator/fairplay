import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import config as C

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS: list[dict] = []
SECTION = [""]


def section(name: str) -> None:
    SECTION[0] = name
    print(f"\n== {name}", flush=True)


def check(name: str, cond, detail: str = "") -> bool:
    ok = bool(cond)
    RESULTS.append({"section": SECTION[0], "name": name, "ok": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    return ok


def note(text: str) -> None:
    RESULTS.append({"section": SECTION[0], "name": text, "ok": None, "detail": ""})
    print("NOTE " + text, flush=True)


def fake(method="GET", path="/", body=None):
    req = urllib.request.Request(C.ADMIN + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=10) as r:
        return json.loads(r.read())


def fake_state():
    return fake("GET", "/")


def fake_code() -> str:
    return fake_state()["code"]


def wait_http(url: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with _opener.open(url, timeout=3):
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


class Services:
    def __init__(self, fresh: bool = True):
        self.procs: list[subprocess.Popen] = []
        self.fresh = fresh
        os.makedirs(C.SCRATCH, exist_ok=True)

    def start(self):
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            os.environ.pop(k, None)
        os.environ["NO_PROXY"] = "*"
        if self.fresh:
            for f in ("server/e2e.db", "server/e2e.db-wal", "server/e2e.db-shm"):
                try:
                    os.remove(os.path.join(C.SCRATCH, f))
                except FileNotFoundError:
                    pass
        fake_log = open(os.path.join(C.SCRATCH, "fake-canvas.log"), "w")
        self.procs.append(subprocess.Popen(
            [sys.executable, os.path.join(C.HERE, "fake-canvas", "server.py"), os.path.join(C.SCRATCH, "cert")],
            stdout=fake_log, stderr=subprocess.STDOUT))
        wait_http(C.ADMIN + "/")
        self.server_log_path = os.path.join(C.SCRATCH, "server.log")
        server_log = open(self.server_log_path, "w")
        env = {**os.environ, "FAIRPLAY_FEATURES": "scan,lms,practice", "FAIRPLAY_SECRET": C.SECRET,
               "PYTHONUNBUFFERED": "1"}
        self.procs.append(subprocess.Popen(
            [C.VENV_PY, os.path.join(C.HERE, "run_server.py")], cwd=os.path.join(C.REPO, "server"),
            stdout=server_log, stderr=subprocess.STDOUT, env=env))
        wait_http(C.SERVER + "/api/health")

    def stop(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def server_log(self) -> str:
        with open(self.server_log_path, encoding="utf-8", errors="replace") as f:
            return f.read()


def write_results(path: str, engines_note: str = "") -> None:
    with open(path, "w") as f:
        json.dump(RESULTS, f, indent=1)

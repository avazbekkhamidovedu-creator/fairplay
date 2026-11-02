import asyncio
import logging
import os
import re
import secrets
import signal as signal_mod
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from . import languages as L
from .config import Settings

log = logging.getLogger("fp-runner")

STDOUT_CAP = 256 * 1024
STDERR_CAP = 64 * 1024
FSIZE_BYTES = 64 * 1024 * 1024
KILL_GRACE_S = 3.0
SWEEP_AGE_S = 300
NAME_PREFIX = "fpr-"
LABEL = "fp-runner=1"
CONTAINER_LIFETIME_S = 900
POOL_MAX_IDLE_S = 120

_HELLO = {
    "c": '#include <stdio.h>\nint main(void){puts("ok");return 0;}\n',
    "cpp": '#include <cstdio>\nint main(){std::puts("ok");}\n',
    "java": 'public class Main { public static void main(String[] a){ System.out.println("ok"); } }\n',
}


@dataclass
class LangInfo:
    key: str
    label: str
    version: str


class Executor(Protocol):
    async def startup(self) -> dict[str, LangInfo]: ...

    async def run(self, language: str, source: str, stdins: list[str],
                  time_limit_ms: int, memory_limit_kb: int) -> dict: ...

    async def sweep(self) -> int: ...


@dataclass
class ExecResult:
    rc: int | None
    out: bytes
    err: bytes
    out_over: bool
    err_over: bool
    timed_out: bool
    elapsed_ms: int


class DockerExecutor:
    def __init__(self, settings: Settings):
        self.s = settings
        self._env = {k: os.environ[k] for k in
                     ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY",
                      "DOCKER_CERT_PATH") if k in os.environ}
        self._versions: dict[str, str] = {}
        self._pool: deque[tuple[str, float]] = deque()
        self._need = asyncio.Event()
        self._refill: asyncio.Task | None = None
        self._closing = False

    async def _docker(self, args: list[str], timeout: float = 30.0, stdin: bytes | None = None):
        proc = await asyncio.create_subprocess_exec(
            self.s.docker_bin, *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._env)
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, b"", b"docker command timed out"
        return proc.returncode, out, err

    async def _rm(self, name: str) -> None:
        await self._docker(["rm", "-f", name], timeout=20)

    def _start_args(self, name: str, mem_mb: int, lifetime_s: int) -> list[str]:
        pids = 64
        return [
            "run", "-d", "--rm", "--init", "--pull", "never", "--name", name, "--label", LABEL,
            "--network", "none", "--read-only",
            "--tmpfs", "/work:rw,exec,nosuid,size=64m,uid=65534,gid=65534,mode=0700",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
            "--memory", f"{mem_mb}m", "--memory-swap", f"{mem_mb}m", "--cpus", "1",
            "--pids-limit", str(pids), "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--ulimit", "nofile=64", "--ulimit", f"fsize={FSIZE_BYTES}",
            "--ulimit", "core=0", "--log-driver", "none",
            "-e", "LC_ALL=C.UTF-8", "-e", "LANG=C.UTF-8", "-e", "TZ=UTC", "-e", "HOME=/tmp",
            "-e", "TMPDIR=/work", "-w", "/work",
            self.s.image, "sleep", str(lifetime_s),
        ]

    async def _exec(self, name: str, cmd: list[str], stdin: bytes, deadline_s: float,
                    out_cap: int, err_cap: int, stop_on_out_over: bool = True) -> ExecResult:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            self.s.docker_bin, "exec", "-i", name, *cmd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self._env)
        out, err = bytearray(), bytearray()
        flags = {"out": False, "err": False}
        over = asyncio.Event()

        async def pump(stream, buf, cap, key, stop):
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                room = cap - len(buf)
                if len(chunk) > room:
                    buf += chunk[:max(room, 0)]
                    flags[key] = True
                    if stop:
                        over.set()
                        return
                else:
                    buf += chunk

        async def feed():
            try:
                proc.stdin.write(stdin)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        tasks = [asyncio.create_task(pump(proc.stdout, out, out_cap, "out", stop_on_out_over)),
                 asyncio.create_task(pump(proc.stderr, err, err_cap, "err", False)),
                 asyncio.create_task(feed())]
        t_wait = asyncio.create_task(proc.wait())
        t_over = asyncio.create_task(over.wait())
        timed_out = False
        try:
            done, _ = await asyncio.wait({t_wait, t_over}, timeout=deadline_s,
                                         return_when=asyncio.FIRST_COMPLETED)
            if t_wait in done:
                await asyncio.wait(tasks[:2], timeout=2.0)
                rc = proc.returncode
            else:
                timed_out = t_over not in done
                rc = None
                proc.kill()
        finally:
            for t in tasks + [t_wait, t_over]:
                t.cancel()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return ExecResult(rc, bytes(out), bytes(err), flags["out"], flags["err"], timed_out,
                          int((time.monotonic() - start) * 1000))

    async def _start(self, name: str) -> None:
        rc, _, _ = await self._docker(self._start_args(name, L.COMPILE_MEMORY_MB, CONTAINER_LIFETIME_S), timeout=60)
        if rc != 0:
            await self._rm(name)
            raise RuntimeError("container start failed")

    async def _take(self) -> str:
        now = time.monotonic()
        while self._pool:
            name, born = self._pool.popleft()
            if now - born < POOL_MAX_IDLE_S:
                self._need.set()
                return name
            asyncio.create_task(self._rm(name))
        self._need.set()
        name = NAME_PREFIX + secrets.token_hex(8)
        await self._start(name)
        return name

    async def _update(self, name: str, mem_mb: int, pids: int | None) -> None:
        args = ["update", "--memory", f"{mem_mb}m", "--memory-swap", f"{mem_mb}m"]
        if pids is not None:
            args += ["--pids-limit", str(pids)]
        rc, _, _ = await self._docker([*args, name], timeout=20)
        if rc != 0:
            raise RuntimeError("container update failed")

    async def _refill_loop(self) -> None:
        while not self._closing:
            try:
                now = time.monotonic()
                while self._pool and now - self._pool[0][1] >= POOL_MAX_IDLE_S:
                    old, _ = self._pool.popleft()
                    await self._rm(old)
                while len(self._pool) < self.s.warm_pool and not self._closing:
                    name = NAME_PREFIX + secrets.token_hex(8)
                    await self._start(name)
                    self._pool.append((name, time.monotonic()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("pool refill failed: %s", type(exc).__name__)
                await asyncio.sleep(5)
            self._need.clear()
            try:
                await asyncio.wait_for(self._need.wait(), POOL_MAX_IDLE_S / 4)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        self._closing = True
        if self._refill:
            self._refill.cancel()
        while self._pool:
            name, _ = self._pool.popleft()
            await self._rm(name)

    async def _compile(self, name: str, lang: str, source: bytes, filename: str, compile_cmd: list[str],
                       limit_mb: int):
        if limit_mb > L.COMPILE_MEMORY_MB:
            await self._update(name, limit_mb, None)
        script = 'cat > "$1" && shift && exec timeout -s KILL ' + str(self.s.compile_timeout_s) + ' "$@"'
        res = await self._exec(name, ["sh", "-c", script, "sh", filename, *compile_cmd], source,
                               self.s.compile_timeout_s + KILL_GRACE_S + 2, L.COMPILE_OUTPUT_CAP,
                               L.COMPILE_OUTPUT_CAP, stop_on_out_over=False)
        text = (res.err + res.out).decode("utf-8", "replace")
        if res.timed_out or res.rc is None:
            return False, "Compilation timed out."
        if res.rc in (124, 137):
            return False, (text[:L.COMPILE_OUTPUT_RETURN] + "\nCompilation exceeded the time or memory limit.").strip()
        if res.rc in (125, 126, 127):
            raise RuntimeError("compile exec failed")
        if res.rc != 0:
            return False, text[:L.COMPILE_OUTPUT_RETURN]
        mem = min(limit_mb, L.COMPILE_MEMORY_MB) if limit_mb < L.COMPILE_MEMORY_MB else limit_mb
        pids = None if lang == "java" else 16
        if limit_mb < L.COMPILE_MEMORY_MB or pids is not None:
            await self._update(name, mem, pids)
        return True, text[:L.COMPILE_OUTPUT_RETURN]

    async def run(self, language: str, source: str, stdins: list[str],
                  time_limit_ms: int, memory_limit_kb: int) -> dict:
        if language not in L.LANGUAGES:
            raise ValueError("language")
        version = self._versions.get(language, "")
        java_class = None
        if language == "java":
            det = L.detect_java_class(source)
            if det.error:
                return {"status": "compile_error", "compile_output": det.error,
                        "runtime_version": version, "results": []}
            java_class = det.name
        filename, compile_cmd = L.compile_argv(language, java_class)
        base_cmd = L.run_argv(language, memory_limit_kb, java_class)
        extra_ms = self.s.java_startup_ms if language == "java" else 0
        inner_s = (time_limit_ms + extra_ms) / 1000.0
        limit_mb = max(1, memory_limit_kb // 1024)
        name = None
        src = source.encode("utf-8")
        results: list[dict] = []
        compile_output = ""
        try:
            name = await self._take()
            ok, compile_output = await self._compile(name, language, src, filename, compile_cmd, limit_mb)
            if not ok:
                return {"status": "compile_error", "compile_output": compile_output,
                        "runtime_version": version, "results": []}
            alive = True
            for text in stdins:
                if not alive:
                    await self._rm(name)
                    name = await self._take()
                    ok, _ = await self._compile(name, language, src, filename, compile_cmd, limit_mb)
                    if not ok:
                        raise RuntimeError("rebuild failed")
                    alive = True
                res, kill = await self._one_run(name, language, base_cmd, text.encode("utf-8"),
                                                time_limit_ms, inner_s)
                results.append(res)
                if kill:
                    alive = False
            return {"status": "ok", "compile_output": compile_output,
                    "runtime_version": version, "results": results}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("internal error: %s", type(exc).__name__)
            return {"status": "internal_error", "compile_output": "", "runtime_version": version, "results": []}
        finally:
            if name:
                await asyncio.shield(self._rm(name))

    async def _one_run(self, name, lang, cmd, stdin, limit_ms, inner_s):
        full = ["timeout", "-k", "0.3", f"{inner_s:.3f}", *cmd]
        res = await self._exec(name, full, stdin, inner_s + KILL_GRACE_S, STDOUT_CAP, STDERR_CAP)
        stdout = res.out.decode("utf-8", "replace")
        stderr = res.err.decode("utf-8", "replace")
        base = {"stdout": stdout, "stderr": stderr, "time_ms": res.elapsed_ms,
                "exit_code": res.rc, "truncated": res.out_over or res.err_over}
        if res.out_over:
            base.update(status="output_limit", exit_code=None, truncated=True)
            return base, True
        if res.timed_out or res.rc is None:
            base.update(status="time_limit", exit_code=None)
            return base, True
        rc = res.rc
        if rc == 124 or (rc == 137 and res.elapsed_ms >= inner_s * 1000):
            base.update(status="time_limit", exit_code=None)
        elif rc == 137 or (lang == "java" and "OutOfMemoryError" in stderr + stdout[-400:] and rc in (1, 3)):
            base.update(status="memory_limit")
        elif rc != 0:
            base["status"] = "runtime_error"
            if rc > 128 and not base["truncated"]:
                try:
                    sig = signal_mod.Signals(rc - 128).name
                except ValueError:
                    sig = str(rc - 128)
                base["stderr"] = stderr + f"\n[fp-runner] terminated by signal {sig}"
        else:
            base["status"] = "ok"
        return base, False

    async def sweep(self) -> int:
        rc, out, _ = await self._docker(["ps", "-a", "--filter", f"label={LABEL}",
                                         "--filter", f"name=^{NAME_PREFIX}", "--format", "{{.Names}}"])
        if rc != 0:
            return 0
        removed = 0
        for name in out.decode().split():
            if not re.fullmatch(re.escape(NAME_PREFIX) + r"[0-9a-f]{16}", name):
                continue
            rc, o, _ = await self._docker(["inspect", "--format", "{{.Created}}", name])
            if rc != 0:
                continue
            try:
                created = datetime.fromisoformat(o.decode().strip()[:19]).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (datetime.now(timezone.utc) - created).total_seconds() > SWEEP_AGE_S:
                await self._rm(name)
                removed += 1
        return removed

    async def _probe_versions(self) -> dict[str, str]:
        script = "gcc --version | head -1; g++ --version | head -1; javac -version 2>&1; java -version 2>&1 | head -1"
        rc, out, _ = await self._docker([
            "run", "--rm", "--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--user", "65534:65534", "--memory", "256m", "--pids-limit", "32", "--log-driver", "none",
            self.s.image, "sh", "-c", script], timeout=60)
        if rc != 0:
            return {}
        lines = out.decode().splitlines()
        found: dict[str, str] = {}
        if len(lines) >= 4:
            g = re.search(r"(\d+\.\d+\.\d+)\s*(?:\S*)$", lines[0])
            gp = re.search(r"(\d+\.\d+\.\d+)\s*(?:\S*)$", lines[1])
            j = re.search(r"javac\s+(\S+)", lines[2])
            if g:
                found["c"] = g.group(1)
            if gp:
                found["cpp"] = gp.group(1)
            if j:
                found["java"] = j.group(1)
        return found

    async def startup(self) -> dict[str, LangInfo]:
        await self.sweep()
        versions = await self._probe_versions()
        info: dict[str, LangInfo] = {}
        labels = {
            "c": lambda v: f"C (gcc {v.split('.')[0]}, C17)",
            "cpp": lambda v: f"C++ (g++ {v.split('.')[0]}, C++20)",
            "java": lambda v: f"Java (OpenJDK {v.split('.')[0]})",
        }
        runtime = {"c": "gcc ", "cpp": "g++ ", "java": "OpenJDK "}
        for key in L.LANGUAGES:
            v = versions.get(key)
            if not v:
                continue
            self._versions[key] = runtime[key] + v
            r = await self.run(key, _HELLO[key], [""], 5000, 262144)
            if r["status"] == "ok" and r["results"] and r["results"][0]["stdout"].strip() == "ok":
                info[key] = LangInfo(key, labels[key](v), v)
            else:
                log.error("self-check failed for %s", key)
        if self.s.warm_pool > 0 and info:
            self._refill = asyncio.create_task(self._refill_loop())
        return info

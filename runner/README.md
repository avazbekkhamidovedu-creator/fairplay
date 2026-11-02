# fp-runner

Small sandboxed code-execution service for C, C++ and Java. It drives the Docker
CLI (fixed argument vectors, no shell on the host), so it runs on any host with
Docker. Python 3.11+, FastAPI, uvicorn.

Each request gets its own container. Source goes in over stdin of `docker exec`,
nothing from the host is mounted, and the container is removed when the request
ends. Containers are never shared between requests.

## Build and run

```
# 1. runtime image (gcc, g++, OpenJDK 21, non-root nobody)
docker build -t fp-runner-runtime:1 runtime

# 2. service
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export RUNNER_TENANTS='school:0123456789abcdef0123456789abcdef0123'
.venv/bin/uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8080
```

Startup sweeps leftover `fpr-*` containers (label `fp-runner=1`, older than 5
minutes), probes the toolchains, and runs a hello world per language. Only
languages that pass are listed by `/v1/health`.

Docker Compose: see `docker-compose.yml` (mounts the Docker socket, read the
hardening notes first).

## Environment

| variable | default | meaning |
|---|---|---|
| `RUNNER_TENANTS` | required | `tenant1:secret1,tenant2:secret2`, secrets of 32+ characters or startup fails |
| `RUNNER_MAX_CONCURRENT` | 4 | global concurrent runs |
| `RUNNER_TENANT_CONCURRENT` | 4 | concurrent runs per tenant |
| `RUNNER_QUEUE_WAIT_S` | 30 | wait for a slot, then 503 with `Retry-After` |
| `RUNNER_IMAGE` | `fp-runner-runtime:1` | runtime image |
| `RUNNER_DOCKER_BIN` | `docker` | docker CLI |
| `RUNNER_JAVA_STARTUP_MS` | 400 | extra wall-clock allowance added to each Java run (JVM start-up) |
| `RUNNER_COMPILE_TIMEOUT_S` | 10 | compile time limit |
| `RUNNER_WARM_POOL` | 2 | idle pre-started containers (0 disables) |

## API

All bodies are JSON. Signed endpoints need these headers:

- `X-FP-Tenant`: tenant name
- `X-FP-Ts`: unix seconds, within 60 s of the server clock
- `X-FP-Nonce`: 16-64 characters of `[A-Za-z0-9_-]`, single use per tenant for 5 minutes
- `X-FP-Sig`: hex HMAC-SHA256 with the tenant secret over `"{ts}.{nonce}.{sha256_hex(body)}"`

Unknown tenant, bad signature, stale timestamp and replayed nonce all return
`401 {"error":"unauthorized"}`. Bodies over 256 KB return 413. Invalid bodies
return 422. A busy service returns 503 with `Retry-After`.

- `GET /v1/health` (unsigned): `{"ok": true, "languages": [...], "version": "..."}`
- `GET /v1/languages` (signed, empty body): `{"languages": [{"key","label","version"}]}`
- `POST /v1/run` (signed):

```
{"language": "c|cpp|java", "source": "...", "stdins": ["...", ...],
 "time_limit_ms": 2000, "memory_limit_kb": 262144}
```

Limits: source 100,000 chars, 1 to 30 stdins of up to 20,000 chars,
`time_limit_ms` 100..10000 (per run), `memory_limit_kb` 16384..1048576.

Response:

```
{"status": "ok|compile_error|internal_error", "compile_output": "...", "runtime_version": "gcc 14.2.0",
 "results": [{"status": "ok|runtime_error|time_limit|memory_limit|output_limit",
              "stdout": "...", "stderr": "...", "time_ms": 12, "exit_code": 0, "truncated": false}]}
```

`results` is empty on `compile_error`. `exit_code` is null for `time_limit` and
`output_limit`. A runtime_error caused by a signal gets a final stderr line
`[fp-runner] terminated by signal SIGSEGV`. Output is compared by the caller, not here.

### curl example

```
cat > /tmp/req.json <<'JSON'
{"language":"c","source":"#include <stdio.h>\nint main(){int a,b;scanf(\"%d %d\",&a,&b);printf(\"%d\\n\",a+b);}","stdins":["1 2\n","3 4\n"]}
JSON
eval curl -s localhost:8080/v1/run -H "'Content-Type: application/json'" \
  $(python3 tools/sign_request.py school 0123456789abcdef0123456789abcdef0123 /tmp/req.json) \
  --data-binary @/tmp/req.json
```

`tools/sign_request.py TENANT SECRET [BODY_FILE|-]` prints the four `-H` flags
with a fresh timestamp and nonce. Use the same bytes for signing and sending
(`--data-binary`).

## Execution model

Per request: one container (`fpr-<random>`) started with
`--network none --read-only --tmpfs /work (64 MB, exec) --tmpfs /tmp (16 MB, noexec)
--cap-drop ALL --no-new-privileges --user 65534:65534 --memory/--memory-swap
--cpus 1 --pids-limit --init --ulimit nofile=64,fsize=64MB,core=0 --log-driver none`,
a clean environment (`LC_ALL=C.UTF-8`, `TZ=UTC`, `HOME=/tmp`), and no host mounts.
The source is compiled once (10 s, 512 MB), the container is then tightened to the
requested memory limit (`docker update`) and each stdin is one `docker exec`.
Pids limit: 16 for C/C++ after compile, 64 for Java.

The warm pool only pre-starts idle containers; a pooled container is handed to
exactly one request and then deleted.

- C: `gcc -O2 -std=c17 -fmax-errors=5 -s -o main main.c -lm`
- C++: `g++ -O2 -std=c++20 -fmax-errors=5 -s -o main main.cpp`
  (no `-march`, so results do not depend on the host CPU; note `-std=c17` is strict ISO, POSIX
  declarations such as `fork` need `_GNU_SOURCE`)
- Java: main class detected from the source (the public top-level type, else the first top-level
  class), written to `<Class>.java`, `javac -encoding UTF-8`. A `package` line or no class at all is a
  `compile_error` ("Define a public class Main; do not use a package."). Run flags:
  `-Xmx` (limit minus up to 96 MB of headroom) `-Xss64m -XX:+UseSerialGC -XX:ActiveProcessorCount=1
  -XX:TieredStopAtLevel=1 -Xshare:auto -XX:+ExitOnOutOfMemoryError -Dfile.encoding=UTF-8`.
  `-Xss64m` is the same for every caller. An `OutOfMemoryError` is `memory_limit`.

Verdicts: `time_limit` when the per-run deadline passes (GNU `timeout` inside the container, a
service-side deadline plus 3 s as backstop that removes the container); `memory_limit` on SIGKILL
before the deadline (cgroup OOM) or a Java OOM; `output_limit` when stdout exceeds 256 KB (container
removed); `runtime_error` on any other non-zero exit. stderr is capped at 64 KB (`truncated` set).
`time_ms` is wall clock around the `docker exec` and includes its small start cost.

Java start-up: every Java run gets `RUNNER_JAVA_STARTUP_MS` added to its deadline so the JVM boot does
not eat the caller's time limit. Measure it on your host (a hello world `time_ms`) and set the variable
to about that value; the 400 default fits a native x86-64/arm64 host.

## Tests

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest            # API/signature/validation tests, plus Docker tests if available
```

Docker tests need `docker info` to work and the `fp-runner-runtime:1` image; otherwise they are
skipped with the reason. On a slow or emulated Docker host set `RUNNER_TEST_COMPILE_TIMEOUT_S`,
`RUNNER_TEST_JAVA_STARTUP_MS` and `RUNNER_TEST_TIME_MS` (see `tests/test_docker.py`).

## Threat model and hardening

The runner executes untrusted code. Defence in depth inside the container: no network, read-only root,
no capabilities, no-new-privileges, non-root, default seccomp profile, cgroup limits for memory, cpu
and pids, bounded tmpfs, output caps, wall-clock kill, container removal and a startup sweep.

Known limits to plan around:

- A container is shared by the stdins of one request. A program can leave files in `/work` or
  background processes that the next stdin of the same submission sees. They cannot reach other
  requests or other tenants.
- Containers share the host kernel. A kernel or runtime escape defeats everything above.
- Anyone who can talk to the Docker socket controls the host. The service needs it.
- Signing does not bind the path or method; single-use nonces and the body hash limit that.
- The nonce set is in memory: run one service process per host (or sticky routing), restarts forget nonces
  (the 60 s timestamp window still applies).
- The startup sweep removes labelled `fpr-*` containers older than 5 minutes, so do not run two
  instances against the same daemon.

Add in production:

- Run on a dedicated host or VM that holds no other workloads and no secrets (no cloud credentials, no
  SSH keys, no database access). Treat it as disposable.
- Use gVisor (`runsc`) or Kata as the Docker runtime, or at least rootless Docker / user-namespace remap,
  and add `--runtime` to the argument vector in `app/executor.py`.
- Put the service behind a firewall that only admits the FairPlay server; terminate TLS in front of it.
- Keep tenant secrets in a secret store, rotate them, and keep the host clock synced (NTP).
- Limit the Docker daemon with a log/disk quota and monitor for `fpr-*` containers that outlive requests.
- Pull the base image by digest and rebuild `fp-runner-runtime` regularly.

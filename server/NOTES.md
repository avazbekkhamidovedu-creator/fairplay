# FairPlay server: scale notes, load tests and sizing

## How it scales
- **Stateless API.** Tokens are HMAC-signed (no session table). Any request can go to any worker or machine, so capacity grows by adding uvicorn workers and API containers behind a load balancer.
- **Hot path off the database.** Heartbeats, app beats, live-room state, integrity-event counters, wrong-code counters/lockouts and cached exam content live in Redis. A heartbeat or app beat never touches the database.
- **Database writes are small and idempotent.** Answer saves are one conditional `UPDATE ... WHERE client_seq < :seq` (a late or duplicated save can never overwrite a newer one). Submit is idempotent and returns the same receipt. Unique `(exam_id, student_id)` prevents double attempts; all foreign keys are indexed.
- **Offline-first clients.** Students keep answers in local storage and retry, so a slow or briefly unavailable server never loses work; it just delays the sync.
- **Postgres in production, SQLite for dev** (same code, `DATABASE_URL`). Redis optional in dev (in-memory fallback, single process only).

## Sizing in one page (perf 0.6.2, 2026-10-23)

**One school, 5,000 students writing at the same time** (all starting within one minute, signed in at check-in):

| Part | Size | Why |
|---|---|---|
| API (uvicorn) | **12 vCPU, 6 GB RAM** — e.g. 3 containers × 4 workers, or one VM with `WEB_CONCURRENCY=12` | 5,000 students keep ~5 vCPU busy; we plan for 60 % so p95 stays well under 300 ms, plus ~3 vCPU for the exam-start minute |
| nginx (TLS, gzip, static cache) | 2 vCPU, 1 GB | serves the web app's files from its cache, compresses JSON |
| Postgres 16 | 2–4 vCPU, 8 GB RAM, SSD | ~0.3 vCPU steady, ~1 vCPU at exam start; a few MB/s of writes |
| Redis 7 | 1–2 vCPU, 1 GB RAM | ~0.35 of one core (Redis uses one core); ~10 MB of data per school |
| Network | **1 Gbit/s** at the server | exam start with empty browser caches: ~2.3 GB in the first minute (~300 Mbit/s average, peaks higher); writing: ~20 Mbit/s |

A single large server works too: e.g. **32 vCPU / 64 GB** = Postgres (4) + Redis (1–2) + nginx (2) + API with 24 workers: comfortably **2 schools at once** (10,000 students), about 3 at the limit.

**Several schools.** Every school adds ~12 API vCPU (add API containers behind nginx / the load balancer; nothing else changes). Postgres and Redis grow slowly; the first limit is Redis (one core): keep one Redis per ~3 schools (≈15,000 students) and Postgres connections under `max_connections` (see "Settings"). Beyond ~4 schools the simplest, most robust layout is a **cell per group of up to 4 schools** (its own API containers + Postgres + Redis, its own address): schools never share an exam, so cells are fully independent and one cell's trouble never reaches another.

| Concurrent students | API vCPU | Postgres | Redis | Layout |
|---|---|---|---|---|
| 5,000 (1 school) | 12 | 2–4 vCPU | 1 | 1 box (32 vCPU) or API ×3 + DB box |
| 10,000 (2) | 24 | 4 vCPU | 1 | same, more API |
| 20,000 (4) | 48 | 8 vCPU, 16 GB | 2 (one per 2 schools, or 1 at the limit) | 1 cell |
| 40,000 (8) | 96 | 2 × 8 vCPU | 2–4 | 2 cells |

**Sign-in is the most expensive single request** (a password check takes ~145 ms of CPU, by design). 5,000 sign-ins = ~725 CPU-seconds: spread over a 10-minute check-in that is ~1.2 vCPU; all within one minute it would need ~12 extra vCPU. So: **open check-in 10–15 minutes before the exam** (student tokens last 8 hours). If the school sits behind one public address (NAT), set `FAIRPLAY_LOGIN_SCHOOL_NETS` to it, otherwise the per-address limit (300 sign-ins a minute) turns most students away. Since 0.6.2 a sign-in rush can no longer slow down students who are already writing (password checks run at a lower CPU priority and queue).

The vCPU numbers come from a shared 2.8 GHz cloud vCPU (this sandbox); a dedicated modern server core is typically faster, so they are on the safe side.

## What one student costs (v0.6, locked FairPlay app)

Per student, measured in the load test (traffic model below):

| Request | per student | when |
|---|---|---|
| page heartbeat (with battery) | 0.20 /s | every 5 s |
| app beat (signed, from the app) | 0.20 /s | every 5 s |
| answer save | 0.04–0.1 /s | 1 s after the student stops typing / clicks; whole answer map (MCQ ~0.5 KB, essays 2–15 KB) |
| integrity events | ~0.01 /s | about one batch per 2 minutes |
| invigilator live room | 1 poll / 3 s per 100 students | ~80 KB JSON (7 KB gzipped) |
| **total** | **≈ 0.45 req/s** | **5,000 students ≈ 2,250 req/s** |
| exam start (once) | ~12 requests + 11 static files | sign-in, exam list ×3, offline package + key, challenge, start, attempt, materials, offline key |

Server CPU per request (ms of one vCPU; API = the uvicorn worker, measured with `loadtest/endpoint_cost.py`, 2 workers, Postgres + Redis on the same box; ±10 % run to run):

| Endpoint | API before | **API after** | Postgres | Redis |
|---|---|---|---|---|
| heartbeat | 1.57 | **1.23–1.41** | 0 | 0.15 |
| app beat | 2.40 | **1.32–1.42** | 0 | 0.17 |
| answer save | 4.93 | **4.1–4.4** | 0.8 | 0.17 |
| events | 4.02 | **3.2–3.4** | 0.8 | 0.3 |
| live room (100 students) | 24.5 | **10.5–12.4** | 3.4–3.8 | 0.25 |
| GET attempt | 3.55 | **2.8–3.1** | 0.55 | 0.1 |
| start | 7.08 | **5.9–7.1** | 1.2–1.5 | 0.4 |
| exam list | 5.12 | **4.75** | 1.1 | 0.1 |
| offline package | 5.75 | **6.2–7.1** | 0.55 | 0.2 |
| submit | 7.47 | **7.3–8.4** | 1.6–1.9 | 0.25 |
| sign-in (PBKDF2 200k) | 158 | **145** | 0.4 | 0.2 |
| static file, gzip / 304 | 1.33 / 1.42 (no 304: full file) | **1.2 / 0.55** | – | – |

So one student costs **≈ 0.8 ms of API CPU per second** at rest, ≈ 1 ms/s under load (cross-checked against the whole-run CPU of the load tests). 5,000 students ≈ 5 vCPU busy → 8 vCPU at 60 % → 12 with the exam-start minute.

Bytes: a cold exam start downloads ~430 KB of web app (JS/CSS/fonts gzipped; +210 KB code editor for coding exams) and ~30 KB of JSON (~10 KB gzipped). A laptop that has the files cached downloads ~1 KB (one 304). Writing: ~0.2 KB/s down and ~0.3 KB/s up per student, headers included.

## What changed in 0.6.2 (perf)

Found by the load tests and the profiler (py-spy), fixed with tests (`tests/test_perf_0_6_2.py`), behaviour unchanged:

1. **Web app files** (`app/static_files.py`): before, every exam start downloaded the whole 1.3 MB bundle again, **uncompressed** (no Cache-Control, no 304). Now hashed `/assets/` files are `immutable` for a year, `index.html` is revalidated (304), other files cached an hour with ETags, and text files go gzip-compressed (precompressed `.br`/`.gz` used when present, else compressed once per process). 5,000 cold starts: 6.3 GB → ~2.3 GB; warm starts: ~5 MB.
2. **Sign-in rush starved the exam** (5,000-student test): PBKDF2 threads took the CPU from the event loops; heartbeats and saves of students already writing waited seconds. Password checks now run in their own threads at a lower CPU priority (`app/security.py`), so the exam keeps the CPU and sign-ins queue.
3. **500 errors under a rush**: Redis "Too many connections" (the pool failed instead of waiting) and database pool timeouts (thousands of requests in progress per worker, each advancing slowly while holding a connection). Now Redis uses a blocking pool, and each worker runs at most `FAIRPLAY_MAX_INFLIGHT` (128) API requests at once; the rest wait in line (`app/admission.py`). Result: 9,964 errors → 0.
4. **School behind one NAT**: the per-address sign-in limit (300/min) would refuse most of a 5,000-student school. New `FAIRPLAY_LOGIN_SCHOOL_NETS` / `FAIRPLAY_LOGIN_SCHOOL_IP_LIMIT` (6,000/min); per-account limits unchanged.
5. **Fewer Redis round trips on the hot paths**: app beat 7 → 4 (one read batch, one write batch), heartbeat 4 → 3, save 5 → 4; saves and app beats read a small "exam facts" entry (question ids, strong-lock flag) instead of fetching and parsing the whole cached exam (`services.get_exam_hot`, dropped together with the exam cache).
6. **Framework overhead**: the role checks were plain functions, so FastAPI ran them in a thread pool on every request (now `async`); the student router is matched first (its paths overlap no other route, checked by a test); hot endpoints return `JSONResponse` directly (skips FastAPI's generic re-encoding; together with hiredis the live room went from 24.5 to ~11 ms); the cached offline package is sent as stored.
7. **hiredis** (C parser for Redis replies) on Linux: −20 % CPU on app beats, −25 % on the live room.
8. **Token-version cache 30 s → 300 s** (`security.USER_CACHE_TTL_S`): 5,000 students caused ~170 database reads a second just to re-check tokens. "Sign out everywhere" and removal from a class still apply at once (they clear the entry).
9. **Deployment**: `nginx.conf` is now a full config: 16k connections per worker (the image default of 1,024 is too few for a school), keep-alive 75 s to laptops, upstream keep-alive 60 s < uvicorn's `--timeout-keep-alive 75` (otherwise nginx can reuse a connection uvicorn just closed → rare 502s on POSTs), gzip for JSON, a cache for the web app's files, 256 KB body buffer (answer maps stay in memory). Dockerfile: `--timeout-keep-alive 75 --backlog 4096`.

## Load tests (2026-10-23, perf 0.6.2)

Sandbox: **2 shared vCPUs, 7 GB RAM**. Load generator, nginx (when used), 2 API workers, Postgres 16 and Redis 7 all on the same 2 vCPUs, so everything above ~500 students is deliberately overloaded. Other jobs ran on the box at times: each run waited until the 1-minute load average was < 0.7 before starting (logged as "load ok"); later in the runs the box was shared. Traffic: `loadtest/loadtest.py --traffic v06` (below).

| Run | What | Requests | Errors | Latency |
|---|---|---|---|---|
| A | 500 students, 5 rooms, sign-in + start over 60 s, 180 s writing | 50,123 | **0** | writing (after the start minute): 225 req/s, p50 9 ms, p95 50–130 ms. Start minute: p95 ~2 s (500 sign-ins = 1.2 vCPU of PBKDF2 on a 2-vCPU box) |
| B (old code) | 5,000 students, 50 rooms, sign-in in the 90 s ramp, 300 s writing | 210,891 | **9,964 (4.7 %)** | 500s from DB-pool timeouts and Redis "Too many connections"; exam traffic starved (170 req/s for 8 minutes) by password checks |
| B | same, 0.6.2 code, check-in first (`--login-first`) | 227,885 | **0** | check-in: 5,000 sign-ins in ~400 s (p95 0.95 s). Writing: ~3× over this box's capacity, so requests queued (p50 20–35 s, the clients slow down as real ones would) and the queue **drained completely** at the end (last 10 s: p50 19 ms) |
| C | burst: 5,000 sign in **and** start within 60 s, 120 s writing | 371,786 | 56 (0.015 %) — all sign-ins that waited > 20 min (client limit) | students who got in wrote smoothly during the whole rush: heartbeat p50 134 ms / p95 845 ms, start p50 236 ms / p95 1.6 s; sign-ins queued behind (p50 10 min: 725 CPU-s of password checks on the leftover CPU of 2 vCPUs) |
| N | 1,500 students **through nginx** (this nginx.conf), check-in first, 180 s writing | 128,824 | **0** | static files from nginx's cache (p50 1.3 ms, X-Cache HIT), JSON gzipped (live room 82 KB → 7 KB); API overloaded on this box (p50 1.4 s) but no 502s or errors |

What the runs prove: the code holds 5,000 students without a single failed request; on too little CPU it slows down and recovers instead of failing; with the sizing above (≥ 4× the CPU of this sandbox for the API alone) it runs at the latency of run A. Not proven here (no hardware): the exact p95 on a 12-vCPU box — the sizing is computed from the measured CPU cost per request with 40 % headroom.

Traffic model (`--traffic v06`, from `web/src/pages/student/ExamTakingPage.tsx`, `web/src/lib/answerQueue.ts`, `mac/Sources/Proctora/AppSession.swift`): `GET /student` + JS/CSS/fonts (`--cached 0.5`: half the laptops have them cached), sign-in, exam list (home), check-in (exam list, offline package + key), "Before you start" (exam list, open-book upload area), challenge + signed start, GET attempt, open-book materials, the app's offline key; then heartbeat every 5 s with battery, signed app beat every 5 s, saves debounced 1 s after a change with the whole answer map (multiple choice: read 15–60 s then click; essays/code: 3–15 s typing bursts, pauses ~6 s, essays grow to 2–5 KB, code to 1–3 KB; `--mid-exam` starts half-written), events about every 2 minutes, submit, Done page. Rooms rotate four kinds: 40 MCQ; 5 short + 3 essays (open book); 4 MCQ + 3 Python questions with visible + hidden tests; mixed. One invigilator per 100 students polls the live room every 3 s. The clients wait for each answer before the next beat (a real app's timer keeps firing), so under overload the test is kinder than reality; with the sizing above there is no overload.

Reproduce (Postgres + Redis running; same `FAIRPLAY_APP_KEY` in both shells):
```
export DATABASE_URL=postgresql+asyncpg://... REDIS_URL=redis://... PROCTORA_SECRET=... FAIRPLAY_APP_KEY=...
PROCTORA_SEED_LOADTEST_USERS=5000 FAIRPLAY_SEED_DEMO=1 FAIRPLAY_LOGIN_IP_LIMIT=100000 WEB_CONCURRENCY=2 \
  uvicorn app.main:app --port 8100 --workers 2 --timeout-keep-alive 75 --backlog 4096 --no-access-log
pip install -r requirements-dev.txt
python3 loadtest/endpoint_cost.py --base http://127.0.0.1:8100 --students 400            # CPU per request
python3 loadtest/loadtest.py --base http://127.0.0.1:8100 --students 500 --exams 5 --duration 180 --ramp 60
python3 loadtest/loadtest.py --base http://127.0.0.1:8100 --students 5000 --exams 50 --duration 300 --ramp 90 --login-first
python3 loadtest/loadtest.py --base http://127.0.0.1:8100 --students 5000 --exams 50 --duration 120 --ramp 60 --timeout 1200
```
`--traffic legacy` reproduces the 2026-10-11 model (save every 3 s, no static files). `--json-out` writes the summary (per-endpoint latency, bytes, a 10-second timeline that shows whether queues drain).

## Settings for production

- **uvicorn**: one worker per vCPU (`WEB_CONCURRENCY`), `--proxy-headers --forwarded-allow-ips <nginx>` (Dockerfile does this), `--timeout-keep-alive 75` (longer than nginx's upstream keep-alive), `--backlog 4096`, `--no-access-log`. Always behind nginx or a load balancer: laptops connect to nginx, uvicorn only sees nginx's few kept-alive connections (direct, 5,000 laptops overflowed uvicorn's accept queue in test C).
- **Pools per worker**: `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=10` (the database is only touched by saves, events, starts and the live room: a few connections per worker are busy at a time). Budget: containers × workers × 15 < Postgres `max_connections` (the server refuses to start otherwise, `FAIRPLAY_DB_MAX_CONNECTIONS`). 12 workers = 180 → `max_connections=300` per school; for 3+ schools on one Postgres lower the overflow (`DB_MAX_OVERFLOW=5`) or add PgBouncer. `REDIS_MAX_CONNECTIONS` (200 per worker) is plenty; requests beyond it wait.
- **`FAIRPLAY_MAX_INFLIGHT`** (128 per worker): API requests in progress at once; the rest wait in line. Sign-in and uploads are not counted.
- **Sign-in**: `FAIRPLAY_LOGIN_SCHOOL_NETS=<school public CIDRs>`; `FAIRPLAY_LOGIN_HASH_SLOTS` (4 per worker) password checks at once. PBKDF2 has 200,000 iterations (`PROCTORA_PBKDF2_ITERATIONS`); OWASP (2023) recommends 600,000 for PBKDF2-SHA256: that triples the sign-in CPU (~430 ms each, 5,000 sign-ins ≈ 3.4 vCPU over a 10-minute check-in). New hashes take the setting; old ones keep working.
- **Postgres**: `shared_buffers` ≈ 25 % of RAM, SSD; attempts are updated in place on every save, so let autovacuum run (defaults are fine at this size).
- **Static files**: through nginx's cache (nginx.conf) or a CDN in front of `/assets/` (immutable, safe to cache forever). The FairPlay apps ship Pyodide and pdf.js themselves (asset pack), so coding and open-book exams don't download them at exam start.

## Known gaps / risks
- The sizing is computed, not measured on the target hardware: run `loadtest.py` once on the real server (a separate client machine, 5,000 students, `--login-first`) before the first big exam.
- TLS was not in the test (nginx terminates it; budget ~1 extra vCPU per school for TLS at 2,250 req/s with keep-alive). Postgres used TLS locally, which slightly inflates the database-path numbers.
- Redis is a single point and single-threaded: one Redis per ~3 schools; it holds live state only (answers are in Postgres), but if it is lost mid-exam the live room is empty until the next beats and one-time nonces must be fetched again.
- The real apps' beat timers keep firing during a slowdown (the test clients wait); with too little CPU real clients would build a longer queue than in run B.

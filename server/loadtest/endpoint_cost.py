#!/usr/bin/env python3
"""Server CPU cost per request, per endpoint.

Runs one kind of request at a time against a running server and reads the CPU time the
API worker processes, Postgres and Redis used meanwhile (/proc/<pid>/stat; Linux, same
machine). Divided by the number of requests that is the CPU cost per request, including
the framework, JSON, auth, Redis and database work, but not the load generator.

    python3 loadtest/endpoint_cost.py --base http://127.0.0.1:8100 --students 400

Same server set-up as loadtest.py (load-test users, demo teacher, FAIRPLAY_APP_KEY).
Students are put in rooms of --room students (default 100), so the live-room poll is
measured with a full room.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aiohttp  # noqa: E402

import loadtest as lt  # noqa: E402

TICK = os.sysconf("SC_CLK_TCK")


def _cpu(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().rsplit(")", 1)[1].split()
        return (int(parts[11]) + int(parts[12])) / TICK      # utime + stime
    except (OSError, IndexError, ValueError):
        return 0.0


def _pids_by_cmd(*needles: str) -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        if all(n in cmd for n in needles):
            out.append(int(d))
    return out


def _children(pid: int) -> list[int]:
    out = []
    try:
        for t in os.listdir(f"/proc/{pid}/task"):
            with open(f"/proc/{pid}/task/{t}/children") as f:
                out += [int(x) for x in f.read().split()]
    except OSError:
        pass
    return out


def _pg_backends(db: str) -> list[int]:
    """Backends of this database + the WAL writer (commits); not other databases' work."""
    import subprocess
    q = (f"select pid from pg_stat_activity where datname = '{db}' "
         "or backend_type in ('walwriter', 'checkpointer', 'background writer')")
    try:
        out = subprocess.run(["psql", os.environ.get("PG_ADMIN_URL", "postgresql://postgres:pg@127.0.0.1/postgres"),
                              "-Atc", q], capture_output=True, text=True, timeout=10).stdout
        return [int(x) for x in out.split()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return _pids_by_cmd("postgres")


class Meter:
    """CPU seconds of: the uvicorn master + its worker processes (found by --port), the
    Postgres backends of the server's database, and redis-server."""

    def __init__(self, port: int, db: str):
        self.port, self.db = port, db

    def groups(self) -> dict[str, list[int]]:
        masters = _pids_by_cmd("uvicorn", "app.main:app", f"--port {self.port}")
        api = list(masters)
        for m in masters:
            api += _children(m)
        return {"api": api, "postgres": _pg_backends(self.db), "redis": _pids_by_cmd("redis-server")}

    def snap(self) -> dict[str, float]:
        g = self.groups()
        return {k: sum(_cpu(p) for p in v) for k, v in g.items()}


async def burst(name: str, n: int, conc: int, make):
    """Run n requests (make(i) -> coroutine), at most conc at once."""
    sem = asyncio.Semaphore(conc)

    async def one(i):
        async with sem:
            await make(i)

    await asyncio.gather(*(one(i) for i in range(n)))


def report(name, n, before, after, wall, rows):
    d = {k: (after[k] - before[k]) * 1000 / max(n, 1) for k in before}
    lat = lt.LAT.get(name, [])
    p50 = lt.pct(lat, 50)
    p95 = lt.pct(lat, 95)
    err = lt.ERR.get(name, 0)
    kb = lt.BYTES_IN.get(name, 0) / max(1, len(lat)) / 1024
    rows.append({"endpoint": name, "n": n, "api_ms": d["api"], "pg_ms": d["postgres"],
                 "redis_ms": d["redis"], "req_s": n / wall, "p50": p50, "p95": p95,
                 "errors": err, "kb_resp": kb})
    print(f"{name:<22}{n:>6}{d['api']:>9.2f}{d['postgres']:>9.2f}{d['redis']:>9.2f}"
          f"{n / wall:>9.0f}{p50:>8.1f}{p95:>8.1f}{err:>6}{kb:>9.2f}", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--students", type=int, default=400)
    ap.add_argument("--room", type=int, default=100)
    ap.add_argument("--conc", type=int, default=16)
    ap.add_argument("--repeat", type=int, default=3, help="requests per student for the hot paths")
    ap.add_argument("--only", default="", help="comma-separated endpoint names")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--db", default="fp_perf", help="the server's Postgres database name")
    ap.add_argument("--teacher-user", default="teacher")
    ap.add_argument("--teacher-password", default="teacher123")
    args = ap.parse_args()
    port = int(args.base.rsplit(":", 1)[1].split("/")[0])
    meter = Meter(port, args.db)
    key = os.environ.get("FAIRPLAY_APP_KEY", "").split(",")[0]
    assert key, "set FAIRPLAY_APP_KEY"
    only = set(filter(None, args.only.split(",")))
    rows: list[dict] = []

    targs = argparse.Namespace(students=args.students, exams=max(1, args.students // args.room),
                               traffic="v06", teacher_user=args.teacher_user,
                               teacher_password=args.teacher_password)
    conn = aiohttp.TCPConnector(limit=args.conc * 2)
    async with aiohttp.ClientSession(base_url=args.base, connector=conn, headers=lt.APP_HEADERS,
                                     timeout=aiohttp.ClientTimeout(total=300)) as c:
        exams, th = await lt.setup_exams(c, targs)
        static = await lt.discover_static(c)
        S = [dict(idx=i, exam=exams[lt.room_of(i, len(exams))]) for i in range(1, args.students + 1)]
        print(f"{len(exams)} rooms of ~{args.room}; kinds {[e['kind'] for e in exams]}")
        print(f"{'endpoint':<22}{'n':>6}{'api ms':>9}{'pg ms':>9}{'redis ms':>9}{'req/s':>9}"
              f"{'p50':>8}{'p95':>8}{'err':>6}{'KB/resp':>9}")

        async def phase(name, n, make, conc=None):
            if only and name not in only and name not in ("login", "challenge", "start"):
                await burst(name, n, conc or args.conc, make)
                return
            before = meter.snap()
            t0 = time.perf_counter()
            await burst(name, n, conc or args.conc, make)
            wall = time.perf_counter() - t0
            report(name, n, before, meter.snap(), wall, rows)

        # --- sign in, start (exam-start costs) ---
        async def login(i):
            s = S[i]
            r = await lt.call(c, "login", "POST", "/api/auth/login",
                              json_body={"username": f"lt{s['idx']:04d}", "password": "loadtest"})
            s["h"] = {"Authorization": f"Bearer {r[1]['token']}"}
        await phase("login", len(S), login, conc=8)

        async def exams_list(i):
            await lt.call(c, "student_exams", "GET", "/api/student/exams", headers=S[i]["h"])
        await phase("student_exams", len(S), exams_list)

        async def package(i):
            await lt.call(c, "package", "GET", f"/api/student/exams/{S[i]['exam']['id']}/package",
                          headers=S[i]["h"])
        await phase("package", len(S), package)

        async def challenge(i):
            s = S[i]
            r = await lt.call(c, "challenge", "GET", f"/api/student/exams/{s['exam']['id']}/challenge",
                              headers=s["h"])
            s["nonce"] = r[1]["nonce"]
        await phase("challenge", len(S), challenge)

        async def start(i):
            s = S[i]
            dev = f"cost-device-{s['idx']:06d}"
            s["dev"] = dev
            r = await lt.call(c, "start", "POST", f"/api/student/exams/{s['exam']['id']}/start",
                              headers=s["h"], json_body={
                                  "start_code": s["exam"]["start_code"],
                                  "client": {"device_id": dev, "nonce": s["nonce"], "version": lt.APP_VERSION,
                                             "signature": lt._sign(key, f"start|{s['nonce']}|{dev}")}})
            a = r[1]
            s["aid"], s["qs"], s["sess"] = a["attempt_id"], a["questions"], a["session"]
            s["h"] = {**s["h"], "X-FairPlay-Session": a["session"]}
            s["writer"] = lt.Writer(a["questions"], True)
            s["seq"] = 0
            s["ts"] = 0
        await phase("start", len(S), start)

        async def get_attempt(i):
            s = S[i]
            await lt.call(c, "get_attempt", "GET", f"/api/student/attempts/{s['aid']}", headers=s["h"])
        await phase("get_attempt", len(S), get_attempt)

        async def offline_key(i):
            s = S[i]
            ts = int(time.time() * 1000)
            s["ts"] = ts
            await lt.call(c, "offline_key", "POST", f"/api/student/attempts/{s['aid']}/offline-key",
                          headers=s["h"], json_body={"session": s["sess"], "device_id": s["dev"], "ts": ts,
                                                     "signature": lt._sign(key, f"offkey|{s['aid']}|{s['sess']}|{s['dev']}|{ts}")})
        await phase("offline_key", len(S), offline_key)

        # --- steady state ---
        n = len(S) * args.repeat

        async def heartbeat(i):
            s = S[i % len(S)]
            await lt.call(c, "heartbeat", "POST", f"/api/student/attempts/{s['aid']}/heartbeat", headers=s["h"],
                          json_body={"current_question": 3, "locked": True, "battery": 0.71, "charging": False})
        await phase("heartbeat", n, heartbeat)

        async def app_beat(i):
            s = S[i % len(S)]
            s["ts"] = ts = max(int(time.time() * 1000), s["ts"] + 1)
            await lt.call(c, "app_beat", "POST", f"/api/student/attempts/{s['aid']}/app-beat", headers=s["h"],
                          json_body={"session": s["sess"], "device_id": s["dev"], "ts": ts, "locked": True,
                                     "checks": lt.CHECKS,
                                     "signature": lt._sign(key, f"beat|{s['aid']}|{s['sess']}|{s['dev']}|{ts}|1|{lt.CHECKS}")})
        await phase("app_beat", n, app_beat)

        async def save(i):
            s = S[i % len(S)]
            w = s["writer"]
            w.apply(random.uniform(3, 15))
            w.cur = (w.cur + 1) % len(w.qs)
            s["seq"] += 1
            await lt.call(c, "save_answers", "PUT", f"/api/student/attempts/{s['aid']}/answers", headers=s["h"],
                          json_body={"answers": w.answers, "client_seq": s["seq"]})
        await phase("save_answers", n, save)

        async def events(i):
            s = S[i % len(S)]
            await lt.call(c, "events", "POST", f"/api/student/attempts/{s['aid']}/events", headers=s["h"],
                          json_body={"events": [{"type": "other_app_foreground", "detail": "Notes",
                                                 "client_time": "2026-01-01T00:00:00Z"}] * 2})
        await phase("events", len(S), events)

        async def live(i):
            e = exams[i % len(exams)]
            await lt.call(c, "live", "GET", f"/api/exams/{e['id']}/live", headers=th)
        await phase("live", max(60, len(exams) * 20), live, conc=4)

        if static:
            urls = static["pages"] + static["assets"] + static["code"]

            async def st(i):
                url = urls[i % len(urls)]
                await lt.call(c, "static(gzip)", "GET", url, ok=(200,),
                              headers={"Accept-Encoding": "gzip, br"}, read_json=False)
            await phase("static(gzip)", len(urls) * 30, st, conc=8)

            async def st304(i):
                url = urls[i % len(urls)]
                v = static["validators"].get(url, {})
                hdrs = {"Accept-Encoding": "gzip"}
                if v.get("ETag"):
                    hdrs["If-None-Match"] = v["ETag"]
                if v.get("Last-Modified"):
                    hdrs["If-Modified-Since"] = v["Last-Modified"]
                await lt.call(c, "static(revalidate)", "GET", url, ok=(200, 304), headers=hdrs, read_json=False)
            await phase("static(revalidate)", len(urls) * 30, st304, conc=8)

        async def submit(i):
            s = S[i]
            await lt.call(c, "submit", "POST", f"/api/student/attempts/{s['aid']}/submit", headers=s["h"],
                          json_body={"answers": s["writer"].answers, "client_seq": s["seq"] + 1})
        await phase("submit", len(S), submit)

        for e in exams:
            await lt.call(c, "close_exam", "POST", f"/api/exams/{e['id']}/close", headers=th)
    errs = {k: v for k, v in lt.ERR.items() if v}
    if errs:
        print("errors:", errs, dict(lt.ERR_SAMPLES))
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    asyncio.run(main())

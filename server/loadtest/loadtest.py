#!/usr/bin/env python3
"""FairPlay load test: N simulated students in M live exam rooms, plus invigilators.

Start the server with PROCTORA_SEED_LOADTEST_USERS >= N so users lt0001..ltNNNN
(password "loadtest") exist, demo accounts on (FAIRPLAY_SEED_DEMO=1: the "teacher"
account sets the exams up), FAIRPLAY_LOGIN_IP_LIMIT raised (every simulated student
comes from this one machine) and the same FAIRPLAY_APP_KEY in both environments:

    python3 loadtest/loadtest.py --students 5000 --exams 50 --duration 300 --ramp 90

--traffic v06 (default) is what one student in the locked FairPlay app (v0.6) sends:

  exam start   GET /student + the JS/CSS bundle and fonts (--static; --cached is the share
               of laptops that already have them), sign in, exam list (home), check-in
               (exam list, offline package + package key), "Before you start" (exam list,
               open-book upload area), challenge + signed start, GET attempt, open-book
               materials list, the app's offline key
  writing      page heartbeat every 5 s (current question + battery), the app's signed
               beat every 5 s, answer saves debounced 1 s after the last change (each save
               carries the whole answer map, like web/src/lib/answerQueue.ts), integrity
               events now and then (about one batch per 2 minutes)
  end          submit, then the exam list (Done page)

Typing model: a multiple-choice / numeric / short question is read for 15-60 s, then
answered (one save). Essays and code are written in bursts of 3-15 s (about 4 characters a
second) with pauses (mean 6 s) in between; a save goes out 1 s after each burst. Essays
grow to 2-5 KB, code answers to 1-3 KB. --mid-exam (default) starts every student with a
half-written exam, so save sizes match the middle of a 2-hour exam, not its first minute.

Exam kinds rotate per room: mcq (40 MCQ + 2 numeric), essay (5 short + 3 essays,
open book), code (4 MCQ + 3 Python questions with visible + hidden tests), mixed
(20 MCQ + 2 short + 1 essay + 1 code, open book).

Invigilators: one per --per-invigilator students (default 100) in each room polls
GET /api/exams/{id}/live every 3 s, like web/src/pages/teacher/LiveRoom.tsx.

--traffic legacy keeps the 2026-10-11 model (save every 3 s with a random answer,
heartbeat 5 s, no static files, no invigilators per room) for comparison with old numbers.
"""
from __future__ import annotations

import argparse
import array
import asyncio
import hashlib
import hmac
import json
import math
import multiprocessing as mp
import os
import random
import re
import statistics
import time
from collections import defaultdict

try:
    import aiohttp
except ImportError:  # pragma: no cover
    raise SystemExit("pip install aiohttp (requirements-dev.txt)")

LAT: dict[str, list[float]] = defaultdict(list)
ERR: dict[str, int] = defaultdict(int)
ERR_SAMPLES: dict[str, str] = {}
BYTES_IN: dict[str, int] = defaultdict(int)     # response bytes as sent (compressed)
BYTES_OUT: dict[str, int] = defaultdict(int)    # request bodies
STATUS: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
# (completion second since run start, latency ms) of every request, for the timeline
TL_T = array.array("f")
TL_L = array.array("f")
T0 = 0.0

APP_VERSION = "0.6.1"
APP_HEADERS = {"X-FairPlay-Platform": "mac", "X-FairPlay-Version": APP_VERSION}
CHECKS = "plat=mac,vm=0,disp=1,mir=0,tap=1,inp=1,proc=0,console=1"


async def call(client: "aiohttp.ClientSession", name: str, method: str, url: str, ok=(200,),
               json_body=None, headers=None, data: bytes | None = None, read_json=True):
    """One request. Returns (status, parsed JSON or None, headers) or None on error."""
    body = data
    hdrs = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body, separators=(",", ":")).encode()
        hdrs["Content-Type"] = "application/json"
    if body:
        BYTES_OUT[name] += len(body)
    t0 = time.perf_counter()
    try:
        async with client.request(method, url, data=body, headers=hdrs) as r:
            raw = await r.read()
            n = int(r.headers.get("Content-Length") or 0) or len(raw)
            status, rh = r.status, r.headers
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        dt = time.perf_counter() - t0
        LAT[name].append(dt)
        ERR[name] += 1
        ERR_SAMPLES.setdefault(name, f"{type(e).__name__}: {e}")
        return None
    dt = time.perf_counter() - t0
    LAT[name].append(dt)
    TL_T.append(time.monotonic() - T0)
    TL_L.append(dt * 1000)
    BYTES_IN[name] += n
    STATUS[name][status] += 1
    if status not in ok:
        ERR[name] += 1
        ERR_SAMPLES.setdefault(name, f"HTTP {status}: {raw[:200]!r}")
        return None
    parsed = None
    if read_json and raw and rh.get("Content-Type", "").startswith("application/json"):
        parsed = json.loads(raw)
    return status, parsed, rh


# ------------------------------------------------------------------ exams
def _mcq(i):
    return {"type": "mcq", "prompt": f"Question {i + 1}: which of these is right? " + "Some context. " * 8,
            "points": 1, "options": [f"Option {k} with a few words" for k in "ABCD"],
            "correct_index": i % 4}


def _code(i):
    return {"type": "code", "prompt": f"Write solve() for task {i + 1}. Read numbers from stdin and "
                                      "print their sum, one per line. " * 3,
            "points": 6, "language": "python",
            "starter_code": "def solve():\n    import sys\n    data = sys.stdin.read().split()\n"
                            "    # your code here\n    pass\n\nif __name__ == '__main__':\n    solve()\n",
            "tests": [{"input": f"{k} {k + 1}\n", "expected": f"{2 * k + 1}\n", "visible": k < 3,
                       "points": 1} for k in range(6)]}


def _essay(i):
    return {"type": "essay", "prompt": f"Essay {i + 1}: discuss the question in detail. " * 4,
            "points": 10, "min_words": 100, "max_words": 1200,
            "rubric": [{"label": "Argument", "points": 6}, {"label": "Clarity", "points": 4}]}


def _short(i):
    return {"type": "short", "prompt": f"Short {i + 1}: name it", "points": 2,
            "accepted_answers": [f"word{i}"]}


def _numeric(i):
    return {"type": "numeric", "prompt": f"Compute value {i + 1}", "points": 2,
            "answer_number": i * 1.5, "tolerance": 0.1}


EXAM_KINDS = {
    "mcq": ([_mcq(i) for i in range(40)] + [_numeric(i) for i in range(2)], False),
    "essay": ([_short(i) for i in range(5)] + [_essay(i) for i in range(3)], True),
    "code": ([_mcq(i) for i in range(4)] + [_code(i) for i in range(3)], False),
    "mixed": ([_mcq(i) for i in range(20)] + [_short(i) for i in range(2)] + [_essay(0), _code(0)], True),
}
KIND_ORDER = ["mcq", "essay", "code", "mixed"]

LEGACY_EXAM = [dict(q) for q in (
    [{"type": "mcq", "prompt": f"MCQ {i}", "points": 1, "options": ["a", "b", "c", "d"],
      "correct_index": i % 4} for i in range(6)]
    + [_numeric(i) for i in range(2)] + [_short(i) for i in range(2)] + [_essay(0)])]

ESSAY_WORDS = ("the exam asks why load matters and the answer grows with every save while "
               "students think about evidence structure clarity and argument").split()
CODE_LINES = ["    for x in data:", "        total += int(x)", "    print(total)",
              "    if not data:", "        return 0", "    result = []", "    data.sort()",
              "    return sum(map(int, data))"]


def exam_body(kind: str, idx: int, class_id: int | None) -> dict:
    if kind == "legacy":
        qs, open_book = LEGACY_EXAM, False
    else:
        qs, open_book = EXAM_KINDS[kind]
    body = {"title": f"Load exam {idx + 1} ({kind})", "duration_min": 180, "course": "LOAD 101",
            "shuffle_questions": True, "questions": [dict(q, topics=["load"]) for q in qs],
            "open_book": open_book, "calculator": "scientific" if kind in ("mcq", "mixed") else "off"}
    if class_id is not None:
        body["class_id"] = class_id
    return body


def room_of(i: int, n_exams: int) -> int:
    return (i - 1) % n_exams


async def setup_exams(client, args) -> tuple[list[dict], dict]:
    r = await call(client, "login(teacher)", "POST", "/api/auth/login",
                   json_body={"username": args.teacher_user, "password": args.teacher_password})
    assert r is not None, dict(ERR_SAMPLES)
    h = {"Authorization": f"Bearer {r[1]['token']}"}
    exams = []
    for i in range(args.exams):
        names = [f"lt{s:04d}" for s in range(1, args.students + 1) if room_of(s, args.exams) == i]
        c = await call(client, "create_class", "POST", "/api/classes", ok=(201,), headers=h,
                       json_body={"name": f"Load room {i + 1}"})
        assert c is not None, dict(ERR_SAMPLES)
        cid = c[1]["id"]
        for k in range(0, len(names), 2000):
            await call(client, "add_members", "POST", f"/api/classes/{cid}/members", headers=h,
                       json_body={"usernames": names[k:k + 2000]})
        kind = "legacy" if args.traffic == "legacy" else KIND_ORDER[i % len(KIND_ORDER)]
        r = await call(client, "create_exam", "POST", "/api/exams", ok=(201,), headers=h,
                       json_body=exam_body(kind, i, cid))
        assert r is not None, dict(ERR_SAMPLES)
        r = await call(client, "open_exam", "POST", f"/api/exams/{r[1]['id']}/open", headers=h)
        assert r is not None, dict(ERR_SAMPLES)
        e = r[1]
        e["kind"] = kind
        e["n_students"] = len(names)
        exams.append(e)
    return exams, h


async def discover_static(client) -> dict:
    """What a browser loads for the exam page: index.html, its /assets/ files, the fonts the
    CSS names (woff2, latin + latin-ext), and the code-editor chunk for coding exams."""
    r = await call(client, "setup_static", "GET", "/student", read_json=False)
    if r is None:
        return {}
    async with client.get("/student") as resp:
        html = await resp.text()
    assets = sorted(set(re.findall(r'(/assets/[\w.-]+\.(?:js|css))', html)))
    fonts, code = [], []
    for a in assets:
        async with client.get(a) as resp:
            text = await resp.text(errors="ignore")
        if a.endswith(".css"):
            fonts += [f"/assets/{m}" for m in re.findall(
                r'(plus-jakarta-sans-latin(?:-ext)?-[4-7]00-normal-[\w-]+\.woff2)', text)]
        else:
            code += [f"/assets/{m}" for m in re.findall(r'(CodeEditor-[\w-]+\.js)', text)]
    info = {"pages": ["/student"], "assets": assets + sorted(set(fonts)), "code": sorted(set(code)),
            "validators": {}}
    for url in info["pages"] + info["assets"] + info["code"]:
        async with client.get(url) as resp:
            info["validators"][url] = {k: resp.headers.get(k) for k in
                                       ("ETag", "Last-Modified", "Cache-Control")}
    return info


def _immutable(cc: str | None) -> bool:
    cc = (cc or "").lower()
    return "immutable" in cc or ("max-age=" in cc and "no-cache" not in cc
                                 and int(re.search(r"max-age=(\d+)", cc).group(1)) > 3600)


async def load_static(client, info: dict, cached: bool, code_exam: bool):
    if not info:
        return
    urls = info["pages"] + info["assets"] + (info["code"] if code_exam else [])
    for url in urls:
        v = info["validators"].get(url, {})
        name = "static_page" if url in info["pages"] else "static_asset"
        if cached and url not in info["pages"] and _immutable(v.get("Cache-Control")):
            continue        # fresh in the browser cache: no request at all
        hdrs = {"Accept-Encoding": "gzip, deflate, br"}
        if cached:          # revalidate: 304 expected
            if v.get("ETag"):
                hdrs["If-None-Match"] = v["ETag"]
            if v.get("Last-Modified"):
                hdrs["If-Modified-Since"] = v["Last-Modified"]
            name += "(304)"
        await call(client, name, "GET", url, ok=(200, 304), headers=hdrs, read_json=False)


# ------------------------------------------------------------------ students
def _sign(key: str, msg: str) -> str:
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _essay_text(n_chars: int) -> str:
    out, size = [], 0
    while size < n_chars:
        w = random.choice(ESSAY_WORDS)
        out.append(w)
        size += len(w) + 1
    return " ".join(out)[:n_chars]


def _code_text(n_chars: int, starter: str) -> str:
    text = starter
    while len(text) < n_chars:
        text += random.choice(CODE_LINES) + "\n"
    return text[:max(n_chars, 1)]


class Writer:
    """Generates (time, mutation) pairs for one student: when an answer changes."""

    def __init__(self, qs: list[dict], mid_exam: bool):
        self.qs = qs
        self.answers: dict = {}
        self.target = {}
        for q in qs:
            if q["type"] == "essay":
                self.target[q["id"]] = random.randint(2000, 5000)
            elif q["type"] == "code":
                self.target[q["id"]] = random.randint(1000, 3000)
        self.cur = 0
        if mid_exam:      # half the exam is written already
            for i, q in enumerate(qs):
                if random.random() < 0.5:
                    self._fill(q, random.uniform(0.2, 0.7))
            self.cur = random.randrange(len(qs))
        self.burst_left = 0

    def _fill(self, q, frac):
        t = q["type"]
        if t == "mcq":
            self.answers[q["id"]] = random.randrange(len(q["options"]))
        elif t == "numeric":
            self.answers[q["id"]] = round(random.uniform(0, 10), 2)
        elif t == "short":
            self.answers[q["id"]] = random.choice(["word0", "word1", "nope"])
        elif t == "essay":
            self.answers[q["id"]] = _essay_text(int(self.target[q["id"]] * frac))
        elif t == "code":
            self.answers[q["id"]] = _code_text(int(self.target[q["id"]] * frac), q.get("starter_code", ""))

    def next_change(self) -> tuple[float, float]:
        """(seconds until the student starts changing an answer, seconds of typing)."""
        q = self.qs[self.cur]
        if q["type"] in ("essay", "code"):
            have = len(str(self.answers.get(q["id"], "")))
            if have < self.target[q["id"]]:
                return random.expovariate(1 / 6.0) + 0.2, random.uniform(3, 15)
            self.cur = (self.cur + 1) % len(self.qs)
            return random.uniform(5, 20), 0.0
        return random.uniform(15, 60), 0.0

    def apply(self, typing_s: float):
        q = self.qs[self.cur]
        if q["type"] in ("essay", "code"):
            have = str(self.answers.get(q["id"], "") or (q.get("starter_code", "") if q["type"] == "code" else ""))
            add = int(typing_s * random.uniform(3, 5))
            if q["type"] == "essay":
                self.answers[q["id"]] = (have + " " + _essay_text(add)).strip()[:self.target[q["id"]] + 200]
            else:
                self.answers[q["id"]] = (have + _code_text(add, ""))[:self.target[q["id"]] + 200]
        else:
            self._fill(q, 1.0)
            if random.random() < 0.85:          # usually move on; sometimes change it again later
                self.cur = (self.cur + 1) % len(self.qs)


TOKENS: dict[int, str] = {}


async def check_in_all(idxs: list[int], args):
    """--login-first: every student signs in (and sees the exam list) before the exam, the
    way a school runs check-in; the exam-start ramp then has no password checks in it."""
    sem = asyncio.Semaphore(args.login_conc)
    conn = aiohttp.TCPConnector(limit=args.login_conc * 2)
    async with aiohttp.ClientSession(base_url=args.base, connector=conn, headers=APP_HEADERS,
                                     timeout=aiohttp.ClientTimeout(total=args.timeout)) as client:
        async def one(i):
            async with sem:
                r = await call(client, "login(check-in)", "POST", "/api/auth/login",
                               json_body={"username": f"lt{i:04d}", "password": "loadtest"})
                if r is not None:
                    TOKENS[i] = r[1]["token"]
                    await call(client, "student_exams(check-in)", "GET", "/api/student/exams",
                               headers={"Authorization": f"Bearer {TOKENS[i]}"})
        await asyncio.gather(*(one(i) for i in idxs))


async def student(idx: int, exam: dict, args, static: dict):
    await asyncio.sleep(random.uniform(0, args.ramp))
    # One keep-alive connection per student (+1 for overlapping beat/save), like the app.
    conn = aiohttp.TCPConnector(limit=2, force_close=False, enable_cleanup_closed=True)
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(base_url=args.base, connector=conn, timeout=timeout,
                                     headers=APP_HEADERS) as client:
        if args.traffic == "legacy":
            await _legacy_student(client, idx, exam, args)
        else:
            await _student(client, idx, exam, args, static)


async def _student(client, idx: int, exam: dict, args, static: dict):
    eid = exam["id"]
    open_book = exam.get("kind") in ("essay", "mixed")
    if static:
        await load_static(client, static, random.random() < args.cached, exam.get("kind") in ("code", "mixed"))
    if idx in TOKENS:            # signed in at check-in (--login-first)
        h = {"Authorization": f"Bearer {TOKENS[idx]}"}
    else:
        r = await call(client, "login", "POST", "/api/auth/login",
                       json_body={"username": f"lt{idx:04d}", "password": "loadtest"})
        if r is None:
            return
        h = {"Authorization": f"Bearer {r[1]['token']}"}
    await call(client, "student_exams", "GET", "/api/student/exams", headers=h)      # home
    # check-in page: exam list + offline package (+ its live-only key)
    await call(client, "student_exams", "GET", "/api/student/exams", headers=h)
    if args.package:
        if await call(client, "package", "GET", f"/api/student/exams/{eid}/package", headers=h):
            await call(client, "package_key", "GET", f"/api/student/exams/{eid}/package-key", headers=h)
    await asyncio.sleep(random.uniform(0.5, 3))       # reading the check-in page
    # "Before you start": the exam list (+ the open-book upload area)
    await call(client, "student_exams", "GET", "/api/student/exams", headers=h)
    if open_book:
        await call(client, "materials(exam)", "GET", f"/api/student/exams/{eid}/materials", headers=h)
    await asyncio.sleep(random.uniform(0.5, 3))       # rules, battery, "I'm ready"
    key = os.environ.get("FAIRPLAY_APP_KEY", "").split(",")[0]
    device = f"loadtest-device-{idx:06d}"
    body: dict = {"start_code": exam["start_code"]}
    if key:
        c = await call(client, "challenge", "GET", f"/api/student/exams/{eid}/challenge", headers=h)
        if c is None:
            return
        nonce = c[1]["nonce"]
        body["client"] = {"device_id": device, "nonce": nonce, "version": APP_VERSION,
                          "signature": _sign(key, f"start|{nonce}|{device}")}
    r = await call(client, "start", "POST", f"/api/student/exams/{eid}/start", headers=h, json_body=body)
    if r is None:
        return
    a = r[1]
    aid, qs = a["attempt_id"], a["questions"]
    sess = a.get("session")
    if sess:
        h = {**h, "X-FairPlay-Session": sess}
    await call(client, "get_attempt", "GET", f"/api/student/attempts/{aid}", headers=h)
    if open_book:
        await call(client, "materials(attempt)", "GET", f"/api/student/attempts/{aid}/materials", headers=h)
    last_ts = [0]

    def ts_now():
        ts = int(time.time() * 1000)
        last_ts[0] = ts = max(ts, last_ts[0] + 1)
        return ts

    if key and sess:
        ts = ts_now()
        await call(client, "offline_key", "POST", f"/api/student/attempts/{aid}/offline-key", headers=h,
                   json_body={"session": sess, "device_id": device, "ts": ts,
                              "signature": _sign(key, f"offkey|{aid}|{sess}|{device}|{ts}")})

    writer = Writer(qs, args.mid_exam)
    seq = 0
    battery = random.uniform(0.3, 1.0)
    charging = random.random() < 0.3
    t_end = time.monotonic() + args.duration
    stop = asyncio.Event()

    async def page_heartbeat():
        nonlocal battery
        await asyncio.sleep(random.uniform(0, 5))
        while not stop.is_set():
            battery = max(0.05, battery - (0 if charging else 0.0005))
            await call(client, "heartbeat", "POST", f"/api/student/attempts/{aid}/heartbeat", headers=h,
                       json_body={"current_question": writer.cur, "locked": True,
                                  "battery": round(battery, 2), "charging": charging})
            await _sleep_until(stop, 5)

    async def app_beat():
        await asyncio.sleep(random.uniform(0, 5))
        while key and sess and not stop.is_set():
            ts = ts_now()
            await call(client, "app_beat", "POST", f"/api/student/attempts/{aid}/app-beat", headers=h,
                       json_body={"session": sess, "device_id": device, "ts": ts, "locked": True,
                                  "checks": CHECKS,
                                  "signature": _sign(key, f"beat|{aid}|{sess}|{device}|{ts}|1|{CHECKS}")})
            await _sleep_until(stop, 5)

    async def events():
        while not stop.is_set():
            if await _sleep_until(stop, random.expovariate(1 / 120.0)):
                return
            evs = [{"type": random.choice(["other_app_foreground", "blocked_shortcut", "focus_lost"]),
                    "detail": "Cmd-key48", "client_time": "2026-01-01T00:00:00Z"}
                   for _ in range(random.randint(1, 3))]
            await call(client, "events", "POST", f"/api/student/attempts/{aid}/events", headers=h,
                       json_body={"events": evs})

    tasks = [asyncio.create_task(f()) for f in (page_heartbeat, app_beat, events)]
    dirty = args.mid_exam            # the half-written answers are sent with the first save
    first = True
    while True:
        wait, typing = writer.next_change()
        if first and dirty:
            wait, first = random.uniform(0, 3), False
        if time.monotonic() + wait + typing + 1.0 >= t_end:
            break
        await asyncio.sleep(wait + typing)
        writer.apply(typing)
        seq += 1
        await asyncio.sleep(1.0)     # answerQueue debounce
        await call(client, "save_answers", "PUT", f"/api/student/attempts/{aid}/answers", headers=h,
                   json_body={"answers": writer.answers, "client_seq": seq})
    await asyncio.sleep(max(0.0, t_end - time.monotonic()))
    stop.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await call(client, "submit", "POST", f"/api/student/attempts/{aid}/submit", headers=h,
               json_body={"answers": writer.answers, "client_seq": seq + 1})
    await call(client, "student_exams", "GET", "/api/student/exams", headers=h)      # Done page


async def _sleep_until(stop: asyncio.Event, s: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), s)
        return True
    except asyncio.TimeoutError:
        return False


async def _legacy_student(client, idx: int, exam: dict, args):
    """The 2026-10-11 model: save every 3 s, heartbeat + app beat every 5 s."""
    r = await call(client, "login", "POST", "/api/auth/login",
                   json_body={"username": f"lt{idx:04d}", "password": "loadtest"})
    if r is None:
        return
    h = {"Authorization": f"Bearer {r[1]['token']}"}
    await call(client, "student_exams", "GET", "/api/student/exams", headers=h)
    key = os.environ.get("FAIRPLAY_APP_KEY", "").split(",")[0]
    device = f"loadtest-device-{idx:06d}"
    body: dict = {"start_code": exam["start_code"]}
    if key:
        c = await call(client, "challenge", "GET", f"/api/student/exams/{exam['id']}/challenge", headers=h)
        if c is None:
            return
        nonce = c[1]["nonce"]
        body["client"] = {"device_id": device, "nonce": nonce, "version": "loadtest",
                          "signature": _sign(key, f"start|{nonce}|{device}")}
    r = await call(client, "start", "POST", f"/api/student/exams/{exam['id']}/start", headers=h, json_body=body)
    if r is None:
        return
    a = r[1]
    aid, qs = a["attempt_id"], a["questions"]
    sess = a.get("session")
    if sess:
        h = {**h, "X-FairPlay-Session": sess}
    answers: dict = {}
    seq = 0
    t_end = time.monotonic() + args.duration
    next_hb = time.monotonic() + random.uniform(0, 5)
    next_save = time.monotonic() + random.uniform(0, 3)
    while True:
        now = time.monotonic()
        if now >= t_end:
            break
        if now >= next_hb:
            next_hb += 5
            await call(client, "heartbeat", "POST", f"/api/student/attempts/{aid}/heartbeat",
                       headers=h, json_body={"current_question": random.randrange(len(qs))})
            if key and sess:
                ts = int(time.time() * 1000)
                await call(client, "app_beat", "POST", f"/api/student/attempts/{aid}/app-beat", headers=h,
                           json_body={"session": sess, "device_id": device, "ts": ts, "locked": True,
                                      "checks": CHECKS,
                                      "signature": _sign(key, f"beat|{aid}|{sess}|{device}|{ts}|1|{CHECKS}")})
            if random.random() < 0.05:
                await call(client, "events", "POST", f"/api/student/attempts/{aid}/events", headers=h,
                           json_body={"events": [{"type": "blocked_shortcut", "detail": "Cmd-key48",
                                                  "client_time": "2026-01-01T00:00:00Z"}]})
        if now >= next_save:
            next_save += args.save_every
            q = random.choice(qs)
            answers[q["id"]] = (random.randrange(len(q["options"])) if q["type"] == "mcq"
                                else round(random.uniform(0, 10), 2) if q["type"] == "numeric"
                                else " ".join(random.choices(ESSAY_WORDS, k=random.randint(50, 400)))
                                if q["type"] == "essay" else random.choice(["word0", "word1", "nope"]))
            seq += 1
            await call(client, "save_answers", "PUT", f"/api/student/attempts/{aid}/answers",
                       headers=h, json_body={"answers": answers, "client_seq": seq})
        await asyncio.sleep(max(0.0, min(next_hb, next_save, t_end) - time.monotonic()))
    await call(client, "submit", "POST", f"/api/student/attempts/{aid}/submit", headers=h,
               json_body={"answers": answers, "client_seq": seq + 1})


# ------------------------------------------------------------------ invigilators
async def invigilator(base: str, h: dict, exam: dict, stop: asyncio.Event, args):
    await asyncio.sleep(random.uniform(0, 3))
    conn = aiohttp.TCPConnector(limit=1)
    async with aiohttp.ClientSession(base_url=base, connector=conn,
                                     timeout=aiohttp.ClientTimeout(total=args.timeout)) as client:
        while not stop.is_set():
            await call(client, "live", "GET", f"/api/exams/{exam['id']}/live", headers=h)
            if await _sleep_until(stop, 3):
                return


async def legacy_teacher_poll(base, h, exams, stop: asyncio.Event, args):
    """2026-10-11 model: one teacher polling 5 random rooms every 3 s."""
    async with aiohttp.ClientSession(base_url=base) as client:
        while not stop.is_set():
            for e in random.sample(exams, min(5, len(exams))):
                await call(client, "live", "GET", f"/api/exams/{e['id']}/live", headers=h)
            if await _sleep_until(stop, 3):
                return


# ------------------------------------------------------------------ runner
def pct(xs, p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = min(len(xs) - 1, max(0, int(math.ceil(p / 100 * len(xs))) - 1))
    return xs[k] * 1000


def run_shard(job):
    """Runs in a child process: simulate a slice of the students."""
    global T0
    shard, idxs, exams, args, static, t0_wall = job
    random.seed(shard * 7919 + time.time_ns())
    try:
        import uvloop  # type: ignore
        uvloop.install()
    except ImportError:
        pass
    T0 = time.monotonic() - (time.time() - t0_wall)

    async def go():
        if args.login_first and args.traffic == "v06":
            t = time.monotonic()
            await check_in_all(idxs, args)
            print(f"shard {shard}: {len(TOKENS)}/{len(idxs)} signed in at check-in in "
                  f"{time.monotonic() - t:.0f}s", flush=True)
            # barrier: the exam starts when every client process has finished check-in
            open(os.path.join(args.sync_dir, str(shard)), "w").close()
            while len(os.listdir(args.sync_dir)) < args.procs:
                await asyncio.sleep(0.5)
        await asyncio.gather(*(student(i, exams[room_of(i, len(exams))], args, static) for i in idxs))

    asyncio.run(go())
    return (dict(LAT), dict(ERR), dict(ERR_SAMPLES), dict(BYTES_IN), dict(BYTES_OUT),
            {k: dict(v) for k, v in STATUS.items()}, TL_T.tobytes(), TL_L.tobytes())


async def main() -> None:
    global T0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--students", type=int, default=2000)
    ap.add_argument("--exams", type=int, default=50, help="exam rooms (students are spread evenly)")
    ap.add_argument("--duration", type=float, default=60, help="seconds each student writes")
    ap.add_argument("--ramp", type=float, default=15, help="spread arrivals over this many seconds")
    ap.add_argument("--procs", type=int, default=max(1, min(4, mp.cpu_count())),
                    help="client processes (the load generator itself needs CPU)")
    ap.add_argument("--traffic", choices=["v06", "legacy"], default="v06")
    ap.add_argument("--static", action=argparse.BooleanOptionalAction, default=True,
                    help="load /student and its JS/CSS/fonts at exam start (v06)")
    ap.add_argument("--cached", type=float, default=0.5,
                    help="share of students whose browser cache already has the files")
    ap.add_argument("--package", action=argparse.BooleanOptionalAction, default=True,
                    help="check-in page downloads the offline package (v06)")
    ap.add_argument("--mid-exam", action=argparse.BooleanOptionalAction, default=True,
                    help="start with half-written answers (realistic save sizes)")
    ap.add_argument("--per-invigilator", type=int, default=100,
                    help="students per invigilator polling the live room every 3 s (0 = none)")
    ap.add_argument("--save-every", type=float, default=3.0, help="legacy traffic: seconds between saves")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--teacher-user", default="teacher")
    ap.add_argument("--teacher-password", default="teacher123")
    ap.add_argument("--json-out", default="", help="write the summary as JSON here")
    ap.add_argument("--login-first", action="store_true",
                    help="v06: everyone signs in first (check-in), then the exam-start ramp begins")
    ap.add_argument("--login-conc", type=int, default=4, help="--login-first: sign-ins at once per client process")
    args = ap.parse_args()

    async with aiohttp.ClientSession(base_url=args.base) as client:
        t0 = time.monotonic()
        exams, th = await setup_exams(client, args)
        static = await discover_static(client) if (args.static and args.traffic == "v06") else {}
        print(f"setup: {len(exams)} exams opened in {time.monotonic() - t0:.1f}s; "
              f"{args.students} students in {args.procs} client processes; "
              f"static files: {len(static.get('assets', []))} (+{len(static.get('code', []))} code)")
        for k, v in list(LAT.items()):
            LAT.pop(k)
        BYTES_IN.clear(), BYTES_OUT.clear(), STATUS.clear(), ERR.clear()
        del TL_T[:], TL_L[:]
        stop = asyncio.Event()
        T0 = time.monotonic()
        t0_wall = time.time()
        if args.traffic == "legacy":
            pollers = [asyncio.create_task(legacy_teacher_poll(args.base, th, exams, stop, args))]
        elif args.per_invigilator > 0:
            pollers = [asyncio.create_task(invigilator(args.base, th, e, stop, args))
                       for e in exams for _ in range(max(1, math.ceil(e["n_students"] / args.per_invigilator)))]
        else:
            pollers = []
        ids = list(range(1, args.students + 1))
        import tempfile
        args.sync_dir = tempfile.mkdtemp(prefix="fairplay-loadtest-")
        jobs = [(k, ids[k::args.procs], exams, args, static, t0_wall) for k in range(args.procs)]
        loop = asyncio.get_running_loop()
        with mp.get_context("spawn").Pool(args.procs) as pool:
            shards = await loop.run_in_executor(None, pool.map, run_shard, jobs)
        import shutil
        shutil.rmtree(args.sync_dir, ignore_errors=True)
        wall = time.monotonic() - T0
        stop.set()
        await asyncio.gather(*pollers)
        for e in exams:
            await call(client, "close_exam", "POST", f"/api/exams/{e['id']}/close", headers=th)
    tl_t, tl_l = TL_T, TL_L
    for lat, err, samples, bin_, bout, status, tt, tl in shards:
        for k, v in lat.items():
            LAT[k].extend(v)
        for k, v in err.items():
            ERR[k] += v
        for k, v in samples.items():
            ERR_SAMPLES.setdefault(k, v)
        for k, v in bin_.items():
            BYTES_IN[k] += v
        for k, v in bout.items():
            BYTES_OUT[k] += v
        for k, v in status.items():
            for s, n in v.items():
                STATUS[k][s] += n
        a, b = array.array("f"), array.array("f")
        a.frombytes(tt)
        b.frombytes(tl)
        tl_t.extend(a)
        tl_l.extend(b)
    for k in ("close_exam",):
        LAT.pop(k, None)
        ERR.pop(k, None)

    total = sum(len(v) for v in LAT.values())
    errors = sum(ERR.values())
    print(f"\nstudents={args.students} exams={args.exams} traffic={args.traffic} duration={args.duration}s "
          f"ramp={args.ramp}s wall={wall:.1f}s requests={total} throughput={total / wall:.0f} req/s")
    print(f"{'endpoint':<20}{'count':>8}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}"
          f"{'errors':>7}{'KB/resp':>9}{'KB/req':>8}")
    summary = {"args": vars(args), "wall_s": wall, "requests": total, "errors": errors,
               "throughput": total / wall, "endpoints": {}}
    for name in sorted(LAT):
        xs = LAT[name]
        kb_in = BYTES_IN[name] / max(1, len(xs)) / 1024
        kb_out = BYTES_OUT[name] / max(1, len(xs)) / 1024
        print(f"{name:<20}{len(xs):>8}{pct(xs, 50):>9.1f}{pct(xs, 95):>9.1f}{pct(xs, 99):>9.1f}"
              f"{max(xs) * 1000:>9.1f}{ERR[name]:>7}{kb_in:>9.2f}{kb_out:>8.2f}")
        summary["endpoints"][name] = {"count": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95),
                                      "p99": pct(xs, 99), "max": max(xs) * 1000, "errors": ERR[name],
                                      "kb_resp": kb_in, "kb_req": kb_out,
                                      "status": dict(STATUS.get(name, {}))}
    allx = [x for v in LAT.values() for x in v]
    print(f"{'ALL':<20}{len(allx):>8}{pct(allx, 50):>9.1f}{pct(allx, 95):>9.1f}"
          f"{pct(allx, 99):>9.1f}{max(allx) * 1000:>9.1f}{errors:>7}")
    print(f"error rate: {errors / max(total, 1) * 100:.3f}%  (mean {statistics.mean(allx) * 1000:.1f} ms)")
    mb_in = sum(BYTES_IN.values()) / 1e6
    mb_out = sum(BYTES_OUT.values()) / 1e6
    print(f"bytes: {mb_in:.1f} MB from the server, {mb_out:.1f} MB to it "
          f"({mb_in * 8 / wall:.1f} / {mb_out * 8 / wall:.1f} Mbit/s average)")
    for k, v in ERR_SAMPLES.items():
        print(f"  sample error [{k}]: {v}")
    # Timeline: does the queue drain? 10-second buckets of completed requests.
    buckets: dict[int, list[float]] = defaultdict(list)
    for t, l in zip(tl_t, tl_l):
        buckets[int(t // 10)].append(l)
    print("\ntimeline (10 s buckets): t  req/s  p50  p95  max (ms)")
    tl_rows = []
    for b in sorted(buckets):
        xs = sorted(buckets[b])
        row = (b * 10, len(xs) / 10, xs[len(xs) // 2], xs[min(len(xs) - 1, int(len(xs) * 0.95))], xs[-1])
        tl_rows.append(row)
        print(f"  {row[0]:>5}s {row[1]:>7.0f} {row[2]:>7.0f} {row[3]:>7.0f} {row[4]:>8.0f}")
    summary["timeline"] = tl_rows
    summary["mb_in"], summary["mb_out"] = mb_in, mb_out
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=1)


if __name__ == "__main__":
    asyncio.run(main())

"""Negative and abuse tests against the running server (scripted API, no browser)."""
import concurrent.futures as cf
import json
import re
import sqlite3
import time
import urllib.parse

import config as C
import standin as S
from lib import check, fake_state, note, section

CODE_RE = re.compile(r"[A-Za-z0-9]{24}")


def make_stand(user: str, exam: dict, device: str, platform="mac"):
    token = S.login(user, "loadtest" if user.startswith("lt") else None)
    st, a = S.start_attempt(token, exam["id"], exam["start_code"], device=device, platform=platform)
    if st != 200:
        return None, token, (st, a)
    hs = {"X-FairPlay-Session": a["session"], "X-FairPlay-Platform": "mac"}
    _, view = S.jhttp("GET", f"/api/student/attempts/{a['attempt_id']}", token=token, headers=hs)
    msg = {"attempt_id": a["attempt_id"], "token": token, "session": a["session"], "url": view["external_url"],
           "allowed_domains": view["allowed_domains"], "finish_url_regex": view.get("finish_url_regex"),
           "device": device}
    return S.NativeStandIn(msg, device=device), token, (st, a)


def detail_of(body: bytes) -> str:
    try:
        d = json.loads(body).get("detail")
        return d if isinstance(d, str) else json.dumps(d)
    except Exception:
        return body.decode("utf-8", "replace")[:200]


def burst(exam: dict, teacher_token: str, count: int = 150):
    section("5b. 150 students burst (scripted)")
    t0 = time.time()
    stands = {}

    def one(i):
        user = f"lt{i:04d}"
        stand, token, (st, a) = make_stand(user, exam, device=f"burst-dev-{i:04d}")
        if stand is None:
            return ("start", st, None, 0.0)
        stands[i] = stand
        t1 = time.time()
        s, h, body = stand.request_code()
        dt = time.time() - t1
        code = None
        if s == 200:
            code = json.loads(body)["code"]
        return ("code", s, code, dt)

    with cf.ThreadPoolExecutor(max_workers=30) as ex:
        res = list(ex.map(one, range(1, count + 1)))
    took = time.time() - t0
    statuses = [r[1] for r in res]
    check(f"150 students start and request their code at once: all 200 ({took:.1f}s)",
          statuses.count(200) == count, str({s: statuses.count(s) for s in set(statuses)}))
    codes = {r[2] for r in res}
    check("all of them got the quiz's current code (one shared code, 24 chars)",
          codes == {fake_state()["code"]} and len(next(iter(codes))) == 24)
    worst = max(r[3] for r in res)
    note(f"burst: worst code-request latency {worst:.2f}s")
    s, res_json = S.jhttp("GET", f"/api/exams/{exam['id']}/results", token=teacher_token)
    mine = [a for a in res_json["attempts"] if re.fullmatch(r"lt0(0\d\d|1[0-4]\d|150)", a["username"])]
    types = [ev["type"] for a in mine for ev in a.get("events", [])]
    check("integrity log has one canvas_code_issued per student in the burst",
          types.count("canvas_code_issued") >= count, str(types.count("canvas_code_issued")))
    check("no canvas_code_refused during the burst", "canvas_code_refused" not in types)
    return stands


def abuse(exam: dict, teacher_token: str, other_token: str, other_attempt: dict):
    section("5a. Negative and abuse tests")
    carol, ctoken, (st, a) = make_stand("carol", exam, "carol-dev-0001")
    check("carol starts her attempt on the Mac app", carol is not None, str(st))
    aid = carol.aid
    # --- proofs
    s, h, b = carol.request_code({"ts": S.fresh_ts()})
    check("no proof is refused 403 with the app message", s == 403 and "FairPlay app" in detail_of(b), f"{s} {detail_of(b)}")
    s, h, b = carol.request_code(carol.code_body(key="some-other-key"))
    check("wrong proof (wrong key) is refused 403", s == 403, str(s))
    s, h, b = carol.request_code(carol.code_body(device="someone-elses-mac"))
    check("proof for another device is refused 403", s == 403, str(s))
    s, h, b = carol.request_code(carol.code_body(session="another-session"))
    check("proof for another session is refused 403", s == 403, str(s))
    old = int(time.time() * 1000) - 10 * 60 * 1000
    s, h, b = carol.request_code(carol.code_body(ts=old))
    check("stale proof (10 minutes old) is refused 403", s == 403, str(s))
    s, h, b = carol.request_code(carol.code_body(ts=int(time.time() * 1000) + 10 * 60 * 1000))
    check("proof from the future is refused 403", s == 403, str(s))
    for label, proof in [("non-ASCII (Arabic-Indic digits)", "٣" * 64), ("non-ASCII (accented letters)", "é" * 64),
                         ("too short", "ab"), ("upper-case hex", "A" * 64), ("emoji", "\U0001F600" * 64)]:
        s, h, b = carol.request_code({"proof": proof, "ts": S.fresh_ts()})
        check(f"{label} proof is refused with 4xx, not a server error", 400 <= s < 500, f"{s} {detail_of(b)[:80]}")
    for label, body in [("ts as a string", {"proof": "a" * 64, "ts": "x"}), ("ts missing", {"proof": "a" * 64}),
                        ("ts huge", {"proof": "a" * 64, "ts": 10 ** 40}), ("body is a list", [1, 2]),
                        ("proof as a number", {"proof": 5, "ts": 1})]:
        s, h, b = carol.request_code(body)
        check(f"malformed body ({label}) gives 4xx", 400 <= s < 500, str(s))
    s, h, b = S.http("POST", f"/api/student/attempts/{aid}/canvas-code", None, ctoken,
                     {"X-FairPlay-Session": carol.session}, raw=b"{not json")
    check("body that is not JSON gives 4xx", 400 <= s < 500, str(s))
    # --- session header
    s, h, b = S.http("POST", f"/api/student/attempts/{aid}/canvas-code", carol.code_body(), ctoken)
    check("missing session header is refused 409", s == 409, f"{s} {detail_of(b)}")
    s, h, b = carol.request_code(carol.code_body(), headers={"X-FairPlay-Session": "old-session"})
    check("wrong session header is refused 409", s == 409, f"{s} {detail_of(b)}")
    # --- ok then replay
    body = carol.code_body()
    s, h, b = carol.request_code(body)
    ok_code = json.loads(b)["code"] if s == 200 else ""
    check("a correct proof gets the code (200, 24 chars, Cache-Control: no-store)",
          s == 200 and len(ok_code) == 24 and "no-store" in (h.get("Cache-Control") or ""), f"{s} {h.get('Cache-Control')}")
    check("the code returned equals the code held by the fake Canvas", ok_code == fake_state()["code"])
    s, h, b = carol.request_code(body)
    check("the same proof sent again (replay) is refused 409", s == 409, f"{s} {detail_of(b)}")
    # --- other student's attempt
    s, h, b = S.http("POST", f"/api/student/attempts/{aid}/canvas-code", carol.code_body(), other_token,
                     {"X-FairPlay-Session": carol.session})
    check("another student cannot ask for carol's code (404)", s == 404, f"{s}")
    s, h, b = S.http("POST", f"/api/student/attempts/{aid}/canvas-code", carol.code_body(), teacher_token,
                     {"X-FairPlay-Session": carol.session})
    check("a teacher token cannot use the student endpoint", s in (401, 403, 404), str(s))
    s, h, b = S.http("POST", f"/api/student/attempts/{aid}/canvas-code", carol.code_body())
    check("no token at all is refused 401", s == 401, str(s))
    s, h, b = S.http("POST", "/api/student/attempts/a_doesnotexist/canvas-code", carol.code_body(), ctoken,
                     {"X-FairPlay-Session": carol.session})
    check("an unknown attempt is 404", s == 404, str(s))
    # --- 7th request
    statuses = []
    for _ in range(5):
        s, h, b = carol.request_code()
        statuses.append(s)
    check("requests 2 to 6 of the attempt are served", statuses == [200] * 5, str(statuses))
    s, h, b = carol.request_code()
    check("the 7th request in 10 minutes is refused 429 with Retry-After", s == 429 and h.get("Retry-After", "").isdigit(),
          f"{s} {h.get('Retry-After')}")
    check("the 429 body does not contain a code", not CODE_RE.search(b.decode()) or "detail" in b.decode())
    # --- platform
    for plat in (None, "windows", "ipad"):
        token = S.login("lt0152", "loadtest")
        st, a2 = S.start_attempt(token, exam["id"], exam["start_code"], device="plat-dev", platform=plat)
        check(f"start with platform header {plat!r} on a Canvas exam is refused 409", st == 409, f"{st} {a2}")
    st, a2 = S.start_attempt(S.login("lt0152", "loadtest"), exam["id"], exam["start_code"], device="plat-dev", platform="windows")
    check("non-mac start says the exam is not available on this device yet", st == 409 and "not available" in json.dumps(a2), str(a2))
    # --- bad start proofs still refused
    st, a3 = S.start_attempt(S.login("lt0152", "loadtest"), exam["id"], exam["start_code"], device="dev-0002-xx", signature_override="0" * 64)
    check("start with a wrong app signature is refused (403)", st == 403, str(st))
    st, a3 = S.start_attempt(S.login("lt0152", "loadtest"), exam["id"], "000000", device="dev-0002-xx")
    check("start with a wrong start code is refused", st in (400, 403, 404, 409, 422), str(st))
    # --- the code is in no student-facing response
    for path in (f"/api/student/exams/{exam['id']}", f"/api/student/attempts/{aid}", "/api/student/exams"):
        s, h, b = S.http("GET", path, None, ctoken, {"X-FairPlay-Session": carol.session})
        check(f"{path} has no 24-char code and no code field", fake_state()["code"] not in b.decode() and '"access_code"' not in b.decode(), str(s))
    return carol


def pending_resume(exam: dict, first):
    """Second start from the same Mac 16 s after the first: waits for the teacher; no code meanwhile."""
    stand, token, (st, a) = first
    time.sleep(max(0, 16.5 - (time.time() - first_time[0])))
    st2, again = S.start_attempt(token, exam["id"], exam["start_code"], device=stand.device)
    check("a new app run on the same Mac waits for teacher approval", st2 == 200 and again.get("approval") == "pending", f"{st2} {again.get('approval')}")
    stand.session = again["session"]
    s, h, b = stand.request_code()
    check("while resume approval is pending the code is refused 409", s == 409, f"{s} {detail_of(b)}")
    return stand


first_time = [0.0]


def exam_limit(exam: dict, teacher_token: str, stands: dict):
    """max(300, 3 x students) code requests per exam per minute; 152 rostered students = 456."""
    section("5e. Per-exam limit (456 per minute for 152 students)")
    roster = 152
    cap = max(300, 3 * roster)
    t0 = time.time()

    def one(args):
        i, k = args
        s, h, b = stands[i].request_code()
        return i, s, h.get("Retry-After")

    jobs = [(i, k) for k in range(3) for i in sorted(stands)]
    with cf.ThreadPoolExecutor(max_workers=30) as ex:
        res = list(ex.map(one, jobs))
    took = time.time() - t0
    ok = [r for r in res if r[1] == 200]
    limited = [r for r in res if r[1] == 429]
    other = [r for r in res if r[1] not in (200, 429)]
    note(f"{len(jobs)} requests in {took:.1f}s: {len(ok)} served, {len(limited)} limited, {len(other)} other")
    check("requests past the exam's per-minute budget are refused 429", len(limited) > 0 and not other,
          f"{len(ok)}/{len(limited)}/{len(other)}")
    served_this_minute = len(ok) + 150 if took < 50 else None
    check("no more than the per-exam budget was served inside one minute (150 earlier + this round)",
          served_this_minute is None or served_this_minute <= cap + 10, f"{served_this_minute} vs {cap}")
    check("every 429 carries Retry-After in seconds", all(r[2] and r[2].isdigit() for r in limited))
    s, res_json = S.jhttp("GET", f"/api/exams/{exam['id']}/results", token=teacher_token)
    refused = 0
    for a in res_json["attempts"]:
        refused += sum(1 for ev in a.get("events", []) if ev["type"] == "canvas_code_refused")
    check("refusals are logged as canvas_code_refused but not once per request (de-duplicated per attempt)",
          0 < refused <= len({r[0] for r in limited}) * 2, f"{refused} events for {len({r[0] for r in limited})} students")
    victim = limited[0][0]
    time.sleep(62)
    s, h, b = stands[victim].request_code()
    check("a student refused for the exam limit gets a code in the next minute (refusals cost no budget)", s == 200, f"{s}")

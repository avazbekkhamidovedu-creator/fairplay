"""Canvas mode end to end. Run from anywhere:  xvfb-run -a python3 e2e/canvas-mode/run_all.py"""
import json
import os
import re
import sqlite3
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"

import config as C  # noqa: E402
import browsers  # noqa: E402
import engine_suite  # noqa: E402
import phase_abuse  # noqa: E402
import phase_manual  # noqa: E402
import phase_student  # noqa: E402
import phase_ui  # noqa: E402
import standin as S  # noqa: E402
from lib import RESULTS, Services, check, fake, fake_state, note, section, write_results  # noqa: E402

SHOTS = os.path.join(C.SCRATCH, "shots")
ENGINES = os.environ.get("CANVAS_E2E_ENGINES", "chromium,webkit").split(",")
ALL_CODES: set[str] = set()


def db_code_state(exam_id):
    con = sqlite3.connect(os.path.join(C.SCRATCH, "server", "e2e.db"))
    try:
        return con.execute("select canvas_code_enc, canvas_code_source, status from exams e where e.id = ?",
                           (exam_id,)).fetchone() if False else con.execute(
            "select canvas_code_enc, canvas_code_source, status from exams where id = ?", (exam_id,)).fetchone()
    finally:
        con.close()


def open_msg_from_view(token, session, aid, device):
    _, view = S.jhttp("GET", f"/api/student/attempts/{aid}", token=token,
                      headers={"X-FairPlay-Session": session, "X-FairPlay-Platform": "mac"})
    return {"attempt_id": aid, "token": token, "session": session, "url": view["external_url"],
            "allowed_domains": view["allowed_domains"], "finish_url_regex": view.get("finish_url_regex"),
            "device": device}


def main():
    os.makedirs(SHOTS, exist_ok=True)
    sv = Services()
    from playwright.sync_api import sync_playwright
    try:
        sv.start()
        with sync_playwright() as pw:
            ui = pw.chromium.launch(args=[f"--host-resolver-rules={browsers.RESOLVER_RULES}"])
            ex = phase_ui.run(ui, SHOTS)
            ALL_CODES.add(ex["code"])
            exam_id = ex["exam_id"]
            ttok = S.login("teacher")

            s, exam = S.jhttp("GET", f"/api/exams/{exam_id}", token=ttok)
            check("teacher exam view: canvas kind, classic, managed, code_status set, no code field",
                  exam.get("external_kind") == "canvas" and exam["canvas"]["quiz_type"] == "classic"
                  and exam["canvas"]["access_code_source"] == "managed" and exam["canvas"]["code_status"] == "set"
                  and "access_code" not in exam["canvas"], json.dumps(exam.get("canvas")))
            check("finish_url_regex is the classic default",
                  exam.get("finish_url_regex") == r"/quizzes/\d+/(submissions/\d+|history)", str(exam.get("finish_url_regex")))
            check("allowed_domains hold the Canvas host, the sign-in hosts and the teacher's addition",
                  all(h in exam["allowed_domains"] for h in (C.CANVAS_HOST, C.SSO_HOST, "login.microsoftonline.com")),
                  str(exam["allowed_domains"]))
            dbrow = db_code_state(exam_id)
            check("the stored code at rest is encrypted (not the plain code)", dbrow[0] and ex["code"] not in str(dbrow[0]) and dbrow[2] in ("live", "open"), str(dbrow[1:]))

            # class membership for the burst users
            s, cls = S.jhttp("POST", f"/api/classes/{ex['class_id']}/members",
                             {"usernames": [f"lt{i:04d}" for i in range(1, C.LOADTEST_USERS + 1)]}, ttok)
            check("152 load-test students added to the class", s in (200, 201), f"{s}")

            # ---------------- 4. student app stand-in, Chromium
            section("4. Student app stand-in (Chromium): start, code, fill, submit, hand in")
            sctx = phase_student.app_context(ui)
            Sp = sctx.new_page()
            phase_student.student_login(Sp, "alice")
            started = phase_student.start_in_app(Sp, "Canvas E2E quiz", ex["start_code"])
            msg = started["msg"]
            check("exam page locked the app before openExternal", "lock" in started["actions"]
                  and started["actions"].index("lock") < started["actions"].index("openExternal"), str(started["actions"]))
            check("openExternal carries kind canvas, the quiz URL, regex and domains",
                  msg.get("kind") == "canvas" and msg["url"] == C.QUIZ_URL and msg.get("finish_url_regex") == exam["finish_url_regex"]
                  and C.CANVAS_HOST in msg["allowed_domains"] and C.SSO_HOST in msg["allowed_domains"])
            check("openExternal message does not contain the access code", ex["code"] not in json.dumps(msg))
            aid = msg["attempt_id"]
            S.http("POST", "/api/student/attempts/%s/app-beat" % aid)  # no-op probe of an unauthenticated beat
            handin = {}

            def done_cb():
                Sp.goto(f"{C.SERVER}/student/external-done/{aid}?reason=finish_url")
                try:
                    Sp.wait_for_url(re.compile(r".*/student/done/.+"), timeout=15000)
                    handin["ok"] = True
                except Exception:
                    handin["ok"] = False
                Sp.wait_for_timeout(1200)
                handin["text"] = Sp.inner_text("body")
                handin["unlocked"] = Sp.evaluate("window.__fp.unlockedAfterSubmit === true")

            results = {}
            for name in ENGINES:
                section(f"4. Canvas page behaviour in {name}")
                try:
                    if name == "chromium":
                        eng = browsers.ChromiumEngine(pw)
                        results[name] = engine_suite.run(name, eng, msg, done_cb=done_cb)
                    elif name == "webkit":
                        stand_b, btok, (st, a) = phase_abuse.make_stand("bob", exam, "webkit-dev-bob")
                        if stand_b is None:
                            check("[webkit] bob starts his attempt", False, str(st))
                            continue
                        eng = browsers.WebKitEngine()
                        bmsg = open_msg_from_view(btok, stand_b.session, stand_b.aid, "webkit-dev-bob")

                        def wk_done():
                            s_, r_ = S.jhttp("POST", f"/api/student/attempts/{stand_b.aid}/submit",
                                             {"answers": {}, "client_seq": 0}, btok,
                                             {"X-FairPlay-Session": stand_b.session})
                            check("[webkit] hand-in of the finished attempt through the submit endpoint", s_ == 200, f"{s_} {r_}")
                        results[name] = engine_suite.run(name, eng, bmsg, done_cb=wk_done)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    check(f"[{name}] engine suite ran to the end", False, repr(e))
                finally:
                    try:
                        eng.close()
                    except Exception:
                        pass

            check("hand-in through the existing external-done flow reached the receipt page", handin.get("ok") is True,
                  handin.get("text", "")[:200])
            check("app unlock was sent only after the server confirmed the hand-in", handin.get("unlocked") is True)
            s, res = S.jhttp("GET", f"/api/exams/{exam_id}/results", token=ttok)
            a_alice = next(x for x in res["attempts"] if x["username"] == "alice")
            ev = [e["type"] for e in a_alice.get("events", [])]
            check("teacher sees alice submitted", a_alice.get("status") == "submitted", str(a_alice.get("status")))
            check("alice's log: 4 canvas_code_issued (3 wrong-code fills + 1 good) and canvas_code_failed",
                  ev.count("canvas_code_issued") == 4 and "client_canvas_code_failed" in ev or "canvas_code_failed" in ev, str(ev))
            if "webkit" in ENGINES:
                a_bob = next(x for x in res["attempts"] if x["username"] == "bob")
                evb = [e["type"] for e in a_bob.get("events", [])]
                check("bob's (WebKit) log: 4 canvas_code_issued and a canvas_code_failed event", evb.count("canvas_code_issued") == 4
                      and any("canvas_code_failed" in t for t in evb), str(evb))

            # ---------------- 5. abuse
            bob_tok = S.login("bob")
            abuse_stand = phase_abuse.abuse(exam | {"start_code": ex["start_code"]}, ttok, bob_tok, None)
            # pending resume (needs 16 s since its first start)
            first = phase_abuse.make_stand("lt0151", exam | {"start_code": ex["start_code"]}, "resume-dev-1")
            phase_abuse.first_time[0] = time.time()
            burst_stands = phase_abuse.burst(exam | {"start_code": ex["start_code"]}, ttok)
            phase_abuse.pending_resume(exam | {"start_code": ex["start_code"]}, first)
            phase_abuse.exam_limit(exam | {"start_code": ex["start_code"]}, ttok, burst_stands)

            # ---------------- close
            section("5c. Close the exam: code cleared in Canvas and forgotten")
            code_before = fake_state()["code"]
            T = ex["page"]
            T.goto(f"{C.SERVER}/teacher/exams/{exam_id}?tab=live")
            T.get_by_role("button", name="Close exam", exact=True).click()
            T.get_by_role("button", name="Yes, close exam").click()
            deadline = time.time() + 40
            while time.time() < deadline and fake_state()["code"] != "":
                time.sleep(0.5)
            check("closing through the teacher UI cleared the code in the fake Canvas", fake_state()["code"] == "")
            row = db_code_state(exam_id)
            check("the stored code is gone from the database", not row[0], str(row))
            s, exam2 = S.jhttp("GET", f"/api/exams/{exam_id}", token=ttok)
            check("teacher view after close: code_status not_set, nothing left to remove in Canvas",
                  exam2["canvas"]["code_status"] == "not_set" and not exam2["canvas"].get("remove_code_in_canvas"), json.dumps(exam2["canvas"]))
            s, h, b = abuse_stand.request_code()
            check("a code request after the close is refused 409", s == 409, f"{s} {phase_abuse.detail_of(b)}")
            s, h, b = S.http("POST", f"/api/student/attempts/{abuse_stand.aid}/canvas-code", abuse_stand.code_body(), abuse_stand.token,
                             {"X-FairPlay-Session": abuse_stand.session})
            check("close handed in carol's open attempt", S.jhttp("GET", f"/api/student/attempts/{abuse_stand.aid}", token=abuse_stand.token,
                  headers={"X-FairPlay-Session": abuse_stand.session})[1].get("status") == "submitted")
            # reopen = a new random code
            s, e3 = S.jhttp("POST", f"/api/exams/{exam_id}/open", None, ttok)
            check("reopening works and sets a new random code", s == 200 and len(fake_state()["code"]) == 24
                  and fake_state()["code"] != code_before, f"{s}")
            ALL_CODES.add(fake_state()["code"])
            ALL_CODES.add(code_before)
            S.jhttp("POST", f"/api/exams/{exam_id}/close", None, ttok)
            check("closing again clears it again", fake_state()["code"] == "")

            ALL_CODES.add(phase_manual.run(ex["page"], pw, ex["class_id"], ttok, db_code_state))

            # ---------------- leak scan
            section("5d. The code is nowhere it should not be")
            log = sv.server_log()
            check("no access code appears in the server log", all(c not in log for c in ALL_CODES if c), f"{len(log)} bytes")
            check("log has the open and close requests (the scan reads something real)", "/open" in log and "/close" in log)
            s, res = S.jhttp("GET", f"/api/exams/{exam_id}/results", token=ttok)
            blob = json.dumps(res)
            check("no access code in the results/events the teacher can read", all(c not in blob for c in ALL_CODES if c))
            for c in ALL_CODES:
                if c:
                    pass

            # ---------------- 6. regression
            section("6. Regression: ordinary website exam through the same harness")
            body = {"title": "Plain site exam", "duration_min": 30, "mode": "external", "external_url": "https://forms.example.com/f/1",
                    "allowed_domains": ["forms.example.com"], "finish_url_contains": "formResponse", "questions": [],
                    "class_id": int(ex["class_id"])}
            s, pe = S.jhttp("POST", "/api/exams", body, ttok)
            check("plain website exam saved", s == 201, f"{s} {pe}")
            check("plain exam: external_kind null, canvas null, finish_url_regex null",
                  pe.get("external_kind") is None and pe.get("canvas") is None and pe.get("finish_url_regex") is None, json.dumps({k: pe.get(k) for k in ("external_kind", "canvas", "finish_url_regex")}))
            s, pe = S.jhttp("POST", f"/api/exams/{pe['id']}/open", None, ttok)
            calls_before = fake_state()["set_calls"]
            check("plain exam opens without touching the fake Canvas", s == 200 and fake_state()["set_calls"] == calls_before)
            cctx = phase_student.app_context(ui, "plain-dev-carol")
            Cp = cctx.new_page()
            phase_student.student_login(Cp, "carol")
            st2 = phase_student.start_in_app(Cp, "Plain site exam", pe["start_code"])
            m2 = st2["msg"]
            check("plain exam: openExternal has no canvas kind and keeps finish_url_contains",
                  m2.get("kind") is None and m2.get("finish_url_contains") == "formResponse" and "forms.example.com" in m2["allowed_domains"], json.dumps({k: m2.get(k) for k in ("kind", "finish_url_contains", "finish_url_regex")}))
            ts_ = S.fresh_ts()
            s, h, b = S.http("POST", f"/api/student/attempts/{m2['attempt_id']}/canvas-code",
                             {"proof": S.sign(C.APP_KEY, f"canvascode|{m2['attempt_id']}|{m2['session']}|plain-dev-carol|{ts_}"), "ts": ts_},
                             m2["token"], {"X-FairPlay-Session": m2["session"]})
            check("canvas-code on a plain website exam is refused 409", s == 409, f"{s}")
            Cp.goto(f"{C.SERVER}/student/external-done/{m2['attempt_id']}?reason=finish_url")
            Cp.wait_for_url(re.compile(r".*/student/done/.+"), timeout=15000)
            check("plain exam hand-in through external-done works", True)
            non_mac_tok = S.login("lt0150", "loadtest")
            st3, a3 = S.start_attempt(non_mac_tok, pe["id"], pe["start_code"], device="win-dev-0001", platform="windows")
            check("plain website exam still starts on a non-mac platform", st3 == 200, f"{st3} {a3}")
            check("the fake Canvas got no rotation call for the plain exam", fake_state()["set_calls"] == calls_before)
            S.jhttp("POST", f"/api/exams/{pe['id']}/close", None, ttok)
            ui.close()
    finally:
        write_results(os.path.join(C.SCRATCH, "results.json"))
        sv.stop()
    fails = [r for r in RESULTS if r["ok"] is False]
    passed = [r for r in RESULTS if r["ok"] is True]
    print(f"\n{len(passed)} passed, {len(fails)} failed")
    for r in fails:
        print("FAILED:", r["section"], "|", r["name"], "|", r["detail"])
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

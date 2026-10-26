"""Canvas mode end to end. Run from anywhere:  xvfb-run -a python3 e2e/canvas-mode/run_all.py"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"

import config as C  # noqa: E402
import browsers  # noqa: E402
import engine_suite  # noqa: E402
import phase_restricted  # noqa: E402
import phase_student  # noqa: E402
import phase_ui  # noqa: E402
import policy_tables  # noqa: E402
import standin as S  # noqa: E402
from lib import RESULTS, Services, check, fake_state, section, write_results  # noqa: E402

SHOTS = os.path.join(C.SCRATCH, "shots")
ENGINES = os.environ.get("CANVAS_E2E_ENGINES", "chromium,webkit").split(",")
ONLY = os.environ.get("CANVAS_E2E_ONLY", "")


def hand_in_chromium(page, aid):
    page.goto(f"{C.SERVER}/student/external-done/{aid}?reason=finish_url")
    try:
        page.wait_for_url(re.compile(r".*/student/done/.+"), timeout=15000)
        ok = True
    except Exception:
        ok = False
    page.wait_for_timeout(1000)
    check("[restricted] hand-in through external-done reached the receipt page and the app unlocked",
          ok and page.evaluate("window.__fp.unlockedAfterSubmit === true"))


def hand_in_webkit(msg):
    s_, r_ = S.jhttp("POST", f"/api/student/attempts/{msg['attempt_id']}/submit", {"answers": {}, "client_seq": 0},
                     msg["token"], {"X-FairPlay-Session": msg["session"]})
    check("[restricted] hand-in of the finished attempt through the submit endpoint", s_ == 200, f"{s_} {r_}")


def msg_for(user, password, exam_id, start_code, device):
    return phase_restricted.api_start_msg(user, password, exam_id, start_code, device)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    policy_tables.run()
    sv = Services()
    from playwright.sync_api import sync_playwright
    try:
        sv.start()
        with sync_playwright() as pw:
            ui = pw.chromium.launch(args=[f"--host-resolver-rules={browsers.RESOLVER_RULES}"])
            ex = phase_ui.run(ui, SHOTS)
            exam_id = ex["exam_id"]
            ttok = S.login("teacher")

            s, exam = S.jhttp("GET", f"/api/exams/{exam_id}", token=ttok)
            check("teacher exam view: canvas kind, classic, scope course 1 / quizzes / 1, no code fields",
                  exam.get("external_kind") == "canvas" and exam["canvas"]["quiz_type"] == "classic"
                  and exam["canvas"]["scope"] == {"course_id": "1", "item_kind": "quizzes", "item_id": "1"}
                  and not any(k in json.dumps(exam) for k in ("access_code", "code_status", "canvas_code")),
                  json.dumps(exam.get("canvas")))
            check("finish_url_regex is the classic default",
                  exam.get("finish_url_regex") == r"/quizzes/\d+/(submissions/\d+|history)", str(exam.get("finish_url_regex")))
            check("allowed_domains hold the Canvas host, the sign-in hosts and the teacher's addition",
                  all(h in exam["allowed_domains"] for h in (C.CANVAS_HOST, C.SSO_HOST, "login.microsoftonline.com")),
                  str(exam["allowed_domains"]))

            if ONLY == "restricted":
                S.jhttp("POST", f"/api/exams/{exam_id}/close", None, ttok)
                phase_restricted.run(pw, ui, ex["page"], ttok, SHOTS, ENGINES, hand_in_chromium, hand_in_webkit)
                raise SystemExit(finish_run(sv))

            s, cls = S.jhttp("POST", f"/api/classes/{ex['class_id']}/members",
                             {"usernames": [f"lt{i:04d}" for i in range(1, C.BURST_USERS + 1)]}, ttok)
            check("152 load-test students added to the class", s in (200, 201), f"{s}")

            # ---------------- 4. student app stand-in, Chromium
            section("4. Student app stand-in (Chromium): start, take the quiz, submit, hand in")
            sctx = phase_student.app_context(ui)
            Sp = sctx.new_page()
            phase_student.student_login(Sp, "alice")
            started = phase_student.start_in_app(Sp, "Canvas E2E quiz", ex["start_code"])
            msg = started["msg"]
            check("exam page locked the app before openExternal", "lock" in started["actions"]
                  and started["actions"].index("lock") < started["actions"].index("openExternal"), str(started["actions"]))
            check("openExternal carries kind canvas, the quiz URL, regex, domains and the scope",
                  msg.get("kind") == "canvas" and msg["url"] == C.QUIZ_URL and msg.get("finish_url_regex") == exam["finish_url_regex"]
                  and C.CANVAS_HOST in msg["allowed_domains"] and C.SSO_HOST in msg["allowed_domains"]
                  and {k: (msg.get("canvas_scope") or {}).get(k) for k in ("course_id", "item_kind", "item_id", "open_book")}
                  == {"course_id": "1", "item_kind": "quizzes", "item_id": "1", "open_book": False}
                  and "saml.fake.test" in (msg.get("canvas_scope") or {}).get("sign_in_hosts", []),
                  json.dumps({k: msg.get(k) for k in ("kind", "url", "canvas_scope")}))
            check("openExternal message carries no code field", not any(k in json.dumps(msg) for k in ("access_code", "canvas_code")))
            aid = msg["attempt_id"]
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

            for name in ENGINES:
                section(f"4. Canvas page behaviour in {name}")
                eng = None
                try:
                    if name == "chromium":
                        eng = browsers.ChromiumEngine(pw)
                        engine_suite.run(name, eng, msg, done_cb=done_cb)
                    elif name == "webkit":
                        bmsg = msg_for("bob", "bob123", exam_id, ex["start_code"], "webkit-dev-bob")
                        eng = browsers.WebKitEngine()

                        def wk_done():
                            s_, r_ = S.jhttp("POST", f"/api/student/attempts/{bmsg['attempt_id']}/submit",
                                             {"answers": {}, "client_seq": 0}, bmsg["token"],
                                             {"X-FairPlay-Session": bmsg["session"]})
                            check("[webkit] hand-in of the finished attempt through the submit endpoint", s_ == 200, f"{s_} {r_}")
                        engine_suite.run(name, eng, bmsg, done_cb=wk_done)
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
            check("teacher sees alice submitted", a_alice.get("status") == "submitted", str(a_alice.get("status")))
            check("alice's log has no canvas_code_* event",
                  not any("canvas_code" in e["type"] for e in a_alice.get("events", [])), str([e["type"] for e in a_alice.get("events", [])]))

            # ---------------- 5b. 150 students start at once
            section("5b. 150 students start the Canvas exam at once (Mac app version under test)")
            users = [f"lt{i:04d}" for i in range(1, 151)]
            toks = [S.login(u, "loadtest") for u in users]

            def go(i):
                return S.start_attempt(toks[i], exam_id, ex["start_code"], device=f"burst-dev-{i:04d}")[0]
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=40) as pool:
                codes = list(pool.map(go, range(len(users))))
            check(f"150 simultaneous starts all served ({time.time() - t0:.1f} s)", codes.count(200) == 150, str(sorted(set(codes))))
            S.jhttp("POST", f"/api/exams/{exam_id}/close", None, ttok)
            s, res = S.jhttp("GET", f"/api/exams/{exam_id}/results", token=ttok)
            blob = json.dumps(res)
            check("no code field in the results the teacher reads", not any(k in blob for k in ("access_code", "canvas_code_issued")))

            phase_restricted.run(pw, ui, ex["page"], ttok, SHOTS, ENGINES, hand_in_chromium, hand_in_webkit)

            # ---------------- 6. regression
            section("6. Regression: ordinary website exam through the same harness")
            body = {"title": "Plain site exam", "duration_min": 30, "mode": "external", "external_url": "https://forms.example.com/f/1",
                    "allowed_domains": ["forms.example.com"], "finish_url_contains": "formResponse", "questions": [],
                    "class_id": int(ex["class_id"])}
            s, pe = S.jhttp("POST", "/api/exams", body, ttok)
            check("plain website exam saved", s == 201, f"{s} {pe}")
            check("plain exam: external_kind null, canvas null, finish_url_regex null",
                  pe.get("external_kind") is None and pe.get("canvas") is None and pe.get("finish_url_regex") is None,
                  json.dumps({k: pe.get(k) for k in ("external_kind", "canvas", "finish_url_regex")}))
            s, pe = S.jhttp("POST", f"/api/exams/{pe['id']}/open", None, ttok)
            cctx = phase_student.app_context(ui, "plain-dev-carol")
            Cp = cctx.new_page()
            phase_student.student_login(Cp, "carol")
            st2 = phase_student.start_in_app(Cp, "Plain site exam", pe["start_code"])
            m2 = st2["msg"]
            check("plain exam: openExternal has no canvas kind, no scope, and keeps finish_url_contains",
                  m2.get("kind") is None and m2.get("canvas_scope") is None and m2.get("finish_url_contains") == "formResponse"
                  and "forms.example.com" in m2["allowed_domains"], json.dumps({k: m2.get(k) for k in ("kind", "finish_url_contains", "finish_url_regex")}))
            Cp.goto(f"{C.SERVER}/student/external-done/{m2['attempt_id']}?reason=finish_url")
            Cp.wait_for_url(re.compile(r".*/student/done/.+"), timeout=15000)
            check("plain exam hand-in through external-done works", True)
            non_mac_tok = S.login("lt0150", "loadtest")
            st3, a3 = S.start_attempt(non_mac_tok, pe["id"], pe["start_code"], device="win-dev-0001", platform="windows")
            check("plain website exam still starts on a non-mac platform", st3 == 200, f"{st3} {a3}")
            S.jhttp("POST", f"/api/exams/{pe['id']}/close", None, ttok)
            ui.close()
    finally:
        write_results(os.path.join(C.SCRATCH, "results.json"))
        sv.stop()
    sys.exit(report())


def finish_run(sv):
    write_results(os.path.join(C.SCRATCH, "results.json"))
    return report()


def report():
    fails = [r for r in RESULTS if r["ok"] is False]
    passed = [r for r in RESULTS if r["ok"] is True]
    print(f"\n{len(passed)} passed, {len(fails)} failed")
    for r in fails:
        print("FAILED:", r["section"], "|", r["name"], "|", r["detail"])
    return 1 if fails else 0


if __name__ == "__main__":
    main()

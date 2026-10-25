"""The browser part of Canvas mode, run in one engine: probe, fill, submit, decoys, wrong code, finish, popups."""
import json
import re
import time
from urllib.parse import urlsplit

import config as C
import standin as S
from browsers import settle, wait_new_document
from lib import check, fake, fake_state, note

REGEX_POS = ["/courses/1/quizzes/1/submissions/5", "/courses/1/quizzes/1/history?version=1",
             "/courses/1/quizzes/1/submissions/5?x=1"]
REGEX_NEG = ["/courses/1/quizzes/1", "/courses/1/quizzes/1/take", "/courses/1/quizzes/1/submissions",
             "/courses/1/pages/decoy"]


def drive(stand, page, first_click=None, max_rounds=8):
    """Everything the app does after a main-frame load, repeated until nothing more happens."""
    if first_click:
        page.set_mark()
        page.click(first_click)
        wait_new_document(page)
    outcomes = []
    for _ in range(max_rounds):
        settle(page)
        out = stand.on_loaded(page)
        outcomes.append(out)
        if out != "clicked":
            break
        # the fill clicked the form's button: wait for the resulting navigation
        try:
            wait_new_document(page, timeout=2.0)
        except TimeoutError:
            pass
    return outcomes


def regex_checks(eng, page, regex):
    ok_py = all(S.finish_matches(C.CANVAS + u, regex) for u in REGEX_POS) and not any(
        S.finish_matches(C.CANVAS + u, regex) for u in REGEX_NEG)
    check(f"[{eng}] finish regex matches the submissions/history URLs (Python re)", ok_py, regex)
    flags = "i" if regex.startswith("(?i)") else ""
    body = regex[4:] if flags else regex
    js = ("(function(){var r=new RegExp(" + json.dumps(body) + "," + json.dumps(flags) + ");return "
          + json.dumps([S.finish_text(C.CANVAS + u) for u in REGEX_POS + REGEX_NEG]) + ".map(function(t){return r.test(t);});})()")
    res = page.eval(js)
    want = [True] * len(REGEX_POS) + [False] * len(REGEX_NEG)
    check(f"[{eng}] same regex in the engine's own JS RegExp gives the same answers", res == want, str(res))
    check(f"[{eng}] app-side safety check (CanvasFinish.isSafe port) accepts the server's regex", S.is_safe_pattern(regex))


def run(eng_name, engine, open_msg, label=None, done_cb=None):
    eng = eng_name
    rules = None
    stand = S.NativeStandIn(open_msg)
    rules = stand.rules
    page = engine.new_page()

    # ------------------------------------------------------------ gates: decoy and sign-in pages
    base_issue = stand.issue_calls
    for name, url in [("decoy page on the Canvas host", f"{C.CANVAS}/courses/1/pages/decoy"),
                      ("sign-in host page", f"https://{C.SSO_HOST}/login"),
                      ("host that is not allowed", f"https://{C.OTHER_HOST}/blocked")]:
        page.goto(url)
        settle(page)
        out = stand.on_loaded(page)
        check(f"[{eng}] {name}: no fetch, no fill (stand-in says {out})",
              out == "gate" and page.value_of("#quiz_access_code") in ("", None))
    check(f"[{eng}] no canvas-code request was sent for those pages", stand.issue_calls == base_issue)
    # defence in depth: the fill script itself refuses another origin
    page.goto(f"https://{C.SSO_HOST}/login")
    settle(page)
    r = page.eval(S.SCRIPTS.fill("DUMMY-NOT-A-CODE", C.CANVAS))
    check(f"[{eng}] fill script run on the wrong origin returns wrongorigin and types nothing",
          r == "wrongorigin" and page.value_of("#quiz_access_code") == "", str(r))
    check(f"[{eng}] fake Canvas saw no decoy form post", fake_state()["typed_on_decoy"] == 0)

    # ------------------------------------------------------------ wrong code: help after 3 fills
    real = fake_state()["code"]
    fake("POST", "/code", {"code": "NotTheRealCode999"})
    fake("POST", "/forget-sessions")
    engine.fresh_context()
    page.close() if False else None
    page = engine.new_page()
    wrong0 = fake_state()["wrong"]
    page.goto(C.QUIZ_URL + "/take")
    outs = drive(stand, page)
    st = fake_state()
    check(f"[{eng}] wrong code: 3 fills, then the help state (no 4th fill)",
          stand.budget.used == 3 and outs[-1] == "help" and st["wrong"] - wrong0 == 3,
          f"fills={stand.budget.used} outs={outs} wrong={st['wrong'] - wrong0}")
    check(f"[{eng}] help state recorded canvas_code_failed", "canvas_code_failed:fill limit reached" in stand.log, str(stand.log))
    check(f"[{eng}] server issued exactly 3 codes for the 3 fills", stand.issue_calls - base_issue == 3,
          str(stand.issue_calls - base_issue))
    fake("POST", "/code", {"code": real})
    fake("POST", "/forget-sessions")
    engine.fresh_context()

    # ------------------------------------------------------------ the happy path
    stand = S.NativeStandIn(open_msg)          # the app restarted: fresh fill budget, same attempt
    page = engine.new_page()
    page.goto(C.QUIZ_URL)
    settle(page)
    out0 = stand.on_loaded(page)
    check(f"[{eng}] landing page: probe finds no field, nothing typed", out0 == "nofield", out0)
    acc0 = fake_state()["accepted"]
    outs = drive(stand, page, first_click="#take_quiz")
    settle(page)
    check(f"[{eng}] access-code form filled by the app, quiz opened without a human typing",
          outs == ["clicked", "nofield"] and "What is 2+2?" in page.text() and fake_state()["accepted"] == acc0 + 1,
          f"{outs} {page.url}")
    check(f"[{eng}] the code the server issued is the one the fake Canvas holds (fill used a server-issued code)",
          len(stand.codes_seen) == 1 and stand.codes_seen[0] == fake_state()["code"])
    check(f"[{eng}] before finishing, finish regex does not match the quiz page", not stand.finish_hit(page.url), page.url)
    page.click("input[name=q1][value='4']")
    page.set_mark()
    page.click("#submit_quiz")
    wait_new_document(page)
    settle(page)
    check(f"[{eng}] submit lands on /submissions/5", urlsplit(page.url).path == "/courses/1/quizzes/1/submissions/5", page.url)
    check(f"[{eng}] finish detected by the server's finish_url_regex", stand.finish_hit(page.url))
    time.sleep(2.1)
    check(f"[{eng}] still on the finish page after the 2 s wait, so the app finishes", stand.finish_hit(page.url))
    check(f"[{eng}] fake Canvas recorded one submission", fake_state()["submitted"] >= 1)
    regex_checks(eng, page, stand.finish_regex)
    if done_cb:
        done_cb()

    # ------------------------------------------------------------ popups and window.opener
    page.goto(f"{C.CANVAS}/courses/1/pages/opener")
    settle(page)
    host = C.CANVAS_HOST
    sso_url, coop_url, other_url = (f"https://{C.SSO_HOST}/popup", f"https://{C.SSO_HOST}/popup-coop",
                                    f"https://{C.OTHER_HOST}/blocked")
    check(f"[{eng}] popup plan: allowed sign-in host on macOS 14.4+ goes to the same view, older macOS gets a child",
          S.popup_plan(sso_url, host, rules, False) == "sameView" and S.popup_plan(sso_url, host, rules, True) == "child")
    check(f"[{eng}] popup plan: a page on the Canvas host is a child view, blank is a child, other host is blocked",
          S.popup_plan(f"{C.CANVAS}/courses/1/pages/x", host, rules, False) == "child"
          and S.popup_plan("about:blank", host, rules, False) == "child"
          and S.popup_plan(other_url, host, rules, False) == "block" and S.popup_plan(other_url, host, rules, True) == "block")
    pop = page.click_popup("#open_sso")
    if pop is None:
        check(f"[{eng}] window.open of the sign-in page opens a window", False)
    else:
        settle(pop)
        opener_seen = pop.eval("window.opener !== null")
        check(f"[{eng}] sign-in popup (no COOP): window.opener is set, so a popup sign-in can finish", opener_seen is True, str(opener_seen))
        time.sleep(0.5)
        msgs = page.eval("window.__msgs.map(function(m){return m.d + '@' + m.o;})")
        check(f"[{eng}] sign-in popup can postMessage to its opener", any(m.startswith("opener-alive@https://sso.fake.test") for m in msgs), str(msgs))
        out = stand.on_loaded(pop)
        check(f"[{eng}] the popup's field is not filled (host gate)", out == "gate" and pop.value_of("#quiz_access_code") == "")
        pop.close()
    pop = page.click_popup("#open_sso_coop")
    if pop is None:
        note(f"[{eng}] COOP popup did not open")
    else:
        settle(pop)
        has = pop.eval("window.opener !== null")
        note(f"[{eng}] observed: sign-in popup served with Cross-Origin-Opener-Policy same-origin has window.opener {'kept' if has else 'NULL'}")
        time.sleep(0.4)
        pop.close()
    pop = page.click_popup("#open_other")
    if pop is not None:
        settle(pop)
        out = stand.on_loaded(pop)
        check(f"[{eng}] a page the app would block is also never filled even if a browser let it open", out == "gate"
              and pop.value_of("#quiz_access_code") == "")
        pop.close()
    page.close()
    return stand

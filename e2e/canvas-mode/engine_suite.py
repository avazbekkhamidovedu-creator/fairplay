"""The browser part of Canvas mode run in one engine: quiz page, submit, finish by the server's regex, popups."""
import json
import re
import time
from urllib.parse import urlsplit

import config as C
import standin as S
from browsers import settle, wait_new_document
from lib import check, fake_state, note

REGEX_POS = ["/courses/1/quizzes/1/submissions/5", "/courses/1/quizzes/1/history?version=1",
             "/courses/1/quizzes/1/submissions/5?x=1", "/courses/1/quizzes/1/%73ubmissions/5"]
REGEX_NEG = ["/courses/1/quizzes/1", "/courses/1/quizzes/1/take", "/courses/1/quizzes/1/submissions",
             "/courses/1/pages/decoy", "/courses/1/quizzes/1/take?next=/quizzes/1/submissions/5",
             "/courses/1/quizzes/1/take#/quizzes/1/history"]


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
    stand = S.NativeStandIn(open_msg)
    rules = stand.rules
    page = engine.new_page()

    page.goto(C.QUIZ_URL)
    settle(page)
    check(f"[{eng}] quiz landing page loads with no form to fill (there is no access code)",
          page.eval("document.querySelector('input[type=password]') === null") is True and "Take the Quiz" in page.text(), page.url)
    page.set_mark()
    page.click("#take_quiz")
    wait_new_document(page)
    settle(page)
    check(f"[{eng}] the quiz opens straight from the Take the Quiz link", "What is 2+2?" in page.text(), page.url)
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
    page.close()
    return stand

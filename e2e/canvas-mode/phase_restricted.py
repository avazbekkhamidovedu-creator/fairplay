"""The restricted Canvas browser end to end: teacher UI and API (scope, open_book), the student attempt view and
openExternal payload, then the whole student journey in Chromium and WebKit with the ported policy."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import canvas_nav as N
import config as C
import restricted
import standin as S
import tabs
from lib import check, fake, fake_state, note, section

QUIZ1 = f"{C.CANVAS}/courses/1/quizzes/1"
DASH = f"{C.CANVAS}/"
EXTRA_DOMAINS = f"{C.SSO_HOST}\n{C.GO_HOST}\n{C.SAML_HOST}"
ALL_CODES: set[str] = set()


def hits_since(n):
    return fake_state()["page_hits"][n:]


def hit_paths(n):
    out = []
    for h in hits_since(n):
        if h.startswith("GET ") and h.split(" ", 1)[1].startswith(C.CANVAS_HOST):
            out.append(h.split(" ", 1)[1][len(C.CANVAS_HOST):] or "/")
    return out


# ------------------------------------------------------------------------------------------ teacher UI helpers
def ui_new_exam(T, class_id, title, url=QUIZ1, open_book=False, shots=None):
    T.goto(f"{C.SERVER}/teacher/exams/new?class={class_id}")
    T.fill("#exam-title", title)
    T.fill("#exam-duration", "40")
    T.get_by_role("button", name=re.compile("More settings")).click()
    T.get_by_text("On another website").first.click()
    T.get_by_text("Canvas quiz", exact=True).first.click()
    T.fill("#exam-external-url", url)
    T.fill("#exam-allowed-domains", EXTRA_DOMAINS)
    T.wait_for_selector("[data-testid=canvas-settings]")
    box = T.locator("#exam-canvas-open-book")
    info = {"default_checked": box.is_checked(), "line_off": T.inner_text("[data-testid=canvas-access-line]")}
    if open_book:
        box.check()
    info["line"] = T.inner_text("[data-testid=canvas-access-line]")
    info["checked"] = box.is_checked()
    if shots:
        T.screenshot(path=f"{shots}/rb-setup-{'book' if open_book else 'closed'}.png", full_page=True)
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_url(re.compile(r".*/teacher/exams/e_[A-Za-z0-9_-]+"), timeout=15000)
    info["id"] = re.search(r"/exams/(e_[A-Za-z0-9_-]+)", T.url).group(1)
    return info


def ui_open_exam(T, exam_id):
    T.get_by_role("button", name=re.compile("Open exam", re.I)).first.click()
    T.get_by_text(re.compile(r"^\d{6}$")).first.wait_for(timeout=20000)
    T.wait_for_timeout(600)
    return re.search(r"\b(\d{6})\b", T.inner_text("body")).group(1)


def ui_close_exam(T, exam_id):
    T.goto(f"{C.SERVER}/teacher/exams/{exam_id}?tab=live")
    T.get_by_role("button", name="Close exam", exact=True).click()
    T.get_by_role("button", name="Yes, close exam").click()
    deadline = time.time() + 40
    while time.time() < deadline and fake_state()["code"] != "":
        time.sleep(0.4)


def get_exam(ttok, eid):
    s, e = S.jhttp("GET", f"/api/exams/{eid}", token=ttok)
    assert s == 200, e
    return e


# ------------------------------------------------------------------------------------------ A. server side
def server_side(T, ttok, class_id, shots):
    section("8a. Scope and open_book through the teacher UI and API")
    r1 = ui_new_exam(T, class_id, "Restricted R1", open_book=False, shots=shots)
    check("UI: open-book checkbox is on the Canvas setup and is off by default",
          r1["default_checked"] is False and r1["checked"] is False, json.dumps(r1))
    check("UI: plain sentence lists what students can open (sign-in, dashboard, this course, this quiz; no course material)",
          "their dashboard" in r1["line"] and "this quiz" in r1["line"] and "pages, modules" not in r1["line"],
          r1["line"])
    e = get_exam(ttok, r1["id"])
    check("API after UI save: canvas.scope is course 1 / quizzes / 1 and open_book false",
          e["canvas"]["scope"] == {"course_id": "1", "item_kind": "quizzes", "item_id": "1"}
          and e["canvas"]["open_book"] is False, json.dumps(e["canvas"]))

    T.goto(f"{C.SERVER}/teacher/exams/{r1['id']}/edit")
    T.wait_for_selector("#exam-canvas-open-book")
    check("UI edit page: checkbox reads the saved value (off)", T.locator("#exam-canvas-open-book").is_checked() is False)
    T.locator("#exam-canvas-open-book").check()
    line_on = T.inner_text("[data-testid=canvas-access-line]")
    check("UI: ticking it adds the course pages, modules and files to the sentence",
          "plus the course pages, modules and files" in line_on, line_on)
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_timeout(1500)
    e = get_exam(ttok, r1["id"])
    check("UI toggle on, saved: API says open_book true, scope unchanged",
          e["canvas"]["open_book"] is True and e["canvas"]["scope"]["item_id"] == "1", json.dumps(e["canvas"]))
    T.goto(f"{C.SERVER}/teacher/exams/{r1['id']}/edit")
    T.wait_for_selector("#exam-canvas-open-book")
    check("UI edit page after reload: checkbox is checked (round trip)", T.locator("#exam-canvas-open-book").is_checked())
    T.locator("#exam-canvas-open-book").uncheck()
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_timeout(1500)
    e = get_exam(ttok, r1["id"])
    check("UI toggle off, saved: API says open_book false again", e["canvas"]["open_book"] is False, json.dumps(e["canvas"]))

    # API round trip on a scratch draft
    body = {"title": "Restricted API", "duration_min": 30, "mode": "external", "external_url": QUIZ1,
            "external_kind": "canvas", "allowed_domains": EXTRA_DOMAINS.split("\n"), "questions": [],
            "class_id": int(class_id), "canvas": {"quiz_type": "classic", "access_code_source": "manual",
                                                  "access_code": "ManualCodeForApi1", "open_book": True}}
    s, e = S.jhttp("POST", "/api/exams", body, ttok)
    check("API create with canvas.open_book true: stored and returned", s == 201 and e["canvas"]["open_book"] is True, f"{s} {e}")
    eid = e["id"]
    base = {k: v for k, v in body.items() if k != "canvas"}

    def put(extra=None, **over):
        b = {**base, **over}
        if extra is not None:
            b["canvas"] = extra
        return S.jhttp("PUT", f"/api/exams/{eid}", b, ttok)

    s, e = put()
    check("PUT without a canvas block keeps open_book true", s == 200 and e["canvas"]["open_book"] is True, f"{s}")
    s, e = put({"quiz_type": "classic", "access_code_source": "manual"})
    check("PUT with a canvas block that omits open_book keeps it", s == 200 and e["canvas"]["open_book"] is True, f"{s}")
    s, e = put({"open_book": False})
    check("PUT open_book false: off, and the teacher view and list agree",
          s == 200 and e["canvas"]["open_book"] is False and get_exam(ttok, eid)["canvas"]["open_book"] is False)
    s, lst = S.jhttp("GET", "/api/exams", token=ttok)
    row = next(x for x in lst if x["id"] == eid)
    check("exam list row carries canvas.open_book and canvas.scope",
          row["canvas"]["open_book"] is False and row["canvas"]["scope"]["course_id"] == "1", json.dumps(row.get("canvas")))
    s, e = put({"open_book": True})
    check("PUT open_book true: on again", s == 200 and e["canvas"]["open_book"] is True)
    s, e = put({"open_book": ["x"]})
    check("PUT open_book with a non-boolean value is refused 422", s == 422, f"{s}")
    check("the refused PUT changed nothing", get_exam(ttok, eid)["canvas"]["open_book"] is True)
    s, e = put({"open_book": True, "scope": {"course_id": "99", "item_kind": "quizzes", "item_id": "99"}})
    check("a client-sent scope is ignored; scope still follows the link",
          s == 200 and e["canvas"]["scope"] == {"course_id": "1", "item_kind": "quizzes", "item_id": "1"}, json.dumps(e.get("canvas")))
    s, e = put(external_url=f"{C.CANVAS}/courses/2/assignments/7")
    check("changing the link changes the scope (course 2 / assignments / 7)",
          s == 200 and e["canvas"]["scope"] == {"course_id": "2", "item_kind": "assignments", "item_id": "7"}, f"{s} {json.dumps(e.get('canvas')) if isinstance(e, dict) else e}")
    for odd in ("/somewhere", "/courses/1/quizzes/1x", "/courses/1/quizzes", "/courses/1",
                "/courses/1/quizzes/" + "9" * 21, "/courses/1/pages/1", "/Courses/1/quizzes/1", "/courses/\u0661/quizzes/1"):
        s, e = put({"quiz_type": "new", "access_code_source": "manual", "open_book": True}, external_url=C.CANVAS + odd)
        check(f"link {odd[:44]} is refused 422 with the one-quiz message (no exam without a scope)",
              s == 422 and "must open one quiz or assignment" in json.dumps(e), f"{s} {str(e)[:120]}")
    check("the refused saves left the link and scope as they were",
          get_exam(ttok, eid)["canvas"]["scope"] == {"course_id": "2", "item_kind": "assignments", "item_id": "7"})
    s, e = put({"quiz_type": "classic", "access_code_source": "manual"}, external_url=f"{C.CANVAS}/courses/1/quizzes/1/edit")
    check("a link under the quiz (.../quizzes/1/edit) is scoped to that quiz",
          s == 200 and e["canvas"]["scope"] == {"course_id": "1", "item_kind": "quizzes", "item_id": "1"}, f"{s}")
    for unclean in ("/courses/%31/quizzes/1", "/courses/1/quizzes/1;jsessionid=x", "/courses/1/quizzes/1\\..\\2"):
        s, e = put({"quiz_type": "classic", "access_code_source": "manual"}, external_url=C.CANVAS + unclean)
        check(f"link {unclean[:40]} (percent, matrix parameter, backslash) is refused 422 with the clean-link message",
              s == 422 and "may not contain" in json.dumps(e), f"{s} {str(e)[:120]}")
    s, e = put({"quiz_type": "classic", "access_code_source": "manual"}, external_url=f"{C.CANVAS}/courses/1/quizzes/1/take?x=1#y")
    check("a /take link with query and fragment keeps scope", s == 200 and e["canvas"]["scope"]["item_id"] == "1")
    s, e = put(external_url=f"{C.CANVAS}/courses/1/quizzes/1", extra=None)
    S.jhttp("DELETE", f"/api/exams/{eid}", token=ttok)

    # plain site exam: nothing canvas
    s, pe = S.jhttp("POST", "/api/exams", {"title": "Plain RB", "duration_min": 30, "mode": "external",
                                           "external_url": "https://forms.example.com/f/1", "allowed_domains": ["forms.example.com"],
                                           "questions": [], "class_id": int(class_id)}, ttok)
    check("plain website exam: canvas null (no scope, no open_book)", s == 201 and pe.get("canvas") is None, f"{s}")
    S.jhttp("DELETE", f"/api/exams/{pe['id']}", token=ttok)
    return r1


# ------------------------------------------------------------------------------------------ student view / bridge
def student_login_lt(p, user):
    p.goto(f"{C.SERVER}/student/login")
    p.fill("#username", user)
    p.fill("#password", "loadtest")
    p.get_by_role("button", name="Sign in").click()
    p.wait_for_url(re.compile(r".*/student/?$"))


def start_via_page(browser, user, title, start_code, device):
    import phase_student
    ctx = phase_student.app_context(browser, device)
    p = ctx.new_page()
    student_login_lt(p, user)
    started = phase_student.start_in_app(p, title, start_code)
    return ctx, p, started["msg"]


def attempt_view(msg):
    s, v = S.jhttp("GET", f"/api/student/attempts/{msg['attempt_id']}", token=msg["token"],
                   headers={"X-FairPlay-Session": msg["session"], "X-FairPlay-Platform": "mac"})
    return v


def api_start(user, exam, start_code, device):
    token = S.login(user, "loadtest")
    st, a = S.start_attempt(token, exam["id"], start_code, device=device)
    assert st == 200, (st, a)
    v = S.jhttp("GET", f"/api/student/attempts/{a['attempt_id']}", token=token,
                headers={"X-FairPlay-Session": a["session"], "X-FairPlay-Platform": "mac"})[1]
    msg = {"attempt_id": a["attempt_id"], "token": token, "session": a["session"], "url": v["external_url"],
           "allowed_domains": v["allowed_domains"], "finish_url_regex": v.get("finish_url_regex"),
           "device": device, "kind": "canvas"}
    if v.get("canvas_scope"):
        msg["canvas_scope"] = v["canvas_scope"]
    return msg


def bridge_checks(label, msg, exam, want_open_book):
    scope = msg.get("canvas_scope")
    check(f"[{label}] openExternal carries canvas_scope with the saved open_book ({want_open_book})",
          scope == {"course_id": "1", "item_kind": "quizzes", "item_id": "1", "open_book": want_open_book}, json.dumps(scope))
    v = attempt_view(msg)
    check(f"[{label}] student attempt view carries the same canvas_scope object", v.get("canvas_scope") == scope, json.dumps(v.get("canvas_scope")))
    check(f"[{label}] the app's parser accepts the message and the start link passes the lobby policy",
          N.scope_from_json(scope) is not None
          and N.decide(msg["url"], C.CANVAS_HOST, N.scope_from_json(scope), N.LOBBY) == N.ALLOW)
    check(f"[{label}] no access code in the message or the attempt view", all(c not in json.dumps([msg, v]) for c in ALL_CODES if c))


# ------------------------------------------------------------------------------------------ B. the journey
def make_app(tab, msg):
    app = restricted.RestrictedApp(msg, tab)
    return app


def blocked_click(label, app, tab, css, path, expect_event=True):
    url0 = tab.url
    n0, ev0 = len(fake_state()["page_hits"]), len(app.events)
    first = N.block_detail(C.CANVAS + path) not in app.block_log.seen
    app.notice = None
    app.click(css)
    ok = tab.url == url0 and not any(p.split("?")[0] == path for p in hit_paths(n0))
    check(f"[{label}] click {css}: refused, page stays, {path} never requested", ok,
          f"url={tab.url} hits={hit_paths(n0)}")
    if expect_event:
        shown = app.notice == restricted.NOTICE
        recorded = len(app.events) > ev0 and app.events[-1][0] == "blocked_navigation"
        if first:
            check(f"[{label}] click {css}: blocked_navigation recorded and the notice shown", shown and recorded, str(app.events[ev0:]))
        else:
            check(f"[{label}] click {css}: notice shown, page already recorded once so no second event",
                  shown and not recorded, str(app.events[ev0:]))


def allowed_click(label, app, tab, css, path):
    n0 = len(fake_state()["page_hits"])
    app.click(css)
    check(f"[{label}] click {css}: opens {path}", urlsplit(tab.url).path == path and path in hit_paths(n0), f"url={tab.url} hits={hit_paths(n0)}")


def sign_in(label, app, tab, landing=QUIZ1):
    fake("POST", "/mode", {"require_login": True, "warm_sso": False})
    n0 = len(fake_state()["page_hits"])
    logins0 = fake_state()["logins"]
    app.start()
    tab.settle()
    check(f"[{label}] the start link was redirected through canvas login, go host and SAML host to the sign-in form",
          urlsplit(tab.url).hostname == C.SAML_HOST and urlsplit(tab.url).path == "/idp/sso", tab.url)
    hops = [u for (u, main, redirect) in tab.route_log if redirect]
    hopset = {urlsplit(u).hostname + urlsplit(u).path for u in hops}
    check(f"[{label}] every redirect hop passed the navigation delegate (canvas /login, /login/saml, go, saml)",
          {"canvas.fake.test/login", "canvas.fake.test/login/saml", "go.fake.test/sso/start", "saml.fake.test/idp/sso"} <= hopset,
          str(sorted(hopset)))
    check(f"[{label}] a committed sign-in page was seen (signInSeen) and the phase is still lobby",
          app.sign_in_seen and app.phase == N.LOBBY)
    app.click("#idp_signin")
    tab.settle(1.0)
    paths = [h for h in hits_since(n0)]
    check(f"[{label}] sign-in landed on the dashboard once (fake saw GET /) and the app opened the exam link by itself",
          any(h == f"GET {C.CANVAS_HOST}/" for h in paths) and fake_state()["logins"] == logins0 + 1
          and app.auto_open_count == 1 and tab.url == landing, f"{tab.url} auto={app.auto_open_count} hits={paths}")
    i_dash = max(i for i, h in enumerate(paths) if h == f"GET {C.CANVAS_HOST}/")
    check(f"[{label}] the exam link request followed the dashboard",
          f"GET {C.CANVAS_HOST}{urlsplit(landing).path}" in paths[i_dash + 1:])


def lobby_phase(label, app, tab, open_book):
    # Back goes to the dashboard (allowed in the lobby); no second auto-open
    check(f"[{label}] Back is enabled on the quiz page (previous page is the dashboard)", app.back_enabled())
    app.back_pressed()
    tab.settle(1.0)
    check(f"[{label}] Back returned to the dashboard and the exam link was not auto-opened again",
          urlsplit(tab.url).path == "/" and app.auto_open_count == 1, f"{tab.url} auto={app.auto_open_count}")
    blocked_click(label, app, tab, "#course2", "/courses/2")
    blocked_click(label, app, tab, "#nav_inbox", "/conversations")
    blocked_click(label, app, tab, "#nav_calendar", "/calendar")
    blocked_click(label, app, tab, "#nav_profile", "/profile")
    allowed_click(label, app, tab, "#course1", "/courses/1")
    allowed_click(label, app, tab, "#l_quizlist", "/courses/1/quizzes")
    blocked_click(label, app, tab, "#l_quiz2", "/courses/1/quizzes/2")
    for css, path in (("#l_files", "/courses/1/files"), ("#l_pages", "/courses/1/pages/p1"),
                      ("#l_modules", "/courses/1/modules"), ("#l_wiki", "/courses/1/wiki/w1")):
        blocked_click(label + (" open_book" if open_book else ""), app, tab, css, path)
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1")
    check(f"[{label}] still in the lobby phase before /take", app.phase == N.LOBBY)

    # pushState on the dashboard
    allowed_click(label, app, tab, "#l_dash", "/")
    n0, ev0 = len(fake_state()["page_hits"]), len(app.events)
    app.notice = None
    app.tab.click("#spa_inbox")
    tab.pump(0.5)
    url_mid = tab.url
    deadline = time.time() + 12
    while time.time() < deadline and urlsplit(tab.url).path != "/":
        tab.pump(0.2)
    tab.settle(1.0)
    check(f"[{label}] pushState to /conversations was seen (page URL changed without a navigation request)",
          urlsplit(url_mid).path == "/conversations" or any("/conversations (in page)" in d for _, d in app.events),
          f"mid={url_mid} events={app.events[ev0:]}")
    check(f"[{label}] pushState route change recovered: back on the dashboard, event '(in page)', notice shown",
          urlsplit(tab.url).path == "/" and any(d == "canvas page /conversations (in page)" for _, d in app.events[ev0:])
          and app.notice == restricted.NOTICE and any(r.startswith("back to") for r in app.recoveries),
          f"url={tab.url} events={app.events[ev0:]} rec={app.recoveries}")
    check(f"[{label}] the server never got a request for /conversations", "/conversations" not in hit_paths(n0))
    # replaceState on the dashboard
    app.notice = None
    ev1, rec1 = len(app.events), len(app.recoveries)
    tab.click("#spa_replace")
    deadline = time.time() + 12
    while time.time() < deadline and (urlsplit(tab.url).path == "/calendar" or len(app.recoveries) == rec1):
        tab.pump(0.2)
    tab.settle(1.0)
    check(f"[{label}] replaceState to /calendar also detected and recovered to an allowed page",
          urlsplit(tab.url).path != "/calendar" and any(d == "canvas page /calendar (in page)" for _, d in app.events[ev1:])
          and app.allows_main_frame(tab.url), f"url={tab.url} events={app.events[ev1:]} rec={app.recoveries[rec1:]}")


def fill_gate_and_take(label, app, tab):
    stand = app.stand
    # a page of ANOTHER quiz with its own code form reaches the view without the delegate: it must not get the code
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1") if urlsplit(tab.url).path != "/courses/1/quizzes/1" else None
    calls0, q2_0 = stand.issue_calls, fake_state()["quiz2_posts"]
    app.goto_unguarded(f"{C.CANVAS}/courses/1/quizzes/2/take")
    check(f"[{label}] second quiz page with its own access-code form is on screen (delegate bypassed)",
          tab.eval("!!document.querySelector('input#quiz_access_code')") is True and "quizzes/2/take" in tab.url, tab.url)
    out = stand.on_loaded(tab)
    check(f"[{label}] code gate: the second quiz's page gets no code (gate), no request to the server, field empty",
          out == "gate" and stand.issue_calls == calls0 and tab.eval("document.querySelector('input#quiz_access_code').value") == ""
          and fake_state()["quiz2_posts"] == q2_0 and fake_state()["quiz2_real_code"] == 0, f"{out} calls={stand.issue_calls - calls0}")
    check(f"[{label}] the fill script itself refuses that page (path check in the injected script)",
          tab.eval(S.SCRIPTS.fill("DUMMY-NOT-THE-CODE", C.CANVAS, "/courses/1/quizzes/1")) == "wrongpath"
          and tab.eval("document.querySelector('input#quiz_access_code').value") == "")
    app.quiz_pressed()
    tab.settle()
    check(f"[{label}] 'Go to my quiz' from that page opens the exam link", tab.url == QUIZ1, tab.url)
    app.quiz_pressed()
    n = len(tab.route_log)
    tab.pump(0.5)
    check(f"[{label}] 'Go to my quiz' on the exam page does nothing", len(tab.route_log) == n and tab.url == QUIZ1)

    # take the quiz: phase flips at /take, code fetched from the real server and typed
    acc0, calls1 = fake_state()["accepted"], stand.issue_calls
    app.click("#take_quiz")
    tab.settle(1.0)
    check(f"[{label}] phase flipped to QUIZ when /take committed", app.phase == N.QUIZ, app.phase)
    check(f"[{label}] code fetched from the real FairPlay server once and typed on the exact quiz: quiz opens",
          stand.issue_calls == calls1 + 1 and fake_state()["accepted"] == acc0 + 1 and "What is 2+2?" in tab.text()
          and stand.codes_seen[-1] == fake_state()["code"], f"calls={stand.issue_calls - calls1} url={tab.url} log={stand.log}")
    ALL_CODES.add(stand.codes_seen[-1])


def quiz_phase_closed(label, app, tab):
    take_url = tab.url
    check(f"[{label}] on the quiz page in QUIZ phase", urlsplit(take_url).path == "/courses/1/quizzes/1/take")
    for css, path in (("#l_dash", "/"), ("#l_course1", "/courses/1"), ("#l_quizlist", "/courses/1/quizzes"),
                      ("#l_course2", "/courses/2"), ("#l_inbox", "/conversations"), ("#l_calendar", "/calendar"),
                      ("#l_quiz2", "/courses/1/quizzes/2"), ("#l_files", "/courses/1/files"),
                      ("#l_file", "/courses/1/files/77"), ("#l_rootfile", "/files/77"), ("#l_pages", "/courses/1/pages/p1"),
                      ("#l_modules", "/courses/1/modules"), ("#l_wiki", "/courses/1/wiki/w1"),
                      ("#l_download", "/courses/1/files/77/download"), ("#l_dots", "/courses/1/quizzes/2")):
        blocked_click(label, app, tab, css, path)
    blocked_click(label, app, tab, "#l_enc", "/courses/1/quizzes/1%2f..%2f2")
    n0 = len(fake_state()["page_hits"])
    app.click("#l_other")
    check(f"[{label}] a link to a host that is not allowed is refused too and never requested",
          tab.url == take_url and not any("other.fake.test" in h for h in hits_since(n0)))
    check(f"[{label}] fake Canvas served no download", fake_state()["downloads"] == 0)
    check(f"[{label}] Back targets: dashboard and sign-in hosts are not allowed in QUIZ phase, allowed in the lobby",
          not app.back_allowed(DASH) and not app.back_allowed(f"https://{C.SAML_HOST}/idp/sso")
          and not app.back_allowed(f"{C.CANVAS}/login") and app.back_allowed(f"{C.CANVAS}/courses/1/quizzes/1")
          and app.allows_main_frame(f"https://{C.SAML_HOST}/idp/sso"))
    # pushState in the quiz phase goes back to the exam link
    ev0, rec0 = len(app.events), len(app.recoveries)
    tab.click("#spa_pages")
    deadline = time.time() + 12
    while time.time() < deadline and (len(app.recoveries) == rec0 or urlsplit(tab.url).path == "/courses/1/pages/p1"):
        tab.pump(0.2)
    tab.settle(1.0)
    check(f"[{label}] pushState to /courses/1/pages/p1 in QUIZ phase: detected, recovered by loading the exam link",
          any(d == "canvas page /courses/1/pages/p1 (in page)" for _, d in app.events[ev0:])
          and app.recoveries[rec0:] == ["load start"] and tab.url == QUIZ1, f"{tab.url} {app.events[ev0:]} {app.recoveries[rec0:]}")
    app.click("#take_quiz")
    check(f"[{label}] back on the quiz after the recovery (code already accepted: no second fill)",
          "What is 2+2?" in tab.text() and app.stand.budget.used == 1, f"{tab.url} fills={app.stand.budget.used}")


def quiz_phase_open_book(label, app, tab):
    check(f"[{label}] on the quiz page in QUIZ phase", urlsplit(tab.url).path == "/courses/1/quizzes/1/take")
    n0 = len(fake_state()["page_hits"])
    for css, path in (("#l_files", "/courses/1/files"), ("#l_pages", "/courses/1/pages/p1"),
                      ("#l_modules", "/courses/1/modules"), ("#l_wiki", "/courses/1/wiki/w1"),
                      ("#l_file", "/courses/1/files/77"), ("#l_rootfile", "/files/77")):
        allowed_click(label + " open_book", app, tab, css, path)
    check(f"[{label}] Back from a course page is enabled (previous page is on the exam host, not a sign-in page)", app.back_enabled())
    blocked_click(label + " open_book", app, tab, "#l_download", "/courses/1/files/77/download")
    check(f"[{label}] open_book keeps downloads blocked: fake Canvas served none", fake_state()["downloads"] == 0)
    url0, ev0, att0 = tab.url, len(app.events), fake_state()["attachments"]
    app.click("#l_attach")
    check(f"[{label}] a page the policy allows but the server sends as an attachment is cancelled at the response: "
          f"page stays, 'download from' event, no new document",
          tab.url == url0 and any(d == f"download from {C.CANVAS_HOST}" for _, d in app.events[ev0:])
          and fake_state()["attachments"] == att0 + 1, f"url={tab.url} ev={app.events[ev0:]}")
    for css, path in (("#l_inbox", "/conversations"), ("#l_dash", "/"), ("#l_course1", "/courses/1"),
                      ("#l_course2", "/courses/2"), ("#l_quizlist", "/courses/1/quizzes"), ("#l_quiz2", "/courses/1/quizzes/2"),
                      ("#l_calendar", "/calendar")):
        blocked_click(label + " open_book", app, tab, css, path)
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1")
    app.click("#take_quiz")
    check(f"[{label}] back on the quiz", "What is 2+2?" in tab.text(), tab.url)


def finish(label, app, tab):
    check(f"[{label}] not finished before the quiz is handed in", not app.finishing)
    tab.click("input[name=q1][value='4']")
    tab.click("#submit_quiz")
    deadline = time.time() + 10
    while time.time() < deadline and urlsplit(tab.url).path != "/courses/1/quizzes/1/submissions/5":
        tab.pump(0.1)
    tab.pump(0.4)
    check(f"[{label}] submit landed on /submissions/5, the finish regex matched and the 2 s wait started",
          app.stand.finish_hit(tab.url) and app.finish_due is not None and not app.finishing, f"{tab.url} due={app.finish_due}")
    deadline = time.time() + 6
    while time.time() < deadline and not app.finishing:
        tab.pump(0.2)
    check(f"[{label}] finish detected after the 2 s wait (same page still a finish page)", app.finishing and app.finish_reason == "finish_url")


def verify_events(label, ttok, exam_id, username, app, min_blocked):
    s, res = S.jhttp("GET", f"/api/exams/{exam_id}/results", token=ttok)
    a = next((x for x in res["attempts"] if x["username"] == username), None)
    evs = a.get("events", []) if a else []
    types = [e["type"] for e in evs]
    blocked = [e for e in evs if e["type"].endswith("blocked_navigation")]
    details = " ".join(str(e.get("detail", "")) for e in blocked)
    check(f"[{label}] teacher results hold {len(blocked)} blocked_navigation events and one canvas_code_issued",
          len(blocked) >= min_blocked and types.count("canvas_code_issued") == 1, f"{len(blocked)} {types.count('canvas_code_issued')}")
    check(f"[{label}] event details are 'canvas page <path>' only: no query, no host, no code",
          all(re.fullmatch(r"canvas page /[^?#]*( \(in page\))?|download from [a-z0-9.\-]+|[a-z0-9.\-]+", str(e.get("detail", ""))) for e in blocked)
          and not any(c in details for c in ALL_CODES if c), details[:200])


def journey(label, tab, msg, ttok, exam_id, username, open_book, hand_in):
    app = make_app(tab, msg)
    sign_in(label, app, tab)
    lobby_phase(label, app, tab, open_book)
    fill_gate_and_take(label, app, tab)
    if open_book:
        quiz_phase_open_book(label, app, tab)
    else:
        quiz_phase_closed(label, app, tab)
    finish(label, app, tab)
    hand_in(app)
    verify_events(label, ttok, exam_id, username, app, 8 if not open_book else 4)
    return app


def assignment_journey(label, tab, msg, ttok, exam_id, username):
    """New Quizzes / assignment link: the quiz phase starts with the first assignment page."""
    app = make_app(tab, msg)
    asg = f"{C.CANVAS}/courses/1/assignments/9"
    sign_in(label, app, tab, landing=asg)
    check(f"[{label}] the first assignment page (the auto-opened exam link) started the QUIZ phase", app.phase == N.QUIZ, app.phase)
    check(f"[{label}] Back to the dashboard is not offered in the QUIZ phase", not app.back_enabled())
    for css, path in (("#l_dash", "/"), ("#l_course1", "/courses/1"), ("#l_quizlist", "/courses/1/quizzes"),
                      ("#l_asglist", "/courses/1/assignments"), ("#l_asg10", "/courses/1/assignments/10"),
                      ("#l_quiz1", "/courses/1/quizzes/1"), ("#l_inbox", "/conversations"),
                      ("#l_files", "/courses/1/files"), ("#l_course2", "/courses/2")):
        blocked_click(label, app, tab, css, path)
    allowed_click(label, app, tab, "#l_asg_sub", "/courses/1/assignments/9/submissions/1")
    allowed_click(label, app, tab, "#l_asg", "/courses/1/assignments/9")
    check(f"[{label}] no confirmation page yet: not finished", not app.finishing)
    allowed_click(label, app, tab, "#l_asg_done", "/courses/1/assignments/9/submission-confirmation")
    deadline = time.time() + 6
    while time.time() < deadline and not app.finishing:
        tab.pump(0.2)
    check(f"[{label}] finish detected from the assignment finish regex ({app.stand.finish_regex})",
          app.finishing and app.finish_reason == "finish_url" and app.stand.finish_hit(tab.url), tab.url)
    return app


def warm_sso(label, tab, msg):
    fake("POST", "/mode", {"require_login": True, "warm_sso": True})
    fake("POST", "/forget-sessions")
    app = make_app(tab, msg)
    n0 = len(fake_state()["page_hits"])
    app.start()
    tab.settle(1.2)
    note(f"observed [{label}]: sign-in done by redirects only (IdP session still valid): landed {tab.url}; "
         f"signInSeen={app.sign_in_seen} auto-open count={app.auto_open_count}")
    check(f"[{label}] warm SSO: the redirect chain ended on the dashboard", urlsplit(tab.url).path == "/" and fake_state()["logins"] >= 1,
          tab.url)
    app.quiz_pressed()
    tab.settle()
    check(f"[{label}] warm SSO: the 'Go to my quiz' button opens the exam link", tab.url == QUIZ1, tab.url)
    fake("POST", "/mode", {"require_login": True, "warm_sso": False})
    return app


def legacy_without_scope(label, tab, msg):
    m = {k: v for k, v in msg.items() if k != "canvas_scope"}
    fake("POST", "/mode", {"require_login": False})
    app = make_app(tab, m)
    n0 = len(fake_state()["page_hits"])
    tab.load(f"{C.CANVAS}/conversations")
    tab.settle()
    check(f"[{label}] no canvas_scope: the earlier behaviour, any page of the Canvas host opens (no restriction)",
          app.scope is None and urlsplit(tab.url).path == "/conversations" and "/conversations" in hit_paths(n0), tab.url)
    app.start()
    tab.settle()
    check(f"[{label}] no canvas_scope: exam link opens, a quiz page of any id still passes the host rules",
          app.allows(f"{C.CANVAS}/courses/9/quizzes/9") and not app.blocks_main_frame(f"{C.CANVAS}/inbox"))


# ------------------------------------------------------------------------------------------ driver
def run(pw, ui_browser, T, ttok, shots, engines, hand_in_chromium, hand_in_webkit):
    results = {}
    s, cl = S.jhttp("POST", "/api/classes", {"name": "Restricted browser class"}, ttok)
    assert s == 201, (s, cl)
    class_id = cl["id"]
    users = [f"lt{i:04d}" for i in range(C.BURST_USERS + 1, C.LOADTEST_USERS + 1)]
    s, _ = S.jhttp("POST", f"/api/classes/{class_id}/members", {"usernames": users}, ttok)
    check(f"{len(users)} students added to a separate class for the restricted-browser exams", s in (200, 201), f"{s}")
    r1 = server_side(T, ttok, class_id, shots)

    # ---- R1: open_book off, managed. Chromium student through the real page + app stand-in; WebKit through the API
    section("8b. Student journey, open_book off (R1)")
    code_before = fake_state()["code"]
    start_code = ui_open_exam(T, r1["id"])
    code = fake_state()["code"]
    ALL_CODES.add(code)
    check("R1 opened through the UI: fake Canvas holds a new managed code", len(code) == 24 and code != code_before)
    exam = {"id": r1["id"]}
    ctx, page, msg = start_via_page(ui_browser, "lt0153", "Restricted R1", start_code, "rb-dev-chromium")
    bridge_checks("R1 page", msg, exam, False)
    msg = {**msg, "device": "rb-dev-chromium"}
    s, e = S.jhttp("GET", f"/api/exams/{r1['id']}", token=ttok)
    if "chromium" in engines:
        tab = tabs.ChromiumTab(pw)
        try:
            section("8c. Journey in Chromium (R1)")
            results["chromium"] = journey("chromium R1", tab, msg, ttok, r1["id"], "lt0153", False,
                                          lambda app: hand_in_chromium(page, msg["attempt_id"]))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[chromium R1] journey ran to the end", False, repr(ex))
        finally:
            tab.close()
        tab = tabs.ChromiumTab(pw)
        try:
            warm_sso("chromium", tab, msg)
            legacy_without_scope("chromium", tab, msg)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[chromium] warm SSO / legacy checks ran", False, repr(ex))
        finally:
            tab.close()
    if "webkit" in engines:
        wmsg = api_start("lt0154", exam, start_code, "rb-dev-webkit")
        bridge_checks("R1 api", wmsg, exam, False)
        tab = None
        try:
            tab = tabs.WebKitTab()
            section("8d. Journey in WebKit (R1)")
            results["webkit"] = journey("webkit R1", tab, wmsg, ttok, r1["id"], "lt0154", False,
                                        lambda app: hand_in_webkit(wmsg))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[webkit R1] journey ran to the end", False, repr(ex))
        finally:
            if tab:
                tab.close()
        try:
            tab = tabs.WebKitTab()
            warm_sso("webkit", tab, wmsg)
            legacy_without_scope("webkit", tab, wmsg)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[webkit] warm SSO / legacy checks ran", False, repr(ex))
        finally:
            if tab:
                tab.close()
    fake("POST", "/mode", {"require_login": True, "warm_sso": False})
    ui_close_exam(T, r1["id"])
    check("R1 closed: managed code cleared in the fake Canvas", fake_state()["code"] == "")

    # ---- R2: open_book on
    section("8e. Student journey, open_book on (R2)")
    r2 = ui_new_exam(T, class_id, "Restricted R2", open_book=True, shots=shots)
    check("UI: R2 created with the open-book box ticked; sentence names pages, modules and files",
          r2["checked"] and "pages, modules and files" in r2["line"], r2["line"])
    e2 = get_exam(ttok, r2["id"])
    check("R2 API: open_book true, scope course 1 quizzes 1", e2["canvas"]["open_book"] is True and e2["canvas"]["scope"]["item_id"] == "1")
    start2 = ui_open_exam(T, r2["id"])
    ALL_CODES.add(fake_state()["code"])
    ex2 = {"id": r2["id"]}
    ctx2, page2, msg2 = start_via_page(ui_browser, "lt0155", "Restricted R2", start2, "rb-dev-chromium-2")
    bridge_checks("R2 page", msg2, ex2, True)
    msg2 = {**msg2, "device": "rb-dev-chromium-2"}
    if "chromium" in engines:
        tab = tabs.ChromiumTab(pw)
        try:
            section("8f. Journey in Chromium (R2, open book)")
            journey("chromium R2", tab, msg2, ttok, r2["id"], "lt0155", True,
                    lambda app: hand_in_chromium(page2, msg2["attempt_id"]))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[chromium R2] journey ran to the end", False, repr(ex))
        finally:
            tab.close()
    if "webkit" in engines:
        wmsg2 = api_start("lt0156", ex2, start2, "rb-dev-webkit-2")
        bridge_checks("R2 api", wmsg2, ex2, True)
        tab = None
        try:
            tab = tabs.WebKitTab()
            section("8g. Journey in WebKit (R2, open book)")
            journey("webkit R2", tab, wmsg2, ttok, r2["id"], "lt0156", True, lambda app: hand_in_webkit(wmsg2))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[webkit R2] journey ran to the end", False, repr(ex))
        finally:
            if tab:
                tab.close()
    ui_close_exam(T, r2["id"])
    fake("POST", "/mode", {"require_login": False, "warm_sso": False})

    # ---- R4: assignment link (New Quizzes), manual code
    section("8h. Assignment exam (New Quizzes link)")
    body = {"title": "Restricted R4", "duration_min": 30, "mode": "external", "external_url": f"{C.CANVAS}/courses/1/assignments/9",
            "external_kind": "canvas", "allowed_domains": EXTRA_DOMAINS.split("\n"), "questions": [], "class_id": int(class_id),
            "canvas": {"quiz_type": "new", "access_code_source": "manual", "access_code": "ManualCodeR4-0001"}}
    s, r4 = S.jhttp("POST", "/api/exams", body, ttok)
    check("R4 created: scope is course 1 / assignments / 9, open_book false", s == 201 and r4["canvas"]["scope"] == {
        "course_id": "1", "item_kind": "assignments", "item_id": "9"} and r4["canvas"]["open_book"] is False, f"{s} {str(r4)[:200]}")
    s, op4 = S.jhttp("POST", f"/api/exams/{r4['id']}/open", None, ttok)
    check("R4 opens (manual code: nothing sent to Canvas)", s == 200, f"{s} {str(op4)[:200]}")
    ALL_CODES.add("ManualCodeR4-0001")
    for eng, user in (("chromium", "lt0157"), ("webkit", "lt0158")):
        if eng not in engines:
            continue
        m4 = api_start(user, {"id": r4["id"]}, op4["start_code"], f"rb-dev-{eng}-4")
        check(f"[{eng} R4] canvas_scope in the attempt is assignments / 9", m4.get("canvas_scope") == {
            "course_id": "1", "item_kind": "assignments", "item_id": "9", "open_book": False}, json.dumps(m4.get("canvas_scope")))
        tab = None
        try:
            tab = tabs.ChromiumTab(pw) if eng == "chromium" else tabs.WebKitTab()
            assignment_journey(f"{eng} R4", tab, m4, ttok, r4["id"], user)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check(f"[{eng} R4] journey ran to the end", False, repr(ex))
        finally:
            if tab:
                tab.close()
    S.jhttp("POST", f"/api/exams/{r4['id']}/close", None, ttok)
    fake("POST", "/mode", {"require_login": False, "warm_sso": False})

    # ---- an exam whose link has no scope cannot be saved any more
    section("8i. Exam link without a scope")
    body = {"title": "Restricted R3", "duration_min": 30, "mode": "external", "external_url": f"{C.CANVAS}/somewhere",
            "external_kind": "canvas", "allowed_domains": EXTRA_DOMAINS.split("\n"), "questions": [], "class_id": int(class_id),
            "canvas": {"quiz_type": "new", "access_code_source": "manual", "access_code": "ManualCodeR3-0001", "open_book": True}}
    s, r3 = S.jhttp("POST", "/api/exams", body, ttok)
    check("creating a Canvas exam with a link that has no scope is refused 422", s == 422, f"{s}")
    T.goto(f"{C.SERVER}/teacher/exams/new?class={class_id}")
    T.fill("#exam-title", "Restricted R3 UI")
    T.get_by_role("button", name=re.compile("More settings")).click()
    T.get_by_text("On another website").first.click()
    T.get_by_text("Canvas quiz", exact=True).first.click()
    T.fill("#exam-external-url", f"{C.CANVAS}/courses/1")
    T.wait_for_selector("[data-testid=canvas-settings]")
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_timeout(1200)
    check("UI: a course link without a quiz is not saved and the teacher is told what link is needed",
          "/teacher/exams/new" in T.url and "must open one quiz or assignment" in T.inner_text("body"), T.url)
    for c in (ctx, ctx2):
        try:
            c.close()
        except Exception:
            pass
    return results

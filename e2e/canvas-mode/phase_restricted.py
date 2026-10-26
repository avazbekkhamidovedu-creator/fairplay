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
from lib import check, fake, fake_state, note, section, server_hits

QUIZ1 = f"{C.CANVAS}/courses/1/quizzes/1"
DASH = f"{C.CANVAS}/"
EXTRA_DOMAINS = f"{C.SSO_HOST}\n{C.GO_HOST}\n{C.SAML_HOST}"


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
    T.wait_for_timeout(1200)


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
          "open the dashboard" in r1["line"] and "only open that quiz" in r1["line"] and "pages, modules" not in r1["line"],
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
          "plus this course's pages, modules and files" in line_on and "downloads stay blocked" in line_on, line_on)
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
            "class_id": int(class_id), "canvas": {"quiz_type": "classic", "open_book": True,
                                                  "access_code_source": "manual", "access_code": "LegacyField-123"}}
    s, e = S.jhttp("POST", "/api/exams", body, ttok)
    check("API create with canvas.open_book true: stored and returned", s == 201 and e["canvas"]["open_book"] is True, f"{s} {e}")
    eid = e["id"]
    check("teacher view of a Canvas exam: canvas holds exactly quiz_type, scope, open_book, removed_hosts, sign_in_hosts",
          set(e["canvas"]) == {"quiz_type", "scope", "open_book", "removed_hosts", "sign_in_hosts"}, json.dumps(sorted(e["canvas"])))
    check("legacy code fields in the request are ignored and never come back (no access_code_source, access_code, code_status)",
          "LegacyField-123" not in json.dumps(get_exam(ttok, eid)) and not any(k in json.dumps(e) for k in ("access_code", "code_status")),
          json.dumps(e["canvas"]))
    base = {k: v for k, v in body.items() if k != "canvas"}

    def put(extra=None, **over):
        b = {**base, **over}
        if extra is not None:
            b["canvas"] = extra
        return S.jhttp("PUT", f"/api/exams/{eid}", b, ttok)

    s, e = put()
    check("PUT without a canvas block keeps open_book true", s == 200 and e["canvas"]["open_book"] is True, f"{s}")
    s, e = put({"quiz_type": "classic"})
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
        s, e = put({"quiz_type": "new", "open_book": True}, external_url=C.CANVAS + odd)
        check(f"link {odd[:44]} is refused 422 with the one-quiz message (no exam without a scope)",
              s == 422 and "must open one quiz or assignment" in json.dumps(e), f"{s} {str(e)[:120]}")
    check("the refused saves left the link and scope as they were",
          get_exam(ttok, eid)["canvas"]["scope"] == {"course_id": "2", "item_kind": "assignments", "item_id": "7"})
    s, e = put({"quiz_type": "classic"}, external_url=f"{C.CANVAS}/courses/1/quizzes/1/edit")
    check("a link under the quiz (.../quizzes/1/edit) is scoped to that quiz",
          s == 200 and e["canvas"]["scope"] == {"course_id": "1", "item_kind": "quizzes", "item_id": "1"}, f"{s}")
    for unclean in ("/courses/%31/quizzes/1", "/courses/1/quizzes/1;jsessionid=x", "/courses/1/quizzes/1\\..\\2"):
        s, e = put({"quiz_type": "classic"}, external_url=C.CANVAS + unclean)
        check(f"link {unclean[:40]} (percent, matrix parameter, backslash) is refused 422 with the clean-link message",
              s == 422 and "may not contain" in json.dumps(e), f"{s} {str(e)[:120]}")
    s, e = put({"quiz_type": "classic"}, external_url=f"{C.CANVAS}/courses/1/quizzes/1/take?x=1#y")
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


def bridge_checks(label, msg, exam, want_open_book):
    scope = msg.get("canvas_scope")
    core = {k: scope.get(k) for k in ("course_id", "item_kind", "item_id", "open_book")} if isinstance(scope, dict) else None
    check(f"[{label}] openExternal carries canvas_scope with the saved open_book ({want_open_book}), the server's sign-in hosts "
          f"and no quiz-lti hosts (classic)",
          core == {"course_id": "1", "item_kind": "quizzes", "item_id": "1", "open_book": want_open_book}
          and scope.get("sign_in_hosts") == [C.SAML_HOST, "login.microsoftonline.com", "*.duosecurity.com"]
          and scope.get("quiz_lti_hosts") == [],
          json.dumps(scope))
    v = attempt_view(msg)
    check(f"[{label}] student attempt view carries the same canvas_scope object", v.get("canvas_scope") == scope, json.dumps(v.get("canvas_scope")))
    check(f"[{label}] the app's parser accepts the message and the start link passes the lobby policy",
          N.scope_from_json(scope) is not None
          and N.decide(msg["url"], C.CANVAS_HOST, N.scope_from_json(scope), N.LOBBY) == N.ALLOW)
    check(f"[{label}] no code, no access_code_source anywhere in the message or the attempt view",
          not any(k in json.dumps([msg, v]) for k in ("access_code", "canvas_code", "code_status")), "")


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


EXAM_SERVER_PATHS = ("/student/external-done/x", "/student")


def server_blocked_click(label, app, tab, css, what):
    """A link, script or redirect that leads to a page of the exam server: nothing opens, nothing is requested."""
    url0, ev0 = tab.url, len(app.events)
    hits0 = {p: server_hits(p) for p in EXAM_SERVER_PATHS}
    app.notice = None
    app.click(css)
    tab.pump(0.6)
    tab.settle(0.6)
    hits1 = {p: server_hits(p) for p in EXAM_SERVER_PATHS}
    check(f"[{label}] {what} ({css}): refused, page stays, the exam server got no request",
          tab.url == url0 and hits1 == hits0, f"url={tab.url} hits {hits0} -> {hits1}")
    check(f"[{label}] {what} ({css}): notice shown and a blocked_navigation recorded or already recorded for that page",
          app.notice == restricted.NOTICE and (len(app.events) > ev0 or any(d.startswith("canvas page /student") for _, d in app.events)),
          str(app.events[ev0:]))


def exam_server_links(label, app, tab, css_list):
    for css, what in css_list:
        server_blocked_click(label, app, tab, css, what)


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
    exam_server_links(label + " LOBBY", app, tab, (("#nav_exam", "dashboard link to the exam server"),
                                                   ("#nav_examgo", "Canvas page that redirects to the exam server")))
    allowed_click(label, app, tab, "#course1", "/courses/1")
    allowed_click(label, app, tab, "#l_quizlist", "/courses/1/quizzes")
    blocked_click(label, app, tab, "#l_quiz2", "/courses/1/quizzes/2")
    for css, path in (("#l_files", "/courses/1/files"), ("#l_pages", "/courses/1/pages/p1"),
                      ("#l_modules", "/courses/1/modules"), ("#l_wiki", "/courses/1/wiki/w1")):
        blocked_click(label + (" open_book" if open_book else ""), app, tab, css, path)
    exam_server_links(label + " LOBBY", app, tab, (("#l_examserver", "course page link to the exam server home"),
                                                   ("#l_examdone", "course page link to the exam server hand-in page"),
                                                   ("#l_examgo", "exam-link redirect to the exam server"),
                                                   ("#spa_exam", "script navigation to the exam server")))
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1")
    allowed_click(label, app, tab, "#l_history", "/courses/1/quizzes/1/history")
    tab.pump(2.8)
    check(f"[{label}] lobby: the quiz history page matches the finish regex but the finish is not armed, so nothing finishes",
          app.stand.finish_hit(tab.url) and not app.armed and app.finish_due is None and not app.finishing,
          f"armed={app.armed} due={app.finish_due} finishing={app.finishing}")
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1")
    check(f"[{label}] still in the lobby phase before /take", app.phase == N.LOBBY)
    check(f"[{label}] lobby: the strip still has the finish control and the app is not locked in",
          app.finish_button_visible and not app.locked_in)

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


def take_quiz(label, app, tab):
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1") if urlsplit(tab.url).path != "/courses/1/quizzes/1" else None
    # a page of ANOTHER quiz reaches the view without the delegate: 'Go to my quiz' still brings the student back
    app.goto_unguarded(f"{C.CANVAS}/courses/1/quizzes/2/take")
    check(f"[{label}] the second quiz's page is on screen (delegate bypassed)", "quizzes/2/take" in tab.url, tab.url)
    app.quiz_pressed()
    tab.settle()
    check(f"[{label}] 'Go to my quiz' from that page opens the exam link", tab.url == QUIZ1, tab.url)
    app.quiz_pressed()
    n = len(tab.route_log)
    tab.pump(0.5)
    check(f"[{label}] 'Go to my quiz' on the exam page does nothing", len(tab.route_log) == n and tab.url == QUIZ1)
    app.click("#take_quiz")
    tab.settle(1.0)
    check(f"[{label}] phase flipped to QUIZ when /take committed and the quiz is on screen without any code step",
          app.phase == N.QUIZ and "What is 2+2?" in tab.text() and urlsplit(tab.url).path == "/courses/1/quizzes/1/take",
          f"{app.phase} {tab.url}")
    started = [e for e in app.events if e[0] == "canvas_quiz_started"]
    check(f"[{label}] canvas_quiz_started recorded once, at the moment the phase flipped", len(started) == 1, str(app.events))
    check(f"[{label}] QUIZ phase: locked in, the finish control is gone from the strip, the marker is stored, finish armed",
          app.locked_in and not app.finish_button_visible and app.marker.is_set(app.aid) and app.armed
          and app.marker.is_armed(app.aid), "")


def history_escape(label, app, tab):
    """What a student could try without a link: history.back() out of the quiz (a script, a keyboard shortcut)."""
    seen = []
    for _ in range(8):
        before = tab.url
        tab.eval("(history.back(), 1)")
        tab.pump(0.7)
        tab.settle(0.8)
        seen.append(tab.url)
        if tab.url == before:
            break
    exam_pages = [u for u in seen if (urlsplit(u).hostname or "") == C.CANVAS_HOST]
    outside = [u for u in exam_pages if not N.is_under_item(u, app.scope)]
    check(f"[{label}] history.back() repeated: no Canvas page outside the exam item is ever left on screen at the end",
          not outside or all(not app.allows_main_frame(u) for u in outside) or
          (urlsplit(tab.url).hostname or "") != C.CANVAS_HOST or N.is_under_item(tab.url, app.scope), f"{seen}")
    final_host = urlsplit(tab.url).hostname
    if final_host == C.CANVAS_HOST:
        check(f"[{label}] after history.back() the view is on the exam item (or was recovered to it)",
              N.is_under_item(tab.url, app.scope), tab.url)
    else:
        note(f"observed [{label}]: history.back() out of the quiz ends on {tab.url} (an allowed sign-in host, no Canvas page)")
    app.quiz_pressed()
    tab.settle(1.0)
    if not N.is_under_item(tab.url, app.scope):
        app.tab.load(app.start_url)
        tab.settle(1.0)
    check(f"[{label}] 'Go to my quiz' puts the student back on the exam item", N.is_under_item(tab.url, app.scope), tab.url)


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
    exam_server_links(label + " QUIZ", app, tab, (("#l_examserver", "link to the exam server home"),
                                                  ("#l_examdone", "link to the exam server hand-in page (it would end the exam)"),
                                                  ("#l_examgo", "exam-link redirect to the exam server"),
                                                  ("#spa_exam", "script navigation to the exam server")))
    check(f"[{label}] fake Canvas served no download", fake_state()["downloads"] == 0)
    check(f"[{label}] Back targets: dashboard and sign-in hosts are not allowed in QUIZ phase, allowed in the lobby",
          not app.back_allowed(DASH) and not app.back_allowed(f"https://{C.SAML_HOST}/idp/sso")
          and not app.back_allowed(f"{C.CANVAS}/login") and app.back_allowed(f"{C.CANVAS}/courses/1/quizzes/1")
          and app.allows_main_frame(f"https://{C.SAML_HOST}/idp/sso"))
    check(f"[{label}] popups to the exam server and to other Canvas pages are blocked, the exam item opens in the same view",
          app.popup_plan("http://127.0.0.1:8765/student", C.CANVAS_HOST) == "block"
          and app.popup_plan("http://127.0.0.1:8765/student/external-done/x", C.CANVAS_HOST) == "block"
          and app.popup_plan(f"{C.CANVAS}/", C.CANVAS_HOST) == "block"
          and app.popup_plan(f"{C.CANVAS}/courses/1/quizzes/1/take", C.CANVAS_HOST) == "sameView")
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
    check(f"[{label}] back on the quiz after the recovery, still one canvas_quiz_started",
          "What is 2+2?" in tab.text() and len([e for e in app.events if e[0] == "canvas_quiz_started"]) == 1, tab.url)
    allowed_click(label, app, tab, "#l_quiz1", "/courses/1/quizzes/1")
    check(f"[{label}] the quiz landing page disarms the finish", not app.armed and not app.marker.is_armed(app.aid))
    allowed_click(label, app, tab, "#l_history", "/courses/1/quizzes/1/history")
    tab.pump(2.8)
    check(f"[{label}] QUIZ phase, not armed: the history page (a finish URL) does not end the exam",
          app.stand.finish_hit(tab.url) and app.finish_due is None and not app.finishing)
    allowed_click(label, app, tab, "#l_sub5", "/courses/1/quizzes/1/submissions/5")
    tab.pump(2.8)
    check(f"[{label}] landing page, then a submission URL without taking the quiz: not armed, nothing finishes",
          app.stand.finish_hit(tab.url) and app.finish_due is None and not app.finishing and not app.armed)
    check(f"[{label}] 'Go to my quiz' does nothing on a page already under the item", (app.quiz_pressed(), tab.url)[1].endswith("/submissions/5"))
    tab.load(app.start_url)
    tab.settle(1.0)
    app.click("#take_quiz")
    check(f"[{label}] taking the quiz arms the finish again", app.armed and "What is 2+2?" in tab.text())
    history_escape(label, app, tab)
    app.click("#take_quiz")
    check(f"[{label}] back on the quiz", "What is 2+2?" in tab.text(), tab.url)


def quiz_phase_open_book(label, app, tab):
    check(f"[{label}] on the quiz page in QUIZ phase", urlsplit(tab.url).path == "/courses/1/quizzes/1/take")
    for css, path in (("#l_files", "/courses/1/files"), ("#l_pages", "/courses/1/pages/p1"),
                      ("#l_modules", "/courses/1/modules"), ("#l_wiki", "/courses/1/wiki/w1"),
                      ("#l_file", "/courses/1/files/77"), ("#l_rootfile", "/files/77")):
        allowed_click(label + " open_book", app, tab, css, path)
    check(f"[{label}] open book: Back from a course page is off (the previous page is not under the exam item)", not app.back_enabled())
    blocked_click(label + " open_book", app, tab, "#l_download", "/courses/1/files/77/download")
    check(f"[{label}] open_book keeps downloads blocked: fake Canvas served none", fake_state()["downloads"] == 0)
    exam_server_links(label + " QUIZ open_book", app, tab, (("#l_examserver", "link to the exam server home"),
                                                            ("#l_examdone", "link to the exam server hand-in page"),
                                                            ("#l_examgo", "exam-link redirect to the exam server")))
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
    check(f"[{label}] back on the quiz; Back now reaches the item page (previous page is under the item)",
          "What is 2+2?" in tab.text() and app.back_enabled(), tab.url)


def relaunch_mid_quiz(label, app, tab, msg):
    """The app is quit and started again in the middle of the quiz: the marker keeps it in QUIZ."""
    defaults = dict(app.marker.defaults)
    again = restricted.RestrictedApp(msg, tab, defaults=defaults)
    check(f"[{label}] relaunch mid-quiz: the new app starts in QUIZ phase straight away and is locked in",
          again.phase == N.QUIZ and again.locked_in and not again.finish_button_visible, again.phase)
    check(f"[{label}] relaunch mid-quiz: the dashboard and course pages are refused at once, the exam item is allowed",
          again.blocks_main_frame(DASH) and again.blocks_main_frame(f"{C.CANVAS}/courses/1")
          and not again.blocks_main_frame(QUIZ1) and not again.blocks_main_frame(f"{C.CANVAS}/courses/1/quizzes/1/take"))
    ev0 = len(again.events)
    again.start()
    tab.settle(1.0)
    check(f"[{label}] relaunch mid-quiz: the exam link opens (the Canvas session is still valid)",
          N.is_under_item(tab.url, again.scope) and "Take the Quiz" in tab.text(), tab.url)
    again.click("#take_quiz")
    check(f"[{label}] relaunch mid-quiz: taking the quiz again does not record canvas_quiz_started a second time",
          "What is 2+2?" in tab.text() and not [e for e in again.events[ev0:] if e[0] == "canvas_quiz_started"], str(again.events[ev0:]))
    blocked_click(label + " after relaunch", again, tab, "#l_dash", "/")
    blocked_click(label + " after relaunch", again, tab, "#l_inbox", "/conversations")
    server_blocked_click(label + " after relaunch", again, tab, "#l_examdone", "link to the exam server hand-in page")
    return again


def relaunch_expired_session(label, tab_factory, msg, defaults):
    """Quit and started again, and Canvas wants a new sign-in while the quiz is running."""
    for skip_go in (False, True):
        fake("POST", "/mode", {"require_login": True, "warm_sso": False, "skip_go": skip_go})
        tab = tab_factory()
        try:
            tag = f"{label} expired session" + (", sign-in hosts all carry a sign-in word" if skip_go else ", go.* hop in the chain")
            app = restricted.RestrictedApp(msg, tab, defaults=dict(defaults))
            check(f"[{tag}] still QUIZ phase after the relaunch", app.phase == N.QUIZ)
            app.start()
            tab.settle(1.2)
            if not skip_go:
                check(f"[{tag}] the {C.GO_HOST} hop is not in the exam's sign-in host list, so it is refused in QUIZ phase "
                      f"(event recorded, no form shown, no Canvas page outside the item on screen)",
                      urlsplit(tab.url).hostname != C.SAML_HOST and any(d == "canvas page /sso/start" for _, d in app.events)
                      and not (urlsplit(tab.url).hostname == C.CANVAS_HOST and not N.is_under_item(tab.url, app.scope)),
                      f"{tab.url} {app.events}")
                continue
            check(f"[{tag}] the sign-in redirects (login, SAML) are allowed in QUIZ phase and the form shows",
                  urlsplit(tab.url).hostname == C.SAML_HOST, tab.url)
            app.click("#idp_signin")
            tab.settle(1.2)
            landed = tab.url
            check(f"[{tag}] the dashboard the sign-in lands on is refused, never shown",
                  not (urlsplit(landed).hostname == C.CANVAS_HOST and urlsplit(landed).path == "/"), landed)
            app.quiz_pressed()
            tab.settle(1.2)
            check(f"[{tag}] after signing in, 'Go to my quiz' opens the exam item",
                  N.is_under_item(tab.url, app.scope), tab.url)
        finally:
            tab.close()
            fake("POST", "/mode", {"require_login": True, "warm_sso": False, "skip_go": False})


def finish(label, app, tab, ttok, exam_id, username):
    check(f"[{label}] not finished before the quiz is handed in", not app.finishing)
    n0 = len(app.events)
    server_blocked_click(label + " QUIZ", app, tab, "#l_examdone", "link to the exam server hand-in page")
    check(f"[{label}] clicking toward the hand-in page did not finish the exam and the app still has no exit control",
          not app.finishing and app.locked_in and not app.finish_button_visible)
    s, v = S.jhttp("GET", f"/api/student/attempts/{app.aid}", token=app.stand.token,
                   headers={"X-FairPlay-Session": app.stand.session, "X-FairPlay-Platform": "mac", "X-FairPlay-Version": C.APP_VERSION})
    check(f"[{label}] the attempt is still in progress on the server", s == 200 and v.get("status") == "in_progress", f"{s} {v.get('status') if isinstance(v, dict) else v}")
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
    check(f"[{label}] finish detected after the 2 s wait (same page still a finish page): the only way out",
          app.finishing and app.finish_reason == "finish_url")
    check(f"[{label}] finishing clears the quiz marker", not app.marker.is_set(app.aid))


def verify_events(label, ttok, exam_id, username, app, min_blocked):
    s, res = S.jhttp("GET", f"/api/exams/{exam_id}/results", token=ttok)
    a = next((x for x in res["attempts"] if x["username"] == username), None)
    evs = a.get("events", []) if a else []
    types = [e["type"] for e in evs]
    blocked = [e for e in evs if e["type"].endswith("blocked_navigation")]
    started = [e for e in evs if e["type"].endswith("canvas_quiz_started")]
    check(f"[{label}] teacher results hold {len(blocked)} blocked_navigation events and exactly one canvas_quiz_started",
          len(blocked) >= min_blocked and len(started) == 1, f"{len(blocked)} started={len(started)} {types[:12]}")
    check(f"[{label}] no canvas_code_* event exists any more", not any("canvas_code" in t for t in types), str(types))
    check(f"[{label}] event details are 'canvas page <path>' only: no query, no host",
          all(re.fullmatch(r"canvas page /[^?#]*( \(in page\))?|download from [a-z0-9.\-]+|[a-z0-9.\-]+", str(e.get("detail", ""))) for e in blocked),
          " ".join(str(e.get("detail", "")) for e in blocked)[:200])
    check(f"[{label}] the exam-server pages the student tried are in the event log as blocked pages",
          any(str(e.get("detail", "")).startswith("canvas page /student") for e in blocked),
          " ".join(str(e.get("detail", "")) for e in blocked)[:200])


def journey(label, tab, msg, ttok, exam_id, username, open_book, hand_in, tab_factory=None, relaunch=True):
    app = make_app(tab, msg)
    sign_in(label, app, tab)
    lobby_phase(label, app, tab, open_book)
    take_quiz(label, app, tab)
    if open_book:
        quiz_phase_open_book(label, app, tab)
    else:
        quiz_phase_closed(label, app, tab)
    if relaunch:
        app2 = relaunch_mid_quiz(label, app, tab, msg)
        if tab_factory is not None:
            try:
                relaunch_expired_session(label, tab_factory, msg, app.marker.defaults)
            except Exception as ex:
                import traceback
                traceback.print_exc()
                check(f"[{label}] expired-session relaunch ran to the end", False, repr(ex))
        app = app2
    finish(label, app, tab, ttok, exam_id, username)
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
    check(f"[{label}] assignment: locked in, no finish control, canvas_quiz_started recorded once",
          app.locked_in and not app.finish_button_visible and len([e for e in app.events if e[0] == "canvas_quiz_started"]) == 1,
          str(app.events))
    exam_server_links(label + " QUIZ", app, tab, (("#l_examserver", "link to the exam server home"),
                                                  ("#l_examdone", "link to the exam server hand-in page")))
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
    app.start()
    tab.settle(1.5)
    check(f"[{label}] warm SSO (no sign-in page ever shown): the redirects end on the dashboard, which counts as the sign-in "
          f"landing, and the app opens the exam link by itself", app.auto_open_count == 1 and tab.url == QUIZ1
          and fake_state()["logins"] >= 1, f"{tab.url} auto={app.auto_open_count} signInSeen={app.sign_in_seen}")
    app.quiz_pressed()
    n = len(tab.route_log)
    tab.pump(0.5)
    check(f"[{label}] warm SSO: 'Go to my quiz' on the exam page does nothing", len(tab.route_log) == n and tab.url == QUIZ1)
    fake("POST", "/mode", {"require_login": True, "warm_sso": False})
    return app


def start_needs_scope(label, tab, msg):
    """The app refuses a Canvas exam start without a usable canvas_scope (bad_request) and loads nothing."""
    m = {k: v for k, v in msg.items() if k != "canvas_scope"}
    app = make_app(tab, m)
    n0 = len(fake_state()["page_hits"])
    app.start()
    tab.pump(0.5)
    check(f"[{label}] no canvas_scope: the app refuses to start (bad_request) and opens no page",
          app.start_error == "bad_request" and not hits_since(n0), f"{app.start_error} {hits_since(n0)}")
    other = {**msg, "canvas_scope": {"course_id": "1", "item_kind": "quizzes", "item_id": "2", "open_book": False}}
    app2 = make_app(tab, other)
    check(f"[{label}] a canvas_scope that does not cover the start link is refused too (bad_request)",
          app2.start_error == "bad_request")
    bad = {**msg, "canvas_scope": {"course_id": "12a", "item_kind": "quizzes", "item_id": "1"}}
    check(f"[{label}] a malformed canvas_scope is refused (bad_request)", make_app(tab, bad).start_error == "bad_request")


# ------------------------------------------------------------------------------------------ driver
def api_start_msg(user, password, exam_id, start_code, device):
    token = S.login(user, password)
    st, a = S.start_attempt(token, exam_id, start_code, device=device)
    assert st == 200, (st, a)
    v = S.jhttp("GET", f"/api/student/attempts/{a['attempt_id']}", token=token,
                headers={"X-FairPlay-Session": a["session"], "X-FairPlay-Platform": "mac", "X-FairPlay-Version": C.APP_VERSION})[1]
    msg = {"attempt_id": a["attempt_id"], "token": token, "session": a["session"], "url": v["external_url"],
           "allowed_domains": v["allowed_domains"], "finish_url_regex": v.get("finish_url_regex"),
           "device": device, "kind": "canvas"}
    if v.get("canvas_scope"):
        msg["canvas_scope"] = v["canvas_scope"]
    return msg


def platform_gate(T, ttok, class_id):
    """Canvas exams start only on a platform with a minimum Canvas version set, and only from an app at or above it."""
    section("8j. Platform gate for Canvas exams")
    body = {"title": "Gate exam", "duration_min": 30, "mode": "external", "external_url": QUIZ1,
            "external_kind": "canvas", "allowed_domains": EXTRA_DOMAINS.split("\n"), "questions": [],
            "class_id": int(class_id), "canvas": {"quiz_type": "classic"}}
    s, g = S.jhttp("POST", "/api/exams", body, ttok)
    assert s == 201, (s, g)
    s, op = S.jhttp("POST", f"/api/exams/{g['id']}/open", None, ttok)
    check("gate exam opens without a Canvas account or any code step", s == 200 and op.get("start_code"), f"{s} {str(op)[:150]}")
    code = op["start_code"]
    users = [f"lt{i:04d}" for i in range(C.BURST_USERS + 1, C.LOADTEST_USERS + 1)]
    cases = [(f"windows {C.APP_VERSION}", "windows", C.APP_VERSION, 409), (f"ipad {C.APP_VERSION}", "ipad", C.APP_VERSION, 409),
             (f"mac {C.APP_VERSION_OLDER} (below the minimum)", "mac", C.APP_VERSION_OLDER, 426),
             (f"mac {C.APP_VERSION} (the app under test)", "mac", C.APP_VERSION, 200), ("mac 0.10.2", "mac", "0.10.2", 200)]
    for i, (label, platform, version, want) in enumerate(cases):
        tok = S.login(users[i], "loadtest")
        st, a = S.start_attempt(tok, g["id"], code, device=f"gate-dev-{i}", platform=platform, version=version)
        detail = a.get("detail") if isinstance(a, dict) else a
        if want == 426:
            ok = st == 426 and isinstance(a, dict) and a.get("min_app_version") == "0.7.2"
        else:
            ok = st == want and (want != 409 or detail == "Canvas exams are not available on this device yet")
        check(f"start of a Canvas exam from {label}: {want}" + (" with the 'not available on this device' message" if want == 409
                                                              else " with min_app_version 0.7.2" if want == 426 else ""),
              ok, f"{st} {detail}")
    tok = S.login(users[len(cases)], "loadtest")
    st, a = S.start_attempt(tok, g["id"], code, device="gate-dev-browser", platform=None)
    check("start of a Canvas exam without any app platform header is refused (not 200)", st != 200, f"{st} {a}")
    tok = S.login(users[len(cases) + 1] if len(users) > len(cases) + 1 else users[0], "loadtest")
    s2, b2 = S.jhttp("POST", f"/api/student/attempts/a_none/canvas-code", {"proof": "x", "ts": 1}, tok,
                     {"X-FairPlay-Platform": "mac", "X-FairPlay-Version": C.APP_VERSION})
    check("the canvas-code endpoint is gone (404 or 405)", s2 in (404, 405), f"{s2}")
    plain = {"title": "Gate plain exam", "duration_min": 30, "mode": "external", "external_url": "https://forms.example.com/f/1",
             "allowed_domains": ["forms.example.com"], "questions": [], "class_id": int(class_id)}
    s, pe = S.jhttp("POST", "/api/exams", plain, ttok)
    s, pop = S.jhttp("POST", f"/api/exams/{pe['id']}/open", None, ttok)
    st, a = S.start_attempt(S.login(users[0], "loadtest"), pe["id"], pop["start_code"], device="gate-dev-win-plain",
                            platform="windows", version="0.1.0")
    check("a plain website exam still starts on windows and on an old app (the gate is for Canvas exams only)", st == 200, f"{st} {a}")
    S.jhttp("POST", f"/api/exams/{pe['id']}/close", None, ttok)
    S.jhttp("POST", f"/api/exams/{g['id']}/close", None, ttok)


def run(pw, ui_browser, T, ttok, shots, engines, hand_in_chromium, hand_in_webkit):
    results = {}
    s, cl = S.jhttp("POST", "/api/classes", {"name": "Restricted browser class"}, ttok)
    assert s == 201, (s, cl)
    class_id = cl["id"]
    users = [f"lt{i:04d}" for i in range(C.BURST_USERS + 1, C.LOADTEST_USERS + 1)]
    s, _ = S.jhttp("POST", f"/api/classes/{class_id}/members", {"usernames": users}, ttok)
    check(f"{len(users)} students added to a separate class for the restricted-browser exams", s in (200, 201), f"{s}")
    r1 = server_side(T, ttok, class_id, shots)

    def new_tab(kind):
        return (lambda: tabs.ChromiumTab(pw)) if kind == "chromium" else (lambda: tabs.WebKitTab())

    # ---- R1: open_book off. Chromium student through the real page + app stand-in; WebKit through the API
    section("8b. Student journey, open_book off (R1)")
    start_code = ui_open_exam(T, r1["id"])
    exam = {"id": r1["id"]}
    ctx, page, msg = start_via_page(ui_browser, "lt0153", "Restricted R1", start_code, "rb-dev-chromium")
    bridge_checks("R1 page", msg, exam, False)
    msg = {**msg, "device": "rb-dev-chromium"}
    if "chromium" in engines:
        tab = tabs.ChromiumTab(pw)
        try:
            section("8c. Journey in Chromium (R1)")
            results["chromium"] = journey("chromium R1", tab, msg, ttok, r1["id"], "lt0153", False,
                                          lambda app: hand_in_chromium(page, msg["attempt_id"]),
                                          tab_factory=new_tab("chromium"))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[chromium R1] journey ran to the end", False, repr(ex))
        finally:
            tab.close()
        tab = tabs.ChromiumTab(pw)
        try:
            warm_sso("chromium", tab, msg)
            start_needs_scope("chromium", tab, msg)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[chromium] warm SSO / legacy checks ran", False, repr(ex))
        finally:
            tab.close()
    if "webkit" in engines:
        wmsg = api_start_msg("lt0154", "loadtest", r1["id"], start_code, "rb-dev-webkit")
        bridge_checks("R1 api", wmsg, exam, False)
        tab = None
        try:
            tab = tabs.WebKitTab()
            section("8d. Journey in WebKit (R1)")
            results["webkit"] = journey("webkit R1", tab, wmsg, ttok, r1["id"], "lt0154", False,
                                        lambda app: hand_in_webkit(wmsg), tab_factory=new_tab("webkit"))
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
            start_needs_scope("webkit", tab, wmsg)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[webkit] warm SSO / legacy checks ran", False, repr(ex))
        finally:
            if tab:
                tab.close()
    fake("POST", "/mode", {"require_login": True, "warm_sso": False})
    ui_close_exam(T, r1["id"])

    # ---- R2: open_book on
    section("8e. Student journey, open_book on (R2)")
    r2 = ui_new_exam(T, class_id, "Restricted R2", open_book=True, shots=shots)
    check("UI: R2 created with the open-book box ticked; sentence names pages, modules and files",
          r2["checked"] and "pages, modules and files" in r2["line"], r2["line"])
    e2 = get_exam(ttok, r2["id"])
    check("R2 API: open_book true, scope course 1 quizzes 1", e2["canvas"]["open_book"] is True and e2["canvas"]["scope"]["item_id"] == "1")
    start2 = ui_open_exam(T, r2["id"])
    ex2 = {"id": r2["id"]}
    ctx2, page2, msg2 = start_via_page(ui_browser, "lt0155", "Restricted R2", start2, "rb-dev-chromium-2")
    bridge_checks("R2 page", msg2, ex2, True)
    msg2 = {**msg2, "device": "rb-dev-chromium-2"}
    if "chromium" in engines:
        tab = tabs.ChromiumTab(pw)
        try:
            section("8f. Journey in Chromium (R2, open book)")
            journey("chromium R2", tab, msg2, ttok, r2["id"], "lt0155", True,
                    lambda app: hand_in_chromium(page2, msg2["attempt_id"]), relaunch=False)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[chromium R2] journey ran to the end", False, repr(ex))
        finally:
            tab.close()
    if "webkit" in engines:
        wmsg2 = api_start_msg("lt0156", "loadtest", ex2["id"], start2, "rb-dev-webkit-2")
        bridge_checks("R2 api", wmsg2, ex2, True)
        tab = None
        try:
            tab = tabs.WebKitTab()
            section("8g. Journey in WebKit (R2, open book)")
            journey("webkit R2", tab, wmsg2, ttok, r2["id"], "lt0156", True, lambda app: hand_in_webkit(wmsg2), relaunch=False)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("[webkit R2] journey ran to the end", False, repr(ex))
        finally:
            if tab:
                tab.close()
    ui_close_exam(T, r2["id"])
    fake("POST", "/mode", {"require_login": False, "warm_sso": False})

    # ---- R4: assignment link (New Quizzes)
    section("8h. Assignment exam (New Quizzes link)")
    body = {"title": "Restricted R4", "duration_min": 30, "mode": "external", "external_url": f"{C.CANVAS}/courses/1/assignments/9",
            "external_kind": "canvas", "allowed_domains": EXTRA_DOMAINS.split("\n"), "questions": [], "class_id": int(class_id),
            "canvas": {"quiz_type": "new"}}
    s, r4 = S.jhttp("POST", "/api/exams", body, ttok)
    check("R4 created: scope is course 1 / assignments / 9, open_book false", s == 201 and r4["canvas"]["scope"] == {
        "course_id": "1", "item_kind": "assignments", "item_id": "9"} and r4["canvas"]["open_book"] is False, f"{s} {str(r4)[:200]}")
    s, op4 = S.jhttp("POST", f"/api/exams/{r4['id']}/open", None, ttok)
    check("R4 opens (nothing is sent to Canvas)", s == 200, f"{s} {str(op4)[:200]}")
    for eng, user in (("chromium", "lt0157"), ("webkit", "lt0158")):
        if eng not in engines:
            continue
        m4 = api_start_msg(user, "loadtest", r4["id"], op4["start_code"], f"rb-dev-{eng}-4")
        sc4 = m4.get("canvas_scope") or {}
        check(f"[{eng} R4] canvas_scope in the attempt is assignments / 9 with the quiz-lti hosts listed",
              {k: sc4.get(k) for k in ("course_id", "item_kind", "item_id", "open_book")} == {
                  "course_id": "1", "item_kind": "assignments", "item_id": "9", "open_book": False}
              and any("quiz-lti-iad-prod" in h for h in sc4.get("quiz_lti_hosts", [])), json.dumps(sc4))
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

    # ---- an exam whose link has no scope cannot be saved
    section("8i. Exam link without a scope")
    body = {"title": "Restricted R3", "duration_min": 30, "mode": "external", "external_url": f"{C.CANVAS}/somewhere",
            "external_kind": "canvas", "allowed_domains": EXTRA_DOMAINS.split("\n"), "questions": [], "class_id": int(class_id),
            "canvas": {"quiz_type": "new", "open_book": True}}
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

    platform_gate(T, ttok, class_id)
    for c in (ctx, ctx2):
        try:
            c.close()
        except Exception:
            pass
    return results

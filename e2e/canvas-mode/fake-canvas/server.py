"""A fake Canvas (Classic Quizzes) for the Canvas mode end-to-end run.

HTTPS on 127.0.0.1:443 with a self-signed certificate, routed by Host header:
  canvas.fake.test  the Canvas site (pages)
  sso.fake.test     an allowed sign-in site
  go.fake.test      sign-in redirect hop
  saml.fake.test    the identity provider
  other.fake.test   a site that is not allowed
An admin interface on plain http://127.0.0.1:9444 lets the test read and reset the state.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

CANVAS_HOST = "canvas.fake.test"
SSO_HOST = "sso.fake.test"
OTHER_HOST = "other.fake.test"
GO_HOST = "go.fake.test"
SAML_HOST = "saml.fake.test"
EXAM_SERVER = "http://127.0.0.1:8765"
ADMIN_PORT = 9444
QUIZ = "/courses/1/quizzes/1"
QUIZ2 = "/courses/1/quizzes/2"

lock = threading.Lock()
state: dict = {}


def reset() -> None:
    with lock:
        state.clear()
        state.update({"sessions": {}, "submitted": 0, "page_hits": [], "require_login": False,
                      "warm_sso": False, "skip_go": False, "downloads": 0, "attachments": 0, "logins": 0})


reset()


def page(title: str, body: str, extra_head: str = "") -> bytes:
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>{extra_head}</head>"
            f"<body>{body}</body></html>").encode()


QUIZ_BODY = """
<h1>Fake Quiz</h1>
<form id="quiz_form" action="/courses/1/quizzes/1/submit" method="post">
  <p id="q1">What is 2+2?</p>
  <label><input type="radio" name="q1" value="3"> 3</label>
  <label><input type="radio" name="q1" value="4"> 4</label>
  <button type="submit" id="submit_quiz" class="btn btn-primary">Submit Quiz</button>
</form>
"""

OPENER_BODY = """
<h1>Course page with a sign-in popup</h1>
<p id="result">none</p>
<button id="open_sso" onclick="openSso()">Sign in</button>
<button id="open_sso_coop" onclick="openSsoCoop()">Sign in (COOP)</button>
<button id="open_other" onclick="openOther()">Other</button>
<script>
  window.__msgs = [];
  window.addEventListener('message', function (e) { window.__msgs.push({o: e.origin, d: e.data}); });
  function openSso() { window.__popup = window.open('https://sso.fake.test/popup', 'sso'); }
  function openSsoCoop() { window.__popup = window.open('https://sso.fake.test/popup-coop', 'ssocoop'); }
  function openOther() { window.__popup = window.open('https://other.fake.test/blocked', 'other'); }
</script>
"""

POPUP_BODY = """
<h1>Sign in</h1>
<p id="state">loading</p>
<script>
  var has = window.opener !== null;
  document.getElementById('state').textContent = 'opener=' + has;
  try { if (window.opener) window.opener.postMessage('opener-alive', '*'); } catch (e) {}
  document.cookie = 'sso_seen=1; Path=/; Secure; SameSite=None';
</script>
"""


SIDE_LINKS = """
<nav id="sidelinks">
  <a id="l_dash" href="/">Dashboard</a>
  <a id="l_course1" href="/courses/1">Course 1</a>
  <a id="l_course2" href="/courses/2">Course 2</a>
  <a id="l_quizlist" href="/courses/1/quizzes">Quizzes</a>
  <a id="l_quiz1" href="/courses/1/quizzes/1">Quiz 1</a>
  <a id="l_quiz2" href="/courses/1/quizzes/2">Quiz 2</a>
  <a id="l_history" href="/courses/1/quizzes/1/history">Quiz 1 history</a>
  <a id="l_sub5" href="/courses/1/quizzes/1/submissions/5">Quiz 1 submission</a>
  <a id="l_files" href="/courses/1/files">Files</a>
  <a id="l_pages" href="/courses/1/pages/p1">Page p1</a>
  <a id="l_modules" href="/courses/1/modules">Modules</a>
  <a id="l_inbox" href="/conversations">Inbox</a>
  <a id="l_calendar" href="/calendar">Calendar</a>
  <a id="l_dots" href="/courses/1/quizzes/1/../2">dots</a>
  <a id="l_enc" href="/courses/1/quizzes/1%2f..%2f2">encoded</a>
  <a id="l_download" href="/courses/1/files/77/download">Download</a>
  <a id="l_file" href="/courses/1/files/77">File 77</a>
  <a id="l_asglist" href="/courses/1/assignments">Assignments</a>
  <a id="l_asg" href="/courses/1/assignments/9">Assignment 9</a>
  <a id="l_asg10" href="/courses/1/assignments/10">Assignment 10</a>
  <a id="l_asg_sub" href="/courses/1/assignments/9/submissions/1">Assignment 9 submission</a>
  <a id="l_asg_done" href="/courses/1/assignments/9/submission-confirmation">Assignment 9 confirmation</a>
  <a id="l_attach" href="/courses/1/files/88">File 88 (sent as attachment)</a>
  <a id="l_rootfile" href="/files/77">Root file 77</a>
  <a id="l_wiki" href="/courses/1/wiki/w1">Wiki</a>
  <a id="l_other" href="https://other.fake.test/blocked">Other site</a>
  <a id="l_examserver" href="http://127.0.0.1:8765/student">Exam server home</a>
  <a id="l_examdone" href="http://127.0.0.1:8765/student/external-done/x?reason=finish_url">Exam server hand-in</a>
  <a id="l_examgo" href="/courses/1/quizzes/1/go-exam">Redirects to the exam server</a>
  <button id="spa_exam" onclick="location.href='http://127.0.0.1:8765/student/external-done/x?reason=finish_url'">Script to the exam server</button>
  <button id="spa_pages" onclick="history.pushState({}, '', '/courses/1/pages/p1'); document.getElementById('route').textContent = location.pathname;">SPA to pages</button>
  <button id="spa_dash" onclick="history.pushState({}, '', '/'); document.getElementById('route').textContent = location.pathname;">SPA to dashboard</button>
  <span id="route"></span>
</nav>
"""

DASHBOARD_BODY = """
<h1>Dashboard</h1>
<div id="cards">
  <a class="card" id="course1" href="/courses/1">Fake Course 1</a>
  <a class="card" id="course2" href="/courses/2">Fake Course 2</a>
</div>
<nav id="global">
  <a id="nav_inbox" href="/conversations">Inbox</a>
  <a id="nav_calendar" href="/calendar">Calendar</a>
  <a id="nav_profile" href="/profile">Account</a>
  <a id="nav_exam" href="http://127.0.0.1:8765/student">Exam server</a>
  <a id="nav_examgo" href="/courses/1/quizzes/1/go-exam">Exam server by redirect</a>
</nav>
<button id="spa_inbox">Inbox (single page)</button>
<button id="spa_replace">Calendar (replace)</button>
<p id="route"></p>
<script>
  function render() { document.getElementById('route').textContent = location.pathname; }
  document.getElementById('spa_inbox').addEventListener('click', function () {
    history.pushState({}, '', '/conversations'); render();
  });
  document.getElementById('spa_replace').addEventListener('click', function () {
    history.replaceState({}, '', '/calendar'); render();
  });
  window.addEventListener('popstate', render);
  render();
</script>
"""

IDP_BODY = """
<h1>Campus single sign-on</h1>
<form id="idp" action="/idp/login" method="post">
  <input name="user" id="idp_user" value="student">
  <input name="pass" id="idp_pass" type="password" value="x">
  <button type="submit" id="idp_signin">Sign in</button>
</form>
"""

SAML_POST = """
<h1>Signing you in</h1>
<form id="saml" action="https://canvas.fake.test/login/saml" method="post">
  <input type="hidden" name="SAMLResponse" value="ok">
  <noscript><button type="submit">Continue</button></noscript>
</form>
<script>document.getElementById('saml').submit();</script>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        return

    def _session(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        sid = c["cs"].value if "cs" in c else None
        new = False
        with lock:
            if sid is None or sid not in state["sessions"]:
                sid = hashlib.sha1(os.urandom(16)).hexdigest()
                state["sessions"][sid] = {}
                new = True
            return sid, state["sessions"][sid], new

    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", headers=None, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"cs={cookie}; Path=/; Secure; HttpOnly; SameSite=Lax")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, to, cookie=None):
        self._send(302, b"", headers={"Location": to}, cookie=cookie)

    def _host(self):
        return (self.headers.get("Host") or "").split(":")[0].lower()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method):
        host, path = self._host(), urlsplit(self.path).path
        with lock:
            state["page_hits"].append(f"{method} {host}{path}")
        if host == SSO_HOST:
            return self._sso(method, path)
        if host == GO_HOST:
            return self._go(method, path)
        if host == SAML_HOST:
            return self._saml(method, path)
        if host == OTHER_HOST:
            return self._send(200, page("blocked", "<h1>Not allowed</h1>"))
        if host != CANVAS_HOST:
            return self._send(404, page("404", "no such host"))
        sid, sess, new = self._session()
        ck = sid if new else None
        if path == QUIZ + "/go-exam":
            return self._redirect(EXAM_SERVER + "/student/external-done/x?reason=finish_url", cookie=ck)
        routed = self._site(method, path, sess, ck)
        if routed:
            return
        if method == "GET" and path == QUIZ:
            body = ("<h1>Fake Quiz</h1><p>Attempts allowed: 1</p>"
                    f'<a class="btn" id="take_quiz" href="{QUIZ}/take">Take the Quiz</a>' + SIDE_LINKS)
            return self._send(200, page("Fake Quiz", body), cookie=ck)
        if path == QUIZ + "/take":
            return self._take(method, sess, ck)
        if method == "POST" and path == QUIZ + "/submit":
            self._body()
            with lock:
                state["submitted"] += 1
            return self._redirect(QUIZ + "/submissions/5", cookie=ck)
        if method == "GET" and path == QUIZ + "/submissions/5":
            return self._send(200, page("Submitted", "<h1>Quiz submitted</h1>"), cookie=ck)
        if method == "GET" and path == "/courses/1/pages/opener":
            return self._send(200, page("Opener", OPENER_BODY), cookie=ck)
        if method == "GET" and path == "/courses/1/assignments/9":
            return self._send(200, page("Assignment", "<h1>An assignment</h1>" + SIDE_LINKS), cookie=ck)
        return self._send(404, page("Not found", "<h1>Not found</h1>"), cookie=ck)

    def _site(self, method, path, sess, ck):
        """Dashboard, courses, sign-in, second quiz and the pages a student could wander to.
        Returns True when it answered."""
        logged = sess.get("user") is not None
        with lock:
            need = state["require_login"]
        if path == "/login" or path.startswith("/login/"):
            if method == "POST" and path == "/login/saml":
                self._body()
                with lock:
                    state["logins"] += 1
                sess["user"] = "student"
                self._redirect("/?login_success=1", cookie=ck)
                return True
            if method == "GET" and path == "/login":
                self._redirect("/login/saml", cookie=ck)
                return True
            if method == "GET" and path == "/login/saml":
                if "assertion=warm" in (self.path.split("?", 1)[1] if "?" in self.path else ""):
                    with lock:
                        state["logins"] += 1
                    sess["user"] = "student"
                    self._redirect("/?login_success=1", cookie=ck)
                    return True
                if logged:
                    self._redirect("/", cookie=ck)
                elif state["skip_go"]:
                    self._redirect(f"https://{SAML_HOST}/idp/sso?SAMLRequest=abc", cookie=ck)
                else:
                    self._redirect(f"https://{GO_HOST}/sso/start?RelayState=%2F", cookie=ck)
                return True
            self._send(200, page("Log in", "<h1>Log in</h1>"), cookie=ck)
            return True
        if need and not logged and method == "GET":
            self._redirect("/login", cookie=ck)
            return True
        if method == "GET" and path == "/":
            self._send(200, page("Dashboard", DASHBOARD_BODY), cookie=ck)
            return True
        simple = {
            "/conversations": "Inbox", "/calendar": "Calendar", "/profile": "Account",
            "/courses/2": "Course 2", "/courses/1/grades": "Grades",
            "/courses/1/modules": "Modules", "/courses/1/quizzes/1/history": "Quiz history", "/courses/1/files": "Files", "/courses/1/files/77": "File 77",
            "/files/77": "File 77", "/courses/1/assignments": "Assignments", "/courses/1/assignments/9": "Assignment 9",
            "/courses/1/assignments/10": "Assignment 10", "/courses/1/assignments/9/submissions/1": "Submission 1",
            "/courses/1/assignments/9/submission-confirmation": "Submitted", "/courses/1/pages/p1": "Page p1", "/courses/1/wiki/w1": "Wiki w1",
        }
        if method == "GET" and path in simple:
            self._send(200, page(simple[path], f"<h1>{simple[path]}</h1>" + SIDE_LINKS), cookie=ck)
            return True
        if method == "GET" and path == "/courses/1":
            self._send(200, page("Course 1", "<h1>Course 1</h1>" + SIDE_LINKS), cookie=ck)
            return True
        if method == "GET" and path == "/courses/1/quizzes":
            self._send(200, page("Quizzes", "<h1>Quizzes</h1>" + SIDE_LINKS), cookie=ck)
            return True
        if method == "GET" and path == "/courses/1/files/88":
            with lock:
                state["attachments"] += 1
            self._send(200, b"<html><body>file as attachment</body></html>", "text/html; charset=utf-8",
                       headers={"Content-Disposition": "attachment; filename=88.html"}, cookie=ck)
            return True
        if method == "GET" and path == "/courses/1/files/77/download":
            with lock:
                state["downloads"] += 1
            self._send(200, b"file bytes", "application/octet-stream",
                       headers={"Content-Disposition": "attachment; filename=77.bin"}, cookie=ck)
            return True
        if path == QUIZ2:
            self._send(200, page("Quiz 2", f'<h1>Quiz 2</h1><a id="take_quiz2" href="{QUIZ2}/take">Take</a>'
                                 + SIDE_LINKS), cookie=ck)
            return True
        if path == QUIZ2 + "/take":
            self._send(200, page("Quiz 2 take", "<h1>Quiz 2 questions</h1>" + SIDE_LINKS), cookie=ck)
            return True
        return False

    def _go(self, method, path):
        if path.startswith("/sso/start"):
            return self._redirect(f"https://{SAML_HOST}/idp/sso?SAMLRequest=abc")
        return self._send(404, page("404", "no"))

    def _saml(self, method, path):
        with lock:
            warm = state["warm_sso"]
        if method == "GET" and path == "/idp/sso":
            if warm:
                return self._redirect(f"https://{CANVAS_HOST}/login/saml?assertion=warm")
            return self._send(200, page("Campus sign-in", IDP_BODY))
        if method == "POST" and path == "/idp/login":
            self._body()
            return self._send(200, page("Signing in", SAML_POST))
        return self._send(404, page("404", "no"))

    def _take(self, method, sess, ck):
        return self._send(200, page("Taking Fake Quiz", QUIZ_BODY + SIDE_LINKS), cookie=ck)

    def _sso(self, method, path):
        if path == "/popup":
            return self._send(200, page("SSO popup", POPUP_BODY))
        if path == "/popup-coop":
            return self._send(200, page("SSO popup COOP", POPUP_BODY),
                              headers={"Cross-Origin-Opener-Policy": "same-origin"})
        if path == "/login":
            return self._send(200, page("SSO", "<h1>Sign in</h1>"))
        return self._send(404, page("404", "no"))


class Admin(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        with lock:
            snap = {k: v for k, v in state.items() if k != "sessions"}
            snap["sessions"] = len(state["sessions"])
        self._json(snap)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/reset":
            reset()
        elif self.path == "/mode":
            with lock:
                for k in ("require_login", "warm_sso", "skip_go"):
                    if k in data:
                        state[k] = bool(data[k])
        elif self.path == "/forget-sessions":
            with lock:
                state["sessions"].clear()
        self._json({"ok": True})


def make_cert(directory: str) -> tuple[str, str]:
    crt, key = os.path.join(directory, "fake2.crt"), os.path.join(directory, "fake2.key")
    if not (os.path.exists(crt) and os.path.exists(key)):
        os.makedirs(directory, exist_ok=True)
        san = ",".join(f"DNS:{h}" for h in (CANVAS_HOST, SSO_HOST, OTHER_HOST, GO_HOST, SAML_HOST))
        san = "subjectAltName=" + san
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                        "-out", crt, "-days", "3", "-subj", f"/CN={CANVAS_HOST}", "-addext", san],
                       check=True, capture_output=True)
    return crt, key


def main() -> None:
    cert_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fake-canvas-cert"
    crt, key = make_cert(cert_dir)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    https = ThreadingHTTPServer(("127.0.0.1", 443), Handler)
    https.daemon_threads = True
    https.socket = ctx.wrap_socket(https.socket, server_side=True)
    admin = ThreadingHTTPServer(("127.0.0.1", ADMIN_PORT), Admin)
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    print("fake canvas ready", flush=True)
    https.serve_forever()


if __name__ == "__main__":
    main()

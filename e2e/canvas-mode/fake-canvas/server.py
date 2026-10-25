"""A fake Canvas (Classic Quizzes) for the Canvas mode end-to-end run.

HTTPS on 127.0.0.1:443 with a self-signed certificate, routed by Host header:
  canvas.fake.test  the Canvas site (pages and REST API)
  sso.fake.test     an allowed sign-in site
  other.fake.test   a site that is not allowed
An admin interface on plain http://127.0.0.1:9444 lets the test read and reset the state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import ssl
import subprocess
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

CANVAS_HOST = "canvas.fake.test"
SSO_HOST = "sso.fake.test"
OTHER_HOST = "other.fake.test"
TOKEN = "fake-canvas-teacher-token-0001"
ADMIN_PORT = 9444
QUIZ = "/courses/1/quizzes/1"

lock = threading.Lock()
state: dict = {}


def reset() -> None:
    with lock:
        state.clear()
        state.update({"code": "", "sessions": {}, "wrong": 0, "accepted": 0, "submitted": 0,
                      "api_calls": [], "page_hits": [], "typed_on_decoy": 0, "set_calls": 0})


reset()


def page(title: str, body: str, extra_head: str = "") -> bytes:
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>{extra_head}</head>"
            f"<body>{body}</body></html>").encode()


ACCESS_FORM = """
<h1>Quiz access</h1>
%(error)s
<form class="access_code_form" id="access_code_form" action="/courses/1/quizzes/1/take" method="post" accept-charset="UTF-8">
  <input type="hidden" name="authenticity_token" value="tok">
  <label for="quiz_access_code">Access code</label>
  <input type="password" name="access_code" id="quiz_access_code" autocomplete="off">
  <button type="submit" class="btn btn-primary" id="submit_access" disabled>Submit</button>
</form>
<script>
  var i = document.getElementById('quiz_access_code'), b = document.getElementById('submit_access');
  i.addEventListener('input', function () { b.disabled = i.value.length === 0; });
</script>
"""

QUIZ_BODY = """
<h1>Fake Quiz</h1>
<form id="quiz_form" action="/courses/1/quizzes/1/submit" method="post">
  <p id="q1">What is 2+2?</p>
  <label><input type="radio" name="q1" value="3"> 3</label>
  <label><input type="radio" name="q1" value="4"> 4</label>
  <button type="submit" id="submit_quiz" class="btn btn-primary">Submit Quiz</button>
</form>
"""

DECOY_BODY = """
<h1>A page about something else</h1>
<form class="access_code_form" action="/courses/1/pages/decoy" method="post">
  <input type="password" name="access_code" id="quiz_access_code">
  <button type="submit" class="btn btn-primary" id="decoy_submit" disabled>Submit</button>
</form>
<script>
  window.__decoySubmitted = false;
  document.querySelector('form').addEventListener('submit', function () { window.__decoySubmitted = true; });
</script>
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
<input type="password" id="quiz_access_code" name="access_code">
<p id="state">loading</p>
<script>
  var has = window.opener !== null;
  document.getElementById('state').textContent = 'opener=' + has;
  try { if (window.opener) window.opener.postMessage('opener-alive', '*'); } catch (e) {}
  document.cookie = 'sso_seen=1; Path=/; Secure; SameSite=None';
</script>
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
                state["sessions"][sid] = {"accepted": False}
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
        if host == OTHER_HOST:
            return self._send(200, page("blocked", "<h1>Not allowed</h1><input id=quiz_access_code>"))
        if host != CANVAS_HOST:
            return self._send(404, page("404", "no such host"))
        if path.startswith("/api/v1/"):
            return self._api(method, path)
        sid, sess, new = self._session()
        ck = sid if new else None
        if method == "GET" and path == QUIZ:
            body = ("<h1>Fake Quiz</h1><p>Attempts allowed: 1</p>"
                    f'<a class="btn" id="take_quiz" href="{QUIZ}/take">Take the Quiz</a>')
            return self._send(200, page("Fake Quiz", body), cookie=ck)
        if path == QUIZ + "/take":
            return self._take(method, sess, ck)
        if method == "POST" and path == QUIZ + "/submit":
            self._body()
            if not self._can_take(sess):
                return self._send(403, page("Forbidden", "Access code required"), cookie=ck)
            with lock:
                state["submitted"] += 1
            return self._redirect(QUIZ + "/submissions/5", cookie=ck)
        if method == "GET" and path == QUIZ + "/submissions/5":
            return self._send(200, page("Submitted", "<h1>Quiz submitted</h1>"), cookie=ck)
        if method == "GET" and path == "/courses/1/pages/decoy":
            return self._send(200, page("Decoy", DECOY_BODY), cookie=ck)
        if method == "POST" and path == "/courses/1/pages/decoy":
            self._body()
            with lock:
                state["typed_on_decoy"] += 1
            return self._send(200, page("Decoy", "<h1>posted</h1>"), cookie=ck)
        if method == "GET" and path == "/courses/1/pages/opener":
            return self._send(200, page("Opener", OPENER_BODY), cookie=ck)
        if method == "GET" and path == "/courses/1/assignments/9":
            return self._send(200, page("Assignment", "<h1>An assignment</h1><input id=quiz_access_code>"), cookie=ck)
        return self._send(404, page("Not found", "<h1>Not found</h1>"), cookie=ck)

    def _can_take(self, sess):
        with lock:
            return (not state["code"]) or sess["accepted"]

    def _take(self, method, sess, ck):
        if method == "POST":
            form = parse_qs(self._body().decode("utf-8", "replace"), keep_blank_values=True)
            given = (form.get("access_code") or [""])[0]
            with lock:
                code = state["code"]
                ok = bool(code) and hmac.compare_digest(given.encode(), code.encode())
                if ok:
                    sess["accepted"] = True
                    state["accepted"] += 1
                else:
                    state["wrong"] += 1
            if ok:
                return self._redirect(QUIZ + "/take", cookie=ck)
            err = '<p class="error" id="bad_code">Invalid access code.</p>'
            return self._send(200, page("Access code", ACCESS_FORM % {"error": err}), cookie=ck)
        if self._can_take(sess):
            return self._send(200, page("Taking Fake Quiz", QUIZ_BODY), cookie=ck)
        return self._send(200, page("Access code", ACCESS_FORM % {"error": ""}), cookie=ck)

    def _sso(self, method, path):
        if path == "/popup":
            return self._send(200, page("SSO popup", POPUP_BODY))
        if path == "/popup-coop":
            return self._send(200, page("SSO popup COOP", POPUP_BODY),
                              headers={"Cross-Origin-Opener-Policy": "same-origin"})
        if path == "/login":
            return self._send(200, page("SSO", "<h1>Sign in</h1><input id=quiz_access_code>"))
        return self._send(404, page("404", "no"))

    def _api(self, method, path):
        body = self._body()
        auth = self.headers.get("Authorization", "")
        with lock:
            state["api_calls"].append({"method": method, "path": path, "auth_ok": auth == f"Bearer {TOKEN}"})
        if auth != f"Bearer {TOKEN}":
            return self._send(401, b'{"errors":[{"message":"Invalid access token."}]}', "application/json")
        if path == "/api/v1/users/self":
            return self._send(200, json.dumps({"id": 7, "name": "Fake Teacher", "login_id": "fteacher"}).encode(),
                              "application/json")
        if path == "/api/v1/courses" and method == "GET":
            return self._send(200, json.dumps([{"id": 1, "name": "Fake Course", "course_code": "FAKE101"}]).encode(),
                              "application/json")
        if path.startswith("/api/v1/courses/1/users"):
            return self._send(200, b"[]", "application/json")
        if method == "PUT" and path == "/api/v1/courses/1/quizzes/1":
            form = parse_qs(body.decode(), keep_blank_values=True)
            if "quiz[access_code]" not in form:
                return self._send(400, b'{"errors":[]}', "application/json")
            with lock:
                state["code"] = form["quiz[access_code]"][0]
                state["set_calls"] += 1
                for s in state["sessions"].values():
                    s["accepted"] = False
            return self._send(200, b'{"id":1}', "application/json")
        return self._send(404, b'{"errors":[{"message":"no route"}]}', "application/json")


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
        elif self.path == "/code":
            with lock:
                state["code"] = data.get("code", "")
                for s in state["sessions"].values():
                    s["accepted"] = False
        elif self.path == "/forget-sessions":
            with lock:
                state["sessions"].clear()
        self._json({"ok": True})


def make_cert(directory: str) -> tuple[str, str]:
    crt, key = os.path.join(directory, "fake.crt"), os.path.join(directory, "fake.key")
    if not (os.path.exists(crt) and os.path.exists(key)):
        os.makedirs(directory, exist_ok=True)
        san = f"subjectAltName=DNS:{CANVAS_HOST},DNS:{SSO_HOST},DNS:{OTHER_HOST}"
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

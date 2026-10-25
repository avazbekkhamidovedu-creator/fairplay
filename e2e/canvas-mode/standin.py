"""The native app's Canvas behaviour, ported from mac/Sources/Proctora (CanvasMode.swift and the
Canvas parts of ExternalExamController.swift), driving a real web page.

The probe and fill JavaScript is NOT re-typed: it is read out of CanvasMode.swift at run time and
the Swift string interpolation is applied here, so the exact text the app injects runs in the page.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

import config as C

SWIFT = os.path.join(C.REPO, "mac", "Sources", "Proctora", "CanvasMode.swift")


# ---------------------------------------------------------------- scripts out of the Swift file
def _swift_source() -> str:
    with open(SWIFT, encoding="utf-8") as f:
        return f.read()


def _dedent_swift(block: str, closing_indent: str) -> str:
    out = []
    for line in block.split("\n"):
        out.append(line[len(closing_indent):] if line.startswith(closing_indent) else line.lstrip())
    return "\n".join(out)


def _multiline(src: str, header: str) -> str:
    i = src.index(header)
    start = src.index('"""\n', i) + 4
    end = src.index('"""', start)
    body = src[start:end]
    closing_indent = body.rsplit("\n", 1)[1] if "\n" in body else ""
    return _dedent_swift(body.rsplit("\n", 1)[0], closing_indent)


class Scripts:
    def __init__(self):
        src = _swift_source()
        self.field = re.search(r'static let field = "([^"]+)"', src).group(1)
        probe = re.search(r'static let probe = "(.*)"\n', src).group(1)
        self.probe = probe.replace(r"\(field)", self.field)
        self._fill = _multiline(src, "static func fill(code: String, origin: String)")
        self.popup_guard = _multiline(src, "static let popupGuard")
        assert r"\(" in self._fill and r"\(field)" in self._fill
        assert "'" not in self.field or True

    @staticmethod
    def js_string(s: str) -> str:
        text = json.dumps(s, ensure_ascii=False).replace("/", "\\/")
        return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    def fill(self, code: str, origin: str) -> str:
        out = (self._fill.replace(r"\(field)", self.field)
               .replace(r"\(jsString(origin))", self.js_string(origin))
               .replace(r"\(jsString(code))", self.js_string(code)))
        assert r"\(" not in out, "unreplaced Swift interpolation"
        return out


SCRIPTS = Scripts()


# ---------------------------------------------------------------- CanvasPolicy, SiteRule, CanvasFinish
class SiteRule:
    def __init__(self, raw: str):
        s = raw.strip()
        if "://" in s:
            s = s.split("://", 1)[1]
        for ch in "?#":
            if ch in s:
                s = s[: s.index(ch)]
        host, path = s, ""
        if "/" in s:
            i = s.index("/")
            host, path = s[:i], s[i:]
        self.wildcard = host.startswith("*.")
        if self.wildcard:
            host = host[2:]
        if ":" in host:
            host = host[: host.index(":")]
        host = host.lower().strip(".")
        if not host or "*" in host:
            raise ValueError(raw)
        self.host = host
        self.path_prefix = "" if path == "/" else path

    def matches(self, h: str, p: str) -> bool:
        if not (h == self.host or (self.wildcard and h.endswith("." + self.host))):
            return False
        if not self.path_prefix:
            return True
        path = p or "/"
        if self.path_prefix.endswith("/"):
            return path.startswith(self.path_prefix)
        return path == self.path_prefix or path.startswith(self.path_prefix + "/")


def _parts(url: str):
    try:
        u = urlsplit(url)
        return u.scheme.lower(), u.username, u.password, (u.hostname or "").lower(), u.port, unquote(u.path)
    except ValueError:
        return None


def policy_allows(url: str, rules: list[SiteRule]) -> bool:
    p = _parts(url)
    if not p:
        return False
    scheme, user, pw, host, port, path = p
    if scheme != "https" or user is not None or pw is not None or not host:
        return False
    if port is not None and port != 443:
        return False
    return any(r.matches(host, path) for r in rules)


def _fill_host(url: str):
    p = _parts(url)
    if not p:
        return None
    scheme, user, pw, host, port, _ = p
    if scheme != "https" or user is not None or pw is not None or not host:
        return None
    if port is not None and port != 443:
        return None
    return host


def is_quiz_path(path: str) -> bool:
    return re.match(r"^/courses/\d+/(quizzes|assignments)/\d+", path) is not None


def fill_origin(page: str, start: str):
    ph, sh = _fill_host(page), _fill_host(start)
    if not ph or not sh or ph != sh:
        return None
    if not is_quiz_path(unquote(urlsplit(page).path)):
        return None
    return "https://" + ph


def is_blank(url: str | None) -> bool:
    return not url or url.lower() == "about:blank"


def popup_plan(url, opener_host, rules, opener_survives_cross_origin: bool) -> str:
    if not url or is_blank(url):
        return "child"
    if not policy_allows(url, rules):
        return "block"
    host = (urlsplit(url).hostname or "").lower()
    if host == (opener_host or "").lower() or opener_survives_cross_origin:
        return "child"
    return "sameView"


def finish_text(url: str) -> str:
    u = urlsplit(url)
    return unquote(u.path) + ("?" + u.query if u.query else "")


def finish_matches(url: str, regex: str | None) -> bool:
    if not regex:
        return False
    text = finish_text(url)[:512]
    return re.search(regex, text) is not None


def is_safe_pattern(pattern: str) -> bool:
    """CanvasFinish.isSafe, line for line."""
    if len(pattern) > 300:
        return False
    if "(?<=" in pattern or "(?<!" in pattern:
        return False
    chars = list(pattern)
    stack: list[dict] = []
    in_class = False
    i = 0
    while i < len(chars):
        c = chars[i]
        if c == "\\":
            if i + 1 < len(chars):
                n = chars[i + 1]
                if ("1" <= n <= "9") or n == "k":
                    return False
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            i += 1
            continue
        if c == "[":
            in_class = True
        elif c == "(":
            if stack:
                stack[-1]["nested"] = True
            stack.append({"alt": False, "quant": False, "nested": False})
        elif c == ")":
            if not stack:
                return False
            g = stack.pop()
            nxt = chars[i + 1] if i + 1 < len(chars) else None
            repeated = nxt in ("+", "*", "{")
            if repeated and (g["alt"] or g["quant"] or g["nested"]):
                return False
            if stack:
                if g["alt"]:
                    stack[-1]["alt"] = True
                if g["quant"] or repeated:
                    stack[-1]["quant"] = True
        elif c == "|":
            if stack:
                stack[-1]["alt"] = True
        elif c in "+*{":
            if stack:
                stack[-1]["quant"] = True
        i += 1
    return not stack


class FillBudget:
    MAX = 3

    def __init__(self):
        self.used = 0

    @property
    def exhausted(self):
        return self.used >= self.MAX

    def take(self):
        if self.exhausted:
            return False
        self.used += 1
        return True


def retry_delay(retry_after) -> float:
    try:
        n = int(str(retry_after).strip())
    except (TypeError, ValueError):
        return 4.0
    return float(min(20, max(1, n)))


def parse_code(status, js) -> str | None:
    if status != 200 or not isinstance(js, dict):
        return None
    code = js.get("code")
    if not isinstance(code, str) or not (4 <= len(code) <= 64):
        return None
    if not all(ord(ch) >= 32 and ord(ch) != 127 for ch in code):
        return None
    return code


# ---------------------------------------------------------------- HTTP to the FairPlay server
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, path: str, body=None, token=None, headers=None, raw: bytes | None = None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(C.SERVER + path, method=method, headers=h, data=data)
    try:
        with _opener.open(req, timeout=60) as r:
            t = r.read()
            return r.status, {k.title(): v for k, v in r.headers.items()}, t
    except urllib.error.HTTPError as e:
        return e.code, {k.title(): v for k, v in e.headers.items()}, e.read()


def jhttp(method, path, body=None, token=None, headers=None):
    s, h, t = http(method, path, body, token, headers)
    try:
        return s, json.loads(t) if t else None
    except ValueError:
        return s, t.decode("utf-8", "replace")


def sign(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


_ts = [0]


def fresh_ts() -> int:
    _ts[0] = max(_ts[0] + 1, int(time.time() * 1000))
    return _ts[0]


def login(user: str, password: str | None = None) -> str:
    s, j = jhttp("POST", "/api/auth/login", {"username": user, "password": password or f"{user}123"})
    assert s == 200, (user, s, j)
    return j["token"]


def start_attempt(token: str, exam_id: str, start_code: str, device: str = C.DEVICE, key: str = C.APP_KEY,
                  platform: str | None = "mac", signature_override: str | None = None):
    """What the Mac app does to start: challenge, signed nonce, POST start."""
    s, ch = jhttp("GET", f"/api/student/exams/{exam_id}/challenge", token=token)
    assert s == 200, ch
    nonce = ch["nonce"]
    client = {"device_id": device, "nonce": nonce,
              "signature": signature_override or sign(key, f"start|{nonce}|{device}"), "version": "0.7.1"}
    h = {"X-FairPlay-Platform": platform} if platform else {}
    return jhttp("POST", f"/api/student/exams/{exam_id}/start", {"start_code": start_code, "client": client},
                 token, h)


class NativeStandIn:
    """The app's session for one attempt: token, app session, device and key as the bridge message gave them."""

    def __init__(self, open_msg: dict, device: str | None = None, key: str = C.APP_KEY):
        self.aid = open_msg["attempt_id"]
        self.token = open_msg["token"]
        self.session = open_msg["session"]
        self.start_url = open_msg["url"]
        self.rules = [SiteRule(d) for d in open_msg["allowed_domains"]]
        start_host = (urlsplit(self.start_url).hostname or "").lower()
        if not any((not r.wildcard) and r.host == start_host and not r.path_prefix for r in self.rules):
            self.rules.append(SiteRule(start_host))
        self.finish_regex = open_msg.get("finish_url_regex")
        self.device, self.key = device or open_msg.get("device") or C.DEVICE, key
        self.budget = FillBudget()
        self.help_shown = False
        self.log: list[str] = []
        self.codes_seen: list[str] = []
        self.issue_calls = 0

    def code_body(self, ts=None, device=None, key=None, session=None):
        ts = ts if ts is not None else fresh_ts()
        msg = f"canvascode|{self.aid}|{session or self.session}|{device or self.device}|{ts}"
        return {"proof": sign(key or self.key, msg), "ts": ts}

    def request_code(self, body=None, headers=None):
        self.issue_calls += 1
        h = {"X-FairPlay-Session": self.session, "X-FairPlay-Platform": "mac", **(headers or {})}
        return http("POST", f"/api/student/attempts/{self.aid}/canvas-code",
                    body if body is not None else self.code_body(), self.token, h)

    def fetch_code(self):
        s, h, t = self.request_code()
        try:
            js = json.loads(t)
        except ValueError:
            js = None
        code = parse_code(s, js)
        if code:
            self.codes_seen.append(code)
        return code, s, h.get("Retry-After")

    def report(self, typ: str, detail: str) -> None:
        http("POST", f"/api/student/attempts/{self.aid}/events",
             {"events": [{"type": typ, "detail": detail}]}, self.token,
             {"X-FairPlay-Session": self.session})

    def failed(self, reason: str) -> None:
        self.log.append(f"canvas_code_failed:{reason}")
        self.report("canvas_code_failed", reason)
        self.help_shown = True

    def on_loaded(self, page, sleep=time.sleep) -> str:
        """canvasPageLoaded + canvasProbed + requestCanvasCode for one finished main-frame load."""
        url = page.url
        if fill_origin(url, self.start_url) is None:
            return "gate"
        if page.eval(SCRIPTS.probe) is not True:
            return "nofield"
        if self.budget.exhausted:
            self.failed("fill limit reached")
            return "help"
        retries = 0
        while True:
            code, status, retry_after = self.fetch_code()
            if code:
                break
            if status == 429 and retries < 3:
                sleep(retry_delay(retry_after))
                retries += 1
                if fill_origin(page.url, self.start_url) is None:
                    return "gate"
                continue
            self.failed("code unavailable")
            return "help"
        origin = fill_origin(page.url, self.start_url)
        if origin is None or not self.budget.take():
            return "gate"
        self.log.append(f"fill {self.budget.used}")
        outcome = page.eval(SCRIPTS.fill(code, origin))
        if outcome != "clicked":
            self.failed(f"fill {outcome}")
            return "help"
        return "clicked"

    def finish_hit(self, url: str) -> bool:
        return finish_matches(url, self.finish_regex)

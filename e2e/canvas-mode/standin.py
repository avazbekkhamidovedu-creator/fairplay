"""The native app's Canvas behaviour, ported from mac/Sources/Proctora (CanvasMode.swift and the
Canvas parts of ExternalExamController.swift), driving a real web page.
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

import canvas_nav as N
import config as C

SWIFT = os.path.join(C.REPO, "mac", "Sources", "Proctora", "CanvasMode.swift")


SWIFT = os.path.join(C.REPO, "mac", "Sources", "Proctora", "CanvasMode.swift")


def _multiline(src: str, header: str) -> str:
    """The text of the Swift multi-line string literal that follows `header`, dedented by its closing quotes."""
    i = src.index(header)
    start = src.index('"""\n', i) + 4
    end = src.index('"""', start)
    body = src[start:end]
    indent = body.rsplit("\n", 1)[1] if "\n" in body else ""
    lines = body.rsplit("\n", 1)[0].split("\n")
    return "\n".join(line[len(indent):] if line.startswith(indent) else line.lstrip() for line in lines)


class Scripts:
    def __init__(self):
        with open(SWIFT, encoding="utf-8") as f:
            self.popup_guard = _multiline(f.read(), "static let popupGuard")


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
    """CanvasFinish.matches reads URL.path: decoded, never the query or the fragment."""
    return unquote(urlsplit(url).path)


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
                  platform: str | None = "mac", signature_override: str | None = None,
                  version: str | None = C.APP_VERSION):
    """What the Mac app does to start: challenge, signed nonce, POST start."""
    s, ch = jhttp("GET", f"/api/student/exams/{exam_id}/challenge", token=token)
    assert s == 200, ch
    nonce = ch["nonce"]
    client = {"device_id": device, "nonce": nonce,
              "signature": signature_override or sign(key, f"start|{nonce}|{device}"), "version": version or C.APP_VERSION}
    h = {"X-FairPlay-Platform": platform} if platform else {}
    if version and platform:
        h["X-FairPlay-Version"] = version
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
        self.scope = N.scope_from_json(open_msg.get("canvas_scope"))
        self.device, self.key = device or open_msg.get("device") or C.DEVICE, key
        self.log: list[str] = []

    def report(self, typ: str, detail: str) -> None:
        http("POST", f"/api/student/attempts/{self.aid}/events",
             {"events": [{"type": typ, "detail": detail}]}, self.token,
             {"X-FairPlay-Session": self.session, "X-FairPlay-Platform": "mac", "X-FairPlay-Version": C.APP_VERSION})

    def finish_hit(self, url: str) -> bool:
        return finish_matches(url, self.finish_regex)

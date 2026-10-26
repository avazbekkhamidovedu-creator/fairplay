"""The restricted Canvas browser, ported from mac/Sources/Proctora/CanvasMode.swift
(CanvasScope, CanvasNavigation, CanvasRecoveryGate, CanvasQuizMarker) and the exam-server test of
MainWindowController.

Pure functions on URL strings; no browser. `path` is always the percent-encoded path of the URL as
URLComponents.percentEncodedPath gives it (query and fragment never part of it).
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

ALWAYS_BLOCKED = {"conversations", "inbox", "profile", "groups", "calendar", "accounts", "api", "users"}
OPEN_BOOK_ROOTS = {"pages", "modules", "files", "wiki"}
MAX_ID_LENGTH = 20
LOBBY, QUIZ = "lobby", "quiz"
ALLOW, BLOCK = "allow", "block"


@dataclass(frozen=True)
class Scope:
    course_id: str
    item_kind: str
    item_id: str
    open_book: bool = False
    sign_in_hosts: tuple = ()
    quiz_lti_hosts: tuple = ()


MAX_HOST_PATTERNS = 50


def is_host_pattern(text: str) -> bool:
    """CanvasScope.isHostPattern: `host` or `*.suffix`, lower-case letters, digits, dot, dash, underscore."""
    body = text[2:] if text.startswith("*.") else text
    if not body or len(body) > 253 or body.startswith(".") or body.endswith(".") or ".." in body:
        return False
    return all(("a" <= c <= "z") or ("0" <= c <= "9") or c in ".-_" for c in body)


def host_list(value):
    """CanvasScope.hostList: None when present but not a list of valid patterns; absent or null is empty."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_HOST_PATTERNS:
        return None
    out = []
    for item in value:
        if not isinstance(item, str):
            return None
        low = item.lower()
        if not is_host_pattern(low):
            return None
        out.append(low)
    return tuple(out)


def host_matches(host: str, patterns) -> bool:
    h = host.lower()
    for p in patterns:
        if p.startswith("*."):
            if h.endswith("." + p[2:]):
                return True
        elif h == p:
            return True
    return False


def is_id(text: str) -> bool:
    return bool(text) and len(text) <= MAX_ID_LENGTH and all(0x30 <= ord(c) <= 0x39 for c in text)


def scope_from_json(obj) -> Scope | None:
    """CanvasScope.init?(json:). None when absent or malformed."""
    if not isinstance(obj, dict):
        return None

    def text(key):
        v = obj.get(key)
        if isinstance(v, bool):
            return None
        if isinstance(v, str):
            return v
        if isinstance(v, int):
            return str(v)
        return None

    course, item = text("course_id"), text("item_id")
    kind = obj.get("item_kind")
    if course is None or not is_id(course) or item is None or not is_id(item):
        return None
    if kind not in ("quizzes", "assignments"):
        return None
    sign_in, lti = host_list(obj.get("sign_in_hosts")), host_list(obj.get("quiz_lti_hosts"))
    if sign_in is None or lti is None:
        return None
    return Scope(course, kind, item, obj.get("open_book") is True, sign_in, lti)


def segments_of_path(path: str) -> list[str] | None:
    """CanvasNavigation.segments(encodedPath:). None when the path is not safe to judge."""
    if path == "":
        return []
    if not path.startswith("/"):
        return None
    lower = path.lower()
    for bad in ("%2f", "%5c", "%2e", "%00", "\\"):
        if bad in lower:
            return None
    parts = path[1:].split("/")
    if parts and parts[-1] == "":
        parts.pop()
    for part in parts:
        if part == "" or part == "." or part == "..":
            return None
    return parts


def _split(url: str):
    try:
        return urlsplit(url)
    except ValueError:
        return None


def segments(url: str) -> list[str] | None:
    u = _split(url)
    if u is None:
        return None
    return segments_of_path(u.path)


def host_of(url: str) -> str | None:
    u = _split(url)
    if u is None:
        return None
    try:
        return u.hostname
    except ValueError:
        return None


def on_host(url: str, exam_host: str) -> bool:
    h = host_of(url)
    return h is not None and h.lower() == exam_host.lower()


def in_course(segs, scope: Scope) -> bool:
    return len(segs) >= 2 and segs[0] == "courses" and segs[1] == scope.course_id


def is_under_item_segs(segs, scope: Scope) -> bool:
    return in_course(segs, scope) and len(segs) >= 4 and segs[2] == scope.item_kind and segs[3] == scope.item_id


def is_under_item(url: str, scope: Scope) -> bool:
    segs = segments(url)
    return segs is not None and is_under_item_segs(segs, scope)


def _is_download(segs) -> bool:
    return any(s.lower() == "download" for s in segs)


def decide_segments(segs, scope: Scope, phase: str) -> str:
    if segs and segs[0].lower() in ALWAYS_BLOCKED:
        return BLOCK
    if len(segs) >= 2 and segs[0].lower() == "courses" and segs[1] != scope.course_id:
        return BLOCK
    if is_under_item_segs(segs, scope):
        return ALLOW
    if phase == LOBBY:
        if not segs:
            return ALLOW
        if segs[0] == "login":
            return ALLOW
        if in_course(segs, scope):
            if len(segs) == 2:
                return ALLOW
            if len(segs) == 3 and segs[2] in ("quizzes", "assignments"):
                return ALLOW
        return BLOCK
    if segs and segs[0] == "login":
        return ALLOW
    if not scope.open_book:
        return BLOCK
    if in_course(segs, scope) and len(segs) >= 3 and segs[2] in OPEN_BOOK_ROOTS:
        return BLOCK if _is_download(segs) else ALLOW
    if segs and segs[0] == "files":
        return BLOCK if _is_download(segs) else ALLOW
    return BLOCK


DOWNLOAD_QUERY_NAMES = {"download", "download_frd"}


def has_download_query(url: str) -> bool:
    """URLComponents.queryItems: split on &, name before the first =, percent-decoded (no + handling)."""
    u = _split(url)
    if u is None or not u.query:
        return False
    for item in u.query.split("&"):
        name = unquote(item.split("=", 1)[0])
        if name.lower() in DOWNLOAD_QUERY_NAMES:
            return True
    return False


def decide(url: str, exam_host: str, scope: Scope, phase: str) -> str:
    """CanvasNavigation.decide: other hosts follow the site rules in the lobby; in the quiz phase a main-frame
    page on another host opens only over https for a host in the scope's sign-in or quiz-lti lists."""
    if not on_host(url, exam_host):
        if phase != QUIZ:
            return ALLOW
        u = _split(url)
        host = host_of(url)
        if u is None or u.scheme.lower() != "https" or not host:
            return BLOCK
        listed = host_matches(host, scope.sign_in_hosts) or host_matches(host, scope.quiz_lti_hosts)
        return ALLOW if listed else BLOCK
    segs = segments(url)
    if segs is None:
        return BLOCK
    if has_download_query(url):
        return BLOCK
    return decide_segments(segs, scope, phase)


def enters_quiz(url: str, exam_host: str, scope: Scope) -> bool:
    if not on_host(url, exam_host):
        return False
    segs = segments(url)
    if segs is None or not is_under_item_segs(segs, scope):
        return False
    if scope.item_kind == "assignments":
        return True
    return len(segs) >= 5 and segs[4] == "take"


def is_item_landing(url: str, exam_host: str, scope: Scope) -> bool:
    """The exact item page /courses/C/K/I (a trailing slash is fine)."""
    if not on_host(url, exam_host):
        return False
    segs = segments(url)
    return segs is not None and len(segs) == 4 and is_under_item_segs(segs, scope)


def is_landing(url: str, exam_host: str, scope: Scope) -> bool:
    if not on_host(url, exam_host):
        return False
    segs = segments(url)
    if segs is None:
        return False
    return not segs or (len(segs) == 2 and segs[0] == "courses" and segs[1] == scope.course_id)


def is_sign_in(url: str, exam_host: str) -> bool:
    if not on_host(url, exam_host):
        return True
    segs = segments(url)
    return bool(segs) and segs[0] == "login"


def block_detail(url: str) -> str:
    u = _split(url)
    return "canvas page " + (u.path if u else "")[:120]


class RecoveryGate:
    MIN_INTERVAL = 1.0
    MAX_REMEMBERED = 200

    def __init__(self):
        self.last: float | None = None
        self.seen: set[str] = set()

    def should_recover(self, now: float) -> bool:
        if self.last is not None and now - self.last < self.MIN_INTERVAL:
            return False
        self.last = now
        return True

    def first_time(self, key: str) -> bool:
        if len(self.seen) >= self.MAX_REMEMBERED:
            self.seen.clear()
        if key in self.seen:
            return False
        self.seen.add(key)
        return True


def is_server_url(url: str, server: str) -> bool:
    """MainWindowController.isServerURL: same scheme, host (any case) and port, default ports filled in."""
    u, sv = _split(url), _split(server)
    if u is None or sv is None or not u.hostname:
        return False

    def port(x):
        try:
            return x.port or (443 if x.scheme.lower() == "https" else 80)
        except ValueError:
            return -1
    return (u.hostname.lower() == (sv.hostname or "").lower() and port(u) == port(sv)
            and u.scheme.lower() == sv.scheme.lower())


class QuizMarker:
    """CanvasQuizMarker: the per-attempt flag in UserDefaults that survives a relaunch."""

    def __init__(self, defaults: dict | None = None):
        self.defaults = {} if defaults is None else defaults

    @staticmethod
    def key(attempt_id: str) -> str:
        return "fairplay.canvasQuizStarted." + attempt_id

    def is_set(self, attempt_id: str) -> bool:
        return bool(self.defaults.get(self.key(attempt_id)))

    def set(self, attempt_id: str) -> None:
        self.defaults[self.key(attempt_id)] = True

    @staticmethod
    def armed_key(attempt_id: str) -> str:
        return "fairplay.canvasFinishArmed." + attempt_id

    def is_armed(self, attempt_id: str) -> bool:
        return bool(self.defaults.get(self.armed_key(attempt_id)))

    def set_armed(self, attempt_id: str, armed: bool) -> None:
        self.defaults[self.armed_key(attempt_id)] = armed

    def clear(self, attempt_id: str) -> None:
        self.defaults.pop(self.key(attempt_id), None)
        self.defaults.pop(self.armed_key(attempt_id), None)

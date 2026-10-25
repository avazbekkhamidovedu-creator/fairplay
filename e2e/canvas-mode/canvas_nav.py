"""The restricted Canvas browser, ported from mac/Sources/Proctora/CanvasMode.swift
(CanvasScope, CanvasNavigation, CanvasRecoveryGate and the scoped CanvasPolicy.fillOrigin).

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
    return Scope(course, kind, item, obj.get("open_book") is True)


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
    if not on_host(url, exam_host):
        return ALLOW
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


def _fill_host(url: str):
    u = _split(url)
    if u is None:
        return None
    try:
        host, port = u.hostname, u.port
    except ValueError:
        return None
    if u.scheme.lower() != "https" or u.username is not None or u.password is not None or not host:
        return None
    if port is not None and port != 443:
        return None
    return host.lower()


def is_quiz_path(path: str) -> bool:
    import re
    return re.match(r"^/courses/\d+/(quizzes|assignments)/\d+", path) is not None


def fill_origin(page: str, start: str, scope: Scope | None = None):
    """CanvasPolicy.fillOrigin(page:start:scope:)."""
    ph, sh = _fill_host(page), _fill_host(start)
    if not ph or not sh or ph != sh:
        return None
    if scope is not None:
        if not is_under_item(page, scope):
            return None
    else:
        u = _split(page)
        if not is_quiz_path(unquote(u.path)):
            return None
    return "https://" + ph

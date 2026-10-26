"""Canvas mode (Mode B): a website exam on a Canvas quiz or assignment. The student signs in to
Canvas inside the app and can reach only the exam's course and item; there is no access code.
Contract: docs/contract-canvas-mode-b-2026-10-25.md."""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException

log = logging.getLogger("proctora.canvas")

KIND = "canvas"
QUIZ_TYPES = ("classic", "new", "assignment")
MAX_REGEX_LEN = 300
MAX_REPEAT = 100
MAX_UNBOUNDED = 2
MAX_QUANTIFIERS = 16

ENV_SSO_HOSTS = "FAIRPLAY_CANVAS_SSO_HOSTS"
DEFAULT_SSO_HOSTS = ("*.duosecurity.com", "login.microsoftonline.com", "login.microsoft.com",
                     "shib.oit.duke.edu", "go.canvas.duke.edu", "sso.canvaslms.com")
QUIZ_LTI_REGIONS = ("iad", "pdx", "dub", "fra", "sin", "syd", "yul")

DEFAULT_FINISH = {
    "classic": r"/quizzes/\d+/(submissions/\d+|history)",
    "new": r"submission-confirmation|submission-screen",
    "assignment": r"/assignments/\d+/submissions/\d+",
}


def canvas_exam(e) -> bool:
    return getattr(e, "external_kind", None) == KIND


def host_of(url: str | None) -> str | None:
    try:
        return (urlsplit(url or "").hostname or "").lower() or None
    except ValueError:
        return None


def is_https(url: str | None) -> bool:
    try:
        return urlsplit(url or "").scheme.lower() == "https"
    except ValueError:
        return False


_QUIZ_PATH = re.compile(r"^/courses/([0-9]+)/quizzes/([0-9]+)(?:/|\Z)")
_ASSIGNMENT_PATH = re.compile(r"^/courses/([0-9]+)/assignments/([0-9]+)(?:/|\Z)")


def infer_quiz_type(url: str) -> str | None:
    path = urlsplit(url).path
    if _QUIZ_PATH.match(path):
        return "classic"
    if _ASSIGNMENT_PATH.match(path):
        return "new"
    return None


_SCOPE_PATH = re.compile(r"/courses/([0-9]{1,20})/(quizzes|assignments)/([0-9]{1,20})(?:/.*)?")

NO_SCOPE_MESSAGE = ("The link must open one quiz or assignment, like "
                    "https://<canvas>/courses/123/quizzes/456")


UNCLEAN_URL_MESSAGE = ("The Canvas link may not contain spaces, backslashes, %, ;, @ before the "
                       "address, .. in the path, or a dot at the end of the website name")


def clean_url(url: str | None) -> bool:
    """False for a link the app and the server could read differently: a backslash, white
    space or control character, % escapes, ;, .. segments, a user name before the host, or a
    host that ends with a dot."""
    s = url or ""
    if any(ch in "\\%;" or ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in s):
        return False
    try:
        parts = urlsplit(s)
        host = parts.hostname or ""
    except ValueError:
        return False
    if "@" in parts.netloc or host.endswith(".") or parts.netloc.endswith("."):
        return False
    return ".." not in parts.path.split("/")


def scope_of(url: str | None) -> dict | None:
    """The course and item a Canvas exam link points at, or None when it is not a plain
    quiz or assignment link. Derived from the link on every read, never sent by a client."""
    if not clean_url(url):
        return None
    try:
        path = urlsplit(url or "").path
    except ValueError:
        return None
    m = _SCOPE_PATH.fullmatch(path)
    if m is None:
        return None
    return {"course_id": m[1], "item_kind": m[2], "item_id": m[3]}


_CLASS_PATTERNS = {"d": "[0-9]", "w": "[A-Za-z0-9_]", "s": r"[ \t\n\r\f\v]"}


def _simple_atom(kind: str, ch: str = "") -> tuple:
    if kind == "esc":
        if ch in "dws":
            return ("cls", ch)
        if ch in "DWSbB":
            return ("any",)
        return ("lit", ch)
    return (kind, ch)


def _atoms_overlap(a: tuple, b: tuple) -> bool:
    """Whether two neighbouring repeated items can match the same character. Anything that is
    not a single literal character or \\d, \\w, \\s counts as overlapping."""
    if a[0] not in ("lit", "cls") or b[0] not in ("lit", "cls"):
        return True
    if a[0] == "lit" and b[0] == "lit":
        return a[1].lower() == b[1].lower()
    if a[0] == "cls" and b[0] == "cls":
        return a[1] == b[1] or "s" not in (a[1], b[1])
    lit, cls = (a, b) if a[0] == "lit" else (b, a)
    if not lit[1].isascii():
        return True
    return re.fullmatch(_CLASS_PATTERNS[cls[1]], lit[1]) is not None


def validate_regex(pattern: str) -> str:
    if len(pattern) > MAX_REGEX_LEN:
        raise ValueError(f"finish_url_regex is longer than {MAX_REGEX_LEN} characters")
    n = len(pattern)
    i = 0
    stack: list[dict] = []
    closed: dict | None = None
    prev = "none"
    unbounded = 0
    total = 0
    pending: tuple | None = None
    cur: tuple = ("any",)
    cur_prev: tuple | None = None

    def bad_escape(ch: str, in_class: bool) -> None:
        if ch == "":
            raise ValueError("finish_url_regex ends with a lone backslash")
        if ch.isdigit() or ch in "kg":
            raise ValueError("finish_url_regex may not use backreferences")
        allowed = "dDwWsS" if in_class else "dDwWsSbB"
        if ch.isascii() and ch.isalpha() and ch not in allowed:
            raise ValueError(f"finish_url_regex may not use \\{ch}: it is not read the same on "
                             "every device")

    while i < n:
        c = pattern[i]
        if c == "\\":
            bad_escape(pattern[i + 1:i + 2], False)
            cur_prev, cur, pending = pending, _simple_atom("esc", pattern[i + 1]), None
            i += 2
            prev, closed = "atom", None
            continue
        if c == "[":
            j = i + 1
            if pattern.startswith("^", j):
                j += 1
            if pattern.startswith("]", j):
                raise ValueError("finish_url_regex: write a ] at the start of a character class "
                                 "as \\]")
            while j < n and pattern[j] != "]":
                ch = pattern[j]
                if ch == "\\":
                    bad_escape(pattern[j + 1:j + 2], True)
                    j += 2
                    continue
                if ch == "[":
                    raise ValueError("finish_url_regex may not nest [ inside a character class")
                if ch in "&-~|" and pattern.startswith(ch * 2, j):
                    raise ValueError("finish_url_regex may not use set operators in a "
                                     "character class")
                j += 1
            if j >= n:
                raise ValueError("finish_url_regex has an unclosed character class")
            i = j + 1
            cur_prev, cur, pending = pending, ("class",), None
            prev, closed = "atom", None
            continue
        if c == "(":
            if pattern.startswith("(?:", i):
                i += 3
            elif pattern.startswith("(?", i):
                m = re.match(r"\(\?[ims]+\)", pattern[i:]) if i == 0 else None
                if m:
                    i += m.end()
                    pending = None
                    prev, closed = "none", None
                    continue
                if pattern.startswith(("(?P<", "(?<"), i) and not pattern.startswith(("(?<=", "(?<!"), i):
                    raise ValueError("finish_url_regex may not use named groups")
                raise ValueError("finish_url_regex may only use (...), (?:...) and a leading "
                                 "(?i) flag")
            else:
                i += 1
            if stack:
                stack[-1]["nested"] = True
            stack.append({"alt": False, "quant": False, "nested": False, "before": pending})
            pending = None
            prev, closed = "none", None
            continue
        if c == "|":
            if stack:
                stack[-1]["alt"] = True
            i += 1
            pending = None
            prev, closed = "none", None
            continue
        if c == ")":
            if not stack:
                raise ValueError("finish_url_regex has an unmatched )")
            g = stack.pop()
            if stack:
                stack[-1]["alt"] = stack[-1]["alt"] or g["alt"]
                stack[-1]["quant"] = stack[-1]["quant"] or g["quant"]
            i += 1
            cur_prev, cur = g["before"], ("group",)
            pending = ("group",) if pending is not None else None
            prev, closed = "group", g
            continue
        if c == "}":
            raise ValueError("finish_url_regex: write a literal } as \\}")
        lo = hi = None
        end = i + 1
        if c in "*+?":
            lo, hi = {"*": (0, None), "+": (1, None), "?": (0, 1)}[c]
        elif c == "{":
            m = re.match(r"\{(\d+)(,(\d*))?\}", pattern[i:])
            if m is None:
                if pattern.startswith("{,", i):
                    raise ValueError("finish_url_regex may not use {,n}: write {0,n}")
                raise ValueError("finish_url_regex: write a literal { as \\{")
            lo = int(m[1])
            hi = lo if m[2] is None else (None if m[3] == "" else int(m[3]))
            end = i + m.end()
            if lo > MAX_REPEAT or (hi is not None and hi > MAX_REPEAT):
                raise ValueError(f"finish_url_regex repeat count is above {MAX_REPEAT}")
        if lo is None and c not in "*+?{":
            cur_prev, cur, pending = pending, (("dot", "") if c == "." else
                                               ("any",) if c in "^$" else ("lit", c)), None
            i += 1
            prev, closed = "atom", None
            continue
        if prev == "quant" and c == "?":
            i += 1
            prev = "lazy"
            continue
        if prev == "quant" and c == "+":
            raise ValueError("finish_url_regex may not use possessive quantifiers")
        if prev in ("quant", "lazy"):
            raise ValueError("finish_url_regex has a repeat of a repeat")
        if prev == "none":
            raise ValueError("finish_url_regex has a repeat with nothing to repeat")
        total += 1
        if hi is None:
            unbounded += 1
            if cur_prev is not None and _atoms_overlap(cur_prev, cur):
                raise ValueError("finish_url_regex has two repeats in a row that can match the "
                                 "same characters (like .*.* or \\w+\\w+)")
            pending = cur
        # The Mac app refuses any group followed by * + or {..} that holds a choice, a repeat or
        # another group (CanvasFinish.isSafe), so the server refuses the same shapes.
        if prev == "group" and closed is not None and (hi is None or hi > 1 or c == "{") \
                and (closed["alt"] or closed["quant"] or closed["nested"]):
            raise ValueError("finish_url_regex repeats a group that has an |, a repeat or another "
                             "group inside (catastrophic backtracking)")
        if stack:
            stack[-1]["quant"] = True
        i = end
        prev, closed = "quant", None
    if stack:
        raise ValueError("finish_url_regex has an unclosed (")
    if unbounded > MAX_UNBOUNDED:
        raise ValueError(f"finish_url_regex has more than {MAX_UNBOUNDED} open-ended repeats "
                         "(*, + or {n,})")
    if total > MAX_QUANTIFIERS:
        raise ValueError(f"finish_url_regex has more than {MAX_QUANTIFIERS} repeats")
    try:
        re.compile(pattern)
    except (re.error, RecursionError, OverflowError) as exc:
        raise ValueError(f"finish_url_regex does not compile: {exc}") from None
    return pattern


def default_finish_regex(quiz_type: str) -> str:
    return DEFAULT_FINISH[quiz_type]


def resolve_finish_regex(given: str | None, quiz_type: str) -> str:
    g = (given or "").strip()
    if not g or g in DEFAULT_FINISH.values():
        return DEFAULT_FINISH[quiz_type]
    return g


# ---------------------------------------------------------------- allowed domains
def sso_hosts() -> list[str]:
    raw = os.environ.get(ENV_SSO_HOSTS)
    if raw is None:
        return list(DEFAULT_SSO_HOSTS)
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def quiz_lti_hosts() -> list[str]:
    return [f"*.quiz-lti-{r}-prod.instructure.com" for r in QUIZ_LTI_REGIONS]


def auto_hosts(quiz_type: str) -> list[str]:
    lti = quiz_lti_hosts() if quiz_type == "new" else []
    return list(dict.fromkeys(lti + sso_hosts()))


def build_allowed_domains(canvas_host: str, quiz_type: str, extra: list[str],
                          removed: list[str] | None = None) -> list[str]:
    from .integrations.site_policy import filter_sites
    removed = removed or []
    lti_all = quiz_lti_hosts()
    sso = sso_hosts()
    lti = lti_all if quiz_type == "new" else []
    sso_used = [h for h in sso if h not in removed]
    teacher = [d for d in extra if d != canvas_host and d not in lti_all and d not in sso]
    kept_auto, dropped_auto = filter_sites(list(dict.fromkeys([canvas_host, *lti, *sso_used])))
    if any(d["site"] == canvas_host for d in dropped_auto):
        why = next(d["reason"] for d in dropped_auto if d["site"] == canvas_host)
        raise HTTPException(422, f"{canvas_host} cannot be used for an exam on this server: {why}")
    kept_teacher, dropped_teacher = filter_sites(teacher)
    if dropped_teacher:
        bad = "; ".join(f"{d['site']} ({d['reason']})" for d in dropped_teacher[:5])
        raise HTTPException(422, f"These websites cannot be allowed in an exam: {bad}")
    return list(dict.fromkeys(kept_auto + kept_teacher))


def auto_host_split(allowed_domains: list[str]) -> tuple[list[str], list[str]]:
    """The sign-in hosts and the quiz-lti hosts that are in force for an exam, read from its
    stored allowed_domains (which already left out what the teacher removed or the policy blocked)."""
    allowed = list(allowed_domains or [])
    sso = set(sso_hosts())
    lti = set(quiz_lti_hosts())
    return [h for h in allowed if h in sso], [h for h in allowed if h in lti]


def blocked_auto_hosts(quiz_type: str, removed: list[str] | None = None) -> list[str]:
    from .integrations.site_policy import filter_sites
    hosts = [h for h in auto_hosts(quiz_type) if h not in (removed or [])]
    return [d["site"] for d in filter_sites(hosts)[1]]


# ---------------------------------------------------------------- save

def clear_fields(e) -> None:
    e.external_kind = None
    e.canvas_quiz_type = None
    e.canvas_removed_hosts = []
    e.canvas_open_book = False


def resolve_quiz_type(e, body) -> str:
    """The quiz type of a canvas save: the request's, else the one already stored, else the
    one the link shows."""
    if body.canvas is not None and body.canvas.quiz_type:
        return body.canvas.quiz_type
    if canvas_exam(e):
        return e.canvas_quiz_type or "classic"
    quiz_type = infer_quiz_type(body.external_url or "")
    if quiz_type is None:
        raise HTTPException(422, "canvas.quiz_type is needed: the link is not a Canvas "
                                 "quiz or assignment link")
    return quiz_type


def apply_settings(e, body, prev_domains: list[str] | None = None) -> None:
    """Copies the validated canvas settings of an ExamIn onto the exam row (after _apply)."""
    if body.mode != "external":
        clear_fields(e)
        e.finish_url_regex = None
        return
    if body.external_kind != KIND:
        clear_fields(e)
        e.finish_url_regex = body.finish_url_regex
        return
    was_canvas = canvas_exam(e)
    quiz_type = resolve_quiz_type(e, body)
    host = host_of(body.external_url)
    extra = list(body.allowed_domains)
    if was_canvas and prev_domains is not None and "allowed_domains" not in body.model_fields_set:
        extra = list(prev_domains)
    removed = list(e.canvas_removed_hosts or []) if was_canvas else []
    if body.canvas is not None and "removed_hosts" in body.canvas.model_fields_set:
        removed = list(body.canvas.removed_hosts)
    if host in removed:
        raise HTTPException(422, "The Canvas website itself cannot be removed from the allowed websites")
    if body.canvas is not None and "open_book" in body.canvas.model_fields_set:
        e.canvas_open_book = bool(body.canvas.open_book)
    elif not was_canvas:
        e.canvas_open_book = False
    e.canvas_removed_hosts = removed
    e.external_kind = KIND
    e.canvas_quiz_type = quiz_type
    e.allowed_domains = build_allowed_domains(host, quiz_type, extra, removed)
    e.finish_url_contains = None
    e.finish_url_regex = resolve_finish_regex(body.finish_url_regex, quiz_type)


def canvas_view(e) -> dict | None:
    if not canvas_exam(e):
        return None
    scope = scope_of(e.external_url)
    return {"quiz_type": e.canvas_quiz_type or "classic",
            "scope": None if scope is None else {**scope},
            "open_book": bool(e.canvas_open_book)}


def teacher_canvas_view(e) -> dict | None:
    v = canvas_view(e)
    if v is None:
        return None
    return {**v, "removed_hosts": list(e.canvas_removed_hosts or []), "sign_in_hosts": sso_hosts()}


def teacher_notice(e) -> str | None:
    if not canvas_exam(e):
        return None
    parts = []
    if scope_of(e.external_url) is None:
        parts.append("Fix the link: it must open one quiz or assignment, like "
                     "https://<canvas>/courses/123/quizzes/456. Until then students cannot start "
                     "this exam and you cannot open it.")
    quiz_type = e.canvas_quiz_type or "classic"
    blocked = blocked_auto_hosts(quiz_type, list(e.canvas_removed_hosts or []))
    if blocked:
        parts.append("This server's website policy blocks these sign-in websites, so FairPlay did "
                     f"not allow them: {', '.join(blocked)}. Students may not be able to sign in "
                     "to Canvas.")
    return " ".join(parts) or None

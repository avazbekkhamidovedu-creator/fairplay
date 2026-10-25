"""Canvas mode (Mode B): a website exam whose Canvas access code is held by the server and
typed by the native app. Contract: docs/contract-canvas-mode-b-2026-10-25.md."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update

from .timeutil import utcnow

log = logging.getLogger("proctora.canvas")

KIND = "canvas"
QUIZ_TYPES = ("classic", "new", "assignment")
SOURCES = ("managed", "manual")
CODE_MIN, CODE_MAX = 4, 64
MANAGED_CODE_LEN = 24
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
CODE_VALID_FOR_S = 60
PROOF_FRESH_MS = 5 * 60 * 1000
PROOF_REPLAY_TTL_S = 6 * 60
CODE_REQUESTS_PER_ATTEMPT = 6
CODE_ATTEMPT_WINDOW_S = 10 * 60
CODE_REQUESTS_PER_EXAM_PER_MIN = 300
CODE_REQUESTS_PER_STUDENT_FACTOR = 3
CANVAS_CALL_TIMEOUT_S = 20
OPEN_CLAIM_S = 90
MAX_REGEX_LEN = 300
MAX_REPEAT = 100
MAX_UNBOUNDED = 8
MAX_QUANTIFIERS = 16

ENV_SSO_HOSTS = "FAIRPLAY_CANVAS_SSO_HOSTS"
DEFAULT_SSO_HOSTS = ("*.duosecurity.com", "login.microsoftonline.com", "login.microsoft.com",
                     "sso.dukekunshan.edu.cn")
QUIZ_LTI_REGIONS = ("iad", "pdx", "dub", "fra", "sin", "syd", "yul")

DEFAULT_FINISH = {
    "classic": r"/quizzes/\d+/(submissions/\d+|history)",
    "new": r"submission-confirmation|submission-screen",
    "assignment": r"/assignments/\d+/submissions/\d+",
}

CODE_FAILED = "canvas_code_failed"
NEW_QUIZ_NOTICE = ("Canvas has no way for FairPlay to set the access code on New Quizzes or "
                   "assignments. Set the code in Canvas and enter it here.")
NO_CODE_NOTICE = "No access code entered yet. Enter the code you set in Canvas before opening the exam."
RELEASE_FAILED_NOTICE = ("FairPlay could not clear the access code when the exam closed. "
                         "Remove the access code in Canvas so the quiz is not left locked.")

_secret = ""


def set_secret(secret: str) -> None:
    global _secret
    _secret = secret


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


_QUIZ_PATH = re.compile(r"^/courses/(\d+)/quizzes/(\d+)(?:/|$)")
_ASSIGNMENT_PATH = re.compile(r"^/courses/(\d+)/assignments/(\d+)(?:/|$)")


def infer_quiz_type(url: str) -> str | None:
    path = urlsplit(url).path
    if _QUIZ_PATH.match(path):
        return "classic"
    if _ASSIGNMENT_PATH.match(path):
        return "new"
    return None


def quiz_ids(url: str | None) -> tuple[str, str] | None:
    m = _QUIZ_PATH.match(urlsplit(url or "").path)
    return (m[1], m[2]) if m else None


def effective_source(quiz_type: str, requested: str) -> str:
    return "managed" if requested == "managed" and quiz_type == "classic" else "manual"


# ---------------------------------------------------------------- finish_url_regex
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
            prev, closed = "atom", None
            continue
        if c == "(":
            if pattern.startswith("(?:", i):
                i += 3
            elif pattern.startswith("(?", i):
                m = re.match(r"\(\?[ims]+\)", pattern[i:]) if i == 0 else None
                if m:
                    i += m.end()
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
            stack.append({"alt": False, "quant": False, "nested": False})
            prev, closed = "none", None
            continue
        if c == "|":
            if stack:
                stack[-1]["alt"] = True
            i += 1
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


def blocked_auto_hosts(quiz_type: str, removed: list[str] | None = None) -> list[str]:
    from .integrations.site_policy import filter_sites
    hosts = [h for h in auto_hosts(quiz_type) if h not in (removed or [])]
    return [d["site"] for d in filter_sites(hosts)[1]]


# ---------------------------------------------------------------- save
def new_managed_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(MANAGED_CODE_LEN))


def seal_code(code: str) -> str:
    from .lms.secretbox import seal
    return seal(_secret, code)


def open_code(sealed: str | None) -> str | None:
    if not sealed:
        return None
    from .lms.secretbox import open_
    try:
        return open_(_secret, sealed)
    except (ValueError, UnicodeDecodeError):
        return None


def clear_fields(e) -> None:
    e.external_kind = None
    e.canvas_quiz_type = None
    e.canvas_code_source = None
    e.canvas_code_enc = None
    e.canvas_code_failed = False
    e.canvas_removed_hosts = []


def resolve_settings(e, body) -> tuple[str, str, str | None]:
    """(quiz_type, effective source, new manual code) of a canvas save. A save without a
    canvas block, or without a source, keeps what the exam already has."""
    was_canvas = canvas_exam(e)
    c = body.canvas
    if c is None:
        if was_canvas:
            quiz_type = e.canvas_quiz_type or "classic"
            requested = e.canvas_code_source or "manual"
        else:
            quiz_type = infer_quiz_type(body.external_url or "")
            if quiz_type is None:
                raise HTTPException(422, "canvas.quiz_type is needed: the link is not a Canvas "
                                         "quiz or assignment link")
            requested = "managed"
        return quiz_type, effective_source(quiz_type, requested), None
    requested = c.access_code_source
    if was_canvas and "access_code_source" not in c.model_fields_set:
        requested = e.canvas_code_source or "manual"
    return c.quiz_type, effective_source(c.quiz_type, requested), c.access_code


async def release_before_change(session, e, body) -> None:
    """A draft that still holds a live managed code gives it back in Canvas before a save
    switches it to a manual code, another quiz or a plain website exam."""
    e.canvas_release_failed = False
    if not (canvas_exam(e) and e.canvas_code_source == "managed" and e.canvas_code_enc):
        return
    if body.mode == "external" and body.external_kind == KIND:
        _, source, _ = resolve_settings(e, body)
        if source == "managed" and quiz_ids(body.external_url) == quiz_ids(e.external_url):
            return
    ok = await clear_in_canvas(session, e)
    e.canvas_code_enc = None
    e.canvas_release_failed = not ok


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
    quiz_type, source, access_code = resolve_settings(e, body)
    host = host_of(body.external_url)
    if source == "managed" and access_code:
        raise HTTPException(422, "access_code is only for the manual source")
    if source == "managed" and quiz_ids(body.external_url) is None:
        raise HTTPException(422, "The link must be a Canvas quiz link like "
                                 "https://school.instructure.com/courses/12/quizzes/34")
    previous = e.canvas_code_source if was_canvas else None
    if source == "manual":
        if access_code:
            e.canvas_code_enc = seal_code(access_code)
        elif previous != "manual":
            e.canvas_code_enc = None
    elif previous != "managed":
        e.canvas_code_enc = None
    extra = list(body.allowed_domains)
    if was_canvas and prev_domains is not None and "allowed_domains" not in body.model_fields_set:
        extra = list(prev_domains)
    removed = list(e.canvas_removed_hosts or []) if was_canvas else []
    if body.canvas is not None and "removed_hosts" in body.canvas.model_fields_set:
        removed = list(body.canvas.removed_hosts)
    if host in removed:
        raise HTTPException(422, "The Canvas website itself cannot be removed from the allowed websites")
    e.canvas_removed_hosts = removed
    e.external_kind = KIND
    e.canvas_quiz_type = quiz_type
    e.canvas_code_source = source
    e.canvas_code_failed = False
    e.allowed_domains = build_allowed_domains(host, quiz_type, extra, removed)
    e.finish_url_contains = None
    e.finish_url_regex = resolve_finish_regex(body.finish_url_regex, quiz_type)


def canvas_view(e) -> dict | None:
    if not canvas_exam(e):
        return None
    return {"quiz_type": e.canvas_quiz_type or "classic",
            "access_code_source": e.canvas_code_source or "manual"}


def code_status(e) -> str:
    if e.canvas_code_failed:
        return "failed"
    return "set" if e.canvas_code_enc else "not_set"


def teacher_canvas_view(e) -> dict | None:
    v = canvas_view(e)
    if v is None:
        return None
    return {**v, "code_status": code_status(e), "removed_hosts": list(e.canvas_removed_hosts or []),
            "remove_code_in_canvas": bool(e.canvas_release_failed)}


def teacher_notice(e) -> str | None:
    parts = []
    if getattr(e, "canvas_release_failed", False):
        parts.append(RELEASE_FAILED_NOTICE)
    if not canvas_exam(e):
        return " ".join(parts) or None
    quiz_type = e.canvas_quiz_type or "classic"
    blocked = blocked_auto_hosts(quiz_type, list(e.canvas_removed_hosts or []))
    if blocked:
        parts.append("This server's website policy blocks these sign-in websites, so FairPlay did "
                     f"not allow them: {', '.join(blocked)}. Students may not be able to sign in "
                     "to Canvas.")
    if quiz_type != "classic":
        parts.append(NEW_QUIZ_NOTICE)
    if (e.canvas_code_source or "manual") == "manual" and not e.canvas_code_enc:
        parts.append(NO_CODE_NOTICE)
    return " ".join(parts) or None


# ---------------------------------------------------------------- Canvas side
def _failed(message: str) -> HTTPException:
    return HTTPException(409, {"code": CODE_FAILED, "message": message})


async def _provider(session, e):
    from .models import LmsConnection
    host = host_of(e.external_url)
    rows = (await session.execute(
        select(LmsConnection).where(LmsConnection.teacher_id == e.teacher_id,
                                    LmsConnection.provider == "canvas")
        .order_by(LmsConnection.id))).scalars().all()
    conn = next((c for c in rows if host_of(c.base_url) == host), None)
    if conn is None:
        return None
    from .lms import make_provider
    from .lms.secretbox import open_
    from .routes_lms import _allow_private
    try:
        token = open_(_secret, conn.credential)
    except (ValueError, UnicodeDecodeError):
        return None
    return make_provider("canvas", conn.base_url, token, allow_private=_allow_private())


async def claim_open(session, e) -> bool:
    """Marks the exam as being opened so two opens cannot both rotate the Canvas code."""
    from .models import Exam
    now = utcnow()
    res = await session.execute(
        update(Exam).where(
            Exam.id == e.id, Exam.status.in_(("draft", "closed")),
            or_(Exam.canvas_open_claim.is_(None),
                Exam.canvas_open_claim < now - timedelta(seconds=OPEN_CLAIM_S)))
        .values(canvas_open_claim=now))
    await session.commit()
    await session.refresh(e)
    return res.rowcount == 1


async def abort_open(session, e, failed: bool = True) -> None:
    from .models import Exam
    eid = e.id
    await session.rollback()
    await session.execute(update(Exam).where(Exam.id == eid)
                          .values(canvas_open_claim=None, canvas_code_failed=failed))
    await session.commit()
    await session.refresh(e)


async def prepare_open(session, e, manual_code: str | None = None) -> None:
    """After claim_open: a managed code is set in Canvas, a manual one must exist. On
    failure the claim is dropped and the exam stays as it was (409 canvas_code_failed)."""
    eid = e.id
    try:
        await _prepare_open(session, e, manual_code)
    except HTTPException:
        await abort_open(session, e, True)
        raise
    except Exception:
        await abort_open(session, e, True)
        log.warning("canvas open failed for exam %s", eid)
        raise _failed("Canvas could not be reached. Try again, or switch to a manual access "
                      "code.") from None
    e.canvas_code_failed = False
    e.canvas_release_failed = False


async def _prepare_open(session, e, manual_code: str | None) -> None:
    from .lms import LmsError
    if (e.canvas_code_source or "manual") == "manual":
        if manual_code:
            e.canvas_code_enc = seal_code(manual_code)
            return
        if not e.canvas_code_enc or open_code(e.canvas_code_enc) is None:
            raise _failed(NO_CODE_NOTICE if not e.canvas_code_enc else
                          "The saved access code can no longer be read. Enter it again.")
        return
    ids = quiz_ids(e.external_url)
    if ids is None:
        raise _failed("The exam link is not a Canvas quiz link. Switch to a manual access code.")
    prov = await _provider(session, e)
    if prov is None:
        raise _failed("Connect your Canvas account (same Canvas address as the quiz) under "
                      "Connections, or switch to a manual access code.")
    code = new_managed_code()
    try:
        async def call():
            async with prov:
                await prov.set_quiz_access_code(ids[0], ids[1], code)
        await asyncio.wait_for(call(), CANVAS_CALL_TIMEOUT_S)
    except LmsError as exc:
        status = f" (HTTP {exc.status})" if exc.status else ""
        raise _failed(f"Canvas did not accept the access code{status}. Check that your Canvas "
                      "account may edit this quiz, or switch to a manual access code.") from None
    except Exception:
        log.warning("canvas code rotation failed for exam %s", e.id)
        raise _failed("Canvas could not be reached. Try again, or switch to a manual access "
                      "code.") from None
    e.canvas_code_enc = seal_code(code)


async def clear_in_canvas(session, e) -> bool:
    """Sets an empty access code on the Canvas quiz. False when it could not be done;
    never raises."""
    ids = quiz_ids(e.external_url)
    try:
        prov = await _provider(session, e) if ids else None
        if prov is None:
            log.warning("canvas code not cleared for exam %s: no usable Canvas connection", e.id)
            return False

        async def call():
            async with prov:
                await prov.set_quiz_access_code(ids[0], ids[1], "")
        await asyncio.wait_for(call(), CANVAS_CALL_TIMEOUT_S)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("canvas code not cleared for exam %s: %s", e.id, type(exc).__name__)
        return False


async def release_on_close(session, e) -> None:
    """After an exam closes: the stored code is forgotten (managed and manual) and a managed
    one is cleared in Canvas. A Canvas failure never blocks the close; the teacher sees
    RELEASE_FAILED_NOTICE instead."""
    if not canvas_exam(e):
        return
    ok = True
    if e.canvas_code_source == "managed":
        ok = await clear_in_canvas(session, e)
    e.canvas_code_enc = None
    e.canvas_release_failed = not ok
    await session.commit()


async def exam_budget(session, store, exam) -> int:
    """Code issues allowed per minute for the whole exam: sized to the roster."""
    key = f"ccb:{exam.id}"
    cached = await store.get(key)
    if cached:
        return int(cached)
    from .access import eligible_ids
    roster = 0
    try:
        ids = eligible_ids(exam)
        if ids is not None:
            roster = (await session.execute(select(func.count()).select_from(ids.subquery()))).scalar() or 0
    except Exception:  # noqa: BLE001
        log.warning("canvas roster size unavailable for exam %s", exam.id)
    budget = max(CODE_REQUESTS_PER_EXAM_PER_MIN, CODE_REQUESTS_PER_STUDENT_FACTOR * int(roster))
    await store.set(key, str(budget), 120)
    return budget

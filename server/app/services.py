"""Shared helpers: ID/code generation, serialization and store-backed caches."""
from __future__ import annotations

import hashlib
import json
import random
import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .grading import num
from .models import Attempt, Exam
from .canvas_mode import auto_host_split, canvas_view, teacher_canvas_view, teacher_notice
from .schemas import CODE_LANGUAGES, DEFAULT_TIME_LIMIT_MS, external_host
from .integrations.registry import integration_for
from .question_types import extended_fields
from .access import open_to_out
from .store import Store
from .timeutil import iso

EXAM_CACHE_TTL = 120          # seconds; also explicitly invalidated on edit/open/close/delete
ATTEMPT_META_TTL = 26 * 3600
LIVE_TTL = 26 * 3600
RECEIPT_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L
TEACHER_ONLY_FIELDS = ("correct_index", "answer_number", "tolerance", "accepted_answers")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(9).replace('-', 'x').replace('_', 'y')}"


START_CODE_DIGITS = 6   # audit #13: was 4 (10 000 codes for a whole room)


def new_start_code() -> str:
    return f"{secrets.randbelow(10 ** START_CODE_DIGITS):0{START_CODE_DIGITS}d}"


def new_invigilator_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def new_receipt() -> str:
    s = "".join(secrets.choice(RECEIPT_ALPHABET) for _ in range(8))
    return f"{s[:4]}-{s[4:]}"


def teacher_question(q: dict) -> dict:
    return {
        "id": q["id"], "type": q["type"], "prompt": q["prompt"], "points": q.get("points", 1),
        "options": list(q.get("options") or []),
        "correct_index": q.get("correct_index"),
        "answer_number": q.get("answer_number"),
        "tolerance": q.get("tolerance"),
        "accepted_answers": list(q.get("accepted_answers") or []),
        "rubric": [dict(r) for r in q.get("rubric") or []],
        "min_words": q.get("min_words"),
        "max_words": q.get("max_words"),
        # slice 6
        "images": [dict(i) for i in q.get("images") or []],
        "topics": list(q.get("topics") or []),
        "answer_lines": q.get("answer_lines"),  # slice 6: paper answer space
        **code_fields(q),                         # phase 4: coding questions only
        **extended_fields(q, teacher=True),       # 2026-10-23: extended question types
    }


def code_language(q: dict, default_language: str | None = None) -> str:
    """Phase 4: the language a coding question runs in (its own, else the exam default)."""
    lang = q.get("language")
    if lang in CODE_LANGUAGES:
        return lang
    return default_language if default_language in CODE_LANGUAGES else "python"


def code_fields(q: dict) -> dict:
    """Phase 4: the stored coding fields (teacher view); {} for other question types."""
    if q.get("type") != "code":
        return {}
    return {
        # multi-language code: language = the first language's runner (None = no in-app runner)
        "language": q.get("language") if q.get("language") in CODE_LANGUAGES else None,
        "starter_code": q.get("starter_code") or "",
        "tests": [{"input": t.get("input", ""), "expected": t.get("expected", ""),
                   "visible": bool(t.get("visible")), "points": t.get("points", 0)}
                  for t in q.get("tests") or [] if isinstance(t, dict)],
        "write_only": bool(q.get("write_only")),
        "time_limit_ms": int(q.get("time_limit_ms") or DEFAULT_TIME_LIMIT_MS),
    }


def student_question(q: dict, default_language: str | None = None) -> dict:
    tq = teacher_question(q)
    for f in TEACHER_ONLY_FIELDS:
        tq.pop(f)
    # slice 6: topic tags can hint at the method ("integration by parts"): not shown while
    # writing. Images are shown above the prompt.
    tq.pop("topics", None)
    tq.pop("external_ref", None)        # the external system's ids: teachers only
    if "prompt_format" in tq and tq["type"] == "code" and tq.get("languages"):
        # extended code question: all samples are visible, each language keeps its runner
        tq["tests"] = [{"input": t["input"], "expected": t["expected"]} for t in tq.get("tests") or []]
        return tq
    if tq["type"] == "code":
        # Phase 4: hidden tests (input and expected output) never reach students, nor their
        # count or points. Visible tests are the samples. The language is resolved here:
        # students see it as a fixed label.
        tq["language"] = code_language(q, default_language)
        tq["tests"] = [{"input": t["input"], "expected": t["expected"]}
                       for t in tq["tests"] if t["visible"]]
    return tq


def exam_delivery(e) -> str:
    """Slice 6 delivery; rows from before slice 6 derive it from mode."""
    d = getattr(e, "delivery", None)
    if d in ("computer", "paper", "website"):
        return d
    return "website" if getattr(e, "mode", None) == "external" else "computer"


def exam_out(e: Exam, pending_grading: int = 0) -> dict:
    return {
        "id": e.id, "title": e.title, "duration_min": e.duration_min, "status": e.status,
        "start_code": e.start_code if e.status == "live" else None,
        # audit #5: per-exam code for the app's emergency exit (teacher only)
        "invigilator_code": e.invigilator_code if e.status == "live" else None,
        "questions": [teacher_question(q) for q in e.questions or []],
        "created_at": iso(e.created_at),
        "question_count": e.question_count, "total_points": num(e.total_points),
        "course": e.course or "", "room": e.room or "", "scheduled_at": iso(e.scheduled_at),
        "shuffle_questions": bool(e.shuffle_questions),
        "grades_released": bool(e.grades_released), "released_at": iso(e.released_at),
        "closed_at": iso(e.closed_at), "pending_grading": int(pending_grading),
        **external_fields(e),
        "canvas": teacher_canvas_view(e) if e.mode == "external" else None,
        "canvas_notice": teacher_notice(e),
        # slice 5: LMS link (ids only; never any credential)
        "course_link_id": e.course_link_id, "lms_assignment_id": e.lms_assignment_id,
        "delivery": exam_delivery(e),  # slice 6
        "class_id": e.class_id,        # audit S-03
        # 2026-10-14, emptied 2026-10-23: outside apps are no longer allowed (older pages
        # still read the field)
        "allowed_apps": [],
        **lock_settings(e),                           # S3 / S6
        **tool_settings(e),                           # 2026-10-23: exam tools
        "default_language": exam_default_language(e),  # phase 4
        "external": external_block(e),                # integrations: null = FairPlay exam
        # 2026-10-23: "all" or the selected student ids; the automatic close time (external
        # exams: from their source, read-only) and whether the last close was automatic
        "open_to": open_to_out(e.open_to),
        "closes_at": iso(exam_closes_at(e)),
        "auto_closed": bool(e.auto_closed),
    }


def exam_closes_at(e: Exam):
    """2026-10-23: when the exam closes by itself (naive UTC) or None. External exams ask
    their source (e.g. the synced end time); FairPlay exams use their own setting."""
    integ = integration_for(e)
    return integ.closes_at(e) if integ is not None else e.closes_at


def external_block(e: Exam) -> dict | None:
    """Teacher views: {source, name, url, read_only, graded_externally, ...} of an exam
    from an external system (app.integrations), None for a FairPlay exam."""
    integ = integration_for(e)
    return integ.external_block(e) if integ else None


def exam_default_language(e) -> str:
    """Phase 4: the exam's default coding language (rows from before: python)."""
    lang = getattr(e, "default_language", None)
    return lang if lang in CODE_LANGUAGES else "python"


def tool_settings(e: Exam) -> dict:
    """2026-10-23: the exam tools as stored (teacher view and the student exam cache).
    Rows from before have the column defaults; website exams never have tools.
    2026-10-24: + website_links (start pages, their hosts are in websites), companions (sites
    the lock also opens so a service like Box works, app.site_companions; teacher view) and
    upload_opens_days (open book: uploads that many days before the date)."""
    if e.mode == "external":
        return {"calculator": "off", "open_book": False, "websites": [], "website_links": [],
                "companions": [], "upload_opens_days": 0}
    from .schemas import clean_website_links
    from .site_companions import companions
    calc = e.calculator if e.calculator in ("scientific", "graphing") else "off"
    websites = list(e.websites or [])
    if (getattr(e, "source", None) or "fairplay") != "fairplay" and websites:
        # security review 2026-10-24: also an exam synced (and started) before the policy
        from .integrations.site_policy import filter_sites
        websites = filter_sites(websites)[0]
    return {"calculator": calc, "open_book": bool(e.open_book), "websites": websites,
            "website_links": clean_website_links(getattr(e, "website_links", None) or [], websites),
            "companions": companions(websites),
            "upload_opens_days": upload_days(e)}


def upload_days(e) -> int:
    """2026-10-24: Exam.upload_opens_days in 0..7 (rows from before: 0)."""
    try:
        n = int(getattr(e, "upload_opens_days", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return n if 0 <= n <= 7 else 0


def student_tools(exam: dict) -> dict:
    """`tools` in the attempt view, the exam list and the offline package. `exam` is the
    cached student view (entries cached before 2026-10-23 have no tool fields: all off).
    2026-10-24: `websites` stays the allowed sites the student sees; `allowed_sites` = those
    plus their companions (app.site_companions), what the page hands the app to open;
    `website_links` = the start pages ({"host", "url", "title"})."""
    if (exam.get("mode") or "questions") == "external":
        return {"calculator": "off", "open_book": False, "websites": [], "website_links": [],
                "allowed_sites": []}
    from .site_companions import allowed_sites
    calc = exam.get("calculator")
    websites = [w for w in exam.get("websites") or [] if isinstance(w, str)]
    links = [{"host": x["host"], "url": x["url"], "title": x.get("title")}
             for x in exam.get("website_links") or []
             if isinstance(x, dict) and isinstance(x.get("url"), str) and x.get("host") in websites]
    return {"calculator": calc if calc in ("scientific", "graphing") else "off",
            "open_book": bool(exam.get("open_book")),
            "websites": websites, "website_links": links,
            "allowed_sites": allowed_sites(websites)}


def canvas_scope_of(exam: dict) -> dict | None:
    canvas = exam.get("canvas") or {}
    scope = canvas.get("scope")
    if not scope:
        return None
    sign_in, lti = auto_host_split(exam.get("allowed_domains") or [])
    return {**scope, "open_book": bool(canvas.get("open_book")),
            "sign_in_hosts": sign_in, "quiz_lti_hosts": lti}


def lock_settings(e: Exam) -> dict:
    """S3 / S6 exam settings (teacher view and the student-side exam cache)."""
    return {"resume_needs_approval": e.resume_needs_approval is not False,
            "require_strong_lock": bool(e.require_strong_lock)}


def external_fields(e: Exam) -> dict:
    """Slice 3 exam fields as stored (teacher view)."""
    external = e.mode == "external"
    return {
        "mode": e.mode or "questions",
        "external_url": e.external_url if external else None,
        "allowed_domains": list(e.allowed_domains or []) if external else [],
        "finish_url_contains": e.finish_url_contains if external else None,
        "finish_url_regex": e.finish_url_regex if external else None,
        "external_kind": e.external_kind if external else None,
        "canvas": canvas_view(e) if external else None,
    }


# Hosts that serve many unrelated things (any shared Doc, Search, Gemini...): the exam
# address only opens its own path there, never the whole host (audit #10).
BROAD_HOSTS = {"docs.google.com", "drive.google.com", "sites.google.com", "www.google.com",
               "google.com", "forms.office.com", "forms.microsoft.com", "onedrive.live.com",
               "www.notion.so", "notion.so", "github.com", "gist.github.com", "www.dropbox.com",
               "dropbox.com", "sharepoint.com", "www.bing.com", "bing.com"}


def start_url_entry(url: str | None) -> str | None:
    """Allowed-site entry the exam address always gets: its exact host, or host + path
    folder on BROAD_HOSTS (https://docs.google.com/forms/d/e/X/viewform ->
    docs.google.com/forms/d/e/X/)."""
    host = external_host(url)
    if not host:
        return None
    if host not in BROAD_HOSTS and not host.endswith(".sharepoint.com"):
        return host
    from urllib.parse import urlsplit
    path = urlsplit((url or "").strip()).path or "/"
    folder = path[:path.rfind("/") + 1] if "/" in path else "/"
    return host if folder in ("", "/") else host + folder


def student_allowed_domains(e: Exam) -> list[str]:
    """Allowed websites for the app: the stored entries (exact host, "*.host" for
    subdomains, optional /path prefix) plus, always, the exam address entry."""
    domains = list(e.allowed_domains or [])
    entry = start_url_entry(e.external_url)
    if entry and entry not in domains:
        domains.insert(0, entry)
    return domains


async def pending_by_exam(session: AsyncSession, exam_ids: list[str]) -> dict[str, int]:
    """Pending essay answers per exam from the denormalised Attempt.needs_grading (one query)."""
    if not exam_ids:
        return {}
    rows = await session.execute(
        select(Attempt.exam_id, func.coalesce(func.sum(Attempt.needs_grading), 0))
        .where(Attempt.exam_id.in_(exam_ids), Attempt.status == "submitted")
        .group_by(Attempt.exam_id))
    return {eid: int(n or 0) for eid, n in rows.all()}


async def exam_out_full(session: AsyncSession, e: Exam) -> dict:
    return exam_out(e, (await pending_by_exam(session, [e.id])).get(e.id, 0))


# ---------------------------------------------------------------- exam cache
def exam_key(exam_id: str) -> str:
    return f"exam:{exam_id}"


def exam_hot_key(exam_id: str) -> str:
    return f"exh:{exam_id}"


async def invalidate_exam(store: Store, exam_id: str) -> None:
    await store.delete(exam_key(exam_id), exam_hot_key(exam_id))


def _hot_of(data: dict) -> dict:
    from .question_types import is_extended
    # Extended questions (multi-select, fill-blank, file upload, multi-language code from
    # DKU OJ) are checked on every save, so they ride along; plain exams stay tiny.
    return {"question_ids": list(data.get("question_ids") or []),
            "questions": [q for q in data.get("questions") or [] if is_extended(q)],
            "require_strong_lock": bool(data.get("require_strong_lock"))}


async def get_exam_hot(store: Store, session: AsyncSession, exam_id: str) -> dict | None:
    """perf 0.6.2: the few exam facts the per-request hot paths need (question ids for
    answer saves, require_strong_lock for app beats) without fetching and parsing the whole
    cached exam (questions and all) on every save and beat. Same lifetime as the exam
    cache: built from it, never outlives it, dropped with it (invalidate_exam)."""
    raw = await store.get(exam_hot_key(exam_id))
    if raw:
        return json.loads(raw)
    exam = await get_exam_cached(store, session, exam_id)
    if exam is None:
        return None
    hot = _hot_of(exam)
    ttl = await store.ttl(exam_key(exam_id))
    if ttl > 0:
        await store.set(exam_hot_key(exam_id), json.dumps(hot), min(ttl, EXAM_CACHE_TTL))
    return hot


async def get_exam_cached(store: Store, session: AsyncSession, exam_id: str) -> dict | None:
    """Student-view exam content (no answer keys) + status/start_code, cached per exam."""
    raw = await store.get(exam_key(exam_id))
    if raw:
        return json.loads(raw)
    e = await session.get(Exam, exam_id)
    if e is None:
        return None
    data = {
        "id": e.id, "title": e.title, "duration_min": e.duration_min, "status": e.status,
        "start_code": e.start_code, "question_count": e.question_count,
        "course": e.course or "", "shuffle_questions": bool(e.shuffle_questions),
        "question_ids": [q["id"] for q in e.questions or []],
        "course_link_id": e.course_link_id,  # slice 5: start restricted to that LMS course
        "class_id": e.class_id,              # audit S-03: ... or to the exam's class
        "delivery": exam_delivery(e),  # slice 6: paper exams are never started in the app
        "scheduled_at": iso(e.scheduled_at),
        "allowed_apps": [],                           # 2026-10-14, emptied 2026-10-23
        **lock_settings(e),                           # S3 / S6: read by start and app-beat
        **tool_settings(e),                           # 2026-10-23: exam tools
        "is_trial": bool(e.is_trial),                 # 2026-10-22: the sample exam
        "source": e.source or "fairplay",             # integrations
        "external_name": (integration_for(e).name if integration_for(e) else None),
        "total_points": num(e.total_points or 0),
        "closes_at": iso(exam_closes_at(e)),         # 2026-10-23 (offline countdown cap)
        "questions": [student_question(q, exam_default_language(e)) for q in e.questions or []],
        **external_fields(e),
    }
    if data["mode"] == "external":
        data["allowed_domains"] = student_allowed_domains(e)
    await store.set(exam_key(exam_id), json.dumps(data), EXAM_CACHE_TTL)
    return data


# ---------------------------------------------------------------- attempt meta cache
def attempt_key(aid: str) -> str:
    return f"att:{aid}"


def attempt_meta(a: Attempt) -> dict:
    return {"exam_id": a.exam_id, "student_id": a.student_id, "status": a.status,
            "deadline_at": iso(a.deadline_at), "session_id": a.session_id,
            "device_id": a.device_id, "started_at": iso(a.started_at),
            "approval": approval_of(a)}


def approval_of(a: Attempt) -> str | None:
    """S3: "pending" while the teacher has not let a resumed attempt continue, "approved"
    after that, None if it never had to wait."""
    if a.pending_resume:
        return "pending"
    return "approved" if a.pending_since is not None else None


async def cache_attempt(store: Store, a: Attempt) -> None:
    await store.set(attempt_key(a.id), json.dumps(attempt_meta(a)), ATTEMPT_META_TTL)


async def get_attempt_meta(store: Store, session: AsyncSession, aid: str) -> dict | None:
    return await attempt_meta_from(store, session, aid, await store.get(attempt_key(aid)))


async def attempt_meta_from(store: Store, session: AsyncSession, aid: str,
                            raw: str | None) -> dict | None:
    """get_attempt_meta with the cached entry already fetched (perf 0.6.2: hot paths read it
    in the same Redis round trip as their other keys)."""
    if raw:
        meta = json.loads(raw)
        if "approval" in meta:  # entries cached before S3 (or the audit fixes) lack it
            return meta
    a = await session.get(Attempt, aid)
    if a is None:
        return None
    await cache_attempt(store, a)
    return attempt_meta(a)


async def find_attempt(session: AsyncSession, exam_id: str, student_id: int) -> Attempt | None:
    res = await session.execute(
        select(Attempt).where(Attempt.exam_id == exam_id, Attempt.student_id == student_id))
    return res.scalar_one_or_none()


def shuffled_for(attempt_id: str, questions: list) -> list:
    """Per-student question order: deterministic from the attempt id (stable across reloads,
    processes and workers), different between attempts."""
    seed = int.from_bytes(hashlib.sha256(attempt_id.encode()).digest()[:8], "big")
    out = list(questions)
    random.Random(seed).shuffle(out)
    return out


def attempt_view(a: Attempt, exam: dict, now: datetime) -> dict:
    questions = exam["questions"]
    if exam.get("shuffle_questions"):
        questions = shuffled_for(a.id, questions)
    mode = exam.get("mode") or "questions"
    external = mode == "external"
    return {
        "attempt_id": a.id, "exam_id": a.exam_id, "title": exam["title"],
        "course": exam.get("course", ""),
        "duration_min": exam["duration_min"], "started_at": iso(a.started_at),
        "deadline_at": iso(a.deadline_at), "server_time": iso(now),
        "questions": [] if external else questions,
        "answers": {} if external else (a.answers or {}), "status": a.status,
        "mode": mode,
        "external_url": exam.get("external_url") if external else None,
        "allowed_domains": list(exam.get("allowed_domains") or []) if external else [],
        "finish_url_contains": exam.get("finish_url_contains") if external else None,
        # 2026-10-14: apps the app keeps open while locked (everything else is closed).
        # 2026-10-23: always [] now (older caches may still hold keys): close everything.
        "allowed_apps": [],
        # 2026-10-23: {calculator, open_book, websites}; the page passes websites to the
        # app as allowed_sites (the only hosts its sub-frames may load)
        "tools": student_tools(exam),
        **({"finish_url_regex": exam.get("finish_url_regex"), "external_kind": exam.get("external_kind"),
            "canvas": exam.get("canvas")} if external else {}),
        **({"canvas_scope": canvas_scope_of(exam)}
           if external and exam.get("external_kind") == "canvas" else {}),
        "approval": approval_of(a),    # S3: "pending" -> the page shows "Wait for your teacher"
        "trial": bool(exam.get("is_trial")),   # 2026-10-22: the sample exam (Stop practice)
        # integrations: "fairplay" or the external system's id (+ its display name)
        "source": exam.get("source") or "fairplay",
        **({"external_name": exam.get("external_name"), "total_points": exam.get("total_points")}
           if (exam.get("source") or "fairplay") != "fairplay" else {}),
    }


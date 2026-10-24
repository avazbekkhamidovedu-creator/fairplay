"""DKU OJ package question <-> FairPlay question, and FairPlay answers -> DKU OJ answers.

FairPlay form of an OJ question (stored in Exam.questions; contract §5). Only the fields
named here are copied, so nothing else DKU OJ might send is ever stored or served:

  common   id ("q_" + OJ id), type, prompt, prompt_format ("markdown" | "html"), title,
           label, points, partial, images [], topics, oj {question_id, order, ...}
  mcq      options [markdown], multiple, true_false, shuffle, external_ref.option_ids
           answer: int index (single) | sorted list of indexes (multiple)
  short    accepted_answers [] (graded in DKU OJ)            answer: string
  fill_blank  blanks (count), blank_points, case_sensitive   answer: list of strings
  file_upload allowed_extensions, max_file_size, instructions_md
                                                             answer: list of answer file ids
  code     languages [{key, name, runner}], templates {key: code}, tests (samples, visible),
           time_limit_ms, memory_limit_kb; language / starter_code = the first language
           (for pages that know only one language)           answer: {"language", "source"}
"""
from __future__ import annotations

import math
import re
from typing import Any

from ...grading import num
from ..sanitize import clean, clean_markdown

OJ_TYPES = ("choice", "fill_blank", "short_answer", "file_upload", "code")
FP_TYPE = {"choice": "mcq", "fill_blank": "fill_blank", "short_answer": "short",
           "file_upload": "file_upload", "code": "code"}
OJ_TYPE = {v: k for k, v in FP_TYPE.items()}

ASSET_SCHEME = "fp-asset:"
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
MAX_TEXT = 200_000


class AdapterError(ValueError):
    """The package cannot be used (message names the problem)."""


def runner_for(key: str) -> str | None:
    """OJ language key -> in-app runner (contract §5), None = runs only after hand-in."""
    k = (key or "").strip().upper()
    if k in ("PY3", "PYPY3"):
        return "python"
    if k in ("JS", "NODE", "V8JS"):
        return "javascript"
    if k in ("C", "C11"):
        return "c"
    if k.startswith("CPP"):
        return "cpp"
    if k.startswith("JAVA"):
        return "java"
    return None


def fp_question_id(oj_id: str, taken: set[str], salt: str = "") -> str:
    """Stable, opaque id: the same OJ question keeps its id across package versions, and
    students never see DKU OJ's problem code."""
    import hashlib
    base = "q_" + hashlib.sha256(f"dkuoj|{salt}|{oj_id}".encode()).hexdigest()[:16]
    qid, n = base, 2
    while qid in taken:
        qid = f"{base[:36]}_{n}"
        n += 1
    taken.add(qid)
    return qid


def _text(v: Any, limit: int = MAX_TEXT) -> str:
    return v[:limit] if isinstance(v, str) else ""


def _points(v: Any) -> int | float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise AdapterError("a question has no usable points") from None
    if not math.isfinite(f) or f < 0:
        raise AdapterError("a question has no usable points")
    return num(f)


def question_from_oj(q: dict, taken: set[str] | None = None, topic: str = "general",
                     salt: str = "") -> dict:
    if not isinstance(q, dict):
        raise AdapterError("a question is not an object")
    t = q.get("type")
    if t not in OJ_TYPES:
        raise AdapterError(f"question type {t!r} is not supported")
    oj_id = q.get("id")
    if not isinstance(oj_id, str) or not oj_id:
        raise AdapterError("a question has no id")
    md = q.get("statement_md")
    html = q.get("statement_html")
    if isinstance(md, str):
        prompt, fmt = md, "markdown"
    elif isinstance(html, str):
        prompt, fmt = html, "html"
    else:
        prompt, fmt = "", "markdown"
    title = _text(q.get("title"), 300)
    out: dict = {
        "id": fp_question_id(oj_id, taken if taken is not None else set(), salt),
        # security review 2026-10-24: cleaned on arrival (app.integrations.sanitize)
        "type": FP_TYPE[t], "prompt": clean(_text(prompt), fmt) or title or oj_id, "prompt_format": fmt,
        "title": title, "label": _text(q.get("label"), 50),
        "points": _points(q.get("points", 0)), "partial": bool(q.get("partial")),
        "options": [], "correct_index": None, "answer_number": None, "tolerance": None,
        "accepted_answers": [], "rubric": [], "min_words": None, "max_words": None,
        "images": [], "topics": [topic], "answer_lines": None,
        "external_ref": {"question_id": oj_id, "order": q.get("order"), "type": t},
    }
    if t == "choice":
        c = q.get("choice") or {}
        opts = [o for o in c.get("options") or [] if isinstance(o, dict)]
        if len(opts) < 2:
            raise AdapterError(f"choice question {oj_id} has fewer than 2 options")
        out["options"] = [clean_markdown(_text(o.get("text_md"), 20_000)) for o in opts]
        out["multiple"] = bool(c.get("multiple"))
        out["true_false"] = bool(c.get("true_false"))
        out["shuffle"] = bool(c.get("shuffle"))
        out["external_ref"]["option_ids"] = [o.get("id") for o in opts]
    elif t == "fill_blank":
        f = q.get("fill_blank") or {}
        blanks = sorted((b for b in f.get("blanks") or [] if isinstance(b, dict)),
                        key=lambda b: b.get("index", 0))
        if not blanks:
            raise AdapterError(f"fill-in question {oj_id} has no blanks")
        out["blanks"] = len(blanks)
        out["blank_points"] = [_points(b.get("points", 0)) for b in blanks]
        out["case_sensitive"] = bool(f.get("case_sensitive"))
    elif t == "short_answer":
        pass
    elif t == "file_upload":
        f = q.get("file_upload") or {}
        exts = [str(x).lower().lstrip(".")[:16] for x in f.get("allowed_extensions") or []
                if isinstance(x, str) and x.strip()]
        out["allowed_extensions"] = exts
        try:
            size = int(f.get("max_file_size") or 0)
        except (TypeError, ValueError):
            size = 0
        out["max_file_size"] = max(0, min(size, 10 * 1024 * 1024)) or 10 * 1024 * 1024
        out["instructions_md"] = clean_markdown(_text(f.get("instructions_md"), 20_000))
        out["max_files"] = 1          # DKU OJ files exactly one upload per question
    elif t == "code":
        c = q.get("code") or {}
        langs = []
        for lang in c.get("languages") or []:
            if isinstance(lang, dict) and isinstance(lang.get("key"), str) and lang["key"]:
                langs.append({"key": lang["key"][:32], "name": _text(lang.get("name"), 100) or lang["key"],
                              "runner": runner_for(lang["key"])})
        if not langs:
            raise AdapterError(f"code question {oj_id} allows no language")
        templates = {k[:32]: _text(v, 20_000) for k, v in (c.get("templates") or {}).items()
                     if isinstance(k, str) and isinstance(v, str)}
        samples = [s for s in c.get("samples") or [] if isinstance(s, dict)]
        try:
            tl = float(c.get("time_limit_s") or 2.0)
        except (TypeError, ValueError):
            tl = 2.0
        try:
            mem = int(c.get("memory_limit_kb") or 0) or None
        except (TypeError, ValueError):
            mem = None
        first = langs[0]
        out.update({
            "languages": langs, "templates": templates,
            "tests": [{"input": _text(s.get("input"), 20_000), "expected": _text(s.get("output"), 20_000),
                       "visible": True, "points": 0} for s in samples],
            "time_limit_ms": int(round(tl * 1000)) if math.isfinite(tl) and tl > 0 else 2000,
            "memory_limit_kb": mem,
            "language": first["runner"], "starter_code": templates.get(first["key"], ""),
            "write_only": False,
        })
    return out


def questions_from_package(pkg: dict, topic: str = "general") -> list[dict]:
    if not isinstance(pkg, dict) or not isinstance(pkg.get("questions"), list):
        raise AdapterError("the package has no question list")
    qs = sorted((q for q in pkg["questions"]), key=lambda q: (q.get("order") or 0) if isinstance(q, dict) else 0)
    taken: set[str] = set()
    salt = str(pkg.get("key") or "")
    out = [question_from_oj(q, taken, topic, salt) for q in qs]
    if not out:
        raise AdapterError("the package has no questions")
    return out


def question_to_oj(fq: dict) -> dict:
    """FairPlay form back to the OJ package form (round trip; used by tests and by the
    teacher view to show what came from DKU OJ)."""
    oj = fq.get("external_ref") or {}
    t = OJ_TYPE[fq["type"]]
    q: dict = {"id": oj.get("question_id"), "order": oj.get("order"), "label": fq.get("label", ""),
               "title": fq.get("title", ""), "type": t, "points": fq.get("points"),
               "partial": bool(fq.get("partial")),
               "statement_md": fq["prompt"] if fq.get("prompt_format") != "html" else None,
               "statement_html": fq["prompt"] if fq.get("prompt_format") == "html" else None}
    if t == "choice":
        q["choice"] = {"multiple": bool(fq.get("multiple")), "true_false": bool(fq.get("true_false")),
                       "shuffle": bool(fq.get("shuffle")),
                       "options": [{"id": i, "text_md": text}
                                   for i, text in zip(oj.get("option_ids") or [], fq.get("options") or [])]}
    elif t == "fill_blank":
        q["fill_blank"] = {"blanks": [{"index": i, "points": p} for i, p in enumerate(fq.get("blank_points") or [])],
                           "case_sensitive": bool(fq.get("case_sensitive"))}
    elif t == "short_answer":
        q["short_answer"] = {}
    elif t == "file_upload":
        q["file_upload"] = {"allowed_extensions": list(fq.get("allowed_extensions") or []),
                            "max_file_size": fq.get("max_file_size"),
                            "instructions_md": fq.get("instructions_md", "")}
    elif t == "code":
        tl = (fq.get("time_limit_ms") or 2000) / 1000
        q["code"] = {"languages": [{"key": lang["key"], "name": lang["name"]} for lang in fq.get("languages") or []],
                     "time_limit_s": num(tl), "memory_limit_kb": fq.get("memory_limit_kb"),
                     "templates": dict(fq.get("templates") or {}),
                     "samples": [{"input": s["input"], "output": s["expected"]} for s in fq.get("tests") or []]}
    return q


def asset_names(pkg: dict) -> list[dict]:
    out = []
    for a in pkg.get("assets") or []:
        if not isinstance(a, dict):
            continue
        name, sha = a.get("name"), a.get("sha256")
        if not isinstance(name, str) or not ASSET_NAME_RE.match(name) or name.startswith("."):
            raise AdapterError(f"asset name {name!r} is not usable")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            raise AdapterError(f"asset {name} has no sha256")
        out.append({"name": name, "sha256": sha.lower(), "bytes": a.get("bytes"),
                    "content_type": a.get("content_type") or ""})
    return out


# ---------------------------------------------------------------- answers
def answer_to_oj(q: dict, v: Any, uploads: dict[str, str] | None = None) -> dict | None:
    """One answer in DKU OJ's submission form, or None when not answered."""
    if v is None:
        return None
    oj = q.get("external_ref") or {}
    out: dict = {"question_id": oj.get("question_id")}
    t = q.get("type")
    if t == "mcq":
        ids = oj.get("option_ids") or []
        picked = v if isinstance(v, list) else [v]
        sel = sorted({ids[i] for i in picked if isinstance(i, int) and 0 <= i < len(ids)},
                     key=lambda x: (str(type(x)), x))
        if not sel:
            return None
        out["choice"] = {"selected": sel}
    elif t == "short":
        if not isinstance(v, str) or not v.strip():
            return None
        out["short_answer"] = {"text": v}
    elif t == "fill_blank":
        vals = [x if isinstance(x, str) else "" for x in (v if isinstance(v, list) else [])]
        if not any(x.strip() for x in vals):
            return None
        n = int(q.get("blanks") or len(vals))
        out["fill_blank"] = {"answers": (vals + [""] * n)[:n]}
    elif t == "file_upload":
        ids = [(uploads or {}).get(x) for x in (v if isinstance(v, list) else [])]
        ids = [x for x in ids if x]
        if not ids:
            return None
        out["file_upload"] = {"upload_ids": ids}
    elif t == "code":
        if isinstance(v, str):
            v = {"language": ((q.get("languages") or [{}])[0]).get("key", ""), "source": v}
        if not isinstance(v, dict) or not str(v.get("source") or "").strip():
            return None
        out["code"] = {"language": v.get("language"), "source": v.get("source")}
    else:
        return None
    return out


def answer_from_oj(q: dict, a: dict) -> Any:
    """DKU OJ's answer form back to the FairPlay answer value (round trip in tests)."""
    t = q.get("type")
    if t == "mcq":
        ids = (q.get("external_ref") or {}).get("option_ids") or []
        idx = sorted(ids.index(x) for x in a["choice"]["selected"] if x in ids)
        return idx if q.get("multiple") else (idx[0] if idx else None)
    if t == "short":
        return a["short_answer"]["text"]
    if t == "fill_blank":
        return list(a["fill_blank"]["answers"])
    if t == "file_upload":
        return list(a["file_upload"]["upload_ids"])
    if t == "code":
        return dict(a["code"])
    return None

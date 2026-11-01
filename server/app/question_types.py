"""Extended FairPlay question types and answers (2026-10-23).

Beyond the original fields, a question may carry (today only exams from an integration
have them; FairPlay's own editor can use them later):

  common       prompt_format "markdown" (FairPlay markdown: tables, fenced code, math as
               ~...~ inline and $$...$$ display, images as fp-asset:<name>) | "html"
               (sanitized), title, label, partial (partial credit)
  mcq          multiple (checkboxes), true_false (two big options), shuffle
               answer: int index; with multiple: sorted list of indexes
  fill_blank   blanks (count), blank_points, case_sensitive     answer: list of strings
  file_upload  allowed_extensions, max_file_size, max_files (default 1), instructions_md
                                                                answer: list of answer file ids
                                                                (at most max_files)
  code         languages [{key, name, runner}] (runner = in-app runner or null: runs after
               hand-in), templates {key: code}, memory_limit_kb  answer: {"language", "source"}

Questions without these fields keep the original rules (numbers, strings or null).
`external_ref` (the external system's ids) is teacher-only.
"""
from __future__ import annotations

from typing import Any

COMMON_FIELDS = ("prompt_format", "title", "label", "partial")
TYPE_FIELDS = {
    "mcq": ("multiple", "true_false", "shuffle"),
    "fill_blank": ("blanks", "blank_points", "case_sensitive"),
    "file_upload": ("allowed_extensions", "max_file_size", "max_files", "instructions_md"),
    "code": ("languages", "templates", "memory_limit_kb", "default_language"),
    "short": (),
}
NEW_TYPES = ("fill_blank", "file_upload")


def is_extended(q: dict) -> bool:
    return "prompt_format" in q or q.get("type") in NEW_TYPES


def _copy(v):
    if isinstance(v, list):
        return [dict(x) if isinstance(x, dict) else x for x in v]
    return dict(v) if isinstance(v, dict) else v


def extended_fields(q: dict, teacher: bool) -> dict:
    """{} for a plain question; else the extended fields (+ external_ref for teachers)."""
    if not is_extended(q):
        return {}
    out = {f: q.get(f) for f in COMMON_FIELDS}
    out["prompt_format"] = out["prompt_format"] if out["prompt_format"] in ("markdown", "html") else "markdown"
    out["partial"] = bool(out["partial"])
    for f in TYPE_FIELDS.get(q.get("type"), ()):
        out[f] = _copy(q.get(f))
    if teacher and isinstance(q.get("external_ref"), dict):
        out["external_ref"] = _copy(q["external_ref"])
    return out


class AnswerError(ValueError):
    pass


def check_answer(q: dict, v: Any, file_ids: set[str] | None = None) -> Any:
    """Validate (and normalise) one answer to an extended question; raises AnswerError.
    None (cleared answer) is always fine. file_ids: the attempt's own files for this
    question (None = don't check ownership)."""
    if v is None:
        return None
    t = q.get("type")
    if t == "mcq":
        n = len(q.get("options") or [])

        def idx(x):
            if isinstance(x, bool) or not isinstance(x, (int, float)) or float(x) != int(x) \
                    or not 0 <= int(x) < n:
                raise AnswerError("choose one of the options")
            return int(x)
        if q.get("multiple"):
            if not isinstance(v, list):
                raise AnswerError("this question takes a list of options")
            return sorted({idx(x) for x in v})
        if isinstance(v, list):
            raise AnswerError("this question takes one option")
        return idx(v)
    if t in ("short", "essay"):
        if not isinstance(v, str):
            raise AnswerError("this question takes text")
        return v
    if t == "numeric":
        if isinstance(v, (list, dict)):
            raise AnswerError("this question takes a number")
        return v
    if t == "fill_blank":
        if not isinstance(v, list) or len(v) > int(q.get("blanks") or 0) \
                or any(x is not None and not isinstance(x, str) for x in v):
            raise AnswerError(f"this question takes up to {q.get('blanks')} answers")
        if any(len(x or "") > 2000 for x in v):
            raise AnswerError("a blank's answer is too long")
        return ["" if x is None else x for x in v]
    if t == "file_upload":
        most = max(1, min(int(q.get("max_files") or 1), 10))
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            raise AnswerError("this question takes a list of uploaded files")
        if len(dict.fromkeys(v)) > most:
            raise AnswerError("this question takes one file" if most == 1 else f"this question takes up to {most} files")
        if file_ids is not None and any(x not in file_ids for x in v):
            raise AnswerError("upload the file first")
        return list(dict.fromkeys(v))
    if t == "code":
        keys = [lang["key"] for lang in q.get("languages") or []]
        if not keys:
            if not isinstance(v, str):
                raise AnswerError("this question takes the program text")
            return v
        if isinstance(v, str):          # a page that knows only one language
            return {"language": keys[0], "source": v}
        if not isinstance(v, dict) or set(v) - {"language", "source"} \
                or not isinstance(v.get("source"), str) or v.get("language") not in keys:
            raise AnswerError("this question takes {\"language\", \"source\"} with one of its languages")
        return {"language": v["language"], "source": v["source"]}
    raise AnswerError("unknown question type")

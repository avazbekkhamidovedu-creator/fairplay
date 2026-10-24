"""2026-10-24: what a student may see of an exam after handing it in.

Set per exam by its source (DKU OJ: the professor's "Student review after hand-in" on the
assignment's FairPlay tab; docs/dkuoj-bridge-v2.md §3) and enforced by the student endpoints:

- "nothing": only "Handed in" (no answers, no score, no review page: 403 REVIEW_CLOSED);
- "answers": the student's own answers, read-only (no points, correct answers or feedback);
- "answers_and_results": their answers, plus points and feedback once the results are
  released.

FairPlay-native exams have no such setting: they behave as "answers_and_results" (the
teacher's "release grades" decides, as before).
"""
from __future__ import annotations

NOTHING = "nothing"
ANSWERS = "answers"
ALL = "answers_and_results"
LEVELS = (NOTHING, ANSWERS, ALL)

REVIEW_CLOSED = "Your teacher has not opened the review for this exam."


def review_level(source: str | None, value: str | None) -> str:
    """The level that applies to an exam of `source` with the stored `value`."""
    from .integrations.registry import integration_for
    integ = integration_for({"source": source})
    if integ is None:
        return ALL
    return integ.review_level(value)


def shows_answers(level: str) -> bool:
    return level in (ANSWERS, ALL)


def shows_results(level: str) -> bool:
    return level == ALL

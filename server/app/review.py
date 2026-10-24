"""2026-10-24: what a student may see of an exam after handing it in.

Set per exam by its source (DKU OJ: the professor's "Student review after hand-in" on the
assignment's FairPlay tab; docs/dkuoj-bridge-v2.md §8) and enforced by the student endpoints.
Five levels (2026-10-24 second set; the first three values kept their meaning):

- "nothing": only "Handed in" (no answers, no score, no review page: 403 REVIEW_CLOSED);
- "score": the total score / percent once the result is released; no questions, no
  per-question points, no answers, no feedback;
- "breakdown": the total plus the points per question (number / title, points earned and
  possible) once released; no question text, no answers, no correct answers, no feedback;
- "answers": the student's own answers, read-only (no points, correct answers or feedback);
- "answers_and_results": everything: their answers, plus the total, the points per question,
  the correct answers (when the source has them) and the feedback once released.

FairPlay-native exams have no such setting: they behave as "answers_and_results" (the
teacher's "release grades" decides, as before).
"""
from __future__ import annotations

NOTHING = "nothing"
SCORE = "score"
BREAKDOWN = "breakdown"
ANSWERS = "answers"
ALL = "answers_and_results"
LEVELS = (NOTHING, SCORE, BREAKDOWN, ANSWERS, ALL)

REVIEW_CLOSED = "Your teacher has not opened the review for this exam."


def review_level(source: str | None, value: str | None) -> str:
    """The level that applies to an exam of `source` with the stored `value`."""
    from .integrations.registry import integration_for
    integ = integration_for({"source": source})
    if integ is None:
        return ALL
    return integ.review_level(value)


def shows_answers(level: str) -> bool:
    """The questions (text, pictures) and the student's own answers."""
    return level in (ANSWERS, ALL)


def shows_score(level: str) -> bool:
    """The total score / percent, once the result is released."""
    return level in (SCORE, BREAKDOWN, ALL)


def shows_breakdown(level: str) -> bool:
    """Points per question (earned / possible), once released."""
    return level in (BREAKDOWN, ALL)


def shows_results(level: str) -> bool:
    """Everything: points next to the answers, correct answers and feedback."""
    return level == ALL


def opens_detail(level: str) -> bool:
    """GET /api/student/grades/{exam_id} answers (anything but "nothing")."""
    return level != NOTHING

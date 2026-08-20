"""Student AI grading quota and per-problem rate limiting.

Students get a global request quota (for usage monitoring) plus a sliding
per-problem rate limit: at most ``_RATE_LIMIT_COUNT`` calls in
``_RATE_WINDOW_SECONDS``. Hitting the limit returns “try again later”; there
is no separate cooldown / ban — the window keeps counting.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_RATE_WINDOW_SECONDS = 300
_RATE_LIMIT_COUNT = 5


def _quota_key(user_id: int) -> str:
    return f'ai_grade_quota:{user_id}'


def _rate_key(user_id: int, problem_id: int) -> str:
    return f'ai_grade_rate:{user_id}:{problem_id}'


def student_quota_limit() -> int:
    return int(getattr(settings, 'DMOJ_AI_GRADING_STUDENT_QUOTA', 0))


def is_teacher_viewer(user, submission) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if user.is_staff or user.has_perm('judge.rejudge_submission'):
        return True
    try:
        contest = submission.contest_object
        if contest is not None and contest.is_editable_by(user):
            return True
    except Exception:
        pass
    return False


def _recent_timestamps(user_id: int, problem_id: int) -> list[float]:
    now = time.time()
    timestamps = list(cache.get(_rate_key(user_id, problem_id)) or [])
    return [t for t in timestamps if now - t < _RATE_WINDOW_SECONDS]


def is_rate_limited(user_id: int, problem_id: int) -> bool:
    """True when this user already hit 5 AI grades for the problem in 5 minutes."""
    return len(_recent_timestamps(user_id, problem_id)) >= _RATE_LIMIT_COUNT


def rate_limit_remaining_seconds(user_id: int, problem_id: int) -> int:
    """Seconds until the oldest call in the window ages out (0 if not limited)."""
    recent = _recent_timestamps(user_id, problem_id)
    if len(recent) < _RATE_LIMIT_COUNT:
        return 0
    oldest = min(recent)
    return max(0, int(_RATE_WINDOW_SECONDS - (time.time() - oldest)) + 1)


def get_remaining(user_id: int) -> int:
    """How many AI grading requests this student still has (global quota)."""
    limit = student_quota_limit()
    if limit <= 0:
        return 0
    used = cache.get(_quota_key(user_id)) or 0
    return max(0, limit - used)


def try_reserve(user_id: int, problem_id: int) -> tuple[bool, str | None]:
    """Claim one AI grading slot; returns (ok, error_message)."""
    recent = _recent_timestamps(user_id, problem_id)
    if len(recent) >= _RATE_LIMIT_COUNT:
        secs = rate_limit_remaining_seconds(user_id, problem_id)
        return False, f'rate_limited:{secs}'

    limit = student_quota_limit()
    if limit <= 0:
        return False, 'quota_disabled'

    key = _quota_key(user_id)
    try:
        used = cache.incr(key)
    except ValueError:
        cache.set(key, 1, None)
        used = 1
    if used > limit:
        refund(user_id)
        return False, 'quota_exhausted'

    now = time.time()
    recent.append(now)
    cache.set(_rate_key(user_id, problem_id), recent, _RATE_WINDOW_SECONDS)
    return True, None


def refund(user_id: int) -> None:
    try:
        cache.decr(_quota_key(user_id))
    except ValueError:
        pass

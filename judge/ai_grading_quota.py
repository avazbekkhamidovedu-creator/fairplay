"""Student AI grading quota and per-problem rate limiting."""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_RATE_WINDOW_SECONDS = 300
_RATE_LIMIT_COUNT = 5
_COOLDOWN_SECONDS = 300


def _quota_key(user_id: int) -> str:
    return f'ai_grade_quota:{user_id}'


def _rate_key(user_id: int, problem_id: int) -> str:
    return f'ai_grade_rate:{user_id}:{problem_id}'


def _cooldown_key(user_id: int, problem_id: int) -> str:
    return f'ai_grade_cooldown:{user_id}:{problem_id}'


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


def is_rate_limited(user_id: int, problem_id: int) -> bool:
    return bool(cache.get(_cooldown_key(user_id, problem_id)))


def rate_limit_remaining_seconds(user_id: int, problem_id: int) -> int:
    until = cache.get(_cooldown_key(user_id, problem_id))
    if not until:
        return 0
    try:
        return max(0, int(float(until) - time.time()))
    except (TypeError, ValueError):
        return _COOLDOWN_SECONDS


def get_remaining(user_id: int) -> int:
    """How many AI grading requests this student still has (global quota)."""
    limit = student_quota_limit()
    if limit <= 0:
        return 0
    used = cache.get(_quota_key(user_id)) or 0
    return max(0, limit - used)


def try_reserve(user_id: int, problem_id: int) -> tuple[bool, str | None]:
    """Claim one AI grading slot; returns (ok, error_message)."""
    if is_rate_limited(user_id, problem_id):
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

    timestamps = list(cache.get(_rate_key(user_id, problem_id)) or [])
    now = time.time()
    recent = [t for t in timestamps if now - t < _RATE_WINDOW_SECONDS]
    if len(recent) >= _RATE_LIMIT_COUNT:
        cache.set(_cooldown_key(user_id, problem_id), time.time() + _COOLDOWN_SECONDS, _COOLDOWN_SECONDS)
        refund(user_id)
        return False, f'rate_limited:{_COOLDOWN_SECONDS}'
    recent.append(now)
    cache.set(_rate_key(user_id, problem_id), recent, _RATE_WINDOW_SECONDS)
    if len(recent) >= _RATE_LIMIT_COUNT:
        cache.set(_cooldown_key(user_id, problem_id), time.time() + _COOLDOWN_SECONDS, _COOLDOWN_SECONDS)
    return True, None


def refund(user_id: int) -> None:
    try:
        cache.decr(_quota_key(user_id))
    except ValueError:
        pass

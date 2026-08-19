"""Contest-level AI grading: realtime vs batch, latest-submission only.

Teachers pick a mode when creating an assignment/exam:

* ``realtime`` — students (and teachers) request AI grading via a button on the
  submission page; nothing is sent to the LLM until they click it.
* ``batch`` — do not call the LLM during the contest; a teacher button (and an
  optional timer after ``end_time``) grades **only the latest** submission per
  (participation, problem).
* ``off`` — contest submissions never invoke AI grading, even if the problem
  has AI enabled.

Practice submissions (no contest) keep the problem-level behaviour.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

AI_BATCH_PENDING = 'AI_BATCH_PENDING'


def problem_has_ai_grading(problem):
    """True when this problem type would invoke the LLM grader."""
    from judge.models import ProblemSubmissionType
    from judge.models.question import GRADE_AI

    st = problem.submission_type
    if st == ProblemSubmissionType.SHORT_ANSWER:
        return True
    if st == ProblemSubmissionType.FILL_BLANK:
        try:
            return problem.fill_blank_config.grading_mode == GRADE_AI
        except Exception:
            return False
    if st == ProblemSubmissionType.CODE:
        try:
            return bool(problem.code_config.ai_enabled)
        except Exception:
            return False
    return False


def contest_ai_mode(contest):
    if contest is None:
        return None
    return getattr(contest, 'ai_grading_mode', None) or 'off'


def should_run_ai_now(submission, *, batch_pass=False, manual_trigger=False):
    """Whether this submission should hit the LLM *right now*.

    Submit never auto-invokes the model — only ``manual_trigger`` (student/teacher
    button) or ``batch_pass`` (teacher batch job) may run AI grading.
    """
    if not problem_has_ai_grading(submission.problem):
        return False
    if batch_pass or getattr(submission, '_ai_batch_pass', False):
        return True
    if manual_trigger:
        return True
    return False


def contest_awaits_student_ai(submission):
    """Submit stores the answer; the LLM runs only when the student presses the button."""
    if not problem_has_ai_grading(submission.problem):
        return False
    contest = getattr(submission, 'contest_object', None)
    if contest is None:
        return True
    return contest_ai_mode(contest) == 'realtime'


def contest_defers_ai(submission):
    contest = getattr(submission, 'contest_object', None)
    return contest_ai_mode(contest) == 'batch' and problem_has_ai_grading(submission.problem)


def is_latest_submission(submission):
    """Latest by date (then id) for the same user, problem, and contest (or none)."""
    from judge.models import Submission

    qs = Submission.objects.filter(user_id=submission.user_id, problem_id=submission.problem_id)
    if submission.contest_object_id:
        qs = qs.filter(contest_object_id=submission.contest_object_id)
    else:
        qs = qs.filter(contest_object__isnull=True)
    latest_id = qs.order_by('-date', '-id').values_list('id', flat=True).first()
    return latest_id == submission.id


def supersede_older_in_flight(new_submission):
    """Abort older queued/grading attempts so only the newest submit is judged.

    Used for realtime AI (homework): if the previous code/answer is still in
    the queue, drop it and grade the new one.
    """
    from judge.models import ProblemSubmissionType, Submission

    contest = getattr(new_submission, 'contest_object', None)
    mode = contest_ai_mode(contest)
    # Practice + realtime contests: cancel in-flight older attempts.
    if not problem_has_ai_grading(new_submission.problem):
        return 0
    if contest is not None and mode != 'realtime':
        return 0

    qs = Submission.objects.filter(
        user_id=new_submission.user_id,
        problem_id=new_submission.problem_id,
        status__in=('QU', 'P', 'G'),
    ).exclude(id=new_submission.id)
    if new_submission.contest_object_id:
        qs = qs.filter(contest_object_id=new_submission.contest_object_id)
    else:
        qs = qs.filter(contest_object__isnull=True)

    aborted = 0
    for old in qs:
        try:
            # Internal (short-answer / fill-blank) AI never sits on the judge bridge.
            if old.problem.submission_type != ProblemSubmissionType.CODE:
                Submission.objects.filter(id=old.id, status__in=('QU', 'P', 'G')).update(
                    status='AB', result='AB',
                )
            else:
                old.abort()
        except Exception:
            logger.exception('Could not abort superseded submission %s', old.id)
            Submission.objects.filter(id=old.id, status__in=('QU', 'P', 'G')).update(
                status='AB', result='AB',
            )
        aborted += 1
    return aborted


def mark_awaiting_batch_ai(submission):
    """Store a finished-looking row that the batch job will overwrite."""
    submission.status = 'D'
    submission.result = 'IE'
    submission.error = AI_BATCH_PENDING
    submission.case_points = 0
    submission.case_total = float(submission.problem.points or 0)
    submission.save(update_fields=['status', 'result', 'error', 'case_points', 'case_total'])


def persist_code_ai_rows(submission, rows):
    """Write supplementary AI rubric rows (batch=-1) for a code submission."""
    from judge.models import SubmissionTestCase
    from judge.question_types.code import AI_GRADE_BATCH

    if not rows:
        return 0
    SubmissionTestCase.objects.filter(submission=submission, batch=AI_GRADE_BATCH).delete()
    SubmissionTestCase.objects.bulk_create([
        SubmissionTestCase(
            submission=submission, case=r['case'], status=r['status'],
            time=0, memory=0, points=r['points'], total=r['total'],
            batch=r.get('batch', AI_GRADE_BATCH), feedback=r.get('feedback', ''),
            extended_feedback=r.get('extended_feedback', ''),
        )
        for r in rows
    ])
    return len(rows)


def run_ai_for_submission(submission, *, batch_pass=False, manual_trigger=False):
    """Grade one submission with AI (code rubric or internal LLM types).

    Returns a short status string. Skips (and does not error) when this is no
    longer the latest submit for that user/problem.
    """
    from judge.models import ProblemSubmissionType
    from judge.question_types.code import supplementary_ai_grade

    if not is_latest_submission(submission):
        return 'skipped-not-latest'
    if not problem_has_ai_grading(submission.problem):
        return 'skipped-no-ai'
    if not should_run_ai_now(submission, batch_pass=batch_pass, manual_trigger=manual_trigger):
        return 'skipped-mode'

    st = submission.problem.submission_type
    if st == ProblemSubmissionType.CODE:
        # Only grade rubric rows once the judge has finished (status D ⇒ test
        # cases are written). Grading a queued/running submission computes the
        # case offset before the real rows exist, colliding with them later on
        # SubmissionTestCase's unique (submission, case) constraint.
        if submission.status != 'D':
            return 'skipped-not-graded'
        rows = supplementary_ai_grade(submission)
        if not rows:
            return 'no-rows'
        persist_code_ai_rows(submission, rows)
        _notify_status(submission)
        return 'code-ai'
    submission._ai_batch_pass = True
    submission.judge(rejudge=True, force_judge=True)
    return 'internal-ai'


def _notify_status(submission):
    try:
        from judge import event_poster as event
        from judge.models import Submission as SubModel
        event.post('sub_%s' % SubModel.get_id_secret(submission.id), {'type': 'grading-end'})
    except Exception:
        logger.exception('Could not post grading-end for submission %s', submission.id)


def latest_submissions_for_contest(contest):
    """Map (participation_id, contest_problem_id) → latest Submission."""
    from judge.models import ContestSubmission

    latest = {}
    qs = (ContestSubmission.objects
          .filter(participation__contest=contest)
          .select_related('submission', 'submission__problem')
          .order_by('submission__date', 'submission_id'))
    for cs in qs:
        latest[(cs.participation_id, cs.problem_id)] = cs.submission
    return latest


def student_can_see_code_ai(user, submission):
    """Whether the supplementary code-AI table is visible to this viewer."""
    if user is None or not user.is_authenticated:
        return False
    if not submission.results_visible_to(user):
        return False
    is_owner = False
    try:
        is_owner = submission.user.user_id == user.id
    except Exception:
        is_owner = False
    is_staff_view = user.is_staff or user.has_perm('judge.see_private_submission')
    try:
        contest = submission.contest_object
        if contest is not None and contest.is_editable_by(user):
            is_staff_view = True
    except Exception:
        pass
    if is_staff_view:
        return True
    if not is_owner:
        return False
    contest = getattr(submission, 'contest_object', None)
    mode = contest_ai_mode(contest)
    if contest is None or mode == 'realtime':
        return True
    if mode == 'batch':
        return bool(getattr(contest, 'ai_grading_batch_finished_at', None))
    return False


def student_can_trigger_ai(user, submission):
    """Show the on-demand AI grading button (teachers always; students when quota allows)."""
    from judge.ai_grading_quota import get_remaining, is_rate_limited, is_teacher_viewer

    if user is None or not user.is_authenticated:
        return False
    if not problem_has_ai_grading(submission.problem):
        return False
    try:
        is_owner = submission.user.user_id == user.id
    except Exception:
        is_owner = False
    teacher = is_teacher_viewer(user, submission)
    if not is_owner and not teacher:
        return False
    if submission.status != 'D':
        return False
    if not is_latest_submission(submission):
        return False

    contest = getattr(submission, 'contest_object', None)
    mode = contest_ai_mode(contest)
    if contest is not None and mode == 'off':
        return False
    if contest is not None and mode == 'batch' and not teacher:
        return False
    if teacher:
        return True
    if contest is None or mode == 'realtime':
        if student_quota_disabled():
            return False
        try:
            profile_id = submission.user_id
        except Exception:
            return False
        if get_remaining(profile_id) <= 0:
            return False
        if is_rate_limited(profile_id, submission.problem_id):
            return False
        return True
    return False


def student_quota_disabled():
    from judge.ai_grading_quota import student_quota_limit
    return student_quota_limit() <= 0


def ai_grade_context(user, submission):
    """Template/API context for the on-demand AI grading button."""
    from judge.ai_grading_quota import (
        get_remaining, is_rate_limited, is_teacher_viewer, rate_limit_remaining_seconds,
    )

    ctx = {
        'can_trigger': student_can_trigger_ai(user, submission),
        'is_teacher': is_teacher_viewer(user, submission),
        'remaining': 0,
        'rate_limited': False,
        'rate_limit_seconds': 0,
        'quota_disabled': student_quota_disabled() and not is_teacher_viewer(user, submission),
    }
    if not ctx['is_teacher'] and user is not None and user.is_authenticated:
        try:
            profile_id = submission.user_id
        except Exception:
            profile_id = None
        if profile_id is not None:
            ctx['remaining'] = get_remaining(profile_id)
            ctx['rate_limited'] = is_rate_limited(profile_id, submission.problem_id)
            ctx['rate_limit_seconds'] = rate_limit_remaining_seconds(profile_id, submission.problem_id)
    return ctx


def schedule_batch_ai_grade(contest_id):
    """(Re)schedule the post-end auto batch, or clear it when mode is not batch."""
    from django.conf import settings

    from judge.models import Contest

    contest = Contest.objects.filter(pk=contest_id).first()
    if contest is None:
        return
    old_tid = contest.ai_grading_scheduled_task_id or ''
    if old_tid:
        try:
            from celery.result import AsyncResult
            AsyncResult(old_tid).revoke()
        except Exception:
            logger.exception('Could not revoke previous batch-AI task %s', old_tid)
        contest.ai_grading_scheduled_task_id = ''

    delay = int(contest.ai_grading_auto_delay_minutes or 0)
    if contest.ai_grading_mode != Contest.AI_GRADING_BATCH or delay <= 0:
        contest.save(update_fields=['ai_grading_scheduled_task_id'])
        return
    if contest.end_time is None:
        # No deadline to anchor the auto-run to; the teacher can still use the button.
        contest.save(update_fields=['ai_grading_scheduled_task_id'])
        return

    try:
        eta = contest.end_time + timedelta(minutes=delay)
        if eta <= timezone.now():
            contest.save(update_fields=['ai_grading_scheduled_task_id'])
            return
        if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            # Tests / eager workers would fire immediately; teacher still has the button.
            contest.save(update_fields=['ai_grading_scheduled_task_id'])
            return
        from judge.tasks.contest import batch_ai_grade
        result = batch_ai_grade.apply_async(args=[contest.key], kwargs={'auto': True}, eta=eta)
        contest.ai_grading_scheduled_task_id = result.id or ''
    except Exception:
        logger.exception('Could not schedule batch AI grade for contest %s', contest.key)
        contest.ai_grading_scheduled_task_id = ''
    contest.save(update_fields=['ai_grading_scheduled_task_id'])


def ai_mode_help_text():
    return _(
        'AI grading always uses each student’s latest submission per problem. '
        'On demand shows a button after submit; AI runs only when requested. '
        'Batch waits until you press the button on the Grade page, or until '
        'the delay after the contest ends.',
    )

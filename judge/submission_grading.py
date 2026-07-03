"""Shared submission-finalization logic.

``finalize_submission`` is the single source of truth for "a submission just reached a terminal
score": it computes the scaled points, persists them, and runs every downstream effect (user
points, problem stats, contest standings, cache invalidation, websocket events). It is used both
by the judge bridge (``JudgeHandler.on_grading_end``, for code) and by ``grade_internally`` (for
internally-graded question types such as multiple-choice), so the two paths stay in lockstep.
"""
from judge import event_poster as event
from judge.caching import finished_submission

__all__ = ['finalize_submission', 'grade_internally']


def finalize_submission(submission, *, case_points, case_total, result,
                        time=0.0, memory=0.0, status='D', error=None, save=True):
    """Persist a terminal grading outcome and propagate it everywhere code grading would.

    ``case_points``/``case_total`` are the raw per-case aggregate; the problem-scaled
    ``submission.points`` is derived here exactly as ``on_grading_end`` does (round to 3,
    all-or-nothing when the problem is not partial). Returns the scaled points.
    """
    problem = submission.problem
    sub_points = round(case_points / case_total * problem.points if case_total > 0 else 0, 3)
    if not problem.partial and sub_points != problem.points:
        sub_points = 0

    submission.case_points = case_points
    submission.case_total = case_total
    submission.status = status
    submission.result = result
    submission.time = time
    submission.memory = memory
    submission.points = sub_points
    if error is not None:
        submission.error = error
    if save:
        submission.save()

    if problem.is_public and not problem.is_organization_private:
        submission.user._updating_stats_only = True
        submission.user.calculate_points()

    problem._updating_stats_only = True
    problem.update_stats()
    submission.update_contest()

    finished_submission(submission)

    event.post('sub_%s' % submission.id_secret, {
        'type': 'grading-end',
        'time': time,
        'memory': memory,
        'points': float(case_points),
        'total': float(problem.points),
        'result': submission.result,
    })
    if hasattr(submission, 'contest'):
        participation = submission.contest.participation
        event.post('contest_%d' % participation.contest_id, {'type': 'update'})

    return sub_points


def grade_internally(submission):
    """Grade a non-code submission without the judge bridge, then finalize it.

    Looks up the registered grader for the problem's submission type, records any per-item
    feedback as ``SubmissionTestCase`` rows (so the status page renders normally), and calls
    ``finalize_submission`` with the grader's outcome.
    """
    from judge.models import SubmissionTestCase
    from judge.question_types import get_grader

    grader = get_grader(submission.problem.submission_type)
    outcome = grader.grade(submission)

    SubmissionTestCase.objects.filter(submission=submission).delete()
    if outcome.cases:
        SubmissionTestCase.objects.bulk_create([
            SubmissionTestCase(
                submission=submission, case=case['case'], status=case['status'],
                time=0, memory=0, points=case.get('points', 0), total=case.get('total', 0),
                feedback=case.get('feedback', '') or '',
            )
            for case in outcome.cases
        ])
    submission.current_testcase = len(outcome.cases)

    return finalize_submission(
        submission, case_points=outcome.case_points, case_total=outcome.case_total,
        result=outcome.result, status='D',
    )

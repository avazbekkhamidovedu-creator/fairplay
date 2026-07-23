"""Fill-in-the-blank question type: front-end authored, internally graded.

A problem has one or more blanks, each with a set of accepted answers (one alternative per line).
A student's answer to a blank is correct if — after normalizing per ``ProblemFillBlankConfig``
(strip surrounding whitespace unless disabled; casefold unless case-sensitive) — it equals any of
that blank's accepted answers. Scoring is +1 per correct blank out of N, mirroring MCQ.
"""
import json

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models import ProblemSubmissionType
from judge.question_types.base import GradingResult, QuestionType
from judge.question_types.registry import register


def _config(problem):
    try:
        return problem.fill_blank_config
    except ObjectDoesNotExist:
        return None


def _normalize(value, config):
    """Normalize an answer string for comparison per the problem's config."""
    if config is None or config.trim_whitespace:
        value = value.strip()
    if config is None or not config.case_sensitive:
        value = value.casefold()
    return value


def _accepted_set(blank, config):
    """The blank's accepted answers, normalized, as a set (blank lines dropped)."""
    result = set()
    for line in blank.answers.splitlines():
        normalized = _normalize(line, config)
        if normalized:
            result.add(normalized)
    return result


@register
class FillBlankQuestionType(QuestionType):
    submission_type = ProblemSubmissionType.FILL_BLANK
    label = _('Fill in the blank')
    order = 30

    # In an exam-style contest, hide results during the contest and grade by the latest answer,
    # for the same integrity reasons as MCQ (see MCQQuestionType).
    hide_results_in_contest = True
    contest_score_uses_latest = True

    # --- authoring / menu ---------------------------------------------------
    def can_create(self, user):
        return user.has_perm('judge.create_private_problem')

    def create_url(self):
        return reverse('problem_fillblank_create')

    def edit_url(self, problem):
        return reverse('problem_fillblank_edit', args=(problem.code,))

    # --- student submission -------------------------------------------------
    def build_form(self, problem, profile, data=None, files=None, initial=None):
        from judge.forms import FillBlankSubmitForm
        return FillBlankSubmitForm(data, problem=problem, initial=initial)

    def store_answer(self, submission, cleaned_data):
        from judge.models import SubmissionSource
        payload = json.dumps({'answers': list(cleaned_data['answers'])})
        source, _created = SubmissionSource.objects.update_or_create(
            submission=submission, defaults={'source': payload})
        submission.source = source  # prime the reverse relation for immediate grading

    # --- grading ------------------------------------------------------------
    def grade(self, submission):
        problem = submission.problem
        config = _config(problem)
        blanks = list(problem.blanks.all())

        try:
            answers = json.loads(submission.source.source).get('answers', [])
        except (ValueError, TypeError, ObjectDoesNotExist):
            answers = []

        cases = []
        case_points = 0
        for index, blank in enumerate(blanks):
            given = answers[index] if index < len(answers) else ''
            correct = _normalize(given, config) in _accepted_set(blank, config)
            if correct:
                case_points += 1
            cases.append({'case': index + 1, 'status': 'AC' if correct else 'WA',
                          'points': 1 if correct else 0, 'total': 1})

        case_total = len(blanks)
        # Always emit granular per-blank points; the all-or-nothing gating is applied downstream
        # by problem.partial (finalize_submission) and contest_problem.partial (update_contest),
        # exactly like code and MCQ problems.
        result = 'AC' if case_total and case_points == case_total else 'WA'
        return GradingResult(case_points=case_points, case_total=case_total, result=result, cases=cases)

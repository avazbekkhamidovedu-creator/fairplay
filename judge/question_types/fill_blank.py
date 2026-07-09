"""Fill-in-the-blank question type: front-end authored, internally graded.

A problem has one or more blanks, each with a set of accepted answers (one alternative per line).
In the default (exact) grading mode a student's answer to a blank is correct if — after normalizing
per ``ProblemFillBlankConfig`` (strip surrounding whitespace unless disabled; casefold unless
case-sensitive) — it equals any of that blank's accepted answers; scoring is +1 per correct blank
out of N, mirroring MCQ. When ``grading_mode == 'ai'`` the configured LLM scores the whole response
0-100 against the accepted answers + rubric, falling back to exact matching if no model is
configured or the call fails, so grading never blocks.
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
        # Fill-in-the-blank problems are authored/edited through the unified create_v2 page.
        return reverse('problem_edit_v2', args=(problem.code,))

    # --- student submission -------------------------------------------------
    def build_form(self, problem, profile, data=None, initial=None):
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
        from judge.models.question import GRADE_AI
        problem = submission.problem
        config = _config(problem)
        blanks = list(problem.blanks.all())

        try:
            answers = json.loads(submission.source.source).get('answers', [])
        except (ValueError, TypeError, ObjectDoesNotExist):
            answers = []

        # AI mode scores the whole response; fall back to exact matching if the model is unavailable
        # or errors, so a misconfigured LLM never leaves a submission ungraded.
        if config is not None and config.grading_mode == GRADE_AI:
            graded = self._grade_ai(problem, config, blanks, answers)
            if graded is not None:
                return graded
        return self._grade_exact(config, blanks, answers)

    def _grade_exact(self, config, blanks, answers):
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

    def _grade_ai(self, problem, config, blanks, answers):
        """Ask the configured LLM for an overall 0-100 score. Returns None to signal "fall back to
        exact matching" (no model configured, or the call/parse failed)."""
        from judge import llm
        if not llm.is_configured():
            return None
        try:
            payload = {
                'question': (problem.description or '')[:4000],
                'rubric': (config.ai_rubric if config else '') or '',
                'blanks': [
                    {'index': index + 1,
                     'accepted_answers': blank.answers.splitlines(),
                     'student_answer': (answers[index] if index < len(answers) else '')[:1000]}
                    for index, blank in enumerate(blanks)
                ],
            }
            messages = [
                {'role': 'system', 'content': (
                    'You grade fill-in-the-blank answers. Given the question, an optional rubric, and '
                    'for each blank its accepted answers and the student answer, reply with ONLY an '
                    'integer 0-100 for the overall percentage of credit earned. No other text.'
                )},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ]
            text = llm.chat(messages, max_tokens=16).text.strip()
            score = max(0, min(100, int(''.join(ch for ch in text if ch.isdigit()) or '0')))
        except Exception:
            return None

        result = 'AC' if score >= 100 else 'WA'
        cases = [{'case': 1, 'status': result, 'points': score, 'total': 100,
                  'feedback': _('Graded by AI.')}]
        return GradingResult(case_points=score, case_total=100, result=result, cases=cases)

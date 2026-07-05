"""Fill-in-the-blank question type: front-end authored, internally graded (exact match or AI).

Exact grading compares each blank to its author-provided accepted answers (``|``-separated
alternatives). AI grading defers to the configured LLM, scoring the whole response against the
rubric; if no LLM is configured it falls back to exact matching so grading never stalls.
"""
import json

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models import ProblemSubmissionType
from judge.models.question import GRADE_AI
from judge.question_types.base import GradingResult, QuestionType
from judge.question_types.registry import register


def _config(problem):
    try:
        return problem.fill_blank_config
    except ObjectDoesNotExist:
        return None


@register
class FillBlankQuestionType(QuestionType):
    submission_type = ProblemSubmissionType.FILL_BLANK
    label = _('Fill in the blank')
    order = 30

    hide_results_in_contest = True
    contest_score_uses_latest = True

    # --- authoring / menu ---------------------------------------------------
    def can_create(self, user):
        return user.has_perm('judge.create_private_problem')

    def create_url(self):
        return reverse('problem_create_v2')

    def edit_url(self, problem):
        return reverse('admin:judge_problem_change', args=(problem.id,))

    # --- student submission -------------------------------------------------
    def build_form(self, problem, profile, data=None, initial=None):
        from judge.forms import FillBlankSubmitForm
        return FillBlankSubmitForm(data, problem=problem, initial=initial)

    def store_answer(self, submission, cleaned_data):
        from judge.models import SubmissionSource
        payload = json.dumps({'blanks': cleaned_data.get('blanks', [])})
        source, _created = SubmissionSource.objects.update_or_create(
            submission=submission, defaults={'source': payload})
        submission.source = source

    # --- grading ------------------------------------------------------------
    def grade(self, submission):
        problem = submission.problem
        blanks = list(problem.blank_answers.order_by('order'))
        config = _config(problem)

        try:
            student = json.loads(submission.source.source).get('blanks', [])
        except (ValueError, TypeError, ObjectDoesNotExist):
            student = []

        if config is not None and config.grading_mode == GRADE_AI:
            graded = self._grade_ai(problem, config, blanks, student)
            if graded is not None:
                return graded

        return self._grade_exact(config, blanks, student)

    def _grade_exact(self, config, blanks, student):
        case_total = len(blanks) or 1
        case_sensitive = bool(config and config.case_sensitive)
        cases, case_points = [], 0
        for index, blank in enumerate(blanks):
            answer = student[index] if index < len(student) else ''
            accepted = blank.accepted_list()
            if not case_sensitive:
                answer_cmp = answer.strip().lower()
                accepted = [a.lower() for a in accepted]
            else:
                answer_cmp = answer.strip()
            ok = answer_cmp in accepted
            case_points += 1 if ok else 0
            cases.append({'case': index + 1, 'status': 'AC' if ok else 'WA',
                          'points': 1 if ok else 0, 'total': 1})
        if not blanks:
            cases = [{'case': 1, 'status': 'WA', 'points': 0, 'total': 1}]
        result = 'AC' if case_points == len(blanks) and blanks else 'WA'
        return GradingResult(case_points=case_points, case_total=case_total, result=result, cases=cases)

    def _grade_ai(self, problem, config, blanks, student):
        from judge import llm
        if not llm.is_configured():
            return None
        try:
            reference = [{'blank': i + 1, 'accepted': b.accepted_list()} for i, b in enumerate(blanks)]
            payload = {
                'question': problem.description[:4000],
                'reference_answers': reference,
                'student_answers': student,
                'rubric': config.ai_rubric,
            }
            messages = [
                {'role': 'system', 'content': (
                    'You grade fill-in-the-blank answers. Given the reference answers, the '
                    'student answers, and an optional rubric, reply with ONLY an integer 0-100 '
                    'representing the percentage of credit earned. No other text.'
                )},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ]
            text = llm.chat(messages, max_tokens=16).text.strip()
            score = int(''.join(ch for ch in text if ch.isdigit()) or '0')
            score = max(0, min(100, score))
            result = 'AC' if score >= 100 else 'WA'
            cases = [{'case': 1, 'status': result, 'points': score, 'total': 100,
                      'feedback': _('Graded by AI.')}]
            return GradingResult(case_points=score, case_total=100, result=result, cases=cases)
        except Exception:
            return None

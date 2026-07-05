"""Short-answer question type: front-end authored, graded by the configured LLM.

The model scores the student's free-text response against an author reference answer and rubric,
returning a 0-100 percentage that feeds partial credit exactly like code test-case points. If no
LLM is configured the submission is left at zero with feedback asking for manual review, so the
grading pipeline never blocks.
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
        return problem.short_answer_config
    except ObjectDoesNotExist:
        return None


@register
class ShortAnswerQuestionType(QuestionType):
    submission_type = ProblemSubmissionType.SHORT_ANSWER
    label = _('Short answer')
    order = 40

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
        from judge.forms import ShortAnswerSubmitForm
        return ShortAnswerSubmitForm(data, problem=problem, initial=initial)

    def store_answer(self, submission, cleaned_data):
        from judge.models import SubmissionSource
        payload = json.dumps({'text': cleaned_data.get('answer', '')})
        source, _created = SubmissionSource.objects.update_or_create(
            submission=submission, defaults={'source': payload})
        submission.source = source

    # --- grading ------------------------------------------------------------
    def grade(self, submission):
        problem = submission.problem
        config = _config(problem)

        try:
            answer = json.loads(submission.source.source).get('text', '')
        except (ValueError, TypeError, ObjectDoesNotExist):
            answer = ''

        score = self._grade_ai(problem, config, answer)
        if score is None:
            cases = [{'case': 1, 'status': 'WA', 'points': 0, 'total': 100,
                      'feedback': _('Awaiting manual grading (AI grader unavailable).')}]
            return GradingResult(case_points=0, case_total=100, result='WA', cases=cases)

        result = 'AC' if score >= 100 else 'WA'
        cases = [{'case': 1, 'status': result, 'points': score, 'total': 100,
                  'feedback': _('Graded by AI.')}]
        return GradingResult(case_points=score, case_total=100, result=result, cases=cases)

    def _grade_ai(self, problem, config, answer):
        from judge import llm
        if not llm.is_configured():
            return None
        try:
            payload = {
                'question': problem.description[:4000],
                'reference_answer': config.reference_answer if config else '',
                'rubric': config.ai_rubric if config else '',
                'student_answer': answer[:4000],
            }
            messages = [
                {'role': 'system', 'content': (
                    'You grade short free-text answers. Given the question, a reference answer, an '
                    'optional rubric, and the student answer, reply with ONLY an integer 0-100 '
                    'representing the percentage of credit earned. No other text.'
                )},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ]
            text = llm.chat(messages, max_tokens=16).text.strip()
            score = int(''.join(ch for ch in text if ch.isdigit()) or '0')
            return max(0, min(100, score))
        except Exception:
            return None

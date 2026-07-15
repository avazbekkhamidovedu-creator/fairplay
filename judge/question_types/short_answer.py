"""Short-answer question type: front-end authored, graded by the configured LLM.

When a ``grading_table`` is configured the LLM scores each sub-question independently and
returns per-item feedback; otherwise the entire answer is graded as one unit.
All scores pass through a calibration pipeline (spline + strictness preset) and are
rounded to 0.5 precision.  An unconfigured LLM leaves the submission at zero with a
manual-review flag so grading never blocks.
"""
import json
import logging
import time as _time

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models import ProblemSubmissionType
from judge.question_types.base import GradingResult, QuestionType
from judge.question_types.registry import register
from judge.utils.calibration import calibrate

logger = logging.getLogger(__name__)


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

        scored = self._grade_ai(problem, config, answer)
        if scored is None:
            cases = [{'case': 1, 'status': 'WA', 'points': 0, 'total': 100,
                      'feedback': _('Awaiting manual grading (AI grader unavailable).')}]
            return GradingResult(case_points=0, case_total=100, result='WA', cases=cases)

        sub_scores, total = scored
        if not sub_scores:
            cases = [{'case': 1, 'status': 'WA', 'points': 0, 'total': 100,
                      'feedback': _('Grading error — please contact staff.')}]
            return GradingResult(case_points=0, case_total=100, result='WA', cases=cases)

        case_total = sum(item['max_points'] for item in sub_scores)
        result = 'AC' if total >= case_total else 'WA'

        # Build one SubmissionTestCase per sub-question so the status page
        # renders a grading table with per-item feedback.
        cases = []
        for i, sq in enumerate(sub_scores):
            cases.append({
                'case': i + 1,
                'status': 'AC' if sq['score'] >= sq['max_points'] else 'WA',
                'points': sq['score'],
                'total': sq['max_points'],
                'feedback': sq['name'],
                'extended_feedback': sq.get('feedback', ''),
            })

        return GradingResult(case_points=total, case_total=case_total,
                             result=result, cases=cases)

    # ------------------------------------------------------------------
    def _grade_ai(self, problem, config, answer):
        """Call the LLM, parse JSON, and apply calibration.

        Returns ``(sub_scores, total)`` where *sub_scores* is a list of dicts
        ``{name, max_points, score, feedback}`` and *total* is the sum of
        calibrated per-item scores rounded to 0.5.  Returns ``None`` when
        the LLM is unavailable or the call fails."""
        from judge import llm

        if not llm.is_configured():
            return None

        grading_table = config.grading_table if config else None
        reference = (config.reference_answer if config else '') or ''
        rubric = (config.ai_rubric if config else '') or ''

        sub_questions = self._build_sub_questions(grading_table, problem.points)
        messages = self._build_messages(problem, reference, rubric, answer, sub_questions)

        try:
            raw = self._call_llm(llm, messages)
            parsed = json.loads(raw)
        except Exception:
            logger.exception('Short-answer AI grading failed for submission')
            return None

        sub_scores = parsed.get('sub_scores', [])
        if not isinstance(sub_scores, list) or not sub_scores:
            return None

        # Calibrate per sub-question
        calibrated = []
        for sq in sub_scores:
            raw_score = float(sq.get('score', 0))
            name = str(sq.get('name', ''))[:30]
            # Look up the matching sub-question for max_points and strictness
            match = next((q for q in sub_questions if q['name'] == name), None)
            max_pts = match['max_points'] if match else 1.0
            strictness = match['strictness'] if match else 'normal'

            cal_score = calibrate(raw_score, max_pts, strictness)
            calibrated.append({
                'name': name,
                'max_points': max_pts,
                'score': cal_score,
                'feedback': str(sq.get('feedback', ''))[:2000],
            })

        total = sum(item['score'] for item in calibrated)
        # Apply 0.5 rounding to the total as a final safety net.
        total = round(total * 2) / 2
        return calibrated, total

    @staticmethod
    def _build_sub_questions(grading_table, problem_points):
        """Return the list of sub-question dicts to send to the LLM.

        When *grading_table* is ``None`` we create a single "Overall" entry
        for backward compatibility.
        """
        if grading_table and isinstance(grading_table, list) and len(grading_table) > 0:
            return grading_table
        return [{'name': 'Overall', 'max_points': float(problem_points), 'strictness': 'normal'}]

    @staticmethod
    def _build_messages(problem, reference, rubric, answer, sub_questions):
        system = (
            'You are a precise short-answer grader.\n\n'
            'Zero-Score Pre-Check — apply BEFORE normal scoring:\n'
            '- BLANK or near-empty (fewer than 5 characters) → score 0.\n'
            '- OFF-TOPIC: answer is completely unrelated to the question → score 0.\n'
            '- NON-ANSWER: "I don\'t know", "idk", "no idea", "?" etc. → score 0.\n'
            '- For all other answers, score normally using the guide below.\n\n'
            'Score guide (as proportion of max score):\n'
            '- Very Low (bottom 30%): Major errors, barely addresses content.\n'
            '- Low (30-50%): Partial understanding, significant gaps or errors.\n'
            '- Medium (50-80%): Adequate to good, minor errors or imprecise wording.\n'
            '- High (80-100%): Excellent, complete reasoning, precise. '
            'Give full marks when deserved.\n\n'
            'Strictness levels (apply per sub-question):\n'
            '- strict (严格): Only award points for fully correct, precise answers.\n'
            '- normal (正常): Award partial credit for partially correct answers.\n'
            '- lenient (宽松): Be generous; give benefit of the doubt.\n\n'
            'Output ONLY valid JSON:\n'
            '{"sub_scores": [{"name": str, "score": float, "feedback": str}], '
            '"total_score": float}'
        )

        # Build grading table text
        table_lines = []
        for sq in sub_questions:
            table_lines.append(
                f'  - {sq["name"]}: max {sq["max_points"]} pts, strictness={sq["strictness"]}'
            )
        table_text = '\n'.join(table_lines) if table_lines else '  (no sub-questions)'

        question = (problem.description or '')[:2000]
        user = (
            f'Question: {question}\n'
            f'Reference Answer: {reference[:1000]}\n'
            f'Rubric: {rubric[:500]}\n'
            f'Student Answer: {answer[:2000]}\n\n'
            f'Grading Table:\n{table_text}\n\n'
            f'Score each sub-question from 0 to its max points. Output JSON.'
        )

        return [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]

    @staticmethod
    def _call_llm(llm, messages):
        """Send the prompt with retry on transient errors (429/5xx), exponential
        back-off 1s → 5s → 25s, max 3 retries.  Returns the stripped text."""
        delays = [1, 5, 25]
        for attempt in range(len(delays) + 1):
            try:
                result = llm.chat(messages, profile='grading')
                text = result.text.strip()
                # Strip markdown code fences if present
                if text.startswith('```'):
                    lines = text.split('\n')
                    text = '\n'.join(lines[1:]) if len(lines) > 1 else text
                    if text.endswith('```'):
                        text = text.rsplit('```', 1)[0]
                return text.strip()
            except Exception as exc:
                logger.warning('LLM attempt %d failed: %s', attempt + 1, exc)
                if attempt < len(delays):
                    msg = str(exc).lower()
                    if any(str(code) in msg for code in (429, 500, 502, 503, 504)):
                        _time.sleep(delays[attempt])
                        continue
                return None
        return None

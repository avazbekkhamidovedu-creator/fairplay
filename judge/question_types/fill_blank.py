"""Fill-in-the-blank question type: front-end authored, internally graded.

In exact mode each blank is compared to its accepted-answers set (+1 per correct
blank out of N). In AI mode the configured LLM scores every blank independently,
applying the calibration pipeline (spline + strictness preset) and rounding each
sub-score to 0.5 precision.  Per-blank feedback is stored so the status page can
render a grading table.
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
        """Ask the configured LLM to score every blank independently, apply
        calibration per blank, and return a GradingResult with per-blank
        SubmissionTestCase rows.  Returns ``None`` to signal "fall back to
        exact matching" (no model configured, or call/parse failed)."""
        from judge import llm

        if not llm.is_configured() or not blanks:
            return None

        # Resolve sub-questions: auto-generate from blanks unless an explicit
        # grading_table was saved.
        grading_table = config.grading_table if config else None
        if grading_table and isinstance(grading_table, list) and len(grading_table) == len(blanks):
            sub_questions = grading_table
        else:
            pts_per_blank = float(problem.points) / len(blanks) if problem.points else 1.0
            sub_questions = [
                {'name': blank.label or f'Blank {i + 1}',
                 'max_points': pts_per_blank,
                 'strictness': 'normal'}
                for i, blank in enumerate(blanks)
            ]

        messages = self._build_ai_messages(problem, config, blanks, answers, sub_questions)
        try:
            raw = self._call_llm(llm, messages)
            parsed = json.loads(raw)
        except Exception:
            logger.exception('Fill-blank AI grading failed for submission')
            return None

        sub_scores = parsed.get('sub_scores', [])
        if not isinstance(sub_scores, list) or not sub_scores:
            return None

        # Calibrate each sub-question
        calibrated = []
        for sq in sub_scores:
            raw_score = float(sq.get('score', 0))
            name = str(sq.get('name', ''))[:30]
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

        case_points = sum(item['score'] for item in calibrated)
        case_total = sum(item['max_points'] for item in calibrated)
        case_points = round(case_points * 2) / 2  # 0.5 precision

        result = 'AC' if case_points >= case_total else 'WA'
        cases = []
        for i, item in enumerate(calibrated):
            cases.append({
                'case': i + 1,
                'status': 'AC' if item['score'] >= item['max_points'] else 'WA',
                'points': item['score'],
                'total': item['max_points'],
                'feedback': item['name'],
                'extended_feedback': item.get('feedback', ''),
            })

        return GradingResult(case_points=case_points, case_total=case_total,
                             result=result, cases=cases)

    # ------------------------------------------------------------------
    @staticmethod
    def _build_ai_messages(problem, config, blanks, answers, sub_questions):
        question = (problem.description or '')[:2000]
        rubric = (config.ai_rubric if config else '') or ''

        system = (
            'You are a precise fill-in-the-blank grader.\n\n'
            'Zero-Score Pre-Check — apply BEFORE normal scoring:\n'
            '- BLANK or near-empty (fewer than 2 characters) → score 0.\n'
            '- OFF-TOPIC: answer is completely unrelated to the blank → score 0.\n'
            '- For all other answers, score normally.\n\n'
            'For each blank, compare the student\'s answer to the accepted answers. '
            'An answer that exactly matches any accepted answer gets full points. '
            'Near-matches (minor typos, synonym-equivalent, different phrasing with '
            'same meaning) deserve partial credit based on how close they are.\n\n'
            'Score guide (as proportion of max points per blank):\n'
            '- Very Low (bottom 30%): Wrong concept, unrelated.\n'
            '- Low (30-50%): Partial understanding but off.\n'
            '- Medium (50-80%): Close, minor issues.\n'
            '- High (80-100%): Excellent match or exact equivalent.\n'
            'Give full marks when the answer is essentially correct.\n\n'
            'Output ONLY valid JSON:\n'
            '{"sub_scores": [{"name": str, "score": float, "feedback": str}], '
            '"total_score": float}'
        )

        blank_lines = []
        for i, blank in enumerate(blanks):
            accepted = ', '.join(blank.answers.splitlines())
            student = answers[i] if i < len(answers) else ''
            name = sub_questions[i]['name'] if i < len(sub_questions) else f'Blank {i + 1}'
            max_pts = sub_questions[i]['max_points'] if i < len(sub_questions) else 1.0
            blank_lines.append(
                f'  - {name}: max {max_pts} pts, '
                f'accepted=[{accepted}], student="{student[:500]}"'
            )

        user = (
            f'Question: {question}\n'
            f'Rubric: {rubric[:500]}\n'
            f'Blanks:\n' + '\n'.join(blank_lines) + '\n\n'
            f'Score each blank independently from 0 to its max points. Output JSON.'
        )

        return [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]

    @staticmethod
    def _call_llm(llm, messages):
        """Send the prompt with retry on transient errors (429/5xx)."""
        delays = [1, 5, 25]
        for attempt in range(len(delays) + 1):
            try:
                result = llm.chat(messages, profile='grading')
                text = result.text.strip()
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

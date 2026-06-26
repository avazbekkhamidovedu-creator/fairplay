"""Tests for the exam-style contest feature: per-problem result hiding, latest-vs-best scoring
(``ExamContestFormat``), submission masking, and the end-of-contest finalize task."""
from datetime import timedelta

from django.template import Context, Template
from django.test import TestCase
from django.utils import timezone

from judge.models import Contest, ContestParticipation, ContestSubmission, Language, Submission
from judge.models.tests.util import CommonDataMixin, create_contest, create_contest_participation, \
    create_contest_problem, create_problem, create_user


class ExamContestTestMixin(CommonDataMixin):
    """Builds an exam contest with one code problem (visible/best) and one MCQ problem (hidden/latest)."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.editor = create_user(username='exam_editor')
        self.contest = create_contest(
            key='exam', format_name='exam',
            authors=('exam_editor',),
            problems=(),
        )
        self.code_problem = create_problem(code='examcode', submission_type='C', points=100)
        self.mcq_problem = create_problem(code='exammcq', submission_type='M', points=100)
        self.code_cp = create_contest_problem(contest=self.contest, problem=self.code_problem, points=100, order=1)
        self.mcq_cp = create_contest_problem(contest=self.contest, problem=self.mcq_problem, points=100, order=2)
        self.participation = create_contest_participation(contest=self.contest, user=self.users['normal'].profile)

    def _add_contest_submission(self, contest_problem, *, points, minutes):
        """Create a Submission + ContestSubmission with controlled points and submission time."""
        sub = Submission.objects.create(
            user=self.participation.user, problem=contest_problem.problem,
            language=Language.get_answer_language(), points=points,
            case_points=points, case_total=100, result='AC' if points else 'WA', status='D',
            contest_object=contest_problem.contest,
        )
        # date is auto_now_add; force a deterministic time so "latest" is unambiguous.
        Submission.objects.filter(id=sub.id).update(date=timezone.now() + timedelta(minutes=minutes))
        sub.refresh_from_db()
        ContestSubmission.objects.create(
            submission=sub, problem=contest_problem, participation=self.participation, points=points)
        return sub


class ResultsHiddenResolutionTest(ExamContestTestMixin, TestCase):
    def test_type_default(self):
        # No override: MCQ hidden, code shown (from the question-type policy flags).
        self.assertTrue(self.mcq_cp.results_hidden)
        self.assertFalse(self.code_cp.results_hidden)

    def test_score_uses_latest_default(self):
        self.assertTrue(self.mcq_cp.score_uses_latest)
        self.assertFalse(self.code_cp.score_uses_latest)

    def test_override_forces_show_and_hide(self):
        self.mcq_cp.hide_results = False
        self.code_cp.hide_results = True
        self.assertFalse(self.mcq_cp.results_hidden)
        self.assertTrue(self.code_cp.results_hidden)


class FormatClassTemplateAccessTest(ExamContestTestMixin, TestCase):
    """Regression: the admin change-form gates the "Finalize results" button on
    ``{% if original.format_class.has_hidden_results %}``. ``format_class`` is a class, and Django's
    template engine auto-calls callables, so without ``do_not_call_in_templates`` it instantiates the
    class, fails (``__init__`` needs args), and falls back to an empty ``string_if_invalid`` — silently
    hiding the button. See judge/contest_format/base.py."""

    TEMPLATE = Template('{% if c.format_class.has_hidden_results %}Y{% else %}N{% endif %}')

    def _render(self, contest):
        return self.TEMPLATE.render(Context({'c': contest}))

    def test_exam_format_class_resolves_in_template(self):
        # Pre-fix this rendered 'N' (format_class collapsed to ''); it must resolve to the class.
        self.assertEqual(self._render(self.contest), 'Y')

    def test_default_format_still_falsy_in_template(self):
        plain = create_contest(key='plain_tpl', format_name='default', problems=())
        self.assertEqual(self._render(plain), 'N')


class ExamScoringTest(ExamContestTestMixin, TestCase):
    def test_latest_for_mcq_best_for_code_excluded_until_finalized(self):
        # Code: best of 50/100 -> 100. MCQ: best 100 (earlier), latest 40 (later).
        self._add_contest_submission(self.code_cp, points=50, minutes=1)
        self._add_contest_submission(self.code_cp, points=100, minutes=2)
        self._add_contest_submission(self.mcq_cp, points=100, minutes=1)
        self._add_contest_submission(self.mcq_cp, points=40, minutes=2)

        # While running (not finalized): MCQ excluded from the total, code counts at its best.
        self.participation.recompute_results()
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.score, 100)
        self.assertTrue((self.participation.format_data or {})[str(self.mcq_cp.id)].get('hidden'))

        # After finalize: MCQ folds in at its LATEST score (40), not its best (100).
        Contest.objects.filter(id=self.contest.id).update(results_finalized=True)
        # Reload so the participation's cached contest/format pick up results_finalized.
        self.participation = ContestParticipation.objects.get(id=self.participation.id)
        self.participation.recompute_results()
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.score, 140)
        self.assertFalse((self.participation.format_data or {})[str(self.mcq_cp.id)].get('hidden'))
        self.assertEqual((self.participation.format_data or {})[str(self.mcq_cp.id)]['points'], 40)


class SubmissionMaskingTest(ExamContestTestMixin, TestCase):
    def _mcq_submission(self):
        return self._add_contest_submission(self.mcq_cp, points=100, minutes=1)

    def test_owner_masked_editor_and_staff_not(self):
        sub = self._mcq_submission()
        self.assertTrue(sub.contest_results_hidden)
        # Owner cannot see their own hidden result.
        self.assertFalse(sub.results_visible_to(self.users['normal']))
        # Contest editor (author) and a view-all-submission staffer can.
        self.assertTrue(sub.results_visible_to(self.editor))
        self.assertTrue(sub.results_visible_to(self.users['superuser']))
        # Anonymous cannot.
        self.assertFalse(sub.results_visible_to(self.users['anonymous']))

    def test_code_problem_not_hidden(self):
        sub = self._add_contest_submission(self.code_cp, points=100, minutes=1)
        self.assertFalse(sub.contest_results_hidden)
        self.assertTrue(sub.results_visible_to(self.users['normal']))

    def test_revealed_after_finalize(self):
        sub = self._mcq_submission()
        Contest.objects.filter(id=self.contest.id).update(results_finalized=True)
        sub = Submission.objects.get(id=sub.id)  # drop cached_property / reload contest_object
        self.assertFalse(sub.contest_results_hidden)
        self.assertTrue(sub.results_visible_to(self.users['normal']))


class DefaultFormatUnaffectedTest(CommonDataMixin, TestCase):
    """Backward compatibility: a default-format contest hides nothing and scores by best."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.contest = create_contest(key='plain', format_name='default', problems=())
        self.mcq_problem = create_problem(code='plainmcq', submission_type='M', points=100)
        self.cp = create_contest_problem(contest=self.contest, problem=self.mcq_problem, points=100, order=1)
        self.participation = create_contest_participation(contest=self.contest, user=self.users['normal'].profile)

    def _add(self, points, minutes):
        sub = Submission.objects.create(
            user=self.participation.user, problem=self.mcq_problem,
            language=Language.get_answer_language(), points=points,
            case_points=points, case_total=100, result='AC' if points else 'WA', status='D',
            contest_object=self.contest,
        )
        Submission.objects.filter(id=sub.id).update(date=timezone.now() + timedelta(minutes=minutes))
        ContestSubmission.objects.create(
            submission=sub, problem=self.cp, participation=self.participation, points=points)
        return sub

    def test_not_hidden_and_scored_by_best(self):
        first = self._add(points=100, minutes=1)
        self._add(points=40, minutes=2)
        # Even an MCQ submission is not masked outside an exam-format contest.
        self.assertFalse(first.contest_results_hidden)
        self.assertTrue(first.results_visible_to(self.users['normal']))
        # Default format counts the best (100), not the latest (40).
        self.participation.recompute_results()
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.score, 100)

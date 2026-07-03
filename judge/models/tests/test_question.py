import json

from django.test import TestCase

from judge.models import ContestSubmission, Language, ProblemMCQConfig, ProblemMCQOption, Submission, \
    SubmissionSource, SubmissionTestCase
from judge.models.tests.util import CommonDataMixin, create_contest, create_contest_participation, \
    create_contest_problem, create_problem, create_user
from judge.question_types import get_grader
from judge.question_types.mcq import ordered_options_for
from judge.submission_grading import finalize_submission, grade_internally


class FinalizeSubmissionTestCase(CommonDataMixin, TestCase):
    """The shared finalize helper must scale points exactly like on_grading_end always did."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.partial_problem = create_problem(code='finpart', points=10, partial=True, is_public=True)
        self.strict_problem = create_problem(code='finstrict', points=10, partial=False, is_public=True)

    def _make_submission(self, problem):
        return Submission.objects.create(
            user=self.users['normal'].profile, problem=problem,
            language=Language.get_answer_language(),
        )

    def test_partial_scaling(self):
        sub = self._make_submission(self.partial_problem)
        finalize_submission(sub, case_points=1, case_total=2, result='WA')
        sub.refresh_from_db()
        self.assertEqual(sub.points, 5)
        self.assertEqual(sub.status, 'D')
        self.assertEqual(sub.result, 'WA')

    def test_non_partial_all_or_nothing(self):
        sub = self._make_submission(self.strict_problem)
        finalize_submission(sub, case_points=1, case_total=2, result='WA')
        sub.refresh_from_db()
        # Not partial and not full marks -> zeroed.
        self.assertEqual(sub.points, 0)

    def test_non_partial_full_marks(self):
        sub = self._make_submission(self.strict_problem)
        finalize_submission(sub, case_points=2, case_total=2, result='AC')
        sub.refresh_from_db()
        self.assertEqual(sub.points, 10)
        self.assertEqual(sub.result, 'AC')

    def test_zero_total_is_safe(self):
        sub = self._make_submission(self.partial_problem)
        finalize_submission(sub, case_points=0, case_total=0, result='WA')
        sub.refresh_from_db()
        self.assertEqual(sub.points, 0)


class MCQGradingTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(self):
        super().setUpTestData()

    def _make_mcq(self, code, options, multiple=False, shuffle=False, partial=False, points=10):
        problem = create_problem(code=code, submission_type='M', points=points, partial=partial, is_public=True)
        ProblemMCQConfig.objects.create(problem=problem, multiple_select=multiple, shuffle_options=shuffle)
        created = []
        for i, (text, correct) in enumerate(options):
            created.append(ProblemMCQOption.objects.create(problem=problem, order=i, text=text, is_correct=correct))
        return problem, created

    def _submit(self, problem, selected_ids):
        sub = Submission.objects.create(
            user=self.users['normal'].profile, problem=problem,
            language=Language.get_answer_language(),
        )
        SubmissionSource.objects.create(submission=sub, source=json.dumps({'selected': selected_ids}))
        grade_internally(sub)
        sub.refresh_from_db()
        return sub

    def test_correct_single(self):
        problem, opts = self._make_mcq('mcqok', [('a', False), ('b', True), ('c', False)])
        sub = self._submit(problem, [opts[1].id])
        self.assertEqual(sub.result, 'AC')
        self.assertEqual(sub.points, 10)
        self.assertEqual(sub.status, 'D')
        # A summary testcase row is recorded for the status page.
        self.assertEqual(SubmissionTestCase.objects.filter(submission=sub).count(), 1)

    def test_wrong_single(self):
        problem, opts = self._make_mcq('mcqwrong', [('a', False), ('b', True), ('c', False)])
        sub = self._submit(problem, [opts[0].id])
        self.assertEqual(sub.result, 'WA')
        self.assertEqual(sub.points, 0)

    def test_empty_selection_is_wrong(self):
        problem, opts = self._make_mcq('mcqempty', [('a', False), ('b', True)])
        sub = self._submit(problem, [])
        self.assertEqual(sub.result, 'WA')
        self.assertEqual(sub.points, 0)

    def test_multiple_select_exact(self):
        problem, opts = self._make_mcq(
            'mcqmulti', [('a', True), ('b', True), ('c', False)], multiple=True, partial=False)
        sub = self._submit(problem, [opts[0].id, opts[1].id])
        self.assertEqual(sub.result, 'AC')
        self.assertEqual(sub.points, 10)

    def test_multiple_select_partial(self):
        problem, opts = self._make_mcq(
            'mcqpartial', [('a', True), ('b', True), ('c', False)], multiple=True, partial=True)
        # One of two correct, no wrong -> 1/2.
        sub = self._submit(problem, [opts[0].id])
        self.assertEqual(sub.result, 'WA')
        self.assertEqual(sub.points, 5)

    def test_multiple_select_partial_penalizes_wrong(self):
        problem, opts = self._make_mcq(
            'mcqpenalty', [('a', True), ('b', True), ('c', False)], multiple=True, partial=True)
        # One correct, one wrong -> max(0, 1 - 1) = 0.
        sub = self._submit(problem, [opts[0].id, opts[2].id])
        self.assertEqual(sub.points, 0)


class MCQContestPartialTestCase(CommonDataMixin, TestCase):
    """The per-contest ``partial`` setting must override the problem-level one.

    Old workflow: ticking a ContestProblem's ``partial`` makes the problem score partially in that
    contest regardless of ``Problem.partial`` (and vice versa). Raw MCQ grading is always granular;
    the all-or-nothing gating is applied separately at problem level (``finalize_submission``) and
    contest level (``update_contest``), exactly like code problems.
    """

    def _make_mcq(self, code, options, partial):
        problem = create_problem(code=code, submission_type='M', points=10, partial=partial, is_public=True)
        ProblemMCQConfig.objects.create(problem=problem, multiple_select=True)
        created = [ProblemMCQOption.objects.create(problem=problem, order=i, text=t, is_correct=c)
                   for i, (t, c) in enumerate(options)]
        return problem, created

    def _submit_in_contest(self, problem, selected_ids, *, contest_partial):
        contest = create_contest(key='c_' + problem.code)
        contest_problem = create_contest_problem(
            problem=problem, contest=contest, points=10, partial=contest_partial)
        participation = create_contest_participation(contest=contest, user=self.users['normal'].profile)
        sub = Submission.objects.create(
            user=self.users['normal'].profile, problem=problem,
            language=Language.get_answer_language(), contest_object=contest,
        )
        contest_sub = ContestSubmission.objects.create(
            submission=sub, problem=contest_problem, participation=participation)
        SubmissionSource.objects.create(submission=sub, source=json.dumps({'selected': selected_ids}))
        grade_internally(sub)
        sub.refresh_from_db()
        contest_sub.refresh_from_db()
        return sub, contest_sub

    def test_contest_partial_overrides_non_partial_problem(self):
        # Problem not partial, but the contest enables partial -> partial scoring in the contest.
        problem, opts = self._make_mcq('cmcq1', [('a', True), ('b', True), ('c', False)], partial=False)
        # One of two correct, no wrong -> raw 1/2.
        sub, contest_sub = self._submit_in_contest(problem, [opts[0].id], contest_partial=True)
        # Problem-level score is all-or-nothing (problem not partial) -> zeroed.
        self.assertEqual(sub.points, 0)
        # Contest-level score honors the contest's partial setting -> half of 10.
        self.assertEqual(contest_sub.points, 5)

    def test_contest_non_partial_overrides_partial_problem(self):
        # Problem partial, but the contest disables partial -> all-or-nothing in the contest.
        problem, opts = self._make_mcq('cmcq2', [('a', True), ('b', True), ('c', False)], partial=True)
        sub, contest_sub = self._submit_in_contest(problem, [opts[0].id], contest_partial=False)
        # Problem-level score is partial.
        self.assertEqual(sub.points, 5)
        # Contest zeroes a non-full score because the contest's partial is off.
        self.assertEqual(contest_sub.points, 0)


class MCQShuffleTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.problem = create_problem(code='mcqshuf', submission_type='M', is_public=True)
        ProblemMCQConfig.objects.create(problem=self.problem, shuffle_options=True)
        for i in range(8):
            ProblemMCQOption.objects.create(problem=self.problem, order=i, text='opt%d' % i, is_correct=(i == 0))
        self.alice = create_user(username='alice').profile
        self.bob = create_user(username='bob').profile

    def test_stable_per_user(self):
        first = [o['id'] for o in ordered_options_for(self.problem, self.alice)]
        second = [o['id'] for o in ordered_options_for(self.problem, self.alice)]
        self.assertEqual(first, second)

    def test_differs_between_users(self):
        alice = [o['id'] for o in ordered_options_for(self.problem, self.alice)]
        bob = [o['id'] for o in ordered_options_for(self.problem, self.bob)]
        self.assertNotEqual(alice, bob)
        self.assertEqual(sorted(alice), sorted(bob))  # same set, different order

    def test_no_is_correct_leaked(self):
        for option in ordered_options_for(self.problem, self.alice):
            self.assertNotIn('is_correct', option)


class AnswerLanguageTestCase(TestCase):
    def test_get_answer_language_idempotent(self):
        first = Language.get_answer_language()
        second = Language.get_answer_language()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.key, 'ANSWER')
        self.assertEqual(first.pygments, 'text')


class GraderRegistryTestCase(TestCase):
    def test_code_routes_to_bridge(self):
        self.assertTrue(get_grader('C').routes_to_bridge)

    def test_mcq_does_not_route_to_bridge(self):
        grader = get_grader('M')
        self.assertFalse(grader.routes_to_bridge)
        self.assertFalse(grader.is_manual)

    def test_unregistered_type_raises(self):
        # Types exposed in the admin choices but without a grader (fill-in-blank, file upload)
        # must fail clearly rather than with a bare KeyError.
        for submission_type in ('B', 'F'):
            with self.assertRaises(NotImplementedError):
                get_grader(submission_type)

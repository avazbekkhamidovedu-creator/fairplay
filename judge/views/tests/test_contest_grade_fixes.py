"""Regression tests for the contest grade/editor fixes of this change set.

Covers four contracts that were broken or unguarded:

1. **Editing a contest must not erase submission history.** ``_sync_problems``
   used to delete-and-recreate every ``ContestProblem`` row; because
   ``ContestSubmission.problem`` is a FK with ``on_delete=CASCADE``, that
   cascade-deleted every ``ContestSubmission`` and the ``contest_submission_delete``
   signal then detached each ``Submission.contest_object`` — so after ANY edit
   (including a pure duration change) all prior submissions silently vanished
   from the grade page / workspace / exports. Rows are now upserted in place.
2. **Grade page totals use the assignment-specified points.** The student
   workspace shows ``ContestSubmission.points / ContestProblem.points``; the
   grade page used ``case_total / problem.points`` (the problem's own scale) and
   disagreed with the student view whenever the assignment re-weights a problem.
3. **Non-code answers render human-readably on the grade page.** MCQ /
   fill-blank / short-answer submissions now ship an ``answer_display`` list
   (same rows as the student workspace) instead of raw JSON source.
4. **Pending queue semantics.** ``is_pending`` means "awaiting teacher review":
   manual types with no score, or AI-graded fill-blank/short-answer problems
   flagged "require teacher review after AI scoring" (now persisted through the
   problem editor). A code submission still in the judge queue is NOT pending.
"""

import json
from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    ContestParticipation,
    ContestProblem,
    ContestSubmission,
    Language,
    ProblemFillBlankConfig,
    ProblemShortAnswerConfig,
    Submission,
    SubmissionSource,
)
from judge.models.tests.util import create_problem, create_user
from judge.views.tests.test_contest_grade import GradeTestBase


class ContestEditPreservesSubmissionsTestCase(GradeTestBase):
    """Editing a contest (e.g. adding duration) must not erase submission history.

    Regression for: after increasing the duration and saving, every previous
    submission disappeared from the assignment. Root cause: _sync_problems did
    delete-and-recreate on ContestProblem, which cascade-deleted ContestSubmission
    and detached Submission.contest_object.
    """

    def _edit(self, **overrides):
        """POST a full edit payload (with the problem list) to the editor."""
        editor = create_user(username='sync_editor', user_permissions=('edit_all_contest',))
        self.contest.authors.add(editor.profile)
        now = timezone.now()
        payload = {
            'name': self.contest.name, 'key': self.contest.key,
            'start_time': (now - timedelta(days=1)).isoformat(),
            'end_time': (now + timedelta(days=2)).isoformat(),
            'duration_hours': 0, 'duration_minutes': 0,
            'is_visible': False, 'is_rated': False, 'use_clarifications': True,
            'hide_problem_tags': False, 'hide_problem_authors': False,
            'allow_virtual_participation': True, 'run_pretests_only': False,
            'rate_all': False, 'tester_see_submissions': False, 'tester_see_scoreboard': False,
            'show_short_display': False, 'is_private': False, 'is_organization_private': False,
            'limit_join_organizations': False, 'seb_required': False,
            'format_name': 'default', 'scoreboard_visibility': 'V', 'points_precision': 3,
            'authors': [editor.profile.id], 'curators': [], 'testers': [], 'spectators': [],
            'organizations': [], 'classes': [], 'join_organizations': [], 'tags': [],
            'private_contestants': [], 'rate_exclude': [], 'view_contest_scoreboard': [],
            'view_contest_submissions': [], 'banned_users': [],
        }
        payload.update(overrides)
        client = Client()
        client.force_login(editor)
        return client.post(
            reverse('contest_edit', args=[self.contest.key]),
            data=json.dumps(payload), content_type='application/json',
        )

    def _make_contest_submission(self, contest_problem=None, points=50.0):
        """Attach a ContestSubmission row + keep the grade fixture's Submission."""
        cp = contest_problem or ContestProblem.objects.get(contest=self.contest, problem=self.problem)
        participation = ContestParticipation.objects.create(
            contest=self.contest, user=self.student.profile, real_start=timezone.now(),
        )
        return ContestSubmission.objects.create(
            submission=self.submission, problem=cp, participation=participation, points=points,
        )

    def test_edit_preserves_contest_submission_links(self):
        """A re-save with the same problem list keeps ContestSubmission rows."""
        self._make_contest_submission()
        self.assertTrue(ContestSubmission.objects.filter(submission=self.submission).exists())

        response = self._edit(problems=[{'id': self.problem.id, 'points': 100}])
        self.assertEqual(response.status_code, 200, response.content)

        # The ContestSubmission link must survive the edit.
        self.assertTrue(
            ContestSubmission.objects.filter(submission=self.submission).exists(),
            'ContestSubmission was cascade-deleted by the contest edit',
        )
        # ... and the submission must still be associated with the contest.
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.contest_object_id, self.contest.id)
        self.assertEqual(self.submission.contest_or_none.points, 50.0)

    def test_edit_with_duration_change_preserves_submissions(self):
        """The exact user-reported scenario: change duration, keep submissions."""
        # Give the contest an initial per-student duration so the extension
        # passes the "only extend, never shorten" guard.
        from datetime import timedelta as td
        self.contest.time_limit = td(hours=1)
        self.contest.save(update_fields=['time_limit'])
        self._make_contest_submission()
        response = self._edit(
            problems=[{'id': self.problem.id, 'points': 100}],
            duration_hours=2, duration_minutes=0,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.time_limit, td(hours=2))
        self.assertTrue(ContestSubmission.objects.filter(submission=self.submission).exists())

    def test_problem_points_update_keeps_same_contest_problem_pk(self):
        """Updating per-problem points edits the row in place (PK stable)."""
        cp = ContestProblem.objects.get(contest=self.contest, problem=self.problem)
        self._make_contest_submission(contest_problem=cp)
        old_pk = cp.pk

        response = self._edit(problems=[{'id': self.problem.id, 'points': 250}])
        self.assertEqual(response.status_code, 200, response.content)

        cp.refresh_from_db()
        self.assertEqual(cp.pk, old_pk, 'ContestProblem was recreated instead of updated')
        self.assertEqual(cp.points, 250)
        self.assertTrue(ContestSubmission.objects.filter(problem=cp).exists())

    def test_removing_problem_detaches_but_keeps_submission_row(self):
        """Removing a problem from the contest is the only allowed deletion:
        its ContestSubmission goes, and the Submission row survives unlinked."""
        self._make_contest_submission()
        response = self._edit(problems=[])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(ContestSubmission.objects.filter(submission=self.submission).exists())
        self.assertTrue(Submission.objects.filter(id=self.submission.id).exists())


class ContestGradeTotalUsesAssignmentPointsTestCase(GradeTestBase):
    """The grade page must show the same points/total the student sees.

    Student workspace: ContestSubmission.points / ContestProblem.points.
    Old grade page:   Submission.points  / (case_total or problem.points).
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Re-weight the assignment: the problem is worth 100 pts on its own
        # scale, but this assignment gives it only 25.
        ContestProblem.objects.filter(contest=cls.contest, problem=cls.problem).update(points=25)
        # Student-visible score after a 50% machine score on a 25-pt problem.
        cls.participation = ContestParticipation.objects.create(
            contest=cls.contest, user=cls.student.profile, real_start=timezone.now(),
        )
        ContestSubmission.objects.create(
            submission=cls.submission, problem=ContestProblem.objects.get(
                contest=cls.contest, problem=cls.problem),
            participation=cls.participation, points=12.5,
        )

    def _grade_payload(self):
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        return json.loads(html.split('var submissions = ')[1].split(';\n')[0])

    def test_total_uses_assignment_points(self):
        payload = self._grade_payload()
        row = next(s for s in payload if s['id'] == self.submission.id)
        self.assertEqual(row['total'], 25)      # ContestProblem.points, not 100
        self.assertEqual(row['points'], 12.5)   # ContestSubmission.points, not 50

    def test_standalone_submission_falls_back_to_problem_scale(self):
        """A submission with no ContestSubmission row keeps the old behavior."""
        lone = Submission.objects.create(
            user=self.student.profile, problem=self.problem,
            language=Language.objects.get(key='PY3'), result='AC', status='D',
            contest_object=self.contest, points=80.0, case_total=100,
        )
        payload = self._grade_payload()
        row = next(s for s in payload if s['id'] == lone.id)
        self.assertEqual(row['total'], 100)
        self.assertEqual(row['points'], 80.0)

    def test_save_endpoint_clamps_to_assignment_total(self):
        response = self._client(self.curator).post(
            self.save_url, {'submission': self.submission.id, 'score': '9999'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['score'], 25.0)  # clamped to assignment total


class ContestGradeAnswerDisplayTestCase(GradeTestBase):
    """MCQ / fill-blank / short-answer submissions ship readable answer rows."""

    def test_mcq_answer_display(self):
        from judge.models import ProblemMCQOption
        mcq = create_problem('grade-mcq', submission_type='M')
        ProblemMCQOption.objects.create(problem=mcq, order=0, text='Alpha', is_correct=False)
        right = ProblemMCQOption.objects.create(problem=mcq, order=1, text='Beta', is_correct=True)
        ContestProblem.objects.create(contest=self.contest, problem=mcq, points=10, order=2)
        sub = Submission.objects.create(
            user=self.student.profile, problem=mcq,
            language=Language.objects.get(key='PY3'), result='AC', status='D',
            contest_object=self.contest, points=10.0, case_total=1,
        )
        SubmissionSource.objects.create(
            submission=sub, source=json.dumps({'selected': [right.id],
                                               'choices': [{'id': right.id, 'order': 1, 'text': 'Beta'}]}))

        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        payload = json.loads(html.split('var submissions = ')[1].split(';\n')[0])
        row = next(s for s in payload if s['id'] == sub.id)
        self.assertTrue(row['answer_display'])
        self.assertEqual(row['answer_display'][0]['value'], 'B. Beta')
        # Raw JSON must not be the only thing shown for these types.
        self.assertNotIn('"selected"', row['answer_display'][0]['value'])

    def test_fill_blank_answer_display(self):
        from judge.models import ProblemBlank
        fb = create_problem('grade-fillblank', submission_type='B')
        ProblemBlank.objects.create(problem=fb, order=0, answers='Paris\nparis', points=1)
        ContestProblem.objects.create(contest=self.contest, problem=fb, points=5, order=3)
        sub = Submission.objects.create(
            user=self.student.profile, problem=fb,
            language=Language.objects.get(key='PY3'), result='AC', status='D',
            contest_object=self.contest, points=5.0, case_total=1,
        )
        SubmissionSource.objects.create(submission=sub, source=json.dumps({'answers': ['Paris']}))

        response = self._client(self.curator).get(self.grade_url)
        payload = json.loads(response.content.decode('utf-8').split('var submissions = ')[1].split(';\n')[0])
        row = next(s for s in payload if s['id'] == sub.id)
        self.assertEqual(row['answer_display'][0]['value'], 'Paris')

    def test_description_is_rendered_html(self):
        from judge.models import Problem
        Problem.objects.filter(id=self.problem.id).update(
            description='## Heading\n\nSome **bold** text')
        self.problem.refresh_from_db()
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        payload = json.loads(html.split('var problems = ')[1].split(';\n')[0])
        meta = next(p for p in payload if p['code'] == self.problem.code)
        self.assertIn('<h4>', meta['description_html'])
        self.assertIn('<strong>', meta['description_html'])
        self.assertNotIn('## Heading', meta['description_html'])


class ContestGradePendingSemanticsTestCase(GradeTestBase):
    """is_pending must mean "awaiting teacher review", not "missing a score"."""

    def _payload_row(self, sub):
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        payload = json.loads(html.split('var submissions = ')[1].split(';\n')[0])
        return next(s for s in payload if s['id'] == sub.id)

    def test_code_submission_with_points_is_not_pending(self):
        row = self._payload_row(self.submission)
        self.assertEqual(row['points'], 50.0)
        self.assertFalse(row['is_pending'])

    def test_fill_blank_with_manual_review_flag_is_pending(self):
        from judge.models import ProblemBlank
        fb = create_problem('grade-pending-fb', submission_type='B')
        ProblemBlank.objects.create(problem=fb, order=0, answers='x', points=1)
        ContestProblem.objects.create(contest=self.contest, problem=fb, points=5, order=4)
        ProblemFillBlankConfig.objects.create(
            problem=fb, grading_mode='ai', requires_manual_review=True)
        sub = Submission.objects.create(
            user=self.student.profile, problem=fb,
            language=Language.objects.get(key='PY3'), result='AC', status='D',
            contest_object=self.contest, points=5.0, case_total=1,
        )
        SubmissionSource.objects.create(submission=sub, source=json.dumps({'answers': ['x']}))
        self.assertTrue(self._payload_row(sub)['is_pending'])

    def test_fill_blank_without_review_flag_is_not_pending(self):
        from judge.models import ProblemBlank
        fb = create_problem('grade-pending-fb2', submission_type='B')
        ProblemBlank.objects.create(problem=fb, order=0, answers='x', points=1)
        ContestProblem.objects.create(contest=self.contest, problem=fb, points=5, order=5)
        ProblemFillBlankConfig.objects.create(
            problem=fb, grading_mode='ai', requires_manual_review=False)
        sub = Submission.objects.create(
            user=self.student.profile, problem=fb,
            language=Language.objects.get(key='PY3'), result='AC', status='D',
            contest_object=self.contest, points=5.0, case_total=1,
        )
        SubmissionSource.objects.create(submission=sub, source=json.dumps({'answers': ['x']}))
        self.assertFalse(self._payload_row(sub)['is_pending'])

    def test_reviewed_pending_submission_leaves_pending(self):
        from judge.models import ProblemBlank
        fb = create_problem('grade-pending-fb3', submission_type='B')
        ProblemBlank.objects.create(problem=fb, order=0, answers='x', points=1)
        ContestProblem.objects.create(contest=self.contest, problem=fb, points=5, order=6)
        ProblemFillBlankConfig.objects.create(
            problem=fb, grading_mode='ai', requires_manual_review=True)
        sub = Submission.objects.create(
            user=self.student.profile, problem=fb,
            language=Language.objects.get(key='PY3'), result='AC', status='D',
            contest_object=self.contest, points=5.0, case_total=1,
            reviewed_by=self.curator.profile, reviewed_at=timezone.now(),
        )
        SubmissionSource.objects.create(submission=sub, source=json.dumps({'answers': ['x']}))
        self.assertFalse(self._payload_row(sub)['is_pending'])

    def test_queued_code_submission_is_not_pending(self):
        """A code submission waiting on the judge resolves itself — not pending."""
        queued = Submission.objects.create(
            user=self.student.profile, problem=self.problem,
            language=Language.objects.get(key='PY3'), result=None, status='QU',
            contest_object=self.contest, points=None, case_total=0,
        )
        row = self._payload_row(queued)
        self.assertIsNone(row['points'])
        self.assertFalse(row['is_pending'])

    def test_virtual_submission_flagged(self):
        """V badge data: virtual participations are still marked."""
        virtual_part = ContestParticipation.objects.create(
            contest=self.contest, user=self.student.profile, virtual=1, real_start=timezone.now(),
        )
        sub = Submission.objects.create(
            user=self.student.profile, problem=self.problem,
            language=Language.objects.get(key='PY3'), result='WA', status='D',
            contest_object=self.contest, points=10.0, case_total=100,
        )
        ContestSubmission.objects.create(
            submission=sub, problem=ContestProblem.objects.get(contest=self.contest),
            participation=virtual_part, points=10.0,
        )
        self.assertTrue(self._payload_row(sub)['virtual'])


class RequireManualReviewRoundTripTestCase(GradeTestBase):
    """The problem editor persists + prefills the review-required flag."""

    def test_requires_manual_review_round_trip(self):
        from judge.models import Problem
        from judge.views.problem_create_v2 import ProblemCreateV2View

        editor = create_user(username='pmr_editor', user_permissions=('create_private_problem',))
        client = Client()
        client.force_login(editor)
        payload = {
            'type': 'blank', 'name': 'Review me', 'description': 'Q {{blank}}',
            'points': 4, 'blanks': ['ans'],
            'ai': True, 'aiReview': True, 'rubric': 'any',
        }
        response = client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                               content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        config = ProblemFillBlankConfig.objects.get(problem=problem)
        self.assertTrue(config.requires_manual_review)

        # Edit prefill must round-trip the flag.
        data = ProblemCreateV2View()._build_edit_data(problem)
        self.assertTrue(data['aiReview'])

    def test_short_answer_requires_manual_review_round_trip(self):
        from judge.models import Problem
        from judge.views.problem_create_v2 import ProblemCreateV2View

        editor = create_user(username='pmr_editor2', user_permissions=('create_private_problem',))
        client = Client()
        client.force_login(editor)
        payload = {
            'type': 'short', 'name': 'Review short', 'description': 'Explain X.',
            'points': 4, 'shortAnswer': 'the answer', 'aiReview': True,
        }
        response = client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                               content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        config = ProblemShortAnswerConfig.objects.get(problem=problem)
        self.assertTrue(config.requires_manual_review)
        self.assertTrue(ProblemCreateV2View()._build_edit_data(problem)['aiReview'])

    def test_requires_manual_review_defaults_false(self):
        from judge.models import Problem
        editor = create_user(username='pmr_editor3', user_permissions=('create_private_problem',))
        client = Client()
        client.force_login(editor)
        payload = {
            'type': 'blank', 'name': 'No review', 'description': 'Q {{blank}}',
            'points': 2, 'blanks': ['ans'], 'ai': True,
        }
        response = client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                               content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertFalse(ProblemFillBlankConfig.objects.get(problem=problem).requires_manual_review)


class GradePageFilterDefaultsTestCase(GradeTestBase):
    """Queue defaults to 'final version'; Participation defaults to 'Live'."""

    def test_queue_defaults_to_final(self):
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        # The final-version option is the selected default.
        self.assertIn('<option value="final" selected>', html)

    def test_participation_defaults_to_live(self):
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('<option value="live" selected>', html)

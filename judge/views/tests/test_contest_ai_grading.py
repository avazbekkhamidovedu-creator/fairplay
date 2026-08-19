"""Contest-level AI grading: realtime vs batch, latest-submission only."""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestSubmission, Language, ProblemCodeConfig, Submission, SubmissionSource
from judge.models.tests.util import CommonDataMixin, create_contest, create_contest_participation, \
    create_contest_problem, create_problem, create_user
from judge.utils.contest_ai import (
    is_latest_submission, latest_submissions_for_contest, should_run_ai_now, student_can_trigger_ai,
    supersede_older_in_flight,
)


class ContestAIGradingCreateTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(
            username='ai_grade_editor',
            user_permissions=(
                'edit_all_contest', 'contest_rating', 'lock_contest', 'contest_access_code',
                'contest_seb_requirement', 'create_private_contest', 'change_contest_visibility',
                'contest_problem_label', 'override_performance_ceiling',
            ),
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)

    def _post(self, **overrides):
        now = timezone.now()
        data = {
            'name': 'AI Homework',
            'key': 'aihw',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }
        data.update(overrides)
        return self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )

    def test_create_form_includes_ai_grading_controls(self):
        response = self.client.get(reverse('contest_create'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id_ai_grading_mode', html)
        self.assertIn('id_ai_grading_auto_delay_minutes', html)
        self.assertIn('latest submission', html.lower())

    def test_default_mode_is_off(self):
        response = self._post()
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='aihw')
        self.assertEqual(contest.ai_grading_mode, Contest.AI_GRADING_OFF)
        self.assertEqual(contest.ai_grading_auto_delay_minutes, 30)

    def test_realtime_mode_saved(self):
        response = self._post(key='aihwrt', ai_grading_mode='realtime')
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='aihwrt')
        self.assertEqual(contest.ai_grading_mode, Contest.AI_GRADING_REALTIME)

    def test_batch_mode_and_manual_only_delay(self):
        response = self._post(
            key='aihwex', ai_grading_mode='batch', ai_grading_auto_delay_minutes=0,
        )
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='aihwex')
        self.assertEqual(contest.ai_grading_mode, Contest.AI_GRADING_BATCH)
        self.assertEqual(contest.ai_grading_auto_delay_minutes, 0)

    def test_invalid_mode_rejected(self):
        response = self._post(key='aihwbad', ai_grading_mode='whenever')
        self.assertEqual(response.status_code, 400)


class ContestAIGradingHelpersTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.author = create_user(username='ai_helper_author', user_permissions=('edit_own_contest',))
        cls.student = create_user(username='ai_helper_student')
        cls.problem = create_problem(code='aihelpcode', submission_type='C', points=100)
        ProblemCodeConfig.objects.create(problem=cls.problem, ai_enabled=True, ai_rubric='rubric')
        cls.contest = create_contest(
            key='aihelp',
            authors=('ai_helper_author',),
            ai_grading_mode=Contest.AI_GRADING_REALTIME,
        )
        cls.cp = create_contest_problem(contest=cls.contest, problem=cls.problem, points=100, order=1)
        cls.part = create_contest_participation(contest=cls.contest, user=cls.student.profile)
        lang = Language.get_default_language()
        cls.older = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem, language=lang,
            status='QU', result=None, contest_object=cls.contest,
        )
        SubmissionSource.objects.create(submission=cls.older, source='print(1)')
        cls.newer = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem, language=lang,
            status='QU', result=None, contest_object=cls.contest,
        )
        SubmissionSource.objects.create(submission=cls.newer, source='print(2)')
        ContestSubmission.objects.create(
            submission=cls.older, problem=cls.cp, participation=cls.part, points=0,
        )
        ContestSubmission.objects.create(
            submission=cls.newer, problem=cls.cp, participation=cls.part, points=0,
        )

    def setUp(self):
        self.contest.refresh_from_db()
        self.older = Submission.objects.select_related('contest_object', 'problem', 'user__user').get(pk=self.older.pk)
        self.newer = Submission.objects.select_related('contest_object', 'problem', 'user__user').get(pk=self.newer.pk)

    def test_is_latest_submission(self):
        self.assertFalse(is_latest_submission(self.older))
        self.assertTrue(is_latest_submission(self.newer))

    def test_should_run_ai_now_realtime_deferred(self):
        self.assertFalse(should_run_ai_now(self.newer))
        self.assertTrue(should_run_ai_now(self.newer, manual_trigger=True))

    def test_should_run_ai_now_batch_skips_live(self):
        self.contest.ai_grading_mode = Contest.AI_GRADING_BATCH
        self.contest.save(update_fields=['ai_grading_mode'])
        self.newer = Submission.objects.select_related('contest_object', 'problem').get(pk=self.newer.pk)
        self.assertFalse(should_run_ai_now(self.newer))
        self.assertTrue(should_run_ai_now(self.newer, batch_pass=True))

    def test_should_run_ai_now_off(self):
        self.contest.ai_grading_mode = Contest.AI_GRADING_OFF
        self.contest.save(update_fields=['ai_grading_mode'])
        self.newer = Submission.objects.select_related('contest_object', 'problem').get(pk=self.newer.pk)
        self.assertFalse(should_run_ai_now(self.newer))

    def test_latest_submissions_for_contest_picks_newest(self):
        latest = latest_submissions_for_contest(self.contest)
        self.assertEqual(latest[(self.part.id, self.cp.id)].id, self.newer.id)

    def test_supersede_aborts_older_in_realtime(self):
        self.contest.ai_grading_mode = Contest.AI_GRADING_OFF
        self.contest.save(update_fields=['ai_grading_mode'])
        self.newer.contest_object = None
        self.newer.contest_object_id = None
        self.newer.save(update_fields=['contest_object'])
        self.older.contest_object = None
        self.older.contest_object_id = None
        self.older.save(update_fields=['contest_object'])
        self.newer = Submission.objects.select_related('contest_object', 'problem').get(pk=self.newer.pk)
        with mock.patch.object(Submission, 'abort') as abort:
            aborted = supersede_older_in_flight(self.newer)
        self.assertEqual(aborted, 1)
        abort.assert_called_once()

    def test_supersede_skips_batch(self):
        self.contest.ai_grading_mode = Contest.AI_GRADING_BATCH
        self.contest.save(update_fields=['ai_grading_mode'])
        self.newer = Submission.objects.select_related('contest_object', 'problem').get(pk=self.newer.pk)
        with mock.patch.object(Submission, 'abort') as abort:
            aborted = supersede_older_in_flight(self.newer)
        self.assertEqual(aborted, 0)
        abort.assert_not_called()

    def test_student_can_trigger_only_latest_done_realtime(self):
        self.newer.status = 'D'
        self.newer.result = 'AC'
        self.newer.save(update_fields=['status', 'result'])
        # Student quota is 0 by default — button hidden for students.
        self.assertFalse(student_can_trigger_ai(self.student, self.newer))
        self.assertFalse(student_can_trigger_ai(self.student, self.older))
        # Teachers can always trigger in on-demand (realtime) mode.
        self.assertTrue(student_can_trigger_ai(self.author, self.newer))

        self.contest.ai_grading_mode = Contest.AI_GRADING_BATCH
        self.contest.save(update_fields=['ai_grading_mode'])
        self.newer = Submission.objects.select_related('contest_object', 'problem', 'user__user').get(pk=self.newer.pk)
        self.assertFalse(student_can_trigger_ai(self.student, self.newer))
        self.assertTrue(student_can_trigger_ai(self.author, self.newer))


class ContestBatchAIGradeViewTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.author = create_user(username='ai_batch_author', user_permissions=('edit_own_contest',))
        cls.student = create_user(username='ai_batch_student')
        cls.contest = create_contest(
            key='aibatch',
            authors=('ai_batch_author',),
            ai_grading_mode=Contest.AI_GRADING_BATCH,
            is_visible=True,
        )
        cls.url = reverse('contest_grade_ai', args=[cls.contest.key])
        cls.grade_url = reverse('contest_grade', args=[cls.contest.key])

    def setUp(self):
        self.contest.refresh_from_db()

    def test_grade_page_shows_button_in_batch_mode(self):
        client = Client()
        client.force_login(self.author)
        response = client.get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['grade_ai_batch'])
        self.assertIn('Run AI grading', response.content.decode())

    def test_post_queues_task(self):
        client = Client()
        client.force_login(self.author)
        with mock.patch('judge.tasks.contest.batch_ai_grade.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = client.post(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['ok'])
        delay.assert_called_once_with(self.contest.key, auto=False)

    def test_post_rejected_when_not_batch(self):
        self.contest.ai_grading_mode = Contest.AI_GRADING_REALTIME
        self.contest.save(update_fields=['ai_grading_mode'])
        client = Client()
        client.force_login(self.author)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 400)

    def test_student_forbidden(self):
        client = Client()
        client.force_login(self.student)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_contest_page_explains_batch(self):
        client = Client()
        client.force_login(self.author)
        html = client.get(reverse('contest_view', args=[self.contest.key])).content.decode()
        self.assertIn('latest submission', html.lower())

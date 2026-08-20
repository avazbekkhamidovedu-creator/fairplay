"""Assignment/exam submission visibility: classmates must not see list or source.

Course assignments are not a public OJ. Practice AC on a public problem must
not unlock an in-contest source; a Visible scoreboard must not dump the field's
submissions. Workspace “My only” is unchanged (own rows only).
"""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestParticipation, ContestSubmission, Language, Submission, SubmissionSource
from judge.models.problem import SubmissionSourceAccess
from judge.models.tests.util import create_contest, create_contest_participation, create_contest_problem, \
    create_problem, create_user


class AssignmentSubmissionVisibilityTestCase(TestCase):
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.student = create_user(username='asgnvis-student')
        cls.classmate = create_user(username='asgnvis-classmate')
        cls.teacher = create_user(
            username='asgnvis-teacher',
            user_permissions=('edit_own_contest', 'edit_own_problem'),
        )
        cls.lang = Language.get_python3()
        now = timezone.now()
        cls.problem = create_problem(
            code='asgnvisp', name='Visibility Prob', is_public=True, points=1,
        )
        cls.contest = create_contest(
            key='asgnvis', name='Visibility HW', is_visible=True,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
            authors=('asgnvis-teacher',),
            scoreboard_visibility=Contest.SCOREBOARD_VISIBLE,
        )
        cls.contest_problem = create_contest_problem(
            contest=cls.contest, problem=cls.problem, points=100, order=1,
        )
        cls.student_part = create_contest_participation(
            contest=cls.contest, user=cls.student.profile,
        )
        cls.classmate_part = create_contest_participation(
            contest=cls.contest, user=cls.classmate.profile,
        )
        cls.student.profile.current_contest = cls.student_part
        cls.student.profile.save()

        cls.practice_ac = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem, language=cls.lang,
            result='AC', status='D', points=1, case_total=1, contest_object=None,
            time=0.1, memory=1024,
        )
        SubmissionSource.objects.create(submission=cls.practice_ac, source='print("practice")')
        cls.classmate_practice = Submission.objects.create(
            user=cls.classmate.profile, problem=cls.problem, language=cls.lang,
            result='AC', status='D', points=1, case_total=1, contest_object=None,
            time=0.1, memory=1024,
        )
        SubmissionSource.objects.create(submission=cls.classmate_practice, source='print("classmate-p")')

        cls.own_contest_sub = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem, language=cls.lang,
            result='WA', status='D', points=40, case_total=100,
            contest_object=cls.contest, time=0.1, memory=1024,
        )
        SubmissionSource.objects.create(submission=cls.own_contest_sub, source='print("own")')
        ContestSubmission.objects.create(
            submission=cls.own_contest_sub, problem=cls.contest_problem,
            participation=cls.student_part, points=40,
        )
        cls.classmate_contest_sub = Submission.objects.create(
            user=cls.classmate.profile, problem=cls.problem, language=cls.lang,
            result='AC', status='D', points=80, case_total=100,
            contest_object=cls.contest, time=0.1, memory=1024,
        )
        SubmissionSource.objects.create(submission=cls.classmate_contest_sub, source='print("secret")')
        ContestSubmission.objects.create(
            submission=cls.classmate_contest_sub, problem=cls.contest_problem,
            participation=cls.classmate_part, points=80,
        )
        cls.student_part.recompute_results()
        cls.classmate_part.recompute_results()

        cls.own_list_url = reverse(
            'contest_user_submissions',
            args=[cls.contest.key, cls.student.username, cls.problem.code],
        )
        cls.classmate_list_url = reverse(
            'contest_user_submissions',
            args=[cls.contest.key, cls.classmate.username, cls.problem.code],
        )
        cls.classmate_all_url = reverse(
            'contest_all_user_submissions',
            args=[cls.contest.key, cls.classmate.username],
        )
        cls.own_all_url = reverse(
            'contest_all_user_submissions',
            args=[cls.contest.key, cls.student.username],
        )

    def setUp(self):
        # setUpTestData objects are reused; reload DB state after mutating tests.
        self.contest.refresh_from_db()
        self.contest_problem.refresh_from_db()
        self.student_part.refresh_from_db()
        self.classmate_part.refresh_from_db()
        self.problem.refresh_from_db()

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_practice_ac_does_not_unlock_contest_source(self):
        self.assertTrue(self.problem.is_solved_by(self.student))
        self.assertFalse(self.classmate_contest_sub.can_see_detail(self.student))
        self.assertTrue(self.own_contest_sub.can_see_detail(self.student))
        self.assertTrue(self.classmate_contest_sub.can_see_detail(self.teacher))
        self.assertTrue(self.classmate_practice.can_see_detail(self.student))

    def test_status_and_source_forbidden_for_classmate_contest_row(self):
        client = self._client(self.student)
        status = reverse('submission_status', args=[self.classmate_contest_sub.id])
        source = reverse('submission_source', args=[self.classmate_contest_sub.id])
        self.assertIn(client.get(status).status_code, (403, 404))
        self.assertIn(client.get(source).status_code, (403, 404))
        own = reverse('submission_status', args=[self.own_contest_sub.id])
        self.assertEqual(client.get(own).status_code, 200)

    def test_editor_can_open_classmate_contest_source(self):
        client = self._client(self.teacher)
        status = reverse('submission_status', args=[self.classmate_contest_sub.id])
        self.assertEqual(client.get(status).status_code, 200)
        grade = reverse('contest_grade', args=[self.contest.key])
        self.assertEqual(client.get(grade).status_code, 200)

    def test_in_contest_big_list_is_own_rows_only(self):
        client = self._client(self.student)
        response = client.get(reverse('all_submissions'))
        self.assertEqual(response.status_code, 200)
        content = response.content
        self.assertIn(('id="%d"' % self.own_contest_sub.id).encode(), content)
        self.assertNotIn(('id="%d"' % self.classmate_contest_sub.id).encode(), content)
        self.assertNotIn(b'print("secret")', content)

    def test_in_contest_list_hides_view_link_for_missing_classmate_rows(self):
        html = self._client(self.student).get(reverse('all_submissions')).content.decode()
        self.assertNotIn(reverse('submission_status', args=[self.classmate_contest_sub.id]), html)

    def test_contest_user_submissions_other_username_is_404(self):
        client = self._client(self.student)
        self.assertEqual(client.get(self.classmate_list_url).status_code, 404)
        self.assertEqual(client.get(self.classmate_all_url).status_code, 404)
        self.assertEqual(client.get(self.own_list_url).status_code, 200)
        self.assertEqual(client.get(self.own_all_url).status_code, 200)

    def test_editor_contest_user_submissions_still_200(self):
        client = self._client(self.teacher)
        self.assertEqual(client.get(self.classmate_list_url).status_code, 200)
        self.assertEqual(client.get(self.classmate_all_url).status_code, 200)

    def test_student_ranking_omits_classmate_submission_list_urls(self):
        html = self._client(self.student).get(
            reverse('contest_ranking', args=[self.contest.key]),
        ).content.decode()
        self.assertNotIn(self.classmate_list_url, html)
        self.assertNotIn(self.classmate_all_url, html)
        self.assertIn(self.own_list_url, html)
        # Scores on a Visible scoreboard remain visible.
        self.assertIn('80', html)

    def test_editor_ranking_still_deep_links_grade(self):
        html = self._client(self.teacher).get(
            reverse('contest_ranking', args=[self.contest.key]),
        ).content.decode()
        self.assertIn(
            '%s?submission=%d' % (reverse('contest_grade', args=[self.contest.key]),
                                  self.classmate_contest_sub.id),
            html,
        )
        self.assertNotIn(self.classmate_list_url, html)

    def test_format_cells_omit_href_when_cell_url_empty(self):
        for format_name in ('default', 'exam', 'icpc', 'ecoo', 'atcoder', 'ioi'):
            self.contest.format_name = format_name
            self.contest.format_config = {}
            self.contest.save()
            self.classmate_part.recompute_results()
            self.classmate_part.refresh_from_db()
            html = str(self.contest.format.display_user_problem(
                self.classmate_part, self.contest_problem, cell_url=''))
            self.assertNotIn('contest_user_submissions', html, format_name)
            self.assertNotIn(self.classmate.username, html, format_name)
            result = str(self.contest.format.display_participation_result(
                self.classmate_part, result_url=''))
            self.assertNotIn('contest_all_user_submissions', result, format_name)
            self.assertNotIn(self.classmate.username, result, format_name)

    def test_exam_hidden_checkmark_has_no_classmate_list_link(self):
        from judge.contest_format.exam import ExamContestFormat

        self.classmate_part.format_data = {
            str(self.contest_problem.id): {'time': 12, 'points': 0, 'hidden': True},
        }
        fmt = ExamContestFormat(self.contest, {})
        html = str(fmt.display_user_problem(
            self.classmate_part, self.contest_problem, cell_url=''))
        self.assertIn('submitted-hidden', html)
        self.assertNotIn(self.classmate_list_url, html)
        self.assertNotIn('<a href', html)

        exam = create_contest(
            key='asgnvisexam', name='Visibility Exam', is_visible=True,
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1),
            authors=('asgnvis-teacher',),
            format_name='exam',
            scoreboard_visibility=Contest.SCOREBOARD_VISIBLE,
        )
        ecp = create_contest_problem(contest=exam, problem=self.problem, points=100, order=1)
        ecp.hide_results = True
        ecp.save(update_fields=['hide_results'])
        part = ContestParticipation.objects.create(
            contest=exam, user=self.classmate.profile,
            real_start=timezone.now() - timedelta(hours=1),
        )
        sub = Submission.objects.create(
            user=self.classmate.profile, problem=self.problem, language=self.lang,
            result='AC', status='D', points=80, case_total=100,
            contest_object=exam, time=0.1, memory=1024,
        )
        ContestSubmission.objects.create(
            submission=sub, problem=ecp, participation=part, points=80,
        )
        part.recompute_results()
        ranking = self._client(self.student).get(
            reverse('contest_ranking', args=[exam.key]),
        ).content.decode()
        classmate_exam_list = reverse(
            'contest_user_submissions',
            args=[exam.key, self.classmate.username, self.problem.code],
        )
        self.assertNotIn(classmate_exam_list, ranking)
        self.assertIn('submitted-hidden', ranking)

    def test_always_visibility_still_denies_contest_source(self):
        self.problem.submission_source_visibility_mode = SubmissionSourceAccess.ALWAYS
        self.problem.save(update_fields=['submission_source_visibility_mode'])
        self.assertFalse(self.classmate_contest_sub.can_see_detail(self.student))
        self.assertTrue(self.classmate_practice.can_see_detail(self.student))

    def test_contest_ranked_submissions_own_only_for_students(self):
        url = reverse('contest_ranked_submissions', args=[self.contest.key, self.problem.code])
        html = self._client(self.student).get(url).content.decode()
        self.assertNotIn(('id="%d"' % self.classmate_contest_sub.id), html)
        editor_html = self._client(self.teacher).get(url).content.decode()
        self.assertIn(('id="%d"' % self.classmate_contest_sub.id), editor_html)

"""FairPlay hiding rule, route by route: denied to the exam's students, allowed to its staff."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestParticipation, ContestSubmission, EditorAttachment, Language, Problem, \
    Submission, SubmissionSource, SubmissionTestCase
from judge.models.tests.util import create_contest_problem, create_user
from judge.utils.fairplay import fairplay_hidden_for, fairplay_hidden_q, fairplay_public_problems
from judge.utils.fairplay_delivery import note_fairplay_closes, record_added_minutes
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin, create_contest, set_fairplay

MARK = 'Write it.'  # text of the code problem's statement


def hidden(response, marker=None):
    """A denial page/404/403, never the protected content."""
    body = response.content.decode(errors='replace')
    denied = response.status_code in (403, 404) or 'No such problem' in body or 'No such contest' in body
    return denied and (marker is None or marker not in body)


@FAIRPLAY_ON
class FairPlayHidingRoutesTestCase(FairPlayDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Problem.objects.filter(pk=cls.code.pk).update(view_tester=True, view_test_cases=True)
        cls.code.refresh_from_db()
        cls.py3 = Language.objects.get(key='PY3')
        cls.py3.template = 'DEFAULT-PY3-TEMPLATE'
        cls.py3.save()
        participation = ContestParticipation.objects.create(contest=cls.exam, user=cls.student.profile)
        cls.participation = participation
        cls.submission = Submission.objects.create(user=cls.student.profile, problem=cls.code, language=cls.py3,
                                                   contest_object=cls.exam, status='D', result='WA',
                                                   case_points=0, case_total=1, time=0.1, memory=1024)
        SubmissionSource.objects.create(submission=cls.submission, source='print(42)')
        ContestSubmission.objects.create(submission=cls.submission, participation=participation,
                                         problem=cls.exam.contest_problems.get(problem=cls.code))
        SubmissionTestCase.objects.create(submission=cls.submission, case=1, status='WA', points=0, total=1,
                                          time=0.1, memory=1024)
        # A practice submission on the same (public) problem, outside any contest.
        cls.practice = Submission.objects.create(user=cls.classmate.profile, problem=cls.code, language=cls.py3,
                                                 status='D', result='AC', time=0.1, memory=1024)
        SubmissionSource.objects.create(submission=cls.practice, source='print(1)')
        cls.attachment = EditorAttachment(original_name='cell.png', size=3)

    def setUp(self):
        self.client = Client()

    def as_user(self, user):
        client = Client()
        if not isinstance(user, AnonymousUser):
            client.force_login(user)
        return client

    def students(self):
        return [self.student, self.classmate, self.outsider]

    def staff_users(self):
        return [self.teacher, self.tester, self.course_admin, self.staff]

    def test_contest_page(self):
        url = reverse('contest_view', args=['fpexam'])
        for user in [self.outsider, AnonymousUser()]:
            with self.subTest(user=str(user)):
                self.assertTrue(hidden(self.as_user(user).get(url), 'CS 101 Midterm'))
        for user in [self.student, self.classmate]:
            with self.subTest(user=str(user)):
                response = self.as_user(user).get(url)
                self.assertContains(response, 'Start exam in FairPlay')
                self.assertNotContains(response, MARK)
        for user in self.staff_users():
            with self.subTest(user=str(user)):
                response = self.as_user(user).get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'CS 101 Midterm')

    def test_join(self):
        client = self.as_user(self.classmate)
        response = client.post(reverse('contest_join', args=['fpexam']))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('contest_fairplay_launch', args=['fpexam']))
        self.assertNotContains(client.get(response['Location']), MARK)
        self.assertFalse(ContestParticipation.objects.filter(contest=self.exam, user=self.classmate.profile).exists())
        self.classmate.profile.refresh_from_db()
        self.assertIsNone(self.classmate.profile.current_contest)

    def test_session_in_exam_is_dropped(self):
        profile = self.student.profile
        profile.current_contest = self.participation
        profile.save()
        client = self.as_user(self.student)
        client.get(reverse('problem_list'))
        profile.refresh_from_db()
        self.assertIsNone(profile.current_contest)
        self.assertTrue(hidden(client.get(reverse('problem_detail', args=['fpcode'])), MARK))

    def test_ranking_stats_and_participations(self):
        client = self.as_user(self.student)
        for name in ('contest_ranking', 'contest_participation_own', 'contest_stats'):
            with self.subTest(route=name):
                self.assertTrue(hidden(client.get(reverse(name, args=['fpexam']))))
        self.assertEqual(client.get(reverse('contest_ranking_ajax', args=['fpexam'])).status_code, 400)

    def test_contest_exports(self):
        client = self.as_user(self.student)
        for name in ('contest_export_pdf', 'contest_export_word'):
            with self.subTest(route=name):
                self.assertTrue(hidden(client.post(reverse(name, args=['fpexam'])), MARK))

    def test_contest_submissions_pages(self):
        client = self.as_user(self.student)
        for url in (reverse('contest_all_user_submissions', args=['fpexam', 'fp_student']),
                    reverse('contest_user_submissions', args=['fpexam', 'fp_student', 'fpcode']),
                    reverse('contest_ranked_submissions', args=['fpexam', 'fpcode'])):
            with self.subTest(url=url):
                self.assertTrue(hidden(client.get(url)))

    def test_contest_lists_and_api(self):
        for user in (self.student, self.outsider, AnonymousUser()):
            client = self.as_user(user)
            with self.subTest(user=str(user)):
                response = client.get(reverse('contest_list'))
                if user == self.student:
                    self.assertContains(response, 'CS 101 Midterm')
                    self.assertContains(response, reverse('contest_fairplay_launch', args=['fpexam']))
                    self.assertNotContains(response, MARK)
                else:
                    self.assertNotContains(response, 'CS 101 Midterm')
                data = client.get('/api/v2/contests').json()
                self.assertNotIn('fpexam', [c['key'] for c in data['data']['objects']])
                self.assertEqual(client.get('/api/v2/contest/fpexam').status_code, 404)
                participations = client.get('/api/v2/participations', {'contest': 'fpexam'}).json()
                self.assertEqual(participations['data']['objects'], [])
        teacher = self.as_user(self.teacher)
        self.assertIn('fpexam', [c['key'] for c in teacher.get('/api/v2/contests').json()['data']['objects']])
        self.assertEqual(teacher.get('/api/v2/contest/fpexam').status_code, 200)

    def test_contest_select2(self):
        response = self.as_user(self.student).get(reverse('submission_contest_filter_select2'), {'term': 'fp'})
        self.assertNotIn('fpexam', response.content.decode())

    def test_problem_pages(self):
        for name in ('problem_detail', 'problem_submit', 'chronological_submissions', 'ranked_submissions',
                     'problem_workspace_submissions'):
            url = reverse(name, args=['fpcode'])
            for user in self.students():
                with self.subTest(route=name, user=str(user)):
                    self.assertTrue(hidden(self.as_user(user).get(url), MARK))
        url = reverse('user_submissions', args=['fpcode', 'fp_student'])
        self.assertTrue(hidden(self.as_user(self.student).get(url)))
        self.assertTrue(hidden(Client().get(reverse('problem_detail', args=['fpcode'])), MARK))
        for user in self.staff_users():
            with self.subTest(user=str(user)):
                self.assertContains(self.as_user(user).get(reverse('problem_detail', args=['fpcode'])), MARK)

    def test_problem_pdf(self):
        with mock.patch('judge.views.problem.PDF_RENDERING_ENABLED', True):
            self.assertEqual(self.as_user(self.student).get(reverse('problem_pdf', args=['fpcode'])).status_code, 404)

    def test_practice_submit_refused(self):
        before = Submission.objects.count()
        response = self.as_user(self.classmate).post(reverse('problem_submit', args=['fpcode']),
                                                     {'source': 'print(1)', 'language': self.py3.id})
        self.assertTrue(hidden(response))
        self.assertEqual(Submission.objects.count(), before)

    def test_problem_lists_and_api(self):
        for user in (self.student, AnonymousUser()):
            client = self.as_user(user)
            with self.subTest(user=str(user)):
                self.assertNotContains(client.get(reverse('problem_list')), 'Reverse a list')
                codes = [p['code'] for p in client.get('/api/v2/problems').json()['data']['objects']]
                self.assertNotIn('fpcode', codes)
                self.assertEqual(client.get('/api/v2/problem/fpcode').status_code, 404)
        teacher = self.as_user(self.teacher)
        self.assertContains(teacher.get(reverse('problem_list')), 'Reverse a list')
        self.assertEqual(teacher.get('/api/v2/problem/fpcode').status_code, 200)

    def test_problem_select2_and_public_feeds(self):
        response = self.as_user(self.student).get(reverse('problem_select2'), {'term': 'fpcode'})
        self.assertNotIn('fpcode', response.content.decode())
        response = self.as_user(self.student).get(reverse('submission_problem_filter_select2'), {'term': 'fpcode'})
        self.assertNotIn('fpcode', response.content.decode())
        self.assertNotIn('fpcode', Client().get(reverse('problem_rss')).content.decode())
        self.assertFalse(fairplay_public_problems().filter(code='fpcode').exists())
        # Points still count it (Profile.calculate_points uses every public problem).
        self.assertTrue(Problem.get_public_problems().filter(code='fpcode').exists())

    def test_tester_and_test_case_downloads(self):
        student = self.as_user(self.student)
        self.assertEqual(student.get(reverse('problem_download_tester', args=['fpcode'])).status_code, 404)
        url = reverse('submission_test_case_download', args=[self.submission.id, 1, 'in'])
        self.assertEqual(student.get(url).status_code, 404)
        response = self.as_user(self.teacher).get(reverse('problem_download_tester', args=['fpcode']))
        self.assertEqual(response.status_code, 200)

    def test_starter_template(self):
        url = reverse('language_template_ajax')
        query = {'id': self.py3.id, 'problem_id': self.code.id}
        self.assertEqual(self.as_user(self.student).get(url, query).content.decode(), 'DEFAULT-PY3-TEMPLATE')
        self.assertEqual(self.as_user(self.teacher).get(url, query).content.decode(),
                         'def reverse(head):\n    pass\n')

    def test_editor_attachment(self):
        attachment = EditorAttachment(original_name='cell.png', size=3)
        attachment.file.save('cell.png', ContentFile(b'png'), save=False)
        attachment.save()
        Problem.objects.filter(pk=self.code.pk).update(
            description=MARK + ' ![cell](/widgets/files/%s/)' % attachment.uuid)
        url = reverse('editor_file_download', args=[attachment.uuid])
        self.assertEqual(self.as_user(self.student).get(url).status_code, 404)
        self.assertEqual(Client().get(url).status_code, 404)
        self.assertEqual(self.as_user(self.teacher).get(url).status_code, 200)

    def test_reused_problem_in_open_contest(self):
        now = timezone.now()
        other = create_contest('fpopen', start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1),
                               is_visible=True)
        create_contest_problem(contest=other, problem=self.code, points=10, order=1)
        participation = ContestParticipation.objects.create(contest=other, user=self.classmate.profile)
        self.classmate.profile.current_contest = participation
        self.classmate.profile.save()
        client = self.as_user(self.classmate)
        self.assertContains(client.get(reverse('contest_view', args=['fpopen'])), 'fpopen')
        self.assertTrue(hidden(client.get(reverse('problem_detail', args=['fpcode'])), MARK))

    def test_submission_detail_pages(self):
        student = self.as_user(self.student)
        for name in ('submission_status', 'submission_source', 'submission_source_raw'):
            with self.subTest(route=name):
                self.assertTrue(hidden(student.get(reverse(name, args=[self.submission.id])), 'print(42)'))
        self.assertEqual(student.get(reverse('submission_testcases_query'), {'id': self.submission.id}).status_code,
                         404)
        self.assertEqual(student.get('/api/v2/submission/%d' % self.submission.id).status_code, 403)
        self.assertEqual(student.get(reverse('submission_single_query'), {'id': self.submission.id}).status_code,
                         404)
        teacher = self.as_user(self.teacher)
        self.assertEqual(teacher.get(reverse('submission_status', args=[self.submission.id])).status_code, 200)

    def test_submission_lists_filters_and_counts(self):
        for user in (self.student, self.classmate):
            client = self.as_user(user)
            with self.subTest(user=str(user)):
                for query in ({}, {'problem': 'fpcode'}, {'contest': 'fpexam'}):
                    body = client.get(reverse('all_submissions'), query).content.decode()
                    self.assertNotIn('/submission/%d"' % self.submission.id, body)
                    self.assertNotIn('/submission/%d"' % self.practice.id, body)
                body = client.get(reverse('all_user_submissions', args=['fp_student'])).content.decode()
                self.assertNotIn('/submission/%d"' % self.submission.id, body)
                ids = [s['id'] for s in client.get('/api/v2/submissions').json()['data']['objects']]
                self.assertNotIn(self.submission.id, ids)
                self.assertNotIn(self.practice.id, ids)
        from judge.views.submission import AllSubmissions
        view = AllSubmissions()
        view.request = mock.Mock(user=self.student, profile=self.student.profile, LANGUAGE_CODE='en')
        view.selected_languages = view.selected_statuses = ()
        self.assertFalse(view._get_queryset().filter(problem=self.code).exists())
        teacher_body = self.as_user(self.teacher).get(reverse('all_submissions')).content.decode()
        self.assertIn('/submission/%d"' % self.submission.id, teacher_body)

    def test_visible_again_after_end_and_finalize(self):
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(minutes=1))
        client = self.as_user(self.student)
        self.assertTrue(hidden(client.get(reverse('problem_detail', args=['fpcode'])), MARK))
        Contest.objects.filter(pk=self.exam.pk).update(results_finalized=True)
        # No minutes added on the FairPlay tab (DKU OJ's count): released at once.
        record_added_minutes(self.exam, 0)
        self.assertContains(client.get(reverse('problem_detail', args=['fpcode'])), MARK)
        self.assertContains(client.get(reverse('contest_view', args=['fpexam'])), 'CS 101 Midterm')
        self.assertEqual(client.get(reverse('submission_status', args=[self.submission.id])).status_code, 200)

    def test_bridge_switch_does_not_unhide(self):
        # Turning the bridge off must not expose a running FairPlay exam.
        from judge.views.tests.fairplay_base import FAIRPLAY_OFF
        with FAIRPLAY_OFF:
            self.assertTrue(hidden(self.as_user(self.student).get(reverse('problem_detail', args=['fpcode'])), MARK))


@FAIRPLAY_ON
class FairPlayReusedProblemTestCase(FairPlayDataMixin, TestCase):
    """An ordinary assignment reusing a problem of a FairPlay exam in its window."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from judge.models.tests.util import create_problem
        now = timezone.now()
        cls.plain = create_problem('fpplainq', name='Plain question', is_public=True, description='PLAIN-TEXT')
        cls.other = create_contest('fpopen', name='Open practice', start_time=now - timedelta(days=2),
                                   end_time=now - timedelta(days=1), is_visible=True)
        create_contest_problem(contest=cls.other, problem=cls.plain, points=1, order=1)
        create_contest_problem(contest=cls.other, problem=cls.code, points=1, order=2)
        Submission.objects.create(user=cls.classmate.profile, problem=cls.code, language=cls.code.allowed_languages
                                  .first(), status='D', result='AC', points=30, case_points=1, case_total=1)

    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client

    def exported_html(self, user, name):
        import sys
        captured = []

        def convert_text(html, *args, outputfile=None, **kwargs):
            captured.append(html)
            open(outputfile, 'wb').close()

        fake = mock.Mock(convert_text=convert_text)
        with mock.patch.dict(sys.modules, {'pypandoc': fake}):
            response = self.client_for(user).post(reverse(name, args=['fpopen']))
        self.assertEqual(response.status_code, 200, response.content[:300])
        return captured[0]

    def test_exports_skip_the_hidden_problem(self):
        for name in ('contest_export_pdf', 'contest_export_word'):
            with self.subTest(route=name):
                html = self.exported_html(self.classmate, name)
                self.assertIn('PLAIN-TEXT', html)
                self.assertNotIn(MARK, html)
                self.assertIn(MARK, self.exported_html(self.teacher, name))

    def test_contest_page_and_api_skip_the_hidden_problem(self):
        client = self.client_for(self.classmate)
        page = client.get(reverse('contest_view', args=['fpopen']))
        self.assertContains(page, 'Plain question')
        self.assertNotContains(page, 'Reverse a list')
        data = client.get('/api/v2/contest/fpopen').json()['data']['object']
        self.assertEqual([p['code'] for p in data['problems']], ['fpplainq'])
        teacher = self.client_for(self.teacher).get('/api/v2/contest/fpopen').json()['data']['object']
        self.assertEqual([p['code'] for p in teacher['problems']], ['fpplainq', 'fpcode'])

    def test_user_pages_skip_the_hidden_problem(self):
        client = self.client_for(self.student)
        self.assertNotContains(client.get(reverse('user_problems', args=['fp_classmate'])), 'Reverse a list')
        solved = client.get('/api/v2/user/fp_classmate').json()['data']['object']['solved_problems']
        self.assertNotIn('fpcode', solved)
        self.assertContains(self.client_for(self.teacher).get(reverse('user_problems', args=['fp_classmate'])),
                            'Reverse a list')

    def test_points_do_not_change(self):
        from judge.models import Profile
        exam = Contest.objects.get(pk=self.exam.pk)
        exam.tags.clear()
        profile = Profile.objects.get(pk=self.classmate.profile.pk)
        profile.calculate_points()
        before = Profile.objects.get(pk=profile.pk).points
        self.assertGreater(before, 0)
        set_fairplay(exam, True)
        profile.calculate_points()
        self.assertEqual(Profile.objects.get(pk=profile.pk).points, before)


@FAIRPLAY_ON
class FairPlayHidingWindowTestCase(FairPlayDataMixin, TestCase):
    """The exam hides from 24 hours before its start until it is released."""

    def set_times(self, start, end, finalized=False):
        Contest.objects.filter(pk=self.exam.pk).update(start_time=start, end_time=end, results_finalized=finalized)

    def visible(self, user=None):
        client = Client()
        client.force_login(user or self.classmate)
        return MARK in client.get(reverse('problem_detail', args=['fpcode'])).content.decode()

    def test_window(self):
        now = timezone.now()
        cases = (
            ('two days before the start', now + timedelta(days=2), now + timedelta(days=2, hours=2), False, True),
            ('within 24 hours of the start', now + timedelta(hours=23), now + timedelta(hours=25), False, False),
            ('running', now - timedelta(hours=1), now + timedelta(hours=1), False, False),
            ('ended, not finalized', now - timedelta(days=3), now - timedelta(days=2), False, False),
            ('ended and finalized', now - timedelta(days=3), now - timedelta(days=2), True, True),
            ('finalized but not ended', now - timedelta(hours=1), now + timedelta(hours=1), True, False),
            ('delivery window over', now - timedelta(days=16), now - timedelta(days=15), False, True),
        )
        for label, start, end, finalized, shown in cases:
            with self.subTest(label):
                self.set_times(start, end, finalized)
                self.assertEqual(self.visible(), shown)
                exam = self.reload_exam()
                self.assertEqual(Contest.objects.filter(fairplay_hidden_q(self.classmate, Contest),
                                                        pk=exam.pk).exists(), not shown)
                self.assertEqual(fairplay_hidden_for(self.classmate, exam), not shown)

    def test_finalized_exam_released_only_when_closed_for_everyone(self):
        now = timezone.now()
        self.set_times(now - timedelta(hours=2), now - timedelta(hours=1), finalized=True)
        # Without DKU OJ's count of added minutes: up to 8 hours after the end may still be running.
        self.assertFalse(self.visible())
        # FairPlay reported its close with a hand-in: 30 minutes after the end, so closed by now.
        note_fairplay_closes(self.exam, now - timedelta(minutes=30))
        self.assertTrue(self.visible())
        # DKU OJ's own count wins: 2 hours added, so students may still be writing.
        record_added_minutes(self.exam, 120)
        self.assertFalse(self.visible())
        self.assertTrue(fairplay_hidden_for(self.classmate, self.reload_exam()))
        cache.delete('fpb:added-minutes:%d' % self.exam.id)
        record_added_minutes(self.exam, 30)
        self.assertTrue(self.visible())
        self.assertFalse(fairplay_hidden_for(self.classmate, self.reload_exam()))
        # Not finalized: hidden until the delivery window is over.
        self.set_times(now - timedelta(hours=2), now - timedelta(hours=1), finalized=False)
        self.assertFalse(self.visible())

    def test_no_query_without_fairplay_exams(self):
        from django.contrib.auth.models import AnonymousUser as Anonymous
        from judge.utils.fairplay import fairplay_hiding_active
        set_fairplay(self.exam, False)
        self.assertFalse(fairplay_hiding_active())
        with self.assertNumQueries(0):
            for model in (Contest, Problem, Submission):
                fairplay_hidden_q(Anonymous(), model)
            fairplay_hidden_for(Anonymous(), self.code)
        set_fairplay(self.exam, True)
        self.assertTrue(fairplay_hiding_active())
        self.assertTrue(fairplay_hidden_for(Anonymous(), Problem.objects.get(pk=self.code.pk)))


class FairPlayHidingSmokeTestCase(TestCase):
    """Ordinary assignments are untouched by the rule."""
    fixtures = ['language_all.json']

    def test_non_fairplay_contest_problem_visible(self):
        from judge.models.tests.util import create_problem
        user = create_user(username='fp_plain_student')
        problem = create_problem('fpplain', is_public=True, description='Plain statement')
        contest = create_contest('fpplaincontest', is_visible=True)
        create_contest_problem(contest=contest, problem=problem, points=1, order=1)
        client = Client()
        client.force_login(user)
        self.assertContains(client.get(reverse('problem_detail', args=['fpplain'])), 'Plain statement')
        self.assertContains(client.get(reverse('contest_view', args=['fpplaincontest'])), 'fpplaincontest')

"""Tests for the teacher-side contest grading workspace (judge.views.contests.ContestGrade).

Regression: the grade page embeds submission *source* (fully student-controlled) into
inline JavaScript. It must be serialized script-safely so a source containing
``</script><script>…</script>`` cannot break out of the <script> block (stored XSS
against the teacher opening the grade view).
"""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestProblem, Language, Submission, SubmissionSource
from judge.models.tests.util import create_contest, create_problem, create_user

XSS_SOURCE = '</script><script>alert(document.cookie)</script>'


class ContestGradeViewTestCase(TestCase):
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.editor = create_user(username='grade_editor', user_permissions=('edit_all_contest',))
        cls.student = create_user(username='grade_student')
        cls.problem = create_problem('grade-xss-prob')
        cls.contest = create_contest(
            key='gradexss',
            name='Grade XSS',
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1),
            authors=('superuser',),
        )
        ContestProblem.objects.create(contest=cls.contest, problem=cls.problem, points=100, order=1)
        cls.submission = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem,
            language=Language.objects.get(key='PY3'),
            result='WA', status='D', contest_object=cls.contest,
        )
        SubmissionSource.objects.create(submission=cls.submission, source=XSS_SOURCE)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)
        self.url = reverse('contest_grade', args=[self.contest.key])

    def test_grade_page_escapes_script_breakout_in_source(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content

        # The literal `</script><script>…` must never appear in the rendered page,
        # otherwise it would break out of the inline <script> block and execute.
        self.assertNotIn(b'</script><script>', content)
        self.assertNotIn(XSS_SOURCE.encode(), content)

        # The source must be present, but only in script-safe escaped form
        # (`|tojson` escapes `<`/`>`/`&`/`'` to \u003c etc.).
        self.assertIn(b'\\u003c/script\\u003e\\u003cscript\\u003e', content)

    def test_grade_page_requires_editor_permission(self):
        normal = create_user(username='grade_bystander')
        self.client.force_login(normal)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_grade_page_anonymous_redirects_to_login(self):
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

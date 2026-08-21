"""Tests for the in-browser IDE submit/status endpoints (judge.views.ide)."""
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from judge.models import Judge, Language, Submission
from judge.models.tests.util import create_problem, create_user


class IDEViewTestCase(TestCase):
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='ideuser')
        cls.other = create_user(username='ideother')
        cls.language = Language.objects.get(key='PY3')
        judge = Judge.objects.create(name='testjudge', auth_key='testkey', online=True)
        judge.runtimes.add(cls.language)

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.force_login(self.user)

    def _submit(self, **overrides):
        data = {'source': 'print(input())', 'language': self.language.id, 'input': 'hello'}
        data.update(overrides)
        return self.client.post(reverse('ide_submit'), data)

    def test_page_uses_local_monaco(self):
        response = self.client.get(reverse('ide'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="code-editor"')
        self.assertContains(response, 'libs/monaco/vs')
        self.assertNotContains(response, 'cdn.jsdelivr.net')

    def test_requires_login(self):
        anon = Client()
        self.assertEqual(anon.get(reverse('ide')).status_code, 302)
        self.assertEqual(anon.post(reverse('ide_submit'), {}).status_code, 302)

    def test_rate_limit(self):
        cache.set(f'ide_run:{self.user.profile.id}', 5, 60)
        self.assertEqual(self._submit().status_code, 429)

    def test_source_size_validated(self):
        self.assertEqual(self._submit(source='').status_code, 400)
        self.assertEqual(self._submit(source='a' * 65537).status_code, 400)

    def test_invalid_language_rejected(self):
        self.assertEqual(self._submit(language=999999).status_code, 400)

    def test_missing_ide_problem_reported(self):
        self.assertEqual(self._submit().status_code, 500)

    def test_successful_submission_judged_with_cached_input(self):
        create_problem(code='idepractice')
        with mock.patch.object(Submission, 'judge') as mock_judge:
            response = self._submit()
        self.assertEqual(response.status_code, 200)
        submission_id = response.json()['submission_id']
        mock_judge.assert_called_once()
        self.assertEqual(cache.get(f'ide_input:{submission_id}'), 'hello')
        submission = Submission.objects.get(id=submission_id)
        self.assertEqual(submission.problem.code, 'idepractice')

    def test_status_denies_other_users(self):
        problem = create_problem(code='idepractice')
        submission = Submission.objects.create(
            user=self.other.profile, problem=problem, language=self.language)
        response = self.client.get(reverse('ide_submission_status', args=[submission.id]))
        self.assertEqual(response.status_code, 403)

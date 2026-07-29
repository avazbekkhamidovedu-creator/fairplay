"""Tests for the AI-hint endpoint (judge.views.submission.SubmissionAIHint)."""
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from judge import ai_hints, llm
from judge.models import Language, Problem, Submission, SubmissionSource
from judge.models.ai_hints import AIHintLog
from judge.models.tests.util import create_problem, create_user

AZURE_SETTINGS = {
    'LLM_PROVIDER': 'azure',
    'AZURE_OPENAI_ENDPOINT': 'https://example.openai.azure.com/',
    'AZURE_OPENAI_API_KEY': 'test-key',
    'AZURE_OPENAI_DEPLOYMENT': 'gpt-test',
}


class AIHintViewTestCase(TestCase):
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='hintowner')
        cls.other = create_user(username='hintother')
        cls.problem = create_problem(code='hintprob', ai_hints_enabled=True)
        cls.submission = Submission.objects.create(
            user=cls.user.profile, problem=cls.problem,
            language=Language.objects.get(key='PY3'), result='WA', status='D')
        SubmissionSource.objects.create(submission=cls.submission, source='print(1)')

    def setUp(self):
        llm._backend = None
        self.addCleanup(setattr, llm, '_backend', None)
        cache.clear()
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse('submission_ai_hint', args=[self.submission.id])

    def test_requires_login(self):
        response = Client().post(self.url)
        self.assertEqual(response.status_code, 302)

    @override_settings(**AZURE_SETTINGS)
    def test_disabled_problem_rejected(self):
        Problem.objects.filter(id=self.problem.id).update(ai_hints_enabled=False)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_unconfigured_backend_rejected(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)

    @override_settings(**AZURE_SETTINGS)
    def test_other_users_submission_rejected(self):
        self.client.force_login(self.other)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    @override_settings(**AZURE_SETTINGS)
    def test_quota_exhausted_rejected(self):
        with mock.patch.object(ai_hints, 'try_reserve', return_value=False):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 429)

    @override_settings(**AZURE_SETTINGS)
    def test_successful_hint_logged_and_counted(self):
        result = ai_hints.HintResult(hint_text='Check your loop bounds.', total_tokens=42)
        with mock.patch.object(ai_hints, 'get_hint', return_value=result):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['hint'], 'Check your loop bounds.')
        self.assertEqual(data['remaining'], 4)
        log = AIHintLog.objects.get(user=self.user.profile)
        self.assertTrue(log.success)
        self.assertEqual(log.hint_text, 'Check your loop bounds.')

    @override_settings(**AZURE_SETTINGS)
    def test_failed_hint_refunds_quota(self):
        with mock.patch.object(ai_hints, 'get_hint', side_effect=RuntimeError('API down')):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)
        log = AIHintLog.objects.get(user=self.user.profile)
        self.assertFalse(log.success)
        self.assertIn('API down', log.error_message)
        self.assertEqual(ai_hints.get_remaining(self.user.profile.id), 5)

"""Tests for DKU-specific features: AI hints, the IDE, CSV user import, and the
pwned-password forced change that backs the import's default password."""
import io
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from judge import ai_hints
from judge.models import Judge, Language, Problem, Profile, Submission, SubmissionSource
from judge.models.ai_hints import AIHintLog
from judge.models.tests.util import create_problem, create_user
from judge.utils.import_export import process_user_csv
from judge.utils.pwned import PwnedPasswordsValidator

AZURE_SETTINGS = {
    'AZURE_OPENAI_ENDPOINT': 'https://example.openai.azure.com/',
    'AZURE_OPENAI_API_KEY': 'test-key',
    'AZURE_OPENAI_DEPLOYMENT': 'gpt-test',
}


class AIHintQuotaTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.profile = create_user(username='hintuser').profile

    def setUp(self):
        cache.clear()

    def test_fresh_user_has_full_quota(self):
        self.assertEqual(ai_hints.get_remaining(self.profile.id), 5)

    def test_reserve_consumes_and_stops_at_limit(self):
        for _ in range(5):
            self.assertTrue(ai_hints.try_reserve(self.profile.id))
        self.assertFalse(ai_hints.try_reserve(self.profile.id))
        self.assertEqual(ai_hints.get_remaining(self.profile.id), 0)
        # A failed reservation must not push the counter past the limit.
        self.assertFalse(ai_hints.try_reserve(self.profile.id))
        ai_hints.refund(self.profile.id)
        self.assertEqual(ai_hints.get_remaining(self.profile.id), 1)

    def test_refund_restores_slot(self):
        self.assertTrue(ai_hints.try_reserve(self.profile.id))
        self.assertEqual(ai_hints.get_remaining(self.profile.id), 4)
        ai_hints.refund(self.profile.id)
        self.assertEqual(ai_hints.get_remaining(self.profile.id), 5)

    def test_cold_cache_seeds_from_db(self):
        problem = create_problem(code='hintseed')
        submission = Submission.objects.create(user=self.profile, problem=problem,
                                               language=Language.get_default_language())
        AIHintLog.objects.create(user=self.profile, submission=submission, problem=problem, success=True)
        AIHintLog.objects.create(user=self.profile, submission=submission, problem=problem, success=True)
        AIHintLog.objects.create(user=self.profile, submission=submission, problem=problem, success=False)
        cache.clear()
        # Two successful hints today; the failed one does not count.
        self.assertEqual(ai_hints.get_remaining(self.profile.id), 3)


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
        for _ in range(5):
            ai_hints.try_reserve(self.user.profile.id)
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

    def test_requires_login(self):
        response = Client().post(reverse('ide_submit'), {})
        self.assertEqual(response.status_code, 302)

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


class UserImportTestCase(TestCase):
    HEADER = 'username,first_name,last_name,email\n'

    def _import(self, rows, **kwargs):
        content = (self.HEADER + '\n'.join(rows)).encode('utf-8')
        return process_user_csv(io.BytesIO(content), **kwargs)

    def test_creates_users_with_profile_and_default_password(self):
        result = self._import(['alice,Alice,Liddell,alice@example.com'])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.errors, [])
        user = User.objects.get(username='alice')
        self.assertTrue(user.check_password('123456'))
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_duplicate_username_within_csv_reported(self):
        result = self._import([
            'bob,Bob,One,bob1@example.com',
            'bob,Bob,Two,bob2@example.com',
        ])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('duplicate username', result.errors[0])
        # First row wins; the second must not overwrite it.
        self.assertEqual(User.objects.get(username='bob').email, 'bob1@example.com')

    def test_existing_user_skipped_unless_update_requested(self):
        create_user(username='carol')
        result = self._import(['carol,Carol,New,carol@example.com'])
        self.assertEqual(result.skipped, 1)
        self.assertEqual(User.objects.get(username='carol').first_name, '')

        result = self._import(['carol,Carol,New,carol@example.com'], update_existing=True)
        self.assertEqual(result.updated, 1)
        carol = User.objects.get(username='carol')
        self.assertEqual(carol.first_name, 'Carol')
        self.assertEqual(carol.email, 'carol@example.com')


class PwnedPasswordForcedChangeTestCase(TestCase):
    """The CSV import's weak default password relies on this flow: a login with
    a known-compromised password must be forced through a password change."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='imported', password='123456')
        Profile.objects.get_or_create(user=cls.user)

    def test_validator_falls_back_to_common_list_when_api_unavailable(self):
        with mock.patch('judge.utils.pwned._get_pwned', return_value=None):
            with self.assertRaises(ValidationError):
                PwnedPasswordsValidator().validate('123456')

    def test_login_with_pwned_password_redirects_to_change(self):
        with mock.patch('judge.utils.pwned.pwned_password', return_value=12345):
            response = self.client.post(reverse('auth_login'), {
                'username': 'imported', 'password': '123456',
            })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session['password_pwned'])

        response = self.client.get('/problems/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('password_change')))

    def test_clean_password_not_redirected(self):
        with mock.patch('judge.utils.pwned.pwned_password', return_value=0):
            self.client.post(reverse('auth_login'), {
                'username': 'imported', 'password': '123456',
            })
        self.assertFalse(self.client.session['password_pwned'])

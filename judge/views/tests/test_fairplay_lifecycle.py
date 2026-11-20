import hashlib
import hmac
import json
from unittest import mock
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import Class, Contest, Organization
from judge.utils.fairplay_package import build_exam_entry, build_package
from judge.utils.fairplay_signing import body_sha256, canonical_string, direction_key, DIRECTION_OJ_TO_FP
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin, INSTALLATION_ID, TOKEN, fp_signed


@FAIRPLAY_ON
class FairPlayLifecycleTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.teacher)

    def test_no_audience_confirmation_exists(self):
        """No instructor confirmation step: the audience the classic service reads is derived from the contest and is
        always confirmed."""
        self.exam.classes.clear()
        audience = build_package(self.exam)[0]['audience']
        self.assertEqual((audience['mode'], audience['confirmed'], audience['class_ids']),
                         ('organizations', True, []))
        self.assertNotIn('fp-aud-', ''.join(self.exam.tags.values_list('name', flat=True)))
        self.assertEqual(build_exam_entry(self.exam)['audience'], audience)

    def test_class_deletion_before_any_sync_preserves_key_and_revokes_scope(self):
        key, class_id = self.exam.key, self.klass.pk
        with mock.patch('judge.utils.fairplay_lifecycle._run_later', side_effect=lambda _delay, fn: fn()), \
                mock.patch('judge.utils.fairplay_lifecycle.FairPlayClient.sync_ping') as send, \
                self.captureOnCommitCallbacks(execute=True):
            self.klass.delete()
        calls = [call.kwargs for call in send.call_args_list if call.kwargs.get('event') == 'audience_revoked']
        self.assertTrue(any(key in call['exam_keys'] and class_id in call['class_ids'] for call in calls))

    def test_org_deletion_and_editor_clear_notify_explicit_keys(self):
        key, org_id = self.exam.key, self.org.pk
        with mock.patch('judge.utils.fairplay_lifecycle._run_later', side_effect=lambda _delay, fn: fn()), \
                mock.patch('judge.utils.fairplay_lifecycle.FairPlayClient.sync_ping') as send, \
                self.captureOnCommitCallbacks(execute=True):
            self.exam.authors.clear()
            self.org.delete()
        calls = [call.kwargs for call in send.call_args_list]
        self.assertTrue(any(key in call['exam_keys'] for call in calls))
        self.assertTrue(any(org_id in call['organization_ids'] for call in calls))

    def test_installation_metadata_and_signed_missing_exam_bind_raw_body_status_and_request_nonce(self):
        for name, args, expected in [('fairplay_api_installation', [], 200),
                                     ('fairplay_api_package', ['missing-exam'], 404)]:
            with self.subTest(name=name):
                path = reverse(name, args=args)
                extra = fp_signed('GET', path)
                response = self.client.get(path, **extra)
                self.assertEqual(response.status_code, expected)
                full = path + '?' + extra['QUERY_STRING']
                canonical = canonical_string('RESPONSE GET %d' % expected, full,
                    response['X-FP-Timestamp'], response['X-FP-Nonce'], extra['HTTP_X_FP_NONCE'],
                    body_sha256(response.content))
                signature = hmac.new(direction_key(DIRECTION_OJ_TO_FP, TOKEN), canonical.encode(), hashlib.sha256).hexdigest()
                self.assertEqual(response['X-FP-Signature'], signature)
                self.assertNotEqual(signature, hmac.new(direction_key(DIRECTION_OJ_TO_FP, TOKEN),
                    canonical.replace(extra['HTTP_X_FP_NONCE'], '0' * 32).encode(), hashlib.sha256).hexdigest())
                if expected == 200:
                    self.assertEqual(response.json(), {'protocol': 1, 'installation_id': INSTALLATION_ID,
                                                      'request_nonce': extra['HTTP_X_FP_NONCE']})

    def test_wrong_installation_or_missing_configuration_fails_closed(self):
        path = reverse('fairplay_api_installation')
        wrong = path + '?' + urlencode({'installation_id': '11111111-1111-4111-8111-111111111111'})
        self.assertEqual(self.client.get(wrong, **fp_signed('GET', wrong)).status_code, 409)
        extra = fp_signed('GET', path)
        with override_settings(FAIRPLAY_INSTALLATION_ID=''):
            self.assertEqual(self.client.get(path, **extra).status_code, 503)

    def test_shared_client_includes_identity_in_signature_query_for_post(self):
        from judge.utils.fairplay_client import FairPlayClient
        from judge.views.tests.test_fairplay_pages import fake_response
        with mock.patch('judge.utils.fairplay_client.requests.request', return_value=fake_response(200, {'ok': True})) as send:
            FairPlayClient().sync_ping(exam_key=self.exam.key, event='audience_revoked')
        self.assertIn('installation_id=' + INSTALLATION_ID, send.call_args.args[1])
        self.assertEqual(json.loads(send.call_args.kwargs['data'])['event'], 'audience_revoked')

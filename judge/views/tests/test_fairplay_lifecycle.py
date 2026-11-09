import hashlib
import hmac
import json
from unittest import mock
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import Class, Contest, Organization
from judge.utils.fairplay_audience import MODES, CONFIRMED, audience_descriptor
from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
from judge.utils.fairplay_package import build_exam_entry, build_package
from judge.utils.fairplay_signing import body_sha256, canonical_string, direction_key, DIRECTION_OJ_TO_FP
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin, INSTALLATION_ID, TOKEN, fp_signed


@FAIRPLAY_ON
class FairPlayLifecycleTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.teacher)

    def confirm(self, mode):
        plan = parse_fairplay_payload(self.exam, {'fairplay_required': True,
            'fairplay_audience_confirm': True, 'fairplay_audience_mode': mode}, set())
        apply_fairplay_editor(self.exam, plan)

    def test_explicit_confirmation_and_empty_class_never_falls_back(self):
        self.assertTrue(audience_descriptor(self.exam)['confirmed'])
        self.exam.classes.clear()
        descriptor = audience_descriptor(self.exam)
        self.assertEqual(descriptor['mode'], 'classes')
        self.assertFalse(descriptor['confirmed'])
        self.assertEqual(descriptor['organization_ids'], [self.org.pk])
        self.assertEqual(build_exam_entry(self.exam)['audience'], descriptor)
        self.assertEqual(build_package(self.exam)[0]['audience'], descriptor)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('contest_fairplay_launch', args=[self.exam.key])).status_code, 404)

    def test_legacy_scope_needs_explicit_authorized_confirmation(self):
        self.exam.tags.remove(*self.exam.tags.filter(name__startswith='fp-aud-'))
        self.assertFalse(audience_descriptor(self.exam)['confirmed'])
        apply_fairplay_editor(self.exam, parse_fairplay_payload(self.exam, {'fairplay_required': True}, set()))
        self.assertFalse(audience_descriptor(self.exam)['confirmed'])
        self.confirm('classes')
        self.assertTrue(audience_descriptor(self.exam)['confirmed'])

    def test_org_scope_is_allowed_only_after_explicit_confirmation(self):
        self.exam.classes.clear()
        self.assertFalse(audience_descriptor(self.exam)['confirmed'])
        self.confirm('organizations')
        self.assertEqual(audience_descriptor(self.exam)['mode'], 'organizations')
        self.assertTrue(audience_descriptor(self.exam)['confirmed'])
        self.exam.organizations.clear()
        self.assertFalse(audience_descriptor(self.exam)['confirmed'])

    def test_reused_class_name_does_not_replace_the_selected_class_id(self):
        original_name = self.klass.name
        Class.objects.filter(pk=self.klass.pk).update(name=original_name + ' renamed')
        other = Class.objects.create(name=original_name, slug='fp-other', organization=self.org)
        self.assertEqual(audience_descriptor(self.exam)['class_ids'], [self.klass.pk])
        self.assertNotIn(other.pk, audience_descriptor(self.exam)['class_ids'])

    def test_private_class_scope_cannot_expand_without_explicit_reconfirmation(self):
        self._private_scope_requires_reconfirmation('classes')

    def test_private_org_scope_cannot_expand_without_explicit_reconfirmation(self):
        self.exam.classes.clear()
        self._private_scope_requires_reconfirmation('organizations')

    def _private_scope_requires_reconfirmation(self, mode):
        self.exam.is_private = True
        self.exam.save(update_fields=['is_private'])
        self.exam.private_contestants.add(self.student.profile)
        self.confirm(mode)
        self.assertTrue(audience_descriptor(self.exam)['confirmed'])
        self.assertTrue(audience_descriptor(self.exam)['selected_only'])
        self.exam.is_private = False
        self.exam.save(update_fields=['is_private'])
        apply_fairplay_editor(self.exam, parse_fairplay_payload(self.exam, {'fairplay_required': True}, set()))
        self.assertFalse(audience_descriptor(self.exam)['confirmed'])
        self.assertTrue(audience_descriptor(self.exam)['selected_only'])
        self.confirm(mode)
        self.assertTrue(audience_descriptor(self.exam)['confirmed'])
        self.assertFalse(audience_descriptor(self.exam)['selected_only'])

    def test_class_deletion_before_any_sync_preserves_key_and_revokes_scope(self):
        key, class_id = self.exam.key, self.klass.pk
        with mock.patch('judge.utils.fairplay_lifecycle._run_later', side_effect=lambda _delay, fn: fn()), \
                mock.patch('judge.utils.fairplay_lifecycle.FairPlayClient.sync_ping') as send, \
                self.captureOnCommitCallbacks(execute=True):
            self.klass.delete()
        calls = [call.kwargs for call in send.call_args_list if call.kwargs.get('event') == 'audience_revoked']
        self.assertTrue(any(key in call['exam_keys'] and class_id in call['class_ids'] for call in calls))
        self.assertEqual(audience_descriptor(self.exam)['mode'], 'classes')
        self.assertFalse(audience_descriptor(self.exam)['confirmed'])

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

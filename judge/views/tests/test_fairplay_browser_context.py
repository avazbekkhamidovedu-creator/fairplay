import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from judge.views.fairplay_browser_context import browser_context, record_browser_completion


class BrowserContextTests(SimpleTestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.secret = base64.b64encode(self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
        self.request = RequestFactory().get('/api/fairplay/browser-context', {'nonce': 'a' * 32})
        self.request.user = SimpleNamespace(pk=7, is_authenticated=True)
        self.request.profile = SimpleNamespace(current_contest=None)
        self.request.session = {}

    def read(self):
        with override_settings(FAIRPLAY_BROWSER_SIGNING_KEY=self.secret):
            response = browser_context(self.request)
        envelope = json.loads(response.content)
        payload = base64.b64decode(envelope['payload'])
        self.key.public_key().verify(base64.b64decode(envelope['signature']), payload)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return json.loads(payload)

    def test_signed_idle_context_binds_user_nonce_and_expiry(self):
        payload = self.read()
        self.assertEqual((payload['user'], payload['nonce'], payload['state']), ('7', 'a' * 32, 'idle'))
        self.assertTrue(time.time() < payload['expires'] <= time.time() + 30)

    def test_default_off_and_anonymous(self):
        with override_settings(FAIRPLAY_BROWSER_SIGNING_KEY=''), self.assertRaises(Http404):
            browser_context(self.request)
        self.request.user.is_authenticated = False
        with override_settings(FAIRPLAY_BROWSER_SIGNING_KEY=self.secret), self.assertRaises(Http404):
            browser_context(self.request)

    def test_receipt_is_bound_to_user_and_freshness(self):
        receipt = {'user': '7', 'contest': 'exam', 'participation': '12', 'at': int(time.time())}
        self.request.session['fairplay_browser_completed'] = receipt
        self.assertEqual(self.read()['state'], 'completed')
        receipt['user'] = '8'
        self.assertEqual(self.read()['state'], 'idle')
        receipt.update(user='7', at=int(time.time()) - 301)
        self.assertEqual(self.read()['state'], 'idle')

    def test_pending_answer_cannot_confirm_completion(self):
        participation = SimpleNamespace(spectate=False, pk=12, contest=SimpleNamespace(key='exam'), drafts=Mock())
        participation.drafts.select_related.return_value = [SimpleNamespace(has_content=lambda: True)]
        self.request.session['fairplay_browser_completed'] = {'stale': True}
        with override_settings(FAIRPLAY_BROWSER_SIGNING_KEY=self.secret):
            record_browser_completion(self.request, participation)
        self.assertNotIn('fairplay_browser_completed', self.request.session)
        participation.drafts.select_related.return_value = []
        with override_settings(FAIRPLAY_BROWSER_SIGNING_KEY=self.secret):
            record_browser_completion(self.request, participation)
        self.assertEqual(self.read()['participation'], '12')

    def test_current_participation_invalidates_old_completion(self):
        self.request.session['fairplay_browser_completed'] = {
            'user': '7', 'contest': 'exam', 'participation': '12', 'at': int(time.time()),
        }
        self.request.profile.current_contest = SimpleNamespace(spectate=True)
        self.assertEqual(self.read()['state'], 'idle')
        self.assertNotIn('fairplay_browser_completed', self.request.session)

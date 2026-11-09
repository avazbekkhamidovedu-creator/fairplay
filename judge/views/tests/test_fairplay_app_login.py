"""FairPlay homework app sign-in: the one-time ticket and its redemption."""
import base64
import hashlib
import json
import threading
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from judge.models.tests.util import create_user
from judge.views import fairplay_app_login
from judge.views.tests.fairplay_base import FAIRPLAY_ON, TOKEN, fp_signed

STATE = 'S' * 22
VERIFIER = 'v' * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()
REDEEM = '/api/fairplay/v1/app-login/redeem'
PUBLIC_URL = override_settings(FAIRPLAY_OJ_PUBLIC_URL='https://oj.example.edu')


def mint(client, state=STATE, challenge=CHALLENGE):
    return client.get(reverse('fairplay_app_login'), {'state': state, 'challenge': challenge})


def ticket_of(response):
    fragment = response['Location'].split('#', 1)[1]
    return dict(part.split('=', 1) for part in fragment.split('&'))['ticket']


def redeem(client, ticket, verifier=VERIFIER, signed=True, raw=None):
    body = raw if raw is not None else json.dumps({'ticket': ticket, 'verifier': verifier}).encode()
    extra = fp_signed('POST', REDEEM, body) if signed else {}
    return client.post(REDEEM, data=body, content_type='application/json', **extra)


@FAIRPLAY_ON
@PUBLIC_URL
class MintTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = create_user(username='al_student', first_name='Ada', last_name='Lovelace',
                                  email='al_student@example.edu')
        cls.admin = create_user(username='al_admin', is_superuser=True)
        cls.staff = create_user(username='al_staff', is_staff=True)
        cls.dormant = create_user(username='al_dormant')

    def setUp(self):
        cache.clear()
        self.client = Client()

    def login(self, user):
        self.client.force_login(user)

    def test_requires_login(self):
        response = mint(self.client)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertNotIn('ticket=', response['Location'])

    def test_bad_state_or_challenge(self):
        self.login(self.student)
        for state, challenge in (('', CHALLENGE), (STATE, ''), ('short', CHALLENGE), (STATE, 'x' * 15),
                                 ('a' * 129, CHALLENGE), (STATE, 'a' * 129), ('bad state!!bad state!!', CHALLENGE),
                                 (STATE, 'abcdefghijklmnop+/=='), (STATE + '\n', CHALLENGE)):
            response = mint(self.client, state, challenge)
            self.assertEqual(response.status_code, 400, (state, challenge))
        self.assertEqual(self.client.get(reverse('fairplay_app_login')).status_code, 400)

    def test_boundary_lengths_are_accepted(self):
        self.login(self.student)
        self.assertEqual(mint(self.client, 'a' * 16, 'b' * 16).status_code, 302)
        self.assertEqual(mint(self.client, 'a_-' * 42 + 'ab', 'B-_' * 42 + 'ab').status_code, 302)

    def test_redirect_shape(self):
        self.login(self.student)
        response = mint(self.client)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Cache-Control'], 'no-store')
        location = response['Location']
        base, fragment = location.split('#')
        self.assertEqual(base, 'https://oj.example.edu/fairplay/app-login/done')
        self.assertNotIn('?', location)
        ticket = ticket_of(response)
        self.assertEqual(fragment, 'ticket=%s&state=%s' % (ticket, STATE))
        self.assertRegex(ticket, r'^[A-Za-z0-9_-]{43}$')
        entry = cache.get('fpb:applogin:' + hashlib.sha256(ticket.encode()).hexdigest())
        self.assertEqual(entry, {'user': self.student.id, 'challenge': CHALLENGE, 'state': STATE})

    def test_public_url_setting(self):
        self.login(self.student)
        with self.settings(FAIRPLAY_OJ_PUBLIC_URL='https://oj.example.edu/'):
            self.assertTrue(mint(self.client)['Location'].startswith('https://oj.example.edu/fairplay/app-login/done#'))

    def test_done_url_ignores_request_host(self):
        self.login(self.student)
        with self.settings(ALLOWED_HOSTS=['testserver', 'evil.example.com']):
            response = self.client.get(reverse('fairplay_app_login'), {'state': STATE, 'challenge': CHALLENGE},
                                       HTTP_HOST='evil.example.com', secure=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://oj.example.edu/fairplay/app-login/done#'))
        self.assertNotIn('evil.example.com', response['Location'])

    def test_unconfigured_public_url_is_refused(self):
        self.login(self.student)
        for value in ('', 'http://oj.example.edu'):
            with self.settings(FAIRPLAY_OJ_PUBLIC_URL=value):
                self.assertEqual(mint(self.client).status_code, 503)

    def test_every_mint_is_a_new_ticket(self):
        self.login(self.student)
        self.assertNotEqual(ticket_of(mint(self.client)), ticket_of(mint(self.client)))

    def test_ticket_is_not_logged(self):
        self.login(self.student)
        with self.assertNoLogs(level='DEBUG'):
            mint(self.client)

    def test_superuser_and_staff_are_refused(self):
        for user in (self.admin, self.staff):
            client = Client()
            client.force_login(user)
            response = mint(client)
            self.assertEqual(response.status_code, 403, user.username)
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertNotIn('ticket', response.content.decode())

    def test_inactive_user_gets_no_ticket(self):
        self.dormant.is_active = False
        self.dormant.save()
        client = Client()
        client.force_login(self.dormant, 'django.contrib.auth.backends.AllowAllUsersModelBackend')
        response = mint(client)
        self.assertNotIn('#ticket=', response.get('Location', ''))

    def test_rate_limit(self):
        self.login(self.student)
        for _ in range(10):
            self.assertEqual(mint(self.client).status_code, 302)
        response = mint(self.client)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Cache-Control'], 'no-store')
        other = Client()
        other.force_login(self.student)
        self.assertEqual(mint(other).status_code, 302)

    def test_bad_requests_do_not_use_up_the_limit(self):
        self.login(self.student)
        for _ in range(12):
            mint(self.client, 'bad', 'bad')
        self.assertEqual(mint(self.client).status_code, 302)

    def test_post_is_not_allowed(self):
        self.login(self.student)
        self.assertEqual(self.client.post(reverse('fairplay_app_login'), {'state': STATE}).status_code, 405)


@FAIRPLAY_ON
@PUBLIC_URL
class DonePageTestCase(TestCase):
    def test_page(self):
        response = self.client.get('/fairplay/app-login/done', {'x': 'y'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        html = response.content.decode()
        self.assertIn('Return to FairPlay', html)
        self.assertIn('history.replaceState', html)

    def test_html_has_no_ticket(self):
        user = create_user(username='al_done')
        self.client.force_login(user)
        ticket = ticket_of(mint(self.client))
        response = self.client.get('/fairplay/app-login/done', HTTP_REFERER='http://x/#ticket=' + ticket)
        self.assertNotIn(ticket, response.content.decode())
        self.assertNotIn('ticket', response.content.decode().replace('history.replaceState', ''))


@FAIRPLAY_ON
@PUBLIC_URL
class RedeemTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = create_user(username='al_redeem', first_name='Ada', last_name='Lovelace',
                                  email='al_redeem@example.edu')

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.force_login(self.student)

    def new_ticket(self):
        return ticket_of(mint(self.client))

    def test_happy_path(self):
        response = redeem(Client(), self.new_ticket())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'netid': 'al_redeem', 'first_name': 'Ada', 'last_name': 'Lovelace',
                                           'email': 'al_redeem@example.edu'})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_replay_is_404(self):
        ticket = self.new_ticket()
        self.assertEqual(redeem(Client(), ticket).status_code, 200)
        self.assertEqual(redeem(Client(), ticket).status_code, 404)

    def test_wrong_verifier_is_409_and_spends_the_ticket(self):
        ticket = self.new_ticket()
        self.assertEqual(redeem(Client(), ticket, verifier='w' * 43).status_code, 409)
        self.assertEqual(redeem(Client(), ticket).status_code, 404)

    def test_unknown_ticket_is_404(self):
        self.assertEqual(redeem(Client(), 'nope' * 11).status_code, 404)

    def test_expired_ticket_is_404(self):
        ticket = self.new_ticket()
        key = 'fpb:applogin:' + hashlib.sha256(ticket.encode()).hexdigest()
        with mock.patch('django.core.cache.backends.locmem.time') as clock:
            clock.time.return_value = 10 ** 10
            self.assertIsNone(cache.get(key))
            self.assertEqual(redeem(Client(), ticket).status_code, 404)

    def test_ticket_lives_sixty_seconds(self):
        with mock.patch.object(fairplay_app_login.cache, 'set') as put:
            self.new_ticket()
        self.assertEqual(put.call_args[0][2], 60)

    def test_unsigned_is_refused(self):
        ticket = self.new_ticket()
        self.assertEqual(redeem(Client(), ticket, signed=False).status_code, 401)
        self.assertEqual(redeem(Client(), ticket).status_code, 200)

    def test_wrong_token_is_refused(self):
        ticket = self.new_ticket()
        body = json.dumps({'ticket': ticket, 'verifier': VERIFIER}).encode()
        response = Client().post(REDEEM, data=body, content_type='application/json',
                                 **fp_signed('POST', REDEEM, body, token=TOKEN + 'x'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(redeem(Client(), ticket).status_code, 200)

    def test_get_is_not_allowed(self):
        response = Client().get(REDEEM, **fp_signed('GET', REDEEM))
        self.assertEqual(response.status_code, 405)

    def test_malformed_bodies_are_400_and_keep_the_ticket(self):
        ticket = self.new_ticket()
        for raw in (b'not json', b'[]', b'{}', json.dumps({'ticket': ticket}).encode(),
                    json.dumps({'ticket': 5, 'verifier': VERIFIER}).encode()):
            self.assertEqual(redeem(Client(), ticket, raw=raw).status_code, 400, raw)
        self.assertEqual(redeem(Client(), ticket).status_code, 200)

    def test_body_over_4kb_is_413(self):
        raw = json.dumps({'ticket': 'a', 'verifier': 'b' * 5000}).encode()
        self.assertEqual(redeem(Client(), 'a', raw=raw).status_code, 413)

    def test_user_made_inactive_after_minting(self):
        ticket = self.new_ticket()
        self.student.is_active = False
        self.student.save()
        self.assertEqual(redeem(Client(), ticket).status_code, 404)

    def test_bridge_off_is_404(self):
        ticket = self.new_ticket()
        with self.settings(FAIRPLAY_TOKEN='', FAIRPLAY_URL=''):
            self.assertEqual(redeem(Client(), ticket).status_code, 404)


@FAIRPLAY_ON
@PUBLIC_URL
class ConcurrentRedeemTestCase(TransactionTestCase):
    def test_single_success(self):
        user = create_user(username='al_race')
        cache.clear()
        client = Client()
        client.force_login(user)
        ticket = ticket_of(mint(client))
        gate = threading.Barrier(8)
        codes = []

        def attempt():
            gate.wait()
            try:
                codes.append(redeem(Client(), ticket).status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(codes), [200] + [404] * 7)

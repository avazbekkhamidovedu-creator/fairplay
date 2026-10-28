"""Security of the DKU OJ side of the FairPlay bridge, one attack scenario per test."""
import hashlib
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.checks import fairplay_bridge_check
from judge.models import Class
from judge.models.tests.util import create_organization, create_user
from judge.utils.fairplay import fairplay_token, fairplay_tokens, fairplay_url_insecure
from judge.utils.fairplay_client import FairPlayClient, FairPlayError, FairPlayUnreachable, MAX_RESPONSE_BYTES
from judge.utils.fairplay_delivery import added_minutes, deliver_submission
from judge.utils.fairplay_package import iso
from judge.utils.fairplay_signing import DIRECTION_FP_TO_OJ, DIRECTION_OJ_TO_FP, canonical_string, direction_key, \
    sign
from judge.utils.fairplay_teaching import CLASS_LOGIN_KEY
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin, TOKEN, create_contest, fp_signed, \
    set_fairplay
from judge.views.tests.test_fairplay_pages import PagesMixin, exam, fake_response

NEW_TOKEN = 'fp-new-token-rotation-0123456789abcdefghij'
REDIS = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': 'redis://x:6379/0'}}


@FAIRPLAY_ON
class ClassScopeTestCase(FairPlayDataMixin, TestCase):
    """FairPlay may not read any class roster or any NetID's classes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.math_org = create_organization(name='math201')
        cls.math = Class.objects.create(organization=cls.math_org, name='MATH 201 A', slug='math201-a')
        cls.zed = create_user(username='zed', first_name='Zed', last_name='Private')
        cls.zed.profile.organizations.add(cls.math_org)
        cls.math.members.add(cls.zed.profile, cls.student.profile)

    def get(self, name, arg):
        url = reverse(name, args=[arg])
        return self.client.get(url, **fp_signed('GET', url))

    def test_roster_of_a_class_without_fairplay_is_404(self):
        response = self.get('fairplay_api_class_roster', self.math.id)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('zed', response.content.decode())
        # A plain (non-FairPlay) assignment for it changes nothing.
        plain = create_contest('mathquiz', authors=('fp_teacher',), is_organization_private=True,
                               organizations=(self.math_org.name,))
        plain.classes.add(self.math)
        self.assertEqual(self.get('fairplay_api_class_roster', self.math.id).status_code, 404)
        # A FairPlay assignment for the class makes it FairPlay's business.
        set_fairplay(plain, True)
        response = self.get('fairplay_api_class_roster', self.math.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('zed', [s['username'] for s in response.json()['students']])

    def test_class_with_a_class_login_shares_its_roster(self):
        # A class login may come before any FairPlay assignment; DKU OJ records it in the cache.
        from judge.utils.fairplay_teaching import mark_class_login
        self.assertEqual(self.get('fairplay_api_class_roster', self.math.id).status_code, 404)
        self.assertEqual(self.get('fairplay_api_student_classes', 'zed').json(), {'classes': []})
        mark_class_login(self.math.id)
        response = self.get('fairplay_api_class_roster', self.math.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('zed', [s['username'] for s in response.json()['students']])
        self.assertEqual([c['id'] for c in self.get('fairplay_api_student_classes', 'zed').json()['classes']],
                         [self.math.id])
        # Lost (cache cleared): back to 404 until a manager makes or sees a class login again.
        cache.delete(CLASS_LOGIN_KEY % self.math.id)
        self.assertEqual(self.get('fairplay_api_class_roster', self.math.id).status_code, 404)

    def test_course_wide_fairplay_assignment_covers_its_classes(self):
        create_contest('mathcourse', authors=('fp_teacher',), is_organization_private=True,
                       organizations=(self.math_org.name,), fairplay_required=True)
        self.assertEqual(self.get('fairplay_api_class_roster', self.math.id).status_code, 200)

    def test_fairplay_class_still_served(self):
        self.assertEqual(self.get('fairplay_api_class_roster', self.klass.id).status_code, 200)

    def test_student_classes_only_fairplay_classes(self):
        self.assertEqual(self.get('fairplay_api_student_classes', 'zed').json(), {'classes': []})
        # fp_student is in both: only the FairPlay class is listed.
        classes = self.get('fairplay_api_student_classes', 'fp_student').json()['classes']
        self.assertEqual([c['id'] for c in classes], [self.klass.id])


@FAIRPLAY_ON
class ClearHandInTestCase(PagesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.fp.student_report.return_value = {'name': 'Avazbek Khamidov', 'netid': 'fp_student',
                                               'status': 'handed_in', 'events': []}
        deliver_submission(self.reload_exam(), {
            'username': 'fp_student', 'attempt_id': 'att_1', 'handed_in_evidence': 'server',
            'handed_in_at': iso(timezone.now()), 'answers': [{'question_id': 'p%06d' % self.short.id,
                                                              'short_answer': {'text': 'x'}}],
            'integrity_report_url': 'https://exam.example.edu/teacher/exams/1/submissions/1'})

    def report(self, user):
        return self.login(user).get(reverse('contest_fairplay_report', args=['fpexam', 'fp_student']))

    def clear(self, user, netid='fp_student'):
        return self.login(user).post(reverse('contest_fairplay_action', args=['fpexam', 'clear-hand-in']),
                                     {'netid': netid})

    def test_report_shows_the_hand_in_and_the_clear_button(self):
        html = self.report(self.teacher).content.decode()
        self.assertIn('id="fairplay-hand-in"', html)
        self.assertIn('att_1', html)
        self.assertIn('Clear FairPlay hand-in', html)
        # Also when FairPlay is down: the hand-in is DKU OJ's own record.
        self.fp.student_report.side_effect = FairPlayUnreachable()
        self.assertContains(self.report(self.teacher), 'id="fairplay-clear-hand-in"')

    def test_clear(self):
        response = self.clear(self.teacher)
        self.assertRedirects(response, reverse('contest_fairplay_report', args=['fpexam', 'fp_student']),
                             fetch_redirect_response=False)
        self.assertNotIn('id="fairplay-hand-in"', self.report(self.teacher).content.decode())
        # Nothing left to clear: back to the tab with a message.
        response = self.login(self.teacher).post(
            reverse('contest_fairplay_action', args=['fpexam', 'clear-hand-in']), {'netid': 'fp_student'},
            follow=True)
        self.assertContains(response, 'There is no FairPlay hand-in of this student to clear.')
        self.assertContains(self.clear(self.teacher, netid='<bad>'), '', status_code=302)

    def test_clear_permissions_and_csrf(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher):
            self.assertEqual(self.clear(user).status_code, 404, user.username)
        self.assertEqual(self.login(self.teacher, csrf=True).post(
            reverse('contest_fairplay_action', args=['fpexam', 'clear-hand-in']), {'netid': 'fp_student'},
        ).status_code, 403)
        self.assertIn('id="fairplay-hand-in"', self.report(self.teacher).content.decode())

    def test_assignment_page_has_no_receipts_box(self):
        html = self.login(self.teacher).get(reverse('contest_view', args=['fpexam'])).content.decode()
        self.assertNotIn('id="fairplay-receipts"', html)
        self.assertNotIn(reverse('contest_fairplay_report', args=['fpexam', 'fp_student']), html)

    def test_add_time_is_remembered(self):
        for _ in range(2):
            self.login(self.teacher).post(reverse('contest_fairplay_action', args=['fpexam', 'add-time']),
                                          {'minutes': '10'})
        self.assertEqual(added_minutes(self.reload_exam()), 20)
        self.fp.add_time.side_effect = FairPlayError('The exam is not live.', status=409)
        self.login(self.teacher).post(reverse('contest_fairplay_action', args=['fpexam', 'add-time']),
                                      {'minutes': '10'})
        self.assertEqual(added_minutes(self.reload_exam()), 20)

    def test_class_login_marks_the_class(self):
        self.assertIsNone(cache.get(CLASS_LOGIN_KEY % self.klass.id))
        self.login(self.course_admin).post(reverse('fairplay_class_login_create',
                                                   args=[self.org.id, self.org.slug, self.klass.id]))
        self.assertIsNotNone(cache.get(CLASS_LOGIN_KEY % self.klass.id))
        # Seeing an active class login (as a manager of the class) renews the mark.
        cache.delete(CLASS_LOGIN_KEY % self.klass.id)
        self.login(self.course_admin).get(reverse('fairplay_course_login', args=[self.org.id, self.org.slug]))
        self.assertIsNotNone(cache.get(CLASS_LOGIN_KEY % self.klass.id))


@FAIRPLAY_ON
class ClientLimitsTestCase(FairPlayDataMixin, TestCase):
    def call(self, response):
        with mock.patch('judge.utils.fairplay_client.requests.request', return_value=response) as request:
            try:
                return FairPlayClient(acting_user='fp_teacher').exam('fpexam')
            finally:
                self.kwargs = request.call_args[1]

    def test_streamed_with_identity_encoding(self):
        response = fake_response(200, None, chunks=[b'{"status":', b' "live"}'])
        self.assertEqual(self.call(response), {'status': 'live'})
        self.assertTrue(self.kwargs['stream'])
        self.assertEqual(self.kwargs['headers']['Accept-Encoding'], 'identity')
        response.close.assert_called_once_with()

    def test_too_large(self):
        big = fake_response(200, None, chunks=[b'[' + b' ' * (64 * 1024)] * (MAX_RESPONSE_BYTES // (64 * 1024) + 2))
        with self.assertRaises(FairPlayError) as cm:
            self.call(big)
        self.assertNotIsInstance(cm.exception, FairPlayUnreachable)
        big.close.assert_called_once_with()
        # read no further than the cap
        self.assertLessEqual(big.raw.read1.call_count, MAX_RESPONSE_BYTES // (64 * 1024) + 2)
        declared = fake_response(200, {'ok': True})
        declared.headers = {'Content-Length': str(MAX_RESPONSE_BYTES + 1)}
        with self.assertRaises(FairPlayError):
            self.call(declared)
        declared.raw.read1.assert_not_called()

    def test_slow_trickle_hits_the_deadline(self):
        # One byte per read, each just under the read timeout: the overall deadline stops it.
        trickle = fake_response(200, None, chunks=[b' '] * 1000)
        clock = iter(range(0, 100000, 2))
        with mock.patch('judge.utils.fairplay_client.monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(FairPlayUnreachable):
                self.call(trickle)
        self.assertLess(trickle.raw.read1.call_count, 30)
        trickle.close.assert_called_once_with()

    def test_read_error_mid_body(self):
        import urllib3
        broken = fake_response(200, {'ok': True})
        broken.raw.read1.side_effect = urllib3.exceptions.ReadTimeoutError(None, None, 'slow')
        with self.assertRaises(FairPlayUnreachable):
            self.call(broken)

    def test_insecure_url_refused(self):
        with override_settings(FAIRPLAY_URL='http://exam.example.edu'):     # tests run with DEBUG off
            with mock.patch('judge.utils.fairplay_client.requests.request') as request:
                with self.assertRaises(FairPlayUnreachable):
                    FairPlayClient(acting_user='fp_teacher').exam('fpexam')
            request.assert_not_called()


class DeploymentCheckTestCase(SimpleTestCase):
    def ids(self, **settings):
        with override_settings(**settings):
            return [error.id for error in fairplay_bridge_check()]

    def test_bridge_off_no_errors(self):
        self.assertEqual(self.ids(FAIRPLAY_TOKEN='', CACHES={}, FAIRPLAY_URL='http://exam.example.edu'), [])

    def test_per_process_cache_refused(self):
        for backend in ('locmem.LocMemCache', 'dummy.DummyCache', 'filebased.FileBasedCache'):
            with self.subTest(backend=backend):
                caches = {'default': {'BACKEND': 'django.core.cache.backends.' + backend, 'LOCATION': '/tmp/x'}}
                self.assertEqual(self.ids(FAIRPLAY_TOKEN=TOKEN, CACHES=caches,
                                          FAIRPLAY_URL='https://exam.example.edu'), ['judge.E101'])
        self.assertEqual(self.ids(FAIRPLAY_TOKEN=TOKEN, CACHES={}, FAIRPLAY_URL='https://exam.example.edu'),
                         ['judge.E101'])
        self.assertEqual(self.ids(FAIRPLAY_TOKEN=TOKEN, CACHES=REDIS, FAIRPLAY_URL='https://exam.example.edu'), [])

    def test_plain_http_off_the_loopback(self):
        base = dict(FAIRPLAY_TOKEN=TOKEN, CACHES=REDIS, FAIRPLAY_ALLOW_INSECURE=False)
        self.assertEqual(self.ids(FAIRPLAY_URL='http://exam.example.edu', **base), ['judge.E102'])
        self.assertEqual(self.ids(FAIRPLAY_URL='http://host.docker.internal:8000', **base), ['judge.E102'])
        for url in ('http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000', 'https://exam.example.edu'):
            with self.subTest(url=url):
                self.assertEqual(self.ids(FAIRPLAY_URL=url, **base), [])
        self.assertEqual(self.ids(FAIRPLAY_URL='http://host.docker.internal:8000',
                                  **dict(base, FAIRPLAY_ALLOW_INSECURE=True)), [])
        # DEBUG patched: override_settings(DEBUG) would rebuild the template engines and lose response.context.
        with mock.patch.object(settings, 'DEBUG', True):
            self.assertEqual(self.ids(FAIRPLAY_URL='http://host.docker.internal:8000', **base), [])

    def test_local_kit_settings_pass(self):
        # A local test set-up: Redis and FAIRPLAY_ALLOW_INSECURE pass even with DEBUG off.
        self.assertEqual(self.ids(FAIRPLAY_TOKEN=TOKEN, CACHES=REDIS, FAIRPLAY_ALLOW_INSECURE=True,
                                  FAIRPLAY_URL='http://host.docker.internal:8000'), [])
        with override_settings(FAIRPLAY_ALLOW_INSECURE='yes'):    # only True itself counts
            self.assertTrue(fairplay_url_insecure('http://exam.example.edu'))

    def test_token_without_url(self):
        self.assertEqual(self.ids(FAIRPLAY_TOKEN=TOKEN, CACHES=REDIS, FAIRPLAY_URL=''), ['judge.E103'])
        self.assertEqual(self.ids(FAIRPLAY_TOKEN='', CACHES=REDIS, FAIRPLAY_URL=''), [])


@FAIRPLAY_ON
class EarlyRejectTestCase(FairPlayDataMixin, TestCase):
    def post(self, extra, size=10, declared=None):
        from django.core.handlers.wsgi import WSGIRequest
        url = reverse('fairplay_api_submissions', args=['fpexam'])
        body = b'x' * size
        meta = dict(extra)
        if declared is not None:
            meta['CONTENT_LENGTH'] = str(declared)
        with mock.patch.object(WSGIRequest, 'read', side_effect=AssertionError('body read')) as read:
            response = Client().generic('POST', url, body, content_type='application/json', **meta)
        return response, read

    def test_refused_without_reading_the_body(self):
        url = reverse('fairplay_api_submissions', args=['fpexam'])
        good = fp_signed('POST', url, b'x' * 10)
        stale = fp_signed('POST', url, b'x' * 10, timestamp=int(timezone.now().timestamp()) - 3600)
        malformed = dict(good, HTTP_X_FP_NONCE='not-a-nonce')
        for label, extra, declared, status in (
                ('no signature headers', {}, None, 401),
                ('malformed headers', malformed, None, 401),
                ('stale timestamp', stale, None, 401),
                ('declared body over the limit', good, 3 * 1024 * 1024, 413)):
            with self.subTest(label):
                response, read = self.post(extra, declared=declared)
                self.assertEqual(response.status_code, status)
                read.assert_not_called()


@FAIRPLAY_ON
class LenientRepliesTestCase(PagesMixin, TestCase):
    def test_tab_survives_odd_counts_and_students(self):
        self.fp.exam.return_value = exam(counts={'roster': 'many', 'set_up': None, 'handed_in': [1]},
                                         students=['not a dict', None, {'netid': 'fp_student', 'status': 'waiting'}],
                                         open_to=7)
        response = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['not_set_up'], 0)
        self.assertEqual(response.context['fp_waiting'], [{'netid': 'fp_student', 'status': 'waiting'}])

    def test_hidden_assignment_is_not_given_to_fairplay(self):
        from judge.models import Contest
        client = self.login(self.teacher)
        self.assertNotContains(client.get(reverse('contest_fairplay', args=['fpexam'])), 'id="fairplay-not-listed"')
        Contest.objects.filter(pk=self.exam.pk).update(is_visible=False)
        self.assertContains(client.get(reverse('contest_fairplay', args=['fpexam'])), 'id="fairplay-not-listed"')

    def test_key_rename_with_odd_settings(self):
        from judge.utils.fairplay_editor import FairPlayEditorPlan, fairplay_save_settings
        with mock.patch('judge.utils.fairplay_client.FairPlayClient') as client_class:
            fp = client_class.return_value
            fp.exam.return_value = {'settings': {'calculator': 'abacus', 'upload_opens_days': 'soon',
                                                 'review': 42, 'websites': 'x'}}
            fp.set_settings.return_value = {'settings': {'websites_blocked': 'nope'}}
            result = fairplay_save_settings('fp_teacher', self.exam, FairPlayEditorPlan(), old_key='fpold')
        self.assertIsNone(result.not_saved)
        sent = fp.set_settings.call_args.args[1]
        self.assertEqual((sent['calculator'], sent['upload_opens_days'], sent['review']), ('off', 0, 'nothing'))
        self.assertEqual(fp.set_settings.call_args.kwargs, {'read_timeout': 3})


@FAIRPLAY_ON
class AttachmentFieldsTestCase(FairPlayDataMixin, TestCase):
    def test_attachment_in_file_instructions_hidden(self):
        from django.core.files.base import ContentFile
        from judge.models import EditorAttachment, ProblemFileConfig
        from judge.utils.fairplay import fairplay_hidden_for
        attachment = EditorAttachment(original_name='brief.pdf', size=3)
        attachment.file.save('brief.pdf', ContentFile(b'pdf'), save=False)
        attachment.save()
        ProblemFileConfig.objects.filter(problem=self.file).update(
            instructions='See [the brief](/widgets/files/%s/).' % attachment.uuid)
        self.assertTrue(fairplay_hidden_for(self.student, attachment))
        self.assertFalse(fairplay_hidden_for(self.teacher, attachment))


class AddedMinutesTTLTestCase(SimpleTestCase):
    def test_capped_at_180_days(self):
        from datetime import timedelta
        from judge.utils.fairplay_delivery import _added_ttl
        far = mock.Mock(end_time=timezone.now() + timedelta(days=400))
        self.assertEqual(_added_ttl(far), 180 * 24 * 3600)
        near = mock.Mock(end_time=timezone.now())
        self.assertEqual(_added_ttl(near), 60 * 24 * 3600)


@FAIRPLAY_ON
class TokenRotationTestCase(FairPlayDataMixin, TestCase):
    def call(self, token):
        url = reverse('fairplay_api_exams')
        return self.client.get(url, **fp_signed('GET', url, token=token))

    def test_parsing(self):
        for value in ([NEW_TOKEN, TOKEN], (NEW_TOKEN, TOKEN), '%s, %s' % (NEW_TOKEN, TOKEN),
                      '%s,%s,%s' % (NEW_TOKEN, TOKEN, 'x' * 40), [NEW_TOKEN, 'short', TOKEN]):
            with self.subTest(value=value), override_settings(FAIRPLAY_TOKEN=value):
                self.assertEqual(fairplay_tokens(), [NEW_TOKEN, TOKEN])
                self.assertEqual(fairplay_token(), NEW_TOKEN)
        with override_settings(FAIRPLAY_TOKEN='short,also-short'):
            self.assertEqual(fairplay_tokens(), [])
            self.assertEqual(fairplay_token(), '')

    def test_either_token_accepted(self):
        with override_settings(FAIRPLAY_TOKEN=[NEW_TOKEN, TOKEN]):
            self.assertEqual(self.call(TOKEN).status_code, 200)
            self.assertEqual(self.call(NEW_TOKEN).status_code, 200)
            self.assertEqual(self.call('fp-unknown-token-0123456789abcdefghij').status_code, 401)
        with override_settings(FAIRPLAY_TOKEN='%s,%s' % (TOKEN, NEW_TOKEN)):
            self.assertEqual(self.call(NEW_TOKEN).status_code, 200)
        with override_settings(FAIRPLAY_TOKEN=TOKEN):
            self.assertEqual(self.call(NEW_TOKEN).status_code, 401)

    def test_direction_keys_per_token(self):
        # The other direction's key of either token is refused.
        url = reverse('fairplay_api_exams')
        from judge.utils.fairplay_signing import signed_headers
        with override_settings(FAIRPLAY_TOKEN=[NEW_TOKEN, TOKEN]):
            for token in (NEW_TOKEN, TOKEN):
                headers = signed_headers('GET', url, b'', token=token, direction=DIRECTION_OJ_TO_FP)
                meta = {'HTTP_' + k.upper().replace('-', '_'): v for k, v in headers.items()}
                self.assertEqual(self.client.get(url, **meta).status_code, 401)

    def test_outgoing_signed_with_the_first(self):
        with override_settings(FAIRPLAY_TOKEN=[NEW_TOKEN, TOKEN]):
            with mock.patch('judge.utils.fairplay_client.requests.request',
                            return_value=fake_response(200, {'ok': True})) as request:
                FairPlayClient(acting_user='fp_teacher').close_exam('fpexam')
        headers = request.call_args[1]['headers']
        canonical = canonical_string('POST', '/api/integrations/dkuoj/v1/exams/fpexam/close',
                                     headers['X-FP-Timestamp'], headers['X-FP-Nonce'], 'fp_teacher',
                                     hashlib.sha256(b'').hexdigest())
        self.assertEqual(headers['X-FP-Signature'], sign(direction_key(DIRECTION_OJ_TO_FP, NEW_TOKEN), canonical))
        self.assertNotEqual(headers['X-FP-Signature'], sign(direction_key(DIRECTION_FP_TO_OJ, NEW_TOKEN), canonical))

    def test_replay_rejected_with_rotation(self):
        url = reverse('fairplay_api_exams')
        with override_settings(FAIRPLAY_TOKEN=[NEW_TOKEN, TOKEN]):
            meta = fp_signed('GET', url, token=TOKEN)
            self.assertEqual(self.client.get(url, **meta).status_code, 200)
            self.assertEqual(self.client.get(url, **meta).status_code, 401)
        cache.delete('fpb:nonce:' + meta['HTTP_X_FP_NONCE'])


@FAIRPLAY_ON
class WebsitesBlockedTestCase(PagesMixin, TestCase):
    def tab(self):
        return self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()

    def test_warning_listed_and_escaped(self):
        data = exam()
        data['settings']['websites_blocked'] = [
            {'site': 'chat.openai.com', 'reason': 'AI chat'},
            {'site': '<img src=x onerror=alert(1)>', 'reason': '<b>messaging</b>'},
            'not a dict', {'reason': 'no site'},
        ]
        self.fp.exam.return_value = data
        html = self.tab()
        self.assertIn('id="fairplay-websites-blocked"', html)
        self.assertIn('FairPlay will not open these sites during the exam:', html)
        self.assertIn('chat.openai.com', html)
        self.assertIn('(AI chat)', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<b>messaging</b>', html)
        self.assertNotIn('no site', html)

    def test_no_warning_when_absent(self):
        self.assertNotIn('id="fairplay-websites-blocked"', self.tab())
        data = exam()
        data['settings']['websites_blocked'] = []
        self.fp.exam.return_value = data
        self.assertNotIn('id="fairplay-websites-blocked"', self.tab())
        self.fp.exam.side_effect = FairPlayUnreachable()
        self.assertNotIn('id="fairplay-websites-blocked"', self.tab())

    def test_editor_has_no_websites_help(self):
        with mock.patch('judge.utils.fairplay_client.FairPlayClient') as client_class:
            client_class.return_value.exam.return_value = exam()
            html = self.login(self.teacher).get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('always blocked by FairPlay', html)

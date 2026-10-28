"""FairPlay bridge: signing, the roster endpoint, the client and the professor pages."""
import hashlib
import hmac
import json
import os
from datetime import timedelta
from unittest import mock

import requests
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import Class
from judge.models.tests.util import create_organization, create_user
from judge.utils.fairplay import is_fairplay_exam
from judge.utils.fairplay_client import CLASS_STATUS_CACHE_KEY, FairPlayClient, FairPlayError, FairPlayUnreachable, \
    QUICK_READ_TIMEOUT, cached_class_status
from judge.utils.fairplay_signing import DIRECTION_FP_TO_OJ, DIRECTION_OJ_TO_FP, body_sha256, canonical_string, \
    direction_key, sign, signed_headers
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, TOKEN, create_contest, \
    fp_signed, grant, set_fairplay

# Fixed signing vectors, shared with the FairPlay server's tests.
VECTORS_PATH = os.path.join(os.path.dirname(__file__), 'fairplay_vectors.json')


def load_vectors():
    with open(VECTORS_PATH, encoding='utf-8') as f:
        return json.load(f)


def meta(headers):
    return {'HTTP_' + key.upper().replace('-', '_'): value for key, value in headers.items()}


class SigningVectorsTestCase(SimpleTestCase):
    def test_direction_keys(self):
        vectors = load_vectors()
        self.assertEqual(vectors['token'], TOKEN)
        self.assertEqual(direction_key(DIRECTION_OJ_TO_FP, vectors['token']).hex(), vectors['key_oj_to_fp_hex'])
        self.assertEqual(direction_key(DIRECTION_FP_TO_OJ, vectors['token']).hex(), vectors['key_fp_to_oj_hex'])
        expected = hmac.new(TOKEN.encode(), b'fairplay-bridge v2 oj->fp', hashlib.sha256).hexdigest()
        self.assertEqual(vectors['key_oj_to_fp_hex'], expected)

    def test_every_vector(self):
        vectors = load_vectors()
        self.assertEqual(len(vectors['cases']), 4)
        for case in vectors['cases']:
            with self.subTest(path=case['path']):
                canonical = canonical_string(case['method'], case['path'], case['timestamp'], case['nonce'],
                                             case['acting_user'], body_sha256(case['body'].encode()))
                self.assertEqual(canonical, case['canonical'])
                key = direction_key(case['direction'], vectors['token'])
                self.assertEqual(sign(key, canonical), case['signature'])
                headers = signed_headers(case['method'], case['path'], case['body'].encode(),
                                         acting_user=case['acting_user'], timestamp=case['timestamp'],
                                         nonce=case['nonce'], token=vectors['token'], direction=case['direction'])
                self.assertEqual(headers['X-FP-Signature'], case['signature'])
                self.assertEqual(headers['X-FP-Timestamp'], str(case['timestamp']))
                self.assertEqual(headers['X-FP-Nonce'], case['nonce'])
                self.assertEqual(headers.get('X-FP-Acting-User', ''), case['acting_user'])

    def test_empty_body_hash(self):
        self.assertEqual(body_sha256(b''), hashlib.sha256(b'').hexdigest())
        self.assertEqual(body_sha256(None), hashlib.sha256(b'').hexdigest())


@FAIRPLAY_ON
class IncomingSignatureTestCase(FairPlayDataMixin, TestCase):
    """The receiving side, replaying the fixed vectors at their own clock."""

    def setUp(self):
        self.vectors = {case['path']: case for case in load_vectors()['cases']}
        for case in self.vectors.values():
            cache.delete('fpb:nonce:' + case['nonce'])

    def vector(self, path_prefix):
        return next(c for p, c in self.vectors.items() if p.startswith(path_prefix) and c['direction'] == 'fp->oj')

    def send(self, case, path=None, body=None, clock=None, **header_overrides):
        headers = {'X-FP-Timestamp': str(case['timestamp']), 'X-FP-Nonce': case['nonce'],
                   'X-FP-Signature': case['signature']}
        headers.update(header_overrides)
        with mock.patch('judge.utils.fairplay_signing.time.time', return_value=clock or case['timestamp']):
            if case['method'] == 'GET':
                return self.client.get(path or case['path'], **meta(headers))
            return self.client.post(path or case['path'], (body if body is not None else case['body']).encode(),
                                    content_type='application/json', **meta(headers))

    def test_vector_get_accepted_then_replay_rejected(self):
        case = self.vector('/api/fairplay/v1/exams?')
        response = self.send(case)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('exams', response.json())
        replay = self.send(case)
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(replay.json(), {'detail': 'bad signature'})

    def test_vector_post_signature_accepted(self):
        case = self.vector('/api/fairplay/v1/exams/c000001/submissions')
        response = self.send(case)
        # The signature passes; the (unknown) exam is the view's own 404.
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail'], 'No FairPlay exam with this key.')

    def test_clock_window(self):
        case = self.vector('/api/fairplay/v1/exams?')
        self.assertEqual(self.send(case, clock=case['timestamp'] + 61).status_code, 401)
        self.assertEqual(self.send(case, clock=case['timestamp'] - 61).status_code, 401)
        self.assertEqual(self.send(case, clock=case['timestamp'] + 60).status_code, 200)

    def test_tampering(self):
        case = self.vector('/api/fairplay/v1/exams?')
        self.assertEqual(self.send(case, path=case['path'].replace('page=2', 'page=3')).status_code, 401)
        self.assertEqual(self.send(case, **{'X-FP-Acting-User': 'someone'}).status_code, 401)
        self.assertEqual(self.send(case, **{'X-FP-Signature': '0' * 64}).status_code, 401)
        self.assertEqual(self.send(case, **{'X-FP-Nonce': 'xyz'}).status_code, 401)
        self.assertEqual(self.send(case, **{'X-FP-Timestamp': 'soon'}).status_code, 401)
        post = self.vector('/api/fairplay/v1/exams/c000001/submissions')
        self.assertEqual(self.send(post, body='{"username":"bl642","answers":[1]}').status_code, 401)
        # The untampered request still goes through: failures did not burn its nonce.
        self.assertEqual(self.send(case).status_code, 200)

    def test_other_direction_key_rejected(self):
        url = reverse('fairplay_api_exams')
        headers = signed_headers('GET', url, b'', token=TOKEN, direction=DIRECTION_OJ_TO_FP)
        self.assertEqual(self.client.get(url, **meta(headers)).status_code, 401)
        self.assertEqual(self.client.get(url, **fp_signed('GET', url)).status_code, 200)

    def test_body_limit_before_hashing(self):
        url = reverse('fairplay_api_submissions', args=['fpexam'])
        body = b'x' * (2 * 1024 * 1024 + 1)
        headers = fp_signed('POST', url, b'{}')
        with mock.patch('judge.utils.fairplay_signing.body_sha256') as hashing:
            response = self.client.post(url, body, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 413)
        hashing.assert_not_called()

    def test_bridge_off(self):
        case = self.vector('/api/fairplay/v1/exams?')
        with FAIRPLAY_OFF:
            self.assertEqual(self.send(case).status_code, 404)
            url = reverse('fairplay_api_class_roster', args=[self.klass.id])
            self.assertEqual(self.client.get(url, **fp_signed('GET', url)).status_code, 404)


@FAIRPLAY_ON
class RosterEndpointTestCase(FairPlayDataMixin, TestCase):
    def get(self, class_id, signed=True):
        url = reverse('fairplay_api_class_roster', args=[class_id])
        return self.client.get(url, **(fp_signed('GET', url) if signed else {}))

    def test_roster(self):
        # Staff and teachers who are members are not students.
        self.klass.members.add(self.course_admin.profile, self.staff.profile)
        ta = create_user(username='fp_ta')
        self.klass.admins.add(ta.profile)
        self.klass.members.add(ta.profile)
        # a professor / TA of another course who edits any FairPlay assignment is not a student
        other_prof = create_user(username='fp_other_prof')
        other_curator = create_user(username='fp_other_curator')
        create_contest('fpotherexam', authors=('fp_other_prof',), curators=('fp_other_curator',),
                       fairplay_required=True)
        plain_author = create_user(username='fp_plain_author')
        create_contest('fpplainexam', authors=('fp_plain_author',))
        for user in (other_prof, other_curator, plain_author):
            self.klass.members.add(user.profile)
        response = self.get(self.klass.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'class': {'id': self.klass.id, 'name': 'FP Section A'},
            'course': {'id': self.org.id, 'name': 'fporg'},
            'students': [
                {'username': 'fp_classmate', 'first_name': '', 'last_name': ''},
                {'username': 'fp_plain_author', 'first_name': '', 'last_name': ''},
                {'username': 'fp_student', 'first_name': 'Avazbek', 'last_name': 'Khamidov'},
            ],
        })

    def test_unknown_class_and_unsigned(self):
        self.assertEqual(self.get(999999).status_code, 404)
        self.assertEqual(self.get(self.klass.id, signed=False).status_code, 401)


@FAIRPLAY_ON
class StudentClassesEndpointTestCase(FairPlayDataMixin, TestCase):
    def get(self, netid, signed=True):
        url = reverse('fairplay_api_student_classes', args=[netid])
        return self.client.get(url, **(fp_signed('GET', url) if signed else {}))

    def test_classes_of_a_student(self):
        self.klass.instructor_names = 'Shuai Dong'
        self.klass.save()
        other = Class.objects.create(organization=self.org, name='FP Section B', slug='fp-b')
        other.members.add(self.student.profile)
        other.admins.add(self.course_admin.profile)
        self.course_admin.first_name, self.course_admin.last_name = 'Course', 'Admin'
        self.course_admin.save()
        gone = Class.objects.create(organization=self.org, name='FP Old', slug='fp-old', is_active=False)
        gone.members.add(self.student.profile)
        response = self.get('fp_student')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response.json(), {'classes': [
            {'id': self.klass.id, 'name': 'FP Section A', 'course': {'id': self.org.id, 'name': 'fporg'},
             'teachers': ['Shuai Dong']},
            {'id': other.id, 'name': 'FP Section B', 'course': {'id': self.org.id, 'name': 'fporg'},
             'teachers': ['Course Admin']},
        ]})

    def test_teachers_are_not_students_of_their_class(self):
        self.klass.members.add(self.course_admin.profile, self.staff.profile)
        ta = create_user(username='fp_ta2')
        self.klass.admins.add(ta.profile)
        self.klass.members.add(ta.profile)
        for netid in ('fp_course_admin', 'fp_staff', 'fp_ta2', 'fp_outsider', 'no_such_user'):
            with self.subTest(netid=netid):
                response = self.get(netid)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'classes': []})

    def test_unsigned(self):
        self.assertEqual(self.get('fp_student', signed=False).status_code, 401)
        with FAIRPLAY_OFF:
            self.assertEqual(self.get('fp_student').status_code, 404)


def fake_response(status=200, payload=None, text=None, chunks=None):
    """A streamed ``requests`` response: the body comes from ``raw.read1`` in ``chunks``."""
    response = mock.Mock()
    response.status_code = status
    response.headers = {}
    if chunks is None:
        body = json.dumps(payload).encode() if payload is not None else (text or 'not json').encode()
        chunks = [body]
    state = {'next': 0}

    def read1(*args, **kwargs):
        index = state['next']
        state['next'] += 1
        return chunks[index] if index < len(chunks) else b''

    response.raw.read1.side_effect = read1
    # The client closes every response; the same mock may answer the next call from the start.
    response.close.side_effect = lambda: state.update(next=0)
    return response


@FAIRPLAY_ON
class ClientTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        cache.delete(CLASS_STATUS_CACHE_KEY % self.klass.id)

    def test_signed_request(self):
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'status': 'live'})) as call:
            data = FairPlayClient(acting_user='fp_teacher').open_exam('fpexam', 'all')
        self.assertEqual(data, {'status': 'live'})
        args, kwargs = call.call_args
        self.assertEqual(args, ('POST', 'https://exam.example.edu/api/integrations/dkuoj/v1/exams/fpexam/open'))
        self.assertEqual(kwargs['timeout'], (5, 15))
        body = kwargs['data']
        self.assertEqual(json.loads(body), {'open_to': 'all'})
        headers = kwargs['headers']
        self.assertEqual(headers['X-FP-Acting-User'], 'fp_teacher')
        canonical = canonical_string('POST', '/api/integrations/dkuoj/v1/exams/fpexam/open',
                                     headers['X-FP-Timestamp'], headers['X-FP-Nonce'], 'fp_teacher',
                                     hashlib.sha256(body).hexdigest())
        self.assertEqual(headers['X-FP-Signature'], sign(direction_key(DIRECTION_OJ_TO_FP, TOKEN), canonical))

    def test_access_codes_pdf(self):
        response = fake_response(200, chunks=[b'%PDF-1.4 ', b'codes'])
        response.headers = {'Content-Type': 'application/pdf'}
        with mock.patch('judge.utils.fairplay_client.requests.request', return_value=response) as call:
            data = FairPlayClient().access_codes_pdf('fpexam', 'fp_teacher')
        self.assertEqual(data, b'%PDF-1.4 codes')
        args, kwargs = call.call_args
        path = '/api/integrations/dkuoj/v1/exams/fpexam/access-codes.pdf'
        self.assertEqual(args, ('GET', 'https://exam.example.edu' + path))
        headers = kwargs['headers']
        self.assertEqual(headers['X-FP-Acting-User'], 'fp_teacher')
        self.assertEqual(headers['Accept'], 'application/pdf')
        canonical = canonical_string('GET', path, headers['X-FP-Timestamp'], headers['X-FP-Nonce'], 'fp_teacher',
                                     hashlib.sha256(b'').hexdigest())
        self.assertEqual(headers['X-FP-Signature'], sign(direction_key(DIRECTION_OJ_TO_FP, TOKEN), canonical))

    def test_access_codes_pdf_errors(self):
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(409, {'detail': 'The exam is not ready.'})):
            with self.assertRaises(FairPlayError) as cm:
                FairPlayClient().access_codes_pdf('fpexam', 'fp_teacher')
        self.assertEqual((cm.exception.status, cm.exception.detail), (409, 'The exam is not ready.'))
        html = fake_response(200, text='<html>')
        html.headers = {'Content-Type': 'text/html'}
        with mock.patch('judge.utils.fairplay_client.requests.request', return_value=html):
            with self.assertRaises(FairPlayError):
                FairPlayClient().access_codes_pdf('fpexam', 'fp_teacher')

    def test_errors(self):
        client = FairPlayClient(acting_user='fp_teacher')
        with mock.patch('judge.utils.fairplay_client.requests.request', side_effect=requests.ConnectionError()):
            with self.assertRaises(FairPlayUnreachable):
                client.exam('fpexam')
        with mock.patch('judge.utils.fairplay_client.requests.request', side_effect=requests.Timeout()):
            with self.assertRaises(FairPlayUnreachable):
                client.exam('fpexam')
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(403, {'detail': 'Not an editor of this exam.'})):
            with self.assertRaises(FairPlayError) as cm:
                client.exam('fpexam')
        self.assertEqual((cm.exception.status, cm.exception.detail), (403, 'Not an editor of this exam.'))
        with mock.patch('judge.utils.fairplay_client.requests.request', return_value=fake_response(502)):
            with self.assertRaises(FairPlayError) as cm:
                client.exam('fpexam')
        self.assertEqual(cm.exception.status, 502)
        with override_settings(FAIRPLAY_URL=''):
            with self.assertRaises(FairPlayUnreachable):
                client.exam('fpexam')

    def test_read_timeouts(self):
        client = FairPlayClient(acting_user='fp_teacher')
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'ok': True})) as call:
            client.exam('fpexam', read_timeout=QUICK_READ_TIMEOUT)
            self.assertEqual(call.call_args[1]['timeout'], (3, 3))
            client.class_status(self.klass.id, use_cache=False, read_timeout=QUICK_READ_TIMEOUT)
            self.assertEqual(call.call_args[1]['timeout'], (3, 3))
            client.close_exam('fpexam')                       # exam actions keep the long timeout
            self.assertEqual(call.call_args[1]['timeout'], (5, 15))
        self.assertIsNone(cached_class_status(999999))

    def test_class_status_cached_and_invalidated(self):
        client = FairPlayClient(acting_user='fp_course_admin')
        status = {'login': None, 'students': []}
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, status)) as call:
            self.assertEqual(client.class_status(self.klass.id), status)
            self.assertEqual(client.class_status(self.klass.id), status)
            self.assertEqual(call.call_count, 1)
            client.reset_student('fp_student', self.klass.id)
            self.assertEqual(call.call_args[0][1],
                             'https://exam.example.edu/api/integrations/dkuoj/v1/students/fp_student/reset')
            self.assertEqual(json.loads(call.call_args[1]['data']), {'class_id': self.klass.id})
            client.class_status(self.klass.id)
            self.assertEqual(call.call_count, 3)

    def test_class_status_cache_never_holds_the_password(self):
        client = FairPlayClient(acting_user='fp_course_admin')
        status = {'login': {'username': 'fpsecti-m4tz', 'password': 'river-cotton-pine-moss-583', 'used': 2},
                  'history': [], 'students': []}
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, status)) as call:
            fresh = client.class_status(self.klass.id, use_cache=False)
            self.assertEqual(fresh['login']['password'], 'river-cotton-pine-moss-583')
            self.assertNotIn('password', cached_class_status(self.klass.id)['login'])
            self.assertNotIn('river-cotton', json.dumps(cache.get(CLASS_STATUS_CACHE_KEY % self.klass.id)))
            cache.delete(CLASS_STATUS_CACHE_KEY % self.klass.id)
            cached = client.class_status(self.klass.id)            # the home page's kind of call
            self.assertNotIn('password', cached['login'])
            self.assertEqual(call.call_count, 2)
        self.assertEqual(status['login']['password'], 'river-cotton-pine-moss-583')   # the answer itself untouched

    def test_paths(self):
        client = FairPlayClient(acting_user='u')
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'ok': True})) as call:
            client.create_class_login(self.klass)
            self.assertEqual(call.call_args[0], (
                'POST', 'https://exam.example.edu/api/integrations/dkuoj/v1/classes/%d/login' % self.klass.id))
            self.assertEqual(json.loads(call.call_args[1]['data']),
                             {'class_name': 'FP Section A', 'course_name': 'fporg'})
            client.stop_class_login(self.klass)
            self.assertEqual(call.call_args[0][0], 'DELETE')
            self.assertIsNone(call.call_args[1]['data'])
            for method, args, path in (
                    ('close_exam', ('fpexam',), '/exams/fpexam/close'),
                    ('new_code', ('fpexam',), '/exams/fpexam/new-code'),
                    ('add_time', ('fpexam', 10), '/exams/fpexam/add-time'),
                    ('approve', ('fpexam', 'fp_student'), '/exams/fpexam/approve'),
                    ('student_report', ('fpexam', 'fp_student'), '/exams/fpexam/students/fp_student/report')):
                getattr(client, method)(*args)
                self.assertEqual(call.call_args[0][1], 'https://exam.example.edu/api/integrations/dkuoj/v1' + path)


EXAM = {
    'synced': True, 'package_ready': True, 'questions': 5, 'points': 66, 'status': 'draft',
    'start_code': None, 'invigilator_code': None, 'closes_at': None, 'open_to': 'all',
    'settings': {'calculator': 'scientific', 'open_book': False, 'websites': ['docs.python.org'],
                 'strong_lock': True, 'resume_needs_approval': True},
    'counts': {'roster': 2, 'set_up': 1, 'not_started': 2, 'writing': 0, 'offline': 0, 'waiting': 0,
               'handed_in': 0},
    'students': [], 'server_time': '2026-10-24T09:00:00Z',
}


def exam(**overrides):
    data = json.loads(json.dumps(EXAM))
    data.update(overrides)
    return data


LOGIN_STATUS = {
    'login': {'username': 'fpsecti-m4tz', 'password': 'river-cotton-pine-moss-583',
              'created_at': '2026-10-20T08:00:00Z',
              'expires_at': '2026-11-03T08:00:00Z', 'used': 28, 'created_by': 'fp_course_admin'},
    'history': [
        {'username': 'fpsecti-m4tz', 'created_at': '2026-10-20T08:00:00Z', 'expires_at': '2026-11-03T08:00:00Z',
         'ended_at': None, 'end_reason': None, 'used': 28, 'created_by': 'fp_course_admin'},
        {'username': 'fpsecti-old1', 'created_at': '2026-10-10T08:00:00Z', 'expires_at': '2026-10-24T08:00:00Z',
         'ended_at': '2026-10-20T08:00:00Z', 'end_reason': 'replaced', 'used': 17, 'created_by': 'fp_course_admin'},
        {'username': 'fpsecti-old2', 'created_at': '2026-09-10T08:00:00Z', 'expires_at': '2026-09-24T08:00:00Z',
         'ended_at': '2026-09-24T08:00:00Z', 'end_reason': 'expired', 'used': 3, 'created_by': None},
        {'username': 'fpsecti-old3', 'created_at': '2026-09-01T08:00:00Z', 'expires_at': '2026-09-15T08:00:00Z',
         'ended_at': '2026-09-02T08:00:00Z', 'end_reason': 'stopped', 'used': 0, 'created_by': None},
        {'username': 'fpsecti-old4', 'created_at': '2026-08-01T08:00:00Z', 'expires_at': '2026-08-15T08:00:00Z',
         'ended_at': '2026-08-02T08:00:00Z', 'end_reason': 'locked', 'used': 1, 'created_by': None},
    ],
    'students': [],
}


class PagesMixin(FairPlayDataMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.class_ta = create_user(username='fp_class_ta')
        cls.klass.admins.add(cls.class_ta.profile)
        cls.other_teacher = create_user(username='fp_other_teacher')
        grant(cls.other_teacher, 'edit_own_contest')
        cls.other_org = create_organization(name='otherorg', admins=('fp_other_teacher',))

    def setUp(self):
        super().setUp()
        patcher = mock.patch('judge.views.fairplay.FairPlayClient')
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.fp = self.client_class.return_value
        self.fp.exam.return_value = exam()
        self.fp.class_status.return_value = {
            'login': {'username': 'fpsecti-m4tz', 'created_at': '2026-10-20T08:00:00Z',
                      'expires_at': '2026-11-03T08:00:00Z', 'used': 1},
            'students': [{'netid': 'fp_student', 'set_up': True, 'username': 'avaz',
                          'set_up_at': '2026-10-21T08:00:00Z'}],
        }

    def login(self, user, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user)
        return client

    def assert_acting(self, user):
        self.client_class.assert_called_with(acting_user=user.username)


@FAIRPLAY_ON
class MenuTestCase(PagesMixin, TestCase):
    def test_no_menu_item_for_anyone(self):
        for user in (self.student, self.teacher, self.course_admin, self.class_ta, self.other_teacher, self.staff):
            html = self.login(user).get(reverse('problem_list')).content.decode()
            self.assertNotIn('nav-fairplay', html, user.username)
            self.assertNotIn(reverse('fairplay_home'), html, user.username)
        self.fp.class_status.assert_not_called()


@FAIRPLAY_ON
class CoursePagesTestCase(PagesMixin, TestCase):
    def test_home_cards(self):
        response = self.login(self.course_admin).get(reverse('fairplay_home'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(reverse('fairplay_course', args=[self.org.id, self.org.slug]), html)
        self.assertIn('1 of 2 set up in FairPlay', html)
        self.assertIn('1 FairPlay exam', html)
        self.assertIn('CS 101 Midterm', html)  # next FairPlay exam
        self.assertNotIn('otherorg', html)
        self.assert_acting(self.course_admin)

    def test_home_never_shows_a_password(self):
        self.fp.class_status.return_value = dict(LOGIN_STATUS)
        html = self.login(self.course_admin).get(reverse('fairplay_home')).content.decode()
        self.assertNotIn('river-cotton-pine-moss-583', html)
        self.assertEqual(self.fp.class_status.call_args[1]['use_cache'], True)

    def test_home_permissions(self):
        for user in (self.student, self.teacher, self.outsider):
            self.assertEqual(self.login(user).get(reverse('fairplay_home')).status_code, 404)
        self.assertEqual(Client().get(reverse('fairplay_home')).status_code, 302)
        html = self.login(self.other_teacher).get(reverse('fairplay_home')).content.decode()
        self.assertIn('otherorg', html)
        self.assertNotIn(reverse('fairplay_course', args=[self.org.id, self.org.slug]), html)
        self.assertEqual(self.login(self.staff).get(reverse('fairplay_home')).status_code, 200)
        with FAIRPLAY_OFF:
            self.assertEqual(self.login(self.course_admin).get(reverse('fairplay_home')).status_code, 404)

    def test_home_unreachable(self):
        self.fp.class_status.side_effect = FairPlayUnreachable()
        response = self.login(self.course_admin).get(reverse('fairplay_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FairPlay not reachable')
        self.assertContains(response, '1 FairPlay exam')

    def test_home_stops_calling_after_a_failure(self):
        second = Class.objects.create(organization=self.org, name='FP Section B', slug='fp-b')
        second.members.add(self.outsider.profile)
        cache.set(CLASS_STATUS_CACHE_KEY % second.id, {'students': [{'netid': 'fp_outsider', 'set_up': True}]}, 60)
        self.addCleanup(cache.delete, CLASS_STATUS_CACHE_KEY % second.id)
        self.fp.class_status.side_effect = FairPlayUnreachable()
        response = self.login(self.course_admin).get(reverse('fairplay_home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fp.class_status.call_count, 1)        # never a second call
        self.fp.class_status.assert_called_with(mock.ANY, use_cache=True, read_timeout=QUICK_READ_TIMEOUT)
        self.assertContains(response, 'FairPlay not reachable')     # one class has no cached status

    def test_home_stops_calling_when_fairplay_is_slow(self):
        second = Class.objects.create(organization=self.org, name='FP Section B', slug='fp-b')
        second.members.add(self.outsider.profile)
        cache.set(CLASS_STATUS_CACHE_KEY % second.id, {'students': [{'netid': 'fp_outsider', 'set_up': True}]}, 60)
        self.addCleanup(cache.delete, CLASS_STATUS_CACHE_KEY % second.id)
        clock = iter(range(0, 1000, 3))                               # every call "takes" 3 s
        with mock.patch('judge.views.fairplay.monotonic', side_effect=lambda: next(clock)):
            response = self.login(self.course_admin).get(reverse('fairplay_home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fp.class_status.call_count, 1)        # the 2 s budget is spent
        # the rest come from the cache: fp_student (first class) + fp_outsider (cached second class)
        self.assertContains(response, '2 of 3 set up in FairPlay')
        self.assertNotContains(response, 'FairPlay not reachable')

    def test_exams_tab_lists_fairplay_assignments_only(self):
        now = timezone.now()
        plain = create_contest('fpplain', name='Plain Quiz', organizations=('fporg',), is_organization_private=True,
                               start_time=now, end_time=now + timedelta(hours=1))
        other = create_contest('fpotherorg', name='Other Course Exam', organizations=('otherorg',),
                               is_organization_private=True, fairplay_required=True,
                               start_time=now, end_time=now + timedelta(hours=1))
        by_class = create_contest('fpbyclass', name='Section Quiz', is_organization_private=True,
                                  fairplay_required=True, start_time=now, end_time=now + timedelta(hours=1))
        by_class.classes.add(self.klass)
        self.assertFalse(is_fairplay_exam(plain) or not is_fairplay_exam(other))
        url = reverse('fairplay_course', args=[self.org.id, self.org.slug])
        html = self.login(self.course_admin).get(url).content.decode()
        self.assertIn('CS 101 Midterm', html)
        self.assertIn('Section Quiz', html)
        self.assertNotIn('Plain Quiz', html)
        self.assertNotIn('Other Course Exam', html)
        # the course admin is not an editor of the midterm: no FairPlay button for it
        self.assertNotIn(reverse('contest_fairplay', args=['fpexam']), html)

    def test_course_permissions(self):
        for tab in ('fairplay_course', 'fairplay_course_students', 'fairplay_course_login'):
            url = reverse(tab, args=[self.org.id, self.org.slug])
            for user in (self.student, self.teacher, self.other_teacher):
                self.assertEqual(self.login(user).get(url).status_code, 404, (tab, user.username))
            for user in (self.course_admin, self.class_ta, self.staff):
                self.assertEqual(self.login(user).get(url).status_code, 200, (tab, user.username))

    def test_students_tab(self):
        url = reverse('fairplay_course_students', args=[self.org.id, self.org.slug])
        response = self.login(self.class_ta).get(url)
        html = response.content.decode()
        self.assertIn('FP Section A', html)
        self.assertIn('fp_student', html)
        self.assertIn('fp_classmate', html)
        self.assertIn('avaz', html)
        self.assertIn('not set up', html)
        self.assertIn(reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'fp_student']),
                      html)
        # the tab is never stale, and waits at most QUICK_READ_TIMEOUT
        self.fp.class_status.assert_called_with(self.klass.id, use_cache=False, read_timeout=QUICK_READ_TIMEOUT)
        self.assert_acting(self.class_ta)

    def test_students_tab_unreachable(self):
        self.fp.class_status.side_effect = FairPlayUnreachable()
        url = reverse('fairplay_course_students', args=[self.org.id, self.org.slug])
        response = self.login(self.course_admin).get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FairPlay not reachable')
        self.assertContains(response, 'fp_classmate')  # DKU OJ's own roster still shows

    def test_reset(self):
        url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'fp_student'])
        self.assertEqual(self.login(self.student).post(url).status_code, 404)
        self.assertEqual(self.login(self.other_teacher).post(url).status_code, 404)
        self.fp.reset_student.assert_not_called()
        self.assertEqual(self.login(self.class_ta).get(url).status_code, 405)
        response = self.login(self.class_ta).post(url)
        self.assertRedirects(response, reverse('fairplay_course_students', args=[self.org.id, self.org.slug]),
                             fetch_redirect_response=False)
        self.fp.reset_student.assert_called_once_with('fp_student', self.klass.id)
        self.assert_acting(self.class_ta)

    def test_reset_sends_the_canonical_netid(self):
        url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'FP_Student'])
        self.login(self.class_ta).post(url)
        self.fp.reset_student.assert_called_once_with('fp_student', self.klass.id)
        self.fp.class_status.assert_not_called()                    # on the live roster: no need to ask

    def test_reset_of_another_class_student_refused(self):
        # A student of another class on a shared assignment (via_this_class false) is not ours to reset.
        self.fp.class_status.return_value = {'login': None, 'students': [
            {'netid': 'other_class_kid', 'set_up': True, 'username': 'okid', 'set_up_at': None,
             'via_this_class': False},
            {'netid': 'no_flag_kid', 'set_up': True, 'username': 'nkid', 'set_up_at': None},
            {'netid': 'Former_Member', 'set_up': True, 'username': 'fm', 'set_up_at': None, 'via_this_class': True},
        ]}
        for netid in ('other_class_kid', 'no_flag_kid'):
            url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, netid])
            self.assertEqual(self.login(self.course_admin).post(url).status_code, 404, netid)
        self.fp.reset_student.assert_not_called()
        self.fp.class_status.assert_called_with(self.klass.id, use_cache=False, read_timeout=QUICK_READ_TIMEOUT)
        # a former member who set up through this class's login: reset with FairPlay's spelling
        url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'former_member'])
        self.login(self.course_admin).post(url)
        self.fp.reset_student.assert_called_once_with('Former_Member', self.klass.id)
        # the Students tab lists the former member only
        html = self.login(self.course_admin).get(
            reverse('fairplay_course_students', args=[self.org.id, self.org.slug])).content.decode()
        self.assertIn('Former_Member', html)
        self.assertNotIn('other_class_kid', html)
        self.assertNotIn('no_flag_kid', html)

    def test_reset_unknown_netid_and_errors(self):
        url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'nobody'])
        self.assertEqual(self.login(self.course_admin).post(url).status_code, 404)
        self.fp.reset_student.assert_not_called()
        self.fp.reset_student.side_effect = FairPlayError('That account is busy.', status=409)
        url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'fp_classmate'])
        response = self.login(self.course_admin).post(url, follow=True)
        self.assertContains(response, 'That account is busy.')

    def test_csrf_required(self):
        url = reverse('fairplay_student_reset', args=[self.org.id, self.org.slug, self.klass.id, 'fp_student'])
        self.assertEqual(self.login(self.course_admin, csrf=True).post(url).status_code, 403)
        url = reverse('fairplay_class_login_create', args=[self.org.id, self.org.slug, self.klass.id])
        self.assertEqual(self.login(self.course_admin, csrf=True).post(url).status_code, 403)
        self.fp.reset_student.assert_not_called()
        self.fp.create_class_login.assert_not_called()

    def test_class_login_tab(self):
        url = reverse('fairplay_course_login', args=[self.org.id, self.org.slug])
        response = self.login(self.course_admin).get(url)
        self.assertEqual(response['Cache-Control'], 'no-store')
        html = response.content.decode()
        self.assertIn('fpsecti-m4tz', html)
        self.assertIn('Class login (for first sign-in)', html)
        self.assertIn('Create new', html)
        self.assertIn(reverse('fairplay_class_login_stop', args=[self.org.id, self.org.slug, self.klass.id]), html)
        self.fp.class_status.assert_called_with(self.klass.id, use_cache=False, read_timeout=QUICK_READ_TIMEOUT)
        self.fp.class_status.return_value = {'login': None, 'students': []}
        html = self.login(self.course_admin).get(url).content.decode()
        self.assertIn('No class login.', html)
        self.assertIn('>Create<', html)

    def test_class_login_password_masked_with_show_and_copy(self):
        self.fp.class_status.return_value = dict(LOGIN_STATUS)
        url = reverse('fairplay_course_login', args=[self.org.id, self.org.slug])
        for user in (self.course_admin, self.class_ta, self.staff):
            with self.subTest(user=user.username):
                response = self.login(user).get(url)
                self.assertEqual(response['Cache-Control'], 'no-store')
                html = response.content.decode()
                kid = self.klass.id
                self.assertIn('id="fairplay-login-username-%d">fpsecti-m4tz<' % kid, html)
                # masked on the page; the real value only in the data attribute Show / Copy read
                self.assertIn('data-secret="river-cotton-pine-moss-583" data-mask="••••••••" data-shown="0">••••••••<',
                              html)
                self.assertEqual(html.count('river-cotton-pine-moss-583'), 1)
                self.assertIn('id="fairplay-show-password-%d"' % kid, html)
                self.assertIn('id="fairplay-copy-password-%d"' % kid, html)
                self.assertIn('navigator.clipboard.writeText', html)
                self.assertIn('Students use the class login to set up FairPlay for the first time', html)
                self.assertNotIn('shown only now', html)
                self.assertIn('Used by', html)
                self.assertIn('28 students', html)
        # a teacher of another course, a student, a course instructor who manages no class: no password
        self.assertEqual(self.login(self.other_teacher).get(url).status_code, 404)
        self.assertEqual(self.login(self.student).get(url).status_code, 404)
        instructor = create_user(username='fp_course_instructor')
        self.org.instructors.add(instructor.profile)
        response = self.login(instructor).get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('river-cotton-pine-moss-583', response.content.decode())
        self.assertContains(response, 'You do not manage any class of this course.')

    def test_class_login_history(self):
        self.fp.class_status.return_value = dict(LOGIN_STATUS)
        url = reverse('fairplay_course_login', args=[self.org.id, self.org.slug])
        html = self.login(self.course_admin).get(url).content.decode()
        self.assertIn('id="fairplay-history-%d"' % self.klass.id, html)
        for text in ('fpsecti-old1', 'fpsecti-old2', 'fpsecti-old3', 'fpsecti-old4', 'Replaced by a new one',
                     'Expired', 'Stopped', 'Stopped after many wrong set-up tries', 'in use', '>17<'):
            self.assertIn(text, html)
        # the history never carries a password; an old one is never on the page
        self.assertNotIn('old-password', html)

    def test_class_login_without_password(self):
        status = dict(LOGIN_STATUS, login=dict(LOGIN_STATUS['login'], password=None))
        self.fp.class_status.return_value = status
        url = reverse('fairplay_course_login', args=[self.org.id, self.org.slug])
        html = self.login(self.course_admin).get(url).content.decode()
        self.assertIn('Create a new class login to see the password.', html)
        self.assertNotIn('data-secret=', html)

    def test_password_not_on_other_tabs(self):
        self.fp.class_status.return_value = dict(LOGIN_STATUS)
        for name in ('fairplay_course', 'fairplay_course_students'):
            response = self.login(self.course_admin).get(reverse(name, args=[self.org.id, self.org.slug]))
            self.assertNotIn('river-cotton-pine-moss-583', response.content.decode())
            self.assertContains(response, 'Class login (for first sign-in)')   # the hint links to it
            self.assertContains(response, reverse('fairplay_course_login', args=[self.org.id, self.org.slug]))

    def test_create_class_login_redirects_to_the_tab(self):
        self.fp.create_class_login.return_value = {'username': 'fpsecti-k2p9', 'password': 'river-cotton-58',
                                                   'expires_at': '2026-11-07T09:00:00Z'}
        url = reverse('fairplay_class_login_create', args=[self.org.id, self.org.slug, self.klass.id])
        client = self.login(self.course_admin)
        self.assertEqual(self.login(self.student).post(url).status_code, 404)
        self.assertEqual(self.login(self.other_teacher).post(url).status_code, 404)
        response = client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('fairplay_course_login', args=[self.org.id, self.org.slug]) +
                         '#fairplay-login-%d' % self.klass.id)
        self.fp.create_class_login.assert_called_once_with(self.klass)
        self.assert_acting(self.course_admin)
        # the tab then shows it from FairPlay's fresh status; DKU OJ keeps nothing
        self.fp.class_status.return_value = {
            'login': {'username': 'fpsecti-k2p9', 'password': 'river-cotton-58', 'created_at': '2026-10-24T09:00:00Z',
                      'expires_at': '2026-11-07T09:00:00Z', 'used': 0}, 'history': [], 'students': []}
        html = client.get(response['Location']).content.decode()
        self.assertIn('data-secret="river-cotton-58"', html)
        self.assertIn('New class login for FP Section A', html)
        self.assertNotIn('river-cotton-58', str(dict(client.session)))

    def test_stop_class_login(self):
        url = reverse('fairplay_class_login_stop', args=[self.org.id, self.org.slug, self.klass.id])
        self.assertEqual(self.login(self.student).post(url).status_code, 404)
        response = self.login(self.class_ta).post(url)
        self.assertEqual(response.status_code, 302)
        self.fp.stop_class_login.assert_called_once_with(self.klass)

    def test_create_class_login_error(self):
        self.fp.create_class_login.side_effect = FairPlayUnreachable()
        url = reverse('fairplay_class_login_create', args=[self.org.id, self.org.slug, self.klass.id])
        response = self.login(self.course_admin).post(url, follow=True)
        self.assertContains(response, 'FairPlay not reachable')


@FAIRPLAY_ON
class AccessCodesTestCase(PagesMixin, TestCase):
    BUTTON = 'Download access codes (PDF)'

    def participation(self, user):
        return self.login(user).get(reverse('contest_participation_own', args=['fpexam']))

    def download(self, user):
        return self.login(user).get(reverse('contest_access_codes_pdf', args=['fpexam']))

    def test_button_for_editors_of_a_fairplay_exam(self):
        url = reverse('contest_access_codes_pdf', args=['fpexam'])
        html = self.participation(self.teacher).content.decode()
        self.assertIn(self.BUTTON, html)
        self.assertIn(url, html)
        self.assertNotIn(self.BUTTON, self.participation(self.student).content.decode())
        set_fairplay(self.exam, False)
        self.assertNotIn(self.BUTTON, self.participation(self.teacher).content.decode())

    def test_no_button_when_bridge_is_off(self):
        with FAIRPLAY_OFF:
            self.assertNotIn(self.BUTTON, self.participation(self.teacher).content.decode())

    def test_download(self):
        self.fp.access_codes_pdf.return_value = b'%PDF-1.4 codes'
        response = self.download(self.teacher)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="access-codes-fpexam.pdf"')
        self.assertEqual(response.content, b'%PDF-1.4 codes')
        self.fp.access_codes_pdf.assert_called_once_with('fpexam', 'fp_teacher')
        self.assert_acting(self.teacher)

    def test_not_ready_shows_one_short_line(self):
        for status in (404, 409):
            with self.subTest(status=status):
                self.fp.access_codes_pdf.side_effect = FairPlayError('FairPlay says: stack trace here', status=status)
                response = self.download(self.teacher)
                self.assertRedirects(response, reverse('contest_participation_own', args=['fpexam']),
                                     fetch_redirect_response=False)
                texts = [str(m) for m in get_messages(response.wsgi_request)]
                self.assertEqual(texts, ['The access codes are not ready yet.'])

    def test_unreachable_shows_one_short_line(self):
        self.fp.access_codes_pdf.side_effect = FairPlayUnreachable()
        response = self.download(self.teacher)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                         ['The access codes could not be downloaded.'])

    def test_permissions(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher, self.outsider):
            self.assertEqual(self.download(user).status_code, 404, user.username)
        self.assertEqual(Client().get(reverse('contest_access_codes_pdf', args=['fpexam'])).status_code, 302)
        set_fairplay(self.exam, False)
        self.assertEqual(self.download(self.teacher).status_code, 404)
        self.fp.access_codes_pdf.assert_not_called()


@FAIRPLAY_ON
class ContestTabTestCase(PagesMixin, TestCase):
    def tab(self, user):
        return self.login(user).get(reverse('contest_fairplay', args=['fpexam']))

    def test_no_tab_in_contest_tabs(self):
        html = self.login(self.teacher).get(reverse('contest_view', args=['fpexam'])).content.decode()
        self.assertNotIn(reverse('contest_fairplay', args=['fpexam']), html)
        html = self.login(self.tester).get(reverse('contest_view', args=['fpexam'])).content.decode()
        self.assertNotIn(reverse('contest_fairplay', args=['fpexam']), html)

    def test_permissions(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher, self.outsider):
            self.assertEqual(self.tab(user).status_code, 404, user.username)
        self.assertEqual(self.tab(self.teacher).status_code, 200)
        self.assertEqual(self.tab(self.staff).status_code, 200)
        self.assertEqual(Client().get(reverse('contest_fairplay', args=['fpexam'])).status_code, 302)
        set_fairplay(self.exam, False)
        self.assertEqual(self.tab(self.teacher).status_code, 404)

    def test_bridge_off(self):
        with FAIRPLAY_OFF:
            self.assertEqual(self.tab(self.teacher).status_code, 404)

    def test_before_open(self):
        response = self.tab(self.teacher)
        html = response.content.decode()
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('id="fairplay-open-btn"', html)
        self.assertIn('docs.python.org', html)
        self.assertIn('1 student has not set up FairPlay yet', html)
        self.assertIn('value="fp_student"', html)
        self.assertIn('value="fp_classmate"', html)
        self.fp.exam.assert_called_once_with('fpexam', read_timeout=QUICK_READ_TIMEOUT)  # a page load: short wait
        self.assert_acting(self.teacher)

    def test_not_synced(self):
        self.fp.exam.return_value = {'synced': False}
        self.assertContains(self.tab(self.teacher), 'FairPlay has not received this assignment yet')

    def test_live(self):
        self.fp.exam.return_value = exam(
            status='live', start_code='219074', invigilator_code='583204', closes_at='2026-10-24T11:00:00Z',
            counts=dict(EXAM['counts'], writing=1, waiting=1),
            students=[
                {'netid': 'fp_student', 'name': 'Avazbek Khamidov', 'set_up': True, 'status': 'writing', 'answered': 2,
                 'questions': 5, 'seconds_left': 2460, 'warnings': ['Left the app once (8 s)']},
                {'netid': 'fp_classmate', 'name': '<b>Eve</b>', 'set_up': True, 'status': 'waiting', 'answered': 0,
                 'questions': 5, 'seconds_left': 3000, 'warnings': []},
            ])
        html = self.tab(self.teacher).content.decode()
        self.assertIn('219074', html)
        self.assertIn('<span id="fairplay-invigilator-code" hidden>583204</span>', html)
        self.assertIn('Add 10 min', html)
        self.assertIn('id="fairplay-close-btn"', html)
        self.assertIn('Left the app once (8 s)', html)
        self.assertIn('41 min left', html)
        self.assertIn('Approve', html)
        self.assertIn('&lt;b&gt;Eve&lt;/b&gt;', html)
        self.assertNotIn('<b>Eve</b>', html)
        self.assertIn(reverse('contest_fairplay_status', args=['fpexam']), html)

    def test_closed_reports(self):
        self.fp.exam.return_value = exam(status='closed', students=[
            {'netid': 'fp_student', 'name': 'Avazbek Khamidov', 'set_up': True, 'status': 'handed_in', 'answered': 5,
             'questions': 5, 'seconds_left': None, 'warnings': []}])
        html = self.tab(self.teacher).content.decode()
        self.assertIn(reverse('contest_fairplay_report', args=['fpexam', 'fp_student']), html)

    def test_unreachable(self):
        self.fp.exam.side_effect = FairPlayUnreachable()
        response = self.tab(self.teacher)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'not reachable')
        self.assertContains(response, reverse('contest_view', args=['fpexam']))  # the tabs still render

    def test_status_json(self):
        url = reverse('contest_fairplay_status', args=['fpexam'])
        self.assertEqual(self.login(self.student).get(url).status_code, 404)
        self.assertEqual(self.login(self.tester).get(url).status_code, 404)
        response = self.login(self.teacher).get(url)
        self.assertEqual(response.json(), {'ok': True, 'exam': exam()})
        self.fp.exam.assert_called_with('fpexam', read_timeout=QUICK_READ_TIMEOUT)   # never holds a worker long
        self.fp.exam.side_effect = FairPlayUnreachable()
        self.assertEqual(self.login(self.teacher).get(url).json(), {'ok': False, 'detail': 'FairPlay not reachable'})

    def test_live_table_polls_one_request_at_a_time(self):
        self.fp.exam.return_value = exam(status='live', start_code='123456', invigilator_code='654321')
        html = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
        self.assertNotIn('setInterval', html)
        self.assertIn('setTimeout(poll', html)
        self.assertIn('document.hidden', html)

    def action(self, user, name, data=None):
        return self.login(user).post(reverse('contest_fairplay_action', args=['fpexam', name]), data or {})

    def test_actions(self):
        cases = (
            ('open', {'open_to': 'all'}, 'open_exam', ('fpexam', 'all')),
            ('open', {'open_to': 'picked', 'netids': ['FP_Student']}, 'open_exam', ('fpexam', ['fp_student'])),
            ('close', {}, 'close_exam', ('fpexam',)),
            ('add-time', {'minutes': '10'}, 'add_time', ('fpexam', 10)),
            ('new-code', {}, 'new_code', ('fpexam',)),
            ('approve', {'netid': 'fp_classmate'}, 'approve', ('fpexam', 'fp_classmate')),
        )
        for name, data, method, args in cases:
            with self.subTest(action=name, data=data):
                getattr(self.fp, method).reset_mock()
                response = self.action(self.teacher, name, data)
                self.assertRedirects(response, reverse('contest_fairplay', args=['fpexam']),
                                     fetch_redirect_response=False)
                getattr(self.fp, method).assert_called_once_with(*args)
                self.assert_acting(self.teacher)

    def test_action_permissions_and_validation(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher):
            self.assertEqual(self.action(user, 'close').status_code, 404)
        self.fp.close_exam.assert_not_called()
        self.assertEqual(self.action(self.teacher, 'explode').status_code, 404)
        self.assertEqual(self.login(self.teacher).get(
            reverse('contest_fairplay_action', args=['fpexam', 'close'])).status_code, 405)
        self.action(self.teacher, 'open', {'open_to': 'picked', 'netids': ['fp_outsider']})
        self.action(self.teacher, 'open', {'open_to': 'picked'})
        self.fp.open_exam.assert_not_called()
        self.action(self.teacher, 'add-time', {'minutes': '500'})
        self.fp.add_time.assert_not_called()
        self.action(self.teacher, 'approve', {'netid': 'fp_outsider'})
        self.fp.approve.assert_not_called()
        self.assertEqual(self.login(self.teacher, csrf=True).post(
            reverse('contest_fairplay_action', args=['fpexam', 'close'])).status_code, 403)
        self.fp.close_exam.assert_not_called()

    def test_action_error_detail_shown(self):
        self.fp.close_exam.side_effect = FairPlayError('The exam is not live.', status=409)
        response = self.login(self.teacher).post(reverse('contest_fairplay_action', args=['fpexam', 'close']),
                                                 follow=True)
        self.assertContains(response, 'The exam is not live.')

    def test_report(self):
        self.fp.student_report.return_value = {
            'name': 'Avazbek Khamidov', 'netid': 'fp_student', 'status': 'handed_in',
            'started_at': '2026-10-24T09:01:00Z', 'handed_in_at': '2026-10-24T10:20:00Z',
            'events': [{'at': '2026-10-24T09:30:00Z', 'kind': 'focus_lost', 'detail': '<script>alert(1)</script>'}]}
        url = reverse('contest_fairplay_report', args=['fpexam', 'fp_student'])
        self.assertEqual(self.login(self.student).get(url).status_code, 404)
        response = self.login(self.teacher).get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('focus_lost', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.fp.student_report.assert_called_once_with('fpexam', 'fp_student')
        self.fp.student_report.side_effect = FairPlayError('No such student.', status=404)
        self.assertEqual(self.login(self.teacher).get(url).status_code, 404)
        self.fp.student_report.side_effect = FairPlayUnreachable()
        self.assertContains(self.login(self.teacher).get(url), 'FairPlay not reachable')

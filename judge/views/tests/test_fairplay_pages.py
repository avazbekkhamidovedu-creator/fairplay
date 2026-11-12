"""FairPlay bridge: signing, the roster endpoint, the client and the access codes download."""
import hashlib
import hmac
import json
import os
from unittest import mock

import requests
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from judge.models import Class
from judge.models.tests.util import create_organization, create_user
from judge.utils.fairplay_client import CLASS_STATUS_CACHE_KEY, FairPlayClient, FairPlayError, FairPlayUnreachable, \
    QUICK_READ_TIMEOUT, cached_class_status
from judge.utils.fairplay_modes import mode_table_from_exam
from judge.utils.fairplay_signing import DIRECTION_FP_TO_OJ, DIRECTION_OJ_TO_FP, body_sha256, canonical_string, \
    direction_key, sign, signed_headers
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, TOKEN, create_contest, \
    fp_signed, grant, installation_path, set_fairplay

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
        headers = signed_headers(case['method'], installation_path(case['path']), case['body'].encode(),
                                 timestamp=case['timestamp'], nonce=case['nonce'], token=TOKEN,
                                 direction=DIRECTION_FP_TO_OJ, acting_user=case.get('acting_user'))
        headers.update(header_overrides)
        with mock.patch('judge.utils.fairplay_signing.time.time', return_value=clock or case['timestamp']):
            if case['method'] == 'GET':
                return self.client.get(installation_path(path or case['path']), **meta(headers))
            return self.client.post(installation_path(path or case['path']), (body if body is not None else case['body']).encode(),
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
        self.assertEqual(args, ('POST', installation_path('https://exam.example.edu/api/integrations/dkuoj/v1/exams/fpexam/open')))
        self.assertEqual(kwargs['timeout'], (5, 15))
        body = kwargs['data']
        self.assertEqual(json.loads(body), {'open_to': 'all'})
        headers = kwargs['headers']
        self.assertEqual(headers['X-FP-Acting-User'], 'fp_teacher')
        canonical = canonical_string('POST', installation_path('/api/integrations/dkuoj/v1/exams/fpexam/open'),
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
        path = installation_path('/api/integrations/dkuoj/v1/exams/fpexam/access-codes.pdf')
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

    @override_settings(FAIRPLAY_CLASS_LOGIN_ENABLED=True)
    def test_read_timeouts(self):
        client = FairPlayClient(acting_user='fp_teacher')
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'ok': True})) as call:
            client.exam('fpexam', read_timeout=QUICK_READ_TIMEOUT)
            self.assertEqual(call.call_args[1]['timeout'], (3, 3))
            FairPlayClient(acting_user='fp_course_admin').class_status(
                self.klass.id, use_cache=False, read_timeout=QUICK_READ_TIMEOUT)
            self.assertEqual(call.call_args[1]['timeout'], (3, 3))
            client.close_exam('fpexam')                       # exam actions keep the long timeout
            self.assertEqual(call.call_args[1]['timeout'], (5, 15))
        self.assertIsNone(cached_class_status(999999))

    def test_class_login_calls_are_off_by_default(self):
        client = FairPlayClient(acting_user='fp_course_admin')
        with mock.patch('judge.utils.fairplay_client.requests.request') as call:
            for fn in (lambda: client.class_status(self.klass.id, use_cache=False),
                       lambda: client.create_class_login(self.klass),
                       lambda: client.stop_class_login(self.klass),
                       lambda: client.reset_student('fp_student', self.klass.id)):
                with self.assertRaises(FairPlayError) as cm:
                    fn()
                self.assertEqual(cm.exception.status, 404)
            self.assertEqual(call.call_count, 0)

    @override_settings(FAIRPLAY_CLASS_LOGIN_ENABLED=True)
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
                             installation_path('https://exam.example.edu/api/integrations/dkuoj/v1/students/fp_student/reset'))
            self.assertEqual(json.loads(call.call_args[1]['data']), {'class_id': self.klass.id})
            client.class_status(self.klass.id)
            self.assertEqual(call.call_count, 3)

    @override_settings(FAIRPLAY_CLASS_LOGIN_ENABLED=True)
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

    @override_settings(FAIRPLAY_CLASS_LOGIN_ENABLED=True)
    def test_class_calls_need_a_current_class_teacher(self):
        status = {'login': None, 'students': []}
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, status)) as call:
            for who in ('fp_teacher', 'fp_student', 'nobody', ''):
                client = FairPlayClient(acting_user=who)
                for fn in (lambda: client.class_status(self.klass.id, use_cache=False),
                           lambda: client.create_class_login(self.klass),
                           lambda: client.stop_class_login(self.klass),
                           lambda: client.reset_student('fp_student', self.klass.id)):
                    with self.assertRaises(FairPlayError) as cm:
                        fn()
                    self.assertEqual(cm.exception.status, 403)
            self.assertEqual(call.call_count, 0)
            self.org.admins.clear()
            with self.assertRaises(FairPlayError):
                FairPlayClient(acting_user='fp_course_admin').class_status(self.klass.id, use_cache=False)
            self.assertEqual(call.call_count, 0)

    @override_settings(FAIRPLAY_CLASS_LOGIN_ENABLED=True)
    def test_paths(self):
        client = FairPlayClient(acting_user='fp_course_admin')
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'ok': True})) as call:
            client.create_class_login(self.klass)
            self.assertEqual(call.call_args[0], (
                'POST', installation_path('https://exam.example.edu/api/integrations/dkuoj/v1/classes/%d/login' % self.klass.id)))
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
                self.assertEqual(call.call_args[0][1], installation_path('https://exam.example.edu/api/integrations/dkuoj/v1' + path))


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
        cache.clear()
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
            self.assertNotIn('/fairplay/', html, user.username)
        self.fp.class_status.assert_not_called()


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
        self.assertIn('class="fp-tools"', html)
        self.assertNotIn('FairPlay student help', html)
        student_html = self.participation(self.student).content.decode()
        self.assertNotIn(self.BUTTON, student_html)
        self.assertNotIn('class="fp-tools"', student_html)
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


MODE_STUDENTS = [
    {'netid': 'fp_student', 'status': 'writing', 'fp_mode': 'on', 'device_state': 'bound',
     'second_device_attempts': 2, 'last_beat_at': '2026-11-01T10:00:00Z'},
    {'netid': 'fp_classmate', 'status': 'not_started', 'fp_mode': 'not_started', 'device_state': 'unused',
     'second_device_attempts': 0, 'last_beat_at': None},
    {'netid': 'old_one', 'status': 'handed_in'},
    {'netid': 'odd', 'fp_mode': 'weird', 'device_state': 5, 'second_device_attempts': 'x'},
]


@FAIRPLAY_ON
class ModeTableTestCase(PagesMixin, TestCase):
    def test_cells_from_batch_response(self):
        table = mode_table_from_exam(exam(settings={'strong_lock': True}, students=MODE_STUDENTS))
        cell = table['students']['fp_student']
        self.assertEqual((cell['mode'], cell['device'], cell['second_device']), ('on', 'bound', 2))
        self.assertEqual(table['students']['fp_classmate']['device'], 'unused')
        for netid in ('old_one', 'odd'):
            cell = table['students'][netid]
            self.assertEqual((cell['mode'], cell['device'], cell['second_device'], cell['last_beat_at']),
                             (None, None, 0, None))
        self.assertIs(table['strong_lock'], True)
        self.assertEqual(table['counts']['on'], 1)
        self.assertEqual((table['second_device_total'], table['second_device_students']), (2, 1))

    def test_summary_row_for_editors_only(self):
        self.fp.exam.return_value = exam(settings={'strong_lock': False}, students=MODE_STUDENTS)
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertIn('FairPlay exam: yes', html)
        self.assertIn('second device x2 (1 students)', html)
        html = self.login(self.student).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertNotIn('fairplay-exam-row', html)

    def test_page_survives_old_fairplay_and_outage(self):
        self.fp.exam.return_value = exam(students=[{'netid': 'fp_student', 'status': 'writing'}])
        response = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam']))
        self.assertEqual(response.status_code, 200)
        self.fp.exam.side_effect = FairPlayUnreachable()
        cache.clear()
        response = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam']))
        self.assertEqual(response.status_code, 200)
        self.assertIn('mode per student: -', response.content.decode())

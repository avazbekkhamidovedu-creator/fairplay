"""FairPlay bridge API: /api/fairplay/v1/ (docs/fairplay-bridge.md)."""
import hashlib
import json
import threading
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError, OperationalError, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestParticipation, ContestProblem, Problem, Submission, SubmissionFile, \
    SubmissionManualGrade
from judge.models.stored_file import StoredFileRecord
from judge.models.tests.util import create_user
from judge.storage import StorageUnavailableError
from judge.utils.fairplay_delivery import _KEY_HAND_IN, _attempt_key, active_hand_in, clear_hand_in, \
    delivery_warnings, parse_time, record_added_minutes, report_url_allowed
from judge.utils.fairplay_package import iso
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, SECRETS, TOKEN, \
    create_contest, fp_signed, set_fairplay


def qid(problem):
    return 'p%06d' % problem.id


def record(attempt_id):
    """The hand-in record DKU OJ keeps (in the cache) for a FairPlay attempt, or None."""
    return cache.get(_attempt_key(attempt_id))


def records():
    return [r for r in cache.get_many([_attempt_key(a) for a in KNOWN_ATTEMPTS]).values()]


KNOWN_ATTEMPTS = ['fp_att_1', 'fp_att_2', 'a1', 'a2', 'att_1', 'att_2', 'att_3', 'f'] + ['url%d' % i for i in range(6)]


def staged_uploads():
    return StoredFileRecord.objects.filter(path__startswith='fairplay_uploads/').exclude(
        status=StoredFileRecord.STATUS_DELETED)


class FairPlayAPIMixin(FairPlayDataMixin):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def api(self, method, name, *args, token=TOKEN, data=None, headers=None, query=None, content_type=None):
        url = reverse(name, args=args)
        if query:
            url += '?' + urlencode(query)
        if isinstance(data, (dict, list)):
            data = json.dumps(data)
            content_type = content_type or 'application/json'
        if isinstance(data, str):
            data = data.encode('utf-8')
        extra = fp_signed(method.upper(), url, data or b'', token=token) if token is not None else {}
        for key, value in (headers or {}).items():
            extra['HTTP_' + key.upper().replace('-', '_')] = value
        if method == 'get':
            return self.client.get(url, **extra)
        return self.client.post(url, data or b'', content_type=content_type or 'application/octet-stream', **extra)

    def answers_body(self, attempt='fp_att_1', username='fp_student', **overrides):
        now = timezone.now()
        body = {
            'username': username, 'attempt_id': attempt, 'package_version': 'abc',
            'started_at': iso(now - timedelta(minutes=50)), 'deadline_at': iso(now + timedelta(minutes=40)),
            'handed_in_at': iso(now - timedelta(minutes=1)), 'handed_in_evidence': 'server',
            'answers': [
                {'question_id': qid(self.mcq), 'choice': {'selected': [self.right.id]}},
                {'question_id': qid(self.blank), 'fill_blank': {'answers': ['hash table', '1']}},
                {'question_id': qid(self.short), 'short_answer': {'text': 'Hashing maps keys.'}},
                {'question_id': qid(self.code), 'code': {'language': 'PY3', 'source': 'print(1)\n'}},
            ],
            'integrity_report_url': 'https://exam.example.edu/teacher/exams/1/attempts/2',
        }
        body.update(overrides)
        return body


@FAIRPLAY_ON
class FairPlayAuthTestCase(FairPlayAPIMixin, TestCase):
    def test_bridge_off_is_invisible(self):
        with FAIRPLAY_OFF:
            for name, args in (('fairplay_api_exams', ()), ('fairplay_api_package', ('fpexam',)),
                               ('fairplay_api_results', ('fpexam',))):
                self.assertEqual(self.api('get', name, *args).status_code, 404)
            self.assertEqual(self.api('post', 'fairplay_api_submissions', 'fpexam',
                                      data=self.answers_body()).status_code, 404)

    def test_missing_or_wrong_signature(self):
        self.assertEqual(self.api('get', 'fairplay_api_exams', token=None).status_code, 401)
        self.assertEqual(self.api('get', 'fairplay_api_exams', token='x' * 48).status_code, 401)
        self.assertEqual(self.api('get', 'fairplay_api_exams', token=TOKEN + 'x').status_code, 401)
        self.assertEqual(self.api('get', 'fairplay_api_exams').status_code, 200)
        # A bare bearer token is refused.
        self.assertEqual(self.client.get(reverse('fairplay_api_exams'),
                                         HTTP_AUTHORIZATION='Bearer %s' % TOKEN).status_code, 401)

    def test_signature_compared_in_constant_time(self):
        with mock.patch('judge.utils.fairplay_signing.hmac.compare_digest', return_value=False) as compare:
            self.assertEqual(self.api('get', 'fairplay_api_exams').status_code, 401)
        compare.assert_called_once()

    def test_site_api_token_users_are_not_logged_in(self):
        # The bridge never authenticates a site user, whatever the header.
        response = self.api('get', 'fairplay_api_exams')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('sessionid', response.cookies)


@FAIRPLAY_ON
class FairPlayExamsTestCase(FairPlayAPIMixin, TestCase):
    def test_lists_fairplay_exams_only(self):
        Contest.objects.create(key='plainexam', name='Plain', start_time=timezone.now(),
                               end_time=timezone.now() + timedelta(hours=1))
        data = self.api('get', 'fairplay_api_exams').json()
        self.assertEqual([e['key'] for e in data['exams']], ['fpexam'])
        self.assertIsNone(data['next_page'])
        self.assertTrue(data['server_time'].endswith('Z'))
        exam = data['exams'][0]
        self.assertEqual(exam['package_version'], self.api('get', 'fairplay_api_package', 'fpexam').json()[
            'package_version'])
        self.assertEqual([r['username'] for r in exam['roster']], ['fp_classmate', 'fp_student'])
        self.assertTrue(exam['updated_at'].endswith('Z'))
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(data))

    def test_changed_since_and_active_keys(self):
        first = self.api('get', 'fairplay_api_exams').json()
        self.assertEqual(first['active_keys'], ['fpexam'])
        self.assertTrue(first['active_since'].endswith('Z'))
        self.assertNotIn('settings', first['exams'][0])
        since = first['server_time']
        self.assertEqual(self.api('get', 'fairplay_api_exams', query={'changed_since': since}).json()['exams'], [])
        # A change to the package is reported.
        Problem.objects.filter(pk=self.mcq.pk).update(description='New wording')
        changed = self.api('get', 'fairplay_api_exams', query={'changed_since': since}).json()
        self.assertEqual([e['key'] for e in changed['exams']], ['fpexam'])
        self.assertNotEqual(changed['exams'][0]['package_version'], first['exams'][0]['package_version'])
        since = changed['server_time']
        # Switched off: no longer among the active keys (FairPlay marks it removed), not listed.
        set_fairplay(self.exam, False)
        removed = self.api('get', 'fairplay_api_exams', query={'changed_since': since}).json()
        self.assertEqual((removed['exams'], removed['active_keys']), ([], []))
        self.assertEqual(self.api('get', 'fairplay_api_exams').json()['exams'], [])
        # Later pages carry no active keys.
        self.assertNotIn('active_keys', self.api('get', 'fairplay_api_exams', query={'page': 2}).json())

    def test_lost_tracking_lists_again(self):
        # Lost change tracking lists the entries again; none is missed.
        since = self.api('get', 'fairplay_api_exams').json()['server_time']
        cache.clear()
        again = self.api('get', 'fairplay_api_exams', query={'changed_since': since}).json()
        self.assertEqual([e['key'] for e in again['exams']], ['fpexam'])

    def test_ended_long_ago_not_tracked(self):
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(days=40),
                                                       end_time=now - timedelta(days=31))
        data = self.api('get', 'fairplay_api_exams').json()
        self.assertEqual((data['exams'], data['active_keys']), ([], []))

    def test_roster_change_is_reported(self):
        since = self.api('get', 'fairplay_api_exams').json()['server_time']
        self.klass.members.add(self.outsider.profile)
        data = self.api('get', 'fairplay_api_exams', query={'changed_since': since}).json()
        self.assertIn('fp_outsider', [r['username'] for r in data['exams'][0]['roster']])

    def test_paging(self):
        now = timezone.now()
        for i in range(101):
            create_contest('fppage%03d' % i, name='P', start_time=now, end_time=now + timedelta(hours=1),
                           is_organization_private=True, is_visible=True, fairplay_required=True)
        page1 = self.api('get', 'fairplay_api_exams').json()
        self.assertEqual(len(page1['exams']), 100)
        self.assertEqual(page1['next_page'], 2)
        page2 = self.api('get', 'fairplay_api_exams', query={'page': 2}).json()
        self.assertEqual(len(page2['exams']), 2)
        self.assertIsNone(page2['next_page'])
        keys = [e['key'] for e in page1['exams'] + page2['exams']]
        self.assertEqual(len(set(keys)), 102)

    def test_later_pages_do_not_move_rows(self):
        # A change between page 1 and page 2 must not shift the offset (rows lost from the pass).
        now = timezone.now()
        for i in range(101):
            create_contest('fpmove%03d' % i, name='P', start_time=now, end_time=now + timedelta(hours=1),
                           is_organization_private=True, is_visible=True, fairplay_required=True)
        page1 = self.api('get', 'fairplay_api_exams').json()
        Contest.objects.filter(key=page1['exams'][0]['key']).update(name='Renamed')
        page2 = self.api('get', 'fairplay_api_exams', query={'page': 2}).json()
        keys = [e['key'] for e in page1['exams'] + page2['exams']]
        self.assertEqual(len(set(keys)), 102)
        # the change is reported by the next pass
        later = self.api('get', 'fairplay_api_exams', query={'changed_since': page1['server_time']}).json()
        self.assertEqual([e['name'] for e in later['exams']], ['Renamed'])

    def test_bad_query(self):
        self.assertEqual(self.api('get', 'fairplay_api_exams', query={'changed_since': 'yesterday'}).status_code, 400)
        self.assertEqual(self.api('get', 'fairplay_api_exams', query={'page': '0'}).status_code, 400)


@FAIRPLAY_ON
class FairPlayPackageEndpointTestCase(FairPlayAPIMixin, TestCase):
    def test_package_and_etag(self):
        response = self.api('get', 'fairplay_api_package', 'fpexam')
        self.assertEqual(response.status_code, 200)
        package = response.json()
        self.assertEqual(response['ETag'], '"%s"' % package['package_version'])
        for secret in SECRETS:
            self.assertNotIn(secret, response.content.decode())
        self.assertNotIn('is_correct', response.content.decode())
        again = self.api('get', 'fairplay_api_package', 'fpexam', headers={'If-None-Match': response['ETag']})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again['ETag'], response['ETag'])
        stale = self.api('get', 'fairplay_api_package', 'fpexam', headers={'If-None-Match': '"0000"'})
        self.assertEqual(stale.status_code, 200)

    def test_unknown_or_not_fairplay(self):
        self.assertEqual(self.api('get', 'fairplay_api_package', 'nosuch').status_code, 404)
        set_fairplay(self.exam, False)
        self.assertEqual(self.api('get', 'fairplay_api_package', 'fpexam').status_code, 404)

    def test_too_early(self):
        start = timezone.now() + timedelta(hours=30)
        Contest.objects.filter(pk=self.exam.pk).update(start_time=start, end_time=start + timedelta(hours=2))
        response = self.api('get', 'fairplay_api_package', 'fpexam')
        self.assertEqual(response.status_code, 425)
        self.assertTrue(5 * 3600 <= int(response['Retry-After']) <= 6 * 3600 + 1)
        Contest.objects.filter(pk=self.exam.pk).update(start_time=timezone.now() + timedelta(hours=23))
        self.assertEqual(self.api('get', 'fairplay_api_package', 'fpexam').status_code, 200)

    def test_assets(self):
        from django.core.files.base import ContentFile
        from judge.models import EditorAttachment
        attachment = EditorAttachment(original_name='cell.png', size=5)
        attachment.file.save('cell.png', ContentFile(b'abcde'), save=False)
        attachment.save()
        Problem.objects.filter(pk=self.mcq.pk).update(description='![x](/widgets/files/%s/)' % attachment.uuid)
        package = self.api('get', 'fairplay_api_package', 'fpexam').json()
        name = package['assets'][0]['name']
        response = self.api('get', 'fairplay_api_asset', 'fpexam', name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'abcde')
        self.assertEqual(response['ETag'], '"%s"' % hashlib.sha256(b'abcde').hexdigest())
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(self.api('get', 'fairplay_api_asset', 'fpexam', 'other.png').status_code, 404)


@FAIRPLAY_ON
class FairPlayUploadTestCase(FairPlayAPIMixin, TestCase):
    def upload(self, data=b'%PDF-1.4 report', username='fp_student', filename='report.pdf', sha=None,
               question=None):
        return self.api('post', 'fairplay_api_uploads', 'fpexam', data=data, query={
            'username': username, 'question_id': question or qid(self.file), 'filename': filename,
        }, headers={'X-Sha256': sha or hashlib.sha256(data).hexdigest()})

    def test_upload_fields_are_signed_query_values(self):
        # The signature covers the student, question and file name in the query string.
        data = b'%PDF-1.4 report'
        url = reverse('fairplay_api_uploads', args=['fpexam'])
        signed_url = url + '?' + urlencode({'username': 'fp_student', 'question_id': qid(self.file),
                                            'filename': 'report.pdf'})
        extra = fp_signed('POST', signed_url, data)
        extra['HTTP_X_SHA256'] = hashlib.sha256(data).hexdigest()
        tampered = signed_url.replace('fp_student', 'fp_other')
        tampered_extra = dict(extra, QUERY_STRING=extra['QUERY_STRING'].replace('username=fp_student', 'username=fp_other'))
        self.assertNotEqual(tampered_extra['QUERY_STRING'], extra['QUERY_STRING'])
        response = self.client.post(tampered, data, content_type='application/octet-stream', **tampered_extra)
        self.assertEqual(response.wsgi_request.GET['username'], 'fp_other')
        self.assertEqual(response.status_code, 401)
        headers_only = fp_signed('POST', url, data)
        headers_only.update({'HTTP_X_SHA256': hashlib.sha256(data).hexdigest(), 'HTTP_X_USERNAME': 'fp_student',
                             'HTTP_X_QUESTION_ID': qid(self.file), 'HTTP_X_FILENAME': 'report.pdf'})
        response = self.client.post(url, data, content_type='application/octet-stream', **headers_only)
        self.assertEqual(response.status_code, 403)       # no username in the query: not on the roster
        response = self.client.post(signed_url, data, content_type='application/octet-stream', **extra)
        self.assertEqual(response.status_code, 200, response.content)

    def test_upload_and_claim(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        upload_id = response.json()['upload_id']
        self.assertTrue(upload_id.startswith('up_'))
        body = self.answers_body(answers=[{'question_id': qid(self.file), 'file_upload': {'upload_ids': [upload_id]}}])
        with self.captureOnCommitCallbacks(execute=True):
            result = self.api('post', 'fairplay_api_submissions', 'fpexam', data=body)
        self.assertEqual(result.status_code, 200, result.content)
        submission = Submission.objects.get(pk=result.json()['submissions'][0]['submission_id'])
        stored = SubmissionFile.objects.get(submission=submission)
        self.assertEqual(stored.original_name, 'report.pdf')
        with stored.file.open('rb') as handle:
            self.assertEqual(handle.read(), b'%PDF-1.4 report')
        # The staged copy is gone once the hand-in is filed.
        self.assertFalse(staged_uploads().exists())
        # An upload is used once (also by another attempt, once the professor cleared the first).
        clear_hand_in(self.exam, self.student.profile)
        again = self.answers_body(attempt='fp_att_2',
                                  answers=[{'question_id': qid(self.file), 'file_upload': {'upload_ids': [upload_id]}}])
        self.assertEqual(self.api('post', 'fairplay_api_submissions', 'fpexam', data=again).status_code, 422)

    def test_checks(self):
        self.assertEqual(self.upload(filename='report.exe').status_code, 422)
        self.assertEqual(self.upload(data=b'x' * 2000).status_code, 422)
        self.assertEqual(self.upload(sha='0' * 64).status_code, 422)
        self.assertEqual(self.upload(question=qid(self.mcq)).status_code, 422)
        self.assertEqual(self.upload(username='fp_outsider').status_code, 403)
        self.assertEqual(self.upload(username='nobody').status_code, 403)
        self.assertFalse(staged_uploads().exists())

    def test_staged_in_the_submission_store(self):
        upload_id = self.upload().json()['upload_id']
        path = staged_uploads().get().path
        self.assertEqual(path, 'fairplay_uploads/%d/%d/%d/%s.pdf' % (self.exam.id, self.student.profile.id,
                                                                     self.file.id, upload_id[3:]))
        self.assertTrue(self.storage.exists(path))

    def test_upload_bound_to_student_and_question(self):
        upload_id = self.upload().json()['upload_id']
        for username, answer in (('fp_classmate', {'question_id': qid(self.file),
                                                   'file_upload': {'upload_ids': [upload_id]}}),
                                 ('fp_student', {'question_id': qid(self.file),
                                                 'file_upload': {'upload_ids': ['up_' + '0' * 24]}}),
                                 ('fp_student', {'question_id': qid(self.file),
                                                 'file_upload': {'upload_ids': ['../../etc/passwd']}})):
            with self.subTest(username=username):
                body = self.answers_body(attempt='x-' + username + str(len(answer)), username=username,
                                         answers=[answer])
                self.assertEqual(self.api('post', 'fairplay_api_submissions', 'fpexam', data=body).status_code, 422)

    def test_original_name_survives_a_lost_cache(self):
        upload_id = self.upload(filename='My Report.pdf').json()['upload_id']
        cache.clear()
        body = self.answers_body(answers=[{'question_id': qid(self.file), 'file_upload': {'upload_ids': [upload_id]}}])
        result = self.api('post', 'fairplay_api_submissions', 'fpexam', data=body)
        self.assertEqual(result.status_code, 200, result.content)
        stored = SubmissionFile.objects.get(submission_id=result.json()['submissions'][0]['submission_id'])
        self.assertEqual(stored.original_name, 'answer.pdf')

    def test_unclaimed_uploads_expire(self):
        self.upload()
        record_row = staged_uploads().get()
        # kept while FairPlay may still retry the hand-in that claims it (14 days)
        StoredFileRecord.objects.filter(pk=record_row.pk).update(created_on=timezone.now() - timedelta(days=14))
        self.upload()
        self.assertTrue(staged_uploads().filter(pk=record_row.pk).exists())
        StoredFileRecord.objects.filter(pk=record_row.pk).update(created_on=timezone.now() - timedelta(days=16))
        self.upload()
        self.assertFalse(staged_uploads().filter(pk=record_row.pk).exists())
        self.assertFalse(self.storage.exists(record_row.path))


@FAIRPLAY_ON
class FairPlaySubmissionsTestCase(FairPlayAPIMixin, TestCase):
    def post(self, body):
        return self.api('post', 'fairplay_api_submissions', 'fpexam', data=body)

    def test_files_every_answer(self):
        response = self.post(self.answers_body())
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data['receipt'].startswith('oj_rcpt_'))
        self.assertEqual([s['question_id'] for s in data['submissions']],
                         [qid(self.mcq), qid(self.blank), qid(self.short), qid(self.code)])
        participation = ContestParticipation.objects.get(contest=self.exam, user=self.student.profile, virtual=0)
        submissions = Submission.objects.filter(contest__participation=participation).order_by('id')
        self.assertEqual([s.problem_id for s in submissions], [self.mcq.id, self.blank.id, self.short.id, self.code.id])
        self.assertEqual(submissions[3].source.source, 'print(1)\n')
        self.assertEqual(submissions[3].language.key, 'PY3')
        self.assertEqual(json.loads(submissions[0].source.source)['selected'], [self.right.id])
        receipt = record('fp_att_1')
        self.assertEqual(receipt['receipt'], data['receipt'])
        self.assertFalse(receipt['is_late'])
        self.assertEqual(receipt['handed_in_evidence'], 'server')
        self.assertEqual(active_hand_in(self.exam, self.student.profile)['attempt_id'], 'fp_att_1')
        # The profile is not put into the exam on this site.
        self.student.profile.refresh_from_db()
        self.assertIsNone(self.student.profile.current_contest)

    def test_grading_waits_for_commit(self):
        with mock.patch('judge.models.Submission.judge') as judge:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                self.assertEqual(self.post(self.answers_body()).status_code, 200)
            judge.assert_not_called()
            for callback in callbacks:
                callback()
        self.assertGreaterEqual(judge.call_count, 2)

    def test_same_attempt_for_another_exam_conflicts(self):
        body = self.answers_body()
        self.assertEqual(self.post(body).status_code, 200)
        create_contest('fpother', name='Other', start_time=self.exam.start_time, end_time=self.exam.end_time,
                       is_organization_private=True, fairplay_required=True)
        response = self.api('post', 'fairplay_api_submissions', 'fpother', data=body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(record('fp_att_1')['contest_id'], self.exam.id)

    def test_idempotent_on_attempt_id(self):
        body = self.answers_body()
        first = self.post(body).json()
        second = self.post(body)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), first)
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 4)
        # Key order and whitespace do not matter.
        self.assertEqual(self.api('post', 'fairplay_api_submissions', 'fpexam',
                                  data=json.dumps(body, indent=2, sort_keys=True),
                                  content_type='application/json').json(), first)

    def test_same_attempt_different_body_conflicts(self):
        self.post(self.answers_body())
        changed = self.answers_body(answers=[{'question_id': qid(self.short), 'short_answer': {'text': 'other'}}])
        self.assertEqual(self.post(changed).status_code, 409)
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 4)

    def test_all_or_nothing(self):
        body = self.answers_body(answers=[
            {'question_id': qid(self.mcq), 'choice': {'selected': [self.right.id]}},
            {'question_id': qid(self.code), 'code': {'language': 'RUST', 'source': 'fn main(){}'}},
        ])
        response = self.post(body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['question_id'], qid(self.code))
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 0)
        self.assertFalse(ContestParticipation.objects.filter(contest=self.exam).exists())
        self.assertEqual(records(), [])

    def test_failure_inside_create_submission_rolls_back(self):
        from judge.utils import fairplay_delivery
        real = fairplay_delivery.create_submission
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise ValidationError('not storable')
            return real(*args, **kwargs)

        with mock.patch.object(fairplay_delivery, 'create_submission', side_effect=flaky):
            response = self.post(self.answers_body())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['question_id'], qid(self.short))
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 0)
        self.assertEqual(records(), [])
        # Nothing on file: the retry is filed.
        self.assertEqual(self.post(self.answers_body()).status_code, 200)

    def test_transient_failures_are_503_with_retry_after(self):
        from judge.utils import fairplay_delivery
        for error in (OperationalError('gone away'), DatabaseError('deadlock'), OSError('disk'),
                      StorageUnavailableError('both stores down')):
            with self.subTest(error=error.__class__.__name__):
                with mock.patch.object(fairplay_delivery, 'create_submission', side_effect=error):
                    response = self.post(self.answers_body())
                self.assertEqual(response.status_code, 503)
                self.assertTrue(int(response['Retry-After']) > 0)
                self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 0)
                self.assertEqual(records(), [])
                self.assertIsNone(active_hand_in(self.exam, self.student.profile))
        # A database failure inside the transaction is transient too, and leaves no record behind.
        with mock.patch.object(fairplay_delivery, '_already_handed_in', side_effect=[None, OperationalError('x')]):
            self.assertEqual(self.post(self.answers_body()).status_code, 503)
        self.assertEqual(records(), [])
        self.assertEqual(self.post(self.answers_body()).status_code, 200)

    def test_answers_to_removed_questions_are_skipped_with_warnings(self):
        body = self.answers_body(answers=[
            {'question_id': 'p999999', 'short_answer': {'text': 'removed question'}},
            {'question_id': qid(self.mcq), 'choice': {'selected': [self.right.id, 123456789]}},
            {'question_id': qid(self.short), 'short_answer': {'text': 'Hashing maps keys.'}},
        ])
        response = self.post(body)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual([s['question_id'] for s in data['submissions']], [qid(self.mcq), qid(self.short)])
        self.assertEqual(len(data['warnings']), 2)
        self.assertIn('p999999', data['warnings'][0])
        self.assertIn('123456789', data['warnings'][1])
        mcq = Submission.objects.get(contest_object=self.exam, problem=self.mcq)
        self.assertEqual(json.loads(mcq.source.source)['selected'], [self.right.id])
        self.assertEqual(record('fp_att_1')['warnings'], data['warnings'])
        self.assertEqual(active_hand_in(self.exam, self.student.profile)['warnings'], data['warnings'])
        # The replay carries the same warnings.
        self.assertEqual(self.post(body).json(), data)
        # Shown to the professor in the hand-in box.
        from judge.utils.fairplay_delivery import hand_ins
        self.assertEqual(hand_ins(self.exam)[0]['warnings'], data['warnings'])

    def test_choice_with_only_removed_options_is_not_filed(self):
        body = self.answers_body(answers=[{'question_id': qid(self.mcq), 'choice': {'selected': [123456789]}}])
        response = self.post(body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['submissions'], [])
        self.assertIn('not filed', response.json()['warnings'][0])
        self.assertFalse(Submission.objects.filter(contest_object=self.exam).exists())

    def test_code_only_in_harness_languages(self):
        # fpcode has a PY3 harness: CPP14 is allowed on the problem but not offered, nor accepted.
        body = self.answers_body(answers=[{'question_id': qid(self.code),
                                           'code': {'language': 'CPP14', 'source': 'int main(){}'}}])
        response = self.post(body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['question_id'], qid(self.code))

    def test_participation_counted(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(self.answers_body()).status_code, 200)
        self.assertEqual(Contest.objects.get(pk=self.exam.pk).user_count, 1)

    def test_rolled_back_delivery_deletes_its_stored_files(self):
        from judge.utils import fairplay_delivery
        upload = self.api('post', 'fairplay_api_uploads', 'fpexam', data=b'%PDF-1.4 r', query={
            'username': 'fp_student', 'question_id': qid(self.file), 'filename': 'r.pdf',
        }, headers={'X-Sha256': hashlib.sha256(b'%PDF-1.4 r').hexdigest()}).json()['upload_id']
        body = self.answers_body(answers=[
            {'question_id': qid(self.file), 'file_upload': {'upload_ids': [upload]}},
            {'question_id': qid(self.code), 'code': {'language': 'RUST', 'source': 'fn main(){}'}},
        ])
        saved = []
        real = fairplay_delivery._stored_files

        def spy(submission):
            files = real(submission)
            saved.extend(name for _storage, name in files)
            return files

        with mock.patch.object(fairplay_delivery, '_stored_files', side_effect=spy):
            self.assertEqual(self.post(body).status_code, 422)
        self.assertEqual(len(saved), 1)
        self.assertFalse(self.storage.exists(saved[0]))
        # The staged upload is still there for the retry.
        self.assertTrue(staged_uploads().exists())

    def test_hand_in_time_kept_within_the_exam(self):
        early = self.exam.start_time - timedelta(days=2)
        self.assertEqual(self.post(self.answers_body(handed_in_at=iso(early))).status_code, 200)
        self.assertFalse(record('fp_att_1')['is_late'])

    def test_slow_delivery_rechecks_inside_the_transaction(self):
        """A delivery whose cache lock expired while another attempt was filed is refused."""
        from judge.utils import fairplay_delivery
        real = fairplay_delivery._already_handed_in
        calls = []

        def slow(contest, profile, attempt_id, body_hash, revision=1):
            result = real(contest, profile, attempt_id, body_hash, revision)
            calls.append(attempt_id)
            if len(calls) == 1:
                # The first delivery stalls here; its lock expires and attempt a2 is filed.
                cache.delete(fairplay_delivery._KEY_LOCK % (contest.id, profile.id))
                second = self.post(self.answers_body(attempt='a2'))
                self.assertEqual(second.status_code, 200, second.content)
            return result

        with mock.patch.object(fairplay_delivery, '_already_handed_in', side_effect=slow):
            response = self.post(self.answers_body(attempt='a1'))
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn('a2', response.json()['detail'])
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 4)
        self.assertIsNone(record('a1'))
        self.assertEqual(active_hand_in(self.exam, self.student.profile)['attempt_id'], 'a2')

    def test_lock_renewed_during_a_long_delivery(self):
        from judge.utils import fairplay_delivery
        with mock.patch.object(fairplay_delivery._StudentLock, 'renew', autospec=True) as renew:
            self.assertEqual(self.post(self.answers_body()).status_code, 200)
        self.assertEqual(renew.call_count, 4)

    def test_hand_in_box_without_records_is_one_query_for_all(self):
        from django.db import connection as db
        from django.test.utils import CaptureQueriesContext
        from judge.utils.fairplay_delivery import hand_ins

        def queries():
            cache.delete_many([_KEY_HAND_IN % (self.exam.id, p.id) for p in
                               (self.student.profile, self.classmate.profile)])
            exam = Contest.objects.get(pk=self.exam.pk)
            with CaptureQueriesContext(db) as captured:
                rows = hand_ins(exam)
            self.assertTrue(all(row['record_missing'] for row in rows))
            return len(rows), len(captured)

        self.assertEqual(self.post(self.answers_body()).status_code, 200)
        one = queries()
        self.assertEqual(self.post(self.answers_body(attempt='fp_att_2', username='fp_classmate')).status_code, 200)
        two = queries()
        self.assertEqual((one[0], two[0]), (1, 2))
        self.assertEqual(one[1], two[1])

    def test_invalid_answers(self):
        cases = [
            [{'question_id': qid(self.mcq), 'choice': {'selected': [self.right.id, self.wrong.id]}}],
            [{'question_id': qid(self.mcq), 'short_answer': {'text': 'wrong type'}}],
            [{'question_id': qid(self.blank), 'fill_blank': {'answers': ['a', 'b', 'c']}}],
            [{'question_id': qid(self.code), 'code': {'language': 'PY3', 'source': ''}}],
            [{'question_id': qid(self.short), 'short_answer': {'text': 'a'}},
             {'question_id': qid(self.short), 'short_answer': {'text': 'b'}}],
        ]
        for index, answers in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(self.post(self.answers_body(attempt='bad%d' % index, answers=answers)).status_code,
                                 422)
        self.assertEqual(self.post(self.answers_body(handed_in_evidence='trust me')).status_code, 422)
        self.assertEqual(self.post(self.answers_body(handed_in_at='soon')).status_code, 422)
        self.assertFalse(Submission.objects.filter(contest_object=self.exam).exists())

    def test_roster_check(self):
        self.assertEqual(self.post(self.answers_body(username='fp_outsider')).status_code, 403)
        self.assertEqual(self.post(self.answers_body(username='fp_teacher')).status_code, 403)
        self.assertEqual(self.post(self.answers_body(username='nobody')).status_code, 403)
        self.assertEqual(self.api('post', 'fairplay_api_submissions', 'nosuch', data=self.answers_body()).status_code,
                         404)

    def test_late_marking(self):
        # A hand-in that waited in FairPlay's outbox is not late.
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(hours=2),
                                                       end_time=now - timedelta(minutes=30))
        queued = self.answers_body(attempt='a1', deadline_at=iso(now - timedelta(minutes=30)),
                                   handed_in_at=iso(now - timedelta(minutes=31)))
        self.assertEqual(self.post(queued).status_code, 200)
        receipt = record('a1')
        self.assertFalse(receipt['is_late'])             # handed in before the end, delivered 31 min later
        self.assertEqual(parse_time(receipt['handed_in_at'], 'x'), parse_time(queued['handed_in_at'], 'x'))
        # Handed in 10 minutes after the close: late.
        late = self.answers_body(attempt='a2', username='fp_classmate', deadline_at=iso(now + timedelta(hours=1)),
                                 handed_in_at=iso(now - timedelta(minutes=20)), handed_in_evidence='sealed')
        self.assertEqual(self.post(late).status_code, 200)
        receipt = record('a2')
        self.assertTrue(receipt['is_late'])
        self.assertEqual(receipt['handed_in_evidence'], 'sealed')

    def test_hand_in_time_in_the_future_counts_as_received(self):
        from judge.utils.fairplay_delivery import _lateness_time
        now = timezone.now()
        self.assertEqual(_lateness_time(self.exam, now + timedelta(hours=1), now), now)
        self.assertEqual(_lateness_time(self.exam, None, now), now)
        self.assertEqual(_lateness_time(self.exam, now - timedelta(minutes=5), now), now - timedelta(minutes=5))
        # Never before the exam's start.
        self.assertEqual(_lateness_time(self.exam, self.exam.start_time - timedelta(days=3), now),
                         self.exam.start_time)

    def test_hand_in_after_the_end_time_is_filed(self):
        # Minutes added on the FairPlay tab move the close, and DKU OJ's late mark with it.
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(hours=2),
                                                       end_time=now - timedelta(minutes=5))
        record_added_minutes(self.reload_exam(), 10)
        body = self.answers_body(started_at=iso(now - timedelta(minutes=60)),
                                 deadline_at=iso(now - timedelta(minutes=1)),
                                 handed_in_at=iso(now - timedelta(minutes=1)))
        response = self.post(body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['submissions']), 4)
        self.assertFalse(record('fp_att_1')['is_late'])

    def test_added_minutes_from_fairplay_only_without_own_count(self):
        # DKU OJ's count of added minutes wins; without it, FairPlay's close, at most 8 hours late.
        from judge.utils.fairplay_delivery import added_minutes
        exam = self.reload_exam()
        self.assertEqual(added_minutes(exam), 0)
        self.assertEqual(added_minutes(exam, exam.end_time + timedelta(minutes=25)), 25)
        self.assertEqual(added_minutes(exam, exam.end_time + timedelta(days=3)), 8 * 60)
        self.assertEqual(added_minutes(exam, exam.end_time - timedelta(minutes=5)), 0)
        record_added_minutes(exam, 10)
        record_added_minutes(exam, 5)
        self.assertEqual(added_minutes(exam, exam.end_time + timedelta(days=3)), 15)

    def test_lateness_uses_fairplay_close_when_count_lost(self):
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(hours=2),
                                                       end_time=now - timedelta(minutes=20))
        body = self.answers_body(handed_in_at=iso(now - timedelta(minutes=1)),
                                 exam_closes_at=iso(now + timedelta(minutes=5)))
        self.assertEqual(self.post(body).status_code, 200)
        self.assertFalse(record('fp_att_1')['is_late'])

    def test_hand_ins_refused_14_days_after_the_close(self):
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(days=20),
                                                       end_time=now - timedelta(days=14, minutes=1))
        response = self.post(self.answers_body())
        self.assertEqual(response.status_code, 410)
        self.assertIn('14 days', response.json()['detail'])
        data = b'%PDF-1.4 report'
        upload = self.api('post', 'fairplay_api_uploads', 'fpexam', data=data, query={
            'username': 'fp_student', 'question_id': qid(self.file), 'filename': 'r.pdf',
        }, headers={'X-Sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(upload.status_code, 410)
        self.assertEqual(records(), [])
        # Added minutes move the window too; inside it the hand-in is filed (and late).
        record_added_minutes(self.reload_exam(), 5)
        response = self.post(self.answers_body())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(record('fp_att_1')['is_late'])

    def test_replay_of_a_filed_attempt_after_the_window(self):
        body = self.answers_body()
        first = self.post(body)
        self.assertEqual(first.status_code, 200)
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(days=30),
                                                       end_time=now - timedelta(days=20))
        again = self.post(body)
        self.assertEqual(again.status_code, 200)       # idempotent: the same receipt, nothing new
        self.assertEqual(again.json(), first.json())

    def test_second_attempt_refused_until_cleared(self):
        # A compromised FairPlay can't overwrite a student's hand-in with a new attempt id.
        first = self.answers_body(attempt='att_1')
        self.assertEqual(self.post(first).status_code, 200)
        forged = self.answers_body(attempt='att_2', answers=[
            {'question_id': qid(self.short), 'short_answer': {'text': 'second, forged later'}}])
        response = self.post(forged)
        self.assertEqual(response.status_code, 409)
        self.assertIn('att_1', response.json()['detail'])
        self.assertEqual(Submission.objects.filter(user=self.student.profile, contest_object=self.exam).count(), 4)
        # Another student is not affected; after the professor clears it, the new attempt is filed.
        self.assertEqual(self.post(self.answers_body(attempt='att_3', username='fp_classmate')).status_code, 200)
        clear_hand_in(self.exam, self.student.profile)
        self.assertEqual(self.post(forged).status_code, 200)
        self.assertEqual(Submission.objects.filter(user=self.student.profile, contest_object=self.exam).count(), 5)
        # The cleared attempt's replay still gets its own receipt (no new submissions).
        self.assertEqual(self.post(first).status_code, 200)
        self.assertEqual(Submission.objects.filter(user=self.student.profile, contest_object=self.exam).count(), 5)

    def test_lost_records_never_file_twice(self):
        # Records lost but answers on file: refused with 409, even a replay, until the hand-in is cleared.
        body = self.answers_body(attempt='att_1')
        self.assertEqual(self.post(body).status_code, 200)
        cache.clear()
        for attempt in ('att_1', 'att_2'):
            response = self.post(self.answers_body(attempt=attempt))
            self.assertEqual(response.status_code, 409)
            self.assertIn('already has answers on file', response.json()['detail'])
        self.assertEqual(Submission.objects.filter(user=self.student.profile, contest_object=self.exam).count(), 4)
        warnings = delivery_warnings(self.exam)
        self.assertEqual([(w['username'], w['attempt_id']) for w in warnings],
                         [('fp_student', 'att_1'), ('fp_student', 'att_2')])
        hand_in = active_hand_in(self.exam, self.student.profile)
        self.assertTrue(hand_in['record_missing'])
        self.assertEqual(hand_in['submission_count'], 4)
        self.assertTrue(clear_hand_in(self.exam, self.student.profile))
        self.assertIsNone(active_hand_in(self.exam, self.student.profile))
        self.assertEqual(self.post(self.answers_body(attempt='att_2')).status_code, 200)
        self.assertEqual(Submission.objects.filter(user=self.student.profile, contest_object=self.exam).count(), 8)

    def test_clear_with_nothing_on_file(self):
        self.assertFalse(clear_hand_in(self.exam, self.student.profile))

    def test_records_expire_after_the_window(self):
        from judge.utils.fairplay_delivery import DELIVERY_WINDOW, RECORD_TTL
        self.assertGreater(RECORD_TTL, DELIVERY_WINDOW.total_seconds() * 2)
        self.assertEqual(self.post(self.answers_body()).status_code, 200)
        for key in (_attempt_key('fp_att_1'), _KEY_HAND_IN % (self.exam.id, self.student.profile.id)):
            full_key = cache.make_and_validate_key(key)
            if hasattr(cache, '_expire_info'):
                remaining = cache._expire_info[full_key] - time.time()
            else:
                remaining = cache._cache.get_client(key).ttl(full_key)
            self.assertGreater(remaining, DELIVERY_WINDOW.total_seconds())

    def test_report_url_only_into_fairplay(self):
        cases = (
            ('https://exam.example.edu/teacher/exams/1/submissions/2', True),
            ('https://evil.example/phish', False),
            ('https://exam.example.edu.evil.example/x', False),
            ('https://exam.example.edu@evil.example/x', False),
            ('javascript:alert(1)', False),
            ('http://exam.example.edu/teacher/1', False),
        )
        for index, (url, kept) in enumerate(cases):
            with self.subTest(url=url):
                user = create_user(username='fp_url_%d' % index)
                user.profile.organizations.add(self.org)
                self.klass.members.add(user.profile)
                response = self.post(self.answers_body(attempt='url%d' % index, username=user.username,
                                                       integrity_report_url=url))
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(record('url%d' % index)['report_url'], url if kept else '')
        with override_settings(FAIRPLAY_PUBLIC_URL='https://fairplay.example.edu'):
            self.assertTrue(report_url_allowed('https://fairplay.example.edu/teacher/1'))
            self.assertTrue(report_url_allowed('https://exam.example.edu/teacher/1'))
            self.assertFalse(report_url_allowed('https://fairplay.example.edu.evil/teacher/1'))

    def test_participation_start_within_the_exam(self):
        # A back-dated (or future) FairPlay start can't move the participation outside the exam.
        self.assertEqual(self.post(self.answers_body(started_at='2001-01-01T00:00:00Z')).status_code, 200)
        participation = ContestParticipation.objects.get(pk=record('fp_att_1')['participation_id'])
        self.assertEqual(participation.real_start, self.reload_exam().start_time)
        self.assertEqual(self.post(self.answers_body(attempt='f', username='fp_classmate',
                                                     started_at='2999-01-01T00:00:00Z')).status_code, 200)
        participation = ContestParticipation.objects.get(pk=record('f')['participation_id'])
        self.assertLessEqual(participation.real_start, timezone.now())

    def test_body_limit(self):
        body = self.answers_body(answers=[{'question_id': qid(self.short), 'short_answer': {'text': 'x' * 2_100_000}}])
        self.assertEqual(self.post(body).status_code, 413)


@FAIRPLAY_ON
class FairPlayResultsTestCase(FairPlayAPIMixin, TestCase):
    def deliver(self):
        response = self.api('post', 'fairplay_api_submissions', 'fpexam', data=self.answers_body())
        self.assertEqual(response.status_code, 200, response.content)
        return {s['question_id']: Submission.objects.get(pk=s['submission_id']) for s in response.json()['submissions']}

    def grade(self, submissions):
        for submission in submissions.values():
            submission.status = 'D'
            submission.result = 'AC'
            submission.case_points = 1
            submission.case_total = 1
            submission.points = submission.problem.points
            submission.save()

    def results(self, **query):
        return self.api('get', 'fairplay_api_results', 'fpexam', query=query).json()

    def test_released_only(self):
        submissions = self.deliver()
        # Not released: the student is listed without any points.
        self.assertEqual([(r['username'], r['released']) for r in self.results()['results']],
                         [('fp_student', False)])
        self.assertNotIn('questions', self.results()['results'][0])
        self.grade(submissions)
        # Exam format: choice / blank / short results stay hidden until finalized.
        self.assertEqual([r['released'] for r in self.results()['results']], [False])
        Contest.objects.filter(pk=self.exam.pk).update(results_finalized=True)
        data = self.results()
        self.assertEqual(len(data['results']), 1)
        result = data['results'][0]
        self.assertEqual(result['username'], 'fp_student')
        self.assertTrue(result['released'])
        self.assertEqual(result['total_points'], 66)
        self.assertEqual(result['total'], 56)  # everything right except the unanswered upload
        points = {q['question_id']: q['points'] for q in result['questions']}
        self.assertEqual(points[qid(self.mcq)], 10)
        self.assertEqual(points[qid(self.file)], 0)
        self.assertIsNone(data['next_page'])

    def test_regrade_and_withdrawal(self):
        submissions = self.deliver()
        self.grade(submissions)
        Contest.objects.filter(pk=self.exam.pk).update(results_finalized=True)
        first = self.results()
        since = first['results'][0]['updated_at']
        # A released manual regrade is reported again.
        grade = SubmissionManualGrade.objects.create(submission=submissions[qid(self.short)], points=4,
                                                     machine_points=10, released=True)
        Submission.objects.filter(pk=grade.submission_id).update(review_note='Too short.')
        regraded = self.results(changed_since=since)
        self.assertEqual(len(regraded['results']), 1)
        short = [q for q in regraded['results'][0]['questions'] if q['question_id'] == qid(self.short)][0]
        self.assertEqual((short['points'], short['feedback_md']), (4, 'Too short.'))
        # Un-finalizing withdraws the release.
        Contest.objects.filter(pk=self.exam.pk).update(results_finalized=False)
        withdrawn = self.results(changed_since=regraded['results'][0]['updated_at'])
        self.assertEqual(withdrawn['results'][0]['released'], False)
        self.assertNotIn('questions', withdrawn['results'][0])

    def test_only_the_first_page_notes_changes(self):
        with mock.patch('judge.utils.fairplay_delivery.refresh_results', return_value={}) as refresh:
            self.results(page=2)
            self.assertFalse(refresh.call_args.kwargs['update'])
            self.results()
            self.assertTrue(refresh.call_args.kwargs['update'])

    def test_lost_tracking_lists_again(self):
        submissions = self.deliver()
        self.grade(submissions)
        Contest.objects.filter(pk=self.exam.pk).update(results_finalized=True)
        since = self.results()['server_time']
        self.assertEqual(self.results(changed_since=since)['results'], [])
        cache.clear()
        again = self.results(changed_since=since)
        self.assertEqual([(r['username'], r['released']) for r in again['results']], [('fp_student', True)])

    def test_unknown_exam(self):
        self.assertEqual(self.api('get', 'fairplay_api_results', 'nosuch').status_code, 404)


@FAIRPLAY_ON
class FairPlayConcurrentDeliveryTestCase(FairPlayDataMixin, TransactionTestCase):
    """Two racing deliveries of one attempt file it once and get the same receipt."""
    serialized_rollback = False

    @classmethod
    def setUpTestData(cls):
        pass

    def setUp(self):
        cache.clear()
        FairPlayDataMixin.setUpTestData.__func__(type(self))

    def test_concurrent_duplicate(self):
        from judge.utils import fairplay_delivery
        body = FairPlayAPIMixin.answers_body(self)
        barrier = threading.Barrier(2, timeout=10)
        results = []

        class RacingCache:
            """The real cache, but both requests pass the "already delivered?" check before either writes."""

            def __getattr__(self, name):
                return getattr(cache, name)

            def get(self, key, *args, **kwargs):
                value = cache.get(key, *args, **kwargs)
                if key.startswith('fpb:attempt:') and not getattr(threading.current_thread(), 'checked', False):
                    threading.current_thread().checked = True
                    barrier.wait()
                return value

        def run():
            try:
                client = Client()
                url = reverse('fairplay_api_submissions', args=['fpexam'])
                data = json.dumps(body).encode()
                response = client.post(url, data, content_type='application/json', **fp_signed('POST', url, data))
                results.append((response.status_code, response.json()))
            finally:
                connection.close()

        with mock.patch.object(fairplay_delivery, 'cache', RacingCache()):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual([status for status, _ in results], [200, 200], results)
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(Submission.objects.filter(contest_object__key='fpexam').count(), 4)
        self.assertEqual(ContestProblem.objects.filter(contest__key='fpexam').count(), 5)

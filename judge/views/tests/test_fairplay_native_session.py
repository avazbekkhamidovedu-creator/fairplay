"""The HTTP lifecycle must prove device identity before changing ownership."""
import base64
import json
import time
import uuid
from types import SimpleNamespace
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from judge.models import FairPlayDeviceSession, FairPlayDeviceSlot
from judge.utils.fairplay_device_session import instructor_unlock
from judge.utils.fairplay_native_admission import session_digest
from judge.utils.fairplay_native_proxy import sign_assertion
from judge.views.fairplay_native_session import native_claim, native_state, native_renew, native_finish, native_abort, native_recover
from judge.views.tests.fairplay_base import FairPlayDataMixin, FAIRPLAY_ON


@FAIRPLAY_ON
@override_settings(DEBUG=True, FAIRPLAY_NATIVE_BROWSER_ENABLED=True,
    FAIRPLAY_NATIVE_LOOPBACK_FIXTURE=True, FAIRPLAY_NATIVE_PROXY_SECRET='p' * 32,
    FAIRPLAY_NATIVE_SESSION_BINDING_SECRET='s' * 32, FAIRPLAY_NATIVE_PROXY_PEERS=['127.0.0.1'],
    FAIRPLAY_BROWSER_SIGNING_KEY=base64.b64encode(b'k' * 32).decode())
class NativeSessionAPITests(FairPlayDataMixin, TestCase):
    def request(self, method='POST', path='/api/fairplay/native/claim/fpexam',
                user=None, fingerprint='a' * 64, cookie='student-cookie', nonce=None):
        data = {'nonce': nonce or uuid.uuid4().hex}
        request = getattr(RequestFactory(), method.lower())(path, data)
        request.user = user or self.student
        request.profile = request.user.profile
        request.session = SimpleNamespace(session_key=cookie)
        timestamp, proxy_nonce = int(time.time()), uuid.uuid4().hex
        signature = sign_assertion(b'p' * 32, method, request.get_full_path(),
            session_digest(cookie, 's' * 32), fingerprint, timestamp, proxy_nonce)
        request.META.update(REMOTE_ADDR='127.0.0.1', HTTP_X_FAIRPLAY_TLS_TIMESTAMP=str(timestamp),
            HTTP_X_FAIRPLAY_TLS_FINGERPRINT=fingerprint, HTTP_X_FAIRPLAY_TLS_NONCE=proxy_nonce,
            HTTP_X_FAIRPLAY_TLS_SIGNATURE=signature)
        return request

    def decode(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        envelope = json.loads(response.content)
        payload = base64.b64decode(envelope['payload'])
        Ed25519PrivateKey.from_private_bytes(b'k' * 32).public_key().verify(
            base64.b64decode(envelope['signature']), payload)
        return json.loads(payload)

    def claim(self, **kwargs):
        return native_claim(self.request(**kwargs), self.exam.key)

    def test_claim_requires_fresh_device_proof_and_roster(self):
        request = self.request()
        payload = self.decode(native_claim(request, self.exam.key))
        self.assertEqual(payload['device'], 'a' * 64)
        self.assertEqual(payload['user'], str(self.student.pk))
        with self.assertRaises(Http404):
            native_claim(request, self.exam.key)
        with self.assertRaises(Http404):
            self.claim(user=self.outsider)
        request = self.request()
        request.META.pop('HTTP_X_FAIRPLAY_TLS_SIGNATURE')
        with self.assertRaises(Http404):
            native_claim(request, self.exam.key)
        self.assertEqual(FairPlayDeviceSession.objects.count(), 1)

    def test_second_device_cannot_claim_or_read_and_a_new_sign_in_of_the_same_device_rebinds(self):
        payload = self.decode(self.claim())
        self.assertEqual(self.claim(fingerprint='b' * 64).status_code, 403)
        with self.assertRaises(Http404):
            native_state(self.request('GET', '/api/fairplay/native/session/' + payload['session'], fingerprint='b' * 64),
                         payload['session'])
        again = self.decode(self.claim(cookie='another-cookie'))
        self.assertEqual(again['session'], payload['session'])
        with self.assertRaises(Http404):
            native_state(self.request('GET', '/api/fairplay/native/session/' + payload['session']), payload['session'])

    def test_teacher_release_is_signed_and_cannot_be_renewed(self):
        payload = self.decode(self.claim())
        instructor_unlock(contest=self.exam, username=self.student.username,
                          actor=self.teacher, reason='Device troubleshooting')
        path = '/api/fairplay/native/session/' + payload['session']
        terminal = self.decode(native_state(self.request('GET', path), payload['session']))
        self.assertEqual(terminal['state'], 'released')
        self.assertEqual(terminal['session'], payload['session'])
        renewed = self.decode(native_renew(self.request('POST', path + '/renew'), payload['session']))
        self.assertEqual(renewed['state'], 'released')
        self.assertEqual(self.claim().status_code, 403)

    def test_invalid_response_nonce_rolls_back_claim(self):
        with self.assertRaises(Http404):
            self.claim(nonce='bad')
        self.assertEqual(FairPlayDeviceSession.objects.count(), 0)

    def test_disabled_or_unshared_production_cache_denies(self):
        for config in ({'FAIRPLAY_NATIVE_BROWSER_ENABLED': False}, {'DEBUG': False}):
            with self.settings(**config), self.assertRaises(Http404):
                self.claim()
        self.assertEqual(FairPlayDeviceSession.objects.count(), 0)

    def test_content_scope_is_exact_and_cleared_after_response(self):
        from django.http import HttpResponse
        from judge.native_browser_middleware import NativeBrowserAdmissionMiddleware
        from judge.utils.fairplay import fairplay_hidden_for
        self.decode(self.claim())
        self.assertTrue(fairplay_hidden_for(self.student, self.exam))
        observed = []
        def content(request):
            observed.append(fairplay_hidden_for(request.user, self.exam))
            self.assertTrue(fairplay_hidden_for(self.classmate, self.exam))
            return HttpResponse('content')
        middleware = NativeBrowserAdmissionMiddleware(content)
        response = middleware(self.request('GET', '/contest/fpexam'))
        self.assertEqual(observed, [False])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertTrue(fairplay_hidden_for(self.student, self.exam))
        observed.clear()
        middleware(self.request('GET', '/contest/fpexam/grade'))
        middleware(self.request('GET', '/contest/fpexam', fingerprint='b' * 64))
        self.assertEqual(observed, [False, True])

    def test_scope_clears_even_if_view_raises(self):
        from judge.native_browser_middleware import NativeBrowserAdmissionMiddleware
        from judge.utils.fairplay import fairplay_hidden_for
        self.decode(self.claim())
        def broken(request):
            self.assertFalse(fairplay_hidden_for(request.user, self.exam))
            raise RuntimeError('test failure')
        with self.assertRaises(RuntimeError):
            NativeBrowserAdmissionMiddleware(broken)(self.request('GET', '/contest/fpexam'))
        self.assertTrue(fairplay_hidden_for(self.student, self.exam))

    def test_finish_cannot_unlock_before_join(self):
        payload = self.decode(self.claim())
        request = self.request('POST', '/api/fairplay/native/session/' + payload['session'] + '/finish')
        request.POST = request.POST.copy()
        request.POST['confirm'] = '1'
        with self.settings(ASSIGNMENT_HARVEST_ENABLED=True):
            response = native_finish(request, payload['session'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')

    def client_request(self, method, path, data=None, fingerprint='a' * 64):
        timestamp, nonce = int(time.time()), uuid.uuid4().hex
        cookie = self.client.session.session_key
        signature = sign_assertion(b'p' * 32, method, path,
                                  session_digest(cookie, 's' * 32), fingerprint, timestamp, nonce)
        return getattr(self.client, method.lower())(path, data or {},
            HTTP_X_FAIRPLAY_TLS_TIMESTAMP=str(timestamp), HTTP_X_FAIRPLAY_TLS_FINGERPRINT=fingerprint,
            HTTP_X_FAIRPLAY_TLS_NONCE=nonce, HTTP_X_FAIRPLAY_TLS_SIGNATURE=signature)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_real_routes_claim_join_save_finish_and_refuse_reentry(self):
        from judge.models import AssignmentDraft, ContestSubmission
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        response = self.client_request('POST', '/contest/fpexam/join')
        self.assertEqual(response.status_code, 302, response.content[:500])
        session = FairPlayDeviceSession.objects.get(pk=payload['session'])
        self.assertIsNotNone(session.participation_id)
        listing = self.client_request('GET', '/problems/')
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'Explain')
        page = self.client_request('GET', '/problem/fpshort')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Explain hashing.')
        response = self.client_request('POST', '/contest/fpexam/draft/fpshort',
                                      {'content': '{"text":"Native route hand-in test"}'})
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertTrue(AssignmentDraft.objects.filter(participation=session.participation).exists())
        path = '/api/fairplay/native/session/' + payload['session'] + '/finish'
        response = self.client_request('POST', path, {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertEqual(self.decode(response)['state'], 'completed')
        self.assertTrue(ContestSubmission.objects.filter(participation=session.participation).exists())
        response = self.client_request('POST', '/contest/fpexam/draft/fpshort',
                                      {'content': '{"text":"Too late"}'})
        self.assertEqual(response.status_code, 403)
        response = self.client_request('POST', '/api/fairplay/native/claim/fpexam', {'nonce': uuid.uuid4().hex})
        self.assertEqual(response.status_code, 403)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_existing_dku_leave_completes_native_exam_and_poll_confirms_unlock(self):
        from judge.models import AssignmentDraft, ContestSubmission
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        response = self.client_request('POST', '/contest/fpexam/join')
        self.assertEqual(response.status_code, 302, response.content[:500])
        session = FairPlayDeviceSession.objects.get(pk=payload['session'])
        self.assertIsNotNone(session.participation_id)
        listing = self.client_request('GET', '/problems/')
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'Explain')
        page = self.client_request('GET', '/problem/fpshort')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Explain hashing.')
        response = self.client_request('POST', '/contest/fpexam/draft/fpshort',
                                      {'content': '{"text":"Native route hand-in test"}'})
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertTrue(AssignmentDraft.objects.filter(participation=session.participation).exists())
        path = '/contest/fpexam/leave'
        response = self.client_request('POST', path, {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertContains(response, 'Exam submitted')
        self.assertNotContains(response, 'Start exam in FairPlay')
        self.assertNotIn('Location', response)
        response = self.client_request('POST', '/api/fairplay/native/session/' + payload['session'] + '/renew', {'nonce': uuid.uuid4().hex})
        self.assertEqual(self.decode(response)['state'], 'completed')
        self.assertTrue(ContestSubmission.objects.filter(participation=session.participation).exists())
        response = self.client_request('POST', '/contest/fpexam/draft/fpshort',
                                      {'content': '{"text":"Too late"}'})
        self.assertEqual(response.status_code, 403)
        response = self.client_request('POST', '/api/fairplay/native/claim/fpexam', {'nonce': uuid.uuid4().hex})
        self.assertEqual(response.status_code, 403)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_renew_after_deadline_queues_the_hand_in_and_the_next_renew_completes(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from judge.models import Contest, ContestSubmission, FairPlayDeviceAudit
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        self.client_request('POST', '/contest/fpexam/join')
        self.client_request('POST', '/contest/fpexam/draft/fpshort', {'content': '{"text":"Written before the deadline"}'})
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(seconds=1))
        path = '/api/fairplay/native/session/' + payload['session'] + '/renew'
        queued = []
        with patch('judge.utils.assignment_harvest.harvest_participation') as inline, \
                patch.object(harvest_participation_drafts, 'delay', side_effect=queued.append), \
                self.captureOnCommitCallbacks(execute=True):
            first = self.decode(self.client_request('POST', path, {'nonce': uuid.uuid4().hex}))
        self.assertEqual(first['state'], 'finishing')
        inline.assert_not_called()
        session = FairPlayDeviceSession.objects.get(pk=payload['session'])
        self.assertEqual(queued, [session.participation_id])
        self.assertEqual(FairPlayDeviceAudit.objects.filter(session=session, action='harvest-queued').count(), 1)
        self.assertFalse(ContestSubmission.objects.filter(participation=session.participation).exists())
        harvest_participation_drafts(session.participation_id)
        second = self.decode(self.client_request('POST', path, {'nonce': uuid.uuid4().hex}))
        self.assertEqual(second['state'], 'completed')
        self.assertTrue(ContestSubmission.objects.filter(participation=session.participation).exists())
        self.assertEqual(FairPlayDeviceAudit.objects.filter(session=session, action='harvest-queued').count(), 1)

    def test_renew_after_deadline_without_join_releases_the_device(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import Contest
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(seconds=1))
        path = '/api/fairplay/native/session/' + payload['session'] + '/renew'
        result = self.decode(self.client_request('POST', path, {'nonce': uuid.uuid4().hex}))
        self.assertEqual(result['state'], 'released')

    def test_plain_browser_gets_launch_page_on_join_and_problem_pages_stay_hidden(self):
        self.client.force_login(self.student)
        response = self.client.get('/problem/fpshort')
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'Explain hashing.', status_code=404)
        response = self.client.get('/problem/fpshort/submit')
        self.assertEqual(response.status_code, 404)
        response = self.client.post('/problem/fpshort/submit', {})
        self.assertIn(response.status_code, (403, 404))
        response = self.client.post('/contest/fpexam/join', follow=True)
        self.assertContains(response, 'Start exam in FairPlay')
        self.assertFalse(FairPlayDeviceSession.objects.exists())
        self.client.force_login(self.teacher)
        response = self.client.get('/contest/fpexam')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Start exam in FairPlay')

    def test_teacher_unlock_http_requires_permission_and_confirmation(self):
        payload = self.decode(self.claim())
        path = '/contest/fpexam/fairplay-help'
        data = dict(action='unlock-device', netid=self.student.username, reason='Device stuck')
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(path, dict(data, confirm='1')).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(path, data).status_code, 200)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')
        self.assertEqual(self.client.post(path, dict(data, confirm='1')).status_code, 302)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'released')

    def test_other_device_is_refused_clearly_until_instructor_frees_the_slot(self):
        self.decode(self.claim())
        refused = self.claim(fingerprint='b' * 64, cookie='new-cookie')
        body = json.loads(refused.content)
        self.assertEqual((refused.status_code, body['reason']), (403, 'other_device'))
        self.assertIn('another computer or browser', body['error'])
        self.assertNotIn('payload', body)
        recovered = native_recover(self.request(path='/api/fairplay/native/recover/fpexam',
                                                fingerprint='b' * 64, cookie='new-cookie'), self.exam.key)
        self.assertEqual((recovered.status_code, json.loads(recovered.content)['reason']), (403, 'other_device'))
        self.client.force_login(self.teacher)
        data = dict(action='release-device', netid=self.student.username, reason='Computer failed', confirm='1')
        self.assertEqual(self.client.post('/contest/fpexam/fairplay-help', data).status_code, 302)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'released')
        again = self.claim(fingerprint='b' * 64, cookie='new-cookie')
        self.assertEqual((again.status_code, json.loads(again.content)['reason']), (403, 'handed_in'))
        self.client.post('/contest/fpexam/fairplay-help', dict(data, action='reopen', minutes='30', reason='Computer failed'))
        payload = self.decode(self.claim(fingerprint='b' * 64, cookie='new-cookie'))
        self.assertEqual((payload['state'], payload['device']), ('active', 'b' * 64))

    def second_exam(self):
        from judge.models import Contest
        exam = Contest.objects.get(pk=self.exam.pk)
        tags, classes, orgs = list(exam.tags.all()), list(exam.classes.all()), list(exam.organizations.all())
        private, authors = list(exam.private_contestants.all()), list(exam.authors.all())
        exam.pk, exam.id, exam.key = None, None, 'fpexam2'
        exam.save()
        exam.tags.set(tags)
        exam.classes.set(classes)
        exam.organizations.set(orgs)
        exam.private_contestants.set(private)
        exam.authors.set(authors)
        return exam

    def test_stuck_unjoined_session_on_another_exam_frees_itself_after_the_grace(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        other = self.second_exam()
        self.decode(self.claim())
        refused = native_claim(self.request(path='/api/fairplay/native/claim/fpexam2'), other.key)
        self.assertEqual((refused.status_code, json.loads(refused.content)['reason']), (403, 'other_device'))
        FairPlayDeviceSession.objects.update(lease_until=timezone.now() - timedelta(days=3))
        payload = self.decode(native_claim(self.request(path='/api/fairplay/native/claim/fpexam2'), other.key))
        self.assertEqual((payload['contest'], payload['state']), ('fpexam2', 'active'))
        old = FairPlayDeviceSession.objects.get(contest=self.exam)
        self.assertEqual(old.state, 'released')
        self.assertTrue(FairPlayDeviceAudit.objects.filter(session=old, action='auto-expire').exists())

    def test_instructor_unlock_still_frees_a_session_on_another_exam_within_the_grace(self):
        other = self.second_exam()
        self.decode(self.claim())
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='stuck')
        payload = self.decode(native_claim(self.request(path='/api/fairplay/native/claim/fpexam2'), other.key))
        self.assertEqual((payload['contest'], payload['state']), ('fpexam2', 'active'))

    def other_claim(self, other):
        return native_claim(self.request(path='/api/fairplay/native/claim/fpexam2'), other.key)

    def test_session_of_an_ended_exam_frees_the_slot_and_is_audited(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import Contest, FairPlayDeviceAudit
        other = self.second_exam()
        self.decode(self.claim())
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(minutes=1))
        payload = self.decode(self.other_claim(other))
        self.assertEqual(payload['contest'], 'fpexam2')
        old = FairPlayDeviceSession.objects.get(contest_id=self.exam.pk)
        self.assertEqual(old.state, 'released')
        self.assertEqual(FairPlayDeviceAudit.objects.filter(session=old, action='auto-expire').count(), 1)

    def test_session_of_an_ended_participation_frees_the_slot(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import ContestParticipation
        other = self.second_exam()
        self.joined()
        from judge.models import Contest
        Contest.objects.filter(pk=self.exam.pk).update(time_limit=timedelta(minutes=1))
        ContestParticipation.objects.filter(contest=self.exam, user=self.student.profile).update(
            real_start=timezone.now() - timedelta(hours=1))
        FairPlayDeviceSession.objects.update(lease_until=timezone.now() - timedelta(hours=1))
        self.student.profile.refresh_from_db()
        self.student.profile.current_contest = None
        self.student.profile.save()
        self.assertEqual(self.decode(self.other_claim(other))['contest'], 'fpexam2')

    def test_disqualified_session_stays_refused_for_its_own_exam_and_frees_the_slot_for_others(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import Contest, ContestParticipation
        other = self.second_exam()
        self.joined()
        Contest.objects.filter(pk=self.exam.pk).update(time_limit=timedelta(minutes=1))
        ContestParticipation.objects.filter(contest=self.exam, user=self.student.profile).update(
            is_disqualified=True, real_start=timezone.now() - timedelta(hours=1))
        FairPlayDeviceSession.objects.update(lease_until=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.claim(fingerprint='b' * 64).status_code, 403)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')
        self.assertEqual(self.decode(self.other_claim(other))['contest'], 'fpexam2')

    def test_abort_before_joining_does_not_count_as_handed_in(self):
        from judge.utils.fairplay_device_session import abort_device
        payload = self.decode(self.claim())
        abort_device(session_id=payload['session'], user_id=self.student.pk, fingerprint='a' * 64,
                     browser_digest=self.held(payload).browser_digest)
        self.assertEqual(self.decode(self.claim())['state'], 'active')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_claim_of_another_exam_after_the_deadline_waits_for_the_queued_hand_in(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from judge.models import Contest
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        other = self.second_exam()
        self.joined()
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(minutes=1))
        with patch.object(harvest_participation_drafts, 'delay'), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.other_claim(other).status_code, 403)
        self.assertEqual(FairPlayDeviceSession.objects.get(contest_id=self.exam.pk).state, 'finishing')
        harvest_participation_drafts(FairPlayDeviceSession.objects.get(contest_id=self.exam.pk).participation_id)
        self.decode(self.other_claim(other))
        self.assertEqual(FairPlayDeviceSession.objects.get(contest_id=self.exam.pk).state, 'completed')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    def test_release_failsafe_does_not_overwrite_a_session_finished_meanwhile(self):
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        from judge.views.fairplay_native_session import close_after_deadline
        a = self.deadline_session(self.student)
        self.end_exam(minutes_ago=20)
        stale = FairPlayDeviceSession.objects.get(pk=a.pk)
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='completed')
        self.assertEqual(close_after_deadline(stale).state, 'completed')
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='auto-release-after-deadline').exists())

    def test_joined_live_session_with_an_expired_lease_still_blocks_another_computer(self):
        from datetime import timedelta
        from django.utils import timezone
        other = self.second_exam()
        self.joined()
        FairPlayDeviceSession.objects.update(lease_until=timezone.now() - timedelta(days=3))
        self.assertEqual(self.other_claim(other).status_code, 403)
        self.assertEqual(self.claim(fingerprint='b' * 64).status_code, 403)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')

    def test_joined_session_with_a_new_sign_in_is_not_rebound(self):
        self.joined()
        self.assertEqual(self.claim(cookie='new-sign-in').status_code, 403)

    def test_finished_session_still_in_the_slot_is_detached(self):
        from judge.models import FairPlayDeviceSlot
        other = self.second_exam()
        self.decode(self.claim())
        FairPlayDeviceSession.objects.update(state='completed')
        self.assertEqual(self.decode(self.other_claim(other))['contest'], 'fpexam2')
        self.assertNotEqual(FairPlayDeviceSlot.objects.get(user=self.student).session.contest_id, self.exam.pk)

    def deadline_session(self, user, joined=True, **fields):
        from django.utils import timezone
        from judge.models import ContestParticipation, FairPlayDeviceSlot
        now = timezone.now()
        session = FairPlayDeviceSession.objects.create(
            user=user, contest=self.exam, fingerprint='a' * 64, browser_digest=session_digest('student-cookie', 's' * 32),
            lease_until=now + timezone.timedelta(seconds=90), **fields)
        if joined:
            session.participation = ContestParticipation.objects.create(
                contest=self.exam, user=user.profile, real_start=now - timezone.timedelta(hours=1), virtual=0)
            session.save(update_fields=['participation'])
        FairPlayDeviceSlot.objects.update_or_create(user=user, defaults={'session': session})
        return session

    def end_exam(self, minutes_ago=1):
        from django.utils import timezone
        from judge.models import Contest
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timezone.timedelta(minutes=minutes_ago))

    def fresh(self, session):
        return FairPlayDeviceSession.objects.select_related('contest', 'participation').get(pk=session.pk)

    def state_payload(self, session, user=None):
        path = '/api/fairplay/native/session/' + str(session.pk)
        return self.decode(native_state(self.request('GET', path, user=user), str(session.pk)))

    def timed_exam(self, limit_minutes=60, contest_minutes=300):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import Contest
        Contest.objects.filter(pk=self.exam.pk).update(
            time_limit=timedelta(minutes=limit_minutes), end_time=timezone.now() + timedelta(minutes=contest_minutes))

    def start_ago(self, session, minutes):
        from django.utils import timezone
        from judge.models import ContestParticipation
        ContestParticipation.objects.filter(pk=session.participation_id).update(
            real_start=timezone.now() - timezone.timedelta(minutes=minutes))

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_student_with_extra_time_stays_locked_while_the_other_is_completed(self):
        from django.utils import timezone
        from judge.models import ContestParticipation
        from judge.views.fairplay_native_session import close_after_deadline
        self.timed_exam()
        a = self.deadline_session(self.student)
        b = self.deadline_session(self.classmate)
        for session in (a, b):
            self.start_ago(session, 90)
        self.start_ago(a, 30)
        self.assertEqual(close_after_deadline(a).state, 'active')
        self.assertEqual(close_after_deadline(b).state, 'finishing')
        self.assertEqual(close_after_deadline(b).state, 'completed')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_extra_time_given_after_the_personal_end_but_before_the_close_is_honoured(self):
        from django.utils import timezone
        from judge.models import ContestParticipation
        from judge.utils.fairplay_device_session import personal_deadline
        from judge.views.fairplay_native_session import close_after_deadline
        self.timed_exam()
        a = self.deadline_session(self.student)
        self.start_ago(a, 90)
        self.assertLessEqual(personal_deadline(self.fresh(a)), timezone.now())
        self.start_ago(a, 30)
        self.assertEqual(close_after_deadline(a).state, 'active')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_unjoined_session_with_a_future_reopen_window_is_not_closed_and_one_without_is(self):
        from django.utils import timezone
        from judge.views.fairplay_native_session import close_after_deadline
        from judge.models import FairPlayDeviceAudit
        a = self.deadline_session(self.student, joined=False)
        FairPlayDeviceAudit.objects.create(
            session=a, actor=self.student, action='reopen-window',
            reason='until=%s;used=0' % (timezone.now() + timezone.timedelta(minutes=10)).isoformat())
        self.end_exam()
        self.assertEqual(close_after_deadline(a).state, 'active')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_the_apps_deadline_equals_what_the_core_enforces(self):
        from django.utils import timezone
        from judge.models import ContestParticipation
        from judge.utils.fairplay_device_session import personal_deadline
        self.timed_exam(limit_minutes=120)
        a = self.deadline_session(self.student)
        self.start_ago(a, 90)
        record = self.fresh(a)
        self.assertEqual(personal_deadline(record), record.participation.end_time)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_the_exam_deadline_still_closes_an_unextended_student_and_nothing_reads_the_removed_column(self):
        from judge.models import ContestParticipation, FairPlayDeviceSession
        from judge.views.fairplay_native_session import close_after_deadline
        self.assertNotIn('reopen_until', [f.name for f in FairPlayDeviceSession._meta.get_fields()])
        self.assertNotIn('fairplay_deadline_override', [f.name for f in ContestParticipation._meta.get_fields()])
        self.timed_exam()
        a = self.deadline_session(self.student)
        self.start_ago(a, 10)
        self.end_exam()
        self.assertEqual(close_after_deadline(a).state, 'finishing')

    def post_state(self, path, extra=None, **kwargs):
        return self.post_with(path, dict({'nonce': uuid.uuid4().hex, 'confirm': '1'}, **(extra or {})), **kwargs)

    def test_signed_state_itself_writes_nothing(self):
        from judge.views.fairplay_native_session import signed_state
        a = self.deadline_session(self.student, joined=False)
        self.end_exam()
        record = self.fresh(a)
        response = signed_state(self.request('GET', '/api/fairplay/native/session/' + str(a.pk)), record)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fresh(a).state, 'active')

    def test_state_renew_and_recover_close_a_session_past_its_deadline(self):
        for name in ('state', 'renew', 'recover'):
            FairPlayDeviceSession.objects.all().update(state='completed')
            a = self.deadline_session(self.student, joined=False)
            self.end_exam()
            path = '/api/fairplay/native/session/' + str(a.pk)
            if name == 'state':
                payload = self.state_payload(a)
            elif name == 'renew':
                payload = self.decode(native_renew(self.post_state(path + '/renew'), str(a.pk)))
            else:
                payload = self.decode(native_recover(self.post_state('/api/fairplay/native/recover/fpexam'), self.exam.key))
            self.assertEqual(payload['state'], 'released', name)
            self.assertEqual(self.fresh(a).state, 'released', name)

    def test_abort_of_an_unjoined_session_after_the_deadline_still_works_once(self):
        from judge.models import FairPlayDeviceAudit
        a = self.deadline_session(self.student, joined=False)
        self.end_exam()
        payload = self.decode(native_abort(self.post_state('/api/fairplay/native/session/%s/abort' % a.pk), str(a.pk)))
        self.assertEqual(payload['state'], 'released')
        self.assertEqual(FairPlayDeviceAudit.objects.filter(session=a, action='abort').count(), 1)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_finish_after_the_deadline_harvests_once(self):
        from unittest.mock import patch
        a = self.deadline_session(self.student)
        self.end_exam()
        with patch('judge.utils.assignment_harvest.harvest_participation') as harvest:
            payload = self.decode(native_finish(self.post_state('/api/fairplay/native/session/%s/finish' % a.pk), str(a.pk)))
        self.assertEqual(harvest.call_count, 1)
        self.assertEqual(payload['state'], 'completed')

    def test_finishing_sessions_keep_a_deadline_and_terminal_ones_do_not(self):
        a = self.deadline_session(self.student)
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='finishing')
        self.assertIsInstance(self.state_payload(a)['deadline'], int)
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='completed')
        self.assertNotIn('deadline', self.state_payload(a))

    def lock_sites_client(self, outcomes):
        from judge.utils.fairplay_client import FairPlayError
        calls = []

        class Client:
            def __init__(self, acting_user=None):
                pass

            def exam(self, key, read_timeout=None):
                calls.append(key)
                outcome = outcomes[min(len(calls), len(outcomes)) - 1]
                if outcome is None:
                    raise FairPlayError('down', 500)
                return {'settings': {'lock_sites': outcome}}
        return Client, calls

    def reset_lock_sites(self):
        from django.core.cache import cache
        for name in ('fp-browser-sites-v2', 'fp-lock-sites-good', 'fp-lock-sites-flight', 'fp-lock-sites-noeditor'):
            cache.delete('%s:%s' % (name, self.exam.pk))

    def test_last_good_sites_are_served_when_fairplay_fails(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils.fairplay_editor import refresh_lock_sites, signed_lock_sites
        self.reset_lock_sites()
        good = {'pages': ['https://docs.python.org/'], 'resources': []}
        client, calls = self.lock_sites_client([good, None])
        with patch('judge.utils.fairplay_client.FairPlayClient', client):
            refresh_lock_sites(self.exam)
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
            refresh_lock_sites(self.exam)
        self.assertEqual(len(calls), 2)
        self.assertEqual(signed_lock_sites(self.exam), good)
        self.reset_lock_sites()

    def test_a_failure_is_not_cached_for_fifteen_seconds(self):
        import time
        from unittest.mock import patch
        from judge.utils import fairplay_editor
        self.reset_lock_sites()
        good = {'pages': ['https://docs.python.org/'], 'resources': []}
        client, calls = self.lock_sites_client([None, good])
        with patch('judge.utils.fairplay_client.FairPlayClient', client), patch.object(fairplay_editor, 'LOCK_SITES_FAILURE', 1):
            fairplay_editor.refresh_lock_sites(self.exam)
            self.assertEqual(fairplay_editor.signed_lock_sites(self.exam), {'pages': [], 'resources': []})
            time.sleep(1.2)
            fairplay_editor.refresh_lock_sites(self.exam)
        self.assertEqual(fairplay_editor.signed_lock_sites(self.exam), good)
        self.reset_lock_sites()

    def test_one_asker_at_a_time_and_signing_never_calls_fairplay(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils.fairplay_editor import refresh_lock_sites, signed_lock_sites
        self.reset_lock_sites()
        client, calls = self.lock_sites_client([{'pages': ['https://docs.python.org/'], 'resources': []}])
        with patch('judge.utils.fairplay_client.FairPlayClient', client):
            cache.add('fp-lock-sites-flight:%s' % self.exam.pk, 1, 10)
            refresh_lock_sites(self.exam)
            self.assertEqual(calls, [])
            self.assertEqual(signed_lock_sites(self.exam), {'pages': [], 'resources': []})
            self.assertEqual(calls, [])
        self.reset_lock_sites()

    def test_an_exam_without_an_editor_logs_one_warning_per_hour(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils.fairplay_editor import refresh_lock_sites
        self.reset_lock_sites()
        self.exam.authors.clear()
        self.exam.curators.clear()
        client, calls = self.lock_sites_client([{'pages': [], 'resources': []}])
        with patch('judge.utils.fairplay_client.FairPlayClient', client), self.assertLogs('judge.fairplay', 'WARNING') as logs:
            refresh_lock_sites(self.exam)
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
            refresh_lock_sites(self.exam)
        self.assertEqual(len([l for l in logs.output if 'no author or curator' in l]), 1)
        self.assertEqual(calls, [])
        self.reset_lock_sites()

    def test_closed_book_exam_still_signs_the_oj_asset_origins(self):
        from django.core.cache import cache
        cache.delete('fp-oj-assets')
        cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
        payload = self.decode(self.claim())
        self.assertEqual(payload['sites'], [])
        self.assertNotIn('resources', payload)
        self.assertIn('https://cdnjs.cloudflare.com', payload['assets'])
        self.assertLessEqual(len(payload['assets']), 16)
        for origin in payload['assets']:
            self.assertRegex(origin, r'^https://[a-z0-9.-]+(:[0-9]{1,5})?$')

    def test_assets_are_absent_for_hold_and_non_active_states(self):
        payload = self.decode(self.claim())
        self.decode(native_recover(self.post_with('/api/fairplay/native/recover/fpexam', {'unclean': '1'}), self.exam.key))
        held = self.decode(native_renew(self.request('POST', '/api/fairplay/native/session/' + payload['session'] + '/renew'),
                                        payload['session']))
        self.assertIs(held['hold'], True)
        self.assertNotIn('assets', held)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(state='released')
        self.assertNotIn('assets', self.state_payload(FairPlayDeviceSession.objects.get(pk=payload['session'])))

    def test_deny_listed_and_malformed_asset_hosts_are_filtered(self):
        from django.core.cache import cache
        from judge.utils.fairplay_editor import oj_asset_origins
        cache.delete('fp-oj-assets')
        with self.settings(ACE_URL='//cdn.chatgpt.com/ace', SELECT2_JS_URL='http://insecure.example.org/x.js',
                           SELECT2_CSS_URL='//user:pw@evil.example.org/x.css', JQUERY_JS='//*.wild.example.org/j.js',
                           FONTAWESOME_CSS='//Fonts.Example.ORG:8443/fa.css', STATIC_URL='/static/', MEDIA_URL='/media/'):
            origins = oj_asset_origins()
        cache.delete('fp-oj-assets')
        self.assertIn('https://fonts.example.org:8443', origins)
        for bad in ('chatgpt', 'insecure', 'evil', 'wild'):
            self.assertFalse([o for o in origins if bad in o], bad)

    def test_asset_list_is_capped_at_sixteen_and_deduplicated(self):
        from django.core.cache import cache
        from judge.utils.fairplay_editor import oj_asset_origins
        cache.delete('fp-oj-assets')
        with self.settings(ACE_URL='//a.example.org/x', SELECT2_JS_URL='//a.example.org/y'):
            self.assertEqual(oj_asset_origins().count('https://a.example.org'), 1)
        cache.delete('fp-oj-assets')

    def test_signed_state_carries_the_newest_lock_while_ownership_stays_with_the_first(self):
        from django.core.cache import cache
        from judge.utils.fairplay_device_session import bind_lock, lock_owner
        payload = self.decode(native_claim(self.post_with('/api/fairplay/native/claim/fpexam', {'lock': 'K7QM3XPA'}), self.exam.key))
        self.assertEqual(payload['lock'], 'K7QM3XPA')
        again = self.decode(native_claim(self.post_with('/api/fairplay/native/claim/fpexam', {'lock': 'B7QM3XPA'}), self.exam.key))
        self.assertEqual(again['session'], payload['session'])
        self.assertEqual(again['lock'], 'B7QM3XPA')
        cache.clear()
        self.assertEqual(lock_owner('K7QM3XPA'), (self.exam.pk, self.student.username))
        other = FairPlayDeviceSession(user=self.teacher, contest=self.exam)
        self.assertFalse(bind_lock(other, 'K7QM3XPA'))

    def test_lock_owner_is_cached_and_dropped_on_bind_and_override(self):
        from django.core.cache import cache
        from judge.utils.fairplay_device_session import lock_owner, lock_owner_key, override_lock_owner
        self.assertIsNone(lock_owner('K7QM3XPA'))
        self.assertEqual(cache.get(lock_owner_key('K7QM3XPA')), 'none')
        with self.captureOnCommitCallbacks(execute=True):
            self.decode(native_claim(self.post_with('/api/fairplay/native/claim/fpexam', {'lock': 'K7QM3XPA'}), self.exam.key))
        self.assertIsNone(cache.get(lock_owner_key('K7QM3XPA')))
        self.assertEqual(lock_owner('K7QM3XPA'), (self.exam.pk, self.student.username))
        self.assertEqual(cache.get(lock_owner_key('K7QM3XPA')), [self.exam.pk, self.student.username])
        override_lock_owner(self.exam, 'fp_classmate', 'K7QM3XPA', self.teacher)
        self.assertIsNone(cache.get(lock_owner_key('K7QM3XPA')))
        self.assertEqual(lock_owner('K7QM3XPA'), (self.exam.pk, 'fp_classmate'))
        cache.clear()
        self.assertEqual(lock_owner('K7QM3XPA'), (self.exam.pk, 'fp_classmate'))

    def test_the_signing_key_is_decoded_once(self):
        from judge.views.fairplay_native_session import signing_key
        self.assertIs(signing_key(), signing_key())

    def test_same_device_new_sign_in_after_joining_is_a_hold_not_another_computer(self):
        self.joined()
        same_device = self.claim(cookie='new-sign-in')
        self.assertEqual((same_device.status_code, json.loads(same_device.content)['reason']), (403, 'on_hold'))
        other_device = self.claim(fingerprint='b' * 64)
        self.assertEqual((other_device.status_code, json.loads(other_device.content)['reason']), (403, 'other_device'))

    def test_good_sites_are_kept_until_the_exam_ends_not_ten_minutes(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils import fairplay_editor
        self.reset_lock_sites()
        good = {'pages': ['https://docs.python.org/'], 'resources': []}
        client, calls = self.lock_sites_client([good])
        timeouts = []
        real_set = cache.set

        def spy(key, value, timeout=None, *args, **kwargs):
            timeouts.append((key, timeout))
            return real_set(key, value, timeout, *args, **kwargs)
        with patch('judge.utils.fairplay_client.FairPlayClient', client), patch.object(cache, 'set', spy):
            fairplay_editor.refresh_lock_sites(self.exam)
        keep = [t for k, t in timeouts if k.startswith('fp-lock-sites-good:')]
        self.assertTrue(keep and keep[0] > 600)
        self.assertLessEqual(keep[0], int((self.exam.end_time - __import__('django').utils.timezone.now()).total_seconds()) + 5)
        self.reset_lock_sites()

    def test_an_unreadable_template_or_a_failing_sites_lookup_never_breaks_a_request(self):
        import tempfile
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils import fairplay_editor
        cache.delete('fp-oj-assets')
        with tempfile.TemporaryDirectory() as folder:
            with open(folder + '/mathjax-load.html', 'wb') as handle:
                handle.write(b'\xff\xfe\x00 not utf-8 \xc3\x28')
            with self.settings(TEMPLATES=[{'DIRS': [folder]}]):
                self.assertIsInstance(fairplay_editor.oj_asset_origins(), list)
        cache.delete('fp-oj-assets')
        cache.delete('fp-oj-assets-good')
        with patch.object(fairplay_editor, '_oj_asset_origins', side_effect=RuntimeError('boom')), \
                patch.object(fairplay_editor, 'signed_lock_sites', side_effect=RuntimeError('boom')):
            payload = self.decode(self.claim())
        self.assertEqual(payload['sites'], [])
        self.assertEqual(payload['assets'], [])

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_deadline_close_is_queued_once_and_a_second_contact_does_not_double_it(self):
        from unittest.mock import patch
        from judge.models import AssignmentDraft, FairPlayDeviceAudit
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        from judge.views.fairplay_native_session import close_after_deadline
        a = self.deadline_session(self.student)
        AssignmentDraft.objects.create(participation=a.participation, problem=self.short, content='{"text":"pending"}')
        self.end_exam()
        with patch.object(harvest_participation_drafts, 'delay') as delay, self.captureOnCommitCallbacks(execute=True):
            first = close_after_deadline(self.fresh(a))
            second = close_after_deadline(self.fresh(a))
        self.assertEqual((first.state, second.state), ('finishing', 'finishing'))
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(FairPlayDeviceAudit.objects.filter(session=a, action='harvest-queued').count(), 1)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    def test_a_finishing_session_is_confirmed_even_when_the_harvest_switch_is_off(self):
        from judge.views.fairplay_native_session import close_after_deadline
        a = self.deadline_session(self.student)
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='finishing')
        self.end_exam(minutes_ago=2)
        self.assertEqual(close_after_deadline(self.fresh(a)).state, 'completed')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_a_deadline_close_is_undone_when_the_deadline_moves_but_an_explicit_hand_in_is_not(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from judge.models import AssignmentDraft, Contest, FairPlayDeviceAudit
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        from judge.views.fairplay_native_session import close_after_deadline, mark_hand_in
        a = self.deadline_session(self.student)
        AssignmentDraft.objects.create(participation=a.participation, problem=self.short, content='{"text":"pending"}')
        self.end_exam()
        with patch.object(harvest_participation_drafts, 'delay'), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(close_after_deadline(self.fresh(a)).state, 'finishing')
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() + timedelta(hours=1))
        reopened = close_after_deadline(self.fresh(a))
        self.assertEqual(reopened.state, 'active')
        self.assertTrue(FairPlayDeviceAudit.objects.filter(session=a, action='deadline-extended').exists())
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='finishing')
        mark_hand_in(self.fresh(a))
        self.assertEqual(close_after_deadline(self.fresh(a)).state, 'finishing')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_a_hand_in_followed_by_the_deadline_close_is_not_undone_by_an_extension(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from judge.models import AssignmentDraft, Contest, FairPlayDeviceAudit
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        from judge.views.fairplay_native_session import close_after_deadline, mark_hand_in
        a = self.deadline_session(self.student)
        AssignmentDraft.objects.create(participation=a.participation, problem=self.short, content='{"text":"pending"}')
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='finishing')
        mark_hand_in(self.fresh(a))
        self.end_exam()
        with patch.object(harvest_participation_drafts, 'delay'), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(close_after_deadline(self.fresh(a)).state, 'finishing')
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() + timedelta(hours=1))
        self.assertEqual(close_after_deadline(self.fresh(a)).state, 'finishing')
        self.assertFalse(FairPlayDeviceAudit.objects.filter(session=a, action='deadline-extended').exists())

    def close_by_deadline(self, a):
        from unittest.mock import patch
        from judge.models import AssignmentDraft
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        from judge.views.fairplay_native_session import close_after_deadline
        AssignmentDraft.objects.create(participation=a.participation, problem=self.short, content='{"text":"pending"}')
        self.end_exam()
        with patch.object(harvest_participation_drafts, 'delay'), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(close_after_deadline(self.fresh(a)).state, 'finishing')
        harvest_participation_drafts(a.participation_id)
        self.assertEqual(close_after_deadline(self.fresh(a)).state, 'completed')

    def extend_exam(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import Contest
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() + timedelta(hours=1))
        self.exam.refresh_from_db()

    def claim_again(self):
        from judge.utils.fairplay_device_session import claim_device
        return claim_device(user_id=self.student.pk, contest=self.exam, fingerprint='a' * 64,
                            browser_digest=session_digest('student-cookie', 's' * 32))

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_editor_extension_after_the_deadline_close_lets_the_student_claim_again(self):
        from judge.models import ContestSubmission, FairPlayDeviceAudit, AssignmentDraft
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        a = self.deadline_session(self.student)
        self.close_by_deadline(a)
        submitted = ContestSubmission.objects.filter(participation=a.participation).count()
        self.assertEqual(submitted, 1)
        self.extend_exam()
        again = self.claim_again()
        self.assertEqual((again.state, again.participation_id), ('active', a.participation_id))
        self.assertNotEqual(again.pk, a.pk)
        self.assertTrue(FairPlayDeviceAudit.objects.filter(session=again, action='deadline-extended').exists())
        harvest_participation_drafts(a.participation_id)
        self.assertEqual(ContestSubmission.objects.filter(participation=a.participation).count(), submitted)
        AssignmentDraft.objects.filter(participation=a.participation).update(content='{"text":"later"}', harvested_at=None)
        self.end_exam()
        harvest_participation_drafts(a.participation_id)
        self.assertEqual(ContestSubmission.objects.filter(participation=a.participation).count(), submitted + 1)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_editor_extension_never_reopens_an_explicit_hand_in_or_a_disqualified_student(self):
        from judge.utils.fairplay_device_session import DeviceSessionDenied
        from judge.views.fairplay_native_session import mark_hand_in
        a = self.deadline_session(self.student)
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='finishing')
        mark_hand_in(self.fresh(a))
        self.close_by_deadline(a)
        self.extend_exam()
        with self.assertRaises(DeviceSessionDenied) as error:
            self.claim_again()
        self.assertEqual(error.exception.reason, 'handed_in')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_editor_extension_of_a_disqualified_student_is_refused(self):
        from judge.utils.fairplay_device_session import DeviceSessionDenied
        a = self.deadline_session(self.student)
        self.close_by_deadline(a)
        a.participation.is_disqualified = True
        a.participation.save(update_fields=['is_disqualified'])
        self.extend_exam()
        with self.assertRaises(DeviceSessionDenied) as error:
            self.claim_again()
        self.assertEqual(error.exception.reason, 'handed_in')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_claim_of_a_finishing_session_after_an_editor_extension_undoes_the_close(self):
        from unittest.mock import patch
        from judge.tasks.assignment_harvest import harvest_participation_drafts
        from judge.views.fairplay_native_session import close_after_deadline
        a = self.deadline_session(self.student)
        self.end_exam()
        with patch.object(harvest_participation_drafts, 'delay'), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(close_after_deadline(self.fresh(a)).state, 'finishing')
        self.extend_exam()
        again = self.claim_again()
        self.assertEqual((again.pk, again.state), (a.pk, 'active'))

    def test_help_page_warns_about_a_completed_session_with_an_unsubmitted_draft(self):
        from django.utils import timezone
        from judge.models import AssignmentDraft
        from judge.utils.fairplay_device_session import begin_finishing, confirm_completed
        a = self.deadline_session(self.student)
        AssignmentDraft.objects.create(participation=a.participation, problem=self.short,
                                       content='{"text":"taken"}', harvested_at=timezone.now())
        arguments = dict(session_id=a.pk, user_id=a.user_id, fingerprint=a.fingerprint, browser_digest=a.browser_digest)
        begin_finishing(**arguments)
        confirm_completed(**arguments)
        html = self.help_html()
        self.assertIn('Some answers were saved but not submitted: %s' % self.short.code, html)
        self.assertIn('harvest consistency check', html)

    def test_help_page_has_no_such_warning_for_a_normal_completion(self):
        from judge.utils.fairplay_device_session import begin_finishing, confirm_completed
        a = self.deadline_session(self.student)
        arguments = dict(session_id=a.pk, user_id=a.user_id, fingerprint=a.fingerprint, browser_digest=a.browser_digest)
        begin_finishing(**arguments)
        confirm_completed(**arguments)
        self.assertNotIn('saved but not submitted', self.help_html())

    def test_deadline_is_signed_for_active_sessions_only(self):
        a = self.deadline_session(self.student)
        payload = self.state_payload(a)
        from judge.utils.fairplay_device_session import personal_deadline
        self.assertEqual(payload['deadline'], int(personal_deadline(self.fresh(a)).timestamp()))
        self.assertIsInstance(payload['deadline'], int)
        FairPlayDeviceSession.objects.filter(pk=a.pk).update(state='released')
        self.assertNotIn('deadline', self.state_payload(a))

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    def test_failsafe_releases_after_the_grace_when_the_harvest_cannot_run(self):
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit, FairPlayDeviceSlot
        from judge.views.fairplay_native_session import close_after_deadline
        a = self.deadline_session(self.student)
        self.end_exam(minutes_ago=5)
        self.assertEqual(close_after_deadline(a).state, 'active')
        self.end_exam(minutes_ago=16)
        released = close_after_deadline(a)
        self.assertEqual(released.state, 'released')
        self.assertTrue(FairPlayDeviceAudit.objects.filter(session=a, action='auto-release-after-deadline').exists())
        self.assertIsNone(FairPlayDeviceSlot.objects.get(user=self.student).session_id)
        self.assertIsNotNone(self.fresh(a).participation_id)

    def test_every_signed_state_after_the_deadline_closes_an_unjoined_session(self):
        a = self.deadline_session(self.student, joined=False)
        self.end_exam()
        self.assertEqual(self.state_payload(a)['state'], 'released')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    def test_state_read_after_the_grace_signs_released(self):
        a = self.deadline_session(self.student)
        self.end_exam(minutes_ago=20)
        self.assertEqual(self.state_payload(a)['state'], 'released')

    HELP = '/contest/fpexam/fairplay-help'

    def quick(self, user=None, confirm=True, mode='continue', csrf=False, **extra):
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user or self.teacher)
        data = dict({'action': 'quick-unlock', 'netid': self.student.username, 'name': 'Avazbek', 'mode': mode}, **extra)
        if confirm:
            data['confirm'] = '1'
        return client.post(self.HELP, data)

    def help_html(self):
        self.client.force_login(self.teacher)
        return self.client.get(self.HELP).content.decode()

    def test_unlock_button_is_visible_per_state_without_the_menu(self):
        self.assertNotIn('fp-unlock"', self.help_html().split('<tbody>')[1].split('</tbody>')[0])
        payload = self.decode(self.claim())
        html = self.help_html()
        self.assertIn('class="fp-btn fp-unlock"', html)
        self.assertIn('data-only="1"', html)
        self.assertIn('Their saved answers are kept', html)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(state='finishing')
        self.assertIn('data-only="1"', self.help_html())
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(state='released')
        released = self.help_html()
        self.assertIn('class="fp-btn fp-unlock"', released)
        self.assertIn('data-only="0"', released)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(state='completed')
        self.assertNotIn('fp-btn fp-unlock"', self.help_html())

    def test_unlock_button_is_also_shown_for_a_held_session(self):
        payload = self.decode(self.claim())
        self.decode(native_recover(self.post_with('/api/fairplay/native/recover/fpexam', {'unclean': '1'}), self.exam.key))
        self.assertIn('data-only="1"', self.help_html())

    def test_quick_unlock_needs_the_confirmation(self):
        self.decode(self.claim())
        response = self.quick(confirm=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')

    def test_unlock_and_continue_keeps_the_attachment_and_lets_the_student_start_again(self):
        from judge.models import ContestParticipation, FairPlayDeviceAudit, Profile
        payload = self.decode(self.claim())
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
                                                            real_start=__import__('django').utils.timezone.now(), virtual=0)
        Profile.objects.filter(pk=self.student.profile.pk).update(current_contest=participation)
        self.assertEqual(self.quick(mode='continue').status_code, 302)
        session = FairPlayDeviceSession.objects.get(pk=payload['session'])
        self.assertEqual(session.state, 'released')
        self.assertEqual(sorted(FairPlayDeviceAudit.objects.filter(session=session).values_list('action', flat=True)),
                         ['reopen', 'reopen-window', 'unlock-device'])
        self.assertEqual(FairPlayDeviceAudit.objects.get(session=session, action='unlock-device').reason,
                         'Unlocked by instructor fp_teacher')
        self.assertEqual(Profile.objects.get(pk=self.student.profile.pk).current_contest_id, participation.pk)
        self.assertEqual(self.decode(self.claim(fingerprint='b' * 64, cookie='new'))['state'], 'active')

    def test_confirm_form_of_the_old_template_posts_no_mode_and_means_unlock_only(self):
        from judge.models import ContestParticipation, FairPlayDeviceAudit, Profile
        payload = self.decode(self.claim())
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
                                                            real_start=__import__('django').utils.timezone.now(), virtual=0)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(participation=participation)
        Profile.objects.filter(pk=self.student.profile.pk).update(current_contest=participation)
        self.client.force_login(self.teacher)
        confirm_page = self.client.post(self.HELP, {'action': 'quick-unlock', 'netid': self.student.username,
                                                    'name': 'Avazbek'})
        self.assertEqual(confirm_page.status_code, 200)
        legacy = {'action': 'quick-unlock', 'netid': self.student.username, 'name': 'Avazbek',
                  'reason': 'Unlocked by instructor fp_teacher', 'confirm': '1'}
        self.assertEqual(self.client.post(self.HELP, legacy).status_code, 302)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'released')
        self.assertEqual(list(FairPlayDeviceAudit.objects.values_list('action', flat=True)), ['unlock-device'])
        self.assertIsNone(Profile.objects.get(pk=self.student.profile.pk).current_contest_id)

    def test_unlock_only_ends_the_attempt_and_detaches_the_student(self):
        from judge.models import ContestParticipation, FairPlayDeviceAudit, Profile
        payload = self.decode(self.claim())
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
                                                            real_start=__import__('django').utils.timezone.now(), virtual=0)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(participation=participation)
        Profile.objects.filter(pk=self.student.profile.pk).update(current_contest=participation)
        self.assertEqual(self.quick(mode='only').status_code, 302)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'released')
        self.assertIsNone(Profile.objects.get(pk=self.student.profile.pk).current_contest_id)
        self.assertEqual(list(FairPlayDeviceAudit.objects.values_list('action', flat=True)), ['unlock-device'])

    def test_the_next_renew_after_an_unlock_is_a_signed_release_with_no_extra_step(self):
        payload = self.decode(self.claim())
        self.quick(mode='only')
        path = '/api/fairplay/native/session/' + payload['session'] + '/renew'
        result = self.decode(native_renew(self.request('POST', path), payload['session']))
        self.assertEqual(result['state'], 'released')
        self.assertNotIn('deadline', result)
        self.assertEqual(result['sites'], [])

    def test_quick_unlock_refuses_non_editors_bad_modes_and_missing_csrf(self):
        from django.contrib.auth.models import User
        payload = self.decode(self.claim())
        for user in (self.student, self.outsider, self.classmate):
            self.assertIn(self.quick(user=user).status_code, (302, 403, 404))
            self.assertEqual(FairPlayDeviceSession.objects.get(pk=payload['session']).state, 'active')
        self.assertEqual(self.quick(mode='delete').status_code, 404)
        self.assertEqual(self.quick(csrf=True).status_code, 403)
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=payload['session']).state, 'active')

    def test_default_reopen_minutes_never_shorten_the_remaining_time(self):
        from django.utils import timezone
        from judge.models import Contest
        from judge.utils.fairplay_device_session import default_reopen_minutes
        self.decode(self.claim())
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() + timezone.timedelta(minutes=100))
        self.exam.refresh_from_db()
        self.assertGreaterEqual(default_reopen_minutes(self.exam, self.student.username), 99)
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() + timezone.timedelta(minutes=10))
        self.exam.refresh_from_db()
        self.assertEqual(default_reopen_minutes(self.exam, self.student.username), 30)

    def test_fallback_form_has_a_roster_dropdown_and_an_optional_reason(self):
        from unittest.mock import Mock, patch
        html = self.help_html()
        self.assertIn('<details id="fp-mac-exit-box"', html)
        self.assertIn('A locked computer is not in the list? Use its code', html)
        self.assertIn('<select name="netid"', html)
        self.assertIn('value="%s"' % self.student.username, html.split('fp-mac-exit-box')[1])
        form = html.split('id="fp-mac-exit"')[1].split('</form>')[0]
        self.assertNotIn('name="reason" maxlength="300" required', form)
        self.bind_exit_lock()
        client = Mock()
        client.native_exit.return_value = {'code': '123456'}
        self.client.force_login(self.teacher)
        with patch('judge.views.fairplay._client', return_value=client):
            response = self.client.post(self.HELP, {'action': 'offline-exit', 'netid': self.student.username,
                                                    'mac_code': 'ABCD-2345', 'confirm': '1'})
        self.assertContains(response, '123456')
        client.native_exit.assert_called_once_with('fpexam', self.student.username, 'ABCD-2345',
                                                  'Exit code requested by instructor fp_teacher')

    def test_unlock_and_reopen_is_one_step_and_audited(self):
        from judge.models import FairPlayDeviceAudit
        self.decode(self.claim())
        self.client.force_login(self.teacher)
        data = dict(action='unlock-reopen', netid=self.student.username, reason='Computer failed', minutes='30')
        self.assertEqual(self.client.post('/contest/fpexam/fairplay-help', data).status_code, 200)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')
        self.assertEqual(self.client.post('/contest/fpexam/fairplay-help', dict(data, confirm='1')).status_code, 302)
        self.assertEqual(sorted(FairPlayDeviceAudit.objects.values_list('action', flat=True)),
                         ['reopen', 'reopen-window', 'unlock-device'])
        payload = self.decode(self.claim(fingerprint='b' * 64, cookie='new-cookie'))
        self.assertEqual(payload['state'], 'active')

    def test_unlock_and_reopen_failure_rolls_back_the_unlock(self):
        self.decode(self.claim())
        from judge.utils.fairplay_device_session import instructor_unlock_and_reopen, DeviceSessionDenied
        with self.assertRaises(DeviceSessionDenied):
            instructor_unlock_and_reopen(contest=self.exam, username=self.student.username, actor=self.teacher,
                                         minutes=0, reason='x')
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')

    def test_help_rows_say_what_to_do_next(self):
        self.decode(self.claim())
        self.client.force_login(self.teacher)
        page = self.client.get('/contest/fpexam/fairplay-help').content.decode()
        self.assertIn('data-action="unlock-reopen"', page)
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='stuck')
        page = self.client.get('/contest/fpexam/fairplay-help').content.decode()
        self.assertIn('Unlocked. Choose Reopen exam so the student can start again.', page)
        self.assertIn('data-action="reopen"', page)

    def test_refusals_are_logged_with_a_fixed_code_and_no_secret(self):
        from judge.views import fairplay_native_session as module
        module._last_logged.clear()
        request = self.request()
        request.META.pop('HTTP_X_FAIRPLAY_TLS_SIGNATURE')
        with self.assertLogs('judge.fairplay', 'WARNING') as logs:
            with self.assertRaises(Http404):
                native_claim(request, self.exam.key)
            with self.assertRaises(Http404):
                self.claim(user=self.outsider)
        text = '\n'.join(logs.output)
        self.assertRegex(text, r'refusal identity:[a-z0-9-]{1,40} on POST')
        self.assertIn('not-on-roster', text)
        self.assertIn('fp_outsider', text)
        for secret in ('p' * 32, 's' * 32, 'student-cookie', 'a' * 16):
            self.assertNotIn(secret, text)

    def test_identity_failures_are_a_bare_404_with_a_safe_label_for_any_ingress_copy(self):
        from unittest.mock import patch
        from judge.views import fairplay_native_session as module

        class Coded(ValueError):
            code = 'bad-peer'
        cases = [(ValueError('Untrusted native ingress'), 'identity:untrusted-native-ingress'),
                 (AttributeError("secret 'p' object has no attribute x"), 'identity:secret-p-object-has-no-attribute-x'),
                 (Coded('whatever s' * 4), 'identity:bad-peer'),
                 (ValueError(''), 'identity:valueerror')]
        for error, label in cases:
            module._last_logged.clear()
            request = self.request()
            with self.settings(FAIRPLAY_NATIVE_INGRESS_MODE='nginx-mtls', FAIRPLAY_NATIVE_INGRESS_SECRET='i' * 32), \
                    patch('judge.utils.fairplay_native_ingress.verified_ingress_fingerprint', side_effect=error), \
                    self.assertLogs('judge.fairplay', 'WARNING') as logs:
                with self.assertRaises(Http404):
                    module.request_identity(request)
            text = '\n'.join(logs.output)
            self.assertIn(label, text)
            for secret in ('i' * 32, 'p' * 32, 's' * 32, 'student-cookie'):
                self.assertNotIn(secret, text)

    def test_identity_module_needs_no_name_outside_the_bundle_from_the_ingress_file(self):
        import inspect
        from judge.views import fairplay_native_session as module
        self.assertNotIn('IngressRefused', inspect.getsource(module))

    def test_handed_in_exam_is_refused_with_a_clear_reason(self):
        self.decode(self.claim())
        session = FairPlayDeviceSession.objects.get()
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='done')
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(state='completed')
        refused = self.claim()
        self.assertEqual((refused.status_code, json.loads(refused.content)['reason']), (403, 'handed_in'))

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_failed_hand_in_keeps_answers_and_does_not_authorize_unlock(self):
        from unittest.mock import patch
        from judge.models import AssignmentDraft
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        self.client_request('POST', '/contest/fpexam/join')
        self.client_request('POST', '/contest/fpexam/draft/fpshort',
                            {'content': '{"text":"Keep this answer"}'})
        path = '/api/fairplay/native/session/' + payload['session'] + '/finish'
        with patch('judge.utils.assignment_harvest.create_submission', side_effect=RuntimeError('storage unavailable')):
            result = self.decode(self.client_request('POST', path, {'nonce': uuid.uuid4().hex, 'confirm': '1'}))
        self.assertEqual(result['state'], 'finishing')
        self.assertIn('Keep this answer', AssignmentDraft.objects.get().content)
        response = self.client_request('POST', '/contest/fpexam/draft/fpshort',
                                      {'content': '{"text":"Racing replacement"}'})
        self.assertEqual(response.status_code, 403)
        result = self.decode(self.client_request('POST', path, {'nonce': uuid.uuid4().hex, 'confirm': '1'}))
        self.assertEqual(result['state'], 'completed')

    def joined_device(self):
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        self.assertEqual(self.client_request('POST', '/contest/fpexam/join').status_code, 302)
        return FairPlayDeviceSession.objects.get(pk=payload['session'])

    def plain_get(self, path):
        return self.client.get(path)

    ROUTES = ('/contest/fpexam/ranking/', '/problem/fpshort', '/problem/fpshort/submissions/')

    def test_every_route_is_served_inside_the_app_and_gated_in_a_plain_browser(self):
        self.joined_device()
        for path in self.ROUTES:
            self.assertEqual(self.client_request('GET', path).status_code, 200, path)
            if path.startswith('/problem/'):
                self.assertEqual(self.plain_get(path).status_code, 404, path)
                self.assertEqual(self.client_request('GET', path, fingerprint='b' * 64).status_code, 404, path)

    def test_routes_are_served_after_the_lease_lapsed_and_after_the_deadline(self):
        from datetime import timedelta
        from django.utils import timezone
        session = self.joined_device()
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(lease_until=timezone.now() - timedelta(minutes=5))
        for path in self.ROUTES:
            self.assertEqual(self.client_request('GET', path).status_code, 200, path)
        self.exam.__class__.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(minutes=1))
        for path in ('/contest/fpexam', '/contest/fpexam/ranking/'):
            self.assertEqual(self.client_request('GET', path).status_code, 200, path)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_draft_save_with_a_lapsed_lease_is_accepted_for_the_admitted_device(self):
        from datetime import timedelta
        from django.utils import timezone
        session = self.joined_device()
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(lease_until=timezone.now() - timedelta(minutes=5))
        response = self.client_request('POST', '/contest/fpexam/draft/fpshort', {'content': '{"text":"late lease"}'})
        self.assertEqual(response.status_code, 200, response.content[:300])

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    def test_leave_with_harvest_disabled_falls_through_to_the_stock_leave(self):
        session = self.joined_device()
        response = self.client_request('POST', '/contest/fpexam/leave', {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertContains(response, 'Exam submitted')
        self.assertNotIn('Location', response)
        self.assertNotIn(b'Hand-in is unavailable', response.content)
        self.assertIsNone(self.student.profile.__class__.objects.get(pk=self.student.profile.pk).current_contest_id)
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=session.pk).state, 'completed')
        self.assertIsNone(FairPlayDeviceSlot.objects.get(user=self.student).session_id)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_leave_with_pending_drafts_falls_through_to_the_stock_leave(self):
        from unittest.mock import patch
        session = self.joined_device()
        self.client_request('POST', '/contest/fpexam/draft/fpshort', {'content': '{"text":"keep"}'})
        with patch('judge.utils.assignment_harvest.create_submission', side_effect=RuntimeError('storage')):
            response = self.client_request('POST', '/contest/fpexam/leave', {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertEqual(response.status_code, 409, response.content[:300])
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=session.pk).state, 'active')
        self.assertIsNotNone(self.student.profile.__class__.objects.get(pk=self.student.profile.pk).current_contest_id)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_failed_hand_in_answers_the_leave_form_with_a_message_page(self):
        from unittest.mock import patch
        session = self.joined_device()
        self.client_request('POST', '/contest/fpexam/draft/fpshort', {'content': '{"text":"keep"}'})
        with patch('judge.utils.assignment_harvest.create_submission', side_effect=RuntimeError('storage')):
            response = self.client_request('POST', '/contest/fpexam/leave', {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Hand-in not complete', status_code=409)
        self.assertNotEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=session.pk).state, 'active')

    def _leave_with_a_vanished_slot(self):
        from unittest.mock import patch
        from judge.utils.fairplay_device_session import DeviceSessionDenied
        session = self.joined_device()
        with patch('judge.utils.fairplay_device_session.begin_finishing', side_effect=DeviceSessionDenied('gone')):
            response = self.client_request('POST', '/contest/fpexam/leave', {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertContains(response, 'Hand-in not complete', status_code=409)
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=session.pk).state, 'active')

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_leave_with_a_vanished_slot_is_a_message_page_with_harvest_on(self):
        self._leave_with_a_vanished_slot()

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=False)
    def test_leave_with_a_vanished_slot_is_a_message_page_with_harvest_off(self):
        self._leave_with_a_vanished_slot()

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_another_device_cannot_end_the_session_through_leave(self):
        session = self.joined_device()
        other = self.client_request('POST', '/contest/fpexam/leave', {'nonce': uuid.uuid4().hex, 'confirm': '1'},
                                    fingerprint='b' * 64)
        self.assertNotContains(other, 'Exam submitted')
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=session.pk).state, 'active')

    def test_problem_pdf_is_served_to_the_admitted_device_only(self):
        from unittest.mock import patch
        self.joined_device()
        with patch('judge.views.problem.PDF_RENDERING_ENABLED', True), \
                patch('judge.views.problem.render_pdf', return_value=b'%PDF-1.4 fp'):
            self.assertEqual(self.client_request('GET', '/problem/fpcode/pdf').status_code, 200)
            self.assertEqual(self.plain_get('/problem/fpcode/pdf').status_code, 404)

    def test_plain_request_without_headers_logs_nothing_and_is_served_as_before(self):
        from django.http import HttpResponse
        from judge.native_browser_middleware import NativeBrowserAdmissionMiddleware
        request = RequestFactory().get('/contest/fpexam')
        request.user = self.student
        request.session = SimpleNamespace(session_key='c')
        with self.assertNoLogs(level='WARNING'):
            response = NativeBrowserAdmissionMiddleware(lambda r: HttpResponse('plain'))(request)
        self.assertEqual(response.content, b'plain')
        self.assertFalse(response.has_header('Cache-Control'))

    def test_reads_are_scoped_only_to_live_sessions(self):
        session = self.joined_device()
        for state in ('active', 'finishing'):
            FairPlayDeviceSession.objects.filter(pk=session.pk).update(state=state)
            self.assertEqual(self.client_request('GET', '/problem/fpshort').status_code, 200, state)
        for state in ('completed', 'released'):
            FairPlayDeviceSession.objects.filter(pk=session.pk).update(state=state)
            self.assertEqual(self.client_request('GET', '/problem/fpshort').status_code, 404, state)

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_draft_write_needs_an_in_window_session_that_is_not_held(self):
        from datetime import timedelta
        from django.utils import timezone
        from judge.models import AssignmentDraft, ContestParticipation, FairPlayDeviceAudit
        session = self.joined_device()
        self.assertEqual(self.client_request('POST', '/contest/fpexam/draft/fpshort', {'content': '{"text":"ok"}'}).status_code, 200)
        AssignmentDraft.objects.all().delete()
        body = {'content': '{"text":"x"}'}
        FairPlayDeviceAudit.objects.create(session=session, actor=self.student, action='lease-gap', reason='t')
        held = self.client_request('POST', '/contest/fpexam/draft/fpshort', body)
        self.assertEqual(held.status_code, 403, held.content[:200])
        self.assertFalse(AssignmentDraft.objects.exists())
        FairPlayDeviceAudit.objects.all().delete()
        ContestParticipation.objects.filter(pk=session.participation_id).update(
            real_start=timezone.now() - timedelta(minutes=100))
        late = self.client_request('POST', '/contest/fpexam/draft/fpshort', body)
        self.assertNotEqual(late.status_code, 200)
        self.assertFalse(AssignmentDraft.objects.exists())

    @override_settings(ASSIGNMENT_HARVEST_ENABLED=True)
    def test_unlisted_post_does_not_take_the_account_lock(self):
        from unittest.mock import patch
        from django.db.models.query import QuerySet
        self.joined_device()
        with patch.object(QuerySet, 'select_for_update', side_effect=AssertionError('locked')):
            self.client_request('POST', '/accounts/logout/')

    def test_native_finish_with_harvest_disabled_completes_and_keeps_drafts(self):
        from judge.models import AssignmentDraft
        session = self.joined_device()
        self.client_request('POST', '/contest/fpexam/draft/fpshort', {'content': '{"text":"keep"}'})
        path = '/api/fairplay/native/session/%s/finish' % session.pk
        response = self.client_request('POST', path, {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertEqual(self.decode(response)['state'], 'completed')
        self.assertTrue(AssignmentDraft.objects.exists())

    def test_server_side_launch_problems_return_a_reason_but_roster_stays_a_404(self):
        from unittest.mock import patch
        with patch('judge.views.fairplay_launch.student_can_launch', return_value=False), \
                patch('judge.utils.fairplay.fairplay_enabled', return_value=False):
            refused = self.claim()
        self.assertEqual(refused.status_code, 403)
        self.assertIn('not set up', json.loads(refused.content)['error'])
        with self.assertRaises(Http404):
            self.claim(user=self.outsider)

    def test_leave_button_never_blocks_on_a_failed_draft_save(self):
        from pathlib import Path
        source = Path('templates/contest/media-js.html').read_text()
        self.assertNotIn('has not reached DKU OJ', source)
        self.assertIn('setTimeout(leaveOnce, 5000)', source)

    def test_abort_is_signed_and_releases_only_own_device(self):
        payload = self.decode(self.claim())
        path = '/api/fairplay/native/session/' + payload['session'] + '/abort'
        request = self.request('POST', path)
        request.POST = request.POST.copy()
        request.POST['confirm'] = '1'
        self.assertEqual(self.decode(native_abort(request, payload['session']))['state'], 'released')
        from judge.models import FairPlayDeviceSlot
        self.assertIsNone(FairPlayDeviceSlot.objects.get(user=self.student).session_id)

    def test_browser_context_cannot_clear_native_participation(self):
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam', {'nonce': uuid.uuid4().hex}))
        self.client_request('POST', '/contest/fpexam/join')
        record = FairPlayDeviceSession.objects.get(pk=payload['session'])
        self.assertIsNotNone(record.participation_id)
        # Normal requests do not possess native proof; they must not erase global ownership.
        self.client.get('/api/fairplay/browser-context', {'nonce': uuid.uuid4().hex})
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.current_contest_id, record.participation_id)
        self.assertEqual(self.client_request('GET', '/problem/fpshort').status_code, 200)

    def editor_client(self, editors):
        from judge.utils.fairplay_client import FairPlayError

        class Fake:
            def __init__(self, acting_user=None):
                self.acting_user = acting_user

            def exam(self, key, read_timeout=None):
                if self.acting_user not in editors:
                    raise FairPlayError('You are not an editor of this exam in FairPlay yet', 403)
                return {'settings': {'websites_input': ['chatgpt.com', 'https://docs.python.org/'],
                                     'lock_sites': {'pages': ['https://docs.python.org/'], 'resources': ['https://cdn.example.org']}}}
        return Fake

    def test_sites_are_read_as_a_synced_editor_of_the_exam(self):
        from unittest.mock import patch
        from django.core.cache import cache
        cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
        try:
            with patch('judge.utils.fairplay_client.FairPlayClient', self.editor_client({'fp_teacher'})):
                payload = self.decode(self.claim())
            self.assertEqual(payload['sites'], ['https://docs.python.org/'])
            self.assertEqual(payload['resources'], ['https://cdn.example.org'])
            self.assertNotIn('chatgpt', json.dumps(payload))
            self.assertEqual((payload['state'], payload['contest']), ('active', 'fpexam'))
        finally:
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)

    def test_exam_without_author_or_curator_has_no_sites_and_no_error(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils.fairplay_editor import acting_editor_username
        self.exam.authors.clear()
        self.exam.curators.clear()
        self.assertIsNone(acting_editor_username(self.exam))
        cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
        try:
            with patch('judge.utils.fairplay_client.FairPlayClient', self.editor_client({'fp_teacher'})):
                payload = self.decode(self.claim())
            self.assertEqual(payload['sites'], [])
        finally:
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)

    def test_curator_is_used_when_there_is_no_author(self):
        from judge.utils.fairplay_editor import acting_editor_username
        self.exam.curators.add(self.teacher.profile)
        self.exam.authors.clear()
        self.assertEqual(acting_editor_username(self.exam), 'fp_teacher')

    def test_v1_browser_context_never_carries_sites(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.views.fairplay_browser_context import browser_context
        self.joined()
        self.student.profile.refresh_from_db()
        cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
        request = RequestFactory().get('/api/fairplay/browser-context', {'nonce': 'a' * 32})
        request.user, request.profile, request.session = self.student, self.student.profile, {}
        try:
            with patch('judge.utils.fairplay_client.FairPlayClient', self.editor_client({'fp_teacher'})):
                payload = self.decode(browser_context(request))
            self.assertEqual(payload['sites'], [])
            self.assertNotIn('resources', payload)
            self.assertEqual(payload['state'], 'active')
        finally:
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)

    def test_resources_only_with_pages_and_never_raw_typed_sites(self):
        from judge.utils.fairplay_editor import clean_lock_sites
        self.assertEqual(clean_lock_sites({'pages': [], 'resources': ['https://cdn.example.org']}),
                         {'pages': [], 'resources': []})
        self.assertEqual(clean_lock_sites({'pages': ['http://x.org/', 'https://a b/', 'https://ok.org/'],
                                           'resources': ['https://cdn.example.org:8443', 'javascript:1', 'https://*.org']}),
                         {'pages': ['https://ok.org/'], 'resources': ['https://cdn.example.org:8443']})
        self.assertEqual(clean_lock_sites(None), {'pages': [], 'resources': []})
        self.assertEqual(len(clean_lock_sites({'pages': ['https://a%d.org/' % i for i in range(60)],
                                               'resources': ['https://r%d.org' % i for i in range(60)]})['pages']) +
                         len(clean_lock_sites({'pages': ['https://a%d.org/' % i for i in range(60)],
                                               'resources': ['https://r%d.org' % i for i in range(60)]})['resources']), 32)

    def test_wildcard_resource_grammar(self):
        from judge.utils.fairplay_editor import clean_lock_sites
        good = ['https://*.boxcloud.com', 'https://*.duosecurity.com', 'https://cdn.example.org:8443']
        bad = ['https://*.com', 'https://*.co.uk', 'https://*.org', 'https://*.edu', 'https://*.github.io', 'https://*.*.x.org',
               'https://*.x.org/path', 'https://*.x.org?q=1', 'https://*.x.org:443', 'https://a.*.x.org', 'http://*.x.org',
               '*.x.org', 'https://*x.org', 'https://*.']
        out = clean_lock_sites({'pages': ['https://ok.org/'], 'resources': good + bad})
        self.assertEqual(out['resources'], good)
        self.assertEqual(clean_lock_sites({'pages': ['https://*.x.org/', 'https://ok.org/'], 'resources': []})['pages'],
                         ['https://ok.org/'])
        self.assertEqual(clean_lock_sites({'pages': [], 'resources': good}), {'pages': [], 'resources': []})

    def test_signed_context_without_lock_sites_from_fairplay_is_closed_book(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.utils.fairplay_client import FairPlayError

        class Down:
            def __init__(self, acting_user=None):
                pass

            def exam(self, key, read_timeout=None):
                raise FairPlayError('down', 500)
        cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)
        try:
            with patch('judge.utils.fairplay_client.FairPlayClient', Down):
                payload = self.decode(self.claim())
            self.assertEqual(payload['sites'], [])
            self.assertNotIn('resources', payload)
        finally:
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)

    def test_reference_policy_is_signed_and_removed_on_release(self):
        from django.core.cache import cache
        cache.set('fp-browser-sites-v2:%s' % self.exam.pk, {'pages': ['https://docs.python.org/'], 'resources': ['https://cdn.example.org']}, 15)
        try:
            payload = self.decode(self.claim())
            self.assertEqual(payload['sites'], ['https://docs.python.org/'])
            instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Test release')
            terminal = self.decode(native_state(self.request('GET', '/api/fairplay/native/session/' + payload['session']), payload['session']))
            self.assertEqual(terminal['sites'], [])
            self.assertNotIn('resources', terminal)
        finally:
            cache.delete('fp-browser-sites-v2:%s' % self.exam.pk)

    def test_recovery_reauthenticates_same_device_after_lost_cookie(self):
        payload = self.decode(self.claim())
        path = '/api/fairplay/native/recover/fpexam'
        result = self.decode(native_recover(self.request('POST', path, cookie='new-login'), self.exam.key))
        self.assertEqual(result['session'], payload['session'])
        with self.assertRaises(Http404):
            native_state(self.request('GET', '/api/fairplay/native/session/' + payload['session']), payload['session'])
        other = native_recover(self.request('POST', path, fingerprint='b' * 64), self.exam.key)
        self.assertEqual(other.status_code, 403)
        self.assertNotIn('payload', json.loads(other.content))
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Recovery test')
        result = self.decode(native_recover(self.request('POST', path, cookie='third-login'), self.exam.key))
        self.assertEqual(result['state'], 'released')
        self.assertEqual(self.decode(native_recover(self.request('POST', path, fingerprint='b' * 64), self.exam.key))['state'], 'rejected')

    def post_with(self, path, extra, **kwargs):
        request = self.request('POST', path, **kwargs)
        request.POST = request.POST.copy()
        request.POST.update(extra)
        return request

    def test_abort_refused_after_join_and_allowed_before(self):
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam', {'nonce': uuid.uuid4().hex}))
        self.client_request('POST', '/contest/fpexam/join')
        path = '/api/fairplay/native/session/' + payload['session'] + '/abort'
        response = self.client_request('POST', path, {'nonce': uuid.uuid4().hex, 'confirm': '1'})
        self.assertEqual(response.status_code, 409)
        record = FairPlayDeviceSession.objects.get(pk=payload['session'])
        self.assertEqual(record.state, 'active')
        self.assertIsNotNone(record.participation_id)
        from judge.models import FairPlayDeviceAudit, FairPlayDeviceSlot
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='abort').exists())
        self.assertEqual(FairPlayDeviceSlot.objects.get(user=self.student).session_id, record.pk)

    def test_recover_has_the_same_gate_as_claim(self):
        self.decode(self.claim())
        path = '/api/fairplay/native/recover/'
        with self.assertRaises(Http404):
            native_recover(self.request('POST', path + 'no-such-key'), 'no-such-key')
        self.exam.is_visible = False
        self.exam.save(update_fields=['is_visible'])
        with self.assertRaises(Http404):
            native_recover(self.request('POST', path + self.exam.key), self.exam.key)
        refused = native_recover(self.request('POST', path + self.exam.key, user=self.teacher), self.exam.key)
        self.assertEqual(refused.status_code, 403)

    def test_instructor_gets_a_clear_unsigned_refusal_and_no_session(self):
        for view in (native_claim, native_recover):
            for visible in (True, False):
                self.exam.is_visible = visible
                self.exam.save(update_fields=['is_visible'])
                response = view(self.request(user=self.teacher), self.exam.key)
                body = json.loads(response.content)
                self.assertEqual((response.status_code, body['reason']), (403, 'instructor'))
                self.assertIn('instructor of this exam', body['error'])
                self.assertNotIn('payload', body)
        self.assertEqual(FairPlayDeviceSession.objects.count(), 0)

    def test_exam_not_started_says_so_and_creates_no_session(self):
        from datetime import timedelta
        from django.utils import timezone
        self.exam.start_time = timezone.now() + timedelta(hours=2)
        self.exam.end_time = timezone.now() + timedelta(hours=4)
        self.exam.save(update_fields=['start_time', 'end_time'])
        response = self.claim()
        body = json.loads(response.content)
        self.assertEqual((response.status_code, body['reason']), (403, 'not_open'))
        self.assertIn('not started yet', body['error'])
        self.assertEqual(FairPlayDeviceSession.objects.count(), 0)

    def test_hidden_exam_stays_404_for_students(self):
        self.exam.is_visible = False
        self.exam.save(update_fields=['is_visible'])
        with self.assertRaises(Http404):
            self.claim()

    def held(self, payload):
        return FairPlayDeviceSession.objects.get(pk=payload['session'])

    def test_unclean_recover_creates_hold_that_blocks_renew_and_claim(self):
        from judge.models import FairPlayDeviceAudit
        payload = self.decode(self.claim())
        self.assertNotIn('hold', payload)
        path = '/api/fairplay/native/recover/fpexam'
        lease = self.held(payload).lease_until
        result = self.decode(native_recover(self.post_with(path, {'unclean': '1'}), self.exam.key))
        self.assertIs(result['hold'], True)
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lease-gap').count(), 1)
        gap_lease = self.held(payload).lease_until
        self.assertLess(gap_lease, lease)
        renewed = self.decode(native_renew(self.request('POST', '/api/fairplay/native/session/' + payload['session'] + '/renew'),
                                            payload['session']))
        self.assertIs(renewed['hold'], True)
        self.assertEqual(self.held(payload).lease_until, gap_lease)
        self.assertEqual(native_claim(self.request(), self.exam.key).status_code, 403)
        self.assertEqual(FairPlayDeviceSession.objects.count(), 1)

    def test_expired_lease_with_new_digest_creates_hold_but_same_digest_does_not(self):
        from judge.models import FairPlayDeviceAudit
        from django.utils import timezone
        payload = self.decode(self.claim())
        path = '/api/fairplay/native/recover/fpexam'
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(lease_until=timezone.now() - timezone.timedelta(seconds=5))
        result = self.decode(native_recover(self.request('POST', path), self.exam.key))
        self.assertNotIn('hold', result)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='lease-gap').exists())
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(lease_until=timezone.now() - timezone.timedelta(seconds=5))
        result = self.decode(native_recover(self.request('POST', path, cookie='new-login'), self.exam.key))
        self.assertIs(result['hold'], True)
        self.assertTrue(FairPlayDeviceAudit.objects.filter(action='lease-gap').exists())

    def test_digest_change_with_live_lease_does_not_hold_before_join(self):
        payload = self.decode(self.claim())
        result = self.decode(native_recover(self.request('POST', '/api/fairplay/native/recover/fpexam', cookie='new-login'), self.exam.key))
        self.assertNotIn('hold', result)

    def joined(self):
        self.client.force_login(self.student)
        payload = self.decode(self.client_request('POST', '/api/fairplay/native/claim/fpexam',
                                                  {'nonce': uuid.uuid4().hex}))
        self.client_request('POST', '/contest/fpexam/join')
        return payload

    def test_digest_change_within_lease_holds_a_joined_session(self):
        from judge.models import FairPlayDeviceAudit
        payload = self.joined()
        self.assertIsNotNone(self.held(payload).participation_id)
        self.assertGreater(self.held(payload).lease_until, __import__('django').utils.timezone.now())
        result = self.decode(native_recover(self.request('POST', '/api/fairplay/native/recover/fpexam', cookie='new-login'), self.exam.key))
        self.assertIs(result['hold'], True)
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lease-gap').count(), 1)

    def test_joined_session_without_digest_change_is_not_held(self):
        payload = self.joined()
        result = self.decode(native_recover(self.request('POST', '/api/fairplay/native/recover/fpexam', cookie=self.client.session.session_key), self.exam.key))
        self.assertNotIn('hold', result)

    def test_claim_records_the_mac_lock_and_refuses_it_for_someone_else(self):
        from judge.models import FairPlayDeviceAudit
        payload = self.decode(native_claim(self.post_with('/api/fairplay/native/claim/fpexam', {'lock': 'k7qm-3xpa'}), self.exam.key))
        audit = FairPlayDeviceAudit.objects.get(action='lock')
        self.assertEqual((str(audit.session_id), audit.reason), (payload['session'], 'K7QM3XPA'))
        from judge.utils.fairplay_device_session import bind_lock
        other = FairPlayDeviceSession.objects.exclude(pk=payload['session']).first() or FairPlayDeviceSession(
            user=self.teacher, contest=self.exam)
        other.user = self.teacher
        self.assertFalse(bind_lock(other, 'K7QM3XPA'))
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lock').count(), 1)

    def test_conflicting_lock_commits_no_session_and_two_binds_leave_one_owner(self):
        from judge.models import FairPlayDeviceAudit
        from judge.utils.fairplay_device_session import bind_lock, lock_owner
        with self.captureOnCommitCallbacks(execute=True):
            first = self.decode(native_claim(self.post_with('/api/fairplay/native/claim/fpexam', {'lock': 'K7QM3XPA'}), self.exam.key))
        self.assertEqual(lock_owner('K7QM3XPA'), (self.exam.pk, self.student.username))
        before = FairPlayDeviceSession.objects.count()
        victim = FairPlayDeviceSession(user=self.teacher, contest=self.exam)
        self.assertFalse(bind_lock(victim, 'K7QM3XPA'))
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lock').count(), 1)
        self.assertEqual(FairPlayDeviceSession.objects.count(), before)
        self.assertEqual(first['user'], str(self.student.pk))

    def test_signed_context_carries_the_bound_lock(self):
        bound = self.decode(native_claim(self.post_with('/api/fairplay/native/claim/fpexam', {'lock': 'k7qm-3xpa'}), self.exam.key))
        self.assertEqual(bound['lock'], 'K7QM3XPA')
        again = self.decode(native_renew(self.request('POST', '/api/fairplay/native/session/' + bound['session'] + '/renew'), bound['session']))
        self.assertEqual(again['lock'], 'K7QM3XPA')

    def test_signed_context_has_no_lock_key_without_a_binding(self):
        self.assertNotIn('lock', self.decode(self.claim()))

    def test_expired_active_lease_is_flagged_once_and_shown_to_the_instructor(self):
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        payload = self.joined()
        cookie = self.client.session.session_key
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(lease_until=timezone.now() - timezone.timedelta(seconds=5))
        note = 'dropped during the exam'
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/contest/fpexam/fairplay-help'), note)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='lease-expired').exists())
        result = self.decode(native_recover(self.request('POST', '/api/fairplay/native/recover/fpexam', cookie=cookie), self.exam.key))
        self.assertNotIn('hold', result)
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lease-expired').count(), 1)
        self.assertContains(self.client.get('/contest/fpexam/fairplay-help'), note)

    def test_normal_renew_adds_no_audit_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        payload = self.joined()
        cookie = self.client.session.session_key
        path = '/api/fairplay/native/session/' + payload['session'] + '/renew'
        with CaptureQueriesContext(connection) as queries:
            native_renew(self.request('POST', path, cookie=cookie), payload['session'])
        self.assertFalse([q for q in queries if 'lease-expired' in q['sql']])
        self.assertLessEqual(len(queries), 10, [q['sql'] for q in queries])
        print('renew queries:', len(queries))

    def renew(self, payload):
        return self.decode(native_renew(self.request('POST', '/api/fairplay/native/session/' + payload['session'] + '/renew',
                                                      cookie=self.client.session.session_key), payload['session']))

    def test_two_renews_keep_a_ninety_second_lease(self):
        from django.utils import timezone
        from judge.utils.fairplay_device_session import LEASE_SECONDS
        self.assertEqual(LEASE_SECONDS, 90)
        payload = self.joined()
        for _ in range(2):
            before = timezone.now()
            result = self.renew(payload)
            lease = self.held(payload).lease_until
            self.assertGreaterEqual(lease, before + timezone.timedelta(seconds=90))
            self.assertLessEqual(lease, timezone.now() + timezone.timedelta(seconds=90))
            self.assertEqual(result['state'], 'active')
            self.assertNotIn('hold', result)
            self.assertLessEqual(result['lease_until'], result['expires'])

    def test_renew_after_ninety_one_seconds_of_silence_flags_lease_expired(self):
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        payload = self.joined()
        self.renew(payload)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(lease_until=timezone.now() - timezone.timedelta(seconds=1))
        self.renew(payload)
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lease-expired').count(), 1)

    def test_renew_inside_the_lease_does_not_flag(self):
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        payload = self.joined()
        self.renew(payload)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(lease_until=timezone.now() + timezone.timedelta(seconds=60))
        self.renew(payload)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='lease-expired').exists())

    def test_row_locks_use_only_portable_arguments(self):
        # The live database is MariaDB: select_for_update(of=...), nowait and skip_locked are not
        # supported there and raise on every call, while SQLite ignores them, so scan the source.
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        for name in ('utils/fairplay_device_session.py', 'views/fairplay_native_session.py'):
            for call in re.findall(r'select_for_update\(([^)]*)\)', (root / name).read_text()):
                self.assertEqual(call.strip(), '', name)

    def test_renew_marks_request_to_skip_the_profile_write(self):
        payload = self.joined()
        request = self.request('POST', '/api/fairplay/native/session/' + payload['session'] + '/renew',
                               cookie=self.client.session.session_key)
        native_renew(request, payload['session'])
        self.assertIs(request.no_profile_update, True)

    def test_renew_of_an_unknown_session_is_404(self):
        from uuid import uuid4
        from django.http import Http404
        self.joined()
        with self.assertRaises(Http404):
            native_renew(self.request('POST', '/api/fairplay/native/session/x/renew',
                                      cookie=self.client.session.session_key), str(uuid4()))

    def test_renew_flags_an_expired_lease(self):
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        payload = self.joined()
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(lease_until=timezone.now() - timezone.timedelta(seconds=5))
        native_renew(self.request('POST', '/api/fairplay/native/session/' + payload['session'] + '/renew', cookie=self.client.session.session_key), payload['session'])
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='lease-expired').count(), 1)

    def test_unlock_then_reopen_clears_hold_and_new_claim_works(self):
        from judge.utils.fairplay_device_session import instructor_reopen
        payload = self.decode(self.claim())
        self.decode(native_recover(self.post_with('/api/fairplay/native/recover/fpexam', {'unclean': '1'}), self.exam.key))
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Restarted')
        instructor_reopen(contest=self.exam, username=self.student.username, actor=self.teacher, minutes=15, reason='Continue')
        fresh = self.decode(self.claim())
        self.assertEqual(fresh['state'], 'active')
        self.assertNotIn('hold', fresh)
        self.assertNotEqual(fresh['session'], payload['session'])
        renewed = self.decode(native_renew(self.request('POST', '/api/fairplay/native/session/' + fresh['session'] + '/renew'),
                                            fresh['session']))
        self.assertNotIn('hold', renewed)

    def test_help_page_shows_hold_note(self):
        self.decode(self.claim())
        self.client.force_login(self.teacher)
        note = 'FairPlay restarted on this device: unlock the device, then reopen to let the student continue.'
        self.assertNotContains(self.client.get('/contest/fpexam/fairplay-help'), note)
        self.decode(native_recover(self.post_with('/api/fairplay/native/recover/fpexam', {'unclean': '1'}), self.exam.key))
        self.assertContains(self.client.get('/contest/fpexam/fairplay-help'), note)

    def bind_exit_lock(self, username=None):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from judge.models import FairPlayDeviceAudit
        user = User.objects.get(username=username or self.student.username)
        session = FairPlayDeviceSession.objects.create(user=user, contest=self.exam, fingerprint='a' * 64,
                                                        browser_digest='b' * 64, lease_until=timezone.now())
        FairPlayDeviceAudit.objects.create(session=session, actor=user, action='lock', reason='ABCD2345')

    def exit_post(self, client):
        from unittest.mock import patch
        self.bind_exit_lock()
        self.client.force_login(self.teacher)
        with patch('judge.views.fairplay._client', return_value=client):
            return self.client.post('/contest/fpexam/fairplay-help', {'action': 'offline-exit',
                'netid': self.student.username, 'reason': 'Lock stuck', 'mac_code': 'ABCD-2345', 'confirm': '1'})

    def exit_messages(self, response):
        from django.contrib.messages import get_messages
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_native_exit_is_tried_first_and_code_is_shown(self):
        from unittest.mock import Mock
        client = Mock()
        client.native_exit.return_value = {'code': '123456'}
        response = self.exit_post(client)
        self.assertContains(response, '123456')
        client.native_exit.assert_called_once_with('fpexam', self.student.username, 'ABCD-2345', 'Lock stuck')
        client.offline_exit.assert_not_called()

    def test_native_exit_404_falls_back_to_offline_exit_outside_native_mode(self):
        from unittest.mock import Mock
        from judge.utils.fairplay_client import FairPlayError
        client = Mock()
        client.native_exit.side_effect = FairPlayError('none', status=404)
        client.offline_exit.return_value = {'code': '654321'}
        with self.settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=False):
            self.assertContains(self.exit_post(client), '654321')
        client.offline_exit.assert_called_once_with('fpexam', self.student.username, 'ABCD-2345')

    def test_native_exit_404_in_native_mode_has_no_classic_fallback(self):
        from unittest.mock import Mock
        from judge.utils.fairplay_client import FairPlayError
        client = Mock()
        client.native_exit.side_effect = FairPlayError('Unknown or expired Mac code', status=404)
        response = self.exit_post(client)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any('No FairPlay lock was registered for that code' in m for m in self.exit_messages(response)))
        client.offline_exit.assert_not_called()

    def test_exit_409_shows_server_detail_and_distinct_messages(self):
        from unittest.mock import Mock
        from judge.utils.fairplay_client import FairPlayError
        client = Mock()
        client.native_exit.side_effect = FairPlayError('DKU OJ installation identity does not match', status=409)
        self.assertTrue(any('installation identity' in m for m in self.exit_messages(self.exit_post(client))))
        client.native_exit.side_effect = FairPlayError('This Mac code belongs to another student or exam', status=409)
        self.assertTrue(any('another student' in m for m in self.exit_messages(self.exit_post(client))))

    def exit_post_for(self, client, netid):
        from unittest.mock import patch
        from django.contrib.auth.models import User
        if netid == self.student.username:
            self.bind_exit_lock(netid)
        self.client.force_login(self.teacher)
        with patch('judge.views.fairplay._client', return_value=client):
            return self.client.post('/contest/fpexam/fairplay-help', {'action': 'offline-exit',
                'netid': netid, 'reason': 'Lock stuck', 'mac_code': 'ABCD-2345', 'confirm': '1'})

    def test_exit_refused_for_editor_and_non_student_but_allowed_without_device_slot(self):
        from unittest.mock import Mock
        for netid in (self.teacher.username, self.outsider.username, 'no-such-netid'):
            client = Mock()
            response = self.exit_post_for(client, netid)
            self.assertEqual(response.status_code, 302, netid)
            self.assertTrue(self.exit_messages(response), netid)
            client.native_exit.assert_not_called()
            client.offline_exit.assert_not_called()
        from judge.models import FairPlayDeviceSlot
        self.assertFalse(FairPlayDeviceSlot.objects.filter(user=self.student).exists())
        client = Mock()
        client.native_exit.return_value = {'code': '123456'}
        self.assertContains(self.exit_post_for(client, self.student.username), '123456')

    def test_native_exit_errors_show_a_message_not_a_500(self):
        from unittest.mock import Mock
        from judge.utils.fairplay_client import FairPlayError
        for status, text in ((409, 'conflict'), (429, 'Too many'), (503, 'exit key'), (422, 'did not accept')):
            client = Mock()
            client.native_exit.side_effect = FairPlayError('', status=status)
            response = self.exit_post(client)
            self.assertEqual(response.status_code, 302, status)
            self.assertTrue(any(text in m for m in self.exit_messages(response)), status)
            client.offline_exit.assert_not_called()

    def test_client_native_exit_is_a_signed_post(self):
        from unittest.mock import patch
        from judge.utils.fairplay_client import FairPlayClient
        with patch.object(FairPlayClient, 'request', return_value={'code': '123456'}) as call:
            self.assertEqual(FairPlayClient().native_exit('fpexam', 'netid1', 'ABCD', 'why'), {'code': '123456'})
        call.assert_called_once_with('POST', '/exams/fpexam/students/netid1/native-exit', {'mac_code': 'ABCD', 'reason': 'why'})

    def test_exit_code_is_reachable_in_native_mode_for_editor_only(self):
        from unittest.mock import Mock
        self.decode(self.claim())
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/contest/fpexam/fairplay-help'), 'data-action="offline-exit"')
        client = Mock()
        client.native_exit.return_value = {'code': '123456'}
        self.assertContains(self.exit_post(client), '123456')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/contest/fpexam/fairplay-help').status_code, 404)
        from unittest.mock import patch
        with patch('judge.views.fairplay._client', return_value=client):
            response = self.client.post('/contest/fpexam/fairplay-help', {'action': 'offline-exit',
                'netid': self.student.username, 'reason': 'x', 'mac_code': 'ABCD', 'confirm': '1'})
        self.assertEqual(response.status_code, 404)
        client.native_exit.assert_called_once()

    def test_reopen_http_uses_native_attempt_without_legacy_service(self):
        from unittest.mock import patch
        self.decode(self.claim())
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Recovery test')
        self.client.force_login(self.teacher)
        with patch('judge.views.fairplay._client', side_effect=AssertionError('Legacy service must not be used')):
            response = self.client.post('/contest/fpexam/fairplay-help',
                {'action': 'reopen', 'netid': self.student.username, 'minutes': '15', 'reason': 'Native resume', 'confirm': '1'})
        self.assertEqual(response.status_code, 302)
        reopened = self.decode(self.claim())
        self.assertEqual(reopened['state'], 'active')
        self.assertEqual(FairPlayDeviceSession.objects.count(), 2)

    def test_native_instructor_help_uses_native_history_and_hides_codes(self):
        from unittest.mock import patch
        self.decode(self.claim())
        self.client.force_login(self.teacher)
        with patch('judge.views.fairplay._client', side_effect=AssertionError('Native help must not use legacy codes')):
            active = self.client.get('/contest/fpexam/fairplay-help')
            self.assertContains(active, 'data-action="unlock-device"')
            self.assertNotContains(active, 'data-action="reissue"')
            self.assertNotContains(active, 'Start code')
            instructor_unlock(contest=self.exam, username=self.student.username,
                              actor=self.teacher, reason='Device recovery')
            closed = self.client.get('/contest/fpexam/fairplay-help')
            self.assertContains(closed, self.student.username)
            self.assertContains(closed, 'data-action="reopen"')
            self.assertNotContains(closed, 'data-action="unlock-device"')

    def test_native_participation_page_has_no_access_code_download(self):
        self.client.force_login(self.teacher)
        response = self.client.get('/contest/fpexam/participations')
        self.assertContains(response, 'FairPlay required')
        self.assertNotContains(response, 'Download access codes')
        self.assertNotContains(response, 'strong lock')


from django.test import TransactionTestCase  # noqa: E402


@FAIRPLAY_ON
@override_settings(DEBUG=True, FAIRPLAY_NATIVE_BROWSER_ENABLED=True,
    FAIRPLAY_NATIVE_LOOPBACK_FIXTURE=True, FAIRPLAY_NATIVE_PROXY_SECRET='p' * 32,
    FAIRPLAY_NATIVE_SESSION_BINDING_SECRET='s' * 32, FAIRPLAY_NATIVE_PROXY_PEERS=['127.0.0.1'],
    FAIRPLAY_BROWSER_SIGNING_KEY=base64.b64encode(b'k' * 32).decode(), ASSIGNMENT_HARVEST_ENABLED=True)
class AtomicScopeTests(FairPlayDataMixin, TransactionTestCase):
    """No outer test transaction: a select_for_update outside atomic, or a cache call inside it, shows up here."""
    request = NativeSessionAPITests.request
    decode = NativeSessionAPITests.decode
    claim = NativeSessionAPITests.claim
    post_with = NativeSessionAPITests.post_with

    def setUp(self):
        type(self).setUpTestData()
        super().setUp()

    def watch(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from django.core.cache.backends.locmem import LocMemCache
        from django.db import connection
        from django.db.models.query import QuerySet
        self.violations = []
        violations = self.violations
        stack = ExitStack()
        for name in ('get', 'set', 'add', 'delete', 'get_many'):
            real = getattr(LocMemCache, name)

            def wrapper(cache_self, *args, _real=real, _name=name, **kwargs):
                if connection.in_atomic_block:
                    violations.append('cache.%s(%s) inside atomic' % (_name, args[:1]))
                return _real(cache_self, *args, **kwargs)
            stack.enter_context(patch.object(LocMemCache, name, wrapper))
        real_sfu = QuerySet.select_for_update

        def sfu(queryset, *args, **kwargs):
            if not connection.in_atomic_block:
                violations.append('select_for_update outside atomic')
            return real_sfu(queryset, *args, **kwargs)
        stack.enter_context(patch.object(QuerySet, 'select_for_update', sfu))
        return stack

    def test_views_touch_the_cache_before_the_lock_and_lock_only_inside_atomic(self):
        from judge.views.fairplay_native_session import native_abort
        with self.watch():
            payload = self.decode(self.claim())
            path = '/api/fairplay/native/session/' + payload['session']
            self.decode(native_renew(self.request('POST', path + '/renew'), payload['session']))
            self.decode(native_state(self.request('GET', path), payload['session']))
            self.decode(native_recover(self.post_with('/api/fairplay/native/recover/fpexam', {}), self.exam.key))
            instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Test')
        self.assertEqual(self.violations, [])

    def test_leave_and_abort_and_instructor_paths_run_inside_atomic(self):
        from judge.views.fairplay_native_session import finish_from_website, native_abort
        with self.watch():
            payload = self.decode(self.claim())
            request = self.post_with('/api/fairplay/native/session/%s/abort' % payload['session'],
                                     {'nonce': uuid.uuid4().hex, 'confirm': '1'})
            self.decode(native_abort(request, payload['session']))
            from django.utils import timezone
            from judge.models import ContestParticipation
            participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
                                                                real_start=timezone.now(), virtual=0)
            self.assertIsNone(finish_from_website(self.request('POST', '/contest/fpexam/leave'), participation))
        self.assertEqual([v for v in self.violations if 'select_for_update' in v], [])


@FAIRPLAY_ON
@override_settings(DEBUG=True, FAIRPLAY_NATIVE_BROWSER_ENABLED=True,
    FAIRPLAY_NATIVE_LOOPBACK_FIXTURE=True, FAIRPLAY_NATIVE_PROXY_SECRET='p' * 32,
    FAIRPLAY_NATIVE_SESSION_BINDING_SECRET='s' * 32, FAIRPLAY_NATIVE_PROXY_PEERS=['127.0.0.1'],
    FAIRPLAY_BROWSER_SIGNING_KEY=base64.b64encode(b'k' * 32).decode(), ASSIGNMENT_HARVEST_ENABLED=True)
class LeaveIdentityOrderTests(FairPlayDataMixin, TransactionTestCase):
    request = NativeSessionAPITests.request
    decode = NativeSessionAPITests.decode
    claim = NativeSessionAPITests.claim

    def setUp(self):
        type(self).setUpTestData()
        super().setUp()

    def test_leave_checks_the_device_identity_before_it_takes_the_account_lock(self):
        from unittest.mock import patch
        from django.db import connection
        from django.utils import timezone
        from judge.models import ContestParticipation
        from judge.views import fairplay_native_session as module
        payload = self.decode(self.claim())
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
                                                            real_start=timezone.now(), virtual=0)
        FairPlayDeviceSession.objects.filter(pk=payload['session']).update(participation=participation)
        order = []
        real_identity = module.request_identity

        def identity(request):
            order.append(('identity', connection.in_atomic_block))
            return real_identity(request)
        with patch.object(module, 'request_identity', identity):
            try:
                module.finish_from_website(self.request('POST', '/contest/fpexam/leave'), participation)
            except AttributeError:
                pass  # the bare test request has no middleware attributes for the success page
        self.assertEqual(order[0], ('identity', False))

"""The FairPlay student help page of a contest: who may open it, the confirm step and the bridge calls."""
import json
from html import unescape
from urllib.parse import parse_qs, urlsplit
from unittest import mock

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from judge.utils.fairplay_client import FairPlayClient, FairPlayError, FairPlayUnreachable
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, INSTALLATION_ID, set_fairplay
from judge.views.tests.test_fairplay_pages import PagesMixin, exam, fake_response

STUDENTS = [
    {'netid': 'fp_student', 'name': 'Avazbek Khamidov', 'status': 'waiting', 'code_state': 'bound',
     'hand_in_count': 0, 'swap_requested': True, 'reopened': False},
    {'netid': 'fp_classmate', 'name': 'Class Mate', 'status': 'handed_in', 'code_state': 'unused',
     'hand_in_count': 2, 'swap_requested': False, 'reopened': True, 'fp_mode': 'submitted'},
]


@FAIRPLAY_ON
class StudentHelpTestCase(PagesMixin, TestCase):
    URL = None

    def setUp(self):
        super().setUp()
        self.URL = reverse('contest_fairplay_help', args=['fpexam'])
        self.fp.exam.return_value = exam(students=STUDENTS, start_code='K7M2QX', invigilator_code='918273')

    def post(self, user, data, csrf=False):
        return self.login(user, csrf=csrf).post(self.URL, data)

    def confirm(self, user, **data):
        data.setdefault('netid', 'fp_student')
        data.setdefault('name', 'Avazbek Khamidov')
        first = self.post(user, data)
        self.assertEqual(first.status_code, 200)
        return self.post(user, dict(data, confirm='1'))

    def test_link_on_the_participation_page(self):
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertIn(self.URL, html)
        self.assertIn('FairPlay student help', html)
        html = self.login(self.student).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertNotIn(self.URL, html)

    def test_page_lists_students_and_codes(self):
        response = self.login(self.teacher).get(self.URL)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        for text in ('K7M2QX', '918273', 'fp_student', 'waiting for approval', 'submitted', 'bound', 'unused',
                     'Approve resume', 'Move to another computer', 'Issue new code', 'Reopen exam',
                     'Give extra time', 'Offline exit code', 'Sync now', 'asks to move to another computer'):
            self.assertIn(text, html)
        self.assertNotIn('Start again', html)
        self.assert_acting(self.teacher)

    def test_menu_actions_depend_on_the_student(self):
        html = self.login(self.teacher).get(self.URL).content.decode()
        self.assertEqual(html.count('data-action="reopen"'), 1)
        self.assertEqual(html.count('data-action="extra-time"'), 1)
        self.assertEqual(html.count('data-action="approve"'), 1)
        self.assertEqual(html.count('data-action="release-device"'), 2)
        self.assertEqual(html.count('aria-label="Actions"'), 2)
        self.assertEqual(html.count('name="reason"'), 1)
        self.assertEqual(html.count('class="fp-choice"'), 8)
        for text in ("Student's computer failed", 'Student lost or cannot read the code',
                     'Approved accommodation', 'Submitted by mistake', 'Lock stuck during the exam', 'Other...'):
            self.assertIn(text, unescape(html))
        self.assertNotIn('For students still taking the exam', html)
        self.fp.exam.return_value = exam(students=STUDENTS[:1])
        html = self.login(self.teacher).get(self.URL).content.decode()
        self.assertNotIn('data-action="reopen"', html)
        self.assertEqual(html.count('data-action="extra-time"'), 1)
        self.fp.exam.return_value = exam(students=STUDENTS[1:])
        html = self.login(self.teacher).get(self.URL).content.decode()
        self.assertIn('data-action="reopen"', html)
        self.assertNotIn('data-action="extra-time"', html)

    def test_only_editors_and_only_fairplay_exams(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher, self.outsider):
            self.assertEqual(self.login(user).get(self.URL).status_code, 404, user.username)
            self.assertEqual(self.post(user, {'action': 'sync'}).status_code, 404, user.username)
        self.assertEqual(Client().get(self.URL).status_code, 302)
        with FAIRPLAY_OFF:
            self.assertEqual(self.login(self.teacher).get(self.URL).status_code, 404)
        set_fairplay(self.exam, False)
        self.assertEqual(self.login(self.teacher).get(self.URL).status_code, 404)
        self.fp.release_device.assert_not_called()

    def test_csrf_is_enforced(self):
        response = self.post(self.teacher, {'action': 'sync'}, csrf=True)
        self.assertEqual(response.status_code, 403)
        self.fp.sync_ping.assert_not_called()

    def test_get_does_not_act(self):
        self.login(self.teacher).get(self.URL + '?action=reissue&netid=fp_student&confirm=1&reason=x')
        self.fp.reissue_code.assert_not_called()

    def test_reason_is_required_for_every_action(self):
        for action in ('approve', 'release-device', 'reissue', 'reopen', 'extra-time'):
            with self.subTest(action=action):
                for reason in ('', '   '):
                    response = self.post(self.teacher, {'action': action, 'netid': 'fp_student', 'minutes': '10',
                                                        'mac_code': 'ABC', 'reason': reason, 'confirm': '1'})
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                                     ['Write a reason first.'])
        for call in (self.fp.approve, self.fp.release_device, self.fp.reissue_code, self.fp.reopen,
                     self.fp.extra_time):
            call.assert_not_called()

    def test_confirm_step_comes_first(self):
        response = self.post(self.teacher, {'action': 'release-device', 'netid': 'fp_student',
                                            'name': 'Avazbek Khamidov', 'reason': 'laptop died'})
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('laptop died', html)
        self.assertIn('name="confirm" value="1"', html)
        self.fp.release_device.assert_not_called()

    def test_actions_call_the_bridge(self):
        self.confirm(self.teacher, action='approve', reason='back after crash')
        self.fp.approve.assert_called_once_with('fpexam', 'fp_student', 'back after crash')
        self.confirm(self.teacher, action='release-device', reason='laptop died')
        self.fp.release_device.assert_called_once_with('fpexam', 'fp_student', 'laptop died')
        self.confirm(self.teacher, action='reopen', reason='pressed submit by mistake', minutes='15')
        self.fp.reopen.assert_called_once_with('fpexam', 'fp_student', 15, 'pressed submit by mistake')
        self.confirm(self.teacher, action='extra-time', reason='power cut', minutes='5')
        self.fp.extra_time.assert_called_once_with('fpexam', 'fp_student', 5, 'power cut')
        self.fp.native_exit.return_value = {'code': '445566'}
        response = self.confirm(self.teacher, action='offline-exit', reason='no network', mac_code='AB12')
        self.fp.native_exit.assert_called_once_with('fpexam', 'fp_student', 'AB12', 'no network')
        self.assertIn('445566', response.content.decode())
        self.assert_acting(self.teacher)

    def test_success_redirects_with_one_line(self):
        response = self.confirm(self.teacher, action='release-device', reason='laptop died')
        self.assertRedirects(response, self.URL, fetch_redirect_response=False)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                         ['Done: Move to another computer for Avazbek Khamidov.'])

    def test_minutes_are_checked(self):
        for minutes in ('', '0', '-5', 'x', '481', '1.5'):
            with self.subTest(minutes=minutes):
                response = self.post(self.teacher, {'action': 'extra-time', 'netid': 'fp_student',
                                                    'reason': 'r', 'minutes': minutes, 'confirm': '1'})
                self.assertEqual(response.status_code, 302)
        self.fp.extra_time.assert_not_called()

    def test_new_code_is_shown_once_and_never_stored(self):
        self.fp.reissue_code.return_value = {'code': 'Q9X4TT', 'first_name': 'Avazbek', 'last_name': 'Khamidov'}
        response = self.confirm(self.teacher, action='reissue', reason='lost the slip')
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('Q9X4TT', html)
        self.assertIn('Avazbek', html)
        self.assertIn('shown only once', html)
        self.fp.reissue_code.assert_called_once_with('fpexam', 'fp_student', 'lost the slip')
        self.assertNotIn('Q9X4TT', self.login(self.teacher).get(self.URL).content.decode())
        self.assertNotIn('Q9X4TT', json.dumps(list(self.client.session.items()), default=str))

    def test_bridge_errors_show_one_line(self):
        self.fp.release_device.side_effect = FairPlayError('The student has not signed in.', status=409)
        response = self.confirm(self.teacher, action='release-device', reason='r')
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ['The student has not signed in.'])
        self.fp.release_device.side_effect = FairPlayUnreachable()
        response = self.confirm(self.teacher, action='release-device', reason='r')
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                         ['FairPlay cannot be reached. Try again in a minute.'])
        self.fp.release_device.side_effect = FairPlayError('stack trace here', status=500)
        response = self.confirm(self.teacher, action='release-device', reason='r')
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ['FairPlay could not do that.'])

    def bind(self, code, username='fp_student', contest=None):
        from django.utils import timezone
        from judge.models import Contest, FairPlayDeviceAudit, FairPlayDeviceSession
        from django.contrib.auth.models import User
        user = User.objects.get(username=username)
        session = FairPlayDeviceSession.objects.create(
            user=user, contest=contest or Contest.objects.get(key='fpexam'), fingerprint='a' * 64,
            browser_digest='b' * 64, lease_until=timezone.now())
        FairPlayDeviceAudit.objects.create(session=session, actor=user, action='lock', reason=code)

    def ask(self, user, code='K7QM-3XPA', **data):
        data = dict({'action': 'offline-exit', 'netid': 'fp_student', 'mac_code': code,
                     'reason': 'locked overnight', 'confirm': '1'}, **data)
        return self.post(user, data)

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_exit_code_is_issued_only_for_the_lock_this_student_set_in_this_exam(self):
        self.fp.native_exit.return_value = {'code': '123456'}
        self.assertIn('id="fp-mac-exit"', self.login(self.teacher).get(self.URL).content.decode())
        self.bind('K7QM3XPA')
        response = self.ask(self.teacher)
        self.fp.native_exit.assert_called_once_with('fpexam', 'fp_student', 'K7QM-3XPA', 'locked overnight')
        self.assertIn('123456', response.content.decode())

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_lock_of_another_exam_gets_no_code_for_an_instructor(self):
        from judge.models import Contest
        other_exam = Contest.objects.create(key='otherexam', name='Other',
                                            start_time=Contest.objects.get(key='fpexam').start_time,
                                            end_time=Contest.objects.get(key='fpexam').end_time)
        self.bind('K7QM3XPA', contest=other_exam)
        self.assertEqual([str(m) for m in get_messages(self.ask(self.teacher).wsgi_request)],
                         ['This Mac code belongs to another exam or student. No exit code was issued.'])
        self.fp.native_exit.assert_not_called()

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_administrator_can_rebind_a_prebound_lock_and_it_is_logged(self):
        from judge.models import Contest, FairPlayDeviceAudit
        from judge.models.tests.util import create_user
        from judge.utils.fairplay_device_session import bind_lock
        self.fp.native_exit.return_value = {'code': '111222'}
        other_exam = Contest.objects.create(key='otherexam', name='Other',
                                            start_time=Contest.objects.get(key='fpexam').start_time,
                                            end_time=Contest.objects.get(key='fpexam').end_time)
        self.bind('K7QM3XPA', contest=other_exam)
        self.ask(self.teacher)
        self.fp.native_exit.assert_not_called()
        admin = create_user(username='fp_admin')
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        with self.assertLogs('judge.fairplay', 'WARNING'):
            self.assertIn('111222', self.ask(admin).content.decode())
        self.assertTrue(FairPlayDeviceAudit.objects.filter(action='lock-override', actor=admin).exists())
        self.fp.native_exit.reset_mock()
        self.assertIn('111222', self.ask(self.teacher).content.decode())

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=False)
    def test_classic_mode_with_the_native_flag_off_uses_the_original_offline_exit_without_the_lock_gate(self):
        from judge.utils.fairplay_client import FairPlayError
        self.fp.native_exit.side_effect = FairPlayError('none', status=404)
        self.fp.offline_exit.return_value = {'code': '445566'}
        response = self.ask(self.teacher, code='K7QM-3XPA')
        self.fp.offline_exit.assert_called_once_with('fpexam', 'fp_student', 'K7QM-3XPA')
        self.assertIn('445566', response.content.decode())

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_administrator_who_is_not_an_editor_asks_the_server_as_the_exams_editor(self):
        from judge.models.tests.util import create_user
        self.fp.native_exit.return_value = {'code': '777888'}
        admin = create_user(username='fp_admin')
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        with self.assertLogs('judge.fairplay', 'WARNING'):
            response = self.ask(admin, reason='stuck overnight')
        self.assertIn('777888', response.content.decode())
        self.client_class.assert_called_with(acting_user='fp_teacher')
        args = self.fp.native_exit.call_args.args
        self.assertEqual(args[:3], ('fpexam', 'fp_student', 'K7QM-3XPA'))
        self.assertTrue(args[3].startswith('stuck overnight'))
        self.assertTrue(args[3].endswith('(released by site administrator fp_admin)'))
        self.assertLessEqual(len(args[3]), 200)

    def test_reasons_are_capped_at_the_servers_two_hundred_characters(self):
        response = self.post(self.teacher, {'action': 'release-device', 'netid': 'fp_student', 'reason': 'x' * 201, 'confirm': '1'})
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                         ['The reason is too long (at most 200 characters).'])
        self.assertEqual(self.post(self.teacher, {'action': 'release-device', 'netid': 'fp_student', 'reason': 'x' * 200,
                                                  'confirm': '1'}).status_code, 302)

    def test_mac_code_cleanup_matches_the_server(self):
        from judge.utils.fairplay_device_session import normalize_mac_code
        for raw in ('k7qm-3xpa', ' K7QM 3XPA ', 'k7qm_3xpa.', 'K7QM\u00b73XPA'):
            self.assertEqual(normalize_mac_code(raw), 'K7QM3XPA', raw)
        self.assertIsNone(normalize_mac_code('K7QM3XP'))

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_override_is_saved_only_after_the_server_agreed(self):
        from judge.models import Contest, FairPlayDeviceAudit
        from judge.models.tests.util import create_user
        from judge.utils.fairplay_client import FairPlayError
        from judge.utils.fairplay_device_session import lock_owner
        other_exam = Contest.objects.create(key='otherexam', name='Other',
                                            start_time=Contest.objects.get(key='fpexam').start_time,
                                            end_time=Contest.objects.get(key='fpexam').end_time)
        self.bind('K7QM3XPA', contest=other_exam)
        admin = create_user(username='fp_admin')
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        self.fp.native_exit.side_effect = FairPlayError('This Mac code belongs to another student or exam', status=409)
        self.ask(admin)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='lock-override').exists())
        self.assertEqual(lock_owner('K7QM3XPA', use_cache=False)[0], other_exam.pk)
        self.fp.native_exit.side_effect = None
        self.fp.native_exit.return_value = {'code': '246810'}
        with self.assertLogs('judge.fairplay', 'WARNING'):
            self.assertIn('246810', self.ask(admin).content.decode())
        self.assertTrue(FairPlayDeviceAudit.objects.filter(action='lock-override', actor=admin).exists())

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_release_decisions_read_the_database_not_a_stale_cache(self):
        from django.core.cache import cache
        from judge.utils.fairplay_device_session import lock_owner_key
        self.fp.native_exit.return_value = {'code': '135790'}
        cache.set(lock_owner_key('K7QM3XPA'), 'none', 600)
        self.bind('K7QM3XPA')
        self.assertIn('135790', self.ask(self.teacher).content.decode())
        cache.set(lock_owner_key('K7QM3XPA'), ['999', 'someone-else'], 600)
        self.assertIn('135790', self.ask(self.teacher).content.decode())

    @override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True)
    def test_unclaimed_lock_needs_an_administrator_and_is_logged(self):
        from judge.models.tests.util import create_user
        self.fp.native_exit.return_value = {'code': '654321'}
        self.ask(self.teacher)
        self.fp.native_exit.assert_not_called()
        admin = create_user(username='fp_admin')
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        with self.assertLogs('judge.fairplay', 'WARNING') as logs:
            response = self.ask(admin)
        self.assertIn('fp_admin', logs.output[0])
        self.assertIn('654321', response.content.decode())

    def test_classic_mode_has_no_standalone_mac_exit_form(self):
        self.assertNotIn('id="fp-mac-exit"', self.login(self.teacher).get(self.URL).content.decode())

    def test_unreachable_page(self):
        self.fp.exam.side_effect = FairPlayUnreachable()
        response = self.login(self.teacher).get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn('FairPlay cannot be reached', response.content.decode())

    def test_sync_now(self):
        response = self.post(self.teacher, {'action': 'sync'})
        self.assertRedirects(response, self.URL, fetch_redirect_response=False)
        self.fp.sync_ping.assert_called_once()
        self.assertEqual(self.fp.sync_ping.call_args.kwargs['exam_key'], 'fpexam')

    def test_unknown_action(self):
        self.assertEqual(self.post(self.teacher, {'action': 'delete', 'netid': 'x', 'reason': 'r'}).status_code, 404)


@FAIRPLAY_ON
class StudentHelpClientTestCase(TestCase):
    def call(self, method, *args):
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'ok': True})) as call:
            getattr(FairPlayClient(acting_user='fp_teacher'), method)(*args)
        url = urlsplit(call.call_args[0][1])
        self.assertEqual(parse_qs(url.query), {'installation_id': [INSTALLATION_ID]})
        return url.path.split('/v1')[1], json.loads(call.call_args[1]['data'])

    def test_paths_and_bodies(self):
        self.assertEqual(self.call('release_device', 'ex', 'n1', 'why'),
                         ('/exams/ex/students/n1/release-device', {'reason': 'why'}))
        self.assertEqual(self.call('reissue_code', 'ex', 'n1', 'why'),
                         ('/exams/ex/access-codes/reissue', {'netid': 'n1', 'reason': 'why'}))
        self.assertEqual(self.call('reopen', 'ex', 'n1', 10, 'why'),
                         ('/exams/ex/students/n1/reopen', {'minutes': 10, 'reason': 'why'}))
        self.assertEqual(self.call('extra_time', 'ex', 'n1', 5, 'why'),
                         ('/exams/ex/students/n1/extra-time', {'minutes': 5, 'reason': 'why'}))
        self.assertEqual(self.call('offline_exit', 'ex', 'n1', 'AB'),
                         ('/exams/ex/students/n1/offline-exit', {'mac_code': 'AB'}))
        self.assertEqual(self.call('approve', 'ex', 'n1'), ('/exams/ex/approve', {'netid': 'n1'}))
        self.assertEqual(self.call('approve', 'ex', 'n1', 'why'),
                         ('/exams/ex/approve', {'netid': 'n1', 'reason': 'why'}))

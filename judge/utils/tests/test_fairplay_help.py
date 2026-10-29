"""The FairPlay student help page of a contest: who may open it, the confirm step and the bridge calls."""
import json
from unittest import mock

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from judge.utils.fairplay_client import FairPlayClient, FairPlayError, FairPlayUnreachable
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, set_fairplay
from judge.views.tests.test_fairplay_pages import PagesMixin, exam, fake_response

STUDENTS = [
    {'netid': 'fp_student', 'name': 'Avazbek Khamidov', 'status': 'waiting', 'code_state': 'bound',
     'hand_in_count': 0, 'swap_requested': True, 'reopened': False},
    {'netid': 'fp_classmate', 'name': 'Class Mate', 'status': 'handed_in', 'code_state': 'unused',
     'hand_in_count': 2, 'swap_requested': False, 'reopened': True},
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
                     'Approve resume', 'Move to another computer', 'Issue new code', 'Reopen hand-in',
                     'Give extra time', 'Offline exit code', 'Sync now', 'asks to move to another computer'):
            self.assertIn(text, html)
        self.assertNotIn('Start again', html)
        self.assert_acting(self.teacher)

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
        for action in ('approve', 'release-device', 'reissue', 'reopen', 'extra-time', 'offline-exit'):
            with self.subTest(action=action):
                for reason in ('', '   '):
                    response = self.post(self.teacher, {'action': action, 'netid': 'fp_student', 'minutes': '10',
                                                        'mac_code': 'ABC', 'reason': reason, 'confirm': '1'})
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                                     ['Write a reason first.'])
        for call in (self.fp.approve, self.fp.release_device, self.fp.reissue_code, self.fp.reopen,
                     self.fp.extra_time, self.fp.offline_exit):
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
        self.fp.offline_exit.return_value = {'code': '445566'}
        response = self.confirm(self.teacher, action='offline-exit', reason='no network', mac_code='AB12')
        self.fp.offline_exit.assert_called_once_with('fpexam', 'fp_student', 'AB12')
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
        return call.call_args[0][1].split('/v1')[1], json.loads(call.call_args[1]['data'])

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

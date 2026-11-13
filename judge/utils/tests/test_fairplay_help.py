"""The FairPlay actions endpoint of a contest (its old help page now redirects to Participation): who may open it,
the confirm step and the bridge calls."""
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

    def test_no_link_to_a_separate_help_page_and_old_url_redirects(self):
        participation = reverse('contest_participation_own', args=['fpexam'])
        html = self.login(self.teacher).get(participation).content.decode()
        self.assertNotIn('FairPlay student help', html)
        self.assertNotIn('href="%s"' % self.URL, html)
        response = self.login(self.teacher).get(self.URL)
        self.assertRedirects(response, participation, fetch_redirect_response=False)

    def test_classic_participation_table_has_the_classic_actions(self):
        participation = reverse('contest_participation_own', args=['fpexam'])
        html = self.login(self.teacher).get(participation).content.decode()
        table = html.split('id="fp-participation-head"')[1]
        for text in ('fp_student', 'Avazbek Khamidov', 'Waiting for approval', 'Class Mate', 'Submitted', 'K7M2QX',
                     '918273', 'Sync now'):
            self.assertIn(text, table)
        menu = html.split('id="fp-part-menu"')[1].split('<form')[0]
        for action in ('approve', 'reopen', 'extra-time', 'release-device', 'offline-exit'):
            self.assertIn('data-action="%s"' % action, menu)
        self.assertNotIn('offline-exit-on-record', menu)
        self.assertIn('data-action="approve" data-mode="" data-states="waiting"', menu)
        self.assertIn('data-action="reopen" data-mode="" data-states="completed"', menu)
        self.assertNotIn('data-action="reissue"', menu)
        self.assertNotIn('Download access codes', html)
        student_html = self.login(self.student).get(participation).content.decode()
        self.assertNotIn('id="fp-participation"', student_html)

    def test_classic_offline_exit_without_a_code_asks_for_the_code_on_the_screen(self):
        response = self.post(self.teacher, {'action': 'offline-exit', 'netid': 'fp_student', 'confirm': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="mac_code"')
        self.fp.offline_exit.assert_not_called()

    def test_classic_block_is_shown_with_codes_and_sync_even_without_students(self):
        self.fp.exam.return_value = exam(students=[], start_code='K7M2QX', invigilator_code='918273')
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        table = html.split('id="fp-participation-head"')[1]
        for text in ('K7M2QX', '918273', 'Sync now', 'No students yet'):
            self.assertIn(text, table)

    def test_every_menu_action_asks_before_it_acts(self):
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        menu = html.split('id="fp-part-menu"')[1].split('<form')[0]
        reopen = menu.split('data-action="reopen"')[1].split('</button>')[0]
        self.assertIn('data-direct="1"', reopen)  # one click: no question, reason or minutes dialog for Reopen exam
        for leftover in ('data-question', 'data-reasons', 'data-minutes="1"', 'data-reason="1"'):
            self.assertNotIn(leftover, reopen)
        self.assertNotIn('Reopen the exam for {who}', unescape(menu))
        menu = menu.replace(reopen, '')
        self.assertNotIn('data-question=""', menu)
        for text in ('Let {who} sign in again from another computer?', 'Approve {who} to resume the exam?', 'Give {who} extra time?'):
            self.assertIn(text, unescape(menu))

    def test_classic_actions_open_the_same_dialog_with_their_reason_lists(self):
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertNotIn('window.confirm', html)
        self.assertNotIn('window.prompt', html)
        self.assertEqual(html.count('<dialog'), 1)
        menu = html.split('id="fp-part-menu"')[1].split('<form')[0]
        for key in ('approve', 'extra-time', 'release-device'):
            self.assertIn('data-reasons="%s"' % key, menu)
        self.assertIn('data-reasons="offline-exit" data-reason-optional="1"', menu)
        self.assertNotIn('data-reasons="reopen"', menu)
        self.assertEqual(menu.count('data-minutes="1"'), 1)  # only Give extra time asks for minutes
        self.assertNotIn('Approved retake of a part', html)
        self.assertNotIn('id="fp-part-reasons-reopen"', html)
        for text in ('Disruption in the room', 's computer ran out of battery'):
            self.assertIn(text, html)

    def test_classic_rows_keep_the_swap_request_reopen_code_and_hand_in_count(self):
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        table = html.split('id="fp-participation-head"')[1].split('</table>')[0]
        for text in ('Asks to move to another computer.', 'Hand-in reopened.', 'Hand-ins: 0.',
                     'Hand-ins: 2.'):
            self.assertIn(text, table)

    def test_the_classic_cache_is_per_acting_instructor_and_a_stale_answer_is_shown_at_once(self):
        import time
        from unittest import mock
        from django.core.cache import cache
        from judge.views import fairplay
        cache.clear()
        class_a = fairplay._classic_cache_keys(self.exam, self.teacher.username)
        class_b = fairplay._classic_cache_keys(self.exam, 'someone_else')
        self.assertNotEqual(class_a, class_b)
        stale = {'students': [dict(STUDENTS[0], name='From the cache')], 'start_code': 'OLD111',
                 'invigilator_code': '000000'}
        cache.set(class_a[0], {'at': time.time() - 100, 'exam': stale}, 900)
        spawned = []
        with mock.patch.object(fairplay, '_spawn', spawned.append):
            html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertIn('From the cache', html)  # shown at once, FairPlay was not waited for
        self.assertEqual(len(self.roster_calls()), 0)
        self.assertEqual(len(spawned), 1)
        spawned[0]()  # the background refresh asks FairPlay as the same instructor and replaces the entry
        self.assertEqual(len(self.roster_calls()), 1)
        self.assertNotIn('OLD111', str(cache.get(class_a[0])))
        cache.clear()

    def stale_page(self, page='contest_participation_own', **entry):
        import time
        from unittest import mock
        from django.core.cache import cache
        from judge.views import fairplay
        cache.clear()
        keys = fairplay._classic_cache_keys(self.exam, self.teacher.username)
        old = {'students': [dict(STUDENTS[0])], 'start_code': 'OLD111', 'invigilator_code': '000000'}
        data = {'at': time.time() - 300, 'exam': old}
        data.update(entry)
        cache.set(keys[0], data, 900)
        with mock.patch.object(fairplay, '_spawn', lambda function: None):
            html = self.login(self.teacher).get(reverse(page, args=['fpexam'])).content.decode()
        cache.clear()
        return html

    def test_stale_data_is_never_shown_as_live(self):
        import time
        failed = self.stale_page(failed=time.time() - 10)
        self.assertIn('FairPlay could not be reached; showing data from 5 minutes ago.', failed)
        refreshing = self.stale_page()
        self.assertIn('Showing data from 5 minutes ago; refreshing.', refreshing)
        self.assertNotIn('could not be reached', refreshing)
        fresh = self.stale_page(at=time.time() - 1)
        self.assertNotIn('minutes ago', fresh)

    def test_the_ranking_tab_says_when_its_mode_summary_is_old_and_ages_read_naturally(self):
        import time
        from judge.views.fairplay import _age_text
        html = self.stale_page(page='contest_ranking', failed=time.time() - 10)
        self.assertIn('FairPlay could not be reached; showing data from 5 minutes ago.', html)
        self.assertEqual([_age_text(n) for n in (1, 45, 60, 119, 120)],
                         ['1 second ago', '45 seconds ago', '1 minute ago', '1 minute ago', '2 minutes ago'])

    def test_the_cached_roster_is_dropped_only_after_fairplay_accepted_an_action(self):
        import time
        from django.core.cache import cache
        from judge.views import fairplay
        cache.clear()
        key = fairplay._classic_cache_keys(self.exam, self.teacher.username)[0]
        cache.set(key, {'at': time.time(), 'exam': {'students': []}}, 900)
        self.post(self.teacher, {'action': 'approve', 'netid': 'fp_student', 'reason': '', 'confirm': '1'})
        self.assertIsNotNone(cache.get(key))  # refused (no reason): the stale fallback is kept
        self.post(self.teacher, {'action': 'approve', 'netid': 'fp_student', 'reason': 'ok', 'confirm': '1'})
        self.assertIsNone(cache.get(key))
        cache.clear()

    def test_the_participation_page_with_codes_is_never_stored(self):
        response = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam']))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])

    def test_the_classic_page_asks_fairplay_for_the_exam_only_once(self):
        from django.core.cache import cache
        cache.clear()
        self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam']))
        self.assertEqual(len(self.fp.exam.call_args_list), 1)  # the mode summary and the table share one answer
        cache.clear()

    def roster_calls(self):
        return [c for c in self.fp.exam.call_args_list if c.kwargs.get('read_timeout') == 2]

    def test_the_classic_roster_is_cached_briefly_and_an_outage_is_not_retried_at_once(self):
        from django.core.cache import cache
        cache.clear()
        page = reverse('contest_participation_own', args=['fpexam'])
        client = self.login(self.teacher)
        client.get(page)
        client.get(page)
        self.assertEqual(len(self.roster_calls()), 1)  # a short read timeout, asked once for two loads
        self.post(self.teacher, {'action': 'sync'})  # any action forgets the cached answer
        client.get(page)
        self.assertEqual(len(self.roster_calls()), 2)
        cache.clear()
        self.fp.exam.reset_mock()
        self.fp.exam.side_effect = FairPlayUnreachable()
        self.assertIn('FairPlay cannot be reached', client.get(page).content.decode())
        self.assertIn('FairPlay cannot be reached', client.get(page).content.decode())
        self.assertEqual(len(self.roster_calls()), 1)
        cache.clear()

    def test_classic_participation_says_so_when_fairplay_is_unreachable(self):
        self.fp.exam.side_effect = FairPlayUnreachable()
        html = self.login(self.teacher).get(reverse('contest_participation_own', args=['fpexam'])).content.decode()
        self.assertIn('FairPlay cannot be reached', html)

    def test_only_editors_and_only_fairplay_exams(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher, self.outsider):
            self.assertEqual(self.login(user).get(self.URL).status_code, 404, user.username)
            self.assertEqual(self.post(user, {'action': 'sync'}).status_code, 404, user.username)
        self.assertEqual(Client().get(self.URL).status_code, 302)
        self.assertEqual(self.login(self.teacher).get(self.URL).status_code, 302)
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
        self.login(self.teacher).get(self.URL + '?action=release-device&netid=fp_student&confirm=1&reason=x')
        self.fp.release_device.assert_not_called()

    def test_reason_is_required_for_every_action(self):
        for action in ('approve', 'release-device', 'reopen', 'extra-time'):
            with self.subTest(action=action):
                for reason in ('', '   '):
                    response = self.post(self.teacher, {'action': action, 'netid': 'fp_student', 'minutes': '10',
                                                        'mac_code': 'ABC', 'reason': reason, 'confirm': '1'})
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                                     ['Write a reason first.'])
        for call in (self.fp.approve, self.fp.release_device, self.fp.reopen,
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
        self.assertRedirects(response, reverse('contest_participation_own', args=['fpexam']),
                             fetch_redirect_response=False)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                         ['Done: Move to another computer for Avazbek Khamidov.'])

    def test_minutes_are_checked(self):
        for minutes in ('', '0', '-5', 'x', '481', '1.5'):
            with self.subTest(minutes=minutes):
                response = self.post(self.teacher, {'action': 'extra-time', 'netid': 'fp_student',
                                                    'reason': 'r', 'minutes': minutes, 'confirm': '1'})
                self.assertEqual(response.status_code, 302)
        self.fp.extra_time.assert_not_called()


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
        self.bind('K7QM3XPA')
        response = self.ask(self.teacher)
        self.fp.native_exit.assert_called_once_with('fpexam', 'fp_student', 'K7QM3XPA', 'locked overnight')
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
        self.fp.offline_exit.assert_called_once_with('fpexam', 'fp_student', 'K7QM3XPA')
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
        self.assertEqual(args[:3], ('fpexam', 'fp_student', 'K7QM3XPA'))
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
    def test_unclaimed_lock_is_released_for_the_exams_instructor_and_logged(self):
        # Changed on purpose: an instructor of the exam may now release a lock no one owns (it used to need a site
        # administrator); the lock is logged and a site administrator still can.
        from judge.models.tests.util import create_user
        self.fp.native_exit.return_value = {'code': '654321'}
        with self.assertLogs('judge.fairplay', 'WARNING') as logs:
            response = self.ask(self.teacher)
        self.assertIn('fp_teacher', logs.output[0])
        self.assertIn('654321', response.content.decode())
        self.fp.native_exit.reset_mock()
        admin = create_user(username='fp_admin')
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        with self.assertLogs('judge.fairplay', 'WARNING') as logs:
            response = self.ask(admin)
        self.assertIn('fp_admin', logs.output[0])
        self.assertIn('654321', response.content.decode())

    def test_sync_now(self):
        response = self.post(self.teacher, {'action': 'sync'})
        self.assertRedirects(response, reverse('contest_participation_own', args=['fpexam']),
                             fetch_redirect_response=False)
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
        self.assertEqual(self.call('reopen', 'ex', 'n1', 10, 'why'),
                         ('/exams/ex/students/n1/reopen', {'minutes': 10, 'reason': 'why'}))
        self.assertEqual(self.call('extra_time', 'ex', 'n1', 5, 'why'),
                         ('/exams/ex/students/n1/extra-time', {'minutes': 5, 'reason': 'why'}))
        self.assertEqual(self.call('offline_exit', 'ex', 'n1', 'AB'),
                         ('/exams/ex/students/n1/offline-exit', {'mac_code': 'AB'}))
        self.assertEqual(self.call('approve', 'ex', 'n1'), ('/exams/ex/approve', {'netid': 'n1'}))
        self.assertEqual(self.call('approve', 'ex', 'n1', 'why'),
                         ('/exams/ex/approve', {'netid': 'n1', 'reason': 'why'}))

"""The ping DKU OJ sends FairPlay after a FairPlay assignment is saved, changed or deleted."""
import json
import threading
from unittest import mock

from django.db import connection, transaction
from django.test import TestCase

from judge.models import Contest
from judge.models.tests.util import create_contest_problem
from judge.utils import fairplay_sync
from judge.utils.fairplay_client import FairPlayClient, FairPlayError, FairPlayUnreachable
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, TOKEN, create_contest, \
    set_fairplay
from judge.views.tests.test_fairplay_pages import fake_response


class SyncMixin(FairPlayDataMixin):
    def setUp(self):
        super().setUp()
        connection.run_on_commit.clear()
        fairplay_sync._last_sent.clear()
        fairplay_sync._trailing.clear()
        self.delayed = []
        self.clock = [1000.0]
        for patcher in (mock.patch.object(fairplay_sync, 'monotonic', side_effect=lambda: self.clock[0]),
                        mock.patch.object(fairplay_sync, '_run_later', side_effect=self.run_later)):
            patcher.start()
            self.addCleanup(patcher.stop)
        sender = mock.patch.object(fairplay_sync, '_send')
        self.send = sender.start()
        self.addCleanup(sender.stop)

    def run_later(self, delay, function):
        if delay:
            self.delayed.append((delay, function))
        else:
            function()

    def commit(self, work=None):
        with self.captureOnCommitCallbacks(execute=True):
            if work:
                work()
        connection.run_on_commit.clear()

    def sent(self):
        return [call.args for call in self.send.call_args_list]


@FAIRPLAY_ON
class SyncPingTestCase(SyncMixin, TestCase):
    def test_nothing_before_the_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.exam.name = 'Renamed'
            self.exam.save()
        self.assertEqual(len(callbacks), 1)
        self.send.assert_not_called()

    def test_edit_pings_once_for_all_the_saves_of_one_transaction(self):
        def work():
            self.exam.name = 'Renamed'
            self.exam.save()
            self.exam.save()
            create_contest_problem(contest=self.exam, problem=self.code, points=5, order=99)
        self.commit(work)
        self.assertEqual(self.sent(), [('fpexam', None)])

    def test_create(self):
        def work():
            contest = create_contest('newexam', authors=('fp_teacher',))
            set_fairplay(contest, True)
        self.commit(work)
        self.assertEqual(self.sent(), [('newexam', None)])

    def test_delete(self):
        self.commit(self.exam.delete)
        self.assertEqual(self.sent(), [('fpexam', None)])
        self.assertFalse(Contest.objects.filter(key='fpexam').exists())

    def test_problem_changes(self):
        problem = self.exam.contest_problems.first()
        self.commit(problem.delete)
        self.assertEqual(self.sent(), [('fpexam', None)])

    def test_access_changes(self):
        self.commit(lambda: self.exam.classes.clear())
        self.assertEqual(self.sent(), [('fpexam', None)])
        self.clock[0] += 10
        self.commit(lambda: self.exam.classes.add(self.klass))
        self.assertEqual(self.sent(), [('fpexam', None), ('fpexam', None)])

    def test_lock_taken_off_pings_once(self):
        self.commit(lambda: set_fairplay(self.exam, False))
        self.assertEqual(self.sent(), [('fpexam', None)])

    def test_lock_put_on(self):
        plain = create_contest('plainexam', authors=('fp_teacher',))
        self.commit(lambda: set_fairplay(plain, True))
        self.assertEqual(self.sent(), [('plainexam', None)])

    def test_class_members(self):
        self.commit(lambda: self.klass.members.add(self.outsider.profile))
        self.assertEqual(self.sent(), [(None, self.klass.id)])

    def test_class_not_used_by_a_fairplay_exam(self):
        set_fairplay(self.exam, False)
        self.commit(lambda: self.klass.members.add(self.outsider.profile))
        self.send.assert_not_called()

    def test_rolled_back_is_silent(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    self.exam.name = 'Renamed'
                    self.exam.save()
                    raise RuntimeError
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.send.assert_not_called()

    def test_bookkeeping_saves_are_ignored(self):
        self.commit(lambda: self.exam.save(update_fields=['harvest_scheduled_task_id']))
        self.send.assert_not_called()

    def test_not_fairplay_contests_are_silent(self):
        def work():
            plain = create_contest('plain', authors=('fp_teacher',))
            plain.name = 'Renamed'
            plain.save()
            create_contest_problem(contest=plain, problem=self.code, points=5, order=1)
            plain.classes.add(self.klass)
            plain.delete()
        self.commit(work)
        self.send.assert_not_called()


@FAIRPLAY_OFF
class SyncOffTestCase(SyncMixin, TestCase):
    def test_bridge_off_does_nothing(self):
        def work():
            self.exam.save()
            self.klass.members.add(self.outsider.profile)
            self.exam.delete()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            work()
        self.assertFalse([c for c in callbacks if hasattr(c, 'fairplay_sync_target')])
        self.send.assert_not_called()


@FAIRPLAY_ON
class SyncDebounceTestCase(SyncMixin, TestCase):
    def save(self):
        self.commit(self.exam.save)

    def test_repeated_saves_send_one_now_and_one_after_the_window(self):
        self.save()
        self.clock[0] += 1
        self.save()
        self.clock[0] += 1
        self.save()
        self.assertEqual(self.sent(), [('fpexam', None)])
        self.assertEqual(len(self.delayed), 1)
        delay, function = self.delayed[0]
        self.assertAlmostEqual(delay, 2.0)
        self.clock[0] += delay
        function()
        self.assertEqual(self.sent(), [('fpexam', None)] * 2)

    def test_a_save_after_the_window_sends_at_once(self):
        self.save()
        self.clock[0] += fairplay_sync.DEBOUNCE_SECONDS
        self.save()
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(self.delayed, [])

    def test_other_exams_are_not_held_back(self):
        other = create_contest('other', authors=('fp_teacher',), fairplay_required=True)
        self.save()
        self.commit(other.save)
        self.assertEqual(self.sent(), [('fpexam', None), ('other', None)])


class SyncSendTestCase(TestCase):
    def send(self, error):
        with mock.patch('judge.utils.fairplay_client.FairPlayClient') as client:
            client.return_value.sync_ping.side_effect = error
            with self.assertLogs('judge.fairplay', 'WARNING') as logs:
                fairplay_sync._send('fpexam', None)
        return '\n'.join(logs.output)

    @FAIRPLAY_ON
    def test_errors_are_only_logged(self):
        for error in (FairPlayUnreachable(), FairPlayError('Too many', status=429), FairPlayError('x', status=404),
                      RuntimeError(TOKEN)):
            text = self.send(error)
            self.assertIn('fpexam', text)
            self.assertNotIn(TOKEN, text)

    @FAIRPLAY_ON
    def test_short_timeout(self):
        with mock.patch('judge.utils.fairplay_client.FairPlayClient') as client:
            fairplay_sync._send('fpexam', 7)
        client.return_value.sync_ping.assert_called_once_with('fpexam', 7, read_timeout=1)

    @FAIRPLAY_ON
    def test_runs_in_a_daemon_thread(self):
        done = threading.Event()
        threads = []
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        side_effect=lambda *a, **k: (threads.append(threading.current_thread()), done.set(),
                                                     fake_response(200, {'ok': True, 'queued': True}))[2]):
            fairplay_sync._run_later(0, lambda: fairplay_sync._send('fpexam', None))
            self.assertTrue(done.wait(5))
        self.assertTrue(threads[0].daemon)
        self.assertIsNot(threads[0], threading.current_thread())


@FAIRPLAY_ON
class SyncClientTestCase(TestCase):
    def call(self, response=None, **kwargs):
        response = response or fake_response(200, {'ok': True, 'queued': True})
        with mock.patch('judge.utils.fairplay_client.requests.request', return_value=response) as request:
            result = FairPlayClient().sync_ping(**kwargs)
        return result, request.call_args

    def test_signed_post(self):
        result, (args, kwargs) = self.call(exam_key='mid term', class_id=4)
        self.assertEqual(result, {'ok': True, 'queued': True})
        self.assertEqual(args, ('POST', 'https://exam.example.edu/api/integrations/dkuoj/v1/sync'))
        self.assertEqual(json.loads(kwargs['data']), {'exam_key': 'mid term', 'class_id': 4})
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/json')
        self.assertNotIn('X-FP-Acting-User', kwargs['headers'])
        from judge.utils.fairplay_signing import canonical_string, direction_key, sign, body_sha256, \
            DIRECTION_OJ_TO_FP
        headers = kwargs['headers']
        canonical = canonical_string('POST', '/api/integrations/dkuoj/v1/sync', int(headers['X-FP-Timestamp']),
                                     headers['X-FP-Nonce'], '', body_sha256(kwargs['data']))
        self.assertEqual(headers['X-FP-Signature'], sign(direction_key(DIRECTION_OJ_TO_FP, TOKEN), canonical))

    def test_class_id_omitted(self):
        _, (_, kwargs) = self.call(exam_key='fpexam')
        self.assertEqual(json.loads(kwargs['data']), {'exam_key': 'fpexam'})

    def test_short_read_timeout(self):
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, {'ok': True, 'queued': False})) as request:
            FairPlayClient().sync_ping('fpexam', read_timeout=1)
        self.assertEqual(request.call_args[1]['timeout'], (1, 1))

    def test_refusals_raise(self):
        for status in (404, 429):
            with self.assertRaises(FairPlayError) as cm:
                self.call(fake_response(status, {'detail': 'no'}), exam_key='fpexam')
            self.assertEqual(cm.exception.status, status)

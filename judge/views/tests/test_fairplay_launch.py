import json

from django.test import TestCase
from django.urls import reverse

from judge.models import Contest, ContestParticipation
from judge.utils.fairplay import fairplay_hidden_for
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, SECRETS, create_contest


@FAIRPLAY_ON
class FairPlayLaunchTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.student)

    def url(self, file=False, key='fpexam'):
        return reverse('contest_fairplay_launch_file' if file else 'contest_fairplay_launch', args=[key])

    def test_two_matching_actions_without_codes_or_exam_contents(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        for text in ('Start exam in FairPlay', 'Download FairPlay', 'fairplay-download/releases/latest',
                     'fairplay://launch?v=1&amp;exam=fpexam'):
            self.assertContains(response, text)
        self.assertNotContains(response, "Download launch file")
        self.assertNotContains(response, "access code")
        for secret in SECRETS:
            self.assertNotContains(response, secret)
        self.assertIn('no-store', response['Cache-Control'])

    def test_launch_file_contains_only_version_and_exam_reference(self):
        response = self.client.get(self.url(file=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'version': 1, 'exam_key': 'fpexam'})
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="fpexam.fairplay"')
        self.assertIn('no-store', response['Cache-Control'])

    def test_anonymous_cannot_download_or_open(self):
        self.client.logout()
        for file in (False, True):
            self.assertEqual(self.client.get(self.url(file)).status_code, 302)

    def test_private_exam_is_hidden_from_outsiders(self):
        self.client.force_login(self.outsider)
        for file in (False, True):
            self.assertEqual(self.client.get(self.url(file)).status_code, 404)

    def test_banned_student_cannot_open_or_download(self):
        self.exam.banned_users.add(self.student.profile)
        for file in (False, True):
            self.assertEqual(self.client.get(self.url(file)).status_code, 404)

    def test_bridge_off_or_other_contest_is_not_a_launch_page(self):
        with FAIRPLAY_OFF:
            self.assertEqual(self.client.get(self.url()).status_code, 404)
        other = create_contest('normal-launch')
        self.assertEqual(self.client.get(self.url(key=other.key)).status_code, 404)

    def test_join_opens_launch_page_without_creating_participation(self):
        url = reverse('contest_join', args=['fpexam'])
        before = ContestParticipation.objects.filter(contest=self.exam, user=self.student.profile).count()
        for method in (self.client.get, self.client.post):
            response = method(url)
            self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        self.assertEqual(ContestParticipation.objects.filter(
            contest=self.exam, user=self.student.profile).count(), before)

    def test_contest_entry_points_at_the_launch_screen(self):
        response = self.client.get(reverse('contest_view', args=['fpexam']))
        self.assertContains(response, 'Start exam in FairPlay')
        self.assertContains(response, 'fairplay://launch?v=1&amp;exam=fpexam')
        self.assertNotContains(response, 'Write it.')
        self.assertTrue(fairplay_hidden_for(self.student, self.exam))
        self.assertFalse(self.exam.is_accessible_by(self.student))

    def test_private_exam_needs_class_membership_and_explicit_student_list(self):
        Contest.objects.filter(pk=self.exam.pk).update(is_private=True)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.exam.private_contestants.add(self.student.profile)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
        exam = Contest.objects.get(pk=self.exam.pk)
        apply_fairplay_editor(exam, parse_fairplay_payload(exam, {
            'fairplay_required': True, 'fairplay_audience_confirm': True,
            'fairplay_audience_mode': 'classes'}, set()))
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.exam.private_contestants.add(self.outsider.profile)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_org_only_exam_uses_org_membership_when_no_classes_are_assigned(self):
        self.exam.classes.clear()
        from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
        apply_fairplay_editor(self.exam, parse_fairplay_payload(self.exam, {
            'fairplay_required': True, 'fairplay_audience_confirm': True,
            'fairplay_audience_mode': 'organizations'}, set()))
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.outsider.profile.organizations.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_invisible_exam_has_no_student_launch_page(self):
        Contest.objects.filter(pk=self.exam.pk).update(is_visible=False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_testers_keep_the_existing_full_contest_view(self):
        self.client.force_login(self.tester)
        response = self.client.get(reverse('contest_view', args=['fpexam']))
        self.assertContains(response, 'class="contest-problems-wrap"')
        self.assertContains(response, self.code.name)
        self.assertContains(response, reverse('problem_detail', args=[self.code.code]))
        self.assertNotContains(response, 'Open exam in FairPlay')


@FAIRPLAY_ON
class FairPlayLaunchProgressTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.student)
        self.url = reverse('contest_fairplay_launch', args=['fpexam'])
        self.status = reverse('contest_fairplay_launch_status', args=['fpexam'])

    def test_launch_page_has_an_accessible_progress_state_and_a_fallback(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('id="fp-launch-progress" hidden', html)
        self.assertIn('role="status" aria-live="polite"', html)
        self.assertIn('Opening FairPlay', html)
        self.assertIn('Waiting for FairPlay to start your exam', html)
        self.assertIn('open?', html)
        self.assertIn('FairPlay didn', html)
        self.assertIn('Click Start exam in FairPlay again.', html)
        self.assertIn('Make sure FairPlay is installed.', html)
        self.assertIn('fairplay-download/releases/latest', html)
        self.assertIn(self.status, html)
        self.assertIn('href="fairplay://launch?', html)  # the existing launch link is untouched

    def make_session(self, **extra):
        from django.utils import timezone
        from judge.models import FairPlayDeviceSession
        return FairPlayDeviceSession.objects.create(user=self.student, contest=Contest.objects.get(key='fpexam'),
                                                    fingerprint='a' * 64, browser_digest='b' * 64,
                                                    lease_until=timezone.now(), **extra)

    def started(self, since_ms):
        response = self.client.get(self.status, {'since': since_ms} if since_ms is not None else {})
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return json.loads(response.content)['started']

    def test_status_counts_only_a_session_after_the_click(self):
        import time
        self.make_session()
        time.sleep(0.05)
        click = int(time.time() * 1000)
        self.assertFalse(self.started(click))  # the session existed before the click
        self.assertFalse(self.started(None))
        self.assertFalse(self.started('garbage'))
        time.sleep(0.05)
        self.make_session()
        self.assertTrue(self.started(click))  # created after the click time on the server's clock

    def test_status_counts_the_apps_claim_mark_after_the_click_and_not_one_before(self):
        import time
        from django.core.cache import cache
        from judge.utils.fairplay_device_session import _launch_mark_key
        contest = Contest.objects.get(key='fpexam')
        cache.delete(_launch_mark_key(self.student.pk, contest.pk))
        cache.set(_launch_mark_key(self.student.pk, contest.pk), int(time.time() * 1000), 60)
        time.sleep(0.05)
        click = int(time.time() * 1000)
        self.assertFalse(self.started(click))  # the mark is older than the click
        cache.set(_launch_mark_key(self.student.pk, contest.pk), int(time.time() * 1000) + 5, 60)
        self.assertTrue(self.started(click))
        cache.delete(_launch_mark_key(self.student.pk, contest.pk))

    def test_a_released_session_or_another_users_session_is_not_a_start(self):
        import time
        self.make_session(state='released')
        self.assertFalse(self.started(int(time.time() * 1000) - 60000))

    def test_the_page_hands_the_server_time_and_the_hint_never_confirms(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('var skew = ', html)
        self.assertIn("'?since=' + clickedAt", html)
        self.assertIn('Switching to FairPlay', html)
        self.assertNotIn('FairPlay is open. Continue', html)
        # blur / visibility / pagehide only call hint(); only the server answer calls confirmed()
        self.assertIn("window.addEventListener('blur', hint)", html)
        self.assertNotIn("window.addEventListener('blur', opened)", html)
        self.assertEqual(html.count('confirmed()'), 2)  # its definition and the one call after the server's answer

    def test_status_is_not_available_to_outsiders_or_anonymous_users(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.status).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.status).status_code, 404)

    def test_polling_runs_the_full_gate_once_per_window_and_never_serves_an_ineligible_user(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from judge.views import fairplay_launch
        cache.clear()
        with patch.object(fairplay_launch, '_contest', wraps=fairplay_launch._contest) as gate:
            for _ in range(3):
                self.client.get(self.status, {'since': 1})
        self.assertEqual(gate.call_count, 1)
        cache.clear()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.status, {'since': 1}).status_code, 404)
        self.assertEqual(self.client.get(self.status, {'since': 1}).status_code, 404)  # a refusal is never cached
        cache.clear()

    def test_a_cached_poll_does_not_look_the_contest_up_again(self):
        from django.core.cache import cache
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        cache.clear()
        self.client.get(self.status, {'since': 1})  # first poll: the gate runs and caches the contest id
        with CaptureQueriesContext(connection) as second:
            self.client.get(self.status, {'since': 1})
        self.assertFalse([q for q in second.captured_queries if 'judge_contest`' in q['sql']], second.captured_queries)
        cache.clear()

    def test_the_cache_key_never_contains_the_url_text(self):
        import re
        from unittest.mock import patch
        from django.core.cache.backends.locmem import LocMemCache
        keys = []
        real_get = LocMemCache.get

        def recording_get(self, key, *args, **kwargs):
            keys.append(key)
            return real_get(self, key, *args, **kwargs)
        with patch.object(LocMemCache, 'get', recording_get):
            response = self.client.get('/contest/we%20ird%0Akey/fairplay-launch/status', {'since': 1})
        self.assertEqual(response.status_code, 404)
        eligible = [k for k in keys if 'fp-launch-eligible' in k]
        self.assertTrue(eligible)
        self.assertTrue(all(re.fullmatch(r'[A-Za-z0-9:_.-]+', k) for k in eligible), eligible)

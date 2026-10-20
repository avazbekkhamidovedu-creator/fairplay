"""Signed-in requests record last_access at most once a minute (or when the IP changes),
instead of an UPDATE of the profile row on every page view, autosave and poll."""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from judge.models import Profile
from judge.models.tests.util import create_user


class LastAccessThrottleTestCase(TestCase):
    def setUp(self):
        self.user = create_user(username='ul_user')
        self.client.force_login(self.user)

    def profile_updates(self, **extra):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/user/', **extra)
        return [q['sql'] for q in ctx.captured_queries
                if q['sql'].startswith('UPDATE') and 'judge_profile' in q['sql'] and 'last_access' in q['sql']]

    def test_recent_access_is_not_written_again(self):
        Profile.objects.filter(id=self.user.profile.id).update(last_access=timezone.now(), ip='127.0.0.1')
        self.assertEqual(self.profile_updates(), [])

    def test_old_access_is_refreshed(self):
        old = timezone.now() - timedelta(minutes=5)
        Profile.objects.filter(id=self.user.profile.id).update(last_access=old, ip='127.0.0.1')
        self.assertEqual(len(self.profile_updates()), 1)
        self.assertGreater(Profile.objects.get(id=self.user.profile.id).last_access, old)

    def test_new_ip_is_recorded_right_away(self):
        Profile.objects.filter(id=self.user.profile.id).update(last_access=timezone.now(), ip='10.0.0.1')
        self.assertEqual(len(self.profile_updates(REMOTE_ADDR='10.0.0.2')), 1)
        self.assertEqual(Profile.objects.get(id=self.user.profile.id).ip, '10.0.0.2')

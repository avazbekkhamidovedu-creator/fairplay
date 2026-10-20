"""When the Pwned Passwords API cannot be reached, logins stop waiting for it for a while
instead of every sign-in holding a server thread for the full timeout."""
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from judge.utils import pwned


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                       'LOCATION': 'pwned-backoff'}})
class PwnedBackoffTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_failure_skips_the_next_calls(self):
        with mock.patch.object(pwned.requests, 'get', side_effect=requests.ConnectTimeout('blocked')) as get:
            self.assertIsNone(pwned._get_pwned('ABCDE'))
            self.assertIsNone(pwned._get_pwned('ABCDE'))
            self.assertIsNone(pwned._get_pwned('12345'))
        self.assertEqual(get.call_count, 1)

    def test_reachable_api_is_still_used(self):
        response = mock.Mock(text='0018A45C4D1DEF81644B54AB7F969B88D65:3\n', raise_for_status=lambda: None)
        with mock.patch.object(pwned.requests, 'get', return_value=response) as get:
            self.assertEqual(pwned._get_pwned('21BD1'), {'0018A45C4D1DEF81644B54AB7F969B88D65': 3})
            pwned._get_pwned('21BD1')
        self.assertEqual(get.call_count, 2)

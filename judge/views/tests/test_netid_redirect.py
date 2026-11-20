from django.test import TestCase, override_settings
from django.urls import reverse


class NetidRedirectTestCase(TestCase):
    @override_settings(AUTH_HOSTS={})
    def test_without_netid_sign_in_set_up_the_login_page_explains(self):
        response = self.client.get(reverse('login_netid_redirect'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[0][0], reverse('auth_login'))
        self.assertContains(response, 'Duke NetID sign-in is not set up on this server')

    @override_settings(AUTH_HOSTS={'Duke-netid': 'https://oj.dukekunshan.edu.cn:4431/'})
    def test_with_netid_sign_in_it_goes_to_the_duke_auth_service(self):
        response = self.client.get(reverse('login_netid_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://oj.dukekunshan.edu.cn:4431/auth/redirect')

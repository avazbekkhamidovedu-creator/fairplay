import hashlib

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from judge.models import Contest
from judge.utils.seb import SEB_INSTALL_URL, request_matches_seb, seb_launch_context

BEK = hashlib.sha256(b'browser exam key').hexdigest()
CFG = hashlib.sha256(b'config key').hexdigest()


def seb_hash(url, key):
    return hashlib.sha256((url + key).encode('utf-8')).hexdigest()


class RequestMatchesSEBTestCase(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_no_headers(self):
        request = self.factory.get('/page')
        self.assertFalse(request_matches_seb(request, [BEK], [CFG]))

    def test_no_allowed_keys(self):
        request = self.factory.get(
            '/page', HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=seb_hash('http://testserver/page', BEK),
        )
        self.assertFalse(request_matches_seb(request, [], []))

    def test_valid_bek_header(self):
        request = self.factory.get(
            '/page', HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=seb_hash('http://testserver/page', BEK),
        )
        self.assertTrue(request_matches_seb(request, [BEK], [CFG]))

    def test_valid_config_key_header(self):
        request = self.factory.get(
            '/page', HTTP_X_SAFEEXAMBROWSER_CONFIGKEYHASH=seb_hash('http://testserver/page', CFG),
        )
        self.assertTrue(request_matches_seb(request, [BEK], [CFG]))

    def test_wrong_hash(self):
        request = self.factory.get(
            '/page', HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=seb_hash('http://testserver/other', BEK),
        )
        self.assertFalse(request_matches_seb(request, [BEK], [CFG]))

    def test_header_checked_against_matching_key_list_only(self):
        # A hash made with a BEK is not accepted in the Config Key header slot.
        request = self.factory.get(
            '/page', HTTP_X_SAFEEXAMBROWSER_CONFIGKEYHASH=seb_hash('http://testserver/page', BEK),
        )
        self.assertFalse(request_matches_seb(request, [BEK], [CFG]))

    def test_any_key_in_list_matches(self):
        request = self.factory.get(
            '/page', HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=seb_hash('http://testserver/page', BEK),
        )
        self.assertTrue(request_matches_seb(request, ['f' * 64, BEK], []))

    def test_hash_includes_query_string(self):
        # SEB hashes the absolute URL including the query string (only the
        # fragment is stripped).
        request = self.factory.get(
            '/page', {'x': '1'},
            HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=seb_hash('http://testserver/page?x=1', BEK),
        )
        self.assertTrue(request_matches_seb(request, [BEK], []))

        request = self.factory.get(
            '/page', {'x': '1'},
            HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=seb_hash('http://testserver/page', BEK),
        )
        self.assertFalse(request_matches_seb(request, [BEK], []))

    def test_received_hash_case_and_whitespace_insensitive(self):
        request = self.factory.get(
            '/page',
            HTTP_X_SAFEEXAMBROWSER_REQUESTHASH=' %s ' % seb_hash('http://testserver/page', BEK).upper(),
        )
        self.assertTrue(request_matches_seb(request, [BEK], []))

    def test_form_and_query_params_ignored(self):
        # Only the SEB HTTP headers are trusted; request parameters are not.
        valid = seb_hash('http://testserver/page', BEK)
        request = self.factory.post('/page', {'_seb_request_hash': valid})
        self.assertFalse(request_matches_seb(request, [BEK], []))


class SEBLaunchContextTestCase(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_without_config_file(self):
        contest = Contest(key='demo')
        context = seb_launch_context(self.factory.get('/page'), contest)
        self.assertIsNone(context['download_url'])
        self.assertIsNone(context['sebs_url'])
        self.assertEqual(context['quit_url'], '')
        self.assertEqual(context['install_url'], SEB_INSTALL_URL)

    def test_with_config_file_http(self):
        contest = Contest(key='demo', seb_quit_url='https://example.com/quit')
        contest.seb_config_file.name = 'seb_configs/demo.seb'
        context = seb_launch_context(self.factory.get('/page'), contest)
        config_path = reverse('contest_seb_config', args=('demo',))
        self.assertEqual(context['download_url'], 'http://testserver' + config_path)
        self.assertEqual(context['sebs_url'], 'seb://testserver' + config_path)
        self.assertEqual(context['quit_url'], 'https://example.com/quit')

    def test_with_config_file_https(self):
        contest = Contest(key='demo')
        contest.seb_config_file.name = 'seb_configs/demo.seb'
        context = seb_launch_context(self.factory.get('/page', secure=True), contest)
        config_path = reverse('contest_seb_config', args=('demo',))
        self.assertEqual(context['download_url'], 'https://testserver' + config_path)
        self.assertEqual(context['sebs_url'], 'sebs://testserver' + config_path)

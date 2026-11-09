import unittest

from judge.utils.fairplay_native_admission import session_digest
from judge.utils.fairplay_native_proxy import (ProxyAssertionError, sign_assertion, verify_assertion,
                                                verified_request_fingerprint)


class Cache:
    def __init__(self):
        self.keys = set()

    def add(self, key, value, timeout):
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


class ProxyAssertionTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(secret=b's' * 32, peer='127.0.0.1', trusted_peers={'127.0.0.1'},
                         method='GET', target='/contest/example?x=1', session_digest='a' * 64,
                         fingerprint='b' * 64, timestamp=1000, nonce='c' * 32,
                         replay_cache=Cache(), now=1000)
        self.args['signature'] = sign_assertion(*(self.args[x] for x in
            ('secret', 'method', 'target', 'session_digest', 'fingerprint', 'timestamp', 'nonce')))

    def test_valid_assertion_is_single_use(self):
        self.assertEqual(verify_assertion(**self.args), 'b' * 64)
        with self.assertRaises(ProxyAssertionError):
            verify_assertion(**self.args)

    def test_binding_peer_time_and_fingerprint_tampering_fails(self):
        for key, value in (('peer', '10.0.0.2'), ('method', 'POST'), ('target', '/contest/other'),
                           ('session_digest', 'd' * 64), ('fingerprint', 'd' * 64),
                           ('timestamp', 989), ('timestamp', 1001), ('nonce', 'd' * 32),
                           ('signature', 'd' * 64)):
            with self.subTest(key=key, value=value), self.assertRaises(ProxyAssertionError):
                verify_assertion(**dict(self.args, **{key: value}))

    def test_cache_failure_and_invalid_secrets_fail_closed(self):
        class Broken:
            def add(self, *args, **kwargs):
                raise RuntimeError('offline')
        with self.assertRaises(ProxyAssertionError):
            verify_assertion(**dict(self.args, replay_cache=Broken()))
        with self.assertRaises(ProxyAssertionError):
            verify_assertion(**dict(self.args, secret=b'short'))

    def test_django_request_binds_actual_session_and_path(self):
        class Session:
            session_key = 'actual-cookie'

        class Request:
            method = 'GET'
            session = Session()
            META = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FAIRPLAY_TLS_TIMESTAMP': '1000',
                    'HTTP_X_FAIRPLAY_TLS_FINGERPRINT': 'b' * 64, 'HTTP_X_FAIRPLAY_TLS_NONCE': 'c' * 32}

            def get_full_path(self):
                return '/contest/example?x=1'

        request = Request()
        digest = session_digest(request.session.session_key, 's' * 32)
        request.META['HTTP_X_FAIRPLAY_TLS_SIGNATURE'] = sign_assertion(
            b's' * 32, request.method, request.get_full_path(), digest, 'b' * 64, 1000, 'c' * 32)
        kwargs = dict(secret=b's' * 32, session_secret='s' * 32, trusted_peers={'127.0.0.1'}, now=1000)
        self.assertEqual(verified_request_fingerprint(request, replay_cache=Cache(), **kwargs), 'b' * 64)
        request.session.session_key = 'different-cookie'
        with self.assertRaises(ProxyAssertionError):
            verified_request_fingerprint(request, replay_cache=Cache(), **kwargs)

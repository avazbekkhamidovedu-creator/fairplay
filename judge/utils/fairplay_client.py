"""Signed calls from DKU OJ to FairPlay (docs/fairplay-bridge.md). POSTs are never retried."""
import json
import logging
from time import monotonic
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import requests
import urllib3
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

from judge.utils.fairplay import fairplay_enabled, fairplay_url_insecure
from judge.utils.fairplay_signing import signed_headers
from judge.utils.fairplay_identity import installation_id
from django.core.exceptions import ImproperlyConfigured

__all__ = ['API_PREFIX', 'CLASS_STATUS_CACHE_SECONDS', 'MAX_RESPONSE_BYTES', 'FairPlayClient', 'FairPlayError',
           'FairPlayUnreachable', 'QUICK_READ_TIMEOUT', 'cached_class_status', 'without_password']

API_PREFIX = '/api/integrations/dkuoj/v1'
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15
QUICK_READ_TIMEOUT = 3     # page loads and polls: a slow FairPlay must not hold a worker
# Answers are a few KB per student; a bigger or slower one is cut off so it can't hold a worker.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
CLASS_STATUS_CACHE_SECONDS = 60
CLASS_STATUS_CACHE_KEY = 'fpb:class-status:%d'

logger = logging.getLogger('judge.fairplay')


class FairPlayError(Exception):
    """FairPlay answered with an error (``status``) or could not be reached (``status`` None)."""

    def __init__(self, detail, status=None):
        super().__init__(detail)
        self.detail = detail
        self.status = status


class FairPlayUnreachable(FairPlayError):
    def __init__(self, detail=None):
        super().__init__(detail or _('FairPlay cannot be reached'), status=None)


def _q(value):
    return quote(str(value), safe='')


def acting_user_teaches_class(username, class_id):
    """Live check in the database, never from a cache."""
    from django.contrib.auth.models import User
    from judge.models import Class
    user = User.objects.filter(username=username, is_active=True).first() if username else None
    return user is not None and Class.get_visible_classes(user).filter(id=class_id).exists()


class FairPlayClient:
    def __init__(self, acting_user=None):
        self.acting_user = acting_user or ''

    @staticmethod
    def _require_class_login():
        if not getattr(settings, 'FAIRPLAY_CLASS_LOGIN_ENABLED', False):
            raise FairPlayError(_('Class logins are not available.'), status=404)

    def _require_teacher(self, class_id):
        if not acting_user_teaches_class(self.acting_user, class_id):
            raise FairPlayError(_('You do not manage this class.'), status=403)

    def request(self, method, path, body=None, read_timeout=None, acting_user=None, binary=False):
        base = (getattr(settings, 'FAIRPLAY_URL', '') or '').rstrip('/')
        if not base or not fairplay_enabled():
            raise FairPlayUnreachable()
        if fairplay_url_insecure(base):
            # Also a system check (judge.checks); this covers servers started without checks.
            raise FairPlayUnreachable(_('FairPlay cannot be reached: FAIRPLAY_URL must use https://'))
        try:
            identity = installation_id()
        except ImproperlyConfigured as error:
            raise FairPlayUnreachable(str(error)) from None
        parts = urlsplit(base + API_PREFIX + path)
        query = parse_qsl(parts.query, keep_blank_values=True)
        if any(name == 'installation_id' and value != identity for name, value in query):
            raise FairPlayUnreachable('FairPlay installation identity does not match.')
        query = [(name, value) for name, value in query if name != 'installation_id']
        url = parts._replace(query=urlencode(query + [('installation_id', identity)])).geturl()
        data = b'' if body is None else json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        parts = urlsplit(url)
        sent_path = parts.path + ('?' + parts.query if parts.query else '')
        headers = signed_headers(method, sent_path, data,
                                 acting_user=self.acting_user if acting_user is None else acting_user)
        headers['Accept'] = 'application/pdf' if binary else 'application/json'
        headers['Accept-Encoding'] = 'identity'
        if body is not None:
            headers['Content-Type'] = 'application/json'
        read_timeout = read_timeout or READ_TIMEOUT
        # The whole call (connect + all of the body) gets at most connect + read timeout.
        deadline = monotonic() + min(CONNECT_TIMEOUT, read_timeout) + read_timeout
        try:
            response = requests.request(method, url, data=data or None, headers=headers,
                                        timeout=(min(CONNECT_TIMEOUT, read_timeout), read_timeout),
                                        allow_redirects=False, stream=True)
        except requests.RequestException as e:
            logger.warning('FairPlay cannot be reached: %s %s: %s', method, path, e.__class__.__name__)
            raise FairPlayUnreachable()
        try:
            raw = _read_capped(response, deadline)
        finally:
            response.close()
        if binary and response.status_code == 200:
            if 'application/pdf' not in response.headers.get('Content-Type', '').lower():
                raise FairPlayError(_('FairPlay sent an unexpected answer.'), status=response.status_code)
            return raw
        try:
            payload = json.loads(raw.decode('utf-8')) if raw else None
        except (ValueError, UnicodeDecodeError):
            payload = None
        if response.status_code >= 400:
            detail = payload.get('detail') if isinstance(payload, dict) else None
            if response.status_code >= 500 and not detail:
                raise FairPlayError(_('FairPlay had a problem (error %d). Try again in a minute.')
                                    % response.status_code, status=response.status_code)
            raise FairPlayError(str(detail or _('FairPlay refused the request (error %d).') % response.status_code),
                                status=response.status_code)
        if not isinstance(payload, dict):
            raise FairPlayError(_('FairPlay sent an unexpected answer.'), status=response.status_code)
        return payload

    def create_class_login(self, klass):
        self._require_class_login()
        self._require_teacher(klass.id)
        try:
            return self.request('POST', '/classes/%d/login' % klass.id,
                                {'class_name': klass.name, 'course_name': klass.organization.name})
        finally:
            cache.delete(CLASS_STATUS_CACHE_KEY % klass.id)

    def stop_class_login(self, klass):
        self._require_class_login()
        self._require_teacher(klass.id)
        try:
            return self.request('DELETE', '/classes/%d/login' % klass.id)
        finally:
            cache.delete(CLASS_STATUS_CACHE_KEY % klass.id)

    def class_status(self, class_id, use_cache=True, read_timeout=None):
        self._require_class_login()
        """Only ``use_cache=False`` returns the class login password; the cached copy never holds it."""
        key = CLASS_STATUS_CACHE_KEY % class_id
        if use_cache:
            cached = cache.get(key)
            if cached is not None:
                return cached
        self._require_teacher(class_id)
        status = self.request('GET', '/classes/%d/status' % class_id, read_timeout=read_timeout)
        safe = without_password(status)
        cache.set(key, safe, CLASS_STATUS_CACHE_SECONDS)
        return safe if use_cache else status

    def reset_student(self, netid, class_id):
        self._require_class_login()
        self._require_teacher(class_id)
        try:
            return self.request('POST', '/students/%s/reset' % _q(netid), {'class_id': class_id})
        finally:
            cache.delete(CLASS_STATUS_CACHE_KEY % class_id)

    def exam(self, key, read_timeout=None):
        return self.request('GET', '/exams/%s' % _q(key), read_timeout=read_timeout)

    def open_exam(self, key, open_to):
        return self.request('POST', '/exams/%s/open' % _q(key), {'open_to': open_to})

    def close_exam(self, key):
        return self.request('POST', '/exams/%s/close' % _q(key))

    def add_time(self, key, minutes):
        return self.request('POST', '/exams/%s/add-time' % _q(key), {'minutes': int(minutes)})

    def new_code(self, key):
        return self.request('POST', '/exams/%s/new-code' % _q(key))

    def approve(self, key, netid, reason=None):
        body = {'netid': netid}
        if reason:
            body['reason'] = reason
        return self.request('POST', '/exams/%s/approve' % _q(key), body)

    def release_device(self, key, netid, reason):
        return self.request('POST', '/exams/%s/students/%s/release-device' % (_q(key), _q(netid)),
                            {'reason': reason})

    def reopen(self, key, netid, minutes, reason):
        return self.request('POST', '/exams/%s/students/%s/reopen' % (_q(key), _q(netid)),
                            {'minutes': int(minutes), 'reason': reason})

    def extra_time(self, key, netid, minutes, reason):
        return self.request('POST', '/exams/%s/students/%s/extra-time' % (_q(key), _q(netid)),
                            {'minutes': int(minutes), 'reason': reason})

    def offline_exit(self, key, netid, mac_code):
        return self.request('POST', '/exams/%s/students/%s/offline-exit' % (_q(key), _q(netid)),
                            {'mac_code': mac_code})

    def native_exit(self, key, netid, mac_code, reason):
        return self.request('POST', '/exams/%s/students/%s/native-exit' % (_q(key), _q(netid)),
                            {'mac_code': mac_code, 'reason': reason})

    def set_settings(self, key, settings, read_timeout=None):
        """Idempotent; once a student has started only ``review`` may change (409 otherwise)."""
        return self.request('POST', '/exams/%s/settings' % _q(key), settings, read_timeout=read_timeout)

    def sync_ping(self, exam_key=None, class_id=None, read_timeout=None, event='changed',
                  exam_keys=None, class_ids=None, organization_ids=None):
        body = {'event': event, 'exam_keys': sorted(set(exam_keys or ([exam_key] if exam_key else []))),
                'class_ids': sorted(set(class_ids or ([int(class_id)] if class_id is not None else []))),
                'organization_ids': sorted(set(organization_ids or []))}
        if exam_key is not None:
            body['exam_key'] = exam_key
        if class_id is not None:
            body['class_id'] = int(class_id)
        return self.request('POST', '/sync', body, read_timeout=read_timeout)

    def student_report(self, key, netid):
        return self.request('GET', '/exams/%s/students/%s/report' % (_q(key), _q(netid)))


def _read_capped(response, deadline):
    """The body, at most ``MAX_RESPONSE_BYTES`` and read by ``deadline`` (``time.monotonic``)."""
    try:
        declared = int(response.headers.get('Content-Length') or 0)
    except (TypeError, ValueError, AttributeError):
        declared = 0
    if declared > MAX_RESPONSE_BYTES:
        raise FairPlayError(_('FairPlay sent an answer that is too large.'), status=response.status_code)
    chunks, size = [], 0
    try:
        while True:
            if monotonic() > deadline:
                raise FairPlayUnreachable()
            # read1 returns after one socket read, so a slow trickle still meets the deadline check.
            chunk = response.raw.read1(CHUNK_BYTES, decode_content=False)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise FairPlayError(_('FairPlay sent an answer that is too large.'), status=response.status_code)
            chunks.append(chunk)
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError):
        raise FairPlayUnreachable()
    return b''.join(chunks)


def without_password(status):
    """A copy of a class status without the class login password (safe to cache)."""
    if not isinstance(status, dict) or not isinstance(status.get('login'), dict):
        return status
    login = {k: v for k, v in status['login'].items() if k != 'password'}
    return dict(status, login=login)


def cached_class_status(class_id):
    """From DKU OJ's cache only; never calls FairPlay."""
    return cache.get(CLASS_STATUS_CACHE_KEY % class_id)

"""Signed calls from DKU OJ to FairPlay (docs/fairplay-bridge.md). POSTs are never retried."""
import json
import logging
from time import monotonic
from urllib.parse import quote, urlsplit

import requests
import urllib3
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

from judge.utils.fairplay import fairplay_enabled, fairplay_url_insecure
from judge.utils.fairplay_signing import signed_headers

__all__ = ['API_PREFIX', 'CLASS_STATUS_CACHE_SECONDS', 'MAX_RESPONSE_BYTES', 'FairPlayClient', 'FairPlayError',
           'FairPlayUnreachable', 'QUICK_READ_TIMEOUT', 'cached_class_status', 'format_fp_time', 'without_password']

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
        super().__init__(detail or _('FairPlay not reachable'), status=None)


def _q(value):
    return quote(str(value), safe='')


class FairPlayClient:
    def __init__(self, acting_user=None):
        self.acting_user = acting_user or ''

    def request(self, method, path, body=None, read_timeout=None, acting_user=None, binary=False):
        base = (getattr(settings, 'FAIRPLAY_URL', '') or '').rstrip('/')
        if not base or not fairplay_enabled():
            raise FairPlayUnreachable()
        if fairplay_url_insecure(base):
            # Also a system check (judge.checks); this covers servers started without checks.
            raise FairPlayUnreachable(_('FairPlay not reachable: FAIRPLAY_URL must use https://'))
        url = base + API_PREFIX + path
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
            logger.warning('FairPlay not reachable: %s %s: %s', method, path, e.__class__.__name__)
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
        try:
            return self.request('POST', '/classes/%d/login' % klass.id,
                                {'class_name': klass.name, 'course_name': klass.organization.name})
        finally:
            cache.delete(CLASS_STATUS_CACHE_KEY % klass.id)

    def stop_class_login(self, klass):
        try:
            return self.request('DELETE', '/classes/%d/login' % klass.id)
        finally:
            cache.delete(CLASS_STATUS_CACHE_KEY % klass.id)

    def class_status(self, class_id, use_cache=True, read_timeout=None):
        """Only ``use_cache=False`` returns the class login password; the cached copy never holds it."""
        key = CLASS_STATUS_CACHE_KEY % class_id
        if use_cache:
            cached = cache.get(key)
            if cached is not None:
                return cached
        status = self.request('GET', '/classes/%d/status' % class_id, read_timeout=read_timeout)
        safe = without_password(status)
        cache.set(key, safe, CLASS_STATUS_CACHE_SECONDS)
        return safe if use_cache else status

    def reset_student(self, netid, class_id):
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

    def approve(self, key, netid):
        return self.request('POST', '/exams/%s/approve' % _q(key), {'netid': netid})

    def set_settings(self, key, settings, read_timeout=None):
        """Idempotent; once a student has started only ``review`` may change (409 otherwise)."""
        return self.request('POST', '/exams/%s/settings' % _q(key), settings, read_timeout=read_timeout)

    def access_codes_pdf(self, exam_key, acting):
        return self.request('GET', '/exams/%s/access-codes.pdf' % _q(exam_key), acting_user=acting, binary=True)

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


def format_fp_time(value, fmt=None):
    """A FairPlay ISO-8601 time as local text, or the raw value if it isn't one."""
    from django.template.defaultfilters import date
    from django.templatetags.tz import localtime
    from django.utils.dateparse import parse_datetime
    if not value:
        return ''
    try:
        parsed = parse_datetime(str(value).replace('Z', '+00:00'))
    except ValueError:
        parsed = None
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        from datetime import timezone as dt_timezone
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return date(localtime(parsed), fmt or _('N j, Y, g:i a'))

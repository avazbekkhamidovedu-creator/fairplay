"""Sign-in of the FairPlay homework app: a one-time ticket that the FairPlay server redeems."""
import base64
import hashlib
import hmac
import json
import re
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from judge.views.api.fairplay import _error, fairplay_api

__all__ = ['app_login_done', 'app_login_mint', 'app_login_redeem']

TICKET_TTL = 60
USED_TTL = 120
MINT_LIMIT = 10
MINT_WINDOW = 60
REDEEM_MAX_BYTES = 4096
PARAM_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
TICKET_KEY = 'fpb:applogin:%s'
USED_KEY = 'fpb:applogin:used:%s'
RATE_KEY = 'fpb:applogin:rate:%s'


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


def _plain(status, text):
    return _no_store(HttpResponse(text, status=status, content_type='text/plain; charset=utf-8'))


def _rate_limited(request):
    if not request.session.session_key:
        request.session.create()
    key = RATE_KEY % request.session.session_key
    cache.add(key, 0, MINT_WINDOW)
    try:
        return cache.incr(key) > MINT_LIMIT
    except ValueError:
        cache.set(key, 1, MINT_WINDOW)
        return False


def _done_url():
    base = (getattr(settings, 'FAIRPLAY_OJ_PUBLIC_URL', '') or '').rstrip('/')
    if not base or not (base.lower().startswith('https://') or settings.DEBUG and base.lower().startswith('http://')):
        return None
    return base + reverse('fairplay_app_login_done')


@login_required
@require_GET
def app_login_mint(request):
    state = request.GET.get('state', '')
    challenge = request.GET.get('challenge', '')
    if not PARAM_RE.fullmatch(state) or not PARAM_RE.fullmatch(challenge):
        return _plain(400, 'Invalid sign-in request.')
    user = request.user
    if user.is_superuser or user.is_staff or not user.is_active:
        return _plain(403, 'This account cannot sign in to the FairPlay app.')
    done_url = _done_url()
    if done_url is None:
        return _plain(503, 'FairPlay app sign-in is not configured on this site.')
    if _rate_limited(request):
        return _plain(429, 'Too many sign-in attempts. Wait a minute and try again.')
    ticket = secrets.token_urlsafe(32)
    cache.set(TICKET_KEY % _digest(ticket), {'user': user.id, 'challenge': challenge, 'state': state}, TICKET_TTL)
    return _no_store(HttpResponseRedirect('%s#ticket=%s&state=%s' % (done_url, ticket, state)))


@require_GET
def app_login_done(request):
    return _no_store(render(request, 'fairplay/app_login_done.html'))


@fairplay_api(max_bytes=REDEEM_MAX_BYTES)
def app_login_redeem(request):
    if request.method != 'POST':
        return _no_store(_error(405, 'POST only.'))
    try:
        body = json.loads(request.fairplay_body.decode('utf-8'))
        ticket, verifier = body['ticket'], body['verifier']
        if not isinstance(ticket, str) or not isinstance(verifier, str) or not ticket or not verifier:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return _no_store(_error(400, 'Expected {"ticket", "verifier"}.'))
    digest = _digest(ticket)
    entry = cache.get(TICKET_KEY % digest)
    if entry is None or not cache.add(USED_KEY % digest, 1, USED_TTL):
        return _no_store(_error(404, 'Unknown, used or expired ticket.'))
    cache.delete(TICKET_KEY % digest)
    seen = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('utf-8')).digest()).rstrip(b'=').decode('ascii')
    if not hmac.compare_digest(seen, entry['challenge']):
        return _no_store(_error(409, 'The verifier does not match.'))
    user = User.objects.filter(id=entry['user'], is_active=True, is_staff=False, is_superuser=False).first()
    if user is None:
        return _no_store(_error(404, 'Unknown, used or expired ticket.'))
    return _no_store(JsonResponse({'netid': user.username, 'first_name': user.first_name,
                                   'last_name': user.last_name, 'email': user.email},
                                  json_dumps_params={'ensure_ascii': False}))

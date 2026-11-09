"""Signed browser context. This reports OJ state; it is NOT native-app admission.

Off until an Ed25519 signing key is configured. The matching public key belongs
in the signed app bundle, never in JavaScript or a student preference.
"""
import base64
import json
import re
import time
from django.conf import settings
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET


def record_browser_completion(request, participation):
    request.session.pop('fairplay_browser_completed', None)
    if not getattr(settings, 'FAIRPLAY_BROWSER_SIGNING_KEY', '') or participation.spectate:
        return
    # A collected draft row remains but its content is cleared. A failed or
    # in-flight harvest still has content and must never authorize auto-unlock.
    drafts = participation.drafts.select_related('problem', 'language')
    if any(draft.has_content() for draft in drafts):
        return
    request.session['fairplay_browser_completed'] = {
        'user': str(request.user.pk), 'contest': participation.contest.key,
        'participation': str(participation.pk), 'at': int(time.time()),
    }


@require_GET
def browser_context(request):
    secret = getattr(settings, 'FAIRPLAY_BROWSER_SIGNING_KEY', '')
    nonce = request.GET.get('nonce', '')
    if not secret or not request.user.is_authenticated or not re.fullmatch(r'[A-Za-z0-9-]{32,64}', nonce):
        raise Http404()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        signer = Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret, validate=True))
    except (ValueError, TypeError):
        raise Http404()
    now = int(time.time())
    payload = {'v': 1, 'nonce': nonce, 'expires': now + 30, 'user': str(request.user.pk),
               'contest': '', 'participation': '', 'state': 'idle', 'sites': []}
    participation = request.profile.current_contest
    if participation:
        request.session.pop('fairplay_browser_completed', None)
    if participation and not participation.spectate and not participation.is_disqualified:
        payload.update(contest=participation.contest.key, participation=str(participation.pk), state='active')
    elif participation is None:
        receipt = request.session.get('fairplay_browser_completed') or {}
        if (receipt.get('user') == payload['user'] and type(receipt.get('at')) is int
                and 0 <= now - receipt['at'] <= 300):
            payload.update(contest=receipt['contest'], participation=receipt['participation'], state='completed')
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    response = JsonResponse({'payload': base64.b64encode(encoded).decode(),
                             'signature': base64.b64encode(signer.sign(encoded)).decode()})
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response

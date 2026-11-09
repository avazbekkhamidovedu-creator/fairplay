"""Device-bound lifecycle API. Disabled until an authenticated TLS ingress is configured."""
import base64
import logging
import json
import re
import time
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from judge.models import Contest, FairPlayDeviceSession
from judge.utils.fairplay_native_admission import session_digest
from judge.utils.fairplay_native_proxy import verified_request_fingerprint
from judge.utils.fairplay_device_session import claim_device, renew_for_request, bind_lock, DeviceSessionDenied

logger = logging.getLogger('judge.fairplay')
_last_logged = {}


def deny(request, code, key=None):
    """The 404 is deliberately bare; the reason goes only to the server log, at most once a minute per case."""
    user = request.user.username if getattr(request.user, 'is_authenticated', False) else '-'
    ident = (code, key, user)
    now = time.monotonic()
    if now - _last_logged.get(ident, -60) >= 60:
        if len(_last_logged) > 512:
            _last_logged.clear()
        _last_logged[ident] = now
        logger.warning('FairPlay native refusal %s on %s %s contest=%s user=%s', code, request.method,
                       request.path.rsplit('/', 1)[0], key or '-', user)
    return Http404()


def request_identity(request):
    cached = getattr(request, '_fairplay_identity', None)
    if cached is not None:
        return cached
    if getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is not True:
        raise deny(request, 'native-disabled')
    if not request.user.is_authenticated:
        raise deny(request, 'not-authenticated')
    if getattr(request, 'impersonator', None):
        raise deny(request, 'impersonation')
    backend = settings.CACHES.get('default', {}).get('BACKEND', '')
    local_fixture = (settings.DEBUG and getattr(settings, 'FAIRPLAY_NATIVE_LOOPBACK_FIXTURE', False) is True
                     and request.META.get('REMOTE_ADDR') in ('127.0.0.1', '::1'))
    if backend not in ('django.core.cache.backends.redis.RedisCache', 'django_redis.cache.RedisCache') and not local_fixture:
        raise deny(request, 'cache-backend')
    try:
        if getattr(settings, 'FAIRPLAY_NATIVE_INGRESS_MODE', '') == 'nginx-mtls':
            from judge.utils.fairplay_native_ingress import verified_ingress_fingerprint
            fingerprint = verified_ingress_fingerprint(request,
                secret=settings.FAIRPLAY_NATIVE_INGRESS_SECRET,
                trusted_peers=frozenset(settings.FAIRPLAY_NATIVE_PROXY_PEERS))
            return fingerprint, session_digest(request.session.session_key, settings.FAIRPLAY_NATIVE_SESSION_BINDING_SECRET)
        secret = settings.FAIRPLAY_NATIVE_PROXY_SECRET
        binding_secret = settings.FAIRPLAY_NATIVE_SESSION_BINDING_SECRET
        fingerprint = verified_request_fingerprint(request, secret=secret.encode(), session_secret=binding_secret,
            trusted_peers=frozenset(settings.FAIRPLAY_NATIVE_PROXY_PEERS), replay_cache=cache)
        return fingerprint, session_digest(request.session.session_key, binding_secret)
    except (ValueError, AttributeError, TypeError) as error:
        label = getattr(error, 'code', None)
        if not (isinstance(label, str) and re.fullmatch(r'[a-z0-9-]{1,40}', label)):
            label = re.sub(r'[^a-z0-9]+', '-', str(error).lower()).strip('-')[:40] or type(error).__name__.lower()
        code = 'identity:' + label
        raise deny(request, code) from None


INSTRUCTOR_TEXT = ('You are the instructor of this exam. Instructors cannot take it in FairPlay: '
                   'open it with a student or test-student account.')
NOT_FAIRPLAY_TEXT = 'FairPlay mode is not turned on for this exam.'


def refusal_response(text, reason):
    # Unsigned and never an unlock: the Mac may show `error` only for the listed reasons and stays locked.
    response = JsonResponse({'error': text, 'reason': reason}, status=403)
    response['Cache-Control'] = 'private, no-store'
    return response


def native_contest(request, key):
    """(contest, None) when this account may claim, else (None, response) or Http404 for anything not safe to reveal."""
    from judge.utils.fairplay import is_fairplay_exam
    from judge.views.fairplay_launch import student_can_launch
    contest = Contest.objects.filter(key=key).first()
    if contest is None:
        raise deny(request, 'invalid-key', key)
    if contest.is_editable_by(request.user):
        return None, refusal_response(INSTRUCTOR_TEXT if is_fairplay_exam(contest) else NOT_FAIRPLAY_TEXT,
                                      'instructor' if is_fairplay_exam(contest) else 'not_fairplay')
    if not contest.is_visible:
        raise deny(request, 'not-visible', key)
    if not is_fairplay_exam(contest):
        raise deny(request, 'not-fairplay-exam', key)
    if not student_can_launch(request, contest):
        from judge.utils.fairplay import fairplay_enabled
        from judge.utils.fairplay_audience import audience_descriptor
        from judge.utils.fairplay_package import exam_roster
        reasons = {
            'fairplay-not-configured': 'FairPlay is not set up on this server yet. Ask your instructor or administrator.',
            'audience-unconfirmed': 'The student list for this exam is not confirmed yet. Ask your instructor.',
            'launch-refused': 'This exam cannot be started in FairPlay right now. Ask your instructor.'}
        if not fairplay_enabled():
            code = 'fairplay-not-configured'
        elif not audience_descriptor(contest)['confirmed']:
            code = 'audience-unconfirmed'
        elif not exam_roster(contest).filter(pk=request.profile.pk).exists():
            raise deny(request, 'not-on-roster', key)
        else:
            code = 'launch-refused'
        deny(request, code, key)
        return None, refusal_response(reasons[code], 'not_fairplay')
    return contest, None


_SIGNING_KEYS = {}


def signing_key():
    """The Ed25519 signing key, decoded once per process for the configured value."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = getattr(settings, 'FAIRPLAY_BROWSER_SIGNING_KEY', None)
    try:
        key = _SIGNING_KEYS.get(raw)
        if key is None:
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
            _SIGNING_KEYS.clear()
            _SIGNING_KEYS[raw] = key
        return key
    except (ValueError, TypeError, AttributeError):
        raise Http404() from None


def check_signing(request):
    """Pure: the nonce has the right shape and the signing key is configured (no state is built or written)."""
    nonce = request.GET.get('nonce', '') if request.method == 'GET' else request.POST.get('nonce', '')
    if not re.fullmatch(r'[A-Za-z0-9-]{32,64}', nonce):
        raise Http404()
    signing_key()


def close_if_due(record):
    """Run the deadline close once, only when this student's deadline has passed; otherwise the record as it is."""
    from django.utils import timezone
    from judge.utils.fairplay_device_session import personal_deadline
    if isinstance(record, FairPlayDeviceSession) and record.state in ('active', 'finishing') \
            and personal_deadline(record) <= timezone.now():
        return close_after_deadline(record)
    return record


def warm_sites(contest):
    from judge.utils.fairplay_editor import refresh_lock_sites
    refresh_lock_sites(contest)


def prefetch_extras(contest):
    """Everything the signed state reads from the cache, fetched BEFORE any row lock is taken."""
    from judge.utils.fairplay_editor import _log_hourly, oj_asset_origins, signed_lock_sites
    try:
        warm_sites(contest)
        sites = signed_lock_sites(contest)
    except Exception:
        _log_hourly('sites', 'FairPlay could not work out the allowed sites; signing none')
        sites = {'pages': [], 'resources': []}
    return {'sites': sites, 'assets': oj_asset_origins()}


def signed_state(request, session, hold=None, extras=None):
    nonce = request.GET.get('nonce', '') if request.method == 'GET' else request.POST.get('nonce', '')
    if not re.fullmatch(r'[A-Za-z0-9-]{32,64}', nonce):
        raise Http404()
    key = signing_key()
    payload = {'v': 2, 'nonce': nonce, 'expires': int(time.time()) + 30,
               'session': str(session.pk), 'device': session.fingerprint, 'user': str(session.user_id),
               'contest': session.contest.key, 'participation': str(session.participation_id or ''),
               'state': session.state}
    payload['lease_until'] = min(int(session.lease_until.timestamp()), payload['expires'])
    if isinstance(session, FairPlayDeviceSession) and session.state == 'active':
        if hold is None:
            from judge.utils.fairplay_device_session import session_on_hold
            hold = session_on_hold(session)
        if hold:
            payload['hold'] = True
    if isinstance(session, FairPlayDeviceSession):
        from judge.models import FairPlayDeviceAudit
        lock = FairPlayDeviceAudit.objects.filter(session=session, action='lock').order_by('-pk').values_list('reason', flat=True).first()
        if lock:
            payload['lock'] = lock
    if isinstance(session, FairPlayDeviceSession) and session.state in ('active', 'finishing'):
        from judge.utils.fairplay_device_session import personal_deadline
        payload['deadline'] = int(personal_deadline(session).timestamp())
    payload['sites'] = []
    if session.state == 'active' and not payload.get('hold'):
        extras = extras or prefetch_extras(session.contest)
        sites = extras['sites']
        payload['sites'] = sites['pages']
        if sites['pages']:
            payload['resources'] = sites['resources']
        payload['assets'] = extras['assets']
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    response = JsonResponse({'payload': base64.b64encode(encoded).decode(),
                             'signature': base64.b64encode(key.sign(encoded)).decode()})
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@require_POST
def native_claim(request, key):
    fingerprint, digest = request_identity(request)
    contest, refusal = native_contest(request, key)
    if refusal is not None:
        return refusal
    extras = prefetch_extras(contest)
    owner = known_lock_owner(request.POST.get('lock', ''))
    close_blocking_session(request.user.pk)
    return _claim_locked(request, contest, fingerprint, digest, extras, owner)


def close_blocking_session(user_id):
    """A joined session of this student whose deadline has passed is closed in its own transaction first, so a
    refused claim cannot roll the close back."""
    from django.utils import timezone
    from judge.models import FairPlayDeviceSlot
    from judge.utils.fairplay_device_session import personal_deadline
    slot = FairPlayDeviceSlot.objects.select_related('session__contest', 'session__participation').filter(
        user_id=user_id, session__isnull=False).first()
    if slot is not None and slot.session.state in ('active', 'finishing') and slot.session.participation_id \
            and not slot.session.participation.is_disqualified and personal_deadline(slot.session) <= timezone.now():
        close_after_deadline(slot.session)


def known_lock_owner(lock):
    """The cached owner of a Mac code, read before any lock is held (None when unknown or no code)."""
    from judge.utils.fairplay_device_session import lock_owner, normalize_mac_code
    code = normalize_mac_code(lock)
    return lock_owner(code) if code else None


@transaction.atomic
def _claim_locked(request, contest, fingerprint, digest, extras=None, owner=None):
    try:
        session = claim_device(user_id=request.user.pk, contest=contest, fingerprint=fingerprint, browser_digest=digest)
    except DeviceSessionDenied as error:
        if error.reason:
            return refusal_response(str(error), error.reason)
        return JsonResponse({'error': str(error)}, status=409)
    if not bind_lock(session, request.POST.get('lock', ''), owner=owner):
        transaction.set_rollback(True)
        return JsonResponse({'error': 'This Mac code belongs to another exam or student.'}, status=409)
    return signed_state(request, close_if_due(session), extras=extras)


def owned_record(request, session_id):
    fingerprint, digest = request_identity(request)
    return get_object_or_404(FairPlayDeviceSession.objects.select_related('contest'), pk=session_id,
                            user_id=request.user.pk, fingerprint=fingerprint, browser_digest=digest)


@require_GET
def native_state(request, session_id):
    # Revoked/finished devices can retrieve only their own signed terminal state.
    record = owned_record(request, session_id)
    check_signing(request)
    extras = prefetch_extras(record.contest)
    return signed_state(request, close_if_due(record), extras=extras)


@require_POST
def native_renew(request, session_id):
    fingerprint, digest = request_identity(request)
    known = FairPlayDeviceSession.objects.select_related('contest').filter(pk=session_id).first()
    extras = prefetch_extras(known.contest) if known is not None else None
    return _renew_locked_view(request, session_id, fingerprint, digest, extras)


@transaction.atomic
def _renew_locked_view(request, session_id, fingerprint, digest, extras=None):
    request.no_profile_update = True
    try:
        renewed = renew_for_request(session_id=session_id, user_id=request.user.pk,
                                    fingerprint=fingerprint, browser_digest=digest)
    except DeviceSessionDenied:
        record = get_object_or_404(FairPlayDeviceSession.objects.select_related('contest'), pk=session_id,
                                   user_id=request.user.pk, fingerprint=fingerprint, browser_digest=digest)
        return signed_state(request, close_after_deadline(record), extras=extras)
    if renewed is None:
        raise Http404()
    record, hold = renewed
    return signed_state(request, close_if_due(record), hold, extras=extras)


def deadline_close_may_be_undone(record):
    """True when the last move to 'finishing' was the deadline close, not an explicit hand-in."""
    from judge.models import FairPlayDeviceAudit
    audit = FairPlayDeviceAudit.objects.filter(session=record)
    if not audit.filter(action__in=('harvest-queued', 'auto-release-after-deadline')).exists():
        return False
    reopened = audit.filter(action__in=('deadline-extended', 'reopen-window')).order_by('-pk') \
        .values_list('pk', flat=True).first()
    handed_in = audit.filter(action='hand-in-started')
    if reopened is not None:
        handed_in = handed_in.filter(pk__gt=reopened)
    return not handed_in.exists()


def mark_hand_in(record):
    """An explicit hand-in: such a 'finishing' session never goes back to 'active' when the deadline moves."""
    from judge.models import FairPlayDeviceAudit
    FairPlayDeviceAudit.objects.create(session=record, actor_id=record.user_id, action='hand-in-started',
                                      reason='The student handed in.')


def queue_harvest(participation_id):
    """Hand the student's remaining answers to the existing background harvest after the transaction commits."""
    def run():
        try:
            from judge.tasks.assignment_harvest import harvest_participation_drafts
            harvest_participation_drafts.delay(participation_id)
        except Exception:
            logger.exception('Could not queue the harvest of participation %s', participation_id)
    transaction.on_commit(run)


HARVEST_REQUEUE_AFTER = 120


def close_after_deadline(record):
    """Past the student's own deadline nothing else would ever end the attempt, so the device would stay locked.

    A joined attempt is moved to `finishing` and its answers go to the background harvest (never inline in this
    request); a later contact confirms completion. Unjoined attempts are aborted at once."""
    from django.utils import timezone
    from judge.models import FairPlayDeviceAudit, FairPlayDeviceSlot, Profile
    from judge.utils.assignment_harvest import assignment_harvest_enabled
    from judge.utils.fairplay_device_session import (RELEASE_AFTER_DEADLINE_GRACE, abort_device, begin_finishing,
                                                     confirm_completed, personal_deadline, release_attachment)
    from datetime import timedelta
    from judge.utils.fairplay_device_session import LEASE_SECONDS
    with transaction.atomic():
        Profile.objects.select_for_update().get(user_id=record.user_id)
        record = FairPlayDeviceSession.objects.select_for_update().select_related(
            'contest', 'participation').get(pk=record.pk)
        if record.state not in ('active', 'finishing'):
            return record
        now = timezone.now()
        deadline = personal_deadline(record)
        if deadline > now:
            # A 'finishing' session made by the DEADLINE close (never by an explicit hand-in) goes back to 'active'
            # when the instructor moved the student's deadline into the future; an explicit hand-in stays finishing.
            if record.state == 'finishing' and deadline_close_may_be_undone(record):
                record.state = 'active'
                record.lease_until = min(deadline, now + timedelta(seconds=LEASE_SECONDS))
                record.save(update_fields=['state', 'lease_until'])
                FairPlayDeviceAudit.objects.create(session=record, actor_id=record.user_id, action='deadline-extended',
                                                  reason='The deadline moved; the attempt is open again.')
            return record
        arguments = dict(session_id=record.pk, user_id=record.user_id,
                         fingerprint=record.fingerprint, browser_digest=record.browser_digest)
        try:
            if record.participation_id is None:
                return abort_device(**arguments)
            if record.state == 'finishing':
                try:
                    record = confirm_completed(**arguments)
                except DeviceSessionDenied:
                    if assignment_harvest_enabled():
                        last = FairPlayDeviceAudit.objects.filter(session=record, action='harvest-queued') \
                            .order_by('-pk').values_list('created_at', flat=True).first()
                        if last is None or (now - last).total_seconds() >= HARVEST_REQUEUE_AFTER:
                            FairPlayDeviceAudit.objects.create(session=record, actor_id=record.user_id, action='harvest-queued',
                                                              reason='The hand-in is still pending; queued again.')
                            queue_harvest(record.participation_id)
                else:
                    Profile.objects.filter(user_id=record.user_id, current_contest_id=record.participation_id).update(
                        current_contest=None)
                    return record
            elif assignment_harvest_enabled():
                record = begin_finishing(**arguments)
                FairPlayDeviceAudit.objects.create(session=record, actor_id=record.user_id, action='harvest-queued',
                                                  reason='The deadline passed; the answers go to the background hand-in.')
                queue_harvest(record.participation_id)
                return record
        except DeviceSessionDenied:
            record = FairPlayDeviceSession.objects.select_related('contest', 'participation').get(pk=record.pk)
        if now >= deadline + RELEASE_AFTER_DEADLINE_GRACE:
            record = FairPlayDeviceSession.objects.select_for_update().select_related('contest', 'participation').get(pk=record.pk)
            if record.state in ('active', 'finishing') and personal_deadline(record) + RELEASE_AFTER_DEADLINE_GRACE <= timezone.now():
                record.state = 'released'
                record.ended_at = now
                record.lease_until = now
                record.save(update_fields=['state', 'ended_at', 'lease_until'])
                FairPlayDeviceSlot.objects.filter(user_id=record.user_id, session=record).update(session=None)
                release_attachment(record, now)
                FairPlayDeviceAudit.objects.create(session=record, actor_id=record.user_id,
                                                  action='auto-release-after-deadline',
                                                  reason='Released automatically: the deadline passed and the answers could not be handed in yet.')
        return record


@require_POST
@transaction.atomic
def native_finish(request, session_id):
    # Validate the response request before touching answers or uploaded files.
    if request.POST.get('confirm') != '1' or not re.fullmatch(r'[A-Za-z0-9-]{32,64}', request.POST.get('nonce', '')):
        raise Http404()
    record = owned_record(request, session_id)
    from judge.utils.fairplay_device_session import begin_finishing, confirm_completed
    from judge.utils.assignment_harvest import assignment_harvest_enabled, harvest_participation
    if record.state in ('completed', 'released'):
        return signed_state(request, record)
    harvest_on = assignment_harvest_enabled()
    arguments = dict(session_id=record.pk, user_id=record.user_id,
                     fingerprint=record.fingerprint, browser_digest=record.browser_digest)
    try:
        record = begin_finishing(**arguments)
    except DeviceSessionDenied as error:
        return JsonResponse({'error': str(error)}, status=409)
    mark_hand_in(record)
    check_signing(request)
    # The account lock serializes this with all admitted answer writes and teacher release.
    # Harvest retains drafts on failure. A retry remains in finishing, never active.
    if harvest_on:
        harvest_participation(record.participation, judge_on_commit=True)
    try:
        record = confirm_completed(**arguments, keep_drafts=not harvest_on)
    except DeviceSessionDenied:
        return signed_state(request, record)
    from judge.models import Profile
    Profile.objects.filter(user_id=record.user_id, current_contest_id=record.participation_id).update(current_contest=None)
    return signed_state(request, record)


@require_POST
@transaction.atomic
def native_abort(request, session_id):
    if request.POST.get('confirm') != '1':
        raise Http404()
    record = owned_record(request, session_id)
    check_signing(request)
    if record.state not in ('completed', 'released'):
        from judge.utils.fairplay_device_session import abort_device
        try:
            record = abort_device(session_id=record.pk, user_id=record.user_id,
                                  fingerprint=record.fingerprint, browser_digest=record.browser_digest)
        except DeviceSessionDenied as error:
            return JsonResponse({'error': str(error)}, status=409)
    return signed_state(request, record)


@require_POST
def native_recover(request, key):
    fingerprint, digest = request_identity(request)
    contest, refusal = native_contest(request, key)
    if refusal is not None:
        return refusal
    extras = prefetch_extras(contest)
    owner = known_lock_owner(request.POST.get('lock', ''))
    return _recover_locked(request, contest, fingerprint, digest, extras, owner)


@transaction.atomic
def _recover_locked(request, contest, fingerprint, digest, extras=None, owner=None):
    from judge.utils.fairplay_device_session import recover_device
    try:
        record = recover_device(user_id=request.user.pk, contest=contest,
                                fingerprint=fingerprint, browser_digest=digest,
                                unclean=request.POST.get('unclean') == '1')
    except DeviceSessionDenied as error:
        # Only an authenticated, serialized check may prove this device owns no
        # active attempt. An unsigned HTTP error is never an unlock instruction.
        from django.utils import timezone
        from judge.models import Profile, FairPlayDeviceSlot
        from types import SimpleNamespace
        from uuid import uuid4
        Profile.objects.select_for_update().get(user_id=request.user.pk)
        if FairPlayDeviceSlot.objects.filter(user_id=request.user.pk, session__contest=contest,
                session__state__in=['active', 'finishing']).exists():
            if error.reason:
                return refusal_response(str(error), error.reason)
            return JsonResponse({'error': str(error)}, status=409)
        record = SimpleNamespace(pk=uuid4(), fingerprint=fingerprint, user_id=request.user.pk,
            contest=contest, participation_id=None, state='rejected', lease_until=timezone.now())
    else:
        if not bind_lock(record, request.POST.get('lock', ''), owner=owner):
            transaction.set_rollback(True)
            return JsonResponse({'error': 'This Mac code belongs to another exam or student.'}, status=409)
        record = close_if_due(record)
    return signed_state(request, record, extras=extras)


def finish_from_website(request, participation):
    """Complete an admitted native attempt through DKU's existing leave POST.

    Return None for unrelated contests. Failed harvesting retains the attempt and
    drafts; native polling never receives a terminal state until hand-in succeeds.
    """
    if not FairPlayDeviceSession.objects.filter(user_id=request.user.pk, participation=participation,
                                                state__in=('active', 'finishing')).exists():
        return None
    identity = request_identity(request)
    outcome = _finish_from_website_locked(request, participation, identity)
    if outcome is None:
        return None
    from judge.utils.views import generic_message
    if outcome == 'not_complete':
        return generic_message(request, 'Hand-in not complete',
            'Your answers are saved but the hand-in did not finish. Go back to the exam and press Leave again.', 409)
    response = generic_message(request, 'Exam submitted',
        'DKU OJ has received your exam. FairPlay is verifying completion and will unlock your computer automatically. You do not need to start the exam again.')
    response['Cache-Control'] = 'private, no-store'
    return response


@transaction.atomic
def _finish_from_website_locked(request, participation, identity):
    from judge.models import Profile
    Profile.objects.select_for_update().get(user_id=request.user.pk)
    record = FairPlayDeviceSession.objects.select_for_update().filter(
        user_id=request.user.pk, participation=participation,
        state__in=('active', 'finishing')).first()
    if record is None:
        return None
    fingerprint, digest = identity
    if record.fingerprint != fingerprint or record.browser_digest != digest:
        raise Http404()
    from judge.utils.assignment_harvest import assignment_harvest_enabled, harvest_participation
    from judge.utils.fairplay_device_session import begin_finishing, confirm_completed
    arguments = dict(session_id=record.pk, user_id=record.user_id,
                     fingerprint=fingerprint, browser_digest=digest)
    if not assignment_harvest_enabled():
        try:
            begin_finishing(**arguments)
            mark_hand_in(record)
            confirm_completed(**arguments, keep_drafts=True)
        except DeviceSessionDenied:
            transaction.set_rollback(True)
            return 'not_complete'
        from judge.utils.problems import participation_submit_progress
        try:
            participation_submit_progress(participation, log_step='leave_post')
        except TypeError:
            participation_submit_progress(participation)
        request.profile.remove_contest()
        return 'submitted'
    try:
        begin_finishing(**arguments)
        mark_hand_in(record)
        harvest_participation(participation, judge_on_commit=True)
        confirm_completed(**arguments)
    except DeviceSessionDenied:
        transaction.set_rollback(True)
        return 'not_complete'
    from judge.models import Profile
    Profile.objects.filter(user_id=record.user_id, current_contest_id=participation.pk).update(current_contest=None)
    return 'submitted'

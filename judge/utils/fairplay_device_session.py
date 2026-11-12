"""Transactional ownership. Call admission only after request-level device proof.

These functions do not infer native identity from cookies, user agents, or page messages.
Expired leases deny access but never silently transfer ownership to another device.
"""
from datetime import timedelta, timezone as dt_timezone
import re
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from judge.models import Contest, ContestParticipation, FairPlayDeviceSession, FairPlayDeviceSlot, FairPlayDeviceAudit, Profile


LEASE_SECONDS = 90
STALE_UNJOINED_GRACE = timedelta(minutes=10)
RELEASE_AFTER_DEADLINE_GRACE = timedelta(minutes=15)


class DeviceSessionDenied(ValueError):
    # reason is set only for refusals the native client may show to the student: other_device, handed_in, on_hold, not_open
    def __init__(self, message='', reason=None):
        super().__init__(message)
        self.reason = reason


class ExamNotOpen(DeviceSessionDenied):
    def __init__(self, message=''):
        super().__init__(message, 'not_open')


def session_on_hold(session):
    """A lease-gap row blocks the session until an instructor unlock or reopen is recorded after it."""
    cleared = FairPlayDeviceAudit.objects.filter(
        session=OuterRef('session'), action__in=['unlock-device', 'reopen'], pk__gt=OuterRef('pk'))
    latest = list(FairPlayDeviceAudit.objects.filter(session=session, action='lease-gap')
                  .annotate(cleared=Exists(cleared)).order_by('-pk').values_list('cleared', flat=True)[:1])
    return bool(latest) and not latest[0]


def flag_expired_lease(session, now=None):
    """Record once per expiry that a joined, active session went without a lease; it does not hold the session."""
    now = now or timezone.now()
    if (session.state != 'active' or session.participation_id is None or session.lease_until >= now
            or session.lease_until >= personal_deadline(session)):
        return False
    audit = FairPlayDeviceAudit.objects.filter(session=session, action='lease-expired')
    if audit.filter(created_at__gt=session.lease_until).exists():
        return False
    FairPlayDeviceAudit.objects.create(session=session, actor_id=session.user_id, action='lease-expired',
                                      reason='The FairPlay lease ran out while the exam was open')
    return True


def lease_expired_flagged(session, now=None, deadline=None, recorded=None):
    """Read-only: an expiry recorded earlier, or a lease that has run out right now. A bulk caller passes the
    session's deadline and whether an expiry row exists, so nothing is queried per session."""
    if session.state != 'active' or session.participation_id is None:
        return False
    now = now or timezone.now()
    ends = deadline if deadline is not None else personal_deadline(session)
    if session.lease_until < now and session.lease_until < ends:
        return True
    if recorded is not None:
        return recorded
    return FairPlayDeviceAudit.objects.filter(session=session, action='lease-expired').exists()


def _locked_profile(user_id):
    return Profile.objects.select_for_update().get(user_id=user_id)


def _identity(fingerprint, browser_digest):
    if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{64}', x) for x in (fingerprint, browser_digest)):
        raise DeviceSessionDenied('Verified device identity required.')


def normal_end(participation):
    """The participation's end as the core computes it from real_start and the contest length."""
    contest = participation.contest
    if participation.spectate:
        return contest.end_time
    if participation.virtual:
        return participation.real_start + (contest.time_limit or (contest.end_time - contest.start_time))
    if contest.time_limit is None:
        return contest.end_time
    return min(participation.real_start + contest.time_limit, contest.end_time)


WINDOW_ACTION = 'reopen-window'


def _utc(value):
    return value.astimezone(dt_timezone.utc).isoformat()


def _write_window(session, actor_id, until, used_seconds):
    FairPlayDeviceAudit.objects.create(session=session, actor_id=actor_id, action=WINDOW_ACTION,
                                      reason='until=%s;used=%d' % (_utc(until), used_seconds))


LAUNCH_MARK_SECONDS = 3600


def _launch_mark_key(user_id, contest_id):
    return 'fp-launch-mark:%s:%s' % (user_id, contest_id)


def note_app_launch(user_id, contest_id):
    """Remember, once the transaction commits, that this user's FairPlay app just claimed or recovered this exam. The
    launch page compares it with the click time. Heartbeat renews and state reads never call this."""
    import time
    from django.core.cache import cache
    now_ms = int(time.time() * 1000)
    transaction.on_commit(lambda: cache.set(_launch_mark_key(user_id, contest_id), now_ms, LAUNCH_MARK_SECONDS))


def app_launch_mark(user_id, contest_id):
    """Milliseconds since the epoch of the user's last claim or recover for this exam, or None."""
    from django.core.cache import cache
    return cache.get(_launch_mark_key(user_id, contest_id))


def audit_summary(session_ids):
    """What the audit rows say about many sessions at once, in one query: {'reopened': ids with a reopen row,
    'windows': id -> newest reopen-window reason (what reopen_until reads), 'unsubmitted': id -> newest draft warning,
    'held': ids still held by a lease gap (no unlock or reopen recorded after it, as session_on_hold reads it),
    'expired': ids with a lease-expired row}."""
    summary = {'reopened': set(), 'windows': {}, 'unsubmitted': {}, 'held': set(), 'expired': set()}
    rows = FairPlayDeviceAudit.objects.filter(
        session_id__in=list(session_ids),
        action__in=('reopen', WINDOW_ACTION, 'completed-with-unsubmitted-draft', 'lease-gap', 'unlock-device',
                    'lease-expired'),
    ).order_by('pk').values_list('session_id', 'action', 'reason')
    for session_id, action, reason in rows:
        if action == 'reopen':
            summary['reopened'].add(session_id)
        if action == 'lease-gap':
            summary['held'].add(session_id)
        elif action in ('unlock-device', 'reopen'):
            summary['held'].discard(session_id)
        elif action == WINDOW_ACTION:
            summary['windows'][session_id] = reason
        elif action == 'lease-expired':
            summary['expired'].add(session_id)
        elif action == 'completed-with-unsubmitted-draft':
            summary['unsubmitted'][session_id] = reason
    return summary


def parse_window(reason):
    """(until, used seconds) from a 'reopen-window' audit reason, or (None, 0)."""
    from datetime import datetime
    if not reason:
        return None, 0
    fields = dict(part.split('=', 1) for part in reason.split(';') if '=' in part)
    try:
        return datetime.fromisoformat(fields['until']), int(fields.get('used', 0))
    except (KeyError, ValueError):
        return None, 0


def _window(session):
    reason = FairPlayDeviceAudit.objects.filter(session=session, action=WINDOW_ACTION) \
        .order_by('-pk').values_list('reason', flat=True).first()
    return parse_window(reason)


def reopen_until(session):
    """The end of the window an instructor opened for this session, kept as an audit row because the exam
    clock itself lives in the participation's real_start."""
    return _window(session)[0]


def reopen_used_seconds(session):
    return _window(session)[1]


def extend_participation(participation, until):
    """Move real_start forward so the core end_time (real_start + contest length) lands on `until`, capped at the
    contest end. It never shortens an attempt and never moves the start past now, so earlier submissions keep a
    non-negative time since start: an extension larger than the time already used gives at most a fresh full length
    from now. Untimed exams already end with the contest."""
    contest = participation.contest
    if contest.time_limit is None or participation.spectate or participation.virtual:
        return False
    target = min(contest.end_time, max(until, normal_end(participation)))
    start = max(participation.real_start, min(target - contest.time_limit, timezone.now()))
    if start == participation.real_start:
        return False
    participation.real_start = start
    participation.save(update_fields=['real_start'])
    participation.__dict__.pop('end_time', None)
    participation.__dict__.pop('start', None)
    participation.recompute_results()
    from judge.utils.assignment_schedule import schedule_participation_harvest
    transaction.on_commit(lambda pid=participation.pk: schedule_participation_harvest(pid))
    return True


_QUERY = object()


def personal_deadline(session, reopen_at=_QUERY):
    """The one end time for this student, the same number the core enforces: a joined session ends with its
    participation (which already includes the instructor's override); an unjoined one ends with its reopen window
    when it has one, else with the contest. A disqualified student never gets more than the contest end.
    `reopen_at` lets a bulk caller pass the already-read window end (or None) instead of querying it per session."""
    contest = session.contest
    participation = session.participation if session.participation_id else None
    if participation is not None:
        deadline = participation.end_time
        return min(deadline, contest.end_time) if participation.is_disqualified else deadline
    until = reopen_until(session) if reopen_at is _QUERY else reopen_at
    return until if until is not None else contest.end_time


def release_attachment(session, now=None, force=False):
    """After FairPlay ended a session (auto-release, abort, deadline failsafe): drop the profile's attachment to THIS
    session's participation when that participation has ended or the session never joined. Another contest's
    attachment, and any live admitted session of the same student, are never touched."""
    now = now or timezone.now()
    attached = Profile.objects.filter(user_id=session.user_id, current_contest__isnull=False)
    if force:
        if session.participation_id:
            return attached.filter(current_contest_id=session.participation_id).update(current_contest=None)
        return attached.filter(current_contest__contest_id=session.contest_id).update(current_contest=None)
    if FairPlayDeviceSlot.objects.filter(user_id=session.user_id, session__state__in=('active', 'finishing')) \
            .exclude(session_id=session.pk).exists():
        return 0
    if session.participation_id:
        participation = session.participation
        if participation.end_time > now and not participation.is_disqualified:
            return 0
        return attached.filter(current_contest_id=session.participation_id).update(current_contest=None)
    return attached.filter(current_contest__contest_id=session.contest_id).update(current_contest=None)


def stale_reason(session, now, for_contest=None):
    """Why an active session protects no live exam any more, or None. A disqualified one goes stale only when
    its exam has ended, or for a claim of another exam; for its own exam it stays refused."""
    if session.state != 'active':
        return None
    participation = session.participation if session.participation_id else None
    if participation is not None and participation.is_disqualified:
        if session.contest.end_time <= now:
            return 'the exam has ended'
        if for_contest is not None and for_contest.pk != session.contest_id:
            return 'the participation was disqualified and the device is needed for another exam'
        return None
    if personal_deadline(session) <= now:
        return 'the exam has ended'
    if participation is None and session.lease_until < now - STALE_UNJOINED_GRACE:
        return 'the exam was never joined and the device has been silent'
    return None


def _free_slot_of(slot, current, user_id, now, for_contest=None):
    """Drop a slot that holds no live exam: a finished session is just detached, a stale active one is released."""
    if current.state not in ('active', 'completed', 'released'):
        return False
    expired = False
    if current.state == 'active':
        why = stale_reason(current, now, for_contest)
        if why is None:
            return False
        if current.participation_id and personal_deadline(current) <= now:
            from judge.views.fairplay_native_session import close_after_deadline
            closed = close_after_deadline(current)
            if closed.state not in ('completed', 'released'):
                return False
            slot.refresh_from_db()
            if slot.session_id == current.pk:
                slot.session = None
                slot.save(update_fields=['session'])
            return True
        expired = True
        current.state = 'released'
        current.ended_at = now
        current.lease_until = now
        current.save(update_fields=['state', 'ended_at', 'lease_until'])
        FairPlayDeviceAudit.objects.create(session=current, actor_id=user_id, action='auto-expire',
                                          reason=('Released automatically: %s.' % why)[:300])
    slot.session = None
    slot.save(update_fields=['session'])
    if expired:
        release_attachment(current, now)
    return True


def undo_deadline_close(session, now):
    from judge.views.fairplay_native_session import deadline_close_may_be_undone
    participation = session.participation if session.participation_id else None
    if (session.state != 'finishing' or participation is None or participation.is_disqualified
            or participation.spectate or personal_deadline(session) <= now
            or not deadline_close_may_be_undone(session)):
        return False
    session.state = 'active'
    session.lease_until = min(personal_deadline(session), now + timedelta(seconds=LEASE_SECONDS))
    session.save(update_fields=['state', 'lease_until'])
    FairPlayDeviceAudit.objects.create(session=session, actor_id=session.user_id, action='deadline-extended',
                                      reason='The deadline moved; the attempt is open again.')
    return True


def deadline_closed_may_reopen(previous, now):
    """A completed or released session that only the deadline closed, while the live deadline is in the future
    again, the student is not disqualified and never handed in by hand."""
    from judge.views.fairplay_native_session import deadline_close_may_be_undone
    if previous.participation_id is None:
        return False
    participation = previous.participation
    if participation.spectate or participation.is_disqualified or participation.end_time <= now:
        return False
    if FairPlayDeviceAudit.objects.filter(session=previous, action='unlock-device').exists():
        return False
    return deadline_close_may_be_undone(previous)


@transaction.atomic
def claim_device(*, user_id, contest, fingerprint, browser_digest):
    """Internal entry primitive, not an HTTP authentication endpoint."""
    _identity(fingerprint, browser_digest)
    _locked_profile(user_id)
    slot, _ = FairPlayDeviceSlot.objects.get_or_create(user_id=user_id)
    current = None
    if slot.session_id:
        current = FairPlayDeviceSession.objects.select_for_update().select_related('contest', 'participation').get(pk=slot.session_id)
        if _free_slot_of(slot, current, user_id, timezone.now(), contest):
            current = None
    if current is not None and slot.session_id and current.contest_id == contest.pk:
        undo_deadline_close(current, timezone.now())
    if current is not None and slot.session_id:
        if (current.state == 'active' and current.contest_id == contest.pk and current.fingerprint == fingerprint
                and current.browser_digest != browser_digest and current.participation_id is None
                and not session_on_hold(current) and current.contest.end_time > timezone.now()):
            current.browser_digest = browser_digest
            current.save(update_fields=['browser_digest'])
            FairPlayDeviceAudit.objects.create(session=current, actor_id=user_id, action='recover',
                                              reason='Same device started again from a new sign-in before the exam was joined')
        if current.state == 'active' and session_on_hold(current):
            raise DeviceSessionDenied('FairPlay restarted on this device. Ask the instructor to unlock and reopen the exam.', 'on_hold')
        if (current.state == 'active' and current.contest_id == contest.pk
                and current.fingerprint == fingerprint and current.browser_digest == browser_digest):
            # A repeated start must renew admission before the client posts join.
            # Returning an expired lease redirects that join back to the launch page.
            return renew_device(session_id=current.pk, user_id=user_id,
                                fingerprint=fingerprint, browser_digest=browser_digest)
        if (current.state == 'active' and current.participation_id is not None and current.contest_id == contest.pk
                and current.fingerprint == fingerprint):
            raise DeviceSessionDenied('FairPlay restarted on this device. Ask the instructor to unlock and reopen the exam.', 'on_hold')
        raise DeviceSessionDenied('This account already has an exam session on another computer or browser. '
                                  'Ask your instructor to choose Unlock device for you, then try again.', 'other_device')
    previous = FairPlayDeviceSession.objects.filter(user_id=user_id, contest=contest,
        state__in=['completed', 'released']).exclude(
        participation__isnull=True, fairplaydeviceaudit__action__in=['auto-expire', 'abort']).order_by('-created_at', '-pk').first()
    now = timezone.now()
    deadline_reopen = False
    if previous and not FairPlayDeviceAudit.objects.filter(session=previous, action='reopen').exists():
        deadline_reopen = deadline_closed_may_reopen(previous, now)
        if not deadline_reopen:
            raise DeviceSessionDenied('This exam was already handed in. Ask your instructor to reopen it if you need to continue.', 'handed_in')
    if now < contest.start_time:
        raise ExamNotOpen('This exam has not started yet. Start it again when it opens (%s UTC).'
                          % contest.start_time.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M'))
    if now >= contest.end_time:
        raise ExamNotOpen('This exam has ended.')
    window = reopen_until(previous) if previous and not deadline_reopen else None
    if previous and not deadline_reopen and (window is None or window <= now):
        raise DeviceSessionDenied('The reopened exam window has ended. Ask your instructor to reopen it again.', 'handed_in')
    session = FairPlayDeviceSession.objects.create(user_id=user_id, contest=contest,
        fingerprint=fingerprint, browser_digest=browser_digest, lease_until=min(contest.end_time, now + timedelta(seconds=LEASE_SECONDS)))
    if previous and not deadline_reopen:
        _write_window(session, user_id, window, reopen_used_seconds(previous))
    if deadline_reopen:
        FairPlayDeviceAudit.objects.create(session=session, actor_id=user_id, action='deadline-extended',
                                          reason='The exam end moved; the attempt is open again.')
    if previous and previous.participation_id:
        if previous.participation.end_time <= now or previous.participation.is_disqualified:
            raise DeviceSessionDenied('This reopened exam is no longer available.', 'handed_in')
        session.participation_id = previous.participation_id
        session.save(update_fields=['participation'])
    slot.session = session
    slot.save(update_fields=['session'])
    return session


def admitted_session(*, user_id, fingerprint, browser_digest, now=None):
    _identity(fingerprint, browser_digest)
    current_time = now or timezone.now()
    session = FairPlayDeviceSession.objects.select_related(
        "contest", "participation__contest").filter(
        fairplaydeviceslot__user_id=user_id, user_id=user_id, fingerprint=fingerprint,
        browser_digest=browser_digest, state='active', lease_until__gt=current_time,
        ).filter(Q(contest__start_time__lte=current_time) | Q(participation__isnull=False)).first()
    if session and personal_deadline(session) <= current_time:
        return None
    if session and session.participation_id:
        participation = session.participation
        if participation.spectate or participation.is_disqualified:
            return None
    return session


def served_session(*, user_id, fingerprint, browser_digest, now=None, states=('active', 'finishing')):
    """The device's own live session, for serving OJ content. The lease only matters for claims."""
    _identity(fingerprint, browser_digest)
    current_time = now or timezone.now()
    session = FairPlayDeviceSession.objects.select_related(
        "contest", "participation__contest").filter(
        user_id=user_id, fingerprint=fingerprint, browser_digest=browser_digest,
        ).filter(Q(contest__start_time__lte=current_time) | Q(participation__isnull=False)
        )
    if states is not None:
        session = session.filter(state__in=states)
    session = session.order_by('-created_at', '-pk').first()
    if session and session.participation_id:
        participation = session.participation
        if participation.spectate or participation.is_disqualified:
            return None
    return session


def _owned(session_id, user_id, fingerprint, browser_digest, states=('active',)):
    _identity(fingerprint, browser_digest)
    _locked_profile(user_id)
    session = FairPlayDeviceSession.objects.select_for_update().filter(
        pk=session_id, user_id=user_id, fingerprint=fingerprint, browser_digest=browser_digest,
        state__in=states, fairplaydeviceslot__user_id=user_id).first()
    if session is None:
        raise DeviceSessionDenied('This device no longer owns the exam session.')
    return session


def _renew_locked(session, profile, hold):
    if hold:
        raise DeviceSessionDenied('FairPlay restarted on this device. Ask the instructor to unlock and reopen the exam.')
    deadline = personal_deadline(session)
    now = timezone.now()
    if deadline <= now:
        raise DeviceSessionDenied('The exam deadline has passed.')
    flag_expired_lease(session, now)
    if session.participation_id and profile.current_contest_id is None:
        Profile.objects.filter(user_id=session.user_id, current_contest__isnull=True).update(current_contest_id=session.participation_id)
    session.lease_until = min(deadline, now + timedelta(seconds=LEASE_SECONDS))
    session.save(update_fields=['lease_until'])
    return session


@transaction.atomic
def renew_device(*, session_id, user_id, fingerprint, browser_digest):
    session = _owned(session_id, user_id, fingerprint, browser_digest)
    profile = Profile.objects.only('current_contest_id').get(user_id=user_id)
    return _renew_locked(session, profile, session_on_hold(session))


def renew_for_request(*, session_id, user_id, fingerprint, browser_digest):
    """Renew inside the caller's transaction with one session read.

    Returns (session, hold), or None when no row matches the device. Raises
    DeviceSessionDenied when the row exists but may not be renewed. Locks the
    profile first and the session second, like every other writer.
    """
    _identity(fingerprint, browser_digest)
    profile = _locked_profile(user_id)
    session = FairPlayDeviceSession.objects.select_for_update().filter(
        pk=session_id, user_id=user_id, fingerprint=fingerprint, browser_digest=browser_digest).first()
    if session is None:
        return None
    if session.state != 'active' or not FairPlayDeviceSlot.objects.filter(session_id=session.pk).exists():
        raise DeviceSessionDenied('This device no longer owns the exam session.')
    if session.participation_id:
        session.participation = ContestParticipation.objects.select_related('contest').get(pk=session.participation_id)
        session.contest = session.participation.contest
    else:
        session.contest = Contest.objects.get(pk=session.contest_id)
    hold = session_on_hold(session)
    return _renew_locked(session, profile, hold), hold


@transaction.atomic
def attach_participation(*, session_id, user_id, fingerprint, browser_digest, participation):
    session = _owned(session_id, user_id, fingerprint, browser_digest)
    if (participation.user.user_id != user_id or participation.contest_id != session.contest_id
            or participation.spectate or participation.is_disqualified
            or session.participation_id not in (None, participation.pk)):
        raise DeviceSessionDenied('Participation does not match this device session.')
    window = reopen_until(session)
    if window is not None:
        extend_participation(participation, window)
    session.participation = participation
    session.save(update_fields=['participation'])
    return session


@transaction.atomic
def begin_finishing(*, session_id, user_id, fingerprint, browser_digest):
    session = _owned(session_id, user_id, fingerprint, browser_digest, states=('active', 'finishing'))
    if session.participation_id is None:
        raise DeviceSessionDenied('The exam has not been joined.')
    session.state = 'finishing'
    session.save(update_fields=['state'])
    return session


def drafts_the_harvest_would_still_submit(participation):
    """Drafts the hand-in harvest will still turn into submissions, by the harvest's own rules: not taken yet,
    the problem still in the exam, the submission limit not used up, and a participation that counts for a grade."""
    from django.db.models import Count
    from judge.models import ContestProblem, ContestSubmission
    from judge.utils.assignment_harvest import participation_is_harvestable
    if not participation_is_harvestable(participation):
        return []
    contest_problems = {cp.problem_id: cp for cp in ContestProblem.objects.filter(contest_id=participation.contest_id)}
    used = dict(ContestSubmission.objects.filter(participation=participation).exclude(submission__status='IE')
                .values_list('problem_id').annotate(Count('id')))
    pending = []
    for draft in participation.drafts.filter(harvested_at__isnull=True).select_related('problem', 'language'):
        contest_problem = contest_problems.get(draft.problem_id)
        if contest_problem is None or not draft.has_content():
            continue
        if contest_problem.max_submissions and used.get(contest_problem.id, 0) >= contest_problem.max_submissions:
            continue
        pending.append(draft)
    return pending


def claimed_but_unsubmitted_problem_codes(participation):
    """Codes of problems whose draft was marked taken by a harvest that never produced a submission (the quiet
    failure that `manage.py check_assignment_harvest_consistency` reports: a read-only audit of draft claims that
    have no submission behind them; run it, or reopen the student, to recover such an answer)."""
    from judge.models import ContestSubmission
    codes = []
    for draft in participation.drafts.filter(harvested_at__isnull=False).select_related('problem'):
        if not draft.has_content():
            continue
        if ContestSubmission.objects.filter(participation=participation, problem__problem_id=draft.problem_id,
                                            submission__date__gte=draft.harvested_at).exists():
            continue
        codes.append(draft.problem.code)
    return codes


@transaction.atomic
def confirm_completed(*, session_id, user_id, fingerprint, browser_digest, keep_drafts=False):
    session = _owned(session_id, user_id, fingerprint, browser_digest, states=('finishing',))
    if session.participation_id is None or (not keep_drafts and drafts_the_harvest_would_still_submit(session.participation)):
        raise DeviceSessionDenied('Answers are still waiting to be submitted.')
    unsubmitted = claimed_but_unsubmitted_problem_codes(session.participation)
    session.state = 'completed'
    session.ended_at = timezone.now()
    session.lease_until = session.ended_at
    session.save(update_fields=['state', 'ended_at', 'lease_until'])
    FairPlayDeviceSlot.objects.filter(user_id=user_id, session=session).update(session=None)
    FairPlayDeviceAudit.objects.create(session=session, actor_id=user_id, action='completed', reason='DKU confirmed final hand-in')
    if unsubmitted:
        FairPlayDeviceAudit.objects.create(session=session, actor_id=user_id, action='completed-with-unsubmitted-draft',
                                          reason=', '.join(unsubmitted)[:200])
    return session


@transaction.atomic
def instructor_unlock(*, contest, username, actor, reason):
    if not contest.is_editable_by(actor):
        raise DeviceSessionDenied('Only this exam’s instructor may unlock a device.')
    reason = reason.strip()
    if not 1 <= len(reason) <= 300:
        raise DeviceSessionDenied('A reason of at most 300 characters is required.')
    profile = Profile.objects.select_for_update().filter(user__username=username).first()
    if profile is None:
        raise DeviceSessionDenied('No active device for this student.')
    slot = FairPlayDeviceSlot.objects.filter(user_id=profile.user_id).first()
    if slot is None or slot.session_id is None:
        raise DeviceSessionDenied('No active device for this student.')
    session = FairPlayDeviceSession.objects.select_for_update().get(pk=slot.session_id)
    if session.contest_id != contest.pk or session.state not in ('active', 'finishing'):
        raise DeviceSessionDenied('No active device for this exam.')
    session.state = 'released'
    session.ended_at = timezone.now()
    session.lease_until = session.ended_at
    session.save(update_fields=['state', 'ended_at', 'lease_until'])
    slot.session = None
    slot.save(update_fields=['session'])
    FairPlayDeviceAudit.objects.create(session=session, actor=actor, action='unlock-device', reason=reason)
    return session


@transaction.atomic
def instructor_unlock_end(*, contest, username, actor, reason):
    """Unlock the device and end the attempt: the student is also detached from the exam's participation."""
    session = instructor_unlock(contest=contest, username=username, actor=actor, reason=reason)
    release_attachment(session, force=True)
    return session


def reopen_minutes_from(left_seconds):
    """The default reopen length for a student with this many seconds left: that or 30 minutes, whichever is longer."""
    minutes = 30
    if left_seconds is not None and left_seconds > 0:
        minutes = max(minutes, int(-(-left_seconds // 60)))
    return min(minutes, 1440)


def default_reopen_minutes(contest, username):
    """The student's remaining time or 30 minutes, whichever is longer (the reopen itself never shortens)."""
    session = FairPlayDeviceSession.objects.select_related('contest', 'participation').filter(
        user__username=username, contest=contest).order_by('-created_at', '-pk').first()
    left = None if session is None else (personal_deadline(session) - timezone.now()).total_seconds()
    return reopen_minutes_from(left)


@transaction.atomic
def instructor_unlock_and_reopen(*, contest, username, actor, minutes, reason):
    """Unlock the student's device for this exam, if one is active, and grant the new session in one step."""
    if not contest.is_editable_by(actor):
        raise DeviceSessionDenied('Only this exam’s instructor may unlock a device.')
    slot = FairPlayDeviceSlot.objects.filter(user__username=username, session__contest=contest,
                                             session__state__in=('active', 'finishing')).exists()
    if slot:
        instructor_unlock(contest=contest, username=username, actor=actor, reason=reason)
    return instructor_reopen(contest=contest, username=username, actor=actor, minutes=minutes, reason=reason)


@transaction.atomic
def abort_device(*, session_id, user_id, fingerprint, browser_digest):
    """Revoke before a native client exits; this is never a submission receipt."""
    session = _owned(session_id, user_id, fingerprint, browser_digest, states=('active', 'finishing'))
    if session.participation_id is not None:
        raise DeviceSessionDenied('A joined exam can only be ended by hand-in or by the instructor.')
    session.state = 'released'
    session.ended_at = timezone.now()
    session.lease_until = session.ended_at
    session.save(update_fields=['state', 'ended_at', 'lease_until'])
    FairPlayDeviceSlot.objects.filter(user_id=user_id, session=session).update(session=None)
    FairPlayDeviceAudit.objects.create(session=session, actor_id=user_id, action='abort',
                                      reason='Student ended the native session without final hand-in')
    release_attachment(session)
    return session


@transaction.atomic
def instructor_reopen(*, contest, username, actor, minutes, reason):
    """Grant one new native session without erasing the previous terminal receipt."""
    if not contest.is_editable_by(actor):
        raise DeviceSessionDenied('Only this exam’s instructor may reopen it.')
    if type(minutes) is not int or not 1 <= minutes <= 1440 or not 1 <= len(reason.strip()) <= 300:
        raise DeviceSessionDenied('Provide 1–1440 minutes and a reason of at most 300 characters.')
    profile = Profile.objects.select_for_update().filter(user__username=username).first()
    if profile is None:
        raise DeviceSessionDenied('No native attempt exists for this student.')
    if FairPlayDeviceSlot.objects.filter(user_id=profile.user_id, session__isnull=False).exists():
        raise DeviceSessionDenied('Unlock the active device before reopening this exam.')
    previous = FairPlayDeviceSession.objects.select_for_update().filter(user_id=profile.user_id,
        contest=contest).order_by('-created_at', '-pk').first()
    if previous is None or previous.state not in ('completed', 'released'):
        raise DeviceSessionDenied('No finished or released native attempt exists.')
    now = timezone.now()
    if not contest.start_time <= now < contest.end_time:
        raise DeviceSessionDenied('Extend the assignment availability before reopening it.')
    until = now + timedelta(minutes=minutes)
    if previous.participation_id:
        participation = previous.participation
        if participation.spectate or participation.is_disqualified:
            raise DeviceSessionDenied('This participation cannot be reopened.')
        until = max(until, normal_end(participation))
    until = min(contest.end_time, until)
    used = 0
    if previous.participation_id:
        used = max(0, int((min(previous.ended_at or now, now) - participation.real_start).total_seconds()))
        extend_participation(participation, until)
    _write_window(previous, actor.pk, until, used)
    FairPlayDeviceAudit.objects.create(session=previous, actor=actor, action='reopen', reason=reason.strip())
    return previous


@transaction.atomic
def instructor_extra_time(*, contest, username, actor, minutes, reason):
    """Add minutes to the end of a student who is writing now, by moving real_start (timed exams only)."""
    if not contest.is_editable_by(actor):
        raise DeviceSessionDenied('Only this exam’s instructor may give extra time.')
    if type(minutes) is not int or not 1 <= minutes <= 1440 or not 1 <= len(reason.strip()) <= 300:
        raise DeviceSessionDenied('Provide 1–1440 minutes and a reason of at most 300 characters.')
    if contest.time_limit is None:
        raise DeviceSessionDenied('This exam has no per-student time limit. Extend the exam end instead.')
    profile = Profile.objects.select_for_update().filter(user__username=username).first()
    session = FairPlayDeviceSession.objects.select_for_update().filter(
        user__username=username, contest=contest, state__in=('active', 'finishing'),
        participation__isnull=False).select_related('participation__contest').first() if profile else None
    if session is None:
        raise DeviceSessionDenied('This student is not writing the exam on a native device.')
    participation = session.participation
    now = timezone.now()
    if participation.is_disqualified or participation.end_time <= now:
        raise DeviceSessionDenied('This participation cannot get extra time.')
    if participation.end_time >= contest.end_time:
        raise DeviceSessionDenied('This student already has time until the exam end.')
    extend_participation(participation, participation.end_time + timedelta(minutes=minutes))
    FairPlayDeviceAudit.objects.create(session=session, actor=actor, action='extra-time', reason=reason.strip())
    return session


@transaction.atomic
def recover_device(*, user_id, contest, fingerprint, browser_digest, unclean=False):
    """Reauthenticated account + the same TLS private key may replace a lost cookie.

    This never moves ownership to another certificate and never creates a session.
    The previous browser digest is invalidated atomically before returning.
    """
    _identity(fingerprint, browser_digest)
    profile = _locked_profile(user_id)
    slot = FairPlayDeviceSlot.objects.filter(user_id=user_id).first()
    session = None
    if slot and slot.session_id:
        session = FairPlayDeviceSession.objects.select_for_update().select_related('contest', 'participation').get(pk=slot.session_id)
        if _free_slot_of(slot, session, user_id, timezone.now(), contest):
            slot = session = None
        elif session.contest_id != contest.pk or session.fingerprint != fingerprint:
            raise DeviceSessionDenied('This account has an exam session on another computer, browser or exam. '
                                      'Ask your instructor to choose Unlock device for you, then try again.', 'other_device')
    if session is None:
        session = FairPlayDeviceSession.objects.select_for_update().filter(user_id=user_id,
            contest=contest, fingerprint=fingerprint, state__in=['completed', 'released']).order_by('-created_at', '-pk').first()
        if session is None:
            raise DeviceSessionDenied('No matching native session can be recovered.')
    digest_changed = session.browser_digest != browser_digest
    flag_expired_lease(session)
    joined = session.participation_id is not None
    expired = session.lease_until < timezone.now()
    if session.state == 'active' and (unclean or (digest_changed and (joined or expired))):
        if not session_on_hold(session):
            FairPlayDeviceAudit.objects.create(session=session, actor_id=user_id, action='lease-gap',
                                              reason='FairPlay restarted on this device during the exam')
            session.lease_until = timezone.now()
            session.save(update_fields=['lease_until'])
    if digest_changed:
        session.browser_digest = browser_digest
        session.save(update_fields=['browser_digest'])
        FairPlayDeviceAudit.objects.create(session=session, actor_id=user_id, action='recover',
                                          reason='Same device reauthenticated after browser interruption')
    if session.state == 'active' and not session_on_hold(session):
        deadline = personal_deadline(session)
        session.lease_until = min(deadline, timezone.now() + timedelta(seconds=LEASE_SECONDS))
        session.save(update_fields=['lease_until'])
    if session.state == 'active' and session.participation_id:
        if profile.current_contest_id not in (None, session.participation_id):
            raise DeviceSessionDenied('Another participation is already open.')
        Profile.objects.filter(pk=profile.pk).update(current_contest_id=session.participation_id)
    return session


MAC_CODE_RE = re.compile(r'[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}')


def normalize_mac_code(raw):
    code = ''.join(ch for ch in (raw or '').upper() if ch.isalnum())
    return code if MAC_CODE_RE.fullmatch(code) else None


LOCK_OWNER_CACHE = 600
LOCK_OWNER_NONE_CACHE = 60


def lock_owner_key(code):
    return 'fp-lock-owner:%s' % code


def lock_owner(code, use_cache=True):
    """(contest id, username) that owns a Mac lock, or None. An administrator override wins, else the first binding.
    Cached per code (an unbound code only briefly); a bind or override drops the entry."""
    from django.core.cache import cache
    key = lock_owner_key(code)
    if use_cache:
        cached = cache.get(key)
        if cached == 'none':
            return None
        if isinstance(cached, (list, tuple)) and len(cached) == 2:
            return int(cached[0]), str(cached[1])
    owner = _lock_owner_from_db(code)
    if use_cache:
        cache.set(key, 'none' if owner is None else [owner[0], owner[1]],
                  LOCK_OWNER_NONE_CACHE if owner is None else LOCK_OWNER_CACHE)
    return owner


def _lock_owner_from_db(code):
    override = FairPlayDeviceAudit.objects.filter(action='lock-override', reason__startswith=code + '|').order_by('-pk').first()
    if override is not None:
        _, contest_id, username = override.reason.split('|', 2)
        return int(contest_id), username
    first = FairPlayDeviceAudit.objects.filter(action='lock', reason=code).select_related('session__user').order_by('pk').first()
    return None if first is None else (first.session.contest_id, first.session.user.username)


def _forget_lock_owner(code):
    from django.core.cache import cache
    transaction.on_commit(lambda: cache.delete(lock_owner_key(code)))


@transaction.atomic
def bind_lock(session, lock, owner=None):
    """Remember which Mac lock belongs to this session (audit row, no schema change).

    Returns False when another student or exam owns the lock; the caller must then roll back.
    `owner` is the owner the caller read from the cache before taking any lock; the final check always reads the
    database. The row is written first and ownership read again, so of two racing binds the earlier row wins.
    """
    code = normalize_mac_code(lock)
    if code is None:
        return True
    mine = (session.contest_id, session.user.username)
    if owner is None:
        owner = _lock_owner_from_db(code)
    if owner is not None and owner != mine:
        return False
    # A row is written whenever this session's code is not the student's newest bind in the exam, so the newest row
    # is the computer that last registered (X, Y, X again ends on X, and a recover rebinding an older session
    # counts). Extra rows never change ownership: the first row for a code decides.
    last = FairPlayDeviceAudit.objects.filter(action='lock', session__user_id=session.user_id,
                                              session__contest_id=session.contest_id) \
        .order_by('-pk').values_list('session_id', 'reason').first()
    if last != (session.pk, code):
        FairPlayDeviceAudit.objects.create(session=session, actor_id=session.user_id, action='lock', reason=code)
        _forget_lock_owner(code)
    return _lock_owner_from_db(code) == mine


def lock_bound_to(contest, username, code, use_cache=True):
    """True/False when the lock is owned by this student in this exam / by someone else, None when unbound.
    Release and permission decisions pass use_cache=False."""
    owner = lock_owner(code, use_cache=use_cache)
    return None if owner is None else owner == (contest.pk, username)


def latest_bound_lock(contest, username):
    """The Mac/PC lock code most recently registered by this student's computer in this exam, or None. Every bind or
    rebind of a different code writes an audit row, so the newest row across all of the student's sessions is the
    computer that last said it was locked (a recover can rebind a lock on an older session), provided the student's
    newest session has registered a lock at all. A code another student
    or exam owns (for example after an administrator override) is never returned: ownership is read uncached."""
    newest = FairPlayDeviceSession.objects.filter(contest=contest, user__username=username) \
        .order_by('-created_at', '-pk').values_list('pk', flat=True).first()
    if newest is None or not FairPlayDeviceAudit.objects.filter(action='lock', session_id=newest).exists():
        return None  # the newest computer has not registered a lock: the older one's code is not offered
    code = FairPlayDeviceAudit.objects.filter(
        action='lock', session__contest=contest, session__user__username=username
    ).order_by('-pk').values_list('reason', flat=True).first()
    if code and normalize_mac_code(code) == code and lock_bound_to(contest, username, code, use_cache=False):
        return code
    return None


def override_lock_owner(contest, username, code, actor):
    """Administrator only: make this student in this exam the owner of a Mac lock. Written as an audit row."""
    from django.core.cache import cache
    current = FairPlayDeviceAudit.objects.filter(action='lock', reason=code).order_by('pk').first()
    if current is None:
        return
    FairPlayDeviceAudit.objects.create(session_id=current.session_id, actor=actor, action='lock-override',
                                      reason='%s|%d|%s' % (code, contest.pk, username))
    cache.delete(lock_owner_key(code))
    _forget_lock_owner(code)

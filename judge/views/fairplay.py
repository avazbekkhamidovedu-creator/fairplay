"""The instructor actions of a FairPlay exam's Participation tab."""
import logging
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext
from django.views import View

from judge.models import Contest
from judge.utils.fairplay import fairplay_enabled, is_fairplay_exam, native_browser_enabled
from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT
from judge.utils.fairplay_device_session import format_lock

__all__ = ['ContestStudentHelp']

logger = logging.getLogger('judge.fairplay')


def _client(request):
    return FairPlayClient(acting_user=request.user.username)


def _fairplay_contest(request, key):
    contest = get_object_or_404(Contest, key=key)
    if not fairplay_enabled() or not is_fairplay_exam(contest) or not contest.is_editable_by(request.user):
        raise Http404()
    return contest


ON_RECORD_ACTION = 'offline-exit-on-record'
MAX_REASON = 200
MAX_MINUTES = 8 * 60
MAX_REOPEN_MINUTES = 1440  # a reopen may be as long as instructor_reopen allows, so it never shortens the time left
NETID_MAX = 64


# action -> (button, question asked on the confirm step, needs minutes)
ACTIONS = {
    'unlock-device': (_l('Unlock device'),
                      _l('Revoke exam access and unlock %(name)s’s device? This does not submit the exam.'), False),
    'quick-unlock': (_l('Unlock student device'),
                     _l('Unlock %(name)s (%(netid)s)? Their computer is released within a few seconds and they return to the start page. Their saved answers are kept.'), False),
    'unlock-reopen': (_l('Unlock and reopen'),
                      _l('Unlock %(name)s’s device and let them start the exam again right away?'), True),
    'approve': (_l('Approve resume'), _l('Approve %(name)s to resume the exam?'), False),
    'release-device': (_l('Move to another computer'),
                       _l('Let %(name)s sign in again from another computer?'), False),
    'reopen': (_l('Reopen exam'), _l('Reopen the exam for %(name)s, who has handed in?'), True),
    'extra-time': (_l('Give extra time'), _l('Give %(name)s extra time?'), True),
    'offline-exit': (_l('Offline exit code'), _l('Get the offline exit code for %(name)s?'), False),
}


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


EXIT_ERRORS = {
    409: _l('FairPlay refused that code because of a conflict. Check the code and the student.'),
    422: _l('FairPlay did not accept that code. Ask the student to read it again from the Mac.'),
    429: _l('Too many exit codes were requested. Wait a few minutes and try again.'),
    503: _l('FairPlay has no exit key set yet. Ask the FairPlay administrator.'),
}


def _exit_error(error):
    detail = (error.detail or '').strip()
    if error.status == 409 and detail and not detail.startswith('FairPlay refused the request'):
        return detail
    return EXIT_ERRORS[error.status]


def _exit_target_problem(request, contest, netid):
    from django.contrib.auth.models import User
    from judge.models import Profile
    from judge.views.fairplay_launch import student_can_launch
    user = User.objects.filter(username=netid, is_active=True).first()
    profile = Profile.objects.filter(user=user).first() if user else None
    if profile is None:
        return _('%(netid)s is not a student of this exam.') % {'netid': netid}
    if contest.is_editable_by(user):
        return _('%(netid)s is an instructor of this exam, not a student. No exit code was issued.') % {'netid': netid}
    target = SimpleNamespace(user=user, profile=profile)
    if not student_can_launch(target, contest):
        return _('%(netid)s cannot start this exam, so no exit code was issued.') % {'netid': netid}
    return None


def _lock_problem(request, contest, netid, mac_code):
    """A refusal text, or None when this lock may be released for this student. The caller is an editor of this exam
    (the view guarantees it). Permission rests on one uncached ownership lookup of the posted code and on nothing the
    page carried:

    - owned by this student in this exam (the lock on record), or owned by nobody: allowed. An unowned code is one the
      OJ never bound (a refused claim, a student not yet admitted, a stale hint); it needs the 8 characters only the
      locked computer shows, and it is audited. Known and accepted: an editor of this exam may issue a code for an
      unowned lock typed from a screen for a student on this exam's list even if that computer locked for another
      exam; FairPlay still decides whether the lock exists.
    - owned by another student or exam: refused; a site administrator may take it over (an override, labelled so).
    The audit label is `request.fairplay_source`, set from what the page carried, and never affects permission.
    """
    from judge.utils.fairplay_device_session import lock_bound_to, normalize_mac_code
    code = normalize_mac_code(mac_code)
    if code is None:
        return _('That is not a Mac code.')
    bound = lock_bound_to(contest, netid, code, use_cache=False)
    if bound is False:
        if request.user.is_superuser:
            request.fairplay_override = (contest, netid, code)
            request.fairplay_source = 'administrator override'
            return None
        return _('This Mac code belongs to another exam or student. No exit code was issued.')
    if request.user.is_superuser and request.profile.id not in contest.editor_ids:
        request.fairplay_source = 'administrator, ' + getattr(request, 'fairplay_source', 'typed')
    return None


def _audit_issued(request, contest, netid, code):
    """Record every issued offline exit code: an audit row on the student's newest session in this exam (the table
    needs a session), else only a warning line. Known and accepted: a student with no session at all has nothing to
    hang a row on, so only the log line records that release. Never raises: the code is already issued and must reach
    the page."""
    from judge.utils.fairplay_device_session import EXIT_ISSUED_ACTION
    source = getattr(request, 'fairplay_source', 'unknown')
    anchor = None
    try:
        from django.db import transaction
        from judge.models import FairPlayDeviceAudit, FairPlayDeviceSession
        anchor = FairPlayDeviceSession.objects.filter(contest=contest, user__username=netid) \
            .order_by('-created_at', '-pk').first()
        if anchor is not None:
            with transaction.atomic():
                FairPlayDeviceAudit.objects.create(session=anchor, actor=request.user, action=EXIT_ISSUED_ACTION,
                                                   reason=('%s|%s' % (code, source))[:300])
    except Exception:
        logger.exception('FairPlay could not write the audit row for an exit code')
        anchor = None
    logger.warning('FairPlay offline exit code issued by %s for %s in %s, lock %s (%s)%s', request.user.username,
                   netid, contest.key, code, source, '' if anchor is not None else ' [no session to audit on]')


def _reopen_note(contest, session, minutes):
    from datetime import timedelta, timezone as dt_timezone
    from judge.utils.fairplay_device_session import reopen_until
    until = reopen_until(session)
    text = _('Reopened until %(time)s UTC.') % {'time': until.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M')}
    if until == contest.end_time and timezone.now() + timedelta(minutes=minutes) > contest.end_time:
        text += ' ' + _('The request was capped at the exam end.')
    return text


def _fail(request, error):
    if error.status in (404, 409, 422) and error.detail:
        messages.error(request, error.detail)
    elif error.status is None:
        messages.error(request, _('FairPlay cannot be reached. Try again in a minute.'))
    else:
        messages.error(request, _('FairPlay could not do that.'))


def _minutes_limit():
    """The longest extra time or classic reopen: the native paths take up to 1440, the FairPlay service 480."""
    return MAX_REOPEN_MINUTES if native_browser_enabled() else MAX_MINUTES


def _parse_minutes(request, maximum):
    """(minutes, problem): the posted whole number of minutes from 1 to `maximum`."""
    try:
        minutes = int(request.POST.get('minutes', ''))
    except ValueError:
        minutes = 0
    if not 1 <= minutes <= maximum:
        return None, _('Minutes must be a whole number from 1 to %d.') % maximum
    return minutes, None


LOCK_SOURCES = (('bound:', 'on record'), ('hint:', 'chosen from list'))


def _chosen_lock(request):
    """(lock the instructor chose or typed, how it was chosen). A typed code wins over a ticked choice. The page marks a
    listed choice with a prefix ('bound:' or 'hint:'); anything else counts as typed. The label is only for the audit
    row and never decides permission."""
    from judge.utils.fairplay_device_session import normalize_mac_code
    typed = request.POST.get('mac_code_typed', '').strip()
    if typed:
        return normalize_mac_code(typed) or typed, 'typed'
    raw, source = request.POST.get('mac_code', '').strip(), 'typed'
    for prefix, label in LOCK_SOURCES:
        if raw.startswith(prefix):
            raw, source = raw[len(prefix):], label
            break
    return normalize_mac_code(raw) or raw, source


def _code_needed(request, contest, netid, name, reason, note):
    """The Offline exit code step when the lock on record is missing or stale: ask for the code on the student's screen.
    It posts the typed-code action, so the usual ownership checks apply to whatever is typed."""
    return _no_store(render(request, 'contest/fairplay_help_confirm.html', {
        'contest': contest, 'title': _('Participation'), 'action': 'offline-exit', 'button': ACTIONS['offline-exit'][0],
        'question': note, 'netid': netid, 'name': name, 'reason': reason, 'minutes': None, 'mac_code': '', 'mode': '',
        'need_code': True,
    }))


def _session_status(session, reopen_live, flags=None):
    """The state key and short label the Participation table shows for a student's latest session. `flags` is
    audit_summary(). Only an attempt that ended (handed in, ended by an instructor unlock or closed by the deadline)
    needs "Reopen exam"; one that was merely unlocked, or released without a lock, can be started again."""
    flags = flags or {}
    if session.state in ('active', 'finishing'):
        if session.state == 'finishing' and session.pk in flags.get('handed_in', ()):
            return {'state': 'submitting', 'short_status': _('Handed in, finishing')}
        return {'state': 'in_progress', 'short_status': _('In progress')}
    if reopen_live:
        return {'state': 'reopened', 'short_status': _('Reopened')}
    if session.state == 'completed':
        return {'state': 'completed', 'short_status': _('Completed')}
    if session.pk in flags.get('unlocked', ()):
        return {'state': 'unlocked', 'short_status': _('Unlocked')}
    if session.pk in flags.get('ended', ()):
        return {'state': 'completed', 'short_status': _('Ended')}
    return {'state': 'not_locked', 'short_status': _('Not locked')}


def is_fairplay_instructor(request, contest):
    """The one gate for everything FairPlay shows an instructor on the Participation tab."""
    return bool(fairplay_enabled() and is_fairplay_exam(contest) and contest.is_editable_by(request.user))


def participation_rows(request, contest, instructor=None):
    """Every student of a native FairPlay exam for the instructor's Participation tab: NetID, name and a short status.
    A fixed number of queries for the whole page, however many students there are. Empty when FairPlay does not
    apply."""
    if instructor is None:
        instructor = is_fairplay_instructor(request, contest)
    if not (instructor and native_browser_enabled()):
        return []
    from judge.models import FairPlayDeviceAudit, FairPlayDeviceSession
    from judge.utils.fairplay_device_session import (audit_summary, lease_expired_flagged, parse_window,
                                                     personal_deadline, reopen_minutes_from)
    from judge.utils.fairplay_package import exam_roster
    from datetime import timezone as dt_timezone
    from django.db.models import Count, Exists, Max, OuterRef, Q
    newer = FairPlayDeviceSession.objects.filter(contest=OuterRef('contest'), user=OuterRef('user')).filter(
        Q(created_at__gt=OuterRef('created_at')) | Q(created_at=OuterRef('created_at'), pk__gt=OuterRef('pk')))
    # Only each student's newest session (by -created_at, -pk) is read, not their whole history.
    latest = {}
    for session in FairPlayDeviceSession.objects.filter(contest=contest).annotate(has_newer=Exists(newer)).filter(
            has_newer=False).select_related('user', 'contest', 'participation', 'participation__contest'):
        latest[session.user.username] = session
    flags = audit_summary([s.pk for s in latest.values()])
    came_back = {username: (count, last) for username, count, last in FairPlayDeviceAudit.objects.filter(
        session__contest=contest, action='came-back').values('session__user__username')
        .annotate(count=Count('pk'), last=Max('created_at')).values_list('session__user__username', 'count', 'last')}
    now = timezone.now()
    rows = {}
    for username, label in ((p.user.username, p.user.get_full_name() or p.user.username)
                            for p in exam_roster(contest).select_related('user').order_by('user__username')[:2000]):
        rows[username] = {'netid': username, 'name': label, 'state': 'not_started', 'short_status': _('Not started'),
                          'notes': [], 'reopen_minutes': reopen_minutes_from(None)}
    for username, session in latest.items():
        row = rows.setdefault(username, {'netid': username, 'name': session.user.get_full_name() or username})
        window = parse_window(flags['windows'].get(session.pk))[0]
        ended = session.state not in ('active', 'finishing')
        row.update(_session_status(session, ended and session.pk in flags['reopened']
                                   and window is not None and window > now, flags))
        deadline = personal_deadline(session, reopen_at=window)
        row['reopen_minutes'] = reopen_minutes_from((deadline - now).total_seconds())
        notes = []
        if row['state'] == 'submitting':
            notes.append(_('Reopen the exam once the hand-in is complete if they need to continue.'))
        if session.state == 'active':
            if lease_expired_flagged(session, now=now, deadline=deadline,
                                     recorded=session.pk in flags['expired']):
                notes.append(_('The FairPlay connection of this device dropped during the exam.'))
        if session.pk in flags['unsubmitted']:
            notes.append(_('Some answers were saved but not submitted: %(problems)s. Ask the system administrator to '
                           'run the harvest consistency check, or reopen the student.')
                         % {'problems': flags['unsubmitted'][session.pk]})
        if username in came_back:
            count, last = came_back[username]
            notes.append(ngettext('Left and came back %(n)d time; last at %(time)s UTC.',
                                  'Left and came back %(n)d times; last at %(time)s UTC.', count)
                         % {'n': count, 'time': last.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M')})
        row['notes'] = notes
    return sorted(rows.values(), key=lambda row: row['netid'])


CLASSIC_STATES = {
    'not_started': ('not_started', _l('Not started')), 'writing': ('in_progress', _l('In progress')),
    'offline': ('in_progress', _l('In progress, offline')), 'waiting': ('waiting', _l('Waiting for approval')),
    'handed_in': ('completed', _l('Submitted')),
}

CLASSIC_FRESH_SECONDS = 15   # an answer this young is used as it is
CLASSIC_STALE_SECONDS = 900  # an older one is still shown at once while a background refresh replaces it
CLASSIC_DOWN_SECONDS = 5
CLASSIC_READ_TIMEOUT = 2


def _classic_cache_keys(contest, username):
    """One entry per exam and acting instructor: FairPlay answers as the instructor who asks."""
    return ('fp-classic-exam:%s:%s' % (contest.pk, username), 'fp-classic-down:%s:%s' % (contest.pk, username),
            'fp-classic-refresh:%s:%s' % (contest.pk, username))


def _forget_classic_exam(contest, username):
    from django.core.cache import cache
    cache.delete_many(_classic_cache_keys(contest, username))


def _fetch_classic_exam(contest, username):
    """Ask FairPlay (as `username`) and keep its answer; None when it cannot be reached. A failure is written on the
    kept entry, so a page that shows the older data can say so."""
    import time
    from django.core.cache import cache
    key, down, _refresh = _classic_cache_keys(contest, username)
    try:
        exam = FairPlayClient(acting_user=username).exam(contest.key, read_timeout=CLASSIC_READ_TIMEOUT)
    except FairPlayError:
        cache.set(down, 1, CLASSIC_DOWN_SECONDS)
        entry = cache.get(key)
        if isinstance(entry, dict):
            entry['failed'] = time.time()
            cache.set(key, entry, CLASSIC_STALE_SECONDS)
        return None
    if not isinstance(exam, dict):
        return None
    cache.set(key, {'at': time.time(), 'exam': exam}, CLASSIC_STALE_SECONDS)
    cache.delete(down)
    return exam


def _spawn(function):
    import threading
    threading.Thread(target=function, daemon=True).start()


def _age_text(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return ngettext('%(n)d second ago', '%(n)d seconds ago', seconds) % {'n': seconds}
    minutes = seconds // 60
    return ngettext('%(n)d minute ago', '%(n)d minutes ago', minutes) % {'n': minutes}


def classic_exam(contest, username):
    """(exam, unreachable, stale). A fresh cached answer is used; an older one is shown at once, refreshed in the
    background, and reported in `stale` ({'age': text, 'failed': whether the last refresh failed}) so it is never
    presented as live; only with nothing cached does the page wait for FairPlay (a short timeout)."""
    import time
    from django.core.cache import cache
    key, down, refresh = _classic_cache_keys(contest, username)
    entry = cache.get(key)
    if isinstance(entry, dict) and isinstance(entry.get('exam'), dict):
        age = time.time() - entry['at']
        if age <= CLASSIC_FRESH_SECONDS:
            return entry['exam'], False, None
        if cache.add(refresh, 1, CLASSIC_READ_TIMEOUT * 2 + 2):
            def background():
                try:
                    _fetch_classic_exam(contest, username)
                finally:
                    cache.delete(refresh)
            _spawn(background)
        failed = bool(entry.get('failed') and entry['failed'] > entry['at']) or bool(cache.get(down))
        return entry['exam'], False, {'age': _age_text(age), 'failed': failed}
    if cache.get(down):
        return {}, True, None
    exam = _fetch_classic_exam(contest, username)
    return (exam or {}), exam is None, None


def classic_rows(request, contest):
    """The students of a classic (FairPlay service) exam for the Participation table, and whether FairPlay answered."""
    exam, unreachable, stale = classic_exam(contest, request.user.username)
    if unreachable:
        return [], {}, True, None
    rows = []
    for s in exam.get('students') or []:
        if not isinstance(s, dict) or not s.get('netid'):
            continue
        state, label = CLASSIC_STATES.get(s.get('status'), ('not_started', s.get('status') or ''))
        if s.get('fp_mode') == 'submitted':
            state, label = CLASSIC_STATES['handed_in']
        notes = []
        if s.get('swap_requested'):
            notes.append(_('Asks to move to another computer.'))
        if s.get('reopened'):
            notes.append(_('Hand-in reopened.'))
        notes.append(_('Hand-ins: %(count)d.') % {'count': s.get('hand_in_count') or 0})
        rows.append({'netid': s['netid'], 'name': s.get('name') or s['netid'], 'state': state, 'short_status': label,
                     'notes': notes})
    return sorted(rows, key=lambda row: row['netid']), exam, False, stale


def participation_context(request, contest, with_rows=True, instructor=None):
    """Template context for the FairPlay part of an instructor's Participation tab. `instructor` is the gate the view
    already computed (is_fairplay_instructor); it is worked out here only when not given."""
    if instructor is None:
        instructor = is_fairplay_instructor(request, contest)
    context = {'fairplay_instructor': instructor, 'fairplay_students': [], 'fairplay_unreachable': False,
               'fairplay_ranked': {}, 'fairplay_problems': [],
               'fairplay_exam': {}, 'fairplay_stale': None, 'fairplay_block': instructor and with_rows,
               'fairplay_max_minutes': _minutes_limit(), 'fairplay_max_reopen_minutes': MAX_REOPEN_MINUTES}
    if not (instructor and with_rows):
        return context
    if native_browser_enabled():
        context['fairplay_students'] = participation_rows(request, contest, instructor=True)
    else:
        (context['fairplay_students'], context['fairplay_exam'], context['fairplay_unreachable'],
         context['fairplay_stale']) = classic_rows(request, contest)
    # The students' live participations as the standard ranking table builds them (problem cells and points).
    from judge.views.contests import get_contest_ranking_list
    users, problems = get_contest_ranking_list(request, contest, show_current_virtual=False)
    context['fairplay_ranked'] = {ranked.user.username: ranked for _rank, ranked in users}
    context['fairplay_problems'] = problems
    # One table of everyone: the FairPlay students plus any live participant FairPlay has no data for (not on the list,
    # the instructor's own participation, or FairPlay unreachable), shown without a FairPlay status.
    known = {row['netid'] for row in context['fairplay_students']}
    extra = [{'netid': name, 'name': ranked.user.get_full_name() or name, 'state': 'other',
              'short_status': '\u2014', 'notes': [], 'reopen_minutes': 30}
             for name, ranked in context['fairplay_ranked'].items() if name not in known]
    context['fairplay_students'] = sorted(list(context['fairplay_students']) + extra, key=lambda row: row['netid'])
    return context


def _accepted(request, contest, text):
    """An action was accepted: say so, and drop the cached roster so the next page load asks FairPlay again."""
    messages.success(request, text)
    _forget_classic_exam(contest, request.user.username)


class ContestStudentHelp(LoginRequiredMixin, View):
    def get(self, request, contest):
        # The separate help page is gone: old links and bookmarks land on the Participation tab.
        contest = _fairplay_contest(request, contest)
        return HttpResponseRedirect(reverse('contest_participation_own', args=[contest.key]))

    def post(self, request, contest):
        contest = _fairplay_contest(request, contest)
        action = request.POST.get('action', '')
        # 'offline-exit-on-record' is the Participation menu's own action: the exit code is issued for the lock on
        # record
        # and no code is typed. The help page's 'offline-exit' always needs the typed code.
        shown_action = action
        use_lock_on_record = action == ON_RECORD_ACTION
        if use_lock_on_record:
            if not native_browser_enabled():
                raise Http404()
            action = 'offline-exit'
        back = HttpResponseRedirect(reverse('contest_participation_own', args=[contest.key]))
        if action == 'sync':
            try:
                _client(request).sync_ping(exam_key=contest.key, read_timeout=QUICK_READ_TIMEOUT)
            except FairPlayError as e:
                _fail(request, e)
            else:
                _accepted(request, contest, _('FairPlay was asked to look again.'))
            return back
        if action not in ACTIONS:
            raise Http404()
        netid = request.POST.get('netid', '').strip()
        reason = request.POST.get('reason', '').strip()
        if not netid or len(netid) > NETID_MAX:
            raise Http404()
        name = request.POST.get('name', '').strip()[:100] or netid
        button, question, needs_minutes = ACTIONS[action]
        minutes = None
        problem = None
        mode = request.POST.get('mode', '')
        if action == 'quick-unlock':
            # A request without the field is the old form, which always meant unlock-only. A field that is present must
            # name its mode: Reopen is never turned into Emergency exit by an empty or lost value.
            mode = mode if 'mode' in request.POST else 'only'
            if not native_browser_enabled() or mode not in ('continue', 'only'):
                raise Http404()
            reason = reason or _('Unlocked by instructor %s') % request.user.username
            if mode == 'continue' and request.POST.get('minutes', '').strip():
                # The instructor chose how long the reopened exam lasts; without a value the default applies.
                minutes, problem = _parse_minutes(request, MAX_REOPEN_MINUTES)
        elif action == 'offline-exit' and not reason:
            reason = _('Exit code requested by instructor %s') % request.user.username
        if problem is None and not reason:
            problem = _('Write a reason first.')
        elif problem is None and len(reason) > MAX_REASON:
            problem = _('The reason is too long (at most %d characters).') % MAX_REASON
        if problem is None and needs_minutes:
            minutes, problem = _parse_minutes(request, _minutes_limit())
        mac_code, lock_field, source, choices, can_type, ask = '', '', 'typed', [], False, False
        lock_kind = ''
        if problem is None and action == 'offline-exit':
            # Ineligible netids (not a student, an instructor, cannot start the exam) fail now, before a code is asked.
            problem = _exit_target_problem(request, contest, netid)
            if problem is None:
                mac_code, source = _chosen_lock(request)
                request.fairplay_source = source
                lock_field = mac_code
                if len(mac_code) > 64:
                    problem = _('Type the code the student reads from the Mac.')
                elif not mac_code or request.POST.get('confirm') != '1':
                    # Nothing is issued without a confirming POST that carries the lock. The page offers the lock on
                    # record and the hints from refused claims, and always a field to type the code on the screen.
                    candidates = []
                    if native_browser_enabled():
                        from judge.utils.fairplay_device_session import lock_candidates
                        candidates = lock_candidates(contest, netid)
                    if not mac_code:
                        if not candidates:
                            return _code_needed(request, contest, netid, name, reason, _(
                                "No locked computer is on record for %(name)s in this exam.") % {'name': name} + (
                                    ' ' + _('If the computer is online, use Unlock student device instead.')
                                    if native_browser_enabled() else ''))
                        can_type = ask = True
                        if len(candidates) == 1 and candidates[0][1] == 'bound':
                            # Only the lock DKU OJ itself bound, and only when it is the sole candidate, is pre-filled.
                            mac_code = candidates[0][0]
                            lock_field = 'bound:' + mac_code
                            lock_kind = _('On record (verified by DKU OJ)')
                        else:
                            # A hint is what the student's app reported and is never pre-selected.
                            choices = [{'value': '%s:%s' % ('bound' if kind == 'bound' else 'hint', c),
                                        'shown': format_lock(c), 'kind': kind,
                                        'label': _('On record (verified by DKU OJ)') if kind == 'bound'
                                        else _("Reported by the student's app, not verified")}
                                       for c, kind in candidates]
        if problem:
            messages.error(request, problem)
            return back
        if request.POST.get('confirm') != '1' or ask:
            return _no_store(render(request, 'contest/fairplay_help_confirm.html', {
                'contest': contest, 'title': _('Participation'), 'action': shown_action, 'button': button,
                'question': question % {'name': name, 'netid': netid}, 'netid': netid, 'name': name, 'reason': reason,
                'minutes': minutes, 'mac_code': lock_field, 'mode': mode, 'choices': choices, 'can_type': can_type,
                'lock_kind': lock_kind,
                'lock_code': format_lock(mac_code) if action == 'offline-exit' else '',
            }))

        if action == 'quick-unlock':
            from judge.utils.fairplay_device_session import (DeviceSessionDenied, NoActiveDevice,
                                                             default_reopen_minutes, instructor_unlock,
                                                             instructor_unlock_and_reopen)
            try:
                if mode == 'only':
                    # Unlock student device: the computer unlocks and the app returns to its home screen. The attempt
                    # is not ended (nothing is detached), so the student may start the exam again by themselves.
                    instructor_unlock(contest=contest, username=netid, actor=request.user, reason=reason,
                                      resumable=True)
                    _accepted(request, contest,
                              _('Device unlocked. %(name)s returns to the start page. The attempt is not ended: the '
                                'student can start the exam again.') % {'name': name})
                else:
                    minutes = minutes or default_reopen_minutes(contest, netid)
                    reopened = instructor_unlock_and_reopen(contest=contest, username=netid, actor=request.user,
                                                            minutes=minutes, reason=reason)
                    _accepted(request, contest,
                              _('Device unlocked. %(name)s returns to the start page and can start again. ')
                              % {'name': name} + _reopen_note(contest, reopened, minutes))
            except NoActiveDevice:
                messages.info(request, _("This student's computer is not locked by this exam right now."))
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            return back

        if action == 'unlock-reopen':
            if not native_browser_enabled():
                raise Http404()
            from judge.utils.fairplay_device_session import instructor_unlock_and_reopen, DeviceSessionDenied
            try:
                reopened = instructor_unlock_and_reopen(contest=contest, username=netid, actor=request.user,
                                                        minutes=minutes, reason=reason)
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            else:
                _accepted(request, contest, _('Device unlocked and exam reopened. The student can start it again now.')
                                 + ' ' + _reopen_note(contest, reopened, minutes))
            return back

        if action in ('unlock-device', 'release-device') and native_browser_enabled():
            from judge.utils.fairplay_device_session import instructor_unlock, DeviceSessionDenied
            try:
                instructor_unlock(contest=contest, username=netid, actor=request.user, reason=reason)
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            else:
                _accepted(request, contest, _('Exam access revoked. The bound device will unlock when it receives the '
                                              'signed instruction.'))
            return back

        if action == 'extra-time' and native_browser_enabled():
            from judge.utils.fairplay_device_session import instructor_extra_time, DeviceSessionDenied
            try:
                instructor_extra_time(contest=contest, username=netid, actor=request.user, minutes=minutes, reason=reason)
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            else:
                _accepted(request, contest, _('Done: %(action)s for %(name)s.') % {'action': button, 'name': name})
            return back

        if action == 'reopen':
            from judge.models import FairPlayDeviceSession
            if FairPlayDeviceSession.objects.filter(contest=contest, user__username=netid).exists():
                from judge.utils.fairplay_device_session import instructor_reopen, DeviceSessionDenied
                try:
                    reopened = instructor_reopen(contest=contest, username=netid, actor=request.user,
                                                 minutes=minutes, reason=reason)
                except DeviceSessionDenied as error:
                    messages.error(request, str(error))
                else:
                    _accepted(request, contest, _('Exam reopened. The student can start one new native session.')
                                     + ' ' + _reopen_note(contest, reopened, minutes))
                return back

        if action == 'offline-exit':
            problem = None
            if native_browser_enabled():
                problem = _lock_problem(request, contest, netid, mac_code)
            if problem:
                messages.error(request, problem)
                return back
        client = _client(request)
        untagged_reason = reason  # forms are re-rendered with this; the site-administrator tag is added at send time
        if action == 'offline-exit' and request.user.is_superuser and request.profile.id not in contest.editor_ids:
            from judge.utils.fairplay_editor import acting_editor_username
            editor = acting_editor_username(contest)
            if editor:
                client = FairPlayClient(acting_user=editor)
                tag = ' (released by site administrator %s)' % request.user.username
                reason = reason[:max(0, MAX_REASON - len(tag))] + tag
        logger.info('FairPlay help by %s on %s for %s: %s (%s)', request.user.username, contest.key, netid,
                    action, reason)
        try:
            if action == 'approve':
                client.approve(contest.key, netid, reason)
            elif action == 'release-device':
                client.release_device(contest.key, netid, reason)
            elif action == 'reopen':
                client.reopen(contest.key, netid, minutes, reason)
            elif action == 'extra-time':
                client.extra_time(contest.key, netid, minutes, reason)
            else:
                try:
                    issued = client.native_exit(contest.key, netid, mac_code, reason)
                    pending = getattr(request, 'fairplay_override', None)
                    if pending is not None:
                        # Only after the server agreed: a refused request must change nothing here. A failure here
                        # must not hide the code that was already issued.
                        try:
                            from judge.utils.fairplay_device_session import override_lock_owner
                            override_lock_owner(pending[0], pending[1], pending[2], request.user)
                            logger.warning('FairPlay Mac code %s rebound by administrator %s to %s in %s',
                                           pending[2], request.user.username, pending[1], pending[0].key)
                        except Exception:
                            logger.exception('FairPlay Mac code %s could not be rebound to %s', pending[2], pending[1])
                except FairPlayError as e:
                    if e.status == 404 and source == 'on record':
                        return _code_needed(request, contest, netid, name, untagged_reason, _(
                            'The lock on record for %(name)s is not the one FairPlay knows now (the computer may have '
                            'a newer code). If the computer is online, use Unlock student device instead.') % {'name': name})
                    if e.status == 404 and native_browser_enabled():
                        messages.error(request, _('No FairPlay lock was registered for that code. The Mac has not registered it yet '
                                                  'and may still be retrying. Ask the student to close and reopen the Leave '
                                                  'sheet on the Mac to get a fresh code, then try again.'))
                        return back
                    if e.status == 404:
                        issued = client.offline_exit(contest.key, netid, mac_code)
                    elif e.status in EXIT_ERRORS:
                        messages.error(request, _exit_error(e))
                        return back
                    else:
                        raise
                _audit_issued(request, contest, netid, mac_code)  # every issued code, whichever route issued it
                return _no_store(render(request, 'contest/fairplay_help_code.html', {
                    'contest': contest, 'title': _('Participation'), 'code': issued.get('code'),
                    'first_name': name, 'last_name': '', 'netid': netid, 'offline': True,
                    'lock_code': format_lock(mac_code),
                }))
        except FairPlayError as e:
            _fail(request, e)
            return back
        _accepted(request, contest, _('Done: %(action)s for %(name)s.') % {'action': button, 'name': name})
        return back

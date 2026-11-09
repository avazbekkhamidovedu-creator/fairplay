"""The Download access codes (PDF) button and the student help page of a FairPlay exam."""
import logging
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy as _l
from django.views import View

from judge.models import Contest
from judge.utils.fairplay import fairplay_enabled, is_fairplay_exam
from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT

__all__ = ['ContestAccessCodesPdf', 'ContestStudentHelp']

logger = logging.getLogger('judge.fairplay')


def _client(request):
    return FairPlayClient(acting_user=request.user.username)


def _fairplay_contest(request, key):
    contest = get_object_or_404(Contest, key=key)
    if not fairplay_enabled() or not is_fairplay_exam(contest) or not contest.is_editable_by(request.user):
        raise Http404()
    return contest


class ContestAccessCodesPdf(LoginRequiredMixin, View):
    def get(self, request, contest):
        contest = _fairplay_contest(request, contest)
        back = HttpResponseRedirect(reverse('contest_participation_own', args=[contest.key]))
        try:
            pdf = _client(request).access_codes_pdf(contest.key, request.user.username)
        except FairPlayError as e:
            if e.status in (404, 409):
                messages.error(request, _('The access codes are not ready yet.'))
            else:
                messages.error(request, _('The access codes could not be downloaded.'))
            return back
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="access-codes-%s.pdf"' % contest.key
        response['Cache-Control'] = 'no-store'
        return response


MAX_REASON = 200
MAX_MINUTES = 8 * 60
NETID_MAX = 64

STATUS_LABELS = {
    'not_started': _l('not started'), 'writing': _l('in progress'), 'offline': _l('in progress, offline'),
    'waiting': _l('waiting for approval'), 'handed_in': _l('submitted'),
}
CODE_LABELS = {'none': _l('none'), 'unused': _l('unused'), 'bound': _l('bound')}

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
    'reissue': (_l('Issue new code'), _l('Issue a new access code to %(name)s? The old code stops working.'), False),
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
    """A Mac code opens only the Mac its student locked in this exam.

    A lock that no session ever claimed (locked before the exam started) can be released only by
    a site administrator, and that is logged. A lock bound to another exam or student never can.
    """
    from judge.utils.fairplay_device_session import lock_bound_to, normalize_mac_code, override_lock_owner
    code = normalize_mac_code(mac_code)
    if code is None:
        return _('That is not a Mac code.')
    bound = lock_bound_to(contest, netid, code, use_cache=False)
    if bound:
        return None
    if bound is False and request.user.is_superuser:
        request.fairplay_override = (contest, netid, code)
        return None
    if bound is False:
        return _('This Mac code belongs to another exam or student. No exit code was issued.')
    if request.user.is_superuser:
        logger.warning('FairPlay unbound Mac code %s released by administrator %s for %s in %s',
                       code, request.user.username, netid, contest.key)
        return None
    return _('This Mac is not linked to this student in this exam. Ask a site administrator to release it.')


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


def _students(exam):
    rows = []
    for s in exam.get('students') or []:
        if not isinstance(s, dict) or not s.get('netid'):
            continue
        rows.append(dict(s, status_label=STATUS_LABELS.get(s.get('status'), s.get('status') or ''),
                         code_label=CODE_LABELS.get(s.get('code_state'), s.get('code_state') or ''),
                         hand_ins=s.get('hand_in_count') or 0))
    return rows


class ContestStudentHelp(LoginRequiredMixin, View):
    def get(self, request, contest):
        contest = _fairplay_contest(request, contest)
        return self.page(request, contest)

    def page(self, request, contest):
        exam, unreachable = {}, False
        native = getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is True
        if not native:
            try:
                exam = _client(request).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
            except FairPlayError:
                unreachable = True
        students = _students(exam)
        from judge.models import FairPlayDeviceSession
        indexed = {row['netid']: row for row in students}
        seen = set()
        for session in FairPlayDeviceSession.objects.filter(contest=contest).select_related('user').order_by('-created_at'):
            if session.user_id in seen:
                continue
            seen.add(session.user_id)
            row = indexed.get(session.user.username)
            if row is None:
                row = {'netid': session.user.username, 'name': session.user.get_full_name() or session.user.username,
                       'code_label': '-', 'hand_ins': 0}
                students.append(row)
                indexed[session.user.username] = row
            active = session.state in ('active', 'finishing')
            from judge.models import FairPlayDeviceAudit
            from judge.utils.fairplay_device_session import session_on_hold, reopen_until, reopen_used_seconds
            if session.state == 'active':
                from judge.utils.fairplay_device_session import lease_expired_flagged
                row['native_lease_expired'] = lease_expired_flagged(session)
            note = None
            if not active:
                from judge.models import FairPlayDeviceAudit
                from django.utils import timezone
                reopened = FairPlayDeviceAudit.objects.filter(session=session, action='reopen').exists()
                window = reopen_until(session)
                if reopened and window and window > timezone.now():
                    note = _('Reopened. The student can start the exam again.')
                    used = reopen_used_seconds(session)
                    if used:
                        note += ' ' + _('Time used before the reopen: %(minutes)d min.') % {'minutes': used // 60}
                elif session.state == 'completed':
                    note = _('Handed in. Choose Reopen exam if the student must continue.')
                else:
                    note = _('Unlocked. Choose Reopen exam so the student can start again.')
            window = reopen_until(session) if not active else None
            reopen_live = (window is not None and window > timezone.now()
                           and FairPlayDeviceAudit.objects.filter(session=session, action='reopen').exists())
            unsubmitted = FairPlayDeviceAudit.objects.filter(session=session, action='completed-with-unsubmitted-draft') \
                .order_by('-pk').values_list('reason', flat=True).first()
            row.update(native_unsubmitted=(_('Some answers were saved but not submitted: %(problems)s. Ask the system administrator to run the harvest consistency check, or reopen the student.') % {'problems': unsubmitted}) if unsubmitted else '', unlock_question=str(ACTIONS['quick-unlock'][1]) % {'name': row.get('name') or row['netid'], 'netid': row['netid']},
                       native_unlock_only_allowed=active,
                       native_can_unlock=active or (session.state == 'released' and not reopen_live),
                       native_note=note, native_session_active=active, native_hold=session.state == 'active' and session_on_hold(session),
                       status='writing' if active else 'handed_in',
                       status_label=_('in progress') if active else (_('completed') if session.state == 'completed' else _('device released')))

        roster = []
        if native:
            from judge.utils.fairplay_package import exam_roster
            roster = [(p.user.username, p.user.get_full_name() or p.user.username)
                      for p in exam_roster(contest).select_related('user').order_by('user__username')[:2000]]
        return _no_store(render(request, 'contest/fairplay_help.html', {
            'contest': contest, 'title': _('FairPlay student help'), 'exam': exam, 'students': students, 'roster': roster,
            'native_mode': native, 'unreachable': unreachable, 'actions': ACTIONS, 'max_minutes': MAX_MINUTES,
        }))

    def post(self, request, contest):
        contest = _fairplay_contest(request, contest)
        action = request.POST.get('action', '')
        back = HttpResponseRedirect(reverse('contest_fairplay_help', args=[contest.key]))
        if action == 'sync':
            try:
                _client(request).sync_ping(exam_key=contest.key, read_timeout=QUICK_READ_TIMEOUT)
            except FairPlayError as e:
                _fail(request, e)
            else:
                messages.success(request, _('FairPlay was asked to look again.'))
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
            mode = mode or 'only'
            if getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is not True or mode not in ('continue', 'only'):
                raise Http404()
            reason = reason or _('Unlocked by instructor %s') % request.user.username
        elif action == 'offline-exit' and not reason:
            reason = _('Exit code requested by instructor %s') % request.user.username
        if not reason:
            problem = _('Write a reason first.')
        elif len(reason) > MAX_REASON:
            problem = _('The reason is too long (at most %d characters).') % MAX_REASON
        if problem is None and needs_minutes:
            try:
                minutes = int(request.POST.get('minutes', ''))
            except ValueError:
                minutes = 0
            if not 1 <= minutes <= MAX_MINUTES:
                problem = _('Minutes must be a whole number from 1 to %d.') % MAX_MINUTES
        mac_code = request.POST.get('mac_code', '').strip()
        if problem is None and action == 'offline-exit' and not (0 < len(mac_code) <= 64):
            problem = _('Type the code the student reads from the Mac.')
        if problem:
            messages.error(request, problem)
            return back
        if request.POST.get('confirm') != '1':
            return _no_store(render(request, 'contest/fairplay_help_confirm.html', {
                'contest': contest, 'title': _('FairPlay student help'), 'action': action, 'button': button,
                'question': question % {'name': name, 'netid': netid}, 'netid': netid, 'name': name, 'reason': reason,
                'minutes': minutes, 'mac_code': mac_code, 'mode': mode,
            }))

        if action == 'quick-unlock':
            from judge.utils.fairplay_device_session import (DeviceSessionDenied, default_reopen_minutes,
                                                             instructor_unlock_and_reopen, instructor_unlock_end)
            try:
                if mode == 'only':
                    instructor_unlock_end(contest=contest, username=netid, actor=request.user, reason=reason)
                    messages.success(request, _('Device unlocked. %(name)s returns to the start page and the attempt is '
                                                'ended. Saved answers are kept.') % {'name': name})
                else:
                    minutes = default_reopen_minutes(contest, netid)
                    reopened = instructor_unlock_and_reopen(contest=contest, username=netid, actor=request.user,
                                                            minutes=minutes, reason=reason)
                    messages.success(request, _('Device unlocked. %(name)s returns to the start page and can start again. ')
                                     % {'name': name} + _reopen_note(contest, reopened, minutes))
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            return back

        if action == 'unlock-reopen':
            if getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is not True:
                raise Http404()
            from judge.utils.fairplay_device_session import instructor_unlock_and_reopen, DeviceSessionDenied
            try:
                reopened = instructor_unlock_and_reopen(contest=contest, username=netid, actor=request.user,
                                                        minutes=minutes, reason=reason)
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            else:
                messages.success(request, _('Device unlocked and exam reopened. The student can start it again now.')
                                 + ' ' + _reopen_note(contest, reopened, minutes))
            return back

        if action in ('unlock-device', 'release-device') and getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is True:
            from judge.utils.fairplay_device_session import instructor_unlock, DeviceSessionDenied
            try:
                instructor_unlock(contest=contest, username=netid, actor=request.user, reason=reason)
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            else:
                messages.success(request, _('Exam access revoked. The bound device will unlock when it receives the signed instruction.'))
            return back

        if action == 'extra-time' and getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is True:
            from judge.utils.fairplay_device_session import instructor_extra_time, DeviceSessionDenied
            try:
                instructor_extra_time(contest=contest, username=netid, actor=request.user, minutes=minutes, reason=reason)
            except DeviceSessionDenied as error:
                messages.error(request, str(error))
            else:
                messages.success(request, _('Done: %(action)s for %(name)s.') % {'action': button, 'name': name})
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
                    messages.success(request, _('Exam reopened. The student can start one new native session.')
                                     + ' ' + _reopen_note(contest, reopened, minutes))
                return back

        if action == 'offline-exit':
            problem = _exit_target_problem(request, contest, netid)
            if problem is None and getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is True:
                problem = _lock_problem(request, contest, netid, mac_code)
            if problem:
                messages.error(request, problem)
                return back
        client = _client(request)
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
            elif action == 'reissue':
                issued = client.reissue_code(contest.key, netid, reason)
                return _no_store(render(request, 'contest/fairplay_help_code.html', {
                    'contest': contest, 'title': _('FairPlay student help'), 'code': issued.get('code'),
                    'first_name': issued.get('first_name') or '', 'last_name': issued.get('last_name') or '',
                    'netid': netid,
                }))
            elif action == 'reopen':
                client.reopen(contest.key, netid, minutes, reason)
            elif action == 'extra-time':
                client.extra_time(contest.key, netid, minutes, reason)
            else:
                try:
                    issued = client.native_exit(contest.key, netid, mac_code, reason)
                    pending = getattr(request, 'fairplay_override', None)
                    if pending is not None:
                        # Only after the server agreed: a refused request must change nothing here.
                        from judge.utils.fairplay_device_session import override_lock_owner
                        override_lock_owner(pending[0], pending[1], pending[2], request.user)
                        logger.warning('FairPlay Mac code %s rebound by administrator %s to %s in %s',
                                       pending[2], request.user.username, pending[1], pending[0].key)
                except FairPlayError as e:
                    if e.status == 404 and getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is True:
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
                return _no_store(render(request, 'contest/fairplay_help_code.html', {
                    'contest': contest, 'title': _('FairPlay student help'), 'code': issued.get('code'),
                    'first_name': name, 'last_name': '', 'netid': netid, 'offline': True,
                }))
        except FairPlayError as e:
            _fail(request, e)
            return back
        messages.success(request, _('Done: %(action)s for %(name)s.') % {'action': button, 'name': name})
        return back

"""The Download access codes (PDF) button and the student help page of a FairPlay exam."""
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
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


MAX_REASON = 300
MAX_MINUTES = 8 * 60
NETID_MAX = 64

STATUS_LABELS = {
    'not_started': _l('not started'), 'writing': _l('in progress'), 'offline': _l('in progress, offline'),
    'waiting': _l('waiting for approval'), 'handed_in': _l('submitted'),
}
CODE_LABELS = {'none': _l('none'), 'unused': _l('unused'), 'bound': _l('bound')}

# action -> (button, question asked on the confirm step, needs minutes)
ACTIONS = {
    'approve': (_l('Approve resume'), _l('Approve %(name)s to resume the exam?'), False),
    'release-device': (_l('Move to another computer'),
                       _l('Let %(name)s sign in again from another computer?'), False),
    'reissue': (_l('Issue new code'), _l('Issue a new access code to %(name)s? The old code stops working.'), False),
    'reopen': (_l('Reopen hand-in'), _l('Reopen the hand-in of %(name)s?'), True),
    'extra-time': (_l('Give extra time'), _l('Give %(name)s extra time?'), True),
    'offline-exit': (_l('Offline exit code'), _l('Get the offline exit code for %(name)s?'), False),
}


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


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
        try:
            exam = _client(request).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
        except FairPlayError:
            unreachable = True
        return _no_store(render(request, 'contest/fairplay_help.html', {
            'contest': contest, 'title': _('FairPlay student help'), 'exam': exam, 'students': _students(exam),
            'unreachable': unreachable, 'actions': ACTIONS, 'max_minutes': MAX_MINUTES,
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
                'question': question % {'name': name}, 'netid': netid, 'name': name, 'reason': reason,
                'minutes': minutes, 'mac_code': mac_code,
            }))

        client = _client(request)
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
                issued = client.offline_exit(contest.key, netid, mac_code)
                return _no_store(render(request, 'contest/fairplay_help_code.html', {
                    'contest': contest, 'title': _('FairPlay student help'), 'code': issued.get('code'),
                    'first_name': name, 'last_name': '', 'netid': netid, 'offline': True,
                }))
        except FairPlayError as e:
            _fail(request, e)
            return back
        messages.success(request, _('Done: %(action)s for %(name)s.') % {'action': button, 'name': name})
        return back

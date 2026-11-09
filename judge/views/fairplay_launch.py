import json
import re
from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from judge.models import Contest
from judge.utils.fairplay import fairplay_enabled, fairplay_hidden_for, is_fairplay_exam
from judge.utils.fairplay_package import exam_roster
from judge.utils.fairplay_audience import audience_descriptor


def student_can_launch(request, contest):
    return bool(request.user.is_authenticated and fairplay_enabled() and
                is_fairplay_exam(contest) and contest.is_visible and
                re.fullmatch(r'[A-Za-z0-9_-]{1,128}', contest.key) and
                audience_descriptor(contest)['confirmed'] and
                exam_roster(contest).filter(pk=request.profile.pk).exists())


def _contest(request, key):
    contest = get_object_or_404(Contest, key=key)
    if not request.user.is_authenticated or not fairplay_enabled() or not is_fairplay_exam(contest):
        raise Http404()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', contest.key):
        raise Http404()
    if not contest.is_editable_by(request.user) and not student_can_launch(request, contest):
        raise Http404()
    return contest


def _private(response):
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _page(request, contest):
    return _private(render(request, 'contest/fairplay_required.html', {
        'contest': contest,
        'title': _('Open exam in FairPlay'),
        'launch_url': 'fairplay://launch?' + urlencode({'v': 1, 'exam': contest.key}),
        'launch_file_url': reverse('contest_fairplay_launch_file', args=[contest.key]),
    }))


def hidden_exam_launch(request, key, redirect=False):
    if not request.user.is_authenticated or not fairplay_enabled():
        return None
    contest = Contest.objects.filter(key=key).first()
    if contest is None or not is_fairplay_exam(contest) or not fairplay_hidden_for(request.user, contest):
        return None
    contest = _contest(request, key)
    if redirect:
        return _private(HttpResponseRedirect(reverse('contest_fairplay_launch', args=[contest.key])))
    return _page(request, contest)


def hidden_problem_launch(request, code):
    """The launch page for a problem of a FairPlay exam this student may take but has not opened in the app."""
    if not request.user.is_authenticated or not fairplay_enabled():
        return None
    from judge.models import ContestProblem
    contests = Contest.objects.filter(
        id__in=ContestProblem.objects.filter(problem__code=code).values('contest_id'))
    for contest in contests:
        if (is_fairplay_exam(contest) and fairplay_hidden_for(request.user, contest)
                and student_can_launch(request, contest)):
            response = _page(request, contest)
            response.status_code = 403
            return response
    return None


class ContestFairPlayLaunch(LoginRequiredMixin, View):
    def get(self, request, contest):
        return _page(request, _contest(request, contest))


class ContestFairPlayLaunchFile(LoginRequiredMixin, View):
    def get(self, request, contest):
        contest = _contest(request, contest)
        response = HttpResponse(json.dumps({'version': 1, 'exam_key': contest.key}),
                                content_type='application/vnd.fairplay.launch+json')
        response['Content-Disposition'] = 'attachment; filename="%s.fairplay"' % contest.key
        response['X-Content-Type-Options'] = 'nosniff'
        return _private(response)

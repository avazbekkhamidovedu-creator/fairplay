import json
import re
import time
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
        'server_now_ms': int(time.time() * 1000),
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


class ContestFairPlayLaunchStatus(LoginRequiredMixin, View):
    """Whether this user's FairPlay app took the exam over since the Start button was clicked. The launch page polls it
    with the click time (on the server's clock, see the page). The app's claim and recover calls (never heartbeats)
    leave a timestamp in the cache; a session created after the click counts too. It reveals nothing beyond the
    user's own state."""

    ELIGIBLE_SECONDS = 60

    def get(self, request, contest):
        from datetime import datetime, timezone as dt_timezone
        from django.core.cache import cache
        from judge.models import FairPlayDeviceSession
        from judge.utils.fairplay_device_session import app_launch_mark
        # A light gate for a poll: the full launch gate runs once a minute per user and exam, and its answer (the
        # contest's pk) is cached, so a poll does no second lookup. Every query also filters on request.user.
        import hashlib
        # the URL text is never used as a cache key itself
        key = 'fp-launch-eligible:%s:%s' % (request.user.pk, hashlib.sha1(contest.encode('utf-8')).hexdigest())
        contest_id = cache.get(key)
        if not isinstance(contest_id, int):
            contest_id = _contest(request, contest).pk
            cache.set(key, contest_id, self.ELIGIBLE_SECONDS)
        try:
            since_ms = float(request.GET.get('since', ''))
            since = datetime.fromtimestamp(since_ms / 1000.0, tz=dt_timezone.utc)
        except (ValueError, OverflowError, OSError):
            since_ms = since = None
        started = False
        if since is not None:
            mark = app_launch_mark(request.user.pk, contest_id)
            started = (mark is not None and mark >= since_ms) or FairPlayDeviceSession.objects.filter(
                contest_id=contest_id, user=request.user, state__in=('active', 'finishing'),
                created_at__gte=since).exists()
        return _private(HttpResponse(json.dumps({'started': started}), content_type='application/json'))

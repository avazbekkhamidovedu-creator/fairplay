"""The Download access codes (PDF) button on the Participation page."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from judge.models import Contest
from judge.utils.fairplay import fairplay_enabled, is_fairplay_exam
from judge.utils.fairplay_client import FairPlayClient, FairPlayError

__all__ = ['ContestAccessCodesPdf']


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

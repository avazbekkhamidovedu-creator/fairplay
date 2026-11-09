"""Narrow protected-content admission. Off without the authenticated ingress.

Every route is served inside the device scope when the request carries a valid device identity and the device
has a session row; hiding stays for everything else. Admitted writes hold the same account row lock as
finish/revoke. A write that began first must finish before revocation; a later write cannot inherit admission.
Streaming responses are excluded from the locked write path because their work outlives this transaction.
"""
from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse
from django.urls import Resolver404, resolve
from judge.models import ContestProblem, Profile
from django.utils import timezone
from judge.utils.fairplay_device_session import (admitted_session, served_session, attach_participation,
                                                 DeviceSessionDenied, personal_deadline, session_on_hold)
from judge.utils.fairplay_native_scope import device_scope
from judge.views.fairplay_native_session import request_identity

_CONTEST_ROUTES = {'contest_view': {'GET', 'HEAD'}, 'contest_join': {'POST'},
                   'assignment_draft': {'POST', 'DELETE'}, 'contest_leave': {'POST'}}
_PROBLEM_ROUTES = {'problem_list': {'GET'}, 'problem_detail': {'GET', 'HEAD'}, 'problem_submit': {'GET', 'POST'},
                   'problem_workspace_run': {'POST'},
                   'problem_workspace_submissions': {'GET'},
                   'problem_workspace_submission_status': {'GET'}}


_IDENTITY_HEADERS = ('HTTP_X_FAIRPLAY_CLIENT_VERIFY', 'HTTP_X_FAIRPLAY_CLIENT_CERT', 'HTTP_X_FAIRPLAY_INGRESS_KEY',
                     'HTTP_X_FAIRPLAY_TLS_FINGERPRINT', 'HTTP_X_FAIRPLAY_TLS_SIGNATURE')


class NativeBrowserAdmissionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (getattr(settings, 'FAIRPLAY_NATIVE_BROWSER_ENABLED', False) is not True
                or not request.user.is_authenticated):
            return self.get_response(request)
        try:
            route = resolve(request.path_info, getattr(request, 'urlconf', None))
        except Resolver404:
            return self.get_response(request)
        name = route.url_name or ''
        if name.startswith('fairplay_native_') or name == 'fairplay_browser_context':
            return self.get_response(request)
        if not any(header in request.META for header in _IDENTITY_HEADERS):
            return self.get_response(request)
        try:
            fingerprint, digest = request_identity(request)
        except Http404:
            # No new permission: existing protected-content gates still apply.
            return self.get_response(request)
        request._fairplay_identity = (fingerprint, digest)
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            session = served_session(user_id=request.user.pk, fingerprint=fingerprint, browser_digest=digest)
            if session is None:
                return self.get_response(request)
            with device_scope(session):
                response = self.get_response(request)
            response['Cache-Control'] = 'private, no-store'
            return response
        if name not in _CONTEST_ROUTES and name not in _PROBLEM_ROUTES:
            return self.get_response(request)
        with transaction.atomic():
            locked_profile = Profile.objects.select_for_update().get(user_id=request.user.pk)
            if name == 'contest_join':
                session = admitted_session(user_id=request.user.pk, fingerprint=fingerprint, browser_digest=digest)
            else:
                session = served_session(user_id=request.user.pk, fingerprint=fingerprint, browser_digest=digest,
                                         states=('active',))
            if session is not None and name != 'contest_join' and (
                    personal_deadline(session) <= timezone.now() or session_on_hold(session)):
                session = None
            if session is None:
                return self.get_response(request)
            if name in _CONTEST_ROUTES:
                matches = route.kwargs.get('contest') == session.contest.key
            else:
                matches = (session.participation_id is not None and
                           locked_profile.current_contest_id == session.participation_id and
                           (name == 'problem_list' or ContestProblem.objects.filter(contest_id=session.contest_id,
                               problem__code=route.kwargs.get('problem')).exists()))
            if not matches:
                return self.get_response(request)
            with device_scope(session):
                response = self.get_response(request)
                if response.status_code >= 500:
                    transaction.set_rollback(True)
                if getattr(response, 'streaming', False):
                    transaction.set_rollback(True)
                    response.close()
                    return HttpResponse(status=403)
                if name == 'contest_join' and response.status_code < 400:
                    profile = Profile.objects.select_related('current_contest').get(user_id=request.user.pk)
                    participation = profile.current_contest
                    if participation is not None and participation.contest_id == session.contest_id:
                        try:
                            attach_participation(session_id=session.pk, user_id=request.user.pk,
                                fingerprint=fingerprint, browser_digest=digest, participation=participation)
                        except DeviceSessionDenied:
                            transaction.set_rollback(True)
                            return HttpResponse(status=409)
                response['Cache-Control'] = 'private, no-store'
                return response

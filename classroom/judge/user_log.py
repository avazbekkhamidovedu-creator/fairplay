from datetime import timedelta

from django.utils.timezone import now

from judge.models import Profile

# last_access is shown in the admin only, so it does not need second-level precision. Writing it
# on every request turned every page view, autosave and status poll into an UPDATE of the user's
# profile row; with hundreds of users online that is most of the database's write load.
LAST_ACCESS_RESOLUTION = timedelta(seconds=60)


class LogUserAccessMiddleware(object):
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if (hasattr(request, 'user') and request.user.is_authenticated and
                not getattr(request, 'no_profile_update', False)):
            current = now()
            # Decided on using REMOTE_ADDR as nginx will translate it to the external IP that hits it.
            ip = request.META.get('REMOTE_ADDR')
            profile = getattr(request, 'profile', None)
            if (profile is None or profile.last_access is None or (ip and ip != profile.ip) or
                    current - profile.last_access >= LAST_ACCESS_RESOLUTION):
                updates = {'last_access': current}
                if ip:
                    updates['ip'] = ip
                Profile.objects.filter(user_id=request.user.pk).update(**updates)

        return response

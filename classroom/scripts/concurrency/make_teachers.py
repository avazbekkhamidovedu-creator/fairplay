"""Create instructors t01..t30 (admins/instructors of cs101) for race.py / soak.py. DEV DATABASE ONLY.

usage (after seed_dev_data):
    python manage.py shell -c "exec(open('scripts/concurrency/make_teachers.py').read())"
"""
from django.contrib.auth.models import User

from judge.models import Language, Organization, Profile
from judge.models.notification import ensure_instructors_group

group = ensure_instructors_group()
cs = Organization.objects.get(slug='cs101')
for i in range(1, 31):
    name = 't%02d' % i
    user = User.objects.filter(username=name).first() or User(username=name)
    user.email = '%s@example.edu' % name
    user.first_name, user.last_name = 'Teacher', name
    user.set_password('fairplay-dev')
    user.is_active = True
    user.save()
    profile, _ = Profile.objects.get_or_create(user=user)
    profile.language = Language.get_default_language()
    profile.save()
    user.groups.add(group)
    cs.admins.add(profile)
    cs.instructors.add(profile)
print('ok')

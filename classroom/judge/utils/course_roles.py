"""Course-role lookups that stay fast however many courses, classes and members there are.

``Organization.objects.filter(Q(member=p) | Q(admins=p) | Q(class__members=p) ...)`` makes Django
LEFT JOIN every role table at once. MariaDB then builds the cross product of all members, admins
and classes of every course before it can apply the OR: at 150 courses with ~60 members each that
took 2.4 s, and it ran on every page for every signed-in course member. ``courses_q`` asks the
same question with one indexed ``IN (SELECT ...)`` per role instead (about 1 ms), and the result is
identical: a course matches when the profile has any of the roles.
"""
from django.db.models import Q

ROLES = ('member', 'admin', 'instructor', 'class_admin', 'class_member')


def _role_subquery(role, profile):
    from judge.models import Class, Organization, Profile
    if role == 'member':
        return Profile.organizations.through.objects.filter(profile=profile).values('organization_id')
    if role == 'admin':
        return Organization.admins.through.objects.filter(profile=profile).values('organization_id')
    if role == 'instructor':
        return Organization.instructors.through.objects.filter(profile=profile).values('organization_id')
    if role == 'class_admin':
        return Class.admins.through.objects.filter(profile=profile).values('class__organization_id')
    if role == 'class_member':
        return Class.members.through.objects.filter(profile=profile).values('class__organization_id')
    raise ValueError('Unknown course role: %r' % (role,))


def courses_q(profile, roles, field='id'):
    """A ``Q`` matching rows whose ``field`` (a course id) is a course where ``profile`` has any of ``roles``.

    ``field`` is ``'id'`` on ``Organization`` or a plain course foreign key such as ``'organization_id'``
    on ``Class``. Do not pass a many-to-many path: that brings the joins back. With no profile,
    matches nothing.
    """
    if profile is None:
        return Q(pk__in=[])
    q = Q()
    for role in roles:
        q |= Q(**{'%s__in' % field: _role_subquery(role, profile)})
    return q


def teaching_q(profile, field='id'):
    """Courses ``profile`` teaches: course admin, course instructor or admin of one of its classes."""
    return courses_q(profile, ('admin', 'instructor', 'class_admin'), field)

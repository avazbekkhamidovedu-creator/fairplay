"""Who teaches what, for the FairPlay pages: course and class rosters and the permission rules."""
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q

from judge.models import Class, Organization, Profile
from judge.utils.fairplay import FAIRPLAY_TAG, fairplay_enabled, fairplay_exams

__all__ = [
    'class_has_fairplay_login', 'class_roster', 'classes_with_fairplay_login', 'course_classes_for',
    'course_fairplay_exams', 'courses_taught_by', 'fairplay_editor_ids', 'mark_class_login', 'show_fairplay_menu',
    'students_only', 'user_is_fairplay_staff', 'user_manages_class', 'user_teaches_course',
]

# FairPlay may ask for the roster of a class with a login made here before the class has any exam.
CLASS_LOGIN_KEY = 'fpb:class-login:%d'
CLASS_LOGIN_TTL = int(timedelta(days=180).total_seconds())


def mark_class_login(class_id):
    cache.set(CLASS_LOGIN_KEY % class_id, 1, CLASS_LOGIN_TTL)


def class_has_fairplay_login(class_id):
    return cache.get(CLASS_LOGIN_KEY % class_id) is not None


def classes_with_fairplay_login(class_ids):
    """Of ``class_ids``, those with a FairPlay class login made from DKU OJ."""
    class_ids = list(class_ids)
    if not class_ids:
        return set()
    found = cache.get_many([CLASS_LOGIN_KEY % class_id for class_id in class_ids])
    return {class_id for class_id in class_ids if (CLASS_LOGIN_KEY % class_id) in found}


def user_is_fairplay_staff(user):
    return bool(user.is_authenticated and (
        user.is_staff or user.is_superuser or user.has_perm('judge.edit_all_organization')))


def courses_taught_by(user):
    """Courses shown on ``/fairplay/`` for ``user`` (all courses for site staff)."""
    if not user.is_authenticated:
        return Organization.objects.none()
    if user_is_fairplay_staff(user):
        return Organization.objects.all()
    profile = user.profile
    return Organization.objects.filter(
        Q(admins=profile) | Q(instructors=profile) | Q(class__admins=profile)).distinct()


def show_fairplay_menu(user):
    if not fairplay_enabled() or not user.is_authenticated:
        return False
    if user_is_fairplay_staff(user):
        return True
    return courses_taught_by(user).exists()


def user_teaches_course(user, org):
    if not user.is_authenticated:
        return False
    return user_is_fairplay_staff(user) or org.profile_teaches_course(user.profile)


def user_manages_class(user, klass):
    """Course administrator, the class's instructor (class admin), or site staff."""
    if not user.is_authenticated:
        return False
    if user_is_fairplay_staff(user):
        return True
    profile = user.profile
    return (klass.organization.admins.filter(id=profile.id).exists() or
            klass.admins.filter(id=profile.id).exists())


def course_classes_for(user, org):
    """The active classes of ``org`` whose FairPlay logins/students ``user`` may manage."""
    classes = Class.objects.filter(organization=org, is_active=True).order_by('name')
    if not user.is_authenticated:
        return classes.none()
    if user_is_fairplay_staff(user) or org.admins.filter(id=user.profile.id).exists():
        return classes
    return classes.filter(admins=user.profile)


def course_fairplay_exams(org):
    return (fairplay_exams().filter(Q(organizations=org) | Q(classes__organization=org)).distinct()
            .order_by('start_time', 'key'))


def fairplay_editor_ids():
    """Authors, curators and testers of any FairPlay exam, never offered as students."""
    return set(Profile.objects.filter(Q(authored_contests__tags__name=FAIRPLAY_TAG) |
                                      Q(curated_contests__tags__name=FAIRPLAY_TAG) |
                                      Q(tested_contests__tags__name=FAIRPLAY_TAG)).values_list('id', flat=True))


def students_only(queryset, org_ids, class_ids, also=()):
    """``queryset`` without the course's teachers, FairPlay exam staff and site staff."""
    teachers = (set(Profile.objects.filter(Q(admin_of__in=org_ids) | Q(instructor_of__in=org_ids) |
                                           Q(class_admin_of__in=class_ids)).values_list('id', flat=True)) |
                fairplay_editor_ids() | set(also))
    return (queryset.exclude(id__in=teachers).exclude(user__is_staff=True).exclude(user__is_superuser=True)
            .select_related('user').distinct().order_by('user__username'))


def class_roster(klass):
    return students_only(Profile.objects.filter(class__id=klass.id), {klass.organization_id}, {klass.id})

"""Class rosters for the FairPlay bridge: who counts as a student of a class."""
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q

from judge.models import Profile
from judge.utils.fairplay import FAIRPLAY_TAG

__all__ = [
    'class_has_fairplay_login', 'class_roster', 'classes_with_fairplay_login', 'fairplay_editor_ids',
    'mark_class_login', 'students_only',
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

import logging

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_delete, pre_delete
from django.dispatch import receiver

from judge.models import Class, Contest, Organization, Profile
from judge.utils.fairplay import fairplay_enabled, fairplay_exams
from judge.utils.fairplay_client import FairPlayClient, FairPlayError
from judge.utils.fairplay_sync import _run_later

logger = logging.getLogger('judge.fairplay')


def notify(event, keys=(), class_ids=(), organization_ids=()):
    if not fairplay_enabled():
        return
    payload = {'event': event, 'exam_keys': sorted(set(keys)), 'class_ids': sorted(set(class_ids)),
               'organization_ids': sorted(set(organization_ids))}

    def send():
        try:
            FairPlayClient().sync_ping(read_timeout=1, **payload)
        except FairPlayError as error:
            logger.warning('FairPlay lifecycle notification failed; authoritative reconciliation required: event=%s exams=%s status=%s',
                           event, payload['exam_keys'], error.status)

    transaction.on_commit(lambda: _run_later(0, send))


def _scope(contests):
    rows = list(contests.distinct())
    return ([row.key for row in rows],
            sorted({pk for row in rows for pk in row.classes.values_list('pk', flat=True)}),
            sorted({pk for row in rows for pk in row.organizations.values_list('pk', flat=True)}))


@receiver(pre_delete, sender=Contest, dispatch_uid='fp_lifecycle_contest_predelete')
@receiver(pre_delete, sender=Class, dispatch_uid='fp_lifecycle_class_predelete')
@receiver(pre_delete, sender=Organization, dispatch_uid='fp_lifecycle_org_predelete')
def before_delete(sender, instance, **kwargs):
    if sender is Contest:
        contests = fairplay_exams().filter(pk=instance.pk)
    elif sender is Class:
        contests = fairplay_exams().filter(classes=instance)
    else:
        contests = fairplay_exams().filter(Q(organizations=instance) | Q(classes__organization=instance))
    instance._fp_lifecycle_scope = _scope(contests)


@receiver(post_delete, sender=Contest, dispatch_uid='fp_lifecycle_contest_postdelete')
@receiver(post_delete, sender=Class, dispatch_uid='fp_lifecycle_class_postdelete')
@receiver(post_delete, sender=Organization, dispatch_uid='fp_lifecycle_org_postdelete')
def after_delete(sender, instance, **kwargs):
    keys, classes, organizations = getattr(instance, '_fp_lifecycle_scope', ([], [], []))
    if sender is Class:
        classes = sorted(set(classes) | {instance.pk})
    elif sender is Organization:
        organizations = sorted(set(organizations) | {instance.pk})
    notify('removed' if sender is Contest else 'audience_revoked', keys, classes, organizations)


def contest_relation(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('pre_add', 'pre_remove', 'pre_clear', 'post_add', 'post_remove', 'post_clear'):
        return
    cache = getattr(instance, '_fp_lifecycle_relations', {})
    if action.startswith('pre_'):
        if not reverse:
            ids = [instance.pk]
        elif action == 'pre_clear':
            contest_field = next(f for f in sender._meta.fields if getattr(f, 'related_model', None) is Contest)
            other_field = next(f for f in sender._meta.fields if getattr(f, 'related_model', None) not in (None, Contest))
            ids = sender.objects.filter(**{other_field.attname: instance.pk}).values_list(contest_field.attname, flat=True)
        else:
            ids = pk_set or ()
        cache[sender._meta.label] = _scope(fairplay_exams().filter(pk__in=ids))
        instance._fp_lifecycle_relations = cache
    else:
        keys, classes, organizations = cache.pop(sender._meta.label, ([], [], []))
        notify('changed', keys, classes, organizations)


for field in ('classes', 'organizations', 'private_contestants', 'banned_users', 'authors', 'curators', 'testers', 'tags'):
    m2m_changed.connect(contest_relation, sender=getattr(Contest, field).through,
                        dispatch_uid='fp_lifecycle_contest_' + field)


def membership_relation(sender, instance, action, reverse, pk_set, **kwargs):
    if not action.startswith(('pre_', 'post_')):
        return
    if action.startswith('pre_'):
        fields = [f for f in sender._meta.fields if getattr(f, 'related_model', None)]
        scope_field = next(f for f in fields if f.related_model in (Class, Organization))
        profile_field = next(f for f in fields if f.related_model is Profile)
        if isinstance(instance, scope_field.related_model):
            ids = [instance.pk]
        elif action == 'pre_clear':
            ids = list(sender.objects.filter(**{profile_field.attname: instance.pk}).values_list(scope_field.attname, flat=True))
        else:
            ids = list(pk_set or ())
        query = Q(classes__pk__in=ids) if scope_field.related_model is Class else Q(organizations__pk__in=ids) | Q(classes__organization_id__in=ids)
        instance._fp_membership_scope = (_scope(fairplay_exams().filter(query)), scope_field.related_model, ids)
    else:
        (keys, classes, organizations), model, ids = getattr(instance, '_fp_membership_scope', (([], [], []), None, []))
        if model is Class:
            classes = sorted(set(classes) | set(ids))
        elif model is Organization:
            organizations = sorted(set(organizations) | set(ids))
        notify('changed', keys, classes, organizations)


for model, field in ((Class, 'members'), (Class, 'admins'), (Organization, 'admins'),
                     (Organization, 'instructors'), (Profile, 'organizations')):
    m2m_changed.connect(membership_relation, sender=getattr(model, field).through,
                        dispatch_uid='fp_lifecycle_membership_%s_%s' % (model.__name__, field))

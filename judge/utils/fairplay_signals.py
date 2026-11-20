"""Keeps the ``fairplay_hiding_active`` flag current (connected in ``JudgeAppConfig.ready``)."""
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from judge.models import Contest, ContestTag
from judge.utils.fairplay import FAIRPLAY_TAG, fairplay_tags_changed


@receiver(m2m_changed, sender=Contest.tags.through, dispatch_uid='fairplay_contest_tags_changed')
def contest_tags_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    if reverse:
        involved = isinstance(instance, ContestTag) and instance.name == FAIRPLAY_TAG
    else:
        involved = action == 'post_clear' or ContestTag.objects.filter(pk__in=pk_set or (), name=FAIRPLAY_TAG).exists()
    if involved:
        fairplay_tags_changed(added=action == 'post_add')
    if action == 'post_add' and involved:
        # Remember that this contest had the lock, so hand-ins already in a student's outbox still arrive after it
        # is switched off.
        if reverse:
            Contest.objects.filter(pk__in=pk_set or ()).update(had_fairplay=True)
        else:
            Contest.objects.filter(pk=instance.pk).update(had_fairplay=True)


@receiver(post_save, sender=ContestTag, dispatch_uid='fairplay_contest_tag_saved')
@receiver(post_delete, sender=ContestTag, dispatch_uid='fairplay_contest_tag_deleted')
@receiver(post_delete, sender=Contest, dispatch_uid='fairplay_contest_deleted')
def contest_tag_saved_or_deleted(sender, instance, **kwargs):
    # A renamed or deleted tag, or a deleted contest: the flag is counted again.
    fairplay_tags_changed(added=False)

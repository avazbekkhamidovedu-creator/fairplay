"""Tells FairPlay right after a FairPlay assignment is saved, changed or deleted (see ``JudgeAppConfig.ready``).

Nothing here reaches the UI: the ping runs after the commit in a daemon thread and every failure is only logged.
"""
import logging
import threading
from time import monotonic

from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver

from judge.models import Class, Contest, ContestProblem, ContestTag
from judge.utils.fairplay import FAIRPLAY_TAG, fairplay_enabled, fairplay_exams

__all__ = ['DEBOUNCE_SECONDS', 'PING_READ_TIMEOUT', 'request_sync']

logger = logging.getLogger('judge.fairplay')

DEBOUNCE_SECONDS = 3
# Connect + read: a ping never takes longer than 2 s.
PING_READ_TIMEOUT = 1
# Saves that change nothing FairPlay shows.
IGNORED_FIELDS = frozenset({
    'harvest_scheduled_task_id', 'ai_grading_scheduled_task_id', 'ai_grading_batch_started_at',
    'ai_grading_batch_finished_at', 'results_finalized',
})

_lock = threading.Lock()
_last_sent = {}
_trailing = set()


def _run_later(delay, function):
    thread = threading.Timer(delay, function) if delay > 0 else threading.Thread(target=function)
    thread.daemon = True
    thread.start()


def _send(exam_key, class_id):
    from judge.utils import fairplay_client
    try:
        fairplay_client.FairPlayClient().sync_ping(exam_key, class_id, read_timeout=PING_READ_TIMEOUT)
    except fairplay_client.FairPlayError as e:
        logger.warning('FairPlay sync ping failed (exam %s, class %s, status %s)', exam_key, class_id, e.status)
    except Exception as e:
        logger.warning('FairPlay sync ping failed (exam %s, class %s): %s', exam_key, class_id, e.__class__.__name__)


def _deliver(exam_key, class_id):
    """Sends at once, and once more after the window when more saves came in meanwhile."""
    key = (exam_key, class_id)
    with _lock:
        now = monotonic()
        last = _last_sent.get(key)
        if last is None or now - last >= DEBOUNCE_SECONDS:
            if len(_last_sent) > 1000:
                _last_sent.clear()
            _last_sent[key] = now
            delay = 0
        elif key in _trailing:
            return
        else:
            _trailing.add(key)
            delay = last + DEBOUNCE_SECONDS - now

    def run():
        if delay:
            with _lock:
                _trailing.discard(key)
                _last_sent[key] = monotonic()
        _send(exam_key, class_id)

    _run_later(delay, run)


def request_sync(exam_key=None, class_id=None):
    """Ping FairPlay once the surrounding transaction commits; one per transaction and target."""
    if not fairplay_enabled() or (exam_key is None and class_id is None):
        return
    target = (exam_key, class_id)
    connection = transaction.get_connection()
    if any(getattr(entry[1], 'fairplay_sync_target', None) == target for entry in connection.run_on_commit):
        return

    def after_commit():
        _deliver(exam_key, class_id)

    after_commit.fairplay_sync_target = target
    transaction.on_commit(after_commit)


def _locked_keys(contest_ids):
    return list(fairplay_exams().filter(pk__in=contest_ids).values_list('key', flat=True))


def _ping_locked(contest_ids):
    if not fairplay_enabled():
        return
    for key in _locked_keys(contest_ids):
        request_sync(key)


@receiver(post_save, sender=Contest, dispatch_uid='fairplay_sync_contest_saved')
def contest_saved(sender, instance, update_fields=None, **kwargs):
    if hasattr(instance, '_updating_stats_only') or not fairplay_enabled():
        return
    if update_fields is not None and set(update_fields) <= IGNORED_FIELDS:
        return
    _ping_locked([instance.pk])


@receiver(pre_delete, sender=Contest, dispatch_uid='fairplay_sync_contest_deleting')
def contest_deleting(sender, instance, **kwargs):
    # The tag rows are gone by post_delete, so the lock is read before.
    instance._fairplay_sync_locked = fairplay_enabled() and bool(_locked_keys([instance.pk]))


@receiver(post_delete, sender=Contest, dispatch_uid='fairplay_sync_contest_deleted')
def contest_deleted(sender, instance, **kwargs):
    if getattr(instance, '_fairplay_sync_locked', False):
        request_sync(instance.key)


@receiver(post_save, sender=ContestProblem, dispatch_uid='fairplay_sync_problem_saved')
@receiver(post_delete, sender=ContestProblem, dispatch_uid='fairplay_sync_problem_removed')
def contest_problem_changed(sender, instance, **kwargs):
    _ping_locked([instance.contest_id])


def _contest_ids(instance, reverse, pk_set):
    if not reverse:
        return [instance.pk]
    return list(pk_set or ())


def _contest_relation_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear') or not fairplay_enabled():
        return
    _ping_locked(_contest_ids(instance, reverse, pk_set))


for _field in ('classes', 'organizations', 'join_organizations', 'private_contestants', 'authors', 'curators',
               'testers'):
    m2m_changed.connect(_contest_relation_changed, sender=getattr(Contest, _field).through,
                        dispatch_uid='fairplay_sync_contest_' + _field)


@receiver(m2m_changed, sender=Contest.tags.through, dispatch_uid='fairplay_sync_contest_tags')
def contest_tags_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if not fairplay_enabled():
        return
    if action == 'pre_clear':
        # Clearing the tags can drop the lock; it is read before the rows are gone.
        if not reverse:
            _ping_locked([instance.pk])
        return
    if action not in ('post_add', 'post_remove'):
        return
    if reverse:
        if isinstance(instance, ContestTag) and instance.name == FAIRPLAY_TAG:
            for key in Contest.objects.filter(pk__in=pk_set or ()).values_list('key', flat=True):
                request_sync(key)
    elif ContestTag.objects.filter(pk__in=pk_set or (), name=FAIRPLAY_TAG).exists():
        # Also when the lock was taken off, so FairPlay can drop the exam.
        request_sync(instance.key)


@receiver(m2m_changed, sender=Class.members.through, dispatch_uid='fairplay_sync_class_members')
def class_members_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear') or not fairplay_enabled():
        return
    class_ids = list(pk_set or ()) if reverse else [instance.pk]
    if not class_ids:
        return
    used = Contest.classes.through.objects.filter(class_id__in=class_ids, contest__tags__name=FAIRPLAY_TAG)
    for class_id in sorted(set(used.values_list('class_id', flat=True))):
        request_sync(class_id=class_id)

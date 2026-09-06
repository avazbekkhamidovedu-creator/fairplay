"""Celery tasks that collect saved answers when an assignment closes.

Collecting a whole class is deliberately split in two. The contest-level task only *finds* the
participations that still have drafts and queues one task each; the per-participation task does the
work. A single task that created every submission for a 200-student class would hold one worker for
the length of the class and lose the whole assignment if it died halfway.
"""
import logging

from celery import shared_task

from judge.models import AssignmentDraft, ContestParticipation
from judge.utils.assignment_harvest import harvest_participation

__all__ = ('harvest_contest_drafts', 'harvest_participation_drafts')

logger = logging.getLogger('judge.assignment_harvest')


@shared_task
def harvest_participation_drafts(participation_id, part=None):
    """Auto-submit one participation's remaining drafts. Idempotent."""
    participation = (
        ContestParticipation.objects
        .select_related('contest', 'user__user')
        .filter(pk=participation_id)
        .first()
    )
    if participation is None:
        return 0
    return len(harvest_participation(participation, part=part))


@shared_task
def harvest_contest_drafts(contest_key):
    """Queue a harvest for every participation in this assignment that still has a draft.

    Participations without drafts are never queued, so an assignment where everybody submitted by
    hand costs one query rather than one task per student.
    """
    participation_ids = list(
        AssignmentDraft.objects
        .filter(participation__contest__key=contest_key, harvested_at__isnull=True)
        .values_list('participation_id', flat=True)
        .distinct(),
    )
    for participation_id in participation_ids:
        harvest_participation_drafts.delay(participation_id)
    return len(participation_ids)

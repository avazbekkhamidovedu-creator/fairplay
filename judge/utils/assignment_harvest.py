"""Turn the drafts a participation still has into real submissions.

This is the "collect the papers" step. It is driven by the draft table rather than by the problem
list, because a successful manual submit deletes its draft: a draft that is still there means the
problem was never submitted, or was submitted and then edited. Those are exactly the two cases a
student loses marks on today, and nothing else has to be compared or guessed at.

Harvesting can be triggered more than once for the same participation — a deadline task and the
recovery sweep can overlap, and finishing a part happens right next to the deadline — so each
draft is claimed with a compare-and-set on ``harvested_at`` before a submission is created. A
claim that fails to produce a submission is released again, because a duplicate submission is a
far smaller problem than an answer that silently disappeared.
"""
import logging

from django.db.models import Count
from django.utils import timezone

from judge.models import AssignmentDraft, ContestProblem, ContestSubmission, Language, ProblemSubmissionType
from judge.utils.assignment_draft import DraftContentError, draft_answer
from judge.utils.contest_parts import PART_CODE, user_bypasses_part_lock
from judge.utils.submission_create import create_submission

__all__ = ['harvest_participation', 'participation_is_harvestable']

logger = logging.getLogger('judge.assignment_harvest')


def participation_is_harvestable(participation):
    """Whether this participation's work counts towards a grade at all."""
    if participation.spectate:
        return False
    if participation.is_disqualified:
        return False
    # Staff who read the assignment with bypass rights are not sitting it.
    return not user_bypasses_part_lock(participation.user.user, participation.contest)


def _claim(draft, now):
    """Mark ``draft`` harvested, but only if nobody else already did. True if we won the claim."""
    return AssignmentDraft.objects.filter(pk=draft.pk, harvested_at__isnull=True).update(harvested_at=now) == 1


def _release(draft):
    AssignmentDraft.objects.filter(pk=draft.pk).update(harvested_at=None)


def harvest_participation(participation, *, part=None):
    """Submit every substantive draft ``participation`` still holds; return the submissions made.

    Pass ``part`` (``PART_CODE`` or ``PART_NON_CODE``) to collect only that half of a
    split-part assignment, leaving the other half for its own deadline.
    """
    if not participation_is_harvestable(participation):
        return []

    drafts = list(
        AssignmentDraft.objects
        .filter(participation=participation, harvested_at__isnull=True)
        .select_related('problem', 'language'),
    )
    if not drafts:
        return []

    contest_problems = {
        contest_problem.problem_id: contest_problem
        for contest_problem in ContestProblem.objects.filter(contest_id=participation.contest_id)
    }
    used = dict(
        ContestSubmission.objects
        .filter(participation=participation)
        .exclude(submission__status='IE')
        .values_list('problem_id')
        .annotate(Count('id')),
    )

    now = timezone.now()
    created = []
    for draft in drafts:
        contest_problem = contest_problems.get(draft.problem_id)
        if contest_problem is None:
            # The problem was removed from the assignment after the student answered it.
            continue
        if part is not None and (draft.problem.submission_type == ProblemSubmissionType.CODE) != (part == PART_CODE):
            continue
        if not draft.has_content():
            continue
        max_submissions = contest_problem.max_submissions
        if max_submissions and used.get(contest_problem.id, 0) >= max_submissions:
            continue

        if not _claim(draft, now):
            continue
        try:
            answer = draft_answer(draft)
        except DraftContentError:
            logger.exception('Unusable draft %d for participation %d', draft.pk, participation.pk)
            _release(draft)
            continue

        is_code = draft.problem.submission_type == ProblemSubmissionType.CODE
        try:
            submission = create_submission(
                participation.user, draft.problem,
                language=draft.language if is_code else Language.get_answer_language(),
                answer=answer,
                participation=participation, contest_problem=contest_problem,
            )
        except Exception:
            logger.exception('Auto-submit failed for draft %d, participation %d', draft.pk, participation.pk)
            _release(draft)
            continue

        # The submission is now the record of what the student wrote, so the draft body is no
        # longer needed; the row stays as evidence of when the answer was collected.
        AssignmentDraft.objects.filter(pk=draft.pk).update(content='')
        used[contest_problem.id] = used.get(contest_problem.id, 0) + 1
        created.append(submission)

    return created

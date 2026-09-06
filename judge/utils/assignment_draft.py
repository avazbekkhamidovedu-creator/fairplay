"""Normalizing and reading back assignment draft content.

Draft content arrives from the student's browser and is later fed to a grader by auto-submit, with
nobody watching in between. So it is normalized into the exact shape the matching
``QuestionType.store_answer`` reads on the way in, and converted back to cleaned-data form on the
way out. Anything that does not fit is rejected at save time rather than becoming a broken
submission at the deadline.
"""
import json

from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.utils.translation import gettext as _

from judge.models import ProblemSubmissionType

__all__ = ['DraftContentError', 'normalize_draft_content', 'draft_answer']

# Matches SubmissionSource.source and AssignmentDraft.content.
MAX_DRAFT_LENGTH = 65536


class DraftContentError(ValueError):
    """The submitted draft content does not fit the problem's question type."""


def _loads(raw):
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw or '{}')
    except (TypeError, ValueError):
        raise DraftContentError(_('Malformed draft content.'))
    if not isinstance(payload, dict):
        raise DraftContentError(_('Malformed draft content.'))
    return payload


def _normalize_mcq(problem, raw):
    payload = _loads(raw)
    selected = payload.get('selected')
    if selected is None:
        selected = []
    if not isinstance(selected, list):
        raise DraftContentError(_('Malformed draft content.'))
    try:
        ids = {int(value) for value in selected}
    except (TypeError, ValueError):
        raise DraftContentError(_('Malformed draft content.'))
    # Drop ids that are not options of this problem: they cannot be graded and would only make the
    # draft look non-empty.
    valid = set(problem.mcq_options.values_list('id', flat=True))
    return json.dumps({'selected': sorted(ids & valid)})


def _normalize_fill_blank(problem, raw):
    payload = _loads(raw)
    answers = payload.get('answers')
    if answers is None:
        answers = []
    if not isinstance(answers, list):
        raise DraftContentError(_('Malformed draft content.'))
    blank_count = problem.blanks.count()
    answers = [('' if answer is None else str(answer)) for answer in answers[:blank_count]]
    answers += [''] * (blank_count - len(answers))
    return json.dumps({'answers': answers})


def _normalize_short_answer(problem, raw):
    payload = _loads(raw)
    text = payload.get('text')
    if text is None:
        text = ''
    if not isinstance(text, str):
        raise DraftContentError(_('Malformed draft content.'))
    return json.dumps({'text': text})


def normalize_draft_content(problem, raw):
    """Return ``raw`` as the canonical draft content string for ``problem``.

    Raises ``DraftContentError`` for content that does not belong to this question type or is too
    long to ever become a submission.
    """
    submission_type = problem.submission_type
    if submission_type == ProblemSubmissionType.CODE:
        content = '' if raw is None else str(raw)
    elif submission_type == ProblemSubmissionType.MCQ:
        content = _normalize_mcq(problem, raw)
    elif submission_type == ProblemSubmissionType.FILL_BLANK:
        content = _normalize_fill_blank(problem, raw)
    elif submission_type == ProblemSubmissionType.SHORT_ANSWER:
        content = _normalize_short_answer(problem, raw)
    elif submission_type == ProblemSubmissionType.FILE:
        # The answer is the uploaded object; this field carries nothing.
        content = ''
    else:
        raise DraftContentError(_('Drafts are not supported for this question type.'))

    if len(content) > MAX_DRAFT_LENGTH:
        raise DraftContentError(_('Draft is too long to save.'))
    return content


def draft_answer(draft):
    """The draft's content as the cleaned-data ``create_submission`` expects for its problem."""
    submission_type = draft.problem.submission_type
    if submission_type == ProblemSubmissionType.CODE:
        return draft.content
    payload = _loads(draft.content or '{}')
    if submission_type == ProblemSubmissionType.MCQ:
        return {'selected': payload.get('selected') or []}
    if submission_type == ProblemSubmissionType.FILL_BLANK:
        return {'answers': payload.get('answers') or []}
    if submission_type == ProblemSubmissionType.SHORT_ANSWER:
        # store_answer reads 'answer'; the stored payload mirrors SubmissionSource, which uses 'text'.
        return {'answer': payload.get('text') or ''}
    if submission_type == ProblemSubmissionType.FILE:
        try:
            upload = draft.upload
        except ObjectDoesNotExist:
            raise DraftContentError(_('Draft has no uploaded file.'))
        # A plain File, never the FieldFile itself. Assigning one model's FieldFile to another
        # model's FileField re-points the new row at the *same* object instead of copying it, so
        # deleting the draft afterwards would take the submission's file with it. Naming it after
        # the student's own filename is also what gets that name onto the graded submission.
        #
        # Opened first: an unopened FieldFile reports itself closed, which makes the wrapper
        # report itself unseekable, and the storage refuses content it cannot rewind and hash.
        upload.file.open('rb')
        return {'file': File(upload.file, name=upload.original_name)}
    raise DraftContentError(_('Drafts are not supported for this question type.'))

"""Server-side answer drafts for assignment participants.

Students answer assignment problems in the workspace, but nothing reaches the server until they
press Submit. Anything they typed and did not submit used to live only in ``localStorage``, so it
was lost when the assignment closed. A draft mirrors the in-progress answer on the server so the
auto-submit that runs when the assignment closes has something to submit.

A draft is deliberately *not* a ``Submission``: it must stay out of submission lists, scoreboards,
Grade pages and exports. Keeping it in its own table makes that structural rather than a filter
every query has to remember.

Successfully submitting a problem deletes its draft, so a surviving draft means exactly one of two
things: the problem was never submitted, or it was submitted and then edited again.
"""
import json

from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.contest import ContestParticipation
from judge.models.problem import Problem, ProblemSubmissionType, ProblemTemplate
from judge.models.runtime import Language

__all__ = ['AssignmentDraft']


def _json_payload(content):
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class AssignmentDraft(models.Model):
    """The latest unsubmitted answer a participant has for one assignment problem."""

    participation = models.ForeignKey(ContestParticipation, verbose_name=_('participation'),
                                      related_name='drafts', on_delete=CASCADE)
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='assignment_drafts',
                                on_delete=CASCADE)
    # Serialized the same way the matching question type writes SubmissionSource, so harvesting can
    # hand the payload straight to the grader: raw source for code, {"selected": [...]} for
    # multiple choice, {"answers": [...]} for fill-in-the-blank, {"text": "..."} for short answer.
    content = models.TextField(verbose_name=_('draft content'), max_length=65536, blank=True)
    language = models.ForeignKey(Language, verbose_name=_('language'), null=True, blank=True,
                                 on_delete=models.SET_NULL)
    updated_on = models.DateTimeField(verbose_name=_('last saved'), auto_now=True)
    harvested_at = models.DateTimeField(verbose_name=_('harvested on'), null=True, blank=True,
                                        help_text=_('When auto-submit turned this draft into a submission.'))

    def __str__(self):
        return _('Draft of %(user)s for %(problem)s') % {
            'user': self.participation.user.username,
            'problem': self.problem.code,
        }

    def has_content(self):
        """Whether this draft holds a real answer worth submitting.

        Blank answers and untouched starter code are not answers: auto-submitting them would file
        empty work on the student's behalf and, for a whole class at once, put it through the judge.
        """
        submission_type = self.problem.submission_type
        if submission_type == ProblemSubmissionType.CODE:
            source = (self.content or '').strip()
            if not source:
                return False
            if self.language_id is None:
                return False
            starter = ProblemTemplate.starter_code(self.language, self.problem_id)
            return source != (starter or '').strip()
        if submission_type == ProblemSubmissionType.MCQ:
            payload = _json_payload(self.content)
            return bool(payload and payload.get('selected'))
        if submission_type == ProblemSubmissionType.FILL_BLANK:
            payload = _json_payload(self.content)
            answers = (payload or {}).get('answers') or []
            return any(str(answer).strip() for answer in answers)
        if submission_type == ProblemSubmissionType.SHORT_ANSWER:
            payload = _json_payload(self.content)
            return bool(str((payload or {}).get('text') or '').strip())
        return bool((self.content or '').strip())

    class Meta:
        unique_together = ('participation', 'problem')
        indexes = [
            # Harvesting scans unharvested drafts for one contest, so lead with the flag.
            models.Index(fields=['harvested_at', 'participation']),
        ]
        verbose_name = _('assignment draft')
        verbose_name_plural = _('assignment drafts')

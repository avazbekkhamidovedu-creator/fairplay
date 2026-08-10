"""The default code question type — a thin wrapper over today's behavior.

Code problems still flow through ``ProblemSubmit`` → ``Submission.judge()`` → the judge bridge,
so this type only contributes authoring/menu metadata; its grading/submit hooks are never
invoked by the internal path.
"""
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models import ProblemSubmissionType
from judge.question_types.base import QuestionType
from judge.question_types.registry import register


@register
class CodeQuestionType(QuestionType):
    submission_type = ProblemSubmissionType.CODE
    label = _('Programming')
    routes_to_bridge = True
    order = 10

    def can_create(self, user):
        # Code problems are authored through Django admin (unchanged).
        return user.is_staff and user.has_perm('judge.add_problem')

    def create_url(self):
        return reverse('admin:judge_problem_add')

    def edit_url(self, problem):
        return reverse('admin:judge_problem_change', args=(problem.id,))

"""Detect a lost update between two editors of the same question or assignment.

The problem editor (``/problems/edit/<code>/``) and the assignment editor
(``/contests/<key>/edit/``) send the whole form on Save. Without a check, the
second of two teachers (or two tabs) who opened the same item silently
overwrites the first one's changes.

The edit page embeds a fingerprint of everything the editor manages, taken when
the page was rendered (``edit_base``). On save the view locks the row
(``SELECT ... FOR UPDATE``, which also serializes concurrent saves of the same
item), recomputes the fingerprint from the database and refuses the save with
HTTP 409 when it differs: someone else saved in between. Columns that other
code updates on its own (solve counts, user counts, finalize state, scheduled
task ids) are left out, so a student joining or a submission being judged never
looks like an edit.
"""
import hashlib
import json

from django.utils.translation import gettext as _

PROBLEM_VOLATILE_FIELDS = frozenset(('user_count', 'ac_rate'))
CONTEST_VOLATILE_FIELDS = frozenset((
    'user_count', 'results_finalized', 'ai_grading_scheduled_task_id', 'ai_grading_batch_started_at',
    'ai_grading_batch_finished_at', 'harvest_scheduled_task_id',
))


class EditConflict(Exception):
    """The item changed after the editor page was opened."""


def problem_conflict_message():
    return _('Someone else (or another tab) saved this question after you opened it. Your changes were not '
             'saved. Copy anything you want to keep, reload the page to see the latest version, and make '
             'your changes again.')


def contest_conflict_message():
    return _('Someone else (or another tab) saved this assignment after you opened it. Your changes were not '
             'saved. Copy anything you want to keep, reload the page to see the latest version, and make '
             'your changes again.')


def _rows(queryset, exclude=()):
    fields = [f.attname for f in queryset.model._meta.concrete_fields if f.attname not in exclude]
    return list(queryset.order_by('pk').values_list(*fields))


def _m2m_ids(instance, exclude=()):
    return {field.name: sorted(getattr(instance, field.name).values_list('pk', flat=True))
            for field in instance._meta.many_to_many if field.name not in exclude}


def _digest(parts):
    text = json.dumps(parts, default=str, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:40]


def problem_edit_fingerprint(problem):
    """Fingerprint of a problem as the editor sees it, read fresh from the database."""
    from judge.models import (LanguageLimit, Problem, ProblemBlank, ProblemClarification, ProblemCodeConfig,
                              ProblemData, ProblemFileConfig, ProblemFillBlankConfig, ProblemMCQConfig,
                              ProblemMCQOption, ProblemShortAnswerConfig, ProblemTemplate, ProblemTestCase,
                              ProblemTranslation, Solution)
    from judge.models.problem_data import ProblemHarness

    pk = problem.pk
    parts = {
        'problem': _rows(Problem.objects.filter(pk=pk), PROBLEM_VOLATILE_FIELDS),
        'm2m': _m2m_ids(problem),
    }
    for name, model, field in (
        ('options', ProblemMCQOption, 'problem'), ('mcq', ProblemMCQConfig, 'problem'),
        ('blanks', ProblemBlank, 'problem'), ('fill', ProblemFillBlankConfig, 'problem'),
        ('short', ProblemShortAnswerConfig, 'problem'), ('code', ProblemCodeConfig, 'problem'),
        ('file', ProblemFileConfig, 'problem'), ('data', ProblemData, 'problem'),
        ('cases', ProblemTestCase, 'dataset'), ('harness', ProblemHarness, 'problem'),
        ('translations', ProblemTranslation, 'problem'), ('limits', LanguageLimit, 'problem'),
        ('templates', ProblemTemplate, 'problem'), ('clarifications', ProblemClarification, 'problem'),
    ):
        parts[name] = _rows(model.objects.filter(**{field: pk}))
    solution = Solution.objects.filter(problem=pk).first()
    parts['solution'] = _rows(Solution.objects.filter(problem=pk))
    parts['solution_authors'] = sorted(solution.authors.values_list('pk', flat=True)) if solution else []
    return _digest(parts)


def contest_edit_fingerprint(contest):
    """Fingerprint of an assignment as the editor sees it, read fresh from the database."""
    from judge.models import Contest, ContestProblem

    return _digest({
        'contest': _rows(Contest.objects.filter(pk=contest.pk), CONTEST_VOLATILE_FIELDS),
        'm2m': _m2m_ids(contest, exclude=('problems',)),
        'problems': _rows(ContestProblem.objects.filter(contest=contest.pk)),
    })


def check_edit_base(submitted, current_fingerprint):
    """Raise ``EditConflict`` when the client sent a base and it is stale.

    Clients that send no ``edit_base`` (API callers, older pages) keep the old
    last-write-wins behaviour, but still get the row lock.
    """
    if submitted and str(submitted) != current_fingerprint():
        raise EditConflict()

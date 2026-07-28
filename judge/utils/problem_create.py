"""Helpers for creating problems with sensible defaults for the code-centric required fields.

Mirrors the "simple preset" defaulting used by the admin's ``SimpleProblemCreationForm`` so that
front-end authoring of non-code question types (e.g. multiple-choice) doesn't make teachers fill
in time/memory limits, languages, or a problem code they don't care about.
"""
from django.conf import settings
from django.utils import timezone

from judge.models import Problem, ProblemGroup, ProblemType


def generate_problem_code():
    existing_codes = set(Problem.objects.values_list('code', flat=True))
    for index in range(1000000):
        code = 'p{:06d}'.format(index)
        if code not in existing_codes:
            return code
    return 'mcq{}'.format(str(int(timezone.now().timestamp()))[-6:])


def default_problem_group():
    group = ProblemGroup.objects.order_by('full_name', 'name').first()
    if group is None:
        group = ProblemGroup.objects.create(name='uncategorized', full_name='Uncategorized')
    return group


def default_problem_type():
    ptype = ProblemType.objects.order_by('full_name', 'name').first()
    if ptype is None:
        ptype = ProblemType.objects.create(name='uncategorized', full_name='Uncategorized')
    return ptype


def apply_simple_problem_defaults(problem):
    """Fill the code-centric required scalar fields a non-code problem doesn't use.

    M2M defaults (``types``) must be assigned by the caller after the problem is saved.
    """
    if not problem.code:
        problem.code = generate_problem_code()
    if problem.time_limit is None:
        problem.time_limit = min(max(1, settings.DMOJ_PROBLEM_MIN_TIME_LIMIT), settings.DMOJ_PROBLEM_MAX_TIME_LIMIT)
    if not problem.memory_limit:
        problem.memory_limit = min(max(262144, settings.DMOJ_PROBLEM_MIN_MEMORY_LIMIT),
                                   settings.DMOJ_PROBLEM_MAX_MEMORY_LIMIT)
    if problem.group_id is None:
        problem.group = default_problem_group()

"""Phased non-programming / programming parts on an assignment.

When ``Contest.lock_non_code_part`` is on and the assignment has both kinds of
questions, students choose which part to start. The other part stays closed.
After they finish the first part they cannot return to it. The site IDE is
available only while they are working on programming problems.
"""
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.models import ContestParticipation, ProblemSubmissionType
from judge.utils.views import generic_message

PART_OPEN = 'open'
PART_CODE_LOCKED = 'code_locked'
PART_NON_CODE_LOCKED = 'non_code_locked'
PART_CHOOSE_REQUIRED = 'choose_required'

PART_NON_CODE = 'non_code'
PART_CODE = 'code'
PART_CHOICES = (PART_NON_CODE, PART_CODE)


def is_code_problem(problem):
    if isinstance(problem, dict):
        submission_type = problem.get('submission_type')
    else:
        submission_type = getattr(problem, 'submission_type', None)
    return submission_type == ProblemSubmissionType.CODE


def user_bypasses_part_lock(user, contest):
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if contest.is_editable_by(user):
        return True
    profile_id = user.profile.id
    if profile_id in contest.editor_ids or profile_id in contest.tester_ids or profile_id in contest.spectator_ids:
        return True
    if contest.is_viewable_by_org_admin(user) or contest.is_viewable_by_course_teacher(user):
        return True
    return False


def contest_has_non_code_problems(contest):
    return contest.contest_problems.exclude(problem__submission_type=ProblemSubmissionType.CODE).exists()


def contest_has_code_problems(contest):
    return contest.contest_problems.filter(problem__submission_type=ProblemSubmissionType.CODE).exists()


def part_lock_applies(contest):
    return (
        bool(contest.lock_non_code_part)
        and contest_has_non_code_problems(contest)
        and contest_has_code_problems(contest)
    )


def part_lock_ui_state(contest, participation, user):
    """Template-facing part-lock flags for assignment and problem pages."""
    applies = part_lock_applies(contest)
    bypass = user_bypasses_part_lock(user, contest)
    spectator = bool(participation and participation.spectate)
    chosen = chosen_part(participation) if participation and not bypass else ''
    current = current_part(participation) if participation and not bypass else ''
    done = bool(participation and first_part_done(participation))
    return {
        'enabled': applies,
        'needs_choice': bool(applies and participation and not chosen and not bypass and not spectator),
        'chosen': chosen,
        'current': current,
        'done': done,
        'can_finish': bool(applies and participation and chosen and not done and not bypass and not spectator),
        'finish_confirm': finish_confirm_message(participation) if participation else '',
        'show_parts': applies,
        'bypass': bypass,
    }


def chosen_part(participation):
    value = getattr(participation, 'chosen_part', '') or '' if participation is not None else ''
    return value if value in PART_CHOICES else ''


def first_part_done(participation):
    part = chosen_part(participation)
    if not part:
        return False
    if part == PART_CODE:
        return participation.code_part_completed_at is not None
    return participation.non_code_part_completed_at is not None


def current_part(participation):
    """Part the student may work on now, or ``''`` if they still have to choose."""
    if participation is None or not part_lock_applies(participation.contest):
        return ''
    part = chosen_part(participation)
    if not part:
        return ''
    if not first_part_done(participation):
        return part
    return PART_CODE if part == PART_NON_CODE else PART_NON_CODE


def blocking_participations_for_profile(profile):
    """Participations that should keep the site IDE closed."""
    if profile is None:
        return []
    now = timezone.now()
    rows = (
        ContestParticipation.objects
        .filter(user=profile, contest__lock_non_code_part=True)
        .exclude(virtual=ContestParticipation.SPECTATE)
        .select_related('contest')
    )
    blocked = []
    for participation in rows:
        contest = participation.contest
        if participation.ended or contest.start_time > now:
            continue
        if not contest_has_non_code_problems(contest):
            continue
        if current_part(participation) == PART_CODE:
            continue
        blocked.append(participation)
    return blocked


def ide_blocked_for_profile(profile, user=None):
    if profile is None:
        return False
    if user is None:
        user = getattr(profile, 'user', None)
    for participation in blocking_participations_for_profile(profile):
        if user_bypasses_part_lock(user, participation.contest):
            continue
        return True
    return False


def problem_part_state(participation, problem, user):
    """``PART_*`` for this student and problem, or ``PART_OPEN`` if unlocked."""
    if participation is None:
        return PART_OPEN
    contest = participation.contest
    if not part_lock_applies(contest):
        return PART_OPEN
    if user_bypasses_part_lock(user, contest) or participation.spectate:
        return PART_OPEN
    current = current_part(participation)
    code = is_code_problem(problem)
    if not current:
        return PART_CHOOSE_REQUIRED
    if current == PART_CODE:
        return PART_OPEN if code else PART_NON_CODE_LOCKED
    return PART_OPEN if not code else PART_CODE_LOCKED


def problem_in_participation_contest(participation, problem):
    if participation is None or problem is None:
        return False
    return participation.contest.contest_problems.filter(problem_id=problem.id).exists()


def problem_part_lock_response(request, problem):
    """403 page if this problem is in the locked part; otherwise None."""
    participation = getattr(request, 'participation', None)
    if participation is None and getattr(request, 'user', None) is not None and request.user.is_authenticated:
        participation = getattr(request.profile, 'current_contest', None)
    if not problem_in_participation_contest(participation, problem):
        return None
    state = problem_part_state(participation, problem, request.user)
    if state == PART_CHOOSE_REQUIRED:
        return generic_message(
            request,
            _('Choose a part first'),
            _('Choose which part to start on the Problems page.'),
            status=403,
        )
    if state == PART_CODE_LOCKED:
        return generic_message(
            request,
            _('Programming problems locked'),
            _('Programming problems are locked while you work on the other part.'),
            status=403,
        )
    if state == PART_NON_CODE_LOCKED:
        return generic_message(
            request,
            _('This part is locked'),
            _('Non-programming questions are locked while you work on the other part.'),
            status=403,
        )
    return None


def ide_blocked_response(request, *, json_mode=False):
    if not ide_blocked_for_profile(getattr(request, 'profile', None), getattr(request, 'user', None)):
        return None
    message = _('The site IDE is available only while you are working on programming problems.')
    if json_mode:
        from django.http import JsonResponse
        return JsonResponse({'error': message}, status=403)
    return generic_message(request, _('IDE unavailable'), message, status=403)


def first_accessible_contest_problem(participation, user):
    if participation is None:
        return None
    for contest_problem in (
        participation.contest.contest_problems
        .select_related('problem')
        .order_by('order')
    ):
        if problem_part_state(participation, contest_problem.problem, user) == PART_OPEN:
            return contest_problem
    return None


def finish_confirm_message(participation):
    if current_part(participation) == PART_CODE:
        return _('Your saved answers for the programming problems will be submitted now. '
                 'You cannot return to them, and the site IDE will close. '
                 'Continue to the non-programming questions?')
    return _('Your saved answers for the non-programming questions will be submitted now. '
             'You cannot return to them. Continue to the programming problems?')

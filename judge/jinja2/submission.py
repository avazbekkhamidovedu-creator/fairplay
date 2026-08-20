from . import registry


@registry.function
def submission_layout(submission, profile_id, user, completed_problem_ids, editable_problem_ids, tester_problem_ids):
    can_edit = False
    if (user.has_perm('judge.edit_all_problem') or
            (user.has_perm('judge.edit_public_problem') and submission.problem.is_public) or
            # We try to avoid evaluating this as much as possible to keep it lazy.
            submission.problem_id in editable_problem_ids):
        can_edit = True

    # Align the list "view" affordance with Submission.can_see_detail so contest
    # rows never show a link that would 403 (practice SOLVED/ALWAYS stay as-is).
    can_view = can_edit or submission.can_see_detail(user)
    return can_view, can_edit

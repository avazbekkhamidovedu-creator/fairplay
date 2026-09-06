"""Save and clear a student's server-side draft for one assignment problem.

The workspace calls this while the student types, so that what they have written survives a closed
tab, a dead laptop or a different machine — and so the deadline sweep has something to submit for
students who never pressed Submit. Every rejection here is a draft that will *not* be
auto-submitted, so the guards mirror the ones on the submit path rather than inventing their own.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.views.generic import View

from judge.models import AssignmentDraft, Language, Problem
from judge.utils.assignment_draft import DraftContentError, normalize_draft_content
from judge.utils.contest_parts import PART_OPEN, problem_part_state, user_bypasses_part_lock


class AssignmentDraftView(LoginRequiredMixin, View):
    """POST saves the current answer as this participation's draft; DELETE drops it."""

    http_method_names = ['post', 'delete']

    def _error(self, message, status):
        return JsonResponse({'error': message}, status=status)

    def _resolve(self):
        """Return ``(participation, problem)``, or an error response if drafts do not apply.

        Authorization is the participation itself: the caller can only ever reach their own
        ``current_contest``, and being joined is what grants access to the assignment's problems.
        """
        participation = self.request.profile.current_contest
        if participation is None or participation.contest.key != self.kwargs['contest']:
            return self._error(_('Join the assignment before saving answers.'), 403)
        contest = participation.contest
        # Spectators and staff who bypass assignment rules are not being graded, so a draft of
        # theirs would only give auto-submit work that must not be submitted.
        if participation.spectate or user_bypasses_part_lock(self.request.user, contest):
            return self._error(_('Answers are not saved in this mode.'), 403)
        if participation.ended:
            # The window is closed and auto-submit has already run or is about to: accepting a
            # write now would either be lost or resurrect a draft that was just harvested.
            return self._error(_('This assignment is closed.'), 403)

        problem = get_object_or_404(Problem, code=self.kwargs['problem'])
        if not contest.contest_problems.filter(problem_id=problem.id).exists():
            return self._error(_('This problem is not part of this assignment.'), 404)
        if problem_part_state(participation, problem, self.request.user) != PART_OPEN:
            return self._error(_('This part is locked.'), 403)
        return participation, problem

    def post(self, request, *args, **kwargs):
        resolved = self._resolve()
        if isinstance(resolved, JsonResponse):
            return resolved
        participation, problem = resolved

        try:
            content = normalize_draft_content(problem, request.POST.get('content', ''))
        except DraftContentError as error:
            return self._error(str(error), 400)

        language = None
        language_id = request.POST.get('language')
        if language_id:
            language = Language.objects.filter(id=language_id).first()
            if language is None or not problem.allowed_languages.filter(id=language.id).exists():
                return self._error(_('That language is not allowed for this problem.'), 400)

        draft, _created = AssignmentDraft.objects.update_or_create(
            participation=participation, problem=problem,
            # An edit after a harvest is a new answer, so it becomes harvestable again.
            defaults={'content': content, 'language': language, 'harvested_at': None},
        )
        return JsonResponse({'saved': True, 'updated_on': draft.updated_on.isoformat()})

    def delete(self, request, *args, **kwargs):
        resolved = self._resolve()
        if isinstance(resolved, JsonResponse):
            return resolved
        participation, problem = resolved
        AssignmentDraft.objects.filter(participation=participation, problem=problem).delete()
        return JsonResponse({'saved': False})

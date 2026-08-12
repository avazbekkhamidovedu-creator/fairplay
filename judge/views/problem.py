import json
import logging
import os
import re
import shutil
import threading
from datetime import timedelta
from operator import itemgetter
from random import randrange
from statistics import mean, median

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import BooleanField, Case, CharField, Count, F, FilteredRelation, Max, Prefetch, Q, When
from django.db.models.functions import Coalesce
from django.db.utils import ProgrammingError
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import get_template, render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.functional import cached_property
from django.utils.html import escape, format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import DetailView, ListView, View
from django.views.generic.detail import SingleObjectMixin
from reversion import revisions

from judge import event_poster as event
from judge.comments import CommentedDetailView, SubmissionCommentMixin
from judge.forms import ProblemCloneForm, ProblemPointsVoteForm, ProblemSubmitForm
from judge.models import (
    ContestProblem,
    ContestSubmission,
    Judge,
    Language,
    Organization,
    Problem,
    ProblemData,
    ProblemGroup,
    ProblemHarness,
    ProblemPointsVote,
    ProblemTemplate,
    ProblemTestCase,
    ProblemTranslation,
    ProblemType,
    RuntimeVersion,
    Solution,
    Submission,
    SubmissionSource,
    problem_data_storage,
)
from judge.utils.diggpaginator import DiggPaginator
from judge.utils.opengraph import generate_opengraph
from judge.utils.pdfoid import PDF_RENDERING_ENABLED, render_pdf
from judge.utils.problems import (
    contest_attempted_ids,
    contest_completed_ids,
    hot_problems,
    user_attempted_ids,
    user_completed_ids,
    user_editable_ids,
    user_tester_ids,
)
from judge.utils.strings import safe_float_or_none, safe_int_or_none
from judge.utils.tickets import own_ticket_filter
from judge.utils.views import QueryStringSortMixin, SingleObjectFormView, TitleMixin, add_file_response, generic_message

recjk = re.compile(
    r'[\u2E80-\u2E99\u2E9B-\u2EF3\u2F00-\u2FD5\u3005\u3007\u3021-\u3029\u3038-\u303A\u303B\u3400-\u4DB5'
    r'\u4E00-\u9FC3\uF900-\uFA2D\uFA30-\uFA6A\uFA70-\uFAD9\U00020000-\U0002A6D6\U0002F800-\U0002FA1D]',
)


def get_contest_problem(problem, profile):
    if profile is None or profile.current_contest is None:
        return None
    try:
        return problem.contests.get(contest_id=profile.current_contest.contest_id)
    except ObjectDoesNotExist:
        return None


def get_contest_submission_count(problem, profile, virtual):
    return (
        profile.current_contest.submissions.exclude(submission__status__in=['IE'])
        .filter(problem__problem=problem, participation__virtual=virtual)
        .count()
    )


def workspace_display_points(submission, request):
    """Score shown in Workspace: assignment uses ContestSubmission.points, else problem scale."""
    if getattr(request, 'in_contest', False):
        contest_sub = submission.contest_or_none
        if contest_sub is not None:
            return contest_sub.points
    return submission.points


def get_workspace_answer_display(submission):
    """Human-readable answer rows for non-code Result panels (MCQ / fill-blank / short answer)."""
    from judge.question_types import get_grader

    grader = get_grader(submission.problem.submission_type)
    if grader.routes_to_bridge:
        return []
    try:
        raw = submission.source.source
    except ObjectDoesNotExist:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return [{'label': '', 'value': raw}] if raw else []

    if submission.problem.submission_type == 'M':
        options = {
            opt.id: opt
            for opt in submission.problem.mcq_options.all()
        }
        rows = []
        # Prefer submit-time snapshot (survives option recreate on edit).
        snapshot = payload.get('choices') if isinstance(payload, dict) else None
        if snapshot:
            for choice in snapshot:
                text = (choice.get('text') or '').strip()
                order = choice.get('order')
                opt_id = choice.get('id')
                live = options.get(int(opt_id)) if opt_id is not None and str(opt_id).isdigit() else None
                if not text and live is not None:
                    text = live.text
                    order = live.order
                if order is None and live is not None:
                    order = live.order
                letter = chr(ord('A') + order) if isinstance(order, int) else ''
                prefix = ('%s. ' % letter) if letter else ''
                if text:
                    rows.append({'label': '', 'value': '%s%s' % (prefix, text)})
                else:
                    rows.append({'label': '', 'value': _('(option no longer available)')})
            return rows or [{'label': '', 'value': _('(no option selected)')}]

        selected = payload.get('selected') or [] if isinstance(payload, dict) else []
        for opt_id in selected:
            opt = options.get(int(opt_id)) if str(opt_id).isdigit() or isinstance(opt_id, int) else None
            if opt is None:
                # Stale id after options were rewritten — do not show bare database ids.
                rows.append({'label': '', 'value': _('(option no longer available)')})
            else:
                letter = chr(ord('A') + opt.order) if opt.order is not None else ''
                label = ('%s. ' % letter) if letter else ''
                rows.append({'label': '', 'value': '%s%s' % (label, opt.text)})
        return rows or [{'label': '', 'value': _('(no option selected)')}]

    if submission.problem.submission_type == 'B':
        answers = payload.get('answers') or []
        blanks = list(submission.problem.blanks.order_by('order'))
        rows = []
        for idx, blank in enumerate(blanks):
            value = answers[idx] if idx < len(answers) else ''
            rows.append({
                'label': _('Blank %(n)d') % {'n': idx + 1},
                'value': value if value != '' else _('(empty)'),
            })
        if not rows and answers:
            for idx, value in enumerate(answers):
                rows.append({
                    'label': _('Blank %(n)d') % {'n': idx + 1},
                    'value': value if value != '' else _('(empty)'),
                })
        return rows

    if submission.problem.submission_type == 'S':
        text = payload.get('text', '')
        return [{'label': '', 'value': text if text != '' else _('(empty)')}]

    return [{'label': '', 'value': raw}] if raw else []


class ProblemMixin(object):
    model = Problem
    slug_url_kwarg = 'problem'
    slug_field = 'code'

    def get_object(self, queryset=None):
        problem = super(ProblemMixin, self).get_object(queryset)
        if not problem.is_accessible_by(self.request.user):
            raise Http404()
        return problem

    def no_such_problem(self):
        code = self.kwargs.get(self.slug_url_kwarg, None)
        return generic_message(
            self.request,
            _('No such problem'),
            _('Could not find a problem with the code "%s".') % code,
            status=404,
        )

    def get(self, request, *args, **kwargs):
        try:
            return super(ProblemMixin, self).get(request, *args, **kwargs)
        except Http404:
            return self.no_such_problem()


class SolvedProblemMixin(object):
    def get_completed_problems(self):
        if self.in_contest:
            return contest_completed_ids(self.profile.current_contest)
        else:
            return user_completed_ids(self.profile) if self.profile is not None else ()

    def get_attempted_problems(self):
        if self.in_contest:
            return contest_attempted_ids(self.profile.current_contest)
        else:
            return user_attempted_ids(self.profile) if self.profile is not None else ()

    @cached_property
    def in_contest(self):
        return self.profile is not None and self.profile.current_contest is not None

    @cached_property
    def contest(self):
        return self.request.profile.current_contest.contest

    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile


class ProblemSolution(
    SolvedProblemMixin,
    ProblemMixin,
    TitleMixin,
    CommentedDetailView,
):
    context_object_name = 'problem'
    template_name = 'problem/editorial.html'

    def get_title(self):
        return _('Editorial for {0}').format(self.object.name)

    def get_content_title(self):
        return mark_safe(
            escape(_('Editorial for {0}')).format(
                format_html(
                    '<a href="{1}">{0}</a>',
                    self.object.name,
                    reverse('problem_detail', args=[self.object.code]),
                ),
            ),
        )

    def get_context_data(self, **kwargs):
        context = super(ProblemSolution, self).get_context_data(**kwargs)

        solution = get_object_or_404(Solution, problem=self.object)

        if not solution.is_accessible_by(self.request.user) or self.request.in_contest:
            raise Http404()
        context['solution'] = solution
        context['has_solved_problem'] = self.object.id in self.get_completed_problems()
        context['enable_comments'] = settings.DMOJ_ENABLE_COMMENTS
        return context

    def get_comment_page(self):
        return 's:' + self.object.code

    def no_such_problem(self):
        code = self.kwargs.get(self.slug_url_kwarg, None)
        return generic_message(
            self.request,
            _('No such editorial'),
            _('Could not find an editorial with the code "%s".') % code,
            status=404,
        )


class ProblemDetail(ProblemMixin, SolvedProblemMixin, CommentedDetailView):
    context_object_name = 'problem'
    template_name = 'problem/workspace.html'

    def get_template_names(self):
        if self.request.GET.get('legacy') == '1':
            return ['problem/problem.html']
        return [self.template_name]

    def get_comment_page(self):
        return 'p:%s' % self.object.code

    def get_context_data(self, **kwargs):
        context = super(ProblemDetail, self).get_context_data(**kwargs)
        user = self.request.user
        authed = user.is_authenticated
        context['has_submissions'] = (
            authed and Submission.objects.filter(user=user.profile, problem=self.object).exists()
        )
        contest_problem = (
            None
            if not authed or user.profile.current_contest is None
            else get_contest_problem(self.object, user.profile)
        )
        context['contest_problem'] = contest_problem
        if contest_problem:
            contest = contest_problem.contest
            # Isolate practice / assignment / exam drafts and UI context.
            if contest.format_class.has_hidden_results:
                context['workspace_mode'] = 'exam'
            else:
                context['workspace_mode'] = 'assignment'
            context['workspace_context_id'] = contest.key
            context['workspace_problems'] = ContestProblem.objects.filter(
                contest=contest,
            ).select_related('problem', 'problem__mcq_config').prefetch_related(
                'problem__types', 'problem__organizations',
            ).order_by('order')
            context['workspace_context_name'] = contest.name
            context['workspace_return_url'] = reverse('contest_view', args=[contest.key])
            context['workspace_current_order'] = contest_problem.order
        else:
            context['workspace_mode'] = 'practice'
            context['workspace_context_id'] = ''
            context['workspace_problems'] = (
                Problem.get_visible_problems(user)
                .select_related('mcq_config')
                .prefetch_related('types', 'organizations')
                .order_by('code')[:250]
            )
            context['workspace_context_name'] = _('Problem list')
            context['workspace_return_url'] = reverse('problem_list')
            context['workspace_current_order'] = None
        if contest_problem:
            clarifications = self.object.clarifications
            context['has_clarifications'] = clarifications.count() > 0
            context['clarifications'] = clarifications.order_by('-date')
            context['submission_limit'] = contest_problem.max_submissions
            if contest_problem.max_submissions:
                context['submissions_left'] = max(
                    contest_problem.max_submissions
                    - get_contest_submission_count(self.object, user.profile, user.profile.current_contest.virtual),
                    0,
                )

        context['available_judges'] = Judge.objects.filter(online=True, problems=self.object)
        context['show_languages'] = self.object.allowed_languages.count() != Language.objects.count()
        context['has_pdf_render'] = PDF_RENDERING_ENABLED
        context['completed_problem_ids'] = self.get_completed_problems()
        context['attempted_problems'] = self.get_attempted_problems()
        if authed:
            context['editable_problem_ids'] = user_editable_ids(user.profile)
            context['tester_problem_ids'] = user_tester_ids(user.profile)
        else:
            context['editable_problem_ids'] = set()
            context['tester_problem_ids'] = set()

        can_edit = self.object.is_editable_by(user)
        context['can_edit_problem'] = can_edit
        if user.is_authenticated:
            tickets = self.object.tickets
            if not can_edit:
                tickets = tickets.filter(own_ticket_filter(user.profile.id))
            context['has_tickets'] = tickets.exists()
            context['num_open_tickets'] = tickets.filter(is_open=True).values('id').distinct().count()

        try:
            context['editorial'] = Solution.objects.get(problem=self.object)
        except ObjectDoesNotExist:
            pass
        try:
            translation = self.object.translations.get(language=self.request.LANGUAGE_CODE)
        except ProblemTranslation.DoesNotExist:
            context['title'] = self.object.name
            context['language'] = settings.LANGUAGE_CODE
            context['description'] = self.object.description
            context['translated'] = False
        else:
            context['title'] = translation.name
            context['language'] = self.request.LANGUAGE_CODE
            context['description'] = translation.description
            context['translated'] = True

        if not self.object.og_image or not self.object.summary:
            metadata = generate_opengraph(
                'generated-meta-problem:%s:%d' % (context['language'], self.object.id),
                context['description'],
                'problem',
            )
        context['meta_description'] = self.object.summary or metadata[0]
        context['og_image'] = self.object.og_image or metadata[1]
        context['enable_comments'] = settings.DMOJ_ENABLE_COMMENTS

        context['vote_perm'] = self.object.vote_permission_for_user(user)
        if context['vote_perm'].can_vote():
            try:
                context['vote'] = ProblemPointsVote.objects.get(voter=user.profile, problem=self.object)
            except ObjectDoesNotExist:
                context['vote'] = None
        else:
            context['vote'] = None

        if contest_problem:
            cp_qs = ContestProblem.objects.filter(contest=contest_problem.contest)
            context['next_contest_problem'] = cp_qs.filter(order__gt=contest_problem.order).order_by('order').first()
            context['prev_contest_problem'] = cp_qs.filter(order__lt=contest_problem.order).order_by('-order').first()

        context['submission_type'] = self.object.submission_type
        from judge.models import ProblemSubmissionType
        from judge.question_types import all_types, get_grader
        workspace_grader = get_grader(self.object.submission_type)
        context['workspace_is_code'] = workspace_grader.routes_to_bridge
        context['workspace_submission_type_label'] = str(workspace_grader.label)
        # Match problem-list redesign filters: multi format (MCQ expanded), course, tags.
        workspace_format_options = []
        for grader in all_types():
            if grader.submission_type == ProblemSubmissionType.MCQ:
                workspace_format_options.append({
                    'code': ProblemSubmissionType.MCQ, 'label': str(grader.label),
                })
                workspace_format_options.append({'code': 'single', 'label': str(_('Single choice'))})
                workspace_format_options.append({'code': 'multiple', 'label': str(_('Multiple choice'))})
                workspace_format_options.append({'code': 'truefalse', 'label': str(_('True/False'))})
            else:
                workspace_format_options.append({
                    'code': grader.submission_type, 'label': str(grader.label),
                })
        context['workspace_format_options'] = workspace_format_options
        context['workspace_difficulty_options'] = [
            {'code': name, 'label': Problem.difficulty_display(name)}
            for name in Problem.DIFFICULTY_TYPE_NAMES
        ]
        context['workspace_organizations'] = list(Organization.objects.order_by('name'))
        reserved_kp = Problem.reserved_auto_type_labels()
        context['workspace_knowledge_types'] = [
            t for t in ProblemType.objects.order_by('full_name')
            if Problem.is_knowledge_problem_type(t, reserved_labels=reserved_kp)
        ]
        context['workspace_attempt_counts'] = {}
        context['workspace_best_points'] = {}
        if authed:
            if contest_problem:
                drawer_problem_ids = [cp.problem_id for cp in context['workspace_problems']]
                # Assignment drawer: ContestSubmission.points (assignment problem scale).
                if drawer_problem_ids:
                    for row in (
                        ContestSubmission.objects.filter(
                            participation=self.request.participation,
                            problem__problem_id__in=drawer_problem_ids,
                        )
                        .values('problem__problem_id')
                        .annotate(c=Count('id'), p=Max('points'))
                    ):
                        context['workspace_attempt_counts'][row['problem__problem_id']] = row['c']
                        context['workspace_best_points'][row['problem__problem_id']] = row['p']
            else:
                drawer_problem_ids = [p.id for p in context['workspace_problems']]
                if drawer_problem_ids:
                    for row in (
                        Submission.objects.filter(user=user.profile, problem_id__in=drawer_problem_ids)
                        .values('problem_id')
                        .annotate(c=Count('id'), p=Max('points'))
                    ):
                        context['workspace_attempt_counts'][row['problem_id']] = row['c']
                        context['workspace_best_points'][row['problem_id']] = row['p']
        # Duration countdown only when assignment defines personal time_limit (not window deadline).
        context['workspace_show_timer'] = bool(
            contest_problem and contest_problem.contest.time_limit is not None,
        )
        context['workspace_timer_seconds'] = 0
        if context['workspace_show_timer'] and self.request.participation.end_time:
            remaining = self.request.participation.time_remaining
            context['workspace_timer_seconds'] = (
                max(0, int(remaining.total_seconds())) if remaining is not None else 0
            )
        context['workspace_submission'] = None
        context['workspace_enable_comments'] = False
        selected_submission_id = self.request.GET.get('submission')
        if authed and selected_submission_id and selected_submission_id.isdigit():
            selected_qs = Submission.objects.select_related(
                'source', 'contest_object', 'contest__problem',
            ).prefetch_related('problem__mcq_options', 'problem__blanks').filter(
                id=int(selected_submission_id), problem=self.object,
            )
            # Assignment/exam: only allow loading submissions from the active contest.
            if contest_problem:
                selected_qs = selected_qs.filter(contest_object=contest_problem.contest)
            selected_submission = selected_qs.first()
            if selected_submission is not None and selected_submission.can_see_detail(user):
                selected_source = None
                if selected_submission.user_id == self.request.profile.id:
                    try:
                        selected_source = selected_submission.source.source
                    except ObjectDoesNotExist:
                        pass
                results_hidden = not selected_submission.results_visible_to(user)
                display_points = workspace_display_points(selected_submission, self.request)
                context['workspace_submission'] = {
                    'id': selected_submission.id,
                    'result': None if results_hidden else selected_submission.result,
                    'status': selected_submission.status,
                    'result_class': None if results_hidden else selected_submission.result_class,
                    'long_status': None if results_hidden else selected_submission.long_status,
                    'points': None if results_hidden else display_points,
                    'time': None if results_hidden else selected_submission.time,
                    'memory': None if results_hidden else selected_submission.memory,
                    'source': selected_source,
                    'answer_display': (
                        get_workspace_answer_display(selected_submission)
                        if selected_source is not None else []
                    ),
                    'results_hidden': results_hidden,
                    'is_graded': selected_submission.is_graded,
                    'id_secret': selected_submission.id_secret,
                    'status_url': reverse(
                        'problem_workspace_submission_status',
                        args=(self.object.code, selected_submission.id),
                    ),
                }
                # Result-tab comments = submission page (sub:ID), gated by DMOJ_ENABLE_COMMENTS + CommentLock.
                if settings.DMOJ_ENABLE_COMMENTS:
                    comment_handler = SubmissionCommentMixin()
                    comment_handler.object = selected_submission
                    comment_handler.request = self.request
                    comment_handler.comment_use_rich_editor = True
                    comment_ctx = comment_handler.get_comment_context()
                    context['workspace_enable_comments'] = True
                    context['comment_use_rich_editor'] = True
                    context['has_comments'] = comment_ctx['has_comments']
                    context['comment_lock'] = comment_ctx['comment_lock']
                    context['comment_list'] = comment_ctx['comment_list']
                    context['vote_hide_threshold'] = comment_ctx['vote_hide_threshold']
                    context['reply_cutoff'] = comment_ctx['reply_cutoff']
                    context['comment_form'] = comment_ctx['comment_form']
                    context['is_new_user'] = comment_ctx.get('is_new_user', False)
                    context['comment_form_action'] = '%s?submission=%d' % (
                        reverse('problem_detail', args=[self.object.code]),
                        selected_submission.id,
                    )
                context['last_msg'] = event.last()
        if authed:
            workspace_subs_qs = Submission.objects.filter(user=self.request.profile, problem=self.object)
            if contest_problem:
                workspace_subs_qs = workspace_subs_qs.filter(contest_object=contest_problem.contest)
            workspace_subs = list(
                workspace_subs_qs
                .select_related('language', 'contest_object', 'contest__problem')
                .order_by('-date')[:20],
            )
            for submission in workspace_subs:
                submission.display_points = workspace_display_points(submission, self.request)
            context['workspace_submissions'] = workspace_subs
        else:
            context['workspace_submissions'] = []
        from judge import ai_hints as ai_hints_mod
        context['ai_hints_possible'] = bool(
            self.object.ai_hints_enabled and ai_hints_mod.is_configured(),
        )
        context['ai_hints_remaining'] = (
            ai_hints_mod.get_remaining(self.request.profile.id)
            if authed and context['ai_hints_possible'] else 0
        )
        if self.request.user.is_authenticated:
            from judge.question_types import get_grader
            grader = get_grader(self.object.submission_type)
            if grader.routes_to_bridge:
                form = ProblemSubmitForm(
                    initial={'language': self.request.profile.language},
                    instance=Submission(user=self.request.profile, problem=self.object),
                )
                if self.object.is_editable_by(self.request.user):
                    form.fields['judge'].choices = tuple(
                        Judge.objects.filter(online=True, problems=self.object).values_list('name', 'name'),
                    )
                form.fields['language'].queryset = self.object.usable_languages.order_by(
                    'name', 'key').prefetch_related(
                    Prefetch('runtimeversion_set', RuntimeVersion.objects.order_by('priority')),
                )
                form.fields['source'].widget.theme = self.request.profile.resolved_ace_theme
                # Set ACE mode to user's language only if it's in the allowed languages
                # Otherwise use the first allowed language
                usable_languages = form.fields['language'].queryset
                if self.request.profile.language and self.request.profile.language in usable_languages:
                    form.fields['source'].widget.mode = self.request.profile.language.ace
                elif usable_languages.exists():
                    form.fields['source'].widget.mode = usable_languages.first().ace
                context['form'] = form
                context['ACE_URL'] = settings.ACE_URL
            else:
                # Non-code (e.g. MCQ): render the type-specific answer form inline, not the Ace editor.
                # The inline form posts to the dedicated submit page (submit_url).
                context['form'] = grader.build_form(self.object, self.request.profile)
                context['submit_url'] = reverse('problem_submit', args=[self.object.code])

        return context

    def post(self, request, *args, **kwargs):
        """Post submission comments from the Result tab.

        Aligns with SubmissionStatus + SubmissionCommentMixin (page sub:ID).
        Falls back to problem-page comments (CommentedDetailView) otherwise.
        """
        self.object = self.get_object()
        sid = request.GET.get('submission') or request.POST.get('workspace_comment_submission')
        if (
            settings.DMOJ_ENABLE_COMMENTS
            and sid
            and str(sid).isdigit()
            and 'body' in request.POST
        ):
            submission_qs = Submission.objects.filter(id=int(sid), problem=self.object)
            contest_problem = (
                get_contest_problem(self.object, request.profile)
                if request.user.is_authenticated else None
            )
            if contest_problem:
                submission_qs = submission_qs.filter(contest_object=contest_problem.contest)
            submission = submission_qs.first()
            if submission is not None and submission.can_see_detail(request.user):
                handler = SubmissionCommentMixin()
                handler.object = submission
                handler.request = request
                response, form = handler.handle_comment_post(request)
                if response is not None:
                    if isinstance(response, HttpResponseRedirect):
                        return HttpResponseRedirect(
                            '%s?submission=%d' % (
                                reverse('problem_detail', args=[self.object.code]),
                                submission.id,
                            ),
                        )
                    return response
                context = self.get_context_data(object=self.object)
                context['comment_form'] = form
                context['workspace_enable_comments'] = True
                return self.render_to_response(context)
        return super(ProblemDetail, self).post(request, *args, **kwargs)


class ProblemVote(ProblemMixin, DetailView):
    context_object_name = 'problem'
    template_name = 'problem/vote-ajax.html'

    def get_context_data(self, **kwargs):
        if not self.object.vote_permission_for_user(self.request.user).can_vote():
            raise Http404()

        context = super().get_context_data(**kwargs)

        try:
            context['vote'] = ProblemPointsVote.objects.get(voter=self.request.profile, problem=self.object)
        except ObjectDoesNotExist:
            context['vote'] = None

        context['max_possible_vote'] = settings.DMOJ_PROBLEM_MAX_USER_POINTS_VOTE
        context['min_possible_vote'] = settings.DMOJ_PROBLEM_MIN_USER_POINTS_VOTE
        return context

    def post(self, request, *args, **kwargs):
        problem = self.get_object()
        if not problem.vote_permission_for_user(request.user).can_vote():
            return JsonResponse({'message': _('Not allowed to vote on this problem.')}, status=403)

        form = ProblemPointsVoteForm(request.POST)
        if not form.is_valid():
            return JsonResponse(form.errors, status=400)

        with transaction.atomic():
            # Delete any pre-existing votes.
            ProblemPointsVote.objects.filter(voter=request.profile, problem=problem).delete()
            vote = form.save(commit=False)
            vote.voter = request.profile
            vote.problem = problem
            vote.save()

        return JsonResponse({'points': vote.points})


class DeleteProblemVote(ProblemMixin, SingleObjectMixin, View):
    http_method_names = [
        'options',
        'post',
    ]  # This disables GET requests, even though ProblemMixin.get exists.

    def post(self, request, *args, **kwargs):
        problem = self.get_object()
        if not problem.vote_permission_for_user(request.user).can_vote():
            return JsonResponse(
                {'message': _('Not allowed to delete votes on this problem.')},
                status=403,
            )

        ProblemPointsVote.objects.filter(voter=request.profile, problem=problem).delete()
        return JsonResponse({'message': _('success')})


class ProblemVoteStats(ProblemMixin, DetailView):
    context_object_name = 'problem'
    template_name = 'problem/vote-stats-ajax.html'

    def get_context_data(self, **kwargs):
        if not self.object.vote_permission_for_user(self.request.user).can_view():
            raise Http404()

        context = super().get_context_data(**kwargs)

        votes = list(self.object.problem_points_votes.order_by('points').values_list('points', flat=True))
        context['votes'] = votes

        if votes:
            context['mean'] = mean(votes)
            context['median'] = median(votes)

        context['max_possible_vote'] = settings.DMOJ_PROBLEM_MAX_USER_POINTS_VOTE
        context['min_possible_vote'] = settings.DMOJ_PROBLEM_MIN_USER_POINTS_VOTE
        return context


class LatexError(Exception):
    pass


class ProblemPdfView(ProblemMixin, SingleObjectMixin, View):
    logger = logging.getLogger('judge.problem.pdf')
    languages = set(map(itemgetter(0), settings.LANGUAGES))

    def get(self, request, *args, **kwargs):
        if not PDF_RENDERING_ENABLED:
            raise Http404()

        language = kwargs.get('language', self.request.LANGUAGE_CODE)
        if language not in self.languages:
            raise Http404()

        problem = self.get_object()
        pdf_basename = '%s.%s.pdf' % (problem.code, language)

        def render_problem_pdf():
            self.logger.info('Rendering PDF in %s: %s', language, problem.code)

            with translation.override(language):
                try:
                    trans = problem.translations.get(language=language)
                except ProblemTranslation.DoesNotExist:
                    trans = None

                problem_name = trans.name if trans else problem.name
                return render_pdf(
                    html=get_template('problem/raw.html')
                    .render(
                        {
                            'problem': problem,
                            'problem_name': problem_name,
                            'description': (trans.description if trans else problem.description),
                            'url': request.build_absolute_uri(),
                        },
                    )
                    .replace('"//', '"https://')
                    .replace("'//", "'https://"),
                    title=problem_name,
                )

        response = HttpResponse()
        response['Content-Type'] = 'application/pdf'
        response['Content-Disposition'] = f'inline; filename={pdf_basename}'

        if settings.DMOJ_PDF_PROBLEM_CACHE:
            pdf_filename = os.path.join(settings.DMOJ_PDF_PROBLEM_CACHE, pdf_basename)
            if not os.path.exists(pdf_filename):
                with open(pdf_filename, 'wb') as f:
                    f.write(render_problem_pdf())

            if settings.DMOJ_PDF_PROBLEM_INTERNAL:
                url_path = f'{settings.DMOJ_PDF_PROBLEM_INTERNAL}/{pdf_basename}'
            else:
                url_path = None

            add_file_response(request, response, url_path, pdf_filename)
        else:
            response.content = render_problem_pdf()

        return response


class ProblemList(QueryStringSortMixin, TitleMixin, SolvedProblemMixin, ListView):
    model = Problem
    title = gettext_lazy('Problems')
    context_object_name = 'problems'
    template_name = 'problem/list.html'
    paginate_by = 20
    sql_sort = frozenset(('points', 'ac_rate', 'user_count', 'code', 'date'))
    manual_sort = frozenset(('name', 'group', 'solved', 'type', 'editorial'))
    all_sorts = sql_sort | manual_sort
    default_desc = frozenset(('points', 'ac_rate', 'user_count', 'date'))
    default_sort = 'code'

    def get_paginator(self, queryset, per_page, orphans=0, allow_empty_first_page=True, **kwargs):
        paginator = DiggPaginator(
            queryset,
            per_page,
            body=6,
            padding=2,
            orphans=orphans,
            count=queryset.values('pk').count() if not self.in_contest else None,
            allow_empty_first_page=allow_empty_first_page,
            **kwargs,
        )
        if not self.in_contest:
            queryset = queryset.add_i18n_name(self.request.LANGUAGE_CODE)
            sort_key = self.order.lstrip('-')
            if sort_key in self.sql_sort:
                queryset = queryset.order_by(self.order, 'id')
            elif sort_key == 'name':
                queryset = queryset.order_by(self.order.replace('name', 'i18n_name'), 'id')
            elif sort_key == 'group':
                queryset = queryset.order_by(self.order + '__name', 'id')
            elif sort_key == 'editorial':
                queryset = queryset.order_by(self.order.replace('editorial', 'has_public_editorial'), 'id')
            elif sort_key == 'solved':
                if self.request.user.is_authenticated:
                    profile = self.request.profile
                    solved = user_completed_ids(profile)
                    attempted = user_attempted_ids(profile)

                    def _solved_sort_order(problem):
                        if problem.id in solved:
                            return 1
                        if problem.id in attempted:
                            return 0
                        return -1

                    queryset = list(queryset)
                    queryset.sort(key=_solved_sort_order, reverse=self.order.startswith('-'))
            elif sort_key == 'type':
                if self.show_types:
                    queryset = list(queryset)
                    queryset.sort(
                        key=lambda problem: (problem.types_list[0] if problem.types_list else ''),
                        reverse=self.order.startswith('-'),
                    )
            paginator.object_list = queryset
        return paginator

    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile

    def get_contest_queryset(self):
        participation = self.profile.current_contest
        contest = participation.contest

        queryset = (
            contest.contest_problems.select_related('problem__group')
            .defer('problem__description')
            .annotate(user_count=Count('submission__participation', distinct=True))
            .annotate(
                i18n_translation=FilteredRelation(
                    'problem__translations',
                    condition=Q(problem__translations__language=self.request.LANGUAGE_CODE),
                ),
            )
            .annotate(
                i18n_name=Coalesce(
                    F('i18n_translation__name'),
                    F('problem__name'),
                    output_field=CharField(),
                ),
            )
            .order_by('order')
        )

        result = []
        for cp in queryset:
            try:
                user_problem_cell = contest.format.display_user_problem(participation, cp)
            except (KeyError, TypeError, ValueError):
                user_problem_cell = mark_safe('<td>???</td>')

            result.append({
                'id': cp.problem_id,
                'code': cp.problem.code,
                'name': cp.problem.name,
                'i18n_name': cp.i18n_name,
                'group': {'full_name': cp.problem.group.full_name},
                'points': cp.points,
                'partial': cp.partial,
                'user_count': cp.user_count,
                'user_problem_cell': user_problem_cell,
            })

        return result

    @staticmethod
    def apply_full_text(queryset, query):
        if recjk.search(query):
            # MariaDB can't tokenize CJK properly, fallback to LIKE '%term%' for each term.
            for term in query.split():
                queryset = queryset.filter(
                    Q(code__icontains=term) | Q(name__icontains=term) | Q(description__icontains=term),
                )
            return queryset
        return queryset.search(query, queryset.BOOLEAN).extra(order_by=['-relevance'])

    def get_visible_problems_filter(self):
        user = self.request.user
        # Users who may see private problems (or edit everything) get the full set on the front-end
        # list too — mirroring Problem.get_visible_problems and the Django admin. Without this an
        # admin who did not author a private problem could not find it here (nor filter by its
        # group / type), even though it shows up in the admin site.
        if user.is_authenticated and (
            user.has_perm('judge.see_private_problem')
            or (user.has_perm('judge.edit_own_problem') and user.has_perm('judge.edit_all_problem'))
        ):
            return Q()
        filter = Q(is_public=True)
        if not self.request.user.has_perm('judge.see_organization_problem'):
            org_filter = Q(is_organization_private=False)
            if self.profile is not None:
                # If classes are set, check class membership; otherwise check org membership
                org_filter |= Q(is_organization_private=True, classes__isnull=True,
                                organizations__in=self.profile.organizations.all())
                org_filter |= Q(is_organization_private=True,
                                classes__in=self.profile.classes.all())
            filter &= org_filter
        if self.profile is not None:
            filter = Problem.q_add_author_curator_tester(filter, self.profile)
            # Org admins can see all org-private problems
            if self.request.user.has_perm('judge.edit_own_problem'):
                filter |= Problem.organization_filter_q(
                    Organization.admins.through.objects.filter(profile=self.profile).values('organization_id'),
                )
        return filter

    def get_format_options(self):
        """Answer-format filter options as ``(code, label, Q)`` tuples.

        MCQ is a single ``submission_type`` but authored as single / multiple / true-false, so it is
        expanded here (via ``mcq_config``) into three distinct, filterable options.
        """
        from judge.models import ProblemSubmissionType
        from judge.question_types import all_types
        options = []
        for t in all_types():
            if t.submission_type == ProblemSubmissionType.MCQ:
                # Whole-MCQ option: matches any MCQ, including legacy ones without an mcq_config row.
                options.append((ProblemSubmissionType.MCQ, str(t.label),
                                Q(submission_type=ProblemSubmissionType.MCQ)))
                # Plus the finer single / multiple / true-false breakdown.
                options.append(('single', _('Single choice'),
                                Q(submission_type=ProblemSubmissionType.MCQ,
                                  mcq_config__is_true_false=False, mcq_config__multiple_select=False)))
                options.append(('multiple', _('Multiple choice'),
                                Q(submission_type=ProblemSubmissionType.MCQ,
                                  mcq_config__is_true_false=False, mcq_config__multiple_select=True)))
                options.append(('truefalse', _('True/False'),
                                Q(submission_type=ProblemSubmissionType.MCQ, mcq_config__is_true_false=True)))
            else:
                options.append((t.submission_type, str(t.label), Q(submission_type=t.submission_type)))
        return options

    def get_normal_queryset(self):
        filter = self.get_visible_problems_filter()
        queryset = (Problem.objects.filter(filter).select_related('group', 'mcq_config')
                    .defer('description', 'summary'))
        if self.profile is not None and self.hide_solved:
            queryset = queryset.exclude(
                id__in=Submission.objects.filter(
                    user=self.profile,
                    result='AC',
                    case_points__gte=F('case_total'),
                ).values_list('problem_id', flat=True),
            )
        if self.profile is not None and self.problem_status:
            completed = self.get_completed_problems()
            if self.problem_status == 'solved':
                queryset = queryset.filter(id__in=completed)
            elif self.problem_status == 'unsolved':
                queryset = queryset.exclude(id__in=completed)
        if self.show_types:
            queryset = queryset.prefetch_related('types')
        queryset = queryset.annotate(
            has_public_editorial=Case(
                When(
                    solution__is_public=True,
                    solution__publish_on__lte=timezone.now(),
                    then=True,
                ),
                default=False,
                output_field=BooleanField(),
            ),
        )
        if self.has_public_editorial:
            queryset = queryset.filter(has_public_editorial=True)
        if self.category is not None:
            queryset = queryset.filter(group__id=self.category)
        if self.selected_types:
            # AND semantics across types/knowledge points: a problem must carry every selected one.
            # Difficulty levels are an exception: selecting several difficulties means OR, since a
            # problem only ever has a single difficulty.
            difficulty_ids = set(
                ProblemType.objects.filter(name__in=Problem.DIFFICULTY_TYPE_NAMES).values_list('id', flat=True),
            )
            selected_difficulty = [t for t in self.selected_types if t in difficulty_ids]
            selected_other = [t for t in self.selected_types if t not in difficulty_ids]
            for type_id in selected_other:
                queryset = queryset.filter(types=type_id)
            if selected_difficulty:
                queryset = queryset.filter(types__in=selected_difficulty)
        if self.selected_formats:
            qmap = {code: q for code, label, q in self.get_format_options()}
            fmt_q = Q()
            for code in self.selected_formats:
                if code in qmap:
                    fmt_q |= qmap[code]
            if fmt_q:
                queryset = queryset.filter(fmt_q)
        if 'search' in self.request.GET:
            self.search_query = query = ' '.join(self.request.GET.getlist('search')).strip()
            if query:
                if settings.ENABLE_FTS and self.full_text:
                    queryset = self.apply_full_text(queryset, query)
                else:
                    text_filter = (
                        Q(code__icontains=query)
                        | Q(name__icontains=query)
                        | Q(
                            translations__name__icontains=query,
                            translations__language=self.request.LANGUAGE_CODE,
                        )
                    )
                    if self.search_full:
                        text_filter |= Q(description__icontains=query)
                    queryset = queryset.filter(text_filter)
        self.prepoint_queryset = queryset
        if self.point_start is not None:
            queryset = queryset.filter(points__gte=self.point_start)
        if self.point_end is not None:
            queryset = queryset.filter(points__lte=self.point_end)
        if self.ac_min is not None:
            queryset = queryset.filter(ac_rate__gte=self.ac_min)
        if self.ac_max is not None:
            queryset = queryset.filter(ac_rate__lte=self.ac_max)
        return queryset.distinct()

    def get_queryset(self):
        if self.in_contest:
            return self.get_contest_queryset()
        else:
            return self.get_normal_queryset()

    def get_context_data(self, **kwargs):
        context = super(ProblemList, self).get_context_data(**kwargs)
        context['hide_solved'] = 0 if self.in_contest else int(self.hide_solved)
        context['show_types'] = 0 if self.in_contest else int(self.show_types)
        context['has_public_editorial'] = 0 if self.in_contest else int(self.has_public_editorial)
        context['full_text'] = 0 if self.in_contest else int(self.full_text)
        context['category'] = self.category
        context['categories'] = ProblemGroup.objects.all()
        if self.show_types:
            # Knowledge points come from the ProblemType taxonomy.
            all_problem_types = list(ProblemType.objects.all())
            context['problem_types'] = all_problem_types
            context['selected_types'] = self.selected_types
            # Difficulty is a dedicated dimension surfaced from the difficulty-named ProblemTypes,
            # ordered easiest -> hardest.
            difficulty_order = {name: i for i, name in enumerate(Problem.DIFFICULTY_TYPE_NAMES)}
            difficulty_types = sorted(
                (t for t in all_problem_types if t.name in Problem.DIFFICULTY_TYPE_NAMES),
                key=lambda t: difficulty_order.get(t.name, len(difficulty_order)),
            )
            # Knowledge points: exclude format/difficulty rows and leftover auto-chip ProblemTypes.
            reserved_labels = Problem.reserved_auto_type_labels()
            knowledge_types = [
                t for t in all_problem_types
                if Problem.is_knowledge_problem_type(t, reserved_labels=reserved_labels)
            ]
            context['difficulty_types'] = difficulty_types
            context['knowledge_types'] = knowledge_types
            difficulty_ids = {t.id for t in difficulty_types}
            knowledge_ids = {t.id for t in knowledge_types}
            context['selected_difficulty_types'] = [i for i in self.selected_types if i in difficulty_ids]
            context['selected_knowledge_types'] = [
                i for i in self.selected_types if i in knowledge_ids
            ]
            # The answer format is driven by the authoritative submission_type discriminator
            # (MCQ expanded into single / multiple / true-false); offer the question types the
            # system actually supports end-to-end.
            context['format_types'] = [{'code': code, 'label': label}
                                       for code, label, q in self.get_format_options()]
            context['selected_formats'] = self.selected_formats
        context['has_fts'] = settings.ENABLE_FTS
        context['search_query'] = self.search_query
        context['completed_problem_ids'] = self.get_completed_problems()
        context['attempted_problems'] = self.get_attempted_problems()

        # Editing/deleting controls: expose which problems on this page the current user may
        # manage, so the list can render per-row edit/delete and bulk delete. Each id is
        # re-checked server-side on delete, so this is purely for showing the right controls.
        user = self.request.user
        can_manage = user.is_authenticated and user.has_perm('judge.edit_own_problem')
        editable_ids = set()
        if can_manage and not self.in_contest:
            page_problems = context.get('problems') or []
            if user.has_perm('judge.edit_all_problem'):
                editable_ids = {p.id for p in page_problems}
            else:
                editable_ids = {p.id for p in page_problems if p.is_editable_by(user)}
        context['can_manage_problems'] = can_manage and not self.in_contest
        context['editable_problem_ids'] = editable_ids

        from judge.question_types import creatable_types
        context['creatable_problem_types'] = creatable_types(self.request.user)
        context['can_import_excel_problems'] = self.request.user.has_perm('judge.create_private_problem')
        context['ac_min'] = self.ac_min if self.ac_min is not None else ''
        context['ac_max'] = self.ac_max if self.ac_max is not None else ''
        context['problem_status'] = self.problem_status
        context['search_full'] = int(self.search_full)

        context.update(self.get_sort_paginate_context())
        if not self.in_contest:
            context.update(self.get_sort_context())

            sort_labels = {
                'code': _('Problem code'),
                'name': _('Problem'),
                'group': _('Category'),
                'type': _('Problem type'),
                'ac_rate': _('AC %'),
                'points': _('Points'),
                'date': _('Date added'),
                'solved': _('Status'),
                'user_count': _('Users'),
                'editorial': _('Editorial'),
            }
            context['current_sort_label'] = sort_labels.get(self.order.lstrip('-'), '')
            context['current_sort_desc'] = self.order.startswith('-')

            # Per-group counts for the group tabs, over the user-visible problem set.
            base_visible = Problem.objects.filter(self.get_visible_problems_filter())
            group_counts = {
                row['group']: row['count']
                for row in base_visible.values('group').annotate(count=Count('pk', distinct=True))
            }
            context['group_counts'] = group_counts
            context['total_problem_count'] = sum(group_counts.values())

            # Per-type problem counts for the knowledge-point picker badges.
            context['type_counts'] = {
                row['types']: row['count']
                for row in base_visible.values('types').annotate(count=Count('pk', distinct=True))
                if row['types'] is not None
            }

            # Progress ring: solved vs total within the current filtered scope.
            completed = set(context['completed_problem_ids'])
            context['progress_total'] = context['page_obj'].paginator.count
            context['progress_solved'] = (
                self.object_list.filter(id__in=completed).count() if completed else 0
            )

            context['hot_problems'] = hot_problems(timedelta(days=1), settings.DMOJ_PROBLEM_HOT_PROBLEM_COUNT)
            context['point_start'], context['point_end'], context['point_values'] = self.get_noui_slider_points()
        else:
            context['hot_problems'] = None
            context['point_start'], context['point_end'], context['point_values'] = (
                0,
                0,
                {},
            )
            context['hide_contest_scoreboard'] = self.contest.scoreboard_visibility in (
                self.contest.SCOREBOARD_AFTER_CONTEST,
                self.contest.SCOREBOARD_AFTER_PARTICIPATION,
                self.contest.SCOREBOARD_HIDDEN,
            )
        return context

    def get_noui_slider_points(self):
        points = sorted(self.prepoint_queryset.values_list('points', flat=True).distinct())
        if not points:
            return 0, 0, {}
        if len(points) == 1:
            return (
                points[0] - 1,
                points[0] + 1,
                {
                    'min': points[0] - 1,
                    '50%': points[0],
                    'max': points[0] + 1,
                },
            )

        start, end = points[0], points[-1]
        if self.point_start is not None:
            start = self.point_start
        if self.point_end is not None:
            end = self.point_end
        points_map = {0.0: 'min', 1.0: 'max'}
        size = len(points) - 1
        return (
            start,
            end,
            {points_map.get(i / size, '%.2f%%' % (100 * i / size,)): j for i, j in enumerate(points)},
        )

    def GET_with_session(self, request, key):
        if not request.GET:
            return request.session.get(key, False)
        return request.GET.get(key, None) == '1'

    def setup_problem_list(self, request):
        self.hide_solved = self.GET_with_session(request, 'hide_solved')
        # The redesigned problem list always renders the problem type column/filter.
        self.show_types = True
        self.full_text = self.GET_with_session(request, 'full_text')
        # Search the statement by default; honor an explicit un-check once the form is submitted.
        if 'filtered' in request.GET:
            self.search_full = request.GET.get('search_full') == '1'
        else:
            self.search_full = True
        self.has_public_editorial = self.GET_with_session(request, 'has_public_editorial')

        self.search_query = None
        self.category = None
        self.selected_types = []
        self.selected_formats = []

        # This actually copies into the instance dictionary...
        self.all_sorts = set(self.all_sorts)
        if not self.show_types:
            self.all_sorts.discard('type')

        self.category = safe_int_or_none(request.GET.get('category'))
        if 'type' in request.GET:
            try:
                self.selected_types = list(map(int, request.GET.getlist('type')))
            except ValueError:
                pass
        if 'format' in request.GET:
            valid_formats = {code for code, label, q in self.get_format_options()}
            self.selected_formats = [c for c in request.GET.getlist('format') if c in valid_formats]

        self.point_start = safe_float_or_none(request.GET.get('point_start'))
        self.point_end = safe_float_or_none(request.GET.get('point_end'))

        self.ac_min = safe_float_or_none(request.GET.get('ac_min'))
        self.ac_max = safe_float_or_none(request.GET.get('ac_max'))
        self.problem_status = request.GET.get('status') or ''
        if self.problem_status not in ('solved', 'unsolved'):
            self.problem_status = ''

    def get(self, request, *args, **kwargs):
        self.setup_problem_list(request)

        try:
            return super(ProblemList, self).get(request, *args, **kwargs)
        except ProgrammingError as e:
            return generic_message(request, 'FTS syntax error', e.args[1], status=400)

    def post(self, request, *args, **kwargs):
        to_update = ('hide_solved', 'show_types', 'has_public_editorial', 'full_text')
        for key in to_update:
            if key in request.GET:
                val = request.GET.get(key) == '1'
                request.session[key] = val
            else:
                request.session.pop(key, None)
        return HttpResponseRedirect(request.get_full_path())


class ProblemBulkDelete(LoginRequiredMixin, View):
    """POST-only endpoint to delete one or more problems from the problem list.

    The per-row delete submits a single ``codes`` value; the bulk toolbar submits several.
    Every problem is re-checked with ``is_editable_by`` here, so a user can only ever delete
    problems they are allowed to edit, regardless of what the form posted.
    """

    def post(self, request, *args, **kwargs):
        redirect_to = request.POST.get('next') or reverse('problem_list')
        if not url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            redirect_to = reverse('problem_list')
        codes = [c for c in request.POST.getlist('codes') if c]
        if not codes:
            messages.error(request, _('No problems selected.'))
            return HttpResponseRedirect(redirect_to)

        deleted = denied = 0
        with transaction.atomic():
            for problem in Problem.objects.filter(code__in=codes):
                if problem.is_editable_by(request.user):
                    problem.delete()
                    deleted += 1
                else:
                    denied += 1

        if deleted:
            messages.success(request, _('Deleted %(count)d problem(s).') % {'count': deleted})
        if denied:
            messages.error(
                request,
                _('Skipped %(count)d problem(s) you are not allowed to delete.') % {'count': denied},
            )
        return HttpResponseRedirect(redirect_to)


class LanguageTemplateAjax(View):
    def get(self, request, *args, **kwargs):
        try:
            language = get_object_or_404(Language, id=int(request.GET.get('id', 0)))
        except ValueError:
            raise Http404()

        problem_id = request.GET.get('problem_id')
        if problem_id:
            try:
                template = ProblemTemplate.objects.get(problem_id=problem_id, language=language).code
                resp = HttpResponse(template, content_type='text/plain')
                resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
                resp['Pragma'] = 'no-cache'
                return resp
            except (ProblemTemplate.DoesNotExist, ValueError):
                pass

        resp = HttpResponse(language.template, content_type='text/plain')
        resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp['Pragma'] = 'no-cache'
        return resp


class ProblemCodeCheckView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if not (
            request.user.has_perm('judge.clone_problem')
            or request.user.is_superuser
            or getattr(request.user.profile, 'is_instructor', False)
        ):
            return JsonResponse({'error': _('Forbidden')}, status=403)

        code = request.GET.get('code', '').strip()
        if not code:
            return JsonResponse({'error': _('Missing code')}, status=400)

        exists = Problem.objects.filter(code=code).exists()
        return JsonResponse({'exists': exists})


class RandomProblem(ProblemList):
    def get(self, request, *args, **kwargs):
        self.setup_problem_list(request)
        if self.in_contest:
            raise Http404()

        queryset = self.get_normal_queryset()
        count = queryset.count()
        if not count:
            return HttpResponseRedirect(
                '%s%s%s'
                % (
                    reverse('problem_list'),
                    request.META['QUERY_STRING'] and '?',
                    request.META['QUERY_STRING'],
                ),
            )
        return HttpResponseRedirect(queryset[randrange(count)].get_absolute_url())


user_logger = logging.getLogger('judge.user')


class ProblemSubmit(LoginRequiredMixin, ProblemMixin, TitleMixin, SingleObjectFormView):
    template_name = 'problem/submit.html'
    form_class = ProblemSubmitForm

    @cached_property
    def grader(self):
        from judge.question_types import get_grader
        return get_grader(self.object.submission_type)

    @cached_property
    def is_code(self):
        # Code submissions use the Ace/language form and the judge bridge; other question types
        # render a type-specific form and are graded internally.
        return self.grader.routes_to_bridge

    @cached_property
    def contest_problem(self):
        if self.request.profile.current_contest is None:
            return None
        return get_contest_problem(self.object, self.request.profile)

    @cached_property
    def remaining_submission_count(self):
        max_subs = self.contest_problem and self.contest_problem.max_submissions
        if max_subs is None:
            return None
        # When an IE submission is rejudged into a non-IE status, it will count towards the
        # submission limit. We max with 0 to ensure that `remaining_submission_count` returns
        # a non-negative integer, which is required for future checks in this view.
        return max(
            0,
            max_subs
            - get_contest_submission_count(
                self.object,
                self.request.profile,
                self.request.profile.current_contest.virtual,
            ),
        )

    @cached_property
    def default_language(self):
        # If the old submission exists, use its language, otherwise use the user's default language.
        if self.old_submission is not None:
            return self.old_submission.language
        return self.request.profile.language

    def get_content_title(self):
        return mark_safe(
            escape(_('Submit to %s'))
            % format_html(
                '<a href="{0}">{1}</a>',
                reverse('problem_detail', args=[self.object.code]),
                self.object.translated_name(self.request.LANGUAGE_CODE),
            ),
        )

    def get_title(self):
        return _('Submit to %s') % self.object.translated_name(self.request.LANGUAGE_CODE)

    def get_initial(self):
        if self.is_code:
            initial = {'language': self.default_language}
            if self.old_submission is not None:
                initial['source'] = self.old_submission.source.source
            return initial
        # Non-code: prefill the previous answer when resubmitting.
        initial = {}
        if self.old_submission is not None and self.object.submission_type == 'M':
            try:
                selected = json.loads(self.old_submission.source.source).get('selected', [])
            except (ValueError, TypeError, ObjectDoesNotExist):
                selected = []
            try:
                multiple = self.object.mcq_config.multiple_select
            except ObjectDoesNotExist:
                multiple = False
            if multiple:
                initial['options'] = [str(s) for s in selected]
            elif selected:
                initial['options'] = str(selected[0])
        elif self.old_submission is not None and self.object.submission_type == 'B':
            try:
                answers = json.loads(self.old_submission.source.source).get('answers', [])
            except (ValueError, TypeError, ObjectDoesNotExist):
                answers = []
            for blank, answer in zip(self.object.blanks.all(), answers):
                initial['fb_%d' % blank.order] = answer
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = Submission(user=self.request.profile, problem=self.object)

        if self.object.is_editable_by(self.request.user):
            kwargs['judge_choices'] = tuple(
                Judge.objects.filter(online=True, problems=self.object).values_list('name', 'name'),
            )
        else:
            kwargs['judge_choices'] = ()

        return kwargs

    def get_form(self, form_class=None):
        if not self.is_code:
            posting = self.request.method in ('POST', 'PUT')
            data = self.request.POST if posting else None
            files = self.request.FILES if posting else None
            return self.grader.build_form(self.object, self.request.profile,
                                          data=data, files=files, initial=self.get_initial())

        form = super().get_form(form_class)

        form.fields['language'].queryset = self.object.usable_languages.order_by('name', 'key').prefetch_related(
            Prefetch('runtimeversion_set', RuntimeVersion.objects.order_by('priority')),
        )

        form_data = getattr(form, 'cleaned_data', form.initial)
        if 'language' in form_data:
            form.fields['source'].widget.mode = form_data['language'].ace
        form.fields['source'].widget.theme = self.request.profile.resolved_ace_theme

        return form

    def get_success_url(self):
        if self.request.POST.get('workspace') == '1':
            return '%s?submission=%d' % (
                reverse('problem_detail', args=(self.object.code,)), self.new_submission.id,
            )
        return reverse('submission_status', args=(self.new_submission.id,))

    def _wants_workspace_json(self):
        return (
            self.request.POST.get('workspace') == '1'
            and self.request.headers.get('X-Workspace-Submit') == '1'
        )

    def _workspace_json_error(self, message, status=400, **extra):
        payload = {'error': message}
        payload.update(extra)
        return JsonResponse(payload, status=status)

    def form_invalid(self, form):
        if self._wants_workspace_json():
            message = _('Please fix the errors below.')
            for _field, errors in form.errors.items():
                if errors:
                    message = str(errors[0])
                    break
            # Empty code / blank required fields surface as field errors — keep them explicit.
            if self.is_code and not (form.data.get('source') or '').strip():
                message = _('Source code cannot be empty.')
            elif not self.is_code:
                non_empty = False
                for key, value in form.data.items():
                    if key in ('csrfmiddlewaretoken', 'workspace', 'parent'):
                        continue
                    if isinstance(value, list):
                        if any(str(v).strip() for v in value):
                            non_empty = True
                            break
                    elif str(value).strip():
                        non_empty = True
                        break
                if not non_empty:
                    message = _('Answer cannot be empty.')
            return self._workspace_json_error(message, status=400, errors=form.errors.get_json_data())
        return super().form_invalid(form)

    def _workspace_success_response(self, form):
        if self._wants_workspace_json():
            submission = self.new_submission
            hidden = not submission.results_visible_to(self.request.user)
            try:
                source = submission.source.source
            except ObjectDoesNotExist:
                source = None
            return JsonResponse({
                'submission_id': submission.id,
                'status': submission.status,
                'result': None if hidden else submission.result,
                'results_hidden': hidden,
                'source': source,
                'answer_display': get_workspace_answer_display(submission) if source is not None else [],
                'status_url': reverse('problem_workspace_submission_status', args=(self.object.code, submission.id)),
            })
        return super().form_valid(form)

    def _submission_blocked_response(self):
        """Pre-submit guards shared by every question type. Returns a response if blocked."""
        if (
            not self.request.user.has_perm('judge.spam_submission')
            and Submission.objects.filter(user=self.request.profile, rejudged_date__isnull=True)
            .exclude(status__in=['D', 'IE', 'CE', 'AB'])
            .count()
            >= settings.DMOJ_SUBMISSION_LIMIT
        ):
            if self._wants_workspace_json():
                return self._workspace_json_error(_('You submitted too many submissions.'), status=429)
            return HttpResponse(
                format_html('<h1>{0}</h1>', _('You submitted too many submissions.')),
                status=429,
            )
        if not self.request.user.is_superuser and self.object.banned_users.filter(id=self.request.profile.id).exists():
            if self._wants_workspace_json():
                return self._workspace_json_error(
                    _('You are permanently barred from submitting to this problem.'),
                    status=403,
                )
            return generic_message(
                self.request,
                _('Banned from submitting'),
                _(
                    'You have been declared persona non grata for this problem. '
                    'You are permanently barred from submitting to this problem.',
                ),
            )
        # Must check for zero and not None. None means infinite submissions remaining.
        if self.remaining_submission_count == 0:
            if self._wants_workspace_json():
                return self._workspace_json_error(
                    _('You have exceeded the submission limit for this problem.'),
                    status=429,
                )
            return generic_message(
                self.request,
                _('Too many submissions'),
                _('You have exceeded the submission limit for this problem.'),
            )
        return None

    def _attach_contest(self, submission):
        contest_problem = self.contest_problem
        if contest_problem is not None:
            # Use the contest object from current_contest.contest because we already use it
            # in profile.update_contest().
            submission.contest_object = self.request.profile.current_contest.contest
            if self.request.profile.current_contest.live:
                submission.locked_after = submission.contest_object.locked_after
            submission.save()
            ContestSubmission(
                submission=submission,
                problem=contest_problem,
                participation=self.request.profile.current_contest,
            ).save()
        else:
            submission.save()

    def form_valid(self, form):
        blocked = self._submission_blocked_response()
        if blocked is not None:
            return blocked
        if self.is_code:
            return self._code_form_valid(form)
        return self._noncode_form_valid(form)

    def _code_form_valid(self, form):
        if not self.object.allowed_languages.filter(id=form.cleaned_data['language'].id).exists():
            raise PermissionDenied()

        # Check if problem uses harness grading - only allow languages with harnesses
        harness_languages = set(self.object.harnesses.values_list('language__key', flat=True))
        if harness_languages:
            language_key = form.cleaned_data['language'].key
            if language_key not in harness_languages:
                harness_language_names = list(
                    self.object.harnesses.select_related('language').values_list('language__name', flat=True),
                )
                return generic_message(
                    self.request,
                    _('Language not supported'),
                    _('This problem only accepts submissions in: %s') % ', '.join(harness_language_names),
                )

        with transaction.atomic():
            self.new_submission = form.save(commit=False)
            self._attach_contest(self.new_submission)
            source = SubmissionSource(submission=self.new_submission, source=form.cleaned_data['source'])
            source.save()

        # Save a query.
        self.new_submission.source = source
        self.new_submission.judge(force_judge=True, judge_id=form.cleaned_data['judge'])

        return self._workspace_success_response(form)

    def _noncode_form_valid(self, form):
        with transaction.atomic():
            self.new_submission = Submission(
                user=self.request.profile, problem=self.object,
                language=Language.get_answer_language(),
            )
            self._attach_contest(self.new_submission)
            self.grader.store_answer(self.new_submission, form.cleaned_data)

        # Defer AI grading to a background thread so the submission page
        # loads immediately with a spinner; the websocket event fires when
        # grading completes and the page updates in-place.
        if self._needs_ai_grading():
            self.new_submission.status = 'QU'
            self.new_submission.save(update_fields=['status'])
            threading.Thread(
                target=self._grade_in_background,
                args=(self.new_submission,),
                daemon=True,
            ).start()
        else:
            self.new_submission.judge(force_judge=True)

        return self._workspace_success_response(form)

    def _needs_ai_grading(self):
        """Return True if this submission type requires an LLM call (and thus
        synchronous grading would block the submit response)."""
        from judge.models import ProblemSubmissionType
        st = self.object.submission_type
        if st == ProblemSubmissionType.SHORT_ANSWER:
            return True
        if st == ProblemSubmissionType.FILL_BLANK:
            from judge.models.question import GRADE_AI
            try:
                config = self.object.fill_blank_config
            except Exception:
                return False
            return config.grading_mode == GRADE_AI
        return False

    @staticmethod
    def _grade_in_background(submission):
        """Called from a daemon thread to grade the submission and fire the
        websocket event so the status page updates."""
        try:
            submission.judge(force_judge=True)
        except Exception:
            logger = logging.getLogger(__name__)
            logger.exception('Background AI grading failed for submission %d',
                             submission.id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['submission_type'] = self.object.submission_type
        context['submission_limit'] = self.contest_problem and self.contest_problem.max_submissions
        context['submissions_left'] = self.remaining_submission_count
        if self.is_code:
            context['langs'] = Language.objects.all()
            context['no_judges'] = not context['form'].fields['language'].queryset
            context['ACE_URL'] = settings.ACE_URL
            context['default_lang'] = self.default_language
        return context

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except Http404:
            # Is this really necessary? This entire post() method could be removed if we don't log this.
            user_logger.info(
                'Naughty user %s wants to submit to %s without permission',
                request.user.username,
                kwargs.get(self.slug_url_kwarg),
            )
            return HttpResponseForbidden(format_html('<h1>{0}</h1>', _('Do you want me to ban you?')))

    def dispatch(self, request, *args, **kwargs):
        submission_id = kwargs.get('submission')
        if submission_id is not None:
            self.old_submission = get_object_or_404(
                Submission.objects.select_related('source', 'language'),
                id=submission_id,
            )
            if not request.user.has_perm('judge.resubmit_other') and self.old_submission.user != request.profile:
                raise PermissionDenied()
        else:
            self.old_submission = None

        return super().dispatch(request, *args, **kwargs)


class ProblemClone(ProblemMixin, PermissionRequiredMixin, TitleMixin, SingleObjectFormView):
    title = gettext_lazy('Clone Problem')
    template_name = 'problem/clone.html'
    form_class = ProblemCloneForm
    permission_required = 'judge.clone_problem'

    def has_permission(self):
        if super().has_permission():
            return True
        if not self.request.user.is_authenticated:
            return False
        problem = self.get_object()
        return problem.is_viewable_by_org_admin(self.request.user)

    def form_valid(self, form):
        problem = self.object

        # Store references to ManyToMany relationships
        languages = problem.allowed_languages.all()
        language_limits = problem.language_limits.all()
        organizations = problem.organizations.all()
        types = problem.types.all()
        old_code = problem.code
        new_code = form.cleaned_data['code']

        # Store references to related objects BEFORE cloning problem
        old_problem_id = problem.id
        test_cases = list(ProblemTestCase.objects.filter(dataset_id=old_problem_id).order_by('order'))
        templates = list(ProblemTemplate.objects.filter(problem_id=old_problem_id))
        harnesses = list(ProblemHarness.objects.filter(problem_id=old_problem_id))

        # Check if ProblemData exists for original problem
        try:
            original_problem_data = ProblemData.objects.get(problem_id=old_problem_id)
            has_problem_data = True
        except ProblemData.DoesNotExist:
            original_problem_data = None
            has_problem_data = False

        # Clone the problem
        problem.pk = None
        problem.is_public = False
        problem.ac_rate = 0
        problem.user_count = 0
        problem.code = new_code

        with revisions.create_revision(atomic=True):
            # Save new problem
            problem.save()
            problem.authors.add(self.request.profile)
            problem.allowed_languages.set(languages)
            problem.language_limits.set(language_limits)
            problem.organizations.set(organizations)
            problem.types.set(types)

            # Clone ProblemData if it exists
            if has_problem_data:
                self._clone_problem_data(original_problem_data, problem, old_code, new_code)

            # Clone ProblemTestCase instances
            self._clone_test_cases(test_cases, problem)

            # Clone ProblemTemplate instances
            self._clone_templates(templates, problem)

            # Clone ProblemHarness instances
            self._clone_harnesses(harnesses, problem)

            # Copy init.yml from original problem
            self._copy_init_yml(old_code, new_code)

            # Set revision metadata
            revisions.set_user(self.request.user)
            revisions.set_comment(_('Cloned problem from %s') % old_code)

        return HttpResponseRedirect(reverse('admin:judge_problem_change', args=(problem.id,)))

    def _clone_problem_data(self, original_data, new_problem, old_code, new_code):
        """
        Clone ProblemData instance and copy associated files.

        Args:
            original_data: Original ProblemData instance
            new_problem: Newly created Problem instance
            old_code: Original problem code
            new_code: New problem code
        """
        logger = logging.getLogger('judge.problem')
        has_file_errors = False

        # Create new ProblemData instance
        new_data = ProblemData()
        new_data.problem = new_problem
        new_data.output_prefix = original_data.output_prefix
        new_data.output_limit = original_data.output_limit
        new_data.feedback = ''  # Reset feedback for new problem
        new_data.checker = original_data.checker
        new_data.unicode = original_data.unicode
        new_data.nobigmath = original_data.nobigmath
        new_data.checker_args = original_data.checker_args

        # Handle zipfile copying
        if original_data.zipfile:
            try:
                # Read original zipfile
                original_zipfile_path = original_data.zipfile.path
                if os.path.exists(original_zipfile_path):
                    with open(original_zipfile_path, 'rb') as f:
                        file_content = f.read()

                    # Create new zipfile with new problem code in path
                    original_filename = os.path.basename(original_data.zipfile.name)
                    new_data.zipfile.save(original_filename, ContentFile(file_content), save=False)
            except (IOError, OSError):
                logger.warning('Failed to copy zipfile for problem %s: %s', new_code, exc_info=True)
                has_file_errors = True

        # Handle generator file copying
        if original_data.generator:
            try:
                # Read original generator
                original_generator_path = original_data.generator.path
                if os.path.exists(original_generator_path):
                    with open(original_generator_path, 'rb') as f:
                        file_content = f.read()

                    # Create new generator with new problem code in path
                    original_filename = os.path.basename(original_data.generator.name)
                    new_data.generator.save(original_filename, ContentFile(file_content), save=False)
            except (IOError, OSError):
                logger.warning('Failed to copy generator for problem %s: %s', new_code, exc_info=True)
                has_file_errors = True

        # Save the new ProblemData instance
        new_data.save()

        # Copy individual test case files from old directory to new directory
        old_dir = problem_data_storage.path(old_code)
        new_dir = problem_data_storage.path(new_code)

        if os.path.exists(old_dir) and os.path.isdir(old_dir):
            try:
                # Create new directory if it doesn't exist
                os.makedirs(new_dir, exist_ok=True)

                # Copy all files except zipfile and generator (already handled)
                # and init.yml (will be copied separately)
                for filename in os.listdir(old_dir):
                    if filename == 'init.yml':
                        continue  # Skip init.yml, it will be copied separately

                    old_file_path = os.path.join(old_dir, filename)
                    new_file_path = os.path.join(new_dir, filename)

                    # Only copy regular files, not directories
                    if os.path.isfile(old_file_path):
                        # Skip if this is the zipfile or generator we already copied
                        if original_data.zipfile and filename == os.path.basename(
                            original_data.zipfile.name,
                        ):
                            continue
                        if original_data.generator and filename == os.path.basename(
                            original_data.generator.name,
                        ):
                            continue

                        shutil.copy2(old_file_path, new_file_path)
            except (IOError, OSError):
                logger.warning('Failed to copy data directory for problem %s: %s', new_code, exc_info=True)
                has_file_errors = True

        # Show warning message to user if there were any file errors
        if has_file_errors:
            messages.warning(
                self.request,
                _(
                    'Some test data files could not be copied. Please verify files in problem data.',
                ),
            )

    def _clone_test_cases(self, test_cases, new_problem):
        """
        Clone all ProblemTestCase instances for the new problem.

        Args:
            test_cases: List of original ProblemTestCase instances
            new_problem: Newly created Problem instance
        """
        new_test_cases = []

        for original_case in test_cases:
            new_case = ProblemTestCase()
            new_case.dataset = new_problem
            new_case.order = original_case.order
            new_case.type = original_case.type
            new_case.input_file = original_case.input_file
            new_case.output_file = original_case.output_file
            new_case.generator_args = original_case.generator_args
            new_case.points = original_case.points
            new_case.is_pretest = original_case.is_pretest
            new_case.output_prefix = original_case.output_prefix
            new_case.output_limit = original_case.output_limit
            new_case.checker = original_case.checker
            new_case.checker_args = original_case.checker_args
            new_case.batch_dependencies = original_case.batch_dependencies

            new_test_cases.append(new_case)

        # Bulk create for efficiency
        if new_test_cases:
            ProblemTestCase.objects.bulk_create(new_test_cases)

    def _clone_templates(self, templates, new_problem):
        """
        Clone all ProblemTemplate instances for the new problem.

        Args:
            templates: List of original ProblemTemplate instances
            new_problem: Newly created Problem instance
        """
        new_templates = []

        for original_template in templates:
            new_template = ProblemTemplate()
            new_template.problem = new_problem
            new_template.language = original_template.language
            new_template.code = original_template.code

            new_templates.append(new_template)

        # Bulk create for efficiency
        if new_templates:
            ProblemTemplate.objects.bulk_create(new_templates)

    def _clone_harnesses(self, harnesses, new_problem):
        """
        Clone all ProblemHarness instances for the new problem.

        Args:
            harnesses: List of original ProblemHarness instances
            new_problem: Newly created Problem instance
        """
        new_harnesses = []

        for original in harnesses:
            new_harness = ProblemHarness()
            new_harness.problem = new_problem
            new_harness.language = original.language
            new_harness.harness_code = original.harness_code
            new_harness.skip_precompile = original.skip_precompile
            new_harness.run_student_main = original.run_student_main

            new_harnesses.append(new_harness)

        # Bulk create for efficiency
        if new_harnesses:
            ProblemHarness.objects.bulk_create(new_harnesses)

    def _copy_init_yml(self, old_code, new_code):
        """
        Copy init.yml from the original problem to the cloned problem.

        Args:
            old_code: Original problem code
            new_code: New problem code
        """
        logger = logging.getLogger('judge.problem')

        try:
            old_init_path = problem_data_storage.path(os.path.join(old_code, 'init.yml'))
            new_init_path = problem_data_storage.path(os.path.join(new_code, 'init.yml'))

            if os.path.exists(old_init_path):
                shutil.copy2(old_init_path, new_init_path)
        except (IOError, OSError):
            logger.warning('Failed to copy init.yml for %s', new_code, exc_info=True)
            messages.warning(
                self.request,
                _(
                    'Failed to copy init.yml. You may need to regenerate it manually.',
                ),
            )


class ProblemWorkspaceRun(LoginRequiredMixin, ProblemMixin, SingleObjectMixin, View):
    """Run code in the existing isolated IDE sandbox, never against this problem."""

    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        from judge.question_types import get_grader
        if not get_grader(self.object.submission_type).routes_to_bridge:
            return JsonResponse({'error': _('Run is available only for programming problems.')}, status=400)
        if self.object.banned_users.filter(id=request.profile.id).exists() and not request.user.is_superuser:
            return JsonResponse({'error': _('You are banned from submitting to this problem.')}, status=403)
        if not self.object.usable_languages.filter(id=request.POST.get('language')).exists():
            return JsonResponse({'error': _('This language is not allowed for this problem.')}, status=400)
        if request.profile.current_contest and not get_contest_problem(self.object, request.profile):
            return JsonResponse({'error': _('This problem is not in your active assignment.')}, status=403)
        # Reuse the site IDE endpoint as-is: same rate limits, source/input
        # caps, output truncation, and bridge protocol. Submissions are filed
        # under the internal `idepractice` sandbox problem (see IDE_PROBLEM_CODE)
        # so they never attach ContestSubmission or touch this problem's tests.
        from judge.views.ide import IDESubmitView
        return IDESubmitView().post(request)


class ProblemWorkspaceSubmissionStatus(LoginRequiredMixin, ProblemMixin, SingleObjectMixin, View):
    """Status JSON for the current user's formal Workspace submission."""

    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        problem = self.get_object()
        submission = get_object_or_404(
            Submission.objects.select_related(
                'source', 'contest_object', 'contest__problem', 'user__user',
            ).prefetch_related('problem__mcq_options', 'problem__blanks'),
            id=kwargs['submission'], problem=problem,
        )
        if not submission.can_see_detail(request.user):
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        contest_problem = get_contest_problem(problem, request.profile)
        if contest_problem and submission.contest_object_id != contest_problem.contest_id:
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        hidden = not submission.results_visible_to(request.user)
        source = None
        if submission.user_id == request.profile.id:
            try:
                source = submission.source.source
            except ObjectDoesNotExist:
                pass
        answer_display = get_workspace_answer_display(submission) if source is not None else []
        comments_html = ''
        if settings.DMOJ_ENABLE_COMMENTS:
            comment_handler = SubmissionCommentMixin()
            comment_handler.object = submission
            comment_handler.request = request
            comment_handler.comment_use_rich_editor = True
            comment_ctx = comment_handler.get_comment_context()
            comment_ctx.update({
                'request': request,
                'ENABLE_COMMENTS': True,
                'comment_use_rich_editor': True,
                'comment_form_action': '%s?submission=%d' % (
                    reverse('problem_detail', args=[problem.code]),
                    submission.id,
                ),
            })
            comments_html = render_to_string('comments/list.html', comment_ctx, request=request)
        return JsonResponse({
            'id': submission.id,
            'status': submission.status,
            'result': None if hidden else submission.result,
            'result_class': None if hidden else submission.result_class,
            'points': None if hidden else workspace_display_points(submission, request),
            'time': None if hidden else submission.time,
            'memory': None if hidden else submission.memory,
            'results_hidden': hidden,
            'is_graded': submission.is_graded,
            'source': source,
            'answer_display': answer_display,
            'comments_html': comments_html,
            'id_secret': submission.id_secret,
            'status_url': reverse(
                'problem_workspace_submission_status',
                args=(problem.code, submission.id),
            ),
        })


class ProblemWorkspaceSubmissionsList(LoginRequiredMixin, ProblemMixin, SingleObjectMixin, View):
    """JSON submission rows for the Workspace inline submissions panel."""

    http_method_names = ['get']
    VALID_SCOPES = frozenset({'my', 'all', 'best'})

    def get(self, request, *args, **kwargs):
        from judge.views.submission import ProblemSubmissionsBase

        problem = self.get_object()
        scope = request.GET.get('scope', 'my')
        if scope not in self.VALID_SCOPES:
            return JsonResponse({'error': _('Invalid scope.')}, status=400)

        contest_problem = get_contest_problem(problem, request.profile)
        # Assignment/exam UI only offers "My"; reject All/Best to match the panel.
        if contest_problem and scope in ('all', 'best'):
            return JsonResponse({'error': _('Permission denied.')}, status=403)

        if scope in ('all', 'best'):
            checker = ProblemSubmissionsBase()
            checker.request = request
            checker.problem = problem
            checker.problem_name = problem.translated_name(request.LANGUAGE_CODE)
            try:
                checker.access_check(request)
            except PermissionDenied:
                return JsonResponse({'error': _('Permission denied.')}, status=403)
            except Http404:
                return JsonResponse({'error': _('Permission denied.')}, status=403)

        if scope == 'my':
            my_qs = Submission.objects.filter(user=request.profile, problem=problem)
            if contest_problem:
                my_qs = my_qs.filter(contest_object=contest_problem.contest)
            submissions = (
                my_qs
                .select_related('language', 'contest_object', 'contest__problem')
                .order_by('-date')[:50]
            )
        elif scope == 'all':
            checker.selected_languages = set()
            checker.selected_statuses = set()
            submissions = checker.get_queryset().select_related(
                'language', 'contest_object', 'contest__problem',
            ).order_by('-id')[:50]
        else:
            checker.selected_languages = set()
            checker.selected_statuses = set()
            submissions = self._best_submissions(
                checker.get_queryset().select_related('language', 'contest_object', 'contest__problem'),
            )

        profile = request.profile
        completed = user_completed_ids(profile)
        editable = user_editable_ids(profile)
        testers = user_tester_ids(profile)
        rows = []
        for submission in submissions:
            if not submission.can_see_detail(request.user):
                continue
            html = render_to_string(
                'problem/workspace-submission-row.html',
                {
                    'submission': submission,
                    'profile_id': profile.id,
                    'completed_problem_ids': completed,
                    'editable_problem_ids': editable,
                    'tester_problem_ids': testers,
                    'request': request,
                },
                request=request,
            )
            rows.append({
                'id': submission.id,
                'html': html,
                'status_url': reverse(
                    'problem_workspace_submission_status',
                    args=(problem.code, submission.id),
                ),
                'url': '%s?submission=%d' % (
                    reverse('problem_detail', args=[problem.code]), submission.id,
                ),
            })

        return JsonResponse({'scope': scope, 'rows': rows})

    @staticmethod
    def _best_submissions(queryset):
        seen_users = set()
        rows = []
        for submission in queryset.filter(points__gt=0, user__is_unlisted=False).order_by('-points', 'time'):
            if submission.user_id in seen_users:
                continue
            seen_users.add(submission.user_id)
            rows.append(submission)
            if len(rows) >= 50:
                break
        return rows

"""Unified front-end "Add problem" page (LeetCode-style redesign).

A single standalone page (see templates/problem/create_v2.html) lets an authorized user author a
problem of several question types. It reuses the existing backend primitives:

* Code problems  -> Problem(submission_type=CODE) + ProblemData / ProblemTestCase written to
  ``problem_data_storage`` and compiled into init.yml (so test cases can be entered at add time).
* Single/Multiple -> Problem(submission_type=MCQ) + ProblemMCQConfig + ProblemMCQOption
  (mirrors judge/views/mcq.py).

Other question types from the design (fill-in-blank, true/false, short answer, compound, other)
have no backend grader yet; the page shows them but this view refuses to save them
(``NOT_IMPLEMENTED_TYPES``) until their models/graders land.

The page talks to this view with a single JSON POST (the rich client collects everything and
submits ``application/json``); responses are JSON ``{ok, redirect}`` or ``{error}``.
"""
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import View

from judge.models import (Language, Organization, Problem, ProblemGroup, ProblemMCQConfig, ProblemMCQOption,
                          ProblemSubmissionType, ProblemType)
from judge.utils.problem_create import apply_simple_problem_defaults, default_problem_group, default_problem_type

# Types the redesigned page offers but the backend cannot grade/store yet.
NOT_IMPLEMENTED_TYPES = {'blank', 'truefalse', 'short', 'compound', 'other'}


def _can_create_any(user):
    """The page is visible to anyone who can create at least one supported problem type."""
    return user.has_perm('judge.create_private_problem') or (user.is_staff and user.has_perm('judge.add_problem'))


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProblemCreateV2View(LoginRequiredMixin, View):
    template_name = 'problem/create_v2.html'

    # ------------------------------------------------------------------ GET
    def get(self, request, *args, **kwargs):
        if not _can_create_any(request.user):
            raise Http404()
        return render(request, self.template_name, self.get_context(request))

    def get_context(self, request):
        user = request.user
        profile = request.profile
        # Organizations the teacher may scope the problem to (their own memberships).
        orgs = list(profile.organizations.values('id', 'name')) if profile else []
        return {
            'title': _('Add problem'),
            'organizations': orgs,
            'groups': list(ProblemGroup.objects.order_by('full_name', 'name').values('id', 'full_name', 'name')),
            'languages': list(Language.objects.order_by('name', 'key').values('id', 'key', 'name')),
            'problem_types': list(ProblemType.objects.order_by('full_name', 'name').values('id', 'full_name', 'name')),
            'problem_types_json': json.dumps([
                {'name': t['name'], 'full': t['full_name'] or t['name']}
                for t in ProblemType.objects.order_by('full_name', 'name').values('name', 'full_name')
            ]),
            'can_create_code': user.is_staff and user.has_perm('judge.add_problem'),
            'can_create_mcq': user.has_perm('judge.create_private_problem'),
            'can_change_public': user.has_perm('judge.change_public_visibility'),
            'save_url': reverse('problem_create_v2'),
            'ai_difficulty_url': reverse('problem_create_v2_ai_difficulty'),
            'problem_list_url': reverse('problem_list'),
        }

    # ------------------------------------------------------------------ POST
    def post(self, request, *args, **kwargs):
        if not _can_create_any(request.user):
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': _('Invalid request.')}, status=400)

        qtype = payload.get('type')
        name = (payload.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': _('Please enter a problem name.')}, status=400)
        if qtype in NOT_IMPLEMENTED_TYPES:
            return JsonResponse({'error': _('This question type is under development and cannot be saved yet.')},
                                status=400)

        try:
            if qtype == 'programming':
                problem = self._create_code(request, payload, name)
            elif qtype in ('single', 'multiple'):
                problem = self._create_mcq(request, payload, name, multiple=(qtype == 'multiple'))
            else:
                return JsonResponse({'error': _('Unknown question type.')}, status=400)
        except PermissionError as e:
            return JsonResponse({'error': str(e)}, status=403)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        action = payload.get('action') or 'save'
        if action == 'another':
            return JsonResponse({'ok': True, 'code': problem.code})
        if action == 'continue' and request.user.is_staff:
            redirect = reverse('admin:judge_problem_change', args=[problem.id])
        else:
            redirect = reverse('problem_detail', args=[problem.code])
        return JsonResponse({'ok': True, 'code': problem.code, 'redirect': redirect})

    # ------------------------------------------------------------------ helpers
    def _num(self, value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _resolve_group(self, payload):
        gid = payload.get('group_id')
        if gid:
            group = ProblemGroup.objects.filter(pk=gid).first()
            if group:
                return group
        return default_problem_group()

    def _resolve_public(self, user, is_public, payload):
        """Mirror the admin/MCQ visibility rules; returns the effective is_public or raises."""
        if not is_public:
            return False
        if user.has_perm('judge.change_public_visibility'):
            return True
        org_private = bool(payload.get('organizations'))
        if not org_private:
            raise PermissionError(_('You do not have permission to make a problem public unless it is '
                                    'private to an organization.'))
        if not user.has_perm('judge.create_private_problem'):
            raise PermissionError(_('You do not have permission to make this problem public.'))
        return True

    def _attach_types(self, problem, payload):
        names = [n for n in (payload.get('problemTypes') or []) if n]
        if names:
            for t in ProblemType.objects.filter(Q(full_name__in=names) | Q(name__in=names)):
                problem.types.add(t)
        if not problem.types.exists():
            problem.types.add(default_problem_type())

    def _attach_orgs(self, problem, payload):
        ids = payload.get('organizations') or []
        if ids:
            problem.organizations.set(Organization.objects.filter(pk__in=ids))

    # ------------------------------------------------------------------ code problem
    def _create_code(self, request, payload, name):
        user = request.user
        if not (user.is_staff and user.has_perm('judge.add_problem')):
            raise PermissionError(_('You do not have permission to create code problems.'))

        testcases = payload.get('testcases') or []
        cleaned_cases = [tc for tc in testcases if (tc.get('input') or tc.get('output'))]
        if not cleaned_cases:
            raise ValueError(_('Please add at least one test case.'))

        with transaction.atomic():
            problem = Problem(submission_type=ProblemSubmissionType.CODE)
            problem.name = name
            problem.description = payload.get('description') or ''
            problem.points = self._num(payload.get('points'), 1)
            problem.partial = bool(payload.get('partial'))
            problem.time_limit = self._num(payload.get('time_limit'), 1)
            problem.memory_limit = int(self._num(payload.get('memory_limit'), 256) * 1024)  # MB -> KB
            problem.group = self._resolve_group(payload)
            apply_simple_problem_defaults(problem)  # fills code + any unset limits
            problem.is_public = self._resolve_public(user, bool(payload.get('is_public')), payload)
            problem.save()

            problem.authors.add(request.profile)
            self._attach_types(problem, payload)
            self._attach_languages(problem, payload)
            self._attach_orgs(problem, payload)
            problem.is_organization_private = problem.organizations.exists()
            problem.save(update_fields=['is_organization_private'])

            self._save_testcases(problem, cleaned_cases)
        return problem

    def _attach_languages(self, problem, payload):
        keys = payload.get('languages') or []
        qs = Language.objects.filter(key__in=keys) if keys else Language.objects.all()
        problem.allowed_languages.set(qs)

    def _save_testcases(self, problem, testcases):
        from judge.models import ProblemData, ProblemTestCase, problem_data_storage
        from judge.utils.problem_data import ProblemDataCompiler

        valid_files = []
        for order, tc in enumerate(testcases, start=1):
            in_name = '%02d.in' % order
            out_name = '%02d.out' % order
            problem_data_storage.save('%s/%s' % (problem.code, in_name),
                                      ContentFile((tc.get('input') or '').encode('utf-8')))
            problem_data_storage.save('%s/%s' % (problem.code, out_name),
                                      ContentFile((tc.get('output') or '').encode('utf-8')))
            valid_files += [in_name, out_name]
            ProblemTestCase.objects.create(
                dataset=problem, order=order, type='C',
                input_file=in_name, output_file=out_name,
                points=int(self._num(tc.get('points'), 0)),
                is_pretest=bool(tc.get('pretest')),
            )

        data, _created = ProblemData.objects.get_or_create(problem=problem)
        ProblemDataCompiler.generate(problem, data, problem.cases.order_by('order'), valid_files)

    # ------------------------------------------------------------------ mcq problem
    def _create_mcq(self, request, payload, name, multiple):
        user = request.user
        if not user.has_perm('judge.create_private_problem'):
            raise PermissionError(_('You do not have permission to create multiple-choice problems.'))

        options = [o for o in (payload.get('options') or []) if (o.get('text') or '').strip()]
        if len(options) < 2:
            raise ValueError(_('Please add at least two choices.'))
        correct = [o for o in options if o.get('correct')]
        if not correct:
            raise ValueError(_('Please mark at least one correct choice.'))
        if not multiple and len(correct) > 1:
            raise ValueError(_('Single-answer questions must have exactly one correct choice.'))

        with transaction.atomic():
            problem = Problem(submission_type=ProblemSubmissionType.MCQ)
            problem.name = name
            problem.description = payload.get('description') or ''
            problem.points = self._num(payload.get('points'), 1)
            problem.partial = bool(payload.get('partial')) if multiple else False
            problem.group = self._resolve_group(payload)
            apply_simple_problem_defaults(problem)
            problem.is_public = self._resolve_public(user, bool(payload.get('is_public')), payload)
            problem.save()

            problem.authors.add(request.profile)
            self._attach_types(problem, payload)
            self._attach_orgs(problem, payload)
            problem.is_organization_private = problem.organizations.exists()
            problem.save(update_fields=['is_organization_private'])

            ProblemMCQConfig.objects.create(
                problem=problem, multiple_select=multiple, shuffle_options=bool(payload.get('shuffle')))
            for index, option in enumerate(options):
                ProblemMCQOption.objects.create(
                    problem=problem, order=index,
                    text=(option.get('text') or '').strip(), is_correct=bool(option.get('correct')))
        return problem


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProblemCreateV2AIDifficultyView(LoginRequiredMixin, View):
    """Suggest Easy/Medium/Hard from problem text; uses LLM when configured, else a heuristic."""

    def post(self, request, *args, **kwargs):
        if not _can_create_any(request.user):
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': _('Invalid request.')}, status=400)

        description = (payload.get('description') or '').strip()
        if not description:
            return JsonResponse({'error': _('Please enter problem content first.')}, status=400)

        level = self._classify(description)
        return JsonResponse({'level': level, 'label': {'easy': 'Easy', 'medium': 'Medium', 'hard': 'Hard'}[level]})

    def _classify(self, description):
        from judge import llm

        if llm.is_configured():
            try:
                messages = [
                    {'role': 'system', 'content': (
                        'You classify educational/problem difficulty. Reply with exactly one word: '
                        'easy, medium, or hard. No other text.'
                    )},
                    {'role': 'user', 'content': description[:4000]},
                ]
                text = llm.chat(messages, max_tokens=16).text.strip().lower()
                for level in ('easy', 'medium', 'hard'):
                    if level in text:
                        return level
            except Exception:
                pass

        compact = description.replace(' ', '')
        if len(compact) < 80:
            return 'easy'
        if len(compact) < 240:
            return 'medium'
        return 'hard'

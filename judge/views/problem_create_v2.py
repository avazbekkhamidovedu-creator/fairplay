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
import re

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import View

from judge.models import (Language, Organization, Problem, ProblemBlank, ProblemFillBlankConfig, ProblemGroup,
                          ProblemMCQConfig, ProblemMCQOption, ProblemShortAnswerConfig, ProblemSubmissionType,
                          ProblemType)
from judge.models.question import GRADE_AI, GRADE_EXACT
from judge.utils.problem_create import apply_simple_problem_defaults, default_problem_group, default_problem_type

# Types the redesigned page offers but the backend cannot grade/store yet.
NOT_IMPLEMENTED_TYPES = {'compound', 'other'}


def _problem_type_code(text):
    code = re.sub(r'[^\w\-]+', '-', (text or '').strip().lower())[:20].strip('-')
    return code or 'type'


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
        context = self.get_context(request)
        edit_code = kwargs.get('problem')
        if edit_code:
            try:
                problem = self._get_editable_problem(request, edit_code)
            except PermissionError:
                raise Http404()
            if problem is None:
                raise Http404()
            context['title'] = _('Edit problem')
            context['edit_code'] = problem.code
            context['edit_data_json'] = json.dumps(self._build_edit_data(problem))
            context['test_data_url'] = reverse('problem_data', args=[problem.code])
            context['edit_detail_url'] = reverse('problem_detail', args=[problem.code])
        return render(request, self.template_name, context)

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
            'default_group_id': default_problem_group().id,
            'save_url': reverse('problem_create_v2'),
            'ai_difficulty_url': reverse('problem_create_v2_ai_difficulty'),
            'create_problem_type_url': reverse('problem_create_v2_problem_type'),
            'create_problem_group_url': reverse('problem_create_v2_problem_group'),
            'create_organization_url': reverse('problem_create_v2_organization'),
            'can_create_organization': user.has_perm('judge.create_private_problem'),
            'ai_problem_type_url': reverse('problem_create_v2_ai_problem_type'),
            'problem_list_url': reverse('problem_list'),
            'problem_preview_url': reverse('problem_preview'),
            'problem_preview_batch_url': reverse('problem_preview_batch'),
            'image_upload_url': reverse('martor_image_uploader'),
            'search_users_url': reverse('martor_search_user'),
            'edit_code': '',
            'edit_data_json': 'null',
            'test_data_url': '',
            'edit_detail_url': '',
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

        # When ``edit_code`` is present we update the existing problem in place (keeping its code/URL)
        # instead of creating a new one; this powers "Save and continue editing".
        existing = None
        edit_code = (payload.get('edit_code') or '').strip()
        if edit_code:
            try:
                existing = self._get_editable_problem(request, edit_code)
            except PermissionError as e:
                return JsonResponse({'error': str(e)}, status=403)
            if existing is None:
                return JsonResponse({'error': _('Problem not found.')}, status=404)

        try:
            if qtype == 'programming':
                problem = self._create_code(request, payload, name, problem=existing)
            elif qtype in ('single', 'multiple'):
                problem = self._create_mcq(request, payload, name, multiple=(qtype == 'multiple'), problem=existing)
            elif qtype == 'truefalse':
                problem = self._create_truefalse(request, payload, name, problem=existing)
            elif qtype == 'blank':
                problem = self._create_fill_blank(request, payload, name, problem=existing)
            elif qtype == 'short':
                problem = self._create_short_answer(request, payload, name, problem=existing)
            else:
                return JsonResponse({'error': _('Unknown question type.')}, status=400)
        except PermissionError as e:
            return JsonResponse({'error': str(e)}, status=403)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        action = payload.get('action') or 'save'
        if action == 'another':
            # Land on a fresh add-problem page.
            return JsonResponse({'ok': True, 'code': problem.code, 'redirect': reverse('problem_create_v2')})
        if action == 'continue':
            # Reopen this problem on the add page for further edits.
            redirect = reverse('problem_edit_v2', args=[problem.code])
        else:
            redirect = reverse('problem_detail', args=[problem.code])
        return JsonResponse({'ok': True, 'code': problem.code, 'redirect': redirect})

    # ------------------------------------------------------------------ edit support
    def _get_editable_problem(self, request, code):
        problem = Problem.objects.filter(code=code).first()
        if problem is None:
            return None
        profile = request.profile
        can_edit = problem.is_editable_by(request.user) or (
            profile is not None and problem.authors.filter(id=profile.id).exists())
        if not can_edit:
            raise PermissionError(_('You do not have permission to edit this problem.'))
        return problem

    def _clear_type_children(self, problem):
        """Remove every question-type-specific child before repopulating on edit (also handles a
        type switch, e.g. single -> programming)."""
        ProblemMCQOption.objects.filter(problem=problem).delete()
        ProblemMCQConfig.objects.filter(problem=problem).delete()
        ProblemBlank.objects.filter(problem=problem).delete()
        ProblemFillBlankConfig.objects.filter(problem=problem).delete()
        ProblemShortAnswerConfig.objects.filter(problem=problem).delete()
        problem.cases.all().delete()

    def _build_edit_data(self, problem):
        """Serialize an existing problem into the shape the add-page JS expects (see collectData)."""
        st = problem.submission_type
        data = {
            'name': problem.name,
            'organizations': list(problem.organizations.values_list('id', flat=True)),
            'group_id': problem.group_id,
            'points': problem.points,
            'is_public': problem.is_public,
            'partial': problem.partial,
            'description': problem.description or '',
            'ai': False,
            'rubric': '',
        }

        diff_labels = {'Easy': 'easy', 'Medium': 'medium', 'Hard': 'hard'}
        type_tags, difficulty = [], ''
        for full in problem.types.values_list('full_name', 'name'):
            label = full[0] or full[1]
            if label in diff_labels:
                difficulty = diff_labels[label]
            else:
                type_tags.append(label)
        data['problemTypes'] = type_tags
        data['difficulty'] = difficulty or 'medium'

        if st == ProblemSubmissionType.CODE:
            data['type'] = 'programming'
            data['time_limit'] = problem.time_limit
            data['memory_limit'] = round((problem.memory_limit or 0) / 1024, 2)  # KB -> MB
            data['languages'] = list(problem.allowed_languages.values_list('key', flat=True))
            data['testcases'] = self._read_testcases(problem)
        elif st == ProblemSubmissionType.MCQ:
            config = getattr(problem, 'mcq_config', None)
            options = list(problem.mcq_options.order_by('order').values('text', 'is_correct'))
            if config is not None and config.is_true_false:
                data['type'] = 'truefalse'
                correct_true = next((o for o in options if o['is_correct']), None)
                data['tfAnswer'] = 'true' if (correct_true and correct_true is options[0]) else 'false'
            else:
                data['type'] = 'multiple' if (config and config.multiple_select) else 'single'
                data['multiple'] = bool(config and config.multiple_select)
                data['options'] = [{'text': o['text'], 'correct': o['is_correct']} for o in options]
        elif st == ProblemSubmissionType.FILL_BLANK:
            data['type'] = 'blank'
            config = getattr(problem, 'fill_blank_config', None)
            data['caseSensitive'] = bool(config and config.case_sensitive)
            data['trimWhitespace'] = bool(config.trim_whitespace) if config else True
            data['ai'] = bool(config and config.grading_mode == GRADE_AI)
            data['rubric'] = (config.ai_rubric if config else '') or ''
            # ProblemBlank stores accepted answers one-per-line and the create page now edits them
            # the same way (one accepted answer per line in a textarea), so pass them through as-is.
            data['blanks'] = list(problem.blanks.order_by('order').values_list('answers', flat=True))
        elif st == ProblemSubmissionType.SHORT_ANSWER:
            data['type'] = 'short'
            config = getattr(problem, 'short_answer_config', None)
            data['shortAnswer'] = (config.reference_answer if config else '') or ''
            data['rubric'] = (config.ai_rubric if config else '') or ''
            data['ai'] = bool(config and (config.ai_rubric or '').strip())
        else:
            data['type'] = 'single'
        return data

    def _read_testcases(self, problem):
        from zipfile import BadZipfile, ZipFile

        from judge.models import ProblemData, problem_data_storage

        zip_file = None
        zip_names = set()
        try:
            data = problem.data_files
        except ProblemData.DoesNotExist:
            data = None
        if data and data.zipfile:
            try:
                zip_file = ZipFile(data.zipfile.path, 'r')
                zip_names = set(zip_file.namelist())
            except (BadZipfile, OSError, ValueError):
                zip_file = None
                zip_names = set()

        def _read_loose(name):
            if not name:
                return ''
            try:
                with problem_data_storage.open('%s/%s' % (problem.code, name)) as fh:
                    return fh.read().decode('utf-8', 'replace')
            except Exception:
                return ''

        def _read_zip(name):
            if not name or not zip_file:
                return ''
            candidates = [name]
            if name not in zip_names:
                candidates.extend(n for n in zip_names if n.endswith('/' + name))
            for candidate in candidates:
                if candidate not in zip_names:
                    continue
                try:
                    return zip_file.read(candidate).decode('utf-8', 'replace')
                except Exception:
                    return ''
            return ''

        def _read(name):
            if not name:
                return ''
            loose_path = '%s/%s' % (problem.code, name)
            if problem_data_storage.exists(loose_path):
                return _read_loose(name)
            return _read_zip(name)

        try:
            cases = []
            for case in problem.cases.order_by('order'):
                # create-v2 only edits Normal cases; Batch start/end belong on test_data.
                if case.type in ('S', 'E'):
                    continue
                cases.append({
                    'input': _read(case.input_file),
                    'output': _read(case.output_file),
                    'points': case.points,
                    'pretest': case.is_pretest,
                })
            return cases
        finally:
            if zip_file is not None:
                zip_file.close()

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
        types = list(ProblemType.objects.filter(Q(full_name__in=names) | Q(name__in=names))) if names else []
        if types:
            problem.types.set(types)
        elif not problem.types.exists():
            problem.types.add(default_problem_type())

    def _attach_orgs(self, problem, payload):
        ids = payload.get('organizations') or []
        problem.organizations.set(Organization.objects.filter(pk__in=ids))

    # ------------------------------------------------------------------ code problem
    def _create_code(self, request, payload, name, problem=None):
        user = request.user
        if not (user.is_staff and user.has_perm('judge.add_problem')):
            raise PermissionError(_('You do not have permission to create code problems.'))

        # Test data is configured through the embedded test_data page after the problem exists,
        # so inline cases are optional here (kept for backwards compatibility / API callers).
        testcases = payload.get('testcases') or []
        cleaned_cases = [tc for tc in testcases if (tc.get('input') or tc.get('output'))]

        with transaction.atomic():
            if problem is None:
                problem = Problem(submission_type=ProblemSubmissionType.CODE)
            else:
                problem.submission_type = ProblemSubmissionType.CODE
                self._clear_type_children(problem)
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

            if cleaned_cases:
                self._save_testcases(problem, cleaned_cases)
        return problem

    def _attach_languages(self, problem, payload):
        # No selection (or an all-invalid selection) means "all languages allowed" so the problem
        # is never left unsubmittable; DMOJ treats an empty allowed_languages set as no languages.
        keys = [k for k in (payload.get('languages') or []) if k]
        qs = Language.objects.filter(key__in=keys) if keys else Language.objects.none()
        if not qs.exists():
            qs = Language.objects.all()
        problem.allowed_languages.set(qs)

    def _clear_problem_data_files(self, problem):
        """Drop leftover zip/archive and old loose case files before rewriting from create-v2."""
        import os

        from judge.models import ProblemData, problem_data_storage

        try:
            data = problem.data_files
        except ProblemData.DoesNotExist:
            data = None
        if data and data.zipfile:
            # ProblemData.save() deletes the previous file when zipfile changes.
            data.zipfile = None
            data.save()

        root = getattr(problem_data_storage, 'location', None) or ''
        problem_dir = os.path.join(root, problem.code) if root else ''
        if problem_dir and os.path.isdir(problem_dir):
            for name in os.listdir(problem_dir):
                lower = name.lower()
                if lower.endswith('.in') or lower.endswith('.out') or lower.endswith('.ans'):
                    try:
                        problem_data_storage.delete('%s/%s' % (problem.code, name))
                    except Exception:
                        pass

    def _save_testcases(self, problem, testcases):
        from judge.models import ProblemData, ProblemTestCase, problem_data_storage
        from judge.utils.problem_data import ProblemDataCompiler

        self._clear_problem_data_files(problem)

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
        if data.zipfile:
            data.zipfile = None
            data.save()
        ProblemDataCompiler.generate(problem, data, problem.cases.order_by('order'), valid_files)

    # ------------------------------------------------------------------ mcq problem
    def _create_mcq(self, request, payload, name, multiple, problem=None):
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
            if problem is None:
                problem = Problem(submission_type=ProblemSubmissionType.MCQ)
            else:
                problem.submission_type = ProblemSubmissionType.MCQ
                self._clear_type_children(problem)
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

    # ------------------------------------------------------------------ true / false
    def _create_truefalse(self, request, payload, name, problem=None):
        """True/false is stored as a two-option single-choice MCQ (正确 / 错误)."""
        user = request.user
        if not user.has_perm('judge.create_private_problem'):
            raise PermissionError(_('You do not have permission to create this problem type.'))
        answer = (payload.get('tfAnswer') or 'true').strip().lower()
        if answer not in ('true', 'false'):
            raise ValueError(_('Please choose the correct answer.'))

        with transaction.atomic():
            if problem is None:
                problem = Problem(submission_type=ProblemSubmissionType.MCQ)
            else:
                problem.submission_type = ProblemSubmissionType.MCQ
                self._clear_type_children(problem)
            problem.name = name
            problem.description = payload.get('description') or ''
            problem.points = self._num(payload.get('points'), 1)
            problem.partial = False
            problem.group = self._resolve_group(payload)
            apply_simple_problem_defaults(problem)
            problem.is_public = self._resolve_public(user, bool(payload.get('is_public')), payload)
            problem.save()

            problem.authors.add(request.profile)
            self._attach_types(problem, payload)
            self._attach_orgs(problem, payload)
            problem.is_organization_private = problem.organizations.exists()
            problem.save(update_fields=['is_organization_private'])

            ProblemMCQConfig.objects.create(problem=problem, multiple_select=False, shuffle_options=False,
                                            is_true_false=True)
            ProblemMCQOption.objects.create(problem=problem, order=0, text=_('True'), is_correct=(answer == 'true'))
            ProblemMCQOption.objects.create(problem=problem, order=1, text=_('False'), is_correct=(answer == 'false'))
        return problem

    # ------------------------------------------------------------------ fill in the blank
    def _create_fill_blank(self, request, payload, name, problem=None):
        user = request.user
        if not user.has_perm('judge.create_private_problem'):
            raise PermissionError(_('You do not have permission to create this problem type.'))
        blanks = [b for b in (payload.get('blanks') or []) if (b or '').strip()]
        if not blanks:
            raise ValueError(_('Please provide at least one blank answer.'))

        with transaction.atomic():
            if problem is None:
                problem = Problem(submission_type=ProblemSubmissionType.FILL_BLANK)
            else:
                problem.submission_type = ProblemSubmissionType.FILL_BLANK
                self._clear_type_children(problem)
            problem.name = name
            problem.description = payload.get('description') or ''
            problem.points = self._num(payload.get('points'), 1)
            problem.partial = bool(payload.get('partial'))
            problem.group = self._resolve_group(payload)
            apply_simple_problem_defaults(problem)
            problem.is_public = self._resolve_public(user, bool(payload.get('is_public')), payload)
            problem.save()

            problem.authors.add(request.profile)
            self._attach_types(problem, payload)
            self._attach_orgs(problem, payload)
            problem.is_organization_private = problem.organizations.exists()
            problem.save(update_fields=['is_organization_private'])

            ProblemFillBlankConfig.objects.create(
                problem=problem,
                case_sensitive=bool(payload.get('caseSensitive')),
                trim_whitespace=bool(payload.get('trimWhitespace', True)),
                grading_mode=GRADE_AI if bool(payload.get('ai')) else GRADE_EXACT,
                ai_rubric=(payload.get('rubric') or '').strip(),
            )
            # The create page enters accepted answers one-per-line per blank; ProblemBlank stores
            # them the same way (the grader splits on newlines).
            for index, answers in enumerate(blanks):
                normalized = '\n'.join(line.strip() for line in answers.splitlines() if line.strip())
                ProblemBlank.objects.create(problem=problem, order=index, answers=normalized)
        return problem

    # ------------------------------------------------------------------ short answer
    def _create_short_answer(self, request, payload, name, problem=None):
        user = request.user
        if not user.has_perm('judge.create_private_problem'):
            raise PermissionError(_('You do not have permission to create this problem type.'))

        with transaction.atomic():
            if problem is None:
                problem = Problem(submission_type=ProblemSubmissionType.SHORT_ANSWER)
            else:
                problem.submission_type = ProblemSubmissionType.SHORT_ANSWER
                self._clear_type_children(problem)
            problem.name = name
            problem.description = payload.get('description') or ''
            problem.points = self._num(payload.get('points'), 1)
            # AI grading returns a 0-100 percentage, so partial credit must be on for it to count.
            problem.partial = True
            problem.group = self._resolve_group(payload)
            apply_simple_problem_defaults(problem)
            problem.is_public = self._resolve_public(user, bool(payload.get('is_public')), payload)
            problem.save()

            problem.authors.add(request.profile)
            self._attach_types(problem, payload)
            self._attach_orgs(problem, payload)
            problem.is_organization_private = problem.organizations.exists()
            problem.save(update_fields=['is_organization_private'])

            ProblemShortAnswerConfig.objects.create(
                problem=problem,
                reference_answer=(payload.get('shortAnswer') or '').strip(),
                ai_rubric=(payload.get('rubric') or '').strip(),
            )
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


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProblemCreateV2ProblemTypeView(LoginRequiredMixin, View):
    """Create a ProblemType from the add-problem page (code and/or display name)."""

    def post(self, request, *args, **kwargs):
        if not _can_create_any(request.user):
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': _('Invalid request.')}, status=400)

        name = (payload.get('name') or '').strip()
        full_name = (payload.get('full_name') or '').strip()
        if not name and not full_name:
            return JsonResponse({'error': _('Please enter a category ID or name.')}, status=400)
        if not name:
            name = _problem_type_code(full_name)
        if not full_name:
            full_name = name
        if len(name) > 20:
            return JsonResponse({'error': _('Category ID must be at most 20 characters.')}, status=400)
        if len(full_name) > 100:
            return JsonResponse({'error': _('Category name must be at most 100 characters.')}, status=400)
        if ProblemType.objects.filter(name=name).exists():
            return JsonResponse({'error': _('This category ID already exists.')}, status=400)

        ptype = ProblemType.objects.create(name=name, full_name=full_name)
        return JsonResponse({'ok': True, 'type': {'name': ptype.name, 'full': ptype.full_name}})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProblemCreateV2ProblemGroupView(LoginRequiredMixin, View):
    """Create a ProblemGroup from the add-problem page (accepts a display name)."""

    def post(self, request, *args, **kwargs):
        if not _can_create_any(request.user):
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': _('Invalid request.')}, status=400)

        full_name = (payload.get('full_name') or '').strip()
        name = (payload.get('name') or '').strip()
        if not full_name and not name:
            return JsonResponse({'error': _('Please enter a group name.')}, status=400)
        if not full_name:
            full_name = name
        if not name:
            name = _problem_type_code(full_name)
        if len(name) > 20:
            return JsonResponse({'error': _('Group ID must be at most 20 characters.')}, status=400)
        if len(full_name) > 100:
            return JsonResponse({'error': _('Group name must be at most 100 characters.')}, status=400)
        if ProblemGroup.objects.filter(name=name).exists():
            # Keep the code unique by suffixing a short number when it collides.
            base = name[:16]
            for i in range(2, 100):
                candidate = '%s-%d' % (base, i)
                if not ProblemGroup.objects.filter(name=candidate).exists():
                    name = candidate
                    break
            else:
                return JsonResponse({'error': _('This group already exists.')}, status=400)

        group = ProblemGroup.objects.create(name=name, full_name=full_name)
        return JsonResponse({'ok': True, 'group': {'id': group.id, 'full': group.full_name or group.name}})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProblemCreateV2OrganizationView(LoginRequiredMixin, View):
    """Create an Organization from the add-problem page and add the current user as admin/member.

    Only users who can create private (organization) problems may make new organizations here; the
    creator is added both as an administrator and a member so it appears in their scoped list.
    """

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm('judge.create_private_problem'):
            return JsonResponse({'error': _('Permission denied.')}, status=403)
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': _('Invalid request.')}, status=400)

        name = (payload.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': _('Please enter an organization name.')}, status=400)
        if len(name) > 128:
            return JsonResponse({'error': _('Organization name must be at most 128 characters.')}, status=400)

        slug = slugify(name)[:128] or 'org'
        short_name = name[:20]
        with transaction.atomic():
            org = Organization.objects.create(name=name, slug=slug, short_name=short_name, about='')
            profile = request.profile
            if profile:
                org.admins.add(profile)
                profile.organizations.add(org)
        return JsonResponse({'ok': True, 'organization': {'id': org.id, 'name': org.name}})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ProblemCreateV2AIProblemTypeView(LoginRequiredMixin, View):
    """Suggest problem_type tags from problem text; uses LLM when configured."""

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

        suggestions = self._suggest(description)
        return JsonResponse({'suggestions': suggestions})

    def _suggest(self, description):
        from judge import llm

        existing = list(ProblemType.objects.order_by('full_name', 'name').values('name', 'full_name'))
        labels = [t['full_name'] or t['name'] for t in existing]

        if llm.is_configured():
            try:
                messages = [
                    {'role': 'system', 'content': (
                        'You classify competitive-programming / educational problems. '
                        'Reply with 1-3 short category labels from this list when possible, '
                        'comma-separated. If none fit, suggest one new short label (≤20 chars). '
                        'Existing labels: ' + ', '.join(labels[:80])
                    )},
                    {'role': 'user', 'content': description[:4000]},
                ]
                text = llm.chat(messages, max_tokens=64).text.strip()
                picked = []
                for part in text.replace('\n', ',').split(','):
                    label = part.strip().strip('.')
                    if not label:
                        continue
                    match = next((t for t in existing if label.lower() in (t['full_name'] or '').lower()
                                  or label.lower() in t['name'].lower()), None)
                    if match:
                        picked.append({'name': match['name'], 'full': match['full_name'] or match['name'],
                                       'existing': True})
                    elif len(label) <= 100:
                        code = label[:20].replace(' ', '-').lower()
                        picked.append({'name': code, 'full': label, 'existing': False})
                    if len(picked) >= 3:
                        break
                if picked:
                    return picked
            except Exception:
                pass

        compact = description.lower()
        picked = []
        for t in existing:
            for token in (t['full_name'] or '', t['name']):
                if token and token.lower() in compact:
                    picked.append({'name': t['name'], 'full': t['full_name'] or t['name'], 'existing': True})
                    break
            if len(picked) >= 3:
                break
        if picked:
            return picked
        return [{'name': 'general', 'full': 'General', 'existing': False}]

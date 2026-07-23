"""Front-end create + edit flow for file-upload problems, plus the student download and teacher
inline-grading views.

Authoring mirrors ``judge/views/fill_blank.py`` (one view drives create and edit, reusing the same
perms and ``apply_simple_problem_defaults``) but has no answer formset — a file problem only needs
its per-problem config. Uploaded files are graded manually by a teacher, so grading goes through
``finalize_submission`` (the single source of truth), never a Django-admin points edit.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import View

from judge.models import Problem, ProblemFileConfig, ProblemSubmissionType, Submission
from judge.storage import StorageUnavailableError
from judge.submission_grading import finalize_submission
from judge.utils.problem_create import apply_simple_problem_defaults, default_problem_type
from judge.widgets import HeavySelect2MultipleWidget, MartorWidget


class ProblemFileForm(forms.ModelForm):
    class Meta:
        model = Problem
        fields = ['name', 'description', 'points', 'partial', 'is_public', 'organizations', 'classes']
        widgets = {
            'description': MartorWidget(attrs={'data-markdownfy-url': reverse_lazy('problem_preview')}),
            'organizations': HeavySelect2MultipleWidget(data_view='organization_select2'),
            'classes': HeavySelect2MultipleWidget(data_view='class_select2'),
        }
        labels = {
            'description': _('Question prompt'),
            'partial': _('Allow partial credit (award any score up to the maximum)'),
        }

    class Media:
        # Dependent select2: filters the class dropdown by the chosen organization(s).
        js = ('admin_class_filter.js',)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['organizations'].required = False
        self.fields['classes'].required = False

    def clean(self):
        # Mirror admin ProblemForm.clean: classes must belong to the selected organizations.
        cleaned_data = super().clean()
        organizations = cleaned_data.get('organizations')
        classes = cleaned_data.get('classes')
        if classes and organizations:
            org_ids = set(organizations.values_list('id', flat=True))
            if classes.exclude(organization_id__in=org_ids).exists():
                raise forms.ValidationError(_('Selected classes must belong to selected organizations.'))
        elif classes and not organizations:
            raise forms.ValidationError(_('You must select organizations before selecting classes.'))

        # Mirror admin ProblemAdmin.save_model: without change_public_visibility, a problem may
        # only be made public if it is organization-private and the user can create private problems.
        if cleaned_data.get('is_public') and self.user is not None \
                and not self.user.has_perm('judge.change_public_visibility'):
            org_private = bool(organizations or classes)
            if not org_private:
                self.add_error('is_public', _('You do not have permission to make a problem '
                                              'public unless it is private to an organization.'))
            elif not self.user.has_perm('judge.create_private_problem'):
                self.add_error('is_public', _('You do not have permission to make this problem public.'))
        return cleaned_data


class FileConfigForm(forms.ModelForm):
    class Meta:
        model = ProblemFileConfig
        fields = ['allowed_extensions', 'max_file_size', 'instructions']
        widgets = {
            'instructions': forms.Textarea(attrs={'rows': 3}),
        }


class ProblemFileEditView(LoginRequiredMixin, View):
    template_name = 'problem/file_edit.html'

    def get_problem(self):
        """Return the problem being edited, or None in create mode. Enforces permissions."""
        code = self.kwargs.get('problem')
        if code is None:
            if not self.request.user.has_perm('judge.create_private_problem'):
                raise Http404()
            return None
        problem = get_object_or_404(Problem, code=code)
        if not (self.request.user.is_superuser or problem.is_editable_by(self.request.user)):
            raise Http404()
        if problem.submission_type != ProblemSubmissionType.FILE:
            raise Http404()
        return problem

    def build_forms(self, problem, data=None):
        creating = problem is None
        config = None if creating else _config_or_none(problem)
        initial = None
        if creating and self.request.user.is_authenticated:
            # Default new file problems to the creator's organizations and partial credit.
            initial = {'organizations': list(self.request.profile.organizations.all()), 'partial': True}
        problem_form = ProblemFileForm(data, instance=problem, initial=initial, user=self.request.user)
        config_form = FileConfigForm(data, instance=config)
        return problem_form, config_form

    def get(self, request, *args, **kwargs):
        problem = self.get_problem()
        problem_form, config_form = self.build_forms(problem)
        return self.render(request, problem, problem_form, config_form)

    def post(self, request, *args, **kwargs):
        problem = self.get_problem()
        creating = problem is None
        problem_form, config_form = self.build_forms(problem, data=request.POST)

        if problem_form.is_valid() & config_form.is_valid():
            with transaction.atomic():
                problem = problem_form.save(commit=False)
                if creating:
                    problem.submission_type = ProblemSubmissionType.FILE
                    apply_simple_problem_defaults(problem)
                problem.save()
                problem_form.save_m2m()
                if creating:
                    problem.types.add(default_problem_type())
                    problem.authors.add(request.profile)
                # Visibility is inherited from the org selector: org-private iff orgs are chosen.
                problem.is_organization_private = problem.organizations.exists()
                problem.save(update_fields=['is_organization_private'])

                config = config_form.save(commit=False)
                config.problem = problem
                config.save()

            if creating:
                return redirect('problem_detail', problem.code)
            messages.success(request, _('Saved “%s”.') % problem.name)
            return redirect('problem_file_edit', problem.code)

        return self.render(request, problem, problem_form, config_form)

    def render(self, request, problem, problem_form, config_form):
        page_title = _('New file-upload question') if problem is None else _('Edit %s') % problem.name
        return render(request, self.template_name, {
            'problem': problem,
            'creating': problem is None,
            'form': problem_form,
            'config_form': config_form,
            'title': page_title,
            'content_title': page_title,
        })


def _config_or_none(problem):
    try:
        return problem.file_config
    except ProblemFileConfig.DoesNotExist:
        return None


def submission_file_download(request, submission):
    """Stream a submission's uploaded file through Django (private storage, no presigned URLs)."""
    sub = get_object_or_404(Submission, id=submission)
    if not sub.can_see_detail(request.user):
        raise PermissionDenied()
    upload = getattr(sub, 'upload', None)
    if upload is None:
        raise Http404()
    try:
        fh = upload.file.open('rb')
    except StorageUnavailableError:
        return HttpResponse(_('The file store is temporarily unavailable. Please try again later.'),
                            status=503, content_type='text/plain')
    # Force attachment + a neutral content-type: uploaded HTML/SVG must never render inline (stored XSS).
    return FileResponse(fh, as_attachment=True, filename=upload.original_name,
                        content_type='application/octet-stream')


@require_POST
def submission_file_grade(request, submission):
    """Teacher inline grading for a file-upload submission."""
    sub = get_object_or_404(Submission.objects.select_related('problem'), id=submission)
    if not (request.user.is_authenticated and sub.problem.is_editable_by(request.user)):
        raise PermissionDenied()
    if sub.problem.submission_type != ProblemSubmissionType.FILE:
        raise Http404()

    max_points = sub.problem.points
    try:
        points = float(request.POST.get('points', ''))
    except (TypeError, ValueError):
        messages.error(request, _('Enter a valid number of points.'))
        return redirect('submission_status', sub.id)
    points = max(0.0, min(points, max_points))
    feedback = (request.POST.get('feedback') or '').strip() or None

    finalize_submission(
        sub, case_points=points, case_total=max_points,
        result='AC' if points >= max_points else 'WA',
        status='D', error=feedback,
    )
    messages.success(request, _('Graded submission #%d.') % sub.id)
    return redirect('submission_status', sub.id)

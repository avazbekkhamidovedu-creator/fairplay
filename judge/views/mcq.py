"""Front-end create + edit flow for multiple-choice problems.

A single view drives both creating a brand-new MCQ problem (no admin round-trip) and editing an
existing one. Permissions reuse the existing problem perms: ``judge.create_private_problem`` to
create, ``Problem.is_editable_by`` to edit. Code-centric required fields are auto-filled via
``apply_simple_problem_defaults`` so teachers only ever see the prompt, choices, and settings.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.forms import inlineformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import View

from judge.models import Problem, ProblemMCQConfig, ProblemMCQOption, ProblemSubmissionType
from judge.utils.problem_create import apply_simple_problem_defaults, default_problem_type
from judge.widgets import HeavySelect2MultipleWidget, MartorWidget


class ProblemMCQForm(forms.ModelForm):
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
            'partial': _('Allow partial credit (multiple-answer questions)'),
        }

    class Media:
        # Dependent select2: filters the class dropdown by the chosen organization(s). The script
        # falls back from django.jQuery to the global jQuery, so it works outside the admin too.
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


class MCQConfigForm(forms.ModelForm):
    class Meta:
        model = ProblemMCQConfig
        fields = ['multiple_select', 'shuffle_options']


def make_option_formset(extra):
    return inlineformset_factory(
        Problem, ProblemMCQOption,
        fields=['text', 'is_correct', 'order'], extra=extra, can_delete=True,
        widgets={
            'order': forms.HiddenInput(attrs={'class': 'option-order'}),
            'text': forms.Textarea(attrs={'rows': 2, 'class': 'option-text'}),
        },
    )


class ProblemMCQEditView(LoginRequiredMixin, View):
    template_name = 'problem/mcq_edit.html'

    def get_problem(self):
        """Return the problem being edited, or None in create mode. Enforces permissions."""
        code = self.kwargs.get('problem')
        if code is None:
            if not self.request.user.has_perm('judge.create_private_problem'):
                raise Http404()
            return None
        problem = get_object_or_404(Problem, code=code)
        # Deliberately NOT applying the is_manually_managed 404 from ProblemManagerMixin: that
        # guard is a code-judge concept and irrelevant to MCQ.
        if not (self.request.user.is_superuser or problem.is_editable_by(self.request.user)):
            raise Http404()
        if problem.submission_type != ProblemSubmissionType.MCQ:
            raise Http404()
        return problem

    def build_forms(self, problem, data=None):
        creating = problem is None
        config = None if creating else _get_or_none(problem)
        # Default a brand-new MCQ to the creator's organizations (preserves the old auto-scope
        # behaviour when the teacher leaves the selector untouched); the field stays editable.
        initial = None
        if creating and self.request.user.is_authenticated:
            initial = {'organizations': list(self.request.profile.organizations.all())}
        problem_form = ProblemMCQForm(data, instance=problem, initial=initial, user=self.request.user)
        config_form = MCQConfigForm(data, instance=config)
        # On edit, start with no blank trailing row; the "Add choice" button adds rows on demand.
        OptionFormSet = make_option_formset(extra=4 if creating else 0)
        formset = OptionFormSet(data, instance=problem if problem is not None else Problem(), prefix='options')
        return problem_form, config_form, formset

    def get(self, request, *args, **kwargs):
        problem = self.get_problem()
        problem_form, config_form, formset = self.build_forms(problem)
        return self.render(request, problem, problem_form, config_form, formset)

    def post(self, request, *args, **kwargs):
        problem = self.get_problem()
        creating = problem is None
        problem_form, config_form, formset = self.build_forms(problem, data=request.POST)

        forms_valid = problem_form.is_valid() & config_form.is_valid() & formset.is_valid()
        option_error = self.validate_options(formset, config_form) if forms_valid else None

        if forms_valid and option_error is None:
            with transaction.atomic():
                problem = problem_form.save(commit=False)
                if creating:
                    problem.submission_type = ProblemSubmissionType.MCQ
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

                self._save_options(formset, problem)

            if creating:
                # Land the teacher on the new problem itself.
                return redirect('problem_detail', problem.code)
            messages.success(request, _('Saved “%s”.') % problem.name)
            return redirect('problem_mcq_edit', problem.code)

        return self.render(request, problem, problem_form, config_form, formset, option_error=option_error)

    def validate_options(self, formset, config_form):
        rows = [f for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('text')]
        if len(rows) < 2:
            return _('Add at least two choices.')
        correct = [f for f in rows if f.cleaned_data.get('is_correct')]
        if not correct:
            return _('Mark at least one choice as correct.')
        multiple = config_form.cleaned_data.get('multiple_select')
        if not multiple and len(correct) > 1:
            return _('Single-answer questions must have exactly one correct choice.')
        return None

    def _save_options(self, formset, problem):
        # Re-sequence order from the (drag-maintained) hidden order field, then save.
        formset.instance = problem
        formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        kept = [f for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('text')]
        kept.sort(key=lambda f: f.cleaned_data.get('order') or 0)
        for index, f in enumerate(kept):
            option = f.instance
            option.problem = problem
            option.order = index
            option.save()

    def render(self, request, problem, problem_form, config_form, formset, option_error=None):
        page_title = _('New multiple-choice question') if problem is None else _('Edit %s') % problem.name
        return render(request, self.template_name, {
            'problem': problem,
            'creating': problem is None,
            'form': problem_form,
            'config_form': config_form,
            'formset': formset,
            'option_error': option_error,
            'title': page_title,
            'content_title': page_title,
        })


def _get_or_none(problem):
    try:
        return problem.mcq_config
    except ProblemMCQConfig.DoesNotExist:
        return None

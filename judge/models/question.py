from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem

__all__ = [
    'ProblemMCQOption', 'ProblemMCQConfig', 'ProblemBlank', 'ProblemFillBlankConfig',
    'ProblemImportDraft', 'ProblemImportItem',
]


class ProblemImportDraft(models.Model):
    """A server-side Excel import preview owned by one teacher."""
    owner = models.ForeignKey('judge.Profile', related_name='problem_import_drafts', on_delete=CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-updated_at',)
        verbose_name = _('problem import draft')
        verbose_name_plural = _('problem import drafts')


class ProblemImportItem(models.Model):
    """One editable row in an Excel import preview."""
    TYPE_CHOICES = (
        ('C', _('Code')),
        ('M', _('Multiple choice')),
        ('B', _('Fill in the blank')),
    )

    draft = models.ForeignKey(ProblemImportDraft, related_name='items', on_delete=CASCADE)
    row_number = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    submission_type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    description = models.TextField(blank=True)
    answer = models.TextField(blank=True)
    solution = models.TextField(blank=True)
    allowed_languages = models.TextField(blank=True)
    test_data = models.TextField(blank=True)
    test_data_zip = models.FileField(upload_to='problem_import_test_data/', blank=True)
    source_label = models.CharField(max_length=255, blank=True)
    options = models.TextField(blank=True)
    correct_options = models.TextField(blank=True)
    blanks = models.TextField(blank=True)
    formatting_error = models.TextField(blank=True)
    created_problem = models.ForeignKey(Problem, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('row_number', 'pk')
        verbose_name = _('problem import item')
        verbose_name_plural = _('problem import items')


class ProblemMCQOption(models.Model):
    """A single multiple-choice option. The MCQ analog of a ProblemTestCase.

    ``is_correct`` is author-only and must never be serialized into anything served to students.
    """
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='mcq_options', on_delete=CASCADE)
    order = models.PositiveIntegerField(verbose_name=_('order'))
    text = models.TextField(verbose_name=_('choice text'))
    is_correct = models.BooleanField(verbose_name=_('is correct'), default=False)

    def __str__(self):
        return f'{self.problem.code} #{self.order}'

    class Meta:
        ordering = ('order',)
        unique_together = ('problem', 'order')
        verbose_name = _('MCQ option')
        verbose_name_plural = _('MCQ options')


class ProblemMCQConfig(models.Model):
    """Per-problem multiple-choice settings."""
    problem = models.OneToOneField(Problem, verbose_name=_('problem'), related_name='mcq_config', on_delete=CASCADE)
    multiple_select = models.BooleanField(
        verbose_name=_('allow multiple answers'), default=False,
        help_text=_('If set, students may select more than one choice (checkboxes); otherwise exactly one (radio).'))
    shuffle_options = models.BooleanField(
        verbose_name=_('shuffle options per student'), default=False,
        help_text=_('Present choices in a different, stable order for each student to deter copying.'))

    def __str__(self):
        return _('MCQ config for %s') % self.problem.code

    class Meta:
        verbose_name = _('MCQ configuration')
        verbose_name_plural = _('MCQ configurations')


class ProblemBlank(models.Model):
    """A single blank in a fill-in-the-blank problem. The fill-blank analog of a ProblemMCQOption.

    ``answers`` holds the accepted correct answers, one alternative per line; a student's answer is
    correct if (after normalization per ProblemFillBlankConfig) it matches any line. The accepted
    answers are author-only and must never be serialized into anything served to students.
    """
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='blanks', on_delete=CASCADE)
    order = models.PositiveIntegerField(verbose_name=_('order'))
    answers = models.TextField(
        verbose_name=_('accepted answers'),
        help_text=_('One accepted answer per line; any of them counts as correct.'))
    label = models.CharField(
        verbose_name=_('label'), max_length=100, blank=True,
        help_text=_('Optional label shown next to the input; defaults to "Blank N".'))

    def __str__(self):
        return f'{self.problem.code} #{self.order}'

    class Meta:
        ordering = ('order',)
        unique_together = ('problem', 'order')
        verbose_name = _('fill-in-the-blank blank')
        verbose_name_plural = _('fill-in-the-blank blanks')


class ProblemFillBlankConfig(models.Model):
    """Per-problem fill-in-the-blank settings."""
    problem = models.OneToOneField(Problem, verbose_name=_('problem'), related_name='fill_blank_config',
                                   on_delete=CASCADE)
    case_sensitive = models.BooleanField(
        verbose_name=_('case sensitive'), default=False,
        help_text=_('If set, answers must match the exact letter case; otherwise case is ignored.'))
    trim_whitespace = models.BooleanField(
        verbose_name=_('trim surrounding whitespace'), default=True,
        help_text=_('If set, leading and trailing whitespace in the student answer is ignored.'))

    def __str__(self):
        return _('Fill-in-the-blank config for %s') % self.problem.code

    class Meta:
        verbose_name = _('fill-in-the-blank configuration')
        verbose_name_plural = _('fill-in-the-blank configurations')

from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem

__all__ = ['ProblemMCQOption', 'ProblemMCQConfig', 'ProblemFillBlankConfig', 'ProblemBlankAnswer',
           'ProblemShortAnswerConfig']


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


# --- Fill-in-the-blank ------------------------------------------------------
GRADE_EXACT = 'exact'
GRADE_AI = 'ai'
GRADE_MODE_CHOICES = (
    (GRADE_EXACT, _('Exact match')),
    (GRADE_AI, _('AI grading')),
)


class ProblemFillBlankConfig(models.Model):
    """Per-problem fill-in-the-blank settings (grading policy)."""
    problem = models.OneToOneField(Problem, verbose_name=_('problem'), related_name='fill_blank_config',
                                   on_delete=CASCADE)
    grading_mode = models.CharField(
        verbose_name=_('grading mode'), max_length=8, choices=GRADE_MODE_CHOICES, default=GRADE_EXACT,
        help_text=_('Exact match compares each blank to its accepted answers; AI grading asks the '
                    'configured model to score the response against the rubric.'))
    case_sensitive = models.BooleanField(
        verbose_name=_('case sensitive'), default=False,
        help_text=_('When exact-matching, require the same letter case.'))
    ai_rubric = models.TextField(
        verbose_name=_('AI rubric'), blank=True,
        help_text=_('Grading guidance sent to the model (never shown to students).'))

    def __str__(self):
        return _('Fill-blank config for %s') % self.problem.code

    class Meta:
        verbose_name = _('fill-in-the-blank configuration')
        verbose_name_plural = _('fill-in-the-blank configurations')


class ProblemBlankAnswer(models.Model):
    """One blank's accepted answers. ``answers`` holds ``|``-separated alternatives (author-only)."""
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='blank_answers', on_delete=CASCADE)
    order = models.PositiveIntegerField(verbose_name=_('order'))
    answers = models.TextField(verbose_name=_('accepted answers'),
                               help_text=_('Alternatives separated by "|".'))

    def accepted_list(self):
        return [a.strip() for a in self.answers.split('|') if a.strip()]

    def __str__(self):
        return f'{self.problem.code} blank #{self.order}'

    class Meta:
        ordering = ('order',)
        unique_together = ('problem', 'order')
        verbose_name = _('fill-in-the-blank answer')
        verbose_name_plural = _('fill-in-the-blank answers')


# --- Short answer -----------------------------------------------------------
class ProblemShortAnswerConfig(models.Model):
    """Per-problem short-answer settings. Graded by the LLM against a reference answer + rubric."""
    problem = models.OneToOneField(Problem, verbose_name=_('problem'), related_name='short_answer_config',
                                   on_delete=CASCADE)
    reference_answer = models.TextField(
        verbose_name=_('reference answer'), blank=True,
        help_text=_('Model answer the response is compared against (never shown to students).'))
    ai_rubric = models.TextField(
        verbose_name=_('AI rubric'), blank=True,
        help_text=_('Grading guidance sent to the model (never shown to students).'))

    def __str__(self):
        return _('Short-answer config for %s') % self.problem.code

    class Meta:
        verbose_name = _('short-answer configuration')
        verbose_name_plural = _('short-answer configurations')

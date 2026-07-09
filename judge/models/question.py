from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem

__all__ = ['ProblemMCQOption', 'ProblemMCQConfig', 'ProblemBlank', 'ProblemFillBlankConfig',
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
    is_true_false = models.BooleanField(
        verbose_name=_('true/false question'), default=False,
        help_text=_('Marks this MCQ as a true/false question so it is labelled accordingly.'))
    shuffle_options = models.BooleanField(
        verbose_name=_('shuffle options per student'), default=False,
        help_text=_('Present choices in a different, stable order for each student to deter copying.'))

    def __str__(self):
        return _('MCQ config for %s') % self.problem.code

    class Meta:
        verbose_name = _('MCQ configuration')
        verbose_name_plural = _('MCQ configurations')


# --- Fill-in-the-blank ------------------------------------------------------
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

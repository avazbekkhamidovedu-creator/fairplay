from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem
from judge.storage import generate_submission_key, submission_storage

__all__ = ['ProblemMCQOption', 'ProblemMCQConfig', 'ProblemBlank', 'ProblemFillBlankConfig',
           'SubmissionFile', 'ProblemFileConfig']


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


def submission_file_path(instance, filename):
    """upload_to callback: the storage key is generated from the submission id, never the raw
    filename (the original name is kept on the model). ``submission_id`` is set before save."""
    return generate_submission_key(instance.submission_id, filename)


class SubmissionFile(models.Model):
    """The binary a student uploaded as their answer for a file-upload submission.

    One file per submission to start; the file itself lives in ``submission_storage`` (S3 primary
    + async Box mirror), never on the judge filesystem. ``original_name`` is what the student is
    offered on download; ``file.name`` is the generated storage key.
    """
    submission = models.OneToOneField('Submission', verbose_name=_('submission'), related_name='upload',
                                      on_delete=CASCADE)
    file = models.FileField(verbose_name=_('uploaded file'), storage=submission_storage,
                            upload_to=submission_file_path, max_length=255)
    original_name = models.CharField(verbose_name=_('original filename'), max_length=255)
    size = models.PositiveBigIntegerField(verbose_name=_('file size'), default=0)
    uploaded_on = models.DateTimeField(verbose_name=_('uploaded on'), auto_now_add=True)

    def __str__(self):
        return _('Upload for submission %d') % self.submission_id

    class Meta:
        verbose_name = _('submission file')
        verbose_name_plural = _('submission files')


class ProblemFileConfig(models.Model):
    """Per-problem file-upload settings (accepted extensions / size cap / instructions)."""
    problem = models.OneToOneField(Problem, verbose_name=_('problem'), related_name='file_config',
                                   on_delete=CASCADE)
    allowed_extensions = models.CharField(
        verbose_name=_('allowed file extensions'), max_length=255, blank=True,
        help_text=_('Comma-separated extensions without dots, e.g. "pdf,zip,docx". '
                    'Leave blank to allow any extension.'))
    max_file_size = models.PositiveBigIntegerField(
        verbose_name=_('maximum file size (bytes)'), default=20 * 1024 * 1024,
        help_text=_('Reject uploads larger than this many bytes.'))
    instructions = models.TextField(
        verbose_name=_('submission instructions'), blank=True,
        help_text=_('Optional guidance shown to students on the submit page.'))

    def allowed_extension_list(self):
        return [e.strip().lower().lstrip('.') for e in self.allowed_extensions.split(',') if e.strip()]

    def __str__(self):
        return _('File-upload config for %s') % self.problem.code

    class Meta:
        verbose_name = _('file-upload configuration')
        verbose_name_plural = _('file-upload configurations')

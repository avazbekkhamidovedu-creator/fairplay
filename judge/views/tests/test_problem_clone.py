"""Tests for problem clone deep-copy behaviour (judge.views.problem.ProblemClone)."""
from django.test import Client, TestCase
from django.urls import reverse

from judge.models import (
    Class,
    Language,
    LanguageLimit,
    Problem,
    ProblemBlank,
    ProblemFileConfig,
    ProblemFillBlankConfig,
    ProblemMCQConfig,
    ProblemMCQOption,
    ProblemShortAnswerConfig,
    ProblemSubmissionType,
)
from judge.models.tests.util import CommonDataMixin, create_organization, create_problem, create_problem_type


class ProblemCloneTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        create_problem_type(name='easy', full_name='Easy')
        cls.org = create_organization(name='clone-org')
        cls.klass = Class.objects.create(
            organization=cls.org, name='Clone Class', slug='clone-class', description='',
        )
        cls.lang = Language.get_python3()

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.users['superuser'])

    def _clone(self, problem, new_code):
        response = self.client.post(
            reverse('problem_clone', args=(problem.code,)),
            {'code': new_code},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('problem_edit_v2', args=(new_code,)))
        return Problem.objects.get(code=new_code)

    def test_clone_mcq_deep_copies_config_and_options(self):
        problem = create_problem(
            code='clone-mcq',
            name='Clone MCQ',
            is_public=True,
            submission_type=ProblemSubmissionType.MCQ,
            authors=('superuser',),
        )
        ProblemMCQConfig.objects.create(problem=problem, multiple_select=True, is_true_false=False)
        ProblemMCQOption.objects.create(problem=problem, order=0, text='A', is_correct=True)
        ProblemMCQOption.objects.create(problem=problem, order=1, text='B', is_correct=False)

        clone = self._clone(problem, 'clonemcq2')
        self.assertEqual(clone.submission_type, ProblemSubmissionType.MCQ)
        self.assertTrue(clone.mcq_config.multiple_select)
        self.assertEqual(
            list(clone.mcq_options.order_by('order').values_list('text', 'is_correct')),
            [('A', True), ('B', False)],
        )
        # Source rows must remain attached to the original problem.
        self.assertEqual(problem.mcq_options.count(), 2)
        self.assertTrue(ProblemMCQConfig.objects.filter(problem=problem).exists())

    def test_clone_fill_blank_and_classes(self):
        problem = create_problem(
            code='clone-blank',
            name='Clone Blank',
            is_public=False,
            is_organization_private=True,
            submission_type=ProblemSubmissionType.FILL_BLANK,
            authors=('superuser',),
            organizations=('clone-org',),
        )
        problem.classes.add(self.klass)
        ProblemFillBlankConfig.objects.create(problem=problem, case_sensitive=True, trim_whitespace=False)
        ProblemBlank.objects.create(problem=problem, order=0, answers='Paris\nparis', label='City')

        clone = self._clone(problem, 'cloneblank2')
        self.assertEqual(clone.submission_type, ProblemSubmissionType.FILL_BLANK)
        self.assertTrue(clone.fill_blank_config.case_sensitive)
        self.assertFalse(clone.fill_blank_config.trim_whitespace)
        blank = clone.blanks.get()
        self.assertEqual(blank.answers, 'Paris\nparis')
        self.assertEqual(blank.label, 'City')
        self.assertEqual(list(clone.organizations.values_list('name', flat=True)), ['clone-org'])
        self.assertEqual(list(clone.classes.values_list('id', flat=True)), [self.klass.id])
        self.assertEqual(problem.blanks.count(), 1)
        self.assertEqual(list(problem.classes.values_list('id', flat=True)), [self.klass.id])

    def test_clone_does_not_move_language_limit_fk(self):
        problem = create_problem(
            code='clone-ll',
            name='Clone Language Limit',
            is_public=True,
            authors=('superuser',),
        )
        LanguageLimit.objects.create(
            problem=problem,
            language=self.lang,
            time_limit=3.5,
            memory_limit=65536,
        )

        clone = self._clone(problem, 'clonell2')
        self.assertEqual(problem.language_limits.count(), 1)
        self.assertEqual(clone.language_limits.count(), 1)
        cloned_limit = clone.language_limits.get()
        self.assertEqual(cloned_limit.language_id, self.lang.id)
        self.assertEqual(cloned_limit.time_limit, 3.5)
        self.assertEqual(cloned_limit.memory_limit, 65536)
        self.assertNotEqual(cloned_limit.pk, problem.language_limits.get().pk)

    def test_clone_short_answer_config(self):
        problem = create_problem(
            code='clone-sa',
            name='Clone Short Answer',
            is_public=True,
            submission_type=ProblemSubmissionType.SHORT_ANSWER,
            authors=('superuser',),
        )
        ProblemShortAnswerConfig.objects.create(
            problem=problem,
            reference_answer='42',
            ai_rubric='mention the answer',
        )

        clone = self._clone(problem, 'clonesa2')
        self.assertEqual(clone.short_answer_config.reference_answer, '42')
        self.assertEqual(clone.short_answer_config.ai_rubric, 'mention the answer')
        self.assertEqual(problem.short_answer_config.reference_answer, '42')

    def test_clone_file_upload_config(self):
        problem = create_problem(
            code='clone-file',
            name='Clone File Upload',
            is_public=True,
            submission_type=ProblemSubmissionType.FILE,
            authors=('superuser',),
        )
        ProblemFileConfig.objects.create(
            problem=problem,
            allowed_extensions='pdf,zip',
            max_file_size=7 * 1024 * 1024,
            instructions='Upload a PDF or ZIP.',
        )

        clone = self._clone(problem, 'clonefile2')
        self.assertEqual(clone.submission_type, ProblemSubmissionType.FILE)
        self.assertEqual(clone.file_config.allowed_extensions, 'pdf,zip')
        self.assertEqual(clone.file_config.max_file_size, 7 * 1024 * 1024)
        self.assertEqual(clone.file_config.instructions, 'Upload a PDF or ZIP.')
        # Source OneToOne row must remain on the original problem.
        self.assertTrue(ProblemFileConfig.objects.filter(problem=problem).exists())
        self.assertEqual(problem.file_config.allowed_extensions, 'pdf,zip')
        self.assertNotEqual(clone.file_config.pk, problem.file_config.pk)

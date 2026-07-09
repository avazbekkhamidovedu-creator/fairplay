from django.test import TestCase
from django.urls import reverse

from judge.models import ProblemMCQConfig
from judge.models.tests.util import CommonDataMixin, create_problem


class ProblemListFormatFilterTest(CommonDataMixin, TestCase):
    """The redesigned problem list filters by answer format via Problem.submission_type."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.code_problem = create_problem(
            code='codeprob', name='Code Problem', is_public=True, submission_type='C')
        self.mcq_problem = create_problem(
            code='mcqprob', name='Quiz Problem', is_public=True, submission_type='M')

    def _content(self, query=''):
        self.client.force_login(self.users['normal'])
        return self.client.get(reverse('problem_list') + query).content

    def test_all_formats_shown_by_default(self):
        content = self._content()
        self.assertIn(b'codeprob', content)
        self.assertIn(b'mcqprob', content)

    def test_filter_by_mcq_format(self):
        content = self._content('?format=M')
        self.assertIn(b'mcqprob', content)
        self.assertNotIn(b'codeprob', content)

    def test_filter_by_code_format(self):
        content = self._content('?format=C')
        self.assertIn(b'codeprob', content)
        self.assertNotIn(b'mcqprob', content)

    def test_filter_by_multiple_formats(self):
        content = self._content('?format=C&format=M')
        self.assertIn(b'codeprob', content)
        self.assertIn(b'mcqprob', content)

    def test_unknown_format_is_ignored(self):
        # 'Z' is not a registered question type, so the filter is dropped (show everything).
        content = self._content('?format=Z')
        self.assertIn(b'codeprob', content)
        self.assertIn(b'mcqprob', content)


class ProblemListMCQSubtypeFilterTest(CommonDataMixin, TestCase):
    """MCQ is one submission_type but the list breaks it into single / multiple / true-false."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.single = create_problem(
            code='mcq-single', name='Single MCQ', is_public=True, submission_type='M')
        self.multiple = create_problem(
            code='mcq-multi', name='Multi MCQ', is_public=True, submission_type='M')
        self.truefalse = create_problem(
            code='mcq-tf', name='TF MCQ', is_public=True, submission_type='M')
        # A legacy MCQ with no mcq_config row: must still match the whole-MCQ filter.
        self.legacy = create_problem(
            code='mcq-legacy', name='Legacy MCQ', is_public=True, submission_type='M')
        ProblemMCQConfig.objects.create(
            problem=self.single, multiple_select=False, is_true_false=False)
        ProblemMCQConfig.objects.create(
            problem=self.multiple, multiple_select=True, is_true_false=False)
        ProblemMCQConfig.objects.create(
            problem=self.truefalse, multiple_select=False, is_true_false=True)

    def _content(self, query=''):
        self.client.force_login(self.users['normal'])
        return self.client.get(reverse('problem_list') + query).content

    def test_filter_single_choice(self):
        content = self._content('?format=single')
        self.assertIn(b'mcq-single', content)
        self.assertNotIn(b'mcq-multi', content)
        self.assertNotIn(b'mcq-tf', content)
        self.assertNotIn(b'mcq-legacy', content)

    def test_filter_multiple_choice(self):
        content = self._content('?format=multiple')
        self.assertIn(b'mcq-multi', content)
        self.assertNotIn(b'mcq-single', content)
        self.assertNotIn(b'mcq-tf', content)

    def test_filter_true_false(self):
        content = self._content('?format=truefalse')
        self.assertIn(b'mcq-tf', content)
        self.assertNotIn(b'mcq-single', content)
        self.assertNotIn(b'mcq-multi', content)

    def test_whole_mcq_filter_covers_all_subtypes(self):
        content = self._content('?format=M')
        for code in (b'mcq-single', b'mcq-multi', b'mcq-tf', b'mcq-legacy'):
            self.assertIn(code, content)

    def test_combining_subtypes(self):
        content = self._content('?format=single&format=truefalse')
        self.assertIn(b'mcq-single', content)
        self.assertIn(b'mcq-tf', content)
        self.assertNotIn(b'mcq-multi', content)

from django.test import TestCase
from django.urls import reverse

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

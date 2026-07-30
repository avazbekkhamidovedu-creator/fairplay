from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from judge.models import Judge, Language, Submission
from judge.models.tests.util import create_problem, create_user


class ProblemWorkspaceTestCase(TestCase):
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='workspace-user')
        cls.language = Language.objects.get(key='PY3')
        cls.judge = Judge.objects.create(name='workspace-judge', auth_key='workspace-key', online=True)
        cls.judge.runtimes.add(cls.language)
        cls.problem = create_problem(code='workspace-code', is_public=True)
        cls.problem.allowed_languages.add(cls.language)
        cls.ide_problem = create_problem(code='idepractice', is_public=True)
        cls.judge.problems.add(cls.problem, cls.ide_problem)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_detail_uses_workspace_shell_and_legacy_escape_hatch(self):
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertContains(response, 'id="workspace"')
        self.assertContains(response, 'id="ws-drawer"')
        legacy = self.client.get(reverse('problem_detail', args=[self.problem.code]) + '?legacy=1')
        self.assertNotContains(legacy, 'id="workspace"')

    def test_run_rejects_language_not_allowed_by_source_problem(self):
        response = self.client.post(reverse('problem_workspace_run', args=[self.problem.code]), {
            'source': 'print(1)', 'language': 999999, 'input': '',
        })
        self.assertEqual(response.status_code, 400)

    def test_run_uses_idepractice_without_contest_submission(self):
        with mock.patch.object(Submission, 'judge'):
            response = self.client.post(reverse('problem_workspace_run', args=[self.problem.code]), {
                'source': 'print(1)', 'language': self.language.id, 'input': 'x',
            })
        self.assertEqual(response.status_code, 200, response.content)
        run = Submission.objects.get(id=response.json()['submission_id'])
        self.assertEqual(run.problem.code, 'idepractice')
        self.assertFalse(hasattr(run, 'contest_submission'))

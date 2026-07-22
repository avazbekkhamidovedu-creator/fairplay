import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from judge.llm.base import LLMResult
from judge.models import Problem, ProblemImportDraft, Solution
from judge.models.tests.util import CommonDataMixin, create_problem_group, create_problem_type, create_user
from judge.utils.simple_xlsx import build_xlsx_workbook


class ProblemImportPreviewTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        create_problem_group(name='import-group')
        create_problem_type(name='import-type')
        cls.creator = create_user(
            username='import_creator',
            user_permissions=('create_private_problem',),
        )

    def setUp(self):
        self.client.force_login(self.creator)

    def upload(self, rows):
        workbook = build_xlsx_workbook([('Problems', rows)])
        return self.client.post(reverse('problem_import_upload'), {
            'excel_file': SimpleUploadedFile(
                'questions.xlsx', workbook,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            ),
        })

    def test_upload_creates_preview_draft_from_question_type_column(self):
        response = self.upload([
            ['name', 'question_type', 'description', 'answer'],
            ['Prime check', 'multiple_choice', 'Which is prime?\nA. 4\nB. 5', 'B'],
            ['Greeting', 'fill_blank', 'Complete: Hello ____', 'world'],
        ])
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        self.assertEqual(draft.items.count(), 2)
        self.assertEqual(draft.items.get(name='Prime check').submission_type, 'M')
        self.assertEqual(draft.items.get(name='Greeting').submission_type, 'B')
        self.assertEqual(self.client.get(payload['preview_url']).status_code, 200)

    @mock.patch('judge.views.problem_import.llm.chat')
    @mock.patch('judge.views.problem_import.llm.is_configured', return_value=True)
    def test_llm_formats_only_the_selected_multiple_choice_draft(self, _configured, chat):
        response = self.upload([
            ['name', 'question_type', 'description', 'answer'],
            ['Prime check', 'multiple_choice', 'Which is prime? A. 4 B. 5', 'B'],
        ])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        item = draft.items.get()
        chat.return_value = LLMResult(text=json.dumps({
            'options': [{'text': '4'}, {'text': '5'}],
            'option_block': 'A. 4 B. 5',
        }))
        response = self.client.post(reverse('problem_import_item_action', args=(draft.pk, item.pk, 'format')), {
            'name': item.name,
            'question_type': 'M',
            'description': item.description,
            'answer': item.answer,
            'options': '',
            'correct_options': '',
            'blanks': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        item.refresh_from_db()
        self.assertEqual(item.options, '4\n5')
        self.assertEqual(item.correct_options, '2')
        chat.assert_called_once()
        self.assertEqual(chat.call_args.kwargs['profile'], 'question_import')

    def test_create_multiple_choice_from_reviewed_draft(self):
        self.upload([
            ['name', 'question_type', 'description'],
            ['Prime check', 'multiple_choice', 'Which number is prime?'],
        ])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        item = draft.items.get()
        response = self.client.post(reverse('problem_import_item_action', args=(draft.pk, item.pk, 'create')), {
            'name': item.name,
            'question_type': 'M',
            'description': item.description,
            'answer': '2',
            'solution': '5 is the only prime option.',
            'options': '4\n5',
            'correct_options': '2',
            'blanks': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        problem = Problem.objects.get(name='Prime check')
        self.assertEqual(problem.submission_type, 'M')
        self.assertEqual(problem.mcq_options.filter(is_correct=True).count(), 1)
        self.assertEqual(Solution.objects.get(problem=problem).content, '5 is the only prime option.')

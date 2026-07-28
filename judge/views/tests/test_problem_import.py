import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from judge.llm.base import LLMResult
from judge.models import Problem, ProblemImportDraft, ProblemImportImage, Solution
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
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_upload_creates_preview_draft_from_question_type_column(self):
        response = self.upload([
            [
                'name*', 'problem_type*', 'description*', 'answer*',
                'tag（less than 20 characters, spaced with commas)',
                'difficulty\n(easy:0; medium:1; difficult:2)', 'explanation',
            ],
            [
                'Prime check', 'multiple_choice', 'Which is prime?\nA. 4\nB. 5', 'B',
                'number theory, primes', '2', 'Prime is only divisible by 1 and itself.',
            ],
            [
                'Greeting', 'fill_blank', 'Complete: Hello ____', 'world', 'vocabulary', '0',
                'Use the familiar greeting.',
            ],
        ])
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        self.assertEqual(draft.items.count(), 2)
        self.assertEqual(draft.items.get(name='Prime check').submission_type, 'M')
        self.assertEqual(draft.items.get(name='Greeting').submission_type, 'B')
        self.assertEqual(draft.items.get(name='Prime check').tags, 'number theory, primes')
        self.assertEqual(draft.items.get(name='Prime check').difficulty, '2')
        self.assertEqual(draft.items.get(name='Prime check').description, 'Which is prime?')
        self.assertEqual(draft.items.get(name='Prime check').options, '4\n5')
        self.assertEqual(draft.items.get(name='Prime check').answer, 'B')
        self.assertEqual(draft.items.get(name='Prime check').correct_options, '2')
        self.assertEqual(draft.items.get(name='Greeting').solution, 'Use the familiar greeting.')
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
            'tags': 'number theory, primes',
            'difficulty': '2',
            'options': '4\n5',
            'correct_options': '2',
            'blanks': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        problem = Problem.objects.get(name='Prime check')
        self.assertEqual(problem.submission_type, 'M')
        self.assertEqual(problem.mcq_options.filter(is_correct=True).count(), 1)
        self.assertSetEqual(
            set(problem.types.values_list('name', flat=True)),
            {'import-type', 'number_theory', 'primes', 'hard'},
        )
        self.assertEqual(Solution.objects.get(problem=problem).content, '5 is the only prime option.')

    def test_single_create_rejects_an_unconfirmed_question_type(self):
        self.upload([
            ['name', 'question_type', 'description'],
            ['Review type', 'Written response', 'Confirm the imported type before creating.'],
        ])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        item = draft.items.get()

        response = self.client.post(reverse('problem_import_item_action', args=(draft.pk, item.pk, 'create')), {
            'name': item.name,
            'question_type': item.submission_type,
            'description': item.description,
            'answer': '',
            'options': '',
            'correct_options': '',
            'blanks': '',
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('Select or confirm', response.json()['message'])
        self.assertFalse(Problem.objects.filter(name='Review type').exists())

    @mock.patch('judge.views.problem_import.store_docx_image')
    def test_docx_figure_is_private_in_preview_then_promoted_on_create(self, store_docx_image):
        self.upload([
            ['name', 'question_type', 'description'],
            ['Figure question', 'multiple_choice', 'Choose the figure.'],
        ])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        item = draft.items.get()
        image = ProblemImportImage.objects.create(item=item, filename='figure.png', content=b'png-data')
        image_url = reverse('problem_import_image', args=(draft.pk, image.token))
        item.description = 'Choose the figure.\n\n![]({})'.format(image_url)
        item.save(update_fields=['description'])

        preview = self.client.get(image_url)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.content, b'png-data')

        store_docx_image.return_value = ('martor/final.png', '/media/martor/final.png')
        response = self.client.post(reverse('problem_import_item_action', args=(draft.pk, item.pk, 'create')), {
            'name': item.name,
            'question_type': 'M',
            'description': item.description,
            'answer': 'A',
            'options': 'first\nsecond',
            'correct_options': '1',
            'blanks': '',
        })

        self.assertTrue(response.json()['ok'])
        problem = Problem.objects.get(name='Figure question')
        self.assertIn('/media/martor/final.png', problem.description)
        self.assertFalse(ProblemImportImage.objects.filter(pk=image.pk).exists())
        store_docx_image.assert_called_once_with('figure.png', b'png-data')

    @mock.patch('judge.views.problem_import.store_docx_image')
    def test_fill_blank_creation_keeps_promoted_docx_image_urls(self, store_docx_image):
        self.upload([
            ['name', 'question_type', 'description', 'answer'],
            ['Figure blank', 'fill_blank', 'The figure shows [[blank]].', 'a triangle'],
        ])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)
        item = draft.items.get()
        image = ProblemImportImage.objects.create(item=item, filename='figure.png', content=b'png-data')
        image_url = reverse('problem_import_image', args=(draft.pk, image.token))
        item.description = 'The figure shows [[blank]].\n\n![]({})'.format(image_url)
        item.blanks = ''
        item.save(update_fields=['description', 'blanks'])
        store_docx_image.return_value = ('martor/final.png', '/media/martor/final.png')

        response = self.client.post(reverse('problem_import_item_action', args=(draft.pk, item.pk, 'create')), {
            'name': item.name,
            'question_type': 'B',
            'description': item.description,
            'answer': item.answer,
            'options': '',
            'correct_options': '',
            'blanks': '',
        })

        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(name='Figure blank')
        self.assertIn('/media/martor/final.png', problem.description)
        self.assertNotIn(image_url, problem.description)

    def test_create_all_creates_all_reviewed_items(self):
        self.upload([
            ['name', 'question_type', 'description', 'answer'],
            ['Prime check', 'multiple_choice', 'Which is prime?\nA. 4\nB. 5', 'B'],
            ['Capital', 'fill_blank', 'The capital of France is [[blank]].', 'Paris'],
        ])
        draft = ProblemImportDraft.objects.get(owner=self.creator.profile)

        response = self.client.post(
            reverse('problem_import_create_all', args=(draft.pk,)),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['count'], 2)
        self.assertEqual(draft.items.filter(created_problem__isnull=False).count(), 2)
        self.assertEqual(Problem.objects.filter(name__in=['Prime check', 'Capital']).count(), 2)

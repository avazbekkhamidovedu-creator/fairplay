import json
import os
import tempfile
import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from reversion.models import Version

from judge.models import (Language, LanguageLimit, License, Problem, ProblemClarification, ProblemData,
                          ProblemFileConfig, ProblemFillBlankConfig, ProblemMCQConfig, ProblemShortAnswerConfig,
                          ProblemTemplate, ProblemTestCase, ProblemTranslation, ProblemType, Solution,
                          problem_data_storage)
from judge.models.tests.util import CommonDataMixin, create_user
from judge.utils.problem_create import default_problem_group
from judge.views.problem_create_v2 import ProblemCreateV2View


class ProblemCreateV2ViewTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = default_problem_group()
        cls.users.update({
            'creator': create_user(
                username='mcq_creator',
                user_permissions=('create_private_problem', 'edit_own_problem'),
            ),
            'staff_adder': create_user(
                username='code_creator',
                is_staff=True,
                user_permissions=('add_problem', 'create_private_problem', 'change_public_visibility',
                                  'edit_own_problem'),
            ),
        })

    def test_get_requires_permission(self):
        self.client.force_login(self.users['normal'])
        self.assertEqual(self.client.get(reverse('problem_create_v2')).status_code, 404)

        self.client.force_login(self.users['creator'])
        self.assertEqual(self.client.get(reverse('problem_create_v2')).status_code, 200)

    def test_create_mcq_via_json(self):
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'single',
            'name': 'HTTP vs TCP',
            'description': 'Which is connection-oriented?',
            'points': 5,
            'group_id': self.group.id,
            'options': [
                {'text': 'HTTP', 'correct': False},
                {'text': 'TCP', 'correct': True},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        problem = Problem.objects.get(code=body['code'])
        self.assertEqual(problem.name, 'HTTP vs TCP')
        self.assertTrue(ProblemMCQConfig.objects.filter(problem=problem, multiple_select=False).exists())

    def test_create_code_with_testcases(self):
        self.client.force_login(self.users['staff_adder'])
        payload = {
            'type': 'programming',
            'name': 'A Plus B',
            'description': 'Read two integers.',
            'points': 10,
            'partial': True,
            'time_limit': 2,
            'memory_limit': 256,
            'group_id': self.group.id,
            'testcases': [
                {'input': '1 2\n', 'output': '3\n', 'points': 50, 'pretest': True},
                {'input': '0 0\n', 'output': '0\n', 'points': 50, 'pretest': False},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        problem = Problem.objects.get(code=body['code'])
        self.assertEqual(problem.cases.count(), 2)
        self.assertTrue(ProblemTestCase.objects.filter(dataset=problem, input_file='01.in').exists())

    def test_rejects_unimplemented_type(self):
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'compound',
            'name': 'Compound',
            'description': 'Not implemented yet',
            'points': 1,
            'group_id': self.group.id,
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_create_mcq_persists_advanced_fields(self):
        from django.contrib.auth.models import Permission
        creator = self.users['creator']
        creator.user_permissions.add(Permission.objects.get(codename='problem_full_markup'))
        self.client.force_login(creator)
        curator = self.users['normal'].profile
        license_obj = License.objects.create(
            key='cc-by-sa-4.0', link='https://example.com/license', name='CC BY-SA 4.0',
            display='CC BY-SA 4.0', text='license text',
        )
        payload = {
            'type': 'single',
            'name': 'Advanced wired',
            'description': 'Authors and license should persist.',
            'points': 5,
            'group_id': self.group.id,
            'options': [
                {'text': 'A', 'correct': True},
                {'text': 'B', 'correct': False},
            ],
            'curators': [curator.id],
            'license_id': license_obj.id,
            'is_full_markup': True,
            'view_test_cases': True,
            'summary': 'short summary',
            'submission_source_visibility_mode': 'S',
            'solution': {
                'is_public': True,
                'publish_on': '2030-01-02 15:30',
                'content': 'Official solution body',
                'authors': [self.users['creator'].profile.id],
            },
            'translations': [
                {'language': 'zh-hans', 'name': '进阶题', 'description': '中文描述'},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        problem = Problem.objects.get(code=body['code'])
        self.assertTrue(problem.authors.filter(id=self.users['creator'].profile.id).exists())
        self.assertTrue(problem.curators.filter(id=curator.id).exists())
        self.assertEqual(problem.license_id, license_obj.id)
        self.assertTrue(problem.is_full_markup)
        self.assertTrue(problem.view_test_cases)
        self.assertEqual(problem.summary, 'short summary')
        self.assertEqual(problem.submission_source_visibility_mode, 'S')
        sol = Solution.objects.get(problem=problem)
        self.assertTrue(sol.is_public)
        self.assertEqual(sol.content, 'Official solution body')
        self.assertTrue(sol.authors.filter(id=self.users['creator'].profile.id).exists())
        tr = ProblemTranslation.objects.get(problem=problem, language='zh-hans')
        self.assertEqual(tr.name, '进阶题')
        self.assertEqual(tr.description, '中文描述')

    def test_edit_omitting_solution_and_translations_keeps_them(self):
        """Partial edit payloads must not wipe Advanced solution/translations."""
        self.client.force_login(self.users['creator'])
        create_payload = {
            'type': 'single',
            'name': 'Keep advanced',
            'description': 'Has solution and translation.',
            'points': 5,
            'group_id': self.group.id,
            'options': [
                {'text': 'A', 'correct': True},
                {'text': 'B', 'correct': False},
            ],
            'solution': {
                'is_public': True,
                'content': 'Keep me',
                'authors': [self.users['creator'].profile.id],
            },
            'translations': [
                {'language': 'zh-hans', 'name': '保留', 'description': '保留描述'},
            ],
        }
        create_resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(create_payload),
            content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 200)
        code = create_resp.json()['code']

        edit_payload = {
            'type': 'single',
            'name': 'Keep advanced (renamed)',
            'description': 'Updated description only.',
            'points': 5,
            'group_id': self.group.id,
            'options': [
                {'text': 'A', 'correct': True},
                {'text': 'B', 'correct': False},
            ],
            'edit_code': code,
        }
        edit_resp = self.client.post(
            reverse('problem_edit_v2', args=[code]),
            data=json.dumps(edit_payload),
            content_type='application/json',
        )
        self.assertEqual(edit_resp.status_code, 200)
        self.assertTrue(edit_resp.json()['ok'])
        problem = Problem.objects.get(code=code)
        self.assertEqual(problem.name, 'Keep advanced (renamed)')
        sol = Solution.objects.get(problem=problem)
        self.assertEqual(sol.content, 'Keep me')
        tr = ProblemTranslation.objects.get(problem=problem, language='zh-hans')
        self.assertEqual(tr.name, '保留')

    def test_edit_does_not_promote_curator_to_author(self):
        """Curators/admins editing must not be force-added to authors."""
        curator = create_user(
            username='mcq_curator',
            user_permissions=('create_private_problem', 'edit_own_problem'),
        )
        self.client.force_login(self.users['creator'])
        create_resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps({
                'type': 'single',
                'name': 'Author ownership',
                'description': 'Keep authors stable on edit.',
                'points': 5,
                'group_id': self.group.id,
                'options': [
                    {'text': 'A', 'correct': True},
                    {'text': 'B', 'correct': False},
                ],
                'curators': [curator.profile.id],
            }),
            content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 200)
        code = create_resp.json()['code']
        problem = Problem.objects.get(code=code)
        self.assertTrue(problem.authors.filter(id=self.users['creator'].profile.id).exists())
        self.assertFalse(problem.authors.filter(id=curator.profile.id).exists())

        self.client.force_login(curator)
        edit_resp = self.client.post(
            reverse('problem_edit_v2', args=[code]),
            data=json.dumps({
                'type': 'single',
                'name': 'Author ownership (edited)',
                'description': 'Edited by curator.',
                'points': 5,
                'group_id': self.group.id,
                'options': [
                    {'text': 'A', 'correct': True},
                    {'text': 'B', 'correct': False},
                ],
                'authors': [self.users['creator'].profile.id],
                'curators': [curator.profile.id],
                'edit_code': code,
            }),
            content_type='application/json',
        )
        self.assertEqual(edit_resp.status_code, 200, edit_resp.content)
        self.assertTrue(edit_resp.json()['ok'])
        problem.refresh_from_db()
        self.assertEqual(list(problem.authors.values_list('id', flat=True)), [self.users['creator'].profile.id])
        self.assertTrue(problem.curators.filter(id=curator.profile.id).exists())

    def test_full_markup_requires_permission(self):
        self.client.force_login(self.users['creator'])  # no problem_full_markup
        resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps({
                'type': 'single',
                'name': 'No full markup',
                'description': 'Should stay False.',
                'points': 1,
                'group_id': self.group.id,
                'options': [
                    {'text': 'A', 'correct': True},
                    {'text': 'B', 'correct': False},
                ],
                'is_full_markup': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        problem = Problem.objects.get(code=resp.json()['code'])
        self.assertFalse(problem.is_full_markup)

    def test_unsupported_translation_language_rejected(self):
        self.client.force_login(self.users['creator'])
        resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps({
                'type': 'single',
                'name': 'Bad translation lang',
                'description': 'Should 400.',
                'points': 1,
                'group_id': self.group.id,
                'options': [
                    {'text': 'A', 'correct': True},
                    {'text': 'B', 'correct': False},
                ],
                'translations': [
                    {'language': 'xx-not-a-lang', 'name': 'X', 'description': 'Y'},
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Unsupported translation language', resp.json()['error'])

    def test_create_code_persists_language_limits_templates_clarifications(self):
        lang = Language.objects.first()
        self.assertIsNotNone(lang)
        self.client.force_login(self.users['staff_adder'])
        resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps({
                'type': 'programming',
                'name': 'Limits and templates',
                'description': 'Wire advanced inlines.',
                'points': 10,
                'group_id': self.group.id,
                'time_limit': 2,
                'memory_limit': 256,
                'testcases': [
                    {'input': '1\n', 'output': '1\n', 'points': 100},
                ],
                'language_limits': [
                    {'language': lang.key, 'time_limit': 3.5, 'memory_limit': 131072},
                ],
                'templates': [
                    {'language': lang.key, 'code': 'print(input())\n'},
                ],
                'clarifications': [
                    {'description': 'Input is a single integer.'},
                ],
                'edit_reason': 'Initial wiring of advanced inlines',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        problem = Problem.objects.get(code=resp.json()['code'])
        lim = LanguageLimit.objects.get(problem=problem, language=lang)
        self.assertEqual(lim.time_limit, 3.5)
        self.assertEqual(lim.memory_limit, 131072)
        tmpl = ProblemTemplate.objects.get(problem=problem, language=lang)
        self.assertEqual(tmpl.code, 'print(input())\n')
        clar = ProblemClarification.objects.get(problem=problem)
        self.assertEqual(clar.description, 'Input is a single integer.')
        versions = Version.objects.get_for_object(problem)
        self.assertTrue(versions.exists())
        self.assertEqual(versions[0].revision.comment, 'Initial wiring of advanced inlines')

    def test_edit_clarifications_can_update_and_delete(self):
        self.client.force_login(self.users['creator'])
        create_resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps({
                'type': 'single',
                'name': 'Clarify me',
                'description': 'Has clarifications.',
                'points': 5,
                'group_id': self.group.id,
                'options': [
                    {'text': 'A', 'correct': True},
                    {'text': 'B', 'correct': False},
                ],
                'clarifications': [
                    {'description': 'Keep this'},
                    {'description': 'Remove this'},
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 200)
        code = create_resp.json()['code']
        problem = Problem.objects.get(code=code)
        keep = ProblemClarification.objects.get(problem=problem, description='Keep this')
        remove = ProblemClarification.objects.get(problem=problem, description='Remove this')

        edit_resp = self.client.post(
            reverse('problem_edit_v2', args=[code]),
            data=json.dumps({
                'type': 'single',
                'name': 'Clarify me',
                'description': 'Has clarifications.',
                'points': 5,
                'group_id': self.group.id,
                'options': [
                    {'text': 'A', 'correct': True},
                    {'text': 'B', 'correct': False},
                ],
                'edit_code': code,
                'clarifications': [
                    {'id': keep.id, 'description': 'Keep this (updated)'},
                    {'id': remove.id, 'description': 'Remove this', 'delete': True},
                    {'description': 'Brand new'},
                ],
                'edit_reason': 'Update clarifications',
            }),
            content_type='application/json',
        )
        self.assertEqual(edit_resp.status_code, 200, edit_resp.content)
        descs = list(
            ProblemClarification.objects.filter(problem=problem).order_by('id').values_list('description', flat=True),
        )
        self.assertEqual(descs, ['Keep this (updated)', 'Brand new'])
        versions = Version.objects.get_for_object(problem)
        self.assertEqual(versions[0].revision.comment, 'Update clarifications')

    def test_ai_difficulty_endpoint(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(
            reverse('problem_create_v2_ai_difficulty'),
            data=json.dumps({'description': 'short'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()['level'], ('easy', 'medium', 'hard'))

    def test_create_problem_type(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(
            reverse('problem_create_v2_problem_type'),
            data=json.dumps({'name': 'graph-theory'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['type']['name'], 'graph-theory')
        self.assertEqual(body['type']['full'], 'graph-theory')

    def test_create_problem_type_from_display_name_only(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(
            reverse('problem_create_v2_problem_type'),
            data=json.dumps({'full_name': 'Dynamic Programming'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['type']['full'], 'Dynamic Programming')

    def test_create_truefalse_as_mcq(self):
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'truefalse', 'name': 'Sky is blue', 'description': 'Is the sky blue?',
            'points': 2, 'group_id': self.group.id, 'tfAnswer': 'true',
        }
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertEqual(problem.submission_type, 'M')
        self.assertTrue(ProblemMCQConfig.objects.filter(problem=problem, multiple_select=False).exists())
        self.assertEqual(problem.mcq_options.count(), 2)
        self.assertTrue(problem.mcq_options.get(order=0).is_correct)
        self.assertFalse(problem.mcq_options.get(order=1).is_correct)

    def test_create_fill_blank(self):
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'blank', 'name': 'Capital', 'description': 'Capital of France is {{blank}}.',
            'points': 3, 'group_id': self.group.id, 'blanks': ['Paris\nparis'],
        }
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertEqual(problem.submission_type, 'B')
        # trim_whitespace defaults to True; case_sensitive defaults to False.
        self.assertTrue(ProblemFillBlankConfig.objects.filter(
            problem=problem, trim_whitespace=True, case_sensitive=False).exists())
        self.assertEqual(problem.blanks.count(), 1)
        # Each line is one accepted answer for the newline-splitting grader.
        self.assertEqual(problem.blanks.first().answers.splitlines(), ['Paris', 'paris'])

    def test_create_fill_blank_ai_mode(self):
        # With ai=True the config records AI grading + rubric; grading itself falls back to exact
        # when no LLM is configured (see judge/question_types/fill_blank.py).
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'blank', 'name': 'Essay blank', 'description': 'Define recursion: {{blank}}',
            'points': 4, 'group_id': self.group.id, 'blanks': ['a function calling itself'],
            'ai': True, 'rubric': 'accept any answer mentioning self-reference',
        }
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        config = ProblemFillBlankConfig.objects.get(problem=problem)
        self.assertEqual(config.grading_mode, 'ai')
        self.assertEqual(config.ai_rubric, 'accept any answer mentioning self-reference')

    def test_create_short_answer(self):
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'short', 'name': 'Explain TCP', 'description': 'Explain TCP handshake.',
            'points': 5, 'group_id': self.group.id, 'shortAnswer': 'SYN, SYN-ACK, ACK', 'rubric': 'mention 3 steps',
        }
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertEqual(problem.submission_type, 'S')
        config = ProblemShortAnswerConfig.objects.get(problem=problem)
        self.assertEqual(config.reference_answer, 'SYN, SYN-ACK, ACK')
        self.assertEqual(config.ai_rubric, 'mention 3 steps')

    def test_create_file_upload(self):
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'file', 'name': 'Submit your report', 'description': 'Upload a PDF report.',
            'points': 20, 'group_id': self.group.id,
            'fileExtensions': 'pdf', 'fileMaxSizeMb': 10, 'fileInstructions': 'One PDF, no zip.',
        }
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertEqual(problem.submission_type, 'F')
        self.assertTrue(problem.partial)
        config = ProblemFileConfig.objects.get(problem=problem)
        self.assertEqual(config.allowed_extensions, 'pdf')
        self.assertEqual(config.max_file_size, 10 * 1024 * 1024)
        self.assertEqual(config.instructions, 'One PDF, no zip.')

    def test_file_upload_edit_data_round_trip(self):
        """_build_edit_data must hand back MB so a resave does not shrink the limit each time."""
        self.client.force_login(self.users['creator'])
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps({
            'type': 'file', 'name': 'Round trip', 'description': 'x', 'points': 5,
            'group_id': self.group.id, 'fileExtensions': 'pdf', 'fileMaxSizeMb': 7,
        }), content_type='application/json')
        problem = Problem.objects.get(code=response.json()['code'])

        data = ProblemCreateV2View()._build_edit_data(problem)
        self.assertEqual(data['type'], 'file')
        # MB, not the raw byte count — otherwise the page would show 7340032 and resave 7 TB.
        self.assertEqual(data['fileMaxSizeMb'], 7)
        self.assertEqual(data['fileExtensions'], 'pdf')

        # Echoing that value back the way the page does must leave the stored bytes unchanged.
        response = self.client.post(reverse('problem_edit_v2', args=[problem.code]), data=json.dumps({
            'type': 'file', 'edit_code': problem.code, 'name': data['name'],
            'description': data['description'], 'points': data['points'], 'group_id': self.group.id,
            'fileExtensions': data['fileExtensions'], 'fileMaxSizeMb': data['fileMaxSizeMb'],
            'fileInstructions': data['fileInstructions'],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProblemFileConfig.objects.get(problem=problem).max_file_size, 7 * 1024 * 1024)

    def test_create_file_upload_rejects_non_positive_size(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps({
            'type': 'file', 'name': 'Bad cap', 'description': 'x', 'points': 5,
            'group_id': self.group.id, 'fileMaxSizeMb': 0,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Problem.objects.filter(name='Bad cap').exists())

    def test_create_fill_blank_multiple(self):
        # The create page writes one ProblemBlank per blank; alternatives are newline-separated.
        # End-to-end grading is covered by judge/views/tests/test_fill_blank.py.
        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'blank', 'name': 'Math', 'description': '1+1={{blank}}, 2+2={{blank}}',
            'points': 2, 'group_id': self.group.id, 'blanks': ['2\ntwo', '4\nfour'],
        }
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(payload),
                                    content_type='application/json')
        problem = Problem.objects.get(code=response.json()['code'])
        blanks = list(problem.blanks.order_by('order'))
        self.assertEqual(len(blanks), 2)
        self.assertEqual(blanks[0].answers.splitlines(), ['2', 'two'])
        self.assertEqual(blanks[1].answers.splitlines(), ['4', 'four'])

    def test_ai_problem_type_endpoint(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(
            reverse('problem_create_v2_ai_problem_type'),
            data=json.dumps({'description': 'Find shortest path in a weighted graph using Dijkstra.'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['suggestions'])

    def test_save_creates_missing_problem_types_and_lists_them(self):
        """AI / free-text tags that are not in the DB yet must be created and attached on save."""
        label = 'Brand New Algorithm Tag'
        self.assertFalse(ProblemType.objects.filter(full_name=label).exists())

        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'single',
            'name': 'Typed MCQ',
            'description': 'A problem with a novel knowledge tag.',
            'points': 5,
            'group_id': self.group.id,
            'problemTypes': [label],
            'options': [
                {'text': 'A', 'correct': True},
                {'text': 'B', 'correct': False},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])

        ptype = ProblemType.objects.get(full_name=label)
        self.assertIn(ptype, list(problem.types.all()))

        # Problem list surfaces knowledge filters from ProblemType rows.
        list_response = self.client.get(reverse('problem_list'))
        self.assertEqual(list_response.status_code, 200)
        knowledge_names = {t.full_name for t in list_response.context['knowledge_types']}
        self.assertIn(label, knowledge_names)

    def test_save_reuses_existing_problem_type_without_duplicating(self):
        existing = ProblemType.objects.create(name='graph', full_name='Graph Theory')
        before = ProblemType.objects.count()

        self.client.force_login(self.users['creator'])
        payload = {
            'type': 'single',
            'name': 'Existing type',
            'description': 'reuse',
            'points': 3,
            'group_id': self.group.id,
            'problemTypes': ['Graph Theory'],
            'options': [
                {'text': 'A', 'correct': True},
                {'text': 'B', 'correct': False},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertEqual(ProblemType.objects.count(), before)
        self.assertEqual(list(problem.types.all()), [existing])

    def test_edit_code_keeps_testcases_when_payload_omits_them(self):
        self.client.force_login(self.users['staff_adder'])
        create_payload = {
            'type': 'programming',
            'name': 'Keep Cases',
            'description': 'has cases',
            'points': 10,
            'group_id': self.group.id,
            'testcases': [
                {'input': '1\n', 'output': '1\n', 'points': 10, 'pretest': False},
            ],
        }
        create_resp = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(create_payload),
            content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 200)
        code = create_resp.json()['code']
        problem = Problem.objects.get(code=code)
        self.assertEqual(problem.cases.count(), 1)

        edit_payload = {
            'type': 'programming',
            'name': 'Keep Cases Edited',
            'description': 'still has cases',
            'points': 10,
            'group_id': self.group.id,
            'edit_code': code,
            # intentionally omit testcases
        }
        edit_resp = self.client.post(
            reverse('problem_edit_v2', args=[code]),
            data=json.dumps(edit_payload),
            content_type='application/json',
        )
        self.assertEqual(edit_resp.status_code, 200)
        problem.refresh_from_db()
        self.assertEqual(problem.name, 'Keep Cases Edited')
        self.assertEqual(problem.cases.count(), 1)


class ProblemCreateV2TestcaseZipBridgeTestCase(CommonDataMixin, TestCase):
    """create-v2 loose-file path vs test_data zip archive interoperability."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = default_problem_group()
        cls.users.update({
            'staff_adder': create_user(
                username='code_zip_bridge',
                is_staff=True,
                user_permissions=('add_problem', 'create_private_problem', 'change_public_visibility',
                                  'edit_own_problem'),
            ),
        })

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_location = problem_data_storage.location
        problem_data_storage.location = self._tmpdir.name

    def tearDown(self):
        problem_data_storage.location = self._old_location
        self._tmpdir.cleanup()

    def _make_code_problem(self):
        self.client.force_login(self.users['staff_adder'])
        payload = {
            'type': 'programming',
            'name': 'Zip Bridge',
            'description': 'bridge',
            'points': 10,
            'group_id': self.group.id,
            'testcases': [
                {'input': '1\n', 'output': '1\n', 'points': 10, 'pretest': False},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        return Problem.objects.get(code=response.json()['code'])

    def test_read_testcases_from_zip_when_loose_missing(self):
        problem = self._make_code_problem()
        # Replace loose files with a zip-backed case layout (as test_data would).
        for name in ('01.in', '01.out'):
            try:
                problem_data_storage.delete('%s/%s' % (problem.code, name))
            except Exception:
                pass
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('sample.in', '9 9\n')
            zf.writestr('sample.out', '18\n')
        buf.seek(0)
        data, _ = ProblemData.objects.get_or_create(problem=problem)
        data.zipfile.save('data.zip', ContentFile(buf.read()), save=True)
        problem.cases.all().delete()
        ProblemTestCase.objects.create(
            dataset=problem, order=1, type='C',
            input_file='sample.in', output_file='sample.out',
            points=10, is_pretest=False,
        )
        ProblemTestCase.objects.create(
            dataset=problem, order=2, type='S',
            input_file='', output_file='', points=20, is_pretest=False,
        )
        ProblemTestCase.objects.create(
            dataset=problem, order=3, type='E',
            input_file='', output_file='', points=None, is_pretest=False,
        )

        view = ProblemCreateV2View()
        cases = view._read_testcases(problem)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['input'], '9 9\n')
        self.assertEqual(cases[0]['output'], '18\n')

    def test_save_testcases_rebuilds_zip_for_classic_editor(self):
        problem = self._make_code_problem()
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('old.in', 'old\n')
            zf.writestr('old.out', 'old\n')
        buf.seek(0)
        data, _ = ProblemData.objects.get_or_create(problem=problem)
        data.zipfile.save('legacy.zip', ContentFile(buf.read()), save=True)
        self.assertTrue(bool(data.zipfile))

        view = ProblemCreateV2View()
        problem.cases.all().delete()
        view._save_testcases(problem, [
            {'input': '2 3\n', 'output': '5\n', 'points': 10, 'pretest': True},
        ], checker='standard')
        data.refresh_from_db()
        self.assertTrue(bool(data.zipfile))
        with problem_data_storage.open('%s/01.in' % problem.code) as fh:
            self.assertEqual(fh.read().decode('utf-8'), '2 3\n')
        with problem_data_storage.open('%s/01.out' % problem.code) as fh:
            self.assertEqual(fh.read().decode('utf-8'), '5\n')
        # create-v2 now rebuilds data.zip so classic test_data stays in sync
        yml_path = os.path.join(problem_data_storage.location, problem.code, 'init.yml')
        if os.path.exists(yml_path):
            with open(yml_path, 'r', encoding='utf-8') as fh:
                self.assertIn('archive', fh.read())
        case = problem.cases.get(order=1)
        self.assertEqual(case.input_file, '01.in')
        self.assertTrue(case.is_pretest)
        data.refresh_from_db()
        self.assertEqual(data.checker, 'standard')

    def test_save_testcases_keeps_zip_filenames(self):
        problem = self._make_code_problem()
        view = ProblemCreateV2View()
        problem.cases.all().delete()
        view._save_testcases(problem, [
            {
                'input': '1\n', 'output': '2\n', 'points': 5, 'pretest': False,
                'input_file': 'sample.in', 'output_file': 'sample.out',
            },
        ], checker='floats')
        case = problem.cases.get(order=1)
        self.assertEqual(case.input_file, 'sample.in')
        self.assertEqual(case.output_file, 'sample.out')
        data = problem.data_files
        data.refresh_from_db()
        self.assertEqual(data.checker, 'floats')
        self.assertTrue(bool(data.zipfile))
        with zipfile.ZipFile(data.zipfile.path) as zf:
            self.assertIn('sample.in', zf.namelist())
            self.assertEqual(zf.read('sample.in').decode('utf-8'), '1\n')

    def test_pair_zip_entries_dat_in_out(self):
        pairs = ProblemCreateV2View._pair_zip_entries([
            'dat10.in', 'dat10.out', 'dat2.in', 'dat2.out', 'dat1.in', 'dat1.out',
            '__MACOSX/._dat1.in', '.DS_Store',
        ])
        self.assertEqual([p['input'] for p in pairs], ['dat1.in', 'dat2.in', 'dat10.in'])
        self.assertEqual(pairs[0]['output'], 'dat1.out')

    def test_create_code_with_multipart_zip(self):
        """Large ZIP path: metadata in JSON field, archive as multipart file (classic limits)."""
        self.client.force_login(self.users['staff_adder'])
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('dat1.in', '1 2\n')
            zf.writestr('dat1.out', '3\n')
            zf.writestr('dat2.in', '4 5\n')
            zf.writestr('dat2.out', '9\n')
        buf.seek(0)

        payload = {
            'type': 'programming',
            'name': 'Multipart Zip',
            'description': 'zip upload',
            'points': 10,
            'group_id': self.group.id,
            'checker': 'standard',
            'testcases': [
                {'input_file': 'dat1.in', 'output_file': 'dat1.out', 'points': 4, 'pretest': True},
                {'input_file': 'dat2.in', 'output_file': 'dat2.out', 'points': 6, 'pretest': False},
            ],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data={
                'payload': json.dumps(payload),
                'testcase_zip': SimpleUploadedFile('cases.zip', buf.read(), content_type='application/zip'),
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body['ok'])
        problem = Problem.objects.get(code=body['code'])
        cases = list(problem.cases.filter(type='C').order_by('order'))
        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0].input_file, 'dat1.in')
        self.assertEqual(cases[0].points, 4)
        self.assertTrue(cases[0].is_pretest)
        self.assertEqual(cases[1].points, 6)
        data = problem.data_files
        self.assertTrue(bool(data.zipfile))
        self.assertEqual(data.checker, 'standard')
        with zipfile.ZipFile(data.zipfile.path) as zf:
            self.assertEqual(zf.read('dat1.in').decode('utf-8'), '1 2\n')
            self.assertEqual(zf.read('dat2.out').decode('utf-8'), '9\n')

    def test_multipart_zip_rejects_unpaired(self):
        self.client.force_login(self.users['staff_adder'])
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('only.in', '1\n')
        buf.seek(0)
        payload = {
            'type': 'programming',
            'name': 'Bad Zip',
            'description': 'x',
            'points': 1,
            'group_id': self.group.id,
            'testcases': [],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data={
                'payload': json.dumps(payload),
                'testcase_zip': SimpleUploadedFile('bad.zip', buf.read(), content_type='application/zip'),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('paired', response.json()['error'].lower())

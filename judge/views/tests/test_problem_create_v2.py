import json
import os
import tempfile
import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from judge.models import (Problem, ProblemBlank, ProblemData, ProblemFillBlankConfig, ProblemMCQConfig,
                          ProblemShortAnswerConfig, ProblemTestCase, problem_data_storage)
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
                user_permissions=('add_problem', 'create_private_problem', 'change_public_visibility'),
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
            'type': 'blank',
            'name': 'Fill',
            'description': 'Fill {{blank}}',
            'points': 1,
            'group_id': self.group.id,
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

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

    def test_save_testcases_clears_zip_and_rewrites_loose(self):
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
        ])
        data.refresh_from_db()
        self.assertFalse(bool(data.zipfile))
        with problem_data_storage.open('%s/01.in' % problem.code) as fh:
            self.assertEqual(fh.read().decode('utf-8'), '2 3\n')
        with problem_data_storage.open('%s/01.out' % problem.code) as fh:
            self.assertEqual(fh.read().decode('utf-8'), '5\n')
        # init.yml must not reference an archive after create-v2 save
        yml_path = os.path.join(problem_data_storage.location, problem.code, 'init.yml')
        if os.path.exists(yml_path):
            with open(yml_path, 'r', encoding='utf-8') as fh:
                self.assertNotIn('archive', fh.read())

import json

from django.test import TestCase
from django.urls import reverse

from judge.models import Problem, ProblemMCQConfig, ProblemTestCase
from judge.models.tests.util import CommonDataMixin, create_user
from judge.utils.problem_create import default_problem_group


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

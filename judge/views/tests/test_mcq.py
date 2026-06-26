from django.test import TestCase
from django.urls import reverse

from judge.models import Class, Problem, ProblemMCQConfig, ProblemMCQOption
from judge.models.tests.util import CommonDataMixin, create_problem, create_user


def option_post_data(rows, multiple=False, shuffle=False, **problem_fields):
    """Build POST data for the MCQ create/edit form + option formset."""
    data = {
        'name': problem_fields.get('name', 'Quiz Q1'),
        'description': problem_fields.get('description', 'What is 2+2?'),
        'points': problem_fields.get('points', 10),
        'options-TOTAL_FORMS': str(len(rows)),
        'options-INITIAL_FORMS': str(problem_fields.get('initial_forms', 0)),
        'options-MIN_NUM_FORMS': '0',
        'options-MAX_NUM_FORMS': '1000',
    }
    if problem_fields.get('partial'):
        data['partial'] = 'on'
    if problem_fields.get('is_public'):
        data['is_public'] = 'on'
    if multiple:
        data['multiple_select'] = 'on'
    if shuffle:
        data['shuffle_options'] = 'on'
    for i, (text, correct) in enumerate(rows):
        data['options-%d-text' % i] = text
        data['options-%d-order' % i] = str(i)
        data['options-%d-id' % i] = ''
        if correct:
            data['options-%d-is_correct' % i] = 'on'
    return data


class MCQAuthoringViewTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.users.update({
            'creator': create_user(
                username='creator',
                user_permissions=('create_private_problem', 'edit_own_problem'),
            ),
            'staff_adder': create_user(
                username='staff_adder',
                is_staff=True,
                user_permissions=('add_problem', 'create_private_problem'),
            ),
        })

    def test_create_requires_permission(self):
        self.client.force_login(self.users['normal'])
        self.assertEqual(self.client.get(reverse('problem_mcq_create')).status_code, 404)
        self.client.force_login(self.users['creator'])
        self.assertEqual(self.client.get(reverse('problem_mcq_create')).status_code, 200)

    def test_create_flow(self):
        self.client.force_login(self.users['creator'])
        data = option_post_data(
            [('4', True), ('3', False), ('5', False)], name='Two plus two', points=7)
        response = self.client.post(reverse('problem_mcq_create'), data)
        self.assertEqual(response.status_code, 302)

        problem = Problem.objects.get(name='Two plus two')
        # Creating lands the teacher on the new problem itself.
        self.assertEqual(response['Location'], reverse('problem_detail', args=[problem.code]))
        self.assertEqual(problem.submission_type, 'M')
        self.assertEqual(problem.points, 7)
        # Creator is recorded as an author so they can edit it afterwards.
        self.assertIn(self.users['creator'].profile, problem.authors.all())
        # Code-centric required fields were auto-filled.
        self.assertTrue(problem.code)
        self.assertIsNotNone(problem.group_id)
        self.assertEqual(problem.mcq_options.count(), 3)
        self.assertEqual(problem.mcq_options.filter(is_correct=True).count(), 1)
        self.assertTrue(hasattr(problem, 'mcq_config'))

    def test_create_rejects_no_correct_answer(self):
        self.client.force_login(self.users['creator'])
        data = option_post_data([('a', False), ('b', False)], name='Bad quiz')
        response = self.client.post(reverse('problem_mcq_create'), data)
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        self.assertFalse(Problem.objects.filter(name='Bad quiz').exists())
        # The validation prompt must be visible on the front-end page, not only in admin.
        self.assertIn(b'Mark at least one choice as correct', response.content)

    def test_create_with_organization_is_org_private(self):
        org = self.organizations['open']
        self.client.force_login(self.users['creator'])
        data = option_post_data([('4', True), ('3', False)], name='Org quiz')
        data['organizations'] = [org.id]
        response = self.client.post(reverse('problem_mcq_create'), data)
        self.assertEqual(response.status_code, 302)

        problem = Problem.objects.get(name='Org quiz')
        self.assertTrue(problem.is_organization_private)
        self.assertIn(org, problem.organizations.all())

    def test_create_rejects_class_without_organization(self):
        org = self.organizations['open']
        cls = Class.objects.create(organization=org, name='Algo 101', slug='algo-101')
        self.client.force_login(self.users['creator'])
        data = option_post_data([('4', True), ('3', False)], name='Classless quiz')
        data['classes'] = [cls.id]  # no organizations selected
        response = self.client.post(reverse('problem_mcq_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Problem.objects.filter(name='Classless quiz').exists())

    def test_create_rejects_single_with_two_correct(self):
        self.client.force_login(self.users['creator'])
        data = option_post_data([('a', True), ('b', True)], name='Ambiguous')
        response = self.client.post(reverse('problem_mcq_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Problem.objects.filter(name='Ambiguous').exists())

    def test_edit_permission_and_manually_managed(self):
        problem = create_problem(
            code='editmcq', submission_type='M', is_public=True, is_manually_managed=True,
            authors=('creator',))
        ProblemMCQConfig.objects.create(problem=problem)
        ProblemMCQOption.objects.create(problem=problem, order=0, text='a', is_correct=True)

        # Non-editor -> 404.
        self.client.force_login(self.users['normal'])
        self.assertEqual(self.client.get(reverse('problem_mcq_edit', args=['editmcq'])).status_code, 404)

        # Editor -> 200, even though is_manually_managed is set (that guard must not apply).
        self.client.force_login(self.users['creator'])
        self.assertEqual(self.client.get(reverse('problem_mcq_edit', args=['editmcq'])).status_code, 200)

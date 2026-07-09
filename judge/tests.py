import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Class, Contest, ContestProblem, Language, Organization, Problem
from judge.models.tests.util import CommonDataMixin, create_problem_group, create_problem_type, create_user
from judge.utils.simple_xlsx import build_xlsx_workbook, read_xlsx_rows


class ProblemAdminCreationTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        create_problem_group(name='default-group')
        create_problem_type(name='default-type')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.users['superuser'])

    def problem_inline_management_data(self):
        return {
            'templates-TOTAL_FORMS': '0',
            'templates-INITIAL_FORMS': '0',
            'templates-MIN_NUM_FORMS': '0',
            'templates-MAX_NUM_FORMS': '1000',
            'language_limits-TOTAL_FORMS': '0',
            'language_limits-INITIAL_FORMS': '0',
            'language_limits-MIN_NUM_FORMS': '0',
            'language_limits-MAX_NUM_FORMS': '1000',
            'problemclarification_set-TOTAL_FORMS': '0',
            'problemclarification_set-INITIAL_FORMS': '0',
            'problemclarification_set-MIN_NUM_FORMS': '0',
            'problemclarification_set-MAX_NUM_FORMS': '1000',
            'solution-TOTAL_FORMS': '0',
            'solution-INITIAL_FORMS': '0',
            'solution-MIN_NUM_FORMS': '0',
            'solution-MAX_NUM_FORMS': '1',
            'translations-TOTAL_FORMS': '0',
            'translations-INITIAL_FORMS': '0',
            'translations-MIN_NUM_FORMS': '0',
            'translations-MAX_NUM_FORMS': '1000',
        }

    def test_admin_add_page_uses_embedded_advanced_creation_form(self):
        response = self.client.get(reverse('admin:judge_problem_add'))

        self.assertContains(response, 'name="name"', html=False)
        self.assertContains(response, 'name="description"', html=False)
        self.assertContains(response, 'name="markdown_upload"', html=False)
        self.assertContains(response, 'name="creation_preset"', html=False)
        self.assertContains(response, 'value="default"', html=False)
        self.assertContains(response, 'value="custom"', html=False)
        self.assertContains(response, 'id="embedded-advanced-toggle"', html=False)
        self.assertContains(response, 'name="embedded_advanced_mode"', html=False)
        self.assertContains(response, 'Optional Basic', html=False)
        self.assertContains(response, 'name="organizations"', html=False)
        self.assertContains(response, 'name="allowed_languages"', html=False)
        self.assertContains(response, 'name="language_limits-TOTAL_FORMS"', html=False)
        self.assertContains(response, 'name="problemclarification_set-TOTAL_FORMS"', html=False)
        self.assertContains(response, 'name="solution-TOTAL_FORMS"', html=False)
        self.assertContains(response, 'name="translations-TOTAL_FORMS"', html=False)
        self.assertContains(response, 'name="templates-TOTAL_FORMS"', html=False)
        self.assertContains(response, 'id="problem-markdown-import-input"', html=False)
        self.assertContains(response, 'id="problem-markdown-batch-import-input"', html=False)
        self.assertContains(response, 'id="problem-excel-import-input"', html=False)
        self.assertContains(response, 'id="problem-markdown-import-status"', html=False)
        self.assertContains(response, reverse('admin:judge_problem_import_markdown'), html=False)
        self.assertContains(response, reverse('admin:judge_problem_batch_create_markdown'), html=False)
        self.assertContains(response, reverse('admin:judge_problem_batch_create_excel'), html=False)
        self.assertContains(response, reverse('admin:judge_problem_download_excel_template'), html=False)
        self.assertContains(response, 'Upload Markdown', html=False)
        self.assertContains(response, 'Batch Upload Markdown', html=False)
        self.assertContains(response, 'Upload Excel', html=False)
        self.assertContains(response, 'Download Excel Template', html=False)
        self.assertContains(response, 'function escapeHtml(value)', html=False)
        self.assertContains(
            response,
            'var summary = escapeHtml(data.message || \'{{ _("Markdown uploaded successfully.")|escapejs }}\');',
            html=False,
        )

    def test_admin_advanced_add_page_uses_original_problem_form(self):
        response = self.client.get('{}?advanced=1'.format(reverse('admin:judge_problem_add')))

        self.assertContains(response, 'name="code"', html=False)
        self.assertContains(response, 'name="time_limit"', html=False)
        self.assertContains(response, 'name="allowed_languages"', html=False)
        self.assertNotContains(response, 'name="creation_preset"', html=False)

    def test_admin_change_page_uses_embedded_problem_layout_and_keeps_actions(self):
        problem = Problem.objects.create(
            code='p000123',
            name='Existing Problem',
            description='Problem statement',
            group=create_problem_group(name='existing-group'),
            time_limit=1,
            memory_limit=262144,
            points=100,
        )

        response = self.client.get(reverse('admin:judge_problem_change', args=[problem.pk]))

        self.assertContains(response, 'id="embedded-advanced-toggle"', html=False)
        self.assertContains(response, 'Optional Basic', html=False)
        self.assertNotContains(response, 'name="creation_preset"', html=False)
        self.assertNotContains(response, 'id="problem-markdown-import-input"', html=False)
        self.assertNotContains(response, 'id="problem-markdown-import-status"', html=False)
        self.assertContains(response, 'deletelink', html=False)
        self.assertContains(response, 'View submissions', html=False)
        self.assertContains(response, 'View problem votes', html=False)
        self.assertContains(response, 'Edit test data', html=False)
        self.assertContains(response, 'Clone problem', html=False)

    def test_admin_add_page_can_create_problem_with_automatic_defaults(self):
        data = {
            'name': 'Simple Problem',
            'description': 'Problem statement',
            'creation_preset': 'default',
            '_save': 'Save',
        }
        data.update(self.problem_inline_management_data())
        response = self.client.post(reverse('admin:judge_problem_add'), data=data)

        self.assertEqual(response.status_code, 302)

        problem = Problem.objects.get(name='Simple Problem')
        self.assertRegex(problem.code, r'^p\d{6}$')
        self.assertFalse(problem.is_public)
        self.assertEqual(problem.time_limit, 1)
        self.assertEqual(problem.memory_limit, 262144)
        self.assertEqual(problem.points, 100)
        self.assertEqual(problem.group.name, 'default-group')
        self.assertCountEqual(problem.types.values_list('name', flat=True), ['default-type'])
        self.assertCountEqual(
            problem.allowed_languages.values_list('id', flat=True),
            Language.objects.values_list('id', flat=True),
        )

    def test_admin_embedded_advanced_mode_can_use_manual_settings(self):
        data = {
            'embedded_advanced_mode': '1',
            'creation_preset': 'custom',
            'code': 'manualcode',
            'name': 'Manual Problem',
            'description': 'Problem statement',
            'group': create_problem_group(name='manual-group').pk,
            'types': [create_problem_type(name='manual-type').pk],
            'allowed_languages': list(Language.objects.values_list('pk', flat=True)[:2]),
            'time_limit': 2,
            'memory_limit': 131072,
            'points': 5,
            '_save': 'Save',
        }
        data.update(self.problem_inline_management_data())
        response = self.client.post(reverse('admin:judge_problem_add'), data=data)

        self.assertEqual(response.status_code, 302)
        problem = Problem.objects.get(code='manualcode')
        self.assertEqual(problem.name, 'Manual Problem')
        self.assertEqual(problem.time_limit, 2)
        self.assertEqual(problem.memory_limit, 131072)
        self.assertEqual(problem.points, 5)

    def test_admin_markdown_import_accepts_markdown_file(self):
        response = self.client.post(
            reverse('admin:judge_problem_import_markdown'),
            data={
                'markdown_file': SimpleUploadedFile(
                    'two-sum.md',
                    b'# Two Sum\r\n\r\nKeep **original** markdown.\r\n',
                    content_type='text/markdown',
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['message'], 'Created 1 problem from Markdown.')
        self.assertEqual(payload['redirect_url'], reverse('admin:judge_problem_changelist'))
        self.assertEqual(len(payload['created']), 1)

        problem = Problem.objects.get(name='Two Sum')
        self.assertEqual(problem.description, 'Keep **original** markdown.')
        self.assertEqual(payload['created'][0]['id'], problem.pk)
        self.assertEqual(payload['created'][0]['code'], problem.code)
        self.assertEqual(payload['created'][0]['name'], 'Two Sum')
        self.assertEqual(
            payload['created'][0]['edit_url'],
            reverse('admin:judge_problem_change', args=[problem.pk]),
        )

    def test_admin_markdown_import_rejects_non_markdown_file(self):
        response = self.client.post(
            reverse('admin:judge_problem_import_markdown'),
            data={
                'markdown_file': SimpleUploadedFile(
                    'two-sum.pdf',
                    b'%PDF-1.4',
                    content_type='application/pdf',
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(
            response.content.decode(),
            {
                'ok': False,
                'message': 'Only Markdown or text files are supported.',
            },
        )

    def test_admin_batch_markdown_create_creates_multiple_problems(self):
        response = self.client.post(
            reverse('admin:judge_problem_batch_create_markdown'),
            data={
                'markdown_files': [
                    SimpleUploadedFile(
                        'two-sum.md',
                        b'# Two Sum\n\nFind two numbers.\n',
                        content_type='text/markdown',
                    ),
                    SimpleUploadedFile(
                        'three-sum.md',
                        b'# Three Sum\n\nFind three numbers.\n',
                        content_type='text/markdown',
                    ),
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['message'], 'Created 2 problems from Markdown.')
        self.assertEqual(payload['redirect_url'], reverse('admin:judge_problem_changelist'))
        self.assertEqual(len(payload['created']), 2)
        self.assertEqual(
            list(
                Problem.objects.filter(name__in=['Two Sum', 'Three Sum'])
                .order_by('name')
                .values_list('name', flat=True),
            ),
            ['Three Sum', 'Two Sum'],
        )

        for problem in Problem.objects.filter(name__in=['Two Sum', 'Three Sum']):
            self.assertEqual(problem.group.name, 'default-group')
            self.assertEqual(problem.points, 100)
            self.assertFalse(problem.is_public)
            self.assertCountEqual(problem.types.values_list('name', flat=True), ['default-type'])
            self.assertCountEqual(
                problem.allowed_languages.values_list('id', flat=True),
                Language.objects.values_list('id', flat=True),
            )
            self.assertCountEqual(
                problem.authors.values_list('user__username', flat=True),
                [self.users['superuser'].username],
            )

    def test_admin_batch_markdown_create_rolls_back_on_invalid_file(self):
        response = self.client.post(
            reverse('admin:judge_problem_batch_create_markdown'),
            data={
                'markdown_files': [
                    SimpleUploadedFile(
                        'valid.md',
                        b'# Valid Problem\n\nBody.\n',
                        content_type='text/markdown',
                    ),
                    SimpleUploadedFile(
                        'invalid.pdf',
                        b'%PDF-1.4',
                        content_type='application/pdf',
                    ),
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(
            response.content.decode(),
            {
                'ok': False,
                'message': 'Only Markdown or text files are supported.',
            },
        )
        self.assertFalse(Problem.objects.filter(name='Valid Problem').exists())

    def test_admin_problem_excel_template_download(self):
        response = self.client.get(reverse('admin:judge_problem_download_excel_template'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('problem_batch_upload_template.xlsx', response['Content-Disposition'])

        rows = read_xlsx_rows(io.BytesIO(response.content))
        self.assertEqual(
            rows[0],
            ['name', 'description', 'organizations', 'classes', 'allowed_languages'],
        )

    def test_admin_problem_excel_template_respects_visible_scope(self):
        scoped_admin = create_user(
            username='scoped_admin',
            is_staff=True,
            user_permissions=('edit_own_problem', 'create_private_problem'),
        )
        self.client.force_login(scoped_admin)
        hidden_org = Organization.objects.create(
            name='Hidden Org',
            slug='hidden-org',
            short_name='HORG',
            about='hidden',
        )
        Class.objects.create(
            organization=hidden_org,
            name='hidden-class',
            slug='hidden-class',
        )
        visible_org = Organization.objects.create(
            name='Visible Org',
            slug='visible-org',
            short_name='VORG',
            about='visible',
        )
        visible_org.admins.add(scoped_admin.profile)
        visible_class = Class.objects.create(
            organization=visible_org,
            name='visible-class',
            slug='visible-class',
        )
        visible_class.admins.add(scoped_admin.profile)

        response = self.client.get(reverse('admin:judge_problem_download_excel_template'))
        rows = read_xlsx_rows(io.BytesIO(response.content))
        flattened = '\n'.join(' | '.join(row) for row in rows)

        self.assertIn('visible-org', flattened)
        self.assertIn('visible-class', flattened)
        self.assertNotIn('hidden-org', flattened)
        self.assertNotIn('hidden-class', flattened)

    def test_admin_excel_batch_create_rejects_organization_outside_visible_scope(self):
        scoped_admin = create_user(
            username='scoped_excel_admin',
            is_staff=True,
            user_permissions=('edit_own_problem', 'create_private_problem'),
        )
        self.client.force_login(scoped_admin)
        hidden_org = Organization.objects.create(
            name='Hidden Org',
            slug='hidden-org',
            short_name='HORG',
            about='hidden',
        )
        workbook = build_xlsx_workbook([
            ('Problems', [
                ['name', 'description', 'organizations', 'classes', 'allowed_languages'],
                ['Excel Problem', 'Body', hidden_org.slug, '', 'PY3'],
            ]),
        ])

        response = self.client.post(
            reverse('admin:judge_problem_batch_create_excel'),
            data={
                'excel_file': SimpleUploadedFile(
                    'problems.xlsx',
                    workbook,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(
            response.content.decode(),
            {
                'ok': False,
                'message': 'Organization "hidden-org" was not found.',
            },
        )
        self.assertFalse(Problem.objects.filter(name='Excel Problem').exists())

    def test_admin_excel_batch_create_creates_multiple_problems(self):
        organization = Organization.objects.create(
            name='Example Org',
            slug='example-org',
            short_name='EORG',
            about='Org',
        )
        active_class = Class.objects.create(
            organization=organization,
            name='example-class',
            slug='example-class',
        )
        active_class.admins.add(self.users['superuser'].profile)

        workbook = build_xlsx_workbook([
            ('Problems', [
                ['name', 'description', 'organizations', 'classes', 'allowed_languages'],
                ['Excel Problem 1', 'First body', 'example-org', 'example-class', 'PY3'],
                ['Excel Problem 2', 'Second body', '', '', ''],
            ]),
        ])

        response = self.client.post(
            reverse('admin:judge_problem_batch_create_excel'),
            data={
                'excel_file': SimpleUploadedFile(
                    'problems.xlsx',
                    workbook,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['message'], 'Created 2 problems from Excel.')
        self.assertEqual(payload['redirect_url'], reverse('admin:judge_problem_changelist'))
        self.assertEqual(len(payload['created']), 2)

        problem1 = Problem.objects.get(name='Excel Problem 1')
        self.assertEqual(problem1.description, 'First body')
        self.assertCountEqual(problem1.organizations.values_list('slug', flat=True), ['example-org'])
        self.assertCountEqual(problem1.classes.values_list('slug', flat=True), ['example-class'])
        self.assertCountEqual(problem1.allowed_languages.values_list('key', flat=True), ['PY3'])

        problem2 = Problem.objects.get(name='Excel Problem 2')
        self.assertEqual(problem2.description, 'Second body')
        self.assertFalse(problem2.organizations.exists())
        self.assertFalse(problem2.classes.exists())
        self.assertCountEqual(
            problem2.allowed_languages.values_list('id', flat=True),
            Language.objects.values_list('id', flat=True),
        )

    def test_admin_excel_batch_create_rejects_missing_headers(self):
        workbook = build_xlsx_workbook([
            ('Problems', [
                ['name', 'description'],
                ['Only Name', 'Only Description'],
            ]),
        ])

        response = self.client.post(
            reverse('admin:judge_problem_batch_create_excel'),
            data={
                'excel_file': SimpleUploadedFile(
                    'bad.xlsx',
                    workbook,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(
            response.content.decode(),
            {
                'ok': False,
                'message': 'Missing required columns: organizations, classes, allowed_languages.',
            },
        )

    def test_admin_add_page_can_fill_problem_from_markdown_upload(self):
        markdown_upload = SimpleUploadedFile(
            'sample.md',
            b'# Uploaded Problem\n\nThis is the body.\n\nSecond paragraph.\n',
            content_type='text/markdown',
        )

        data = {
            'markdown_upload': markdown_upload,
            'creation_preset': 'default',
            '_save': 'Save',
        }
        data.update(self.problem_inline_management_data())
        response = self.client.post(reverse('admin:judge_problem_add'), data=data)

        self.assertEqual(response.status_code, 302)

        problem = Problem.objects.get(name='Uploaded Problem')
        self.assertEqual(problem.description, 'This is the body.\n\nSecond paragraph.')
        self.assertRegex(problem.code, r'^p\d{6}$')


class ContestAdminCreationTestCase(CommonDataMixin, TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.users['superuser'])

    def contest_post_data(self, name='Simple Contest'):
        start = timezone.now() + timezone.timedelta(days=1)
        end = start + timezone.timedelta(hours=2)
        return {
            'name': name,
            'start_time_0': start.strftime('%Y-%m-%d'),
            'start_time_1': start.strftime('%H:%M:%S'),
            'end_time_0': end.strftime('%Y-%m-%d'),
            'end_time_1': end.strftime('%H:%M:%S'),
        }

    def test_admin_add_page_uses_embedded_advanced_creation_form(self):
        response = self.client.get(reverse('admin:judge_contest_add'))

        self.assertContains(response, 'name="name"', html=False)
        self.assertContains(response, 'name="start_time_0"', html=False)
        self.assertContains(response, 'name="end_time_0"', html=False)
        self.assertContains(response, 'contest_problems-TOTAL_FORMS', html=False)
        self.assertContains(response, 'contest_problems-0-problem', html=False)
        self.assertContains(response, 'name="creation_preset"', html=False)
        self.assertContains(response, 'id="embedded-advanced-toggle"', html=False)
        self.assertContains(response, 'name="embedded_advanced_mode"', html=False)

    def test_admin_advanced_add_page_uses_original_contest_form(self):
        response = self.client.get('{}?advanced=1'.format(reverse('admin:judge_contest_add')))

        self.assertContains(response, 'name="key"', html=False)
        self.assertContains(response, 'name="authors"', html=False)
        self.assertContains(response, 'name="format_name"', html=False)
        self.assertNotContains(response, 'name="creation_preset"', html=False)

    def test_admin_add_page_can_create_contest_with_automatic_defaults(self):
        problem_one = Problem.objects.create(
            code='contestproblemone',
            name='Contest Problem One',
            description='',
            group=create_problem_group('contest-group-1'),
            time_limit=1,
            memory_limit=65536,
            points=1,
        )
        problem_two = Problem.objects.create(
            code='contestproblemtwo',
            name='Contest Problem Two',
            description='',
            group=create_problem_group('contest-group-2'),
            time_limit=1,
            memory_limit=65536,
            points=1,
        )
        data = self.contest_post_data()
        data.update({
            'creation_preset': 'default',
            'format_config': '',
            'contest_problems-TOTAL_FORMS': '2',
            'contest_problems-INITIAL_FORMS': '0',
            'contest_problems-MIN_NUM_FORMS': '0',
            'contest_problems-MAX_NUM_FORMS': '1000',
            'contest_problems-0-problem': problem_one.pk,
            'contest_problems-0-points': '100',
            'contest_problems-0-partial': 'on',
            'contest_problems-0-order': '1',
            'contest_problems-1-problem': problem_two.pk,
            'contest_problems-1-points': '100',
            'contest_problems-1-partial': 'on',
            'contest_problems-1-order': '2',
            '_save': 'Save',
        })

        response = self.client.post(reverse('admin:judge_contest_add'), data=data)

        self.assertEqual(response.status_code, 302)

        contest = Contest.objects.get(name='Simple Contest')
        self.assertTrue(contest.key.startswith('c'))
        self.assertEqual(len(contest.key), 7)
        self.assertCountEqual(contest.authors.values_list('user__username', flat=True), ['superuser'])
        self.assertFalse(contest.is_visible)
        self.assertTrue(contest.use_clarifications)
        self.assertEqual(contest.scoreboard_visibility, Contest.SCOREBOARD_VISIBLE)
        self.assertEqual(contest.points_precision, 3)
        self.assertEqual(contest.format_name, 'default')
        self.assertEqual(contest.access_code, '')
        self.assertCountEqual(
            ContestProblem.objects.filter(contest=contest).values_list('problem__code', flat=True),
            ['contestproblemone', 'contestproblemtwo'],
        )

    def test_admin_embedded_advanced_mode_can_use_manual_settings(self):
        data = self.contest_post_data(name='Manual Contest')
        data.update({
            'embedded_advanced_mode': '1',
            'creation_preset': 'custom',
            'key': 'manualcontest',
            'authors': [self.users['superuser'].profile.pk],
            'is_visible': 'on',
            'use_clarifications': 'on',
            'scoreboard_visibility': Contest.SCOREBOARD_HIDDEN,
            'points_precision': 5,
            'format_name': 'default',
            'format_config': '',
            'access_code': 'contest-code',
            'allow_virtual_participation': 'on',
            'contest_problems-TOTAL_FORMS': '0',
            'contest_problems-INITIAL_FORMS': '0',
            'contest_problems-MIN_NUM_FORMS': '0',
            'contest_problems-MAX_NUM_FORMS': '1000',
            '_save': 'Save',
        })

        response = self.client.post(reverse('admin:judge_contest_add'), data=data)

        self.assertEqual(response.status_code, 302)
        contest = Contest.objects.get(key='manualcontest')
        self.assertEqual(contest.name, 'Manual Contest')
        self.assertTrue(contest.is_visible)
        self.assertTrue(contest.use_clarifications)
        self.assertEqual(contest.scoreboard_visibility, Contest.SCOREBOARD_HIDDEN)
        self.assertEqual(contest.points_precision, 5)
        self.assertEqual(contest.access_code, 'contest-code')

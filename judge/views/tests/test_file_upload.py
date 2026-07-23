import tempfile
from unittest import mock

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Problem, ProblemFileConfig, Submission, SubmissionFile
from judge.models.tests.util import CommonDataMixin, create_problem, create_user
from judge.storage.resilient import ResilientStorage


def _pdf(name='report.pdf', content=b'%PDF-1.4 hello'):
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class FileUploadBase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.users.update({
            'creator': create_user(
                username='creator',
                user_permissions=('create_private_problem', 'edit_own_problem'),
            ),
            'other': create_user(username='other'),
        })

    def setUp(self):
        super().setUp()
        # Route the FileField at a throwaway temp dir so tests never touch real/object storage.
        self._tmp = tempfile.TemporaryDirectory()
        storage = ResilientStorage(primary=FileSystemStorage(location=self._tmp.name), backup=None)
        self._patch = mock.patch.object(SubmissionFile._meta.get_field('file'), 'storage', storage)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()
        super().tearDown()

    def make_problem(self, code='up1', **kwargs):
        opts = dict(submission_type='F', points=10, partial=True, is_public=True,
                    submission_source_visibility_mode='O', authors=('creator',))
        opts.update(kwargs)
        problem = create_problem(code=code, **opts)
        ProblemFileConfig.objects.create(
            problem=problem, allowed_extensions='pdf', max_file_size=1024)
        return problem


class FileUploadAuthoringTestCase(FileUploadBase):
    def test_create_menu_and_page_permission(self):
        # In the "Add new problem" menu for a user who can create private problems.
        self.client.force_login(self.users['creator'])
        content = self.client.get(reverse('problem_list')).content
        self.assertIn(reverse('problem_file_create').encode(), content)
        self.assertEqual(self.client.get(reverse('problem_file_create')).status_code, 200)

        # Not available to a user without the permission.
        self.client.force_login(self.users['normal'])
        self.assertEqual(self.client.get(reverse('problem_file_create')).status_code, 404)

    def test_create_flow(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(reverse('problem_file_create'), {
            'name': 'Upload PDF', 'description': 'Upload your report', 'points': 10,
            'allowed_extensions': 'pdf', 'max_file_size': 1048576, 'instructions': 'PDF only',
            'partial': 'on',
        })
        self.assertEqual(response.status_code, 302)
        problem = Problem.objects.get(name='Upload PDF')
        self.assertEqual(problem.submission_type, 'F')
        self.assertTrue(problem.partial)
        self.assertEqual(problem.file_config.allowed_extensions, 'pdf')
        self.assertEqual(problem.file_config.max_file_size, 1048576)

    def test_edit_rejects_non_editor(self):
        self.make_problem(code='editf')
        self.client.force_login(self.users['normal'])
        self.assertEqual(self.client.get(reverse('problem_file_edit', args=['editf'])).status_code, 404)
        self.client.force_login(self.users['creator'])
        self.assertEqual(self.client.get(reverse('problem_file_edit', args=['editf'])).status_code, 200)


class FileUploadSubmitTestCase(FileUploadBase):
    def setUp(self):
        super().setUp()
        self.problem = self.make_problem(code='sub1')

    def test_submit_stores_file_and_stays_queued(self):
        self.client.force_login(self.users['normal'])
        response = self.client.post(reverse('problem_submit', args=['sub1']), {'file': _pdf()})
        self.assertEqual(response.status_code, 302)

        sub = Submission.objects.get(problem=self.problem, user=self.users['normal'].profile)
        self.assertEqual(sub.status, 'QU')          # manual: waits for a human, never auto-graded
        self.assertIsNone(sub.result)
        self.assertEqual(sub.language.key, 'ANSWER')
        self.assertEqual(sub.upload.original_name, 'report.pdf')
        self.assertEqual(sub.upload.size, len(b'%PDF-1.4 hello'))
        self.assertTrue(sub.upload.file.storage.exists(sub.upload.file.name))

    def test_submit_rejects_bad_extension(self):
        self.client.force_login(self.users['normal'])
        response = self.client.post(reverse('problem_submit', args=['sub1']),
                                    {'file': _pdf(name='evil.exe')})
        self.assertEqual(response.status_code, 200)  # re-render with error, no redirect
        self.assertContains(response, 'not allowed')
        self.assertFalse(Submission.objects.filter(problem=self.problem).exists())

    def test_submit_rejects_oversize(self):
        self.client.force_login(self.users['normal'])
        big = _pdf(content=b'x' * 4096)  # config cap is 1024 bytes
        response = self.client.post(reverse('problem_submit', args=['sub1']), {'file': big})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too large')
        self.assertFalse(Submission.objects.filter(problem=self.problem).exists())

    def test_detail_and_submit_pages_render_file_form(self):
        self.client.force_login(self.users['normal'])
        for url in (reverse('problem_detail', args=['sub1']), reverse('problem_submit', args=['sub1'])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'multipart/form-data')
            self.assertContains(response, 'type="file"')
            self.assertNotContains(response, 'id_source')  # no Ace code editor on a file problem


class FileUploadGradeDownloadTestCase(FileUploadBase):
    def setUp(self):
        super().setUp()
        self.problem = self.make_problem(code='gd1')
        self.sub = Submission.objects.create(
            user=self.users['normal'].profile, problem=self.problem,
            language=Language.get_answer_language(),
        )
        SubmissionFile.objects.create(
            submission=self.sub, file=_pdf(), original_name='report.pdf', size=14)

    def test_grade_updates_points(self):
        self.client.force_login(self.users['creator'])
        response = self.client.post(reverse('submission_file_grade', args=[self.sub.id]),
                                    {'points': '7', 'feedback': 'Nice work'})
        self.assertEqual(response.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, 'D')
        self.assertEqual(self.sub.points, 7)
        self.assertEqual(self.sub.error, 'Nice work')

    def test_grade_clamps_to_max(self):
        self.client.force_login(self.users['creator'])
        self.client.post(reverse('submission_file_grade', args=[self.sub.id]), {'points': '999'})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.points, 10)
        self.assertEqual(self.sub.result, 'AC')

    def test_grade_requires_editor(self):
        self.client.force_login(self.users['normal'])
        response = self.client.post(reverse('submission_file_grade', args=[self.sub.id]), {'points': '5'})
        self.assertEqual(response.status_code, 403)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, 'QU')

    def test_download_allowed_for_owner_denied_for_other(self):
        self.client.force_login(self.users['normal'])            # the submitter
        response = self.client.get(reverse('submission_file_download', args=[self.sub.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('report.pdf', response['Content-Disposition'])

        self.client.force_login(self.users['other'])             # unrelated user (ONLY_OWN visibility)
        self.assertEqual(
            self.client.get(reverse('submission_file_download', args=[self.sub.id])).status_code, 403)

    def test_rejudge_is_noop(self):
        with mock.patch('judge.models.submission.judge_submission') as bridge:
            self.sub.judge(rejudge=True, force_judge=True)
        bridge.assert_not_called()
        self.sub.refresh_from_db()
        self.assertNotEqual(self.sub.result, 'IE')

    def test_status_page_shows_grade_form_and_download(self):
        # Editor sees the inline grade form, the download link, and the awaiting-grading note.
        self.client.force_login(self.users['creator'])
        response = self.client.get(reverse('submission_status', args=[self.sub.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('submission_file_grade', args=[self.sub.id]))
        self.assertContains(response, reverse('submission_file_download', args=[self.sub.id]))
        self.assertContains(response, 'Awaiting grading')

        # A non-editor viewer does not get the grade form.
        self.client.force_login(self.users['normal'])
        response = self.client.get(reverse('submission_status', args=[self.sub.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('submission_file_grade', args=[self.sub.id]))

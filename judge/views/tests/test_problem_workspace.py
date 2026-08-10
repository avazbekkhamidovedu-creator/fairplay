import json
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    ContestSubmission,
    Judge,
    Language,
    ProblemMCQConfig,
    ProblemMCQOption,
    Submission,
    SubmissionSource,
)
from judge.models.tests.util import (
    create_contest,
    create_contest_participation,
    create_contest_problem,
    create_problem,
    create_user,
)


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

        cls.mcq = create_problem(code='workspace-mcq', is_public=True, submission_type='M')
        ProblemMCQConfig.objects.create(problem=cls.mcq, multiple_select=False)
        cls.mcq_opt_wrong = ProblemMCQOption.objects.create(
            problem=cls.mcq, order=0, text='Option Alpha', is_correct=False,
        )
        cls.mcq_opt_right = ProblemMCQOption.objects.create(
            problem=cls.mcq, order=1, text='Option Beta', is_correct=True,
        )

        cls.author = create_user(username='workspace-author')
        cls.contest = create_contest(
            key='wsassign', name='Workspace Assignment',
            start_time=timezone.now() - timezone.timedelta(hours=1),
            end_time=timezone.now() + timezone.timedelta(hours=2),
            is_visible=True,
            authors=('workspace-author',),
        )
        cls.contest_problem = create_contest_problem(
            contest=cls.contest, problem=cls.problem, points=100, order=1,
        )

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

    def test_selected_own_submission_exposes_result_and_source_for_editor_restore(self):
        submission = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='AC', status='D', points=100, time=0.01, memory=1024,
        )
        SubmissionSource.objects.create(submission=submission, source='print(42)')
        response = self.client.get(
            reverse('problem_detail', args=[self.problem.code]) + '?submission=%d' % submission.id,
        )
        self.assertContains(response, 'id="submitted-source"')
        self.assertContains(response, 'print(42)')
        self.assertContains(response, 'id="btn-load-editor"')
        self.assertContains(response, 'id="workspace-result-source"')
        self.assertContains(response, 'result-source-details')

    def test_mcq_result_shows_human_readable_submitted_source(self):
        submission = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='AC', status='D', points=100, time=0, memory=0,
        )
        SubmissionSource.objects.create(
            submission=submission,
            source=json.dumps({'selected': [self.mcq_opt_right.id]}),
        )
        status = self.client.get(reverse(
            'problem_workspace_submission_status', args=[self.mcq.code, submission.id],
        ))
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertTrue(any('Option Beta' in (row.get('value') or '') for row in body['answer_display']))
        self.assertIn('comments_html', body)
        detail = self.client.get(
            reverse('problem_detail', args=[self.mcq.code]) + '?submission=%d' % submission.id,
        )
        self.assertContains(detail, 'Option Beta')
        self.assertContains(detail, 'Submitted source')
        self.assertNotContains(detail, 'Your answer')
        self.assertNotContains(detail, 'Raw submitted answer')
        self.assertContains(detail, 'id="workspace-result-comments"')

    def test_workspace_submissions_list_returns_oj_style_html_rows(self):
        submission = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='AC', status='D', points=100, case_points=100, case_total=100,
            time=0.01, memory=1024,
        )
        response = self.client.get(
            reverse('problem_workspace_submissions', args=[self.problem.code]) + '?scope=my',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['scope'], 'my')
        self.assertEqual(len(payload['rows']), 1)
        html = payload['rows'][0]['html']
        self.assertIn('submission-row', html)
        self.assertIn('ws-submission-link', html)
        self.assertIn('view', html)
        self.assertIn(str(submission.id), html)

    def test_workspace_submit_returns_json_and_status_payload(self):
        with mock.patch.object(Submission, 'judge'):
            response = self.client.post(
                reverse('problem_submit', args=[self.problem.code]),
                {'workspace': '1', 'source': 'print(1)', 'language': self.language.id, 'judge': ''},
                HTTP_X_WORKSPACE_SUBMIT='1',
            )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        submission = Submission.objects.get(id=payload['submission_id'])
        self.assertEqual(submission.problem, self.problem)
        status = self.client.get(payload['status_url'])
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body['id'], submission.id)
        self.assertIn('status', body)
        self.assertIn('source', body)
        self.assertEqual(body['source'], 'print(1)')

    def test_assignment_my_submissions_filters_by_contest_object(self):
        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()

        in_contest = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='AC', status='D', points=100, contest_object=self.contest,
            time=0.01, memory=1024,
        )
        ContestSubmission.objects.create(
            submission=in_contest, problem=self.contest_problem, participation=participation,
        )
        practice = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='WA', status='D', points=0, contest_object=None,
            time=0.01, memory=1024,
        )

        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.context['workspace_mode'], 'assignment')
        ids = [row.id for row in response.context['workspace_submissions']]
        self.assertEqual(ids, [in_contest.id])
        self.assertNotIn(practice.id, ids)
        self.assertContains(response, '?submission=%d' % in_contest.id)
        self.assertNotContains(response, 'data-sub="all"')
        self.assertNotContains(response, 'data-sub="best"')

    def test_practice_still_shows_all_and_best_tabs(self):
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.context['workspace_mode'], 'practice')
        self.assertContains(response, 'data-sub="all"')
        self.assertContains(response, 'data-sub="best"')

    def test_practice_workspace_submissions_json_without_current_contest(self):
        submission = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='AC', status='D', points=100, time=0.01, memory=1024,
        )
        response = self.client.get(
            reverse('problem_workspace_submissions', args=[self.problem.code]) + '?scope=my',
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload['scope'], 'my')
        self.assertEqual([row['id'] for row in payload['rows']], [submission.id])

    def test_practice_drawer_has_filter_controls(self):
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.context['workspace_mode'], 'practice')
        self.assertContains(response, 'id="fp-status"', html=False)
        self.assertContains(response, 'id="fp-type"', html=False)
        self.assertContains(response, 'id="fp-course"', html=False)
        self.assertContains(response, 'id="fp-tags"', html=False)
        self.assertContains(response, 'id="fp-ac-min"', html=False)
        self.assertContains(response, 'id="fp-diff"', html=False)
        self.assertNotContains(response, 'id="fp-group"', html=False)
        self.assertIn('workspace_organizations', response.context)

    def test_assignment_workspace_exposes_context_id_for_drafts(self):
        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.context['workspace_mode'], 'assignment')
        self.assertContains(response, 'data-context-id="%s"' % self.contest.key)
        self.assertContains(response, 'id="workspace-submit-form"')

    def test_practice_workspace_form_disables_browser_autocomplete(self):
        response = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(response.context['workspace_mode'], 'practice')
        self.assertNotContains(response, 'data-context-id="wsassign"')
        # Fill-blank/MCQ fields get autocomplete off via JS and/or form widgets.
        content = response.content.decode()
        self.assertTrue(
            'autocomplete="off"' in content or 'autocomplete="new-password"' in content,
            'expected autocomplete disabled on workspace answer form',
        )

    def test_workspace_empty_code_submit_returns_json_error(self):
        response = self.client.post(
            reverse('problem_submit', args=[self.problem.code]),
            {'workspace': '1', 'source': '', 'language': self.language.id, 'judge': ''},
            HTTP_X_WORKSPACE_SUBMIT='1',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertIn('error', response.json())
        self.assertNotIn('<!DOCTYPE', response.content.decode()[:40])

    def test_mcq_answer_display_uses_snapshot_when_options_recreated(self):
        from judge.views.problem import get_workspace_answer_display

        submission = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='WA', status='D', points=0, time=0, memory=0,
        )
        # Legacy raw ids (option deleted) + snapshot from a newer submit path.
        SubmissionSource.objects.create(
            submission=submission,
            source=json.dumps({
                'selected': [999001],
                'choices': [{'id': 999001, 'order': 0, 'text': 'Snapshotted True'}],
            }),
        )
        rows = get_workspace_answer_display(submission)
        self.assertTrue(any('Snapshotted True' in (row.get('value') or '') for row in rows))

        stale = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='WA', status='D', points=0, time=0, memory=0,
        )
        SubmissionSource.objects.create(
            submission=stale,
            source=json.dumps({'selected': [999002]}),
        )
        stale_rows = get_workspace_answer_display(stale)
        self.assertTrue(stale_rows)
        self.assertNotIn('999002', stale_rows[0]['value'])
        self.assertIn('no longer available', stale_rows[0]['value'])

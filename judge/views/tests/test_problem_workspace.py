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
    ProblemTemplate,
    Submission,
    SubmissionSource,
)
from judge.models.tests.util import (
    create_contest,
    create_contest_participation,
    create_contest_problem,
    create_organization,
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

    def test_assignment_rejects_practice_submission_query(self):
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
        SubmissionSource.objects.create(submission=in_contest, source='print("contest")')
        practice = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='WA', status='D', points=0, contest_object=None,
            time=0.01, memory=1024,
        )
        SubmissionSource.objects.create(submission=practice, source='print("practice")')

        blocked = self.client.get(
            reverse('problem_detail', args=[self.problem.code]) + '?submission=%d' % practice.id,
        )
        self.assertEqual(blocked.context['workspace_mode'], 'assignment')
        self.assertIsNone(blocked.context['workspace_submission'])

        allowed = self.client.get(
            reverse('problem_detail', args=[self.problem.code]) + '?submission=%d' % in_contest.id,
        )
        self.assertIsNotNone(allowed.context['workspace_submission'])
        self.assertEqual(allowed.context['workspace_submission']['id'], in_contest.id)

    def test_assignment_scope_all_and_best_forbidden(self):
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

        for scope in ('all', 'best'):
            response = self.client.get(
                reverse('problem_workspace_submissions', args=[self.problem.code]) + '?scope=%s' % scope,
            )
            self.assertEqual(response.status_code, 403, scope)
            self.assertIn('error', response.json())

        mine = self.client.get(
            reverse('problem_workspace_submissions', args=[self.problem.code]) + '?scope=my',
        )
        self.assertEqual(mine.status_code, 200)
        ids = [row['id'] for row in mine.json()['rows']]
        self.assertEqual(ids, [in_contest.id])
        self.assertNotIn(practice.id, ids)

    def test_assignment_status_rejects_practice_submission(self):
        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()

        practice = Submission.objects.create(
            user=self.user.profile, problem=self.problem, language=self.language,
            result='WA', status='D', points=0, contest_object=None,
            time=0.01, memory=1024,
        )
        response = self.client.get(
            reverse('problem_workspace_submission_status', args=[self.problem.code, practice.id]),
        )
        self.assertEqual(response.status_code, 403)

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

    def test_workspace_toolbar_has_progress_not_in_drawer(self):
        practice = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertContains(practice, 'ws-toolbar-progress')
        self.assertContains(practice, 'id="toolbar-progress-bar"', html=False)
        self.assertNotContains(practice, 'id="drawer-bar"', html=False)
        self.assertNotContains(practice, 'id="drawer-solved-frac"', html=False)

        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        assignment = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(assignment.context['workspace_mode'], 'assignment')
        self.assertContains(assignment, 'ws-toolbar-progress')
        self.assertContains(assignment, 'id="toolbar-progress-bar"', html=False)
        self.assertNotContains(assignment, 'id="drawer-progress"', html=False)
        self.assertNotContains(practice, 'id="drawer-close"', html=False)
        self.assertNotContains(assignment, 'id="drawer-close"', html=False)

    def test_practice_course_filter_lists_membership_without_public_sentinel(self):
        mine = create_organization(name='WsMine')
        other = create_organization(name='WsOther')
        self.user.profile.organizations.add(mine)
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        names = {org.name for org in response.context['workspace_organizations']}
        self.assertIn(mine.name, names)
        self.assertNotIn(other.name, names)
        course = response.content.decode().split('id="fp-course"', 1)[1].split('</select>', 1)[0]
        self.assertNotIn('value="public"', course)
        self.assertNotRegex(course, r'>\s*Public\s*<')
        self.assertIn(mine.name, course)
        self.assertNotIn(other.name, course)
        # Public problems stay in the drawer; filtering is "all courses" when none are selected.
        self.assertContains(response, 'data-public="1"', html=False)

    def test_anonymous_problem_detail_stays_in_practice(self):
        # `authed and current_contest` is False for guests; that must not be
        # treated as an active participation (bool has no .contest).
        response = Client().get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['workspace_in_contest'])
        self.assertEqual(response.context['workspace_mode'], 'practice')

    def test_assignment_drawer_stays_on_contest_for_other_problems(self):
        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()

        on_assignment = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(on_assignment.context['workspace_mode'], 'assignment')
        self.assertTrue(on_assignment.context['workspace_in_contest'])
        self.assertEqual(
            [cp.problem.code for cp in on_assignment.context['workspace_problems']],
            [self.problem.code],
        )

        # Public problem outside this assignment must not switch the drawer to practice.
        outside = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(outside.context['workspace_mode'], 'assignment')
        self.assertTrue(outside.context['workspace_in_contest'])
        self.assertEqual(
            [cp.problem.code for cp in outside.context['workspace_problems']],
            [self.problem.code],
        )

    def test_expired_live_window_rejoins_as_spectator(self):
        from judge.models import ContestParticipation

        contest = create_contest(
            key='wsexpired', name='Expired Window',
            start_time=timezone.now() - timezone.timedelta(hours=2),
            end_time=timezone.now() + timezone.timedelta(hours=2),
            time_limit=timezone.timedelta(minutes=1),
            is_visible=True,
            authors=('workspace-author',),
        )
        create_contest_participation(
            contest=contest, user=self.user.profile,
            real_start=timezone.now() - timezone.timedelta(hours=1),
        )
        self.user.profile.current_contest = None
        self.user.profile.save()

        response = self.client.post(reverse('contest_join', args=[contest.key]))
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertIsNotNone(self.user.profile.current_contest)
        self.assertTrue(self.user.profile.current_contest.spectate)
        self.assertEqual(self.user.profile.current_contest.virtual, ContestParticipation.SPECTATE)

    def test_assignment_workspace_exposes_context_id_for_drafts(self):
        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.context['workspace_mode'], 'assignment')
        self.assertContains(response, 'data-context-id="%s"' % self.contest.key)
        self.assertContains(response, 'id="workspace-submit-form"')

    def test_assignment_toolbar_puts_leave_on_submit_row(self):
        participation = create_contest_participation(contest=self.contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertContains(response, 'ws-toolbar has-leave')
        self.assertContains(response, 'ws-toolbar-center')
        self.assertContains(response, 'id="btn-submit"')
        self.assertContains(response, reverse('contest_leave', args=[self.contest.key]))
        self.assertContains(response, 'assignment-leave-form')
        self.assertContains(response, 'Leave')

    def test_practice_workspace_has_no_leave_button(self):
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.context['workspace_mode'], 'practice')
        self.assertNotContains(response, 'assignment-leave-form')
        self.assertNotContains(response, reverse('contest_leave', args=[self.contest.key]))

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

    def test_ai_hints_tab_unavailable_copy_is_simple(self):
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertContains(response, 'AI hints are not available for this problem.')
        self.assertNotContains(response, 'complexity budget')
        self.assertNotContains(response, 'smallest counterexample')
        self.assertNotContains(response, 'input-output relation')
        self.assertNotContains(response, 'classic status page')

    def test_ai_hints_tab_available_copy_is_simple(self):
        problem = create_problem(code='ws-ai-hint', is_public=True, ai_hints_enabled=True)
        with mock.patch('judge.ai_hints.is_configured', return_value=True):
            response = self.client.get(reverse('problem_detail', args=[problem.code]))
        self.assertContains(response, 'These hints help you think.')
        self.assertContains(response, 'Submit a solution first.')
        self.assertContains(response, 'Get AI Hint')
        self.assertNotContains(response, 'classic status page')
        self.assertNotContains(response, 'non-AC formal')
        self.assertNotContains(response, 'complexity budget')

    def test_ai_hints_possible_when_assignment_allows(self):
        problem = create_problem(code='ws-ai-asg', is_public=True, ai_hints_enabled=False)
        contest = create_contest(
            key='wsaiasg', name='AI Assignment',
            start_time=timezone.now() - timezone.timedelta(hours=1),
            end_time=timezone.now() + timezone.timedelta(days=1),
            allow_ai_hints=True,
            is_visible=True,
        )
        create_contest_problem(contest=contest, problem=problem, points=100, order=1)
        participation = create_contest_participation(contest=contest, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        with mock.patch('judge.ai_hints.is_configured', return_value=True):
            response = self.client.get(reverse('problem_detail', args=[problem.code]))
        self.assertTrue(response.context['ai_hints_possible'])
        problem.refresh_from_db()
        self.assertFalse(problem.ai_hints_enabled)

    def test_mcq_result_shows_verdict_and_score_over_max(self):
        self.mcq.points = 10
        self.mcq.save(update_fields=['points'])
        submission = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='WA', status='D', points=0, time=0, memory=0,
        )
        SubmissionSource.objects.create(
            submission=submission,
            source=json.dumps({'selected': [self.mcq_opt_wrong.id]}),
        )
        status = self.client.get(reverse(
            'problem_workspace_submission_status', args=[self.mcq.code, submission.id],
        ))
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body['result'], 'WA')
        self.assertEqual(body['points'], 0)
        self.assertEqual(body['points_total'], 10)
        detail = self.client.get(
            reverse('problem_detail', args=[self.mcq.code]) + '?submission=%d' % submission.id,
        )
        self.assertContains(detail, 'Judging results')
        self.assertContains(detail, 'case-WA')
        self.assertContains(detail, 'Score:')
        self.assertContains(detail, '0 / 10')
        self.assertNotContains(detail, ' · 0')
        self.assertNotContains(detail, 'Execution results')
        self.assertNotContains(detail, 'Execution Results')
        self.assertNotContains(detail, 'Test case #')

    def test_exam_drawer_hides_mcq_score_until_finalize(self):
        exam = create_contest(
            key='wsexamhide', name='Workspace Exam',
            format_name='exam', is_visible=True,
            start_time=timezone.now() - timezone.timedelta(hours=1),
            end_time=timezone.now() + timezone.timedelta(hours=2),
            authors=('workspace-author',),
        )
        exam_mcq = create_contest_problem(contest=exam, problem=self.mcq, points=10, order=1)
        participation = create_contest_participation(contest=exam, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        submission = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='AC', status='D', points=10, contest_object=exam, time=0, memory=0,
        )
        ContestSubmission.objects.create(
            submission=submission, problem=exam_mcq, participation=participation, points=10,
        )

        hidden = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(hidden.context['workspace_mode'], 'exam')
        self.assertTrue(hidden.context['workspace_exam_hide_scores'])
        self.assertContains(hidden, 'drawer-score tried')
        self.assertContains(hidden, '—/10')
        self.assertContains(hidden, 'attempted-problem-color')
        self.assertNotContains(hidden, 'solved-problem-color')
        self.assertNotContains(hidden, '10/10')

        exam.results_finalized = True
        exam.save(update_fields=['results_finalized'])
        shown = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertFalse(shown.context['workspace_exam_hide_scores'])
        self.assertContains(shown, 'drawer-score got')
        self.assertContains(shown, '10/10')
        self.assertContains(shown, 'solved-problem-color')

    def test_exam_drawer_uses_latest_mcq_score_after_finalize(self):
        exam = create_contest(
            key='wsexamlatest', name='Workspace Exam Latest',
            format_name='exam', is_visible=True,
            start_time=timezone.now() - timezone.timedelta(hours=1),
            end_time=timezone.now() + timezone.timedelta(hours=2),
            authors=('workspace-author',),
            results_finalized=True,
        )
        exam_mcq = create_contest_problem(contest=exam, problem=self.mcq, points=10, order=1)
        participation = create_contest_participation(contest=exam, user=self.user.profile)
        self.user.profile.current_contest = participation
        self.user.profile.save()
        first = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='AC', status='D', points=10, contest_object=exam, time=0, memory=0,
        )
        ContestSubmission.objects.create(
            submission=first, problem=exam_mcq, participation=participation, points=10,
        )
        Submission.objects.filter(id=first.id).update(date=timezone.now() - timezone.timedelta(minutes=5))
        latest = Submission.objects.create(
            user=self.user.profile, problem=self.mcq, language=Language.get_answer_language(),
            result='WA', status='D', points=0, contest_object=exam, time=0, memory=0,
        )
        ContestSubmission.objects.create(
            submission=latest, problem=exam_mcq, participation=participation, points=0,
        )

        response = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(response.context['workspace_best_points'][self.mcq.id], 0)
        self.assertContains(response, 'drawer-score tried')
        self.assertContains(response, '0/10')
        self.assertContains(response, 'attempted-problem-color')
        self.assertNotContains(response, 'solved-problem-color')

    def test_workspace_prefills_problem_template_for_selected_language(self):
        starter = 'def solve():\n    # problem-template-marker\n    pass\n'
        ProblemTemplate.objects.create(problem=self.problem, language=self.language, code=starter)
        self.user.profile.language = self.language
        self.user.profile.save()

        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertContains(response, 'problem-template-marker')
        self.assertContains(response, 'id="workspace-language-templates"')
        templates = response.context['workspace_language_templates']
        self.assertEqual(templates[str(self.language.id)], starter)
        self.assertEqual(response.context['form'].initial.get('source'), starter)
        html = response.content.decode()
        start = html.find('id="workspace-language-templates">')
        self.assertNotEqual(start, -1)
        start = html.find('>', start) + 1
        payload = json.loads(html[start:html.find('</script>', start)])
        self.assertEqual(payload[str(self.language.id)], starter)

    def test_language_template_ajax_prefers_problem_template(self):
        self.language.template = 'print("language-default")\n'
        self.language.save()
        ProblemTemplate.objects.create(
            problem=self.problem, language=self.language, code='print("problem-starter")\n',
        )
        response = self.client.get(reverse('language_template_ajax'), {
            'id': self.language.id,
            'problem_id': self.problem.id,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), 'print("problem-starter")\n')

    def test_language_template_ajax_falls_back_to_language_template(self):
        self.language.template = 'print("language-default")\n'
        self.language.save()
        response = self.client.get(reverse('language_template_ajax'), {
            'id': self.language.id,
            'problem_id': self.problem.id,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), 'print("language-default")\n')

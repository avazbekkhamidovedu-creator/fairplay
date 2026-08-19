"""Dual-role coverage of contest AI grading across modes and problem types.

Creates two homeworks (on-demand / realtime), two exams (batch), and two
assignments with AI grading off. Each uses a distinct problem set mixing
AI-capable types (code+rubric, short answer, fill-blank AI) and types that
must never call AI (MCQ, file upload, code without AI, fill-blank exact).

Student and teacher clients hit the same URLs for every case.
"""
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest, ContestSubmission, Language, ProblemBlank, ProblemCodeConfig, ProblemFileConfig,
    ProblemFillBlankConfig, ProblemMCQConfig, ProblemMCQOption, ProblemShortAnswerConfig,
    Submission, SubmissionSource,
)
from judge.models.question import GRADE_AI, GRADE_EXACT
from judge.models.tests.util import (
    CommonDataMixin, create_contest, create_contest_participation, create_contest_problem,
    create_problem, create_user,
)
from judge.utils.contest_ai import (
    problem_has_ai_grading, should_run_ai_now, student_can_trigger_ai,
)


def _done_sub(user, problem, contest, source, *, status='D', result='AC', language=None):
    lang = language or Language.get_default_language()
    sub = Submission.objects.create(
        user=user.profile, problem=problem, language=lang,
        status=status, result=result, contest_object=contest,
        time=0, memory=0, points=0,
        case_points=0, case_total=float(problem.points or 0),
    )
    SubmissionSource.objects.create(submission=sub, source=source)
    return sub


class AIGradingDualRoleTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = create_user(username='aie2e_teacher')
        cls.student = create_user(username='aie2e_student')
        cls.other = create_user(username='aie2e_other')
        cls.py = Language.objects.get(key='PY3')
        cls.answer = Language.get_answer_language()
        now = timezone.now()

        def bank(prefix, kinds):
            """Build a distinct problem list. kinds is a list of type tags."""
            problems = []
            for i, kind in enumerate(kinds, start=1):
                code = f'{prefix}p{i}{kind}'
                if kind == 'codeai':
                    p = create_problem(code=code, submission_type='C', points=100, is_public=True)
                    p.allowed_languages.add(cls.py)
                    ProblemCodeConfig.objects.create(problem=p, ai_enabled=True, ai_rubric='style')
                elif kind == 'codeno':
                    p = create_problem(code=code, submission_type='C', points=100, is_public=True)
                    p.allowed_languages.add(cls.py)
                    ProblemCodeConfig.objects.create(problem=p, ai_enabled=False)
                elif kind == 'mcq':
                    p = create_problem(code=code, submission_type='M', points=10, is_public=True)
                    ProblemMCQConfig.objects.create(problem=p, multiple_select=False)
                    ProblemMCQOption.objects.create(problem=p, order=0, text='wrong', is_correct=False)
                    ProblemMCQOption.objects.create(problem=p, order=1, text='right', is_correct=True)
                elif kind == 'sa':
                    p = create_problem(code=code, submission_type='S', points=20, is_public=True)
                    ProblemShortAnswerConfig.objects.create(
                        problem=p, reference_answer='42', ai_rubric='full credit if 42',
                    )
                elif kind == 'fbai':
                    p = create_problem(code=code, submission_type='B', points=10, is_public=True)
                    ProblemFillBlankConfig.objects.create(problem=p, grading_mode=GRADE_AI, ai_rubric='blanks')
                    ProblemBlank.objects.create(problem=p, order=0, answers='yes', points=10)
                elif kind == 'fbex':
                    p = create_problem(code=code, submission_type='B', points=10, is_public=True)
                    ProblemFillBlankConfig.objects.create(problem=p, grading_mode=GRADE_EXACT)
                    ProblemBlank.objects.create(problem=p, order=0, answers='paris', points=10)
                elif kind == 'file':
                    p = create_problem(code=code, submission_type='F', points=10, is_public=True)
                    ProblemFileConfig.objects.create(problem=p, allowed_extensions='pdf')
                else:
                    raise ValueError(kind)
                problems.append((kind, p))
            return problems

        specs = [
            ('hw1', 'Homework 1 Algorithms', Contest.AI_GRADING_REALTIME, 'default',
             ['codeai', 'mcq', 'file']),
            ('hw2', 'Homework 2 Written', Contest.AI_GRADING_REALTIME, 'default',
             ['sa', 'fbex', 'codeno']),
            ('ex1', 'Exam 1 Midterm', Contest.AI_GRADING_BATCH, 'exam',
             ['codeai', 'mcq', 'fbai']),
            ('ex2', 'Exam 2 Final', Contest.AI_GRADING_BATCH, 'exam',
             ['sa', 'codeno', 'file']),
            ('off1', 'Practice Off A', Contest.AI_GRADING_OFF, 'default',
             ['codeai', 'mcq']),
            ('off2', 'Practice Off B', Contest.AI_GRADING_OFF, 'default',
             ['sa', 'fbai']),
        ]
        cls.contests = {}
        for key, name, mode, fmt, kinds in specs:
            contest = create_contest(
                key=key, name=name, authors=('aie2e_teacher',),
                ai_grading_mode=mode, format_name=fmt, is_visible=True,
                start_time=now - timezone.timedelta(hours=1),
                end_time=now + timezone.timedelta(hours=3),
            )
            problems = bank(key, kinds)
            cps = []
            for order, (kind, problem) in enumerate(problems, start=1):
                cps.append((kind, problem, create_contest_problem(
                    contest=contest, problem=problem, points=problem.points, order=order,
                )))
            part = create_contest_participation(contest=contest, user=cls.student.profile)
            create_contest_participation(contest=contest, user=cls.other.profile)
            cls.contests[key] = {
                'contest': contest, 'mode': mode, 'problems': cps, 'part': part, 'format': fmt,
            }

    def setUp(self):
        cache.clear()
        self.student_client = Client()
        self.student_client.force_login(self.student)
        self.teacher_client = Client()
        self.teacher_client.force_login(self.teacher)
        self.other_client = Client()
        self.other_client.force_login(self.other)

    def _subs_for(self, key):
        info = self.contests[key]
        rows = []
        for kind, problem, cp in info['problems']:
            if kind in ('codeai', 'codeno'):
                src, lang = 'print(1)', self.py
            elif kind == 'mcq':
                opt = problem.mcq_options.get(is_correct=True)
                src, lang = '{"selected": [%d]}' % opt.id, self.answer
            elif kind == 'sa':
                src, lang = '{"text": "42"}', self.answer
            elif kind in ('fbai', 'fbex'):
                src, lang = '{"answers": ["yes"]}', self.answer
            else:
                src, lang = '{"file": "x.pdf"}', self.answer
            sub = _done_sub(self.student, problem, info['contest'], src, language=lang)
            ContestSubmission.objects.create(
                submission=sub, problem=cp, participation=info['part'], points=0,
            )
            rows.append((kind, problem, sub))
        return rows

    def _status(self, client, sub):
        return client.get(reverse('submission_status', args=[sub.id]))

    def _ai_post(self, client, sub):
        return client.post(reverse('submission_ai_grade', args=[sub.id]))

    def test_problem_type_ai_capability(self):
        mapping = {
            'codeai': True, 'codeno': False, 'mcq': False, 'sa': True,
            'fbai': True, 'fbex': False, 'file': False,
        }
        for info in self.contests.values():
            for kind, problem, _cp in info['problems']:
                self.assertEqual(
                    problem_has_ai_grading(problem), mapping[kind],
                    msg='%s %s' % (info['contest'].key, kind),
                )

    def test_homework_on_demand_never_auto_grades(self):
        for key in ('hw1', 'hw2'):
            for kind, problem, _cp in self.contests[key]['problems']:
                sub = _done_sub(self.student, problem, self.contests[key]['contest'], 'x')
                self.assertFalse(should_run_ai_now(sub), msg='%s %s' % (key, kind))
                if problem_has_ai_grading(problem):
                    self.assertTrue(should_run_ai_now(sub, manual_trigger=True))

    def test_exam_batch_only_runs_on_batch_pass(self):
        for key in ('ex1', 'ex2'):
            for kind, problem, _cp in self.contests[key]['problems']:
                sub = _done_sub(self.student, problem, self.contests[key]['contest'], 'x')
                self.assertFalse(should_run_ai_now(sub))
                if problem_has_ai_grading(problem):
                    self.assertTrue(should_run_ai_now(sub, batch_pass=True))

    def test_off_never_runs_ai(self):
        for key in ('off1', 'off2'):
            for kind, problem, _cp in self.contests[key]['problems']:
                sub = _done_sub(self.student, problem, self.contests[key]['contest'], 'x')
                self.assertFalse(should_run_ai_now(sub))
                self.assertFalse(student_can_trigger_ai(self.student, sub))
                self.assertFalse(student_can_trigger_ai(self.teacher, sub))

    def test_student_quota_zero_hides_button_teacher_sees_it_on_homework_ai(self):
        for key in ('hw1', 'hw2'):
            for kind, problem, sub in self._subs_for(key):
                has_ai = problem_has_ai_grading(problem)
                self.assertFalse(student_can_trigger_ai(self.student, sub), msg=kind)
                student_page = self._status(self.student_client, sub)
                self.assertEqual(student_page.status_code, 200)
                self.assertNotContains(student_page, 'View AI grading')
                teacher_page = self._status(self.teacher_client, sub)
                self.assertEqual(teacher_page.status_code, 200)
                if has_ai:
                    self.assertTrue(student_can_trigger_ai(self.teacher, sub), msg=kind)
                    self.assertContains(teacher_page, 'View AI grading')
                else:
                    self.assertFalse(student_can_trigger_ai(self.teacher, sub), msg=kind)
                    self.assertNotContains(teacher_page, 'View AI grading')

    def test_exam_student_no_button_teacher_can_trigger_ai_types(self):
        for key in ('ex1', 'ex2'):
            for kind, problem, sub in self._subs_for(key):
                student_page = self._status(self.student_client, sub)
                self.assertEqual(student_page.status_code, 200)
                self.assertNotContains(student_page, 'View AI grading')
                teacher_page = self._status(self.teacher_client, sub)
                self.assertEqual(teacher_page.status_code, 200)
                if problem_has_ai_grading(problem):
                    self.assertTrue(student_can_trigger_ai(self.teacher, sub), msg='%s %s' % (key, kind))
                    self.assertContains(teacher_page, 'View AI grading')
                else:
                    self.assertNotContains(teacher_page, 'View AI grading')

    def test_off_neither_role_sees_button(self):
        for key in ('off1', 'off2'):
            for kind, problem, sub in self._subs_for(key):
                for client in (self.student_client, self.teacher_client):
                    page = self._status(client, sub)
                    self.assertEqual(page.status_code, 200)
                    self.assertNotContains(page, 'View AI grading')

    def test_other_student_cannot_view_or_trigger(self):
        _, _, sub = self._subs_for('hw1')[0]
        page = self._status(self.other_client, sub)
        self.assertIn(page.status_code, (302, 403))
        resp = self._ai_post(self.other_client, sub)
        self.assertIn(resp.status_code, (302, 403))

    def test_anonymous_redirected(self):
        _, _, sub = self._subs_for('hw1')[0]
        anon = Client()
        self.assertEqual(anon.get(reverse('submission_status', args=[sub.id])).status_code, 302)
        self.assertEqual(anon.post(reverse('submission_ai_grade', args=[sub.id])).status_code, 302)

    def test_older_submission_cannot_trigger(self):
        kind, problem, cp = self.contests['hw1']['problems'][0]
        older = _done_sub(self.student, problem, self.contests['hw1']['contest'], 'old')
        newer = _done_sub(self.student, problem, self.contests['hw1']['contest'], 'new')
        self.assertFalse(student_can_trigger_ai(self.teacher, older))
        self.assertTrue(student_can_trigger_ai(self.teacher, newer))

    def test_queued_submission_cannot_trigger(self):
        kind, problem, cp = self.contests['hw1']['problems'][0]
        sub = _done_sub(self.student, problem, self.contests['hw1']['contest'], 'q', status='QU', result=None)
        self.assertFalse(student_can_trigger_ai(self.teacher, sub))
        self.assertFalse(student_can_trigger_ai(self.student, sub))

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=0)
    def test_student_post_rejected_when_quota_zero(self):
        for kind, problem, sub in self._subs_for('hw1'):
            if not problem_has_ai_grading(problem):
                continue
            resp = self._ai_post(self.student_client, sub)
            self.assertEqual(resp.status_code, 403, msg=kind)
            self.assertIn('not available', resp.json()['error'].lower())

    def test_teacher_post_skips_quota_and_runs(self):
        ai_sub = None
        for kind, problem, sub in self._subs_for('hw1'):
            if problem_has_ai_grading(problem):
                ai_sub = sub
                break
        with mock.patch('judge.llm.is_configured', return_value=True), \
                mock.patch('judge.utils.contest_ai.run_ai_for_submission', return_value='code-ai') as run:
            resp = self._ai_post(self.teacher_client, ai_sub)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['ok'])
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs.get('manual_trigger'))

    def test_teacher_post_on_mcq_forbidden(self):
        for kind, problem, sub in self._subs_for('hw1'):
            if kind == 'mcq':
                resp = self._ai_post(self.teacher_client, sub)
                self.assertEqual(resp.status_code, 403)
                return
        self.fail('no mcq')

    def test_unconfigured_llm_returns_503_for_teacher(self):
        for kind, problem, sub in self._subs_for('hw2'):
            if kind != 'sa':
                continue
            with mock.patch('judge.llm.is_configured', return_value=False):
                resp = self._ai_post(self.teacher_client, sub)
            self.assertEqual(resp.status_code, 503)
            return
        self.fail('no sa')

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=10)
    def test_student_with_quota_can_trigger_homework_ai_only(self):
        cache.clear()
        for key in ('hw1', 'hw2', 'ex1', 'off1'):
            for kind, problem, sub in self._subs_for(key):
                if not problem_has_ai_grading(problem):
                    self.assertFalse(student_can_trigger_ai(self.student, sub), msg='%s %s' % (key, kind))
                    continue
                if key in ('hw1', 'hw2'):
                    self.assertTrue(student_can_trigger_ai(self.student, sub), msg='%s %s' % (key, kind))
                    page = self._status(self.student_client, sub)
                    self.assertContains(page, 'View AI grading')
                else:
                    self.assertFalse(student_can_trigger_ai(self.student, sub), msg='%s %s' % (key, kind))

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=10)
    def test_student_rate_limit_five_per_problem(self):
        cache.clear()
        with mock.patch('judge.llm.is_configured', return_value=True), \
                mock.patch('judge.utils.contest_ai.run_ai_for_submission', return_value='ok'):
            sa = [row for row in self._subs_for('hw2') if row[0] == 'sa'][0][2]
            for i in range(5):
                resp = self._ai_post(self.student_client, sa)
                self.assertEqual(resp.status_code, 200, 'attempt %s %s' % (i, resp.content))
            resp = self._ai_post(self.student_client, sa)
            self.assertEqual(resp.status_code, 429)
            page = self._status(self.student_client, sa)
            self.assertNotContains(page, 'View AI grading')
            # A different AI problem is still allowed (limit is per problem).
            code_ai = [row for row in self._subs_for('hw1') if row[0] == 'codeai'][0][2]
            resp = self._ai_post(self.student_client, code_ai)
            self.assertEqual(resp.status_code, 200, resp.content)

    def test_contest_pages_explain_mode_to_both_roles(self):
        for key, needle in (
            ('hw1', 'on demand'),
            ('hw2', 'on demand'),
            ('ex1', 'after the assignment ends'),
            ('ex2', 'after the assignment ends'),
        ):
            url = reverse('contest_view', args=[key])
            for client in (self.student_client, self.teacher_client):
                html = client.get(url).content.decode().lower()
                self.assertIn(needle, html, msg='%s' % key)

    def test_grade_page_batch_button_teacher_only(self):
        for key, expect_btn in (('hw1', False), ('ex1', True), ('ex2', True), ('off1', False)):
            url = reverse('contest_grade', args=[key])
            teacher = self.teacher_client.get(url)
            self.assertEqual(teacher.status_code, 200, key)
            if expect_btn:
                self.assertTrue(teacher.context['grade_ai_batch'])
                self.assertIn('Run AI grading', teacher.content.decode())
            else:
                self.assertFalse(teacher.context['grade_ai_batch'])
            student = self.student_client.get(url)
            self.assertIn(student.status_code, (302, 403, 404))

    def test_batch_endpoint_student_forbidden_homework_rejected_exam_ok(self):
        student = self.student_client.post(reverse('contest_grade_ai', args=['ex1']))
        self.assertEqual(student.status_code, 403)
        teacher_hw = self.teacher_client.post(reverse('contest_grade_ai', args=['hw1']))
        self.assertEqual(teacher_hw.status_code, 400)
        teacher_off = self.teacher_client.post(reverse('contest_grade_ai', args=['off1']))
        self.assertEqual(teacher_off.status_code, 400)
        with mock.patch('judge.tasks.contest.batch_ai_grade.delay'):
            with self.captureOnCommitCallbacks(execute=True):
                teacher_ex = self.teacher_client.post(reverse('contest_grade_ai', args=['ex1']))
        self.assertEqual(teacher_ex.status_code, 200, teacher_ex.content)
        self.assertTrue(teacher_ex.json()['ok'])

    def test_create_form_shows_on_demand_label(self):
        editor = create_user(
            username='aie2e_editor',
            user_permissions=(
                'edit_all_contest', 'create_private_contest', 'change_contest_visibility',
            ),
        )
        client = Client()
        client.force_login(editor)
        html = client.get(reverse('contest_create')).content.decode()
        self.assertIn('id_ai_grading_mode', html)
        self.assertIn('On demand', html)

    def test_http_submit_short_answer_homework_does_not_queue_ai(self):
        info = self.contests['hw2']
        sa = [p for kind, p, _cp in info['problems'] if kind == 'sa'][0]
        self.student.profile.current_contest = info['part']
        self.student.profile.save()
        resp = self.student_client.post(
            reverse('problem_submit', args=[sa.code]),
            {'answer': 'the answer is 42'},
        )
        self.assertEqual(resp.status_code, 302, resp.content)
        sub = Submission.objects.filter(user=self.student.profile, problem=sa).latest('id')
        self.assertEqual(sub.contest_object_id, info['contest'].id)
        self.assertEqual(sub.status, 'D')
        self.assertNotEqual(sub.status, 'QU')
        self.assertEqual(sub.result, 'IR')

    def test_http_submit_mcq_never_offers_ai(self):
        info = self.contests['hw1']
        mcq = [p for kind, p, _cp in info['problems'] if kind == 'mcq'][0]
        opt = mcq.mcq_options.get(is_correct=True)
        self.student.profile.current_contest = info['part']
        self.student.profile.save()
        resp = self.student_client.post(
            reverse('problem_submit', args=[mcq.code]),
            {'options': str(opt.id)},
        )
        self.assertEqual(resp.status_code, 302, resp.content)
        sub = Submission.objects.filter(user=self.student.profile, problem=mcq).latest('id')
        self.assertEqual(sub.result, 'AC')
        page = self._status(self.student_client, sub)
        self.assertNotContains(page, 'View AI grading')
        page = self._status(self.teacher_client, sub)
        self.assertNotContains(page, 'View AI grading')

    def test_http_submit_fill_blank_exact_no_ai_button(self):
        info = self.contests['hw2']
        fb = [p for kind, p, _cp in info['problems'] if kind == 'fbex'][0]
        self.student.profile.current_contest = info['part']
        self.student.profile.save()
        resp = self.student_client.post(
            reverse('problem_submit', args=[fb.code]),
            {'fb_0': 'paris'},
        )
        self.assertEqual(resp.status_code, 302, resp.content)
        sub = Submission.objects.filter(user=self.student.profile, problem=fb).latest('id')
        self.assertFalse(problem_has_ai_grading(fb))
        self.assertNotContains(self._status(self.student_client, sub), 'View AI grading')
        self.assertNotContains(self._status(self.teacher_client, sub), 'View AI grading')

    def test_http_submit_exam_fill_blank_ai_marks_batch_pending(self):
        info = self.contests['ex1']
        fb = [p for kind, p, _cp in info['problems'] if kind == 'fbai'][0]
        self.student.profile.current_contest = info['part']
        self.student.profile.save()
        resp = self.student_client.post(
            reverse('problem_submit', args=[fb.code]),
            {'fb_0': 'yes'},
        )
        self.assertEqual(resp.status_code, 302, resp.content)
        sub = Submission.objects.filter(user=self.student.profile, problem=fb).latest('id')
        self.assertEqual(sub.error, 'AI_BATCH_PENDING')
        student_page = self._status(self.student_client, sub)
        self.assertContains(student_page, 'Results hidden')
        self.assertNotContains(student_page, 'View AI grading')
        teacher_page = self._status(self.teacher_client, sub)
        self.assertContains(teacher_page, 'Awaiting AI grading')
        self.assertContains(teacher_page, 'View AI grading')

    def test_http_submit_exam_short_answer_marks_batch_pending(self):
        info = self.contests['ex2']
        sa = [p for kind, p, _cp in info['problems'] if kind == 'sa'][0]
        self.student.profile.current_contest = info['part']
        self.student.profile.save()
        resp = self.student_client.post(
            reverse('problem_submit', args=[sa.code]),
            {'answer': 'batch me'},
        )
        self.assertEqual(resp.status_code, 302, resp.content)
        sub = Submission.objects.filter(user=self.student.profile, problem=sa).latest('id')
        self.assertEqual(sub.error, 'AI_BATCH_PENDING')
        page = self._status(self.student_client, sub)
        self.assertContains(page, 'Results hidden')
        self.assertNotContains(page, 'View AI grading')
        teacher_page = self._status(self.teacher_client, sub)
        self.assertContains(teacher_page, 'Awaiting AI grading')
        self.assertContains(teacher_page, 'View AI grading')

    def test_http_submit_off_short_answer_finishes_without_ai_button(self):
        info = self.contests['off2']
        sa = [p for kind, p, _cp in info['problems'] if kind == 'sa'][0]
        self.student.profile.current_contest = info['part']
        self.student.profile.save()
        resp = self.student_client.post(
            reverse('problem_submit', args=[sa.code]),
            {'answer': 'should not ai'},
        )
        self.assertEqual(resp.status_code, 302, resp.content)
        sub = Submission.objects.filter(user=self.student.profile, problem=sa).latest('id')
        self.assertEqual(sub.status, 'D')
        self.assertNotEqual(sub.error, 'AI_BATCH_PENDING')
        self.assertNotContains(self._status(self.student_client, sub), 'View AI grading')
        self.assertNotContains(self._status(self.teacher_client, sub), 'View AI grading')

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=10)
    def test_practice_short_answer_defers_to_ai_button(self):
        """No contest → submit stores the answer; LLM runs only on button."""
        p = create_problem(code='practiceai', submission_type='S', points=10, is_public=True)
        ProblemShortAnswerConfig.objects.create(problem=p, reference_answer='x')
        self.student_client.post(
            reverse('problem_submit', args=[p.code]),
            {'answer': 'maybe x'},
        )
        sub = Submission.objects.filter(user=self.student.profile, problem=p).latest('id')
        self.assertFalse(should_run_ai_now(sub))
        self.assertTrue(should_run_ai_now(sub, manual_trigger=True))
        self.assertEqual(sub.result, 'IR')
        page = self._status(self.student_client, sub)
        self.assertContains(page, 'View AI grading')

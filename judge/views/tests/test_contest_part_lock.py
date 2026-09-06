"""Lock assignment parts: student chooses which part to start first."""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import AssignmentDraft, ContestParticipation, ContestProblem, Language, ProblemMCQOption, Submission
from judge.models.tests.util import create_contest, create_problem, create_user
from judge.utils.assignment_harvest import harvest_participation
from judge.utils.contest_parts import ide_blocked_for_profile, part_lock_applies, problem_part_state


class PartLockTestMixin:
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.teacher = create_user(username='partlock_teacher')
        cls.student = create_user(username='partlock_student')
        cls.python = Language.objects.get(key='PY3')
        cls.mcq = create_problem('partlock-mcq', submission_type='M', is_public=False)
        cls.option = ProblemMCQOption.objects.create(problem=cls.mcq, order=1, text='right', is_correct=True)
        ProblemMCQOption.objects.create(problem=cls.mcq, order=2, text='wrong', is_correct=False)
        cls.code = create_problem('partlock-code', submission_type='C', is_public=False,
                                  allowed_languages=('PY3',))
        cls.contest = create_contest(
            key='partlockasn',
            name='Part lock assignment',
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            authors=('partlock_teacher',),
            is_visible=True,
            lock_non_code_part=True,
        )
        ContestProblem.objects.create(contest=cls.contest, problem=cls.mcq, points=10, order=1)
        ContestProblem.objects.create(contest=cls.contest, problem=cls.code, points=90, order=2)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.student)

    def _join(self):
        participation = ContestParticipation.objects.create(
            contest=self.contest, user=self.student.profile,
        )
        self.student.profile.current_contest = participation
        self.student.profile.save()
        return participation

    def _start(self, part):
        return self.client.post(
            reverse('contest_start_part', args=[self.contest.key]),
            {'part': part},
        )


class LockNonCodePartTestCase(PartLockTestMixin, TestCase):
    def test_part_lock_applies_when_mixed_types(self):
        self.assertTrue(part_lock_applies(self.contest))

    def test_must_choose_part_before_opening_problems(self):
        self._join()
        for problem in (self.mcq, self.code):
            response = self.client.get(reverse('problem_detail', args=[problem.code]))
            self.assertEqual(response.status_code, 403)
            self.assertContains(response, 'Choose a part first', status_code=403)
            self.assertContains(response, 'Problems page', status_code=403)

    def test_start_non_code_opens_only_that_part(self):
        self._join()
        response = self._start('non_code')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.mcq.code])).status_code, 200)
        locked = self.client.get(reverse('problem_detail', args=[self.code.code]))
        self.assertEqual(locked.status_code, 403)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 403)

    def test_start_code_opens_only_that_part(self):
        self._join()
        response = self._start('code')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.code.code])).status_code, 200)
        locked = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(locked.status_code, 403)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 200)

    def test_finish_non_code_unlocks_code_and_locks_non_code(self):
        participation = self._join()
        self._start('non_code')
        response = self.client.post(reverse('contest_finish_non_code_part', args=[self.contest.key]))
        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        self.assertIsNotNone(participation.non_code_part_completed_at)
        self.assertEqual(problem_part_state(participation, self.code, self.student), 'open')
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.code.code])).status_code, 200)
        locked = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(locked.status_code, 403)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 200)

    def test_finish_code_unlocks_non_code_and_closes_ide(self):
        participation = self._join()
        self._start('code')
        response = self.client.post(reverse('contest_finish_non_code_part', args=[self.contest.key]))
        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        self.assertIsNotNone(participation.code_part_completed_at)
        self.assertEqual(problem_part_state(participation, self.mcq, self.student), 'open')
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.mcq.code])).status_code, 200)
        locked = self.client.get(reverse('problem_detail', args=[self.code.code]))
        self.assertEqual(locked.status_code, 403)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 403)

    def test_cannot_change_chosen_part(self):
        participation = self._join()
        self._start('non_code')
        self._start('code')
        participation.refresh_from_db()
        self.assertEqual(participation.chosen_part, 'non_code')
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.code.code])).status_code, 403)

    def test_ide_blocked_until_programming_part(self):
        self._join()
        self.assertEqual(self.client.get(reverse('ide')).status_code, 403)
        self.assertEqual(self.client.post(reverse('ide_submit'), {}).status_code, 403)

    def test_ide_blocked_after_leave_until_programming_part(self):
        self._join()
        self.student.profile.current_contest = None
        self.student.profile.save()
        self.assertTrue(ide_blocked_for_profile(self.student.profile, self.student))
        self.assertEqual(self.client.get(reverse('ide')).status_code, 403)

    def test_teacher_bypasses_lock(self):
        self.client.force_login(self.teacher)
        participation = ContestParticipation.objects.create(
            contest=self.contest, user=self.teacher.profile,
        )
        self.teacher.profile.current_contest = participation
        self.teacher.profile.save()
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.code.code])).status_code, 200)
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.mcq.code])).status_code, 200)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 200)

    def test_setting_off_does_not_lock(self):
        self.contest.lock_non_code_part = False
        self.contest.save(update_fields=['lock_non_code_part'])
        self._join()
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.code.code])).status_code, 200)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 200)

    def test_only_code_problems_setting_is_noop(self):
        now = timezone.now()
        code_only = create_problem('partlock-onlycode', submission_type='C', is_public=False)
        contest = create_contest(
            key='partlockcode',
            name='Code only',
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            authors=('partlock_teacher',),
            is_visible=True,
            lock_non_code_part=True,
        )
        ContestProblem.objects.create(contest=contest, problem=code_only, points=100, order=1)
        self.assertFalse(part_lock_applies(contest))
        participation = ContestParticipation.objects.create(
            contest=contest, user=self.student.profile,
        )
        self.student.profile.current_contest = participation
        self.student.profile.save()
        self.assertEqual(self.client.get(reverse('problem_detail', args=[code_only.code])).status_code, 200)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 200)

    def test_only_non_code_blocks_ide_for_whole_assignment(self):
        now = timezone.now()
        quiz = create_problem('partlock-onlymcq', submission_type='M', is_public=False)
        contest = create_contest(
            key='partlockquiz',
            name='Quiz only',
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            authors=('partlock_teacher',),
            is_visible=True,
            lock_non_code_part=True,
        )
        ContestProblem.objects.create(contest=contest, problem=quiz, points=10, order=1)
        participation = ContestParticipation.objects.create(
            contest=contest, user=self.student.profile,
        )
        self.student.profile.current_contest = participation
        self.student.profile.save()
        self.assertEqual(self.client.get(reverse('problem_detail', args=[quiz.code])).status_code, 200)
        self.assertEqual(self.client.get(reverse('ide')).status_code, 403)

    def test_join_redirects_to_problem_list(self):
        response = self.client.post(reverse('contest_join', args=[self.contest.key]))
        self.assertRedirects(response, reverse('problem_list'))

    def test_assignment_list_opens_current_detail_while_joined(self):
        self._join()
        response = self.client.get(reverse('contest_list'))
        self.assertRedirects(response, reverse('contest_view', args=[self.contest.key]))

    def test_problems_are_not_links_before_join(self):
        self.mcq.is_public = True
        self.mcq.save(update_fields=['is_public'])
        response = self.client.get(reverse('contest_view', args=[self.contest.key]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.mcq.name)
        self.assertNotContains(response, reverse('problem_detail', args=[self.mcq.code]))
        self.assertNotContains(response, reverse('problem_detail', args=[self.code.code]))

    def test_problem_list_offers_part_choice(self):
        self._join()
        response = self.client.get(reverse('problem_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('contest_start_part', args=[self.contest.key]))
        self.assertContains(response, 'Start non-programming questions')
        self.assertContains(response, 'Start programming problems')
        self.assertNotContains(response, reverse('problem_detail', args=[self.mcq.code]))
        self.assertNotContains(response, reverse('problem_detail', args=[self.code.code]))

    def test_contest_page_offers_part_choice(self):
        self._join()
        response = self.client.get(reverse('contest_view', args=[self.contest.key]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('contest_start_part', args=[self.contest.key]))
        self.assertContains(response, 'Start non-programming questions')
        self.assertContains(response, 'Start programming problems')
        self.assertNotContains(response, 'Finish this part')

    def test_workspace_puts_finish_left_of_submit(self):
        self._join()
        self._start('non_code')
        response = self.client.get(reverse('problem_detail', args=[self.mcq.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        finish_at = html.find('finish-non-code-part-form')
        submit_at = html.find('id="btn-submit"')
        leave_at = html.find('assignment-leave-form')
        self.assertGreater(finish_at, -1)
        self.assertGreater(submit_at, -1)
        self.assertGreater(leave_at, -1)
        self.assertLess(leave_at, finish_at)
        self.assertLess(finish_at, submit_at)

    def test_contest_page_shows_finish_after_choice(self):
        self._join()
        self._start('non_code')
        response = self.client.get(reverse('contest_view', args=[self.contest.key]))
        self.assertContains(response, reverse('contest_finish_non_code_part', args=[self.contest.key]))
        self.assertContains(response, 'Finish this part')

    def test_the_finish_confirm_says_the_saved_answers_are_submitted(self):
        # Finishing collects this part's drafts on the way out, so the confirm has to name that
        # too. A student who reads only "you cannot return" does not know what was just sent.
        self._join()
        self._start('non_code')
        response = self.client.get(reverse('contest_view', args=[self.contest.key]))
        self.assertContains(
            response,
            'Your saved answers for the non-programming questions will be submitted now. '
            'You cannot return to them. Continue to the programming problems?',
        )

    def test_the_finish_confirm_names_the_part_being_handed_in(self):
        self._join()
        self._start('code')
        response = self.client.get(reverse('contest_view', args=[self.contest.key]))
        self.assertContains(response, 'Your saved answers for the programming problems will be submitted now.')
        self.assertNotContains(response, 'Your saved answers for the non-programming questions')

    def test_start_and_finish_require_join(self):
        start = self.client.post(
            reverse('contest_start_part', args=[self.contest.key]),
            {'part': 'non_code'},
        )
        self.assertEqual(start.status_code, 403)
        finish = self.client.post(reverse('contest_finish_non_code_part', args=[self.contest.key]))
        self.assertEqual(finish.status_code, 403)


class FinishPartHarvestTestCase(PartLockTestMixin, TestCase):
    """Finishing a part puts it out of reach, so its drafts have to be collected on the way out.

    Everywhere else a draft can wait for the deadline. Here it cannot: the moment the completion
    timestamp lands, these problems answer 403 for this student, and an answer left as a draft
    would be stranded somewhere they can no longer submit it.
    """

    def _finish(self):
        return self.client.post(reverse('contest_finish_non_code_part', args=[self.contest.key]))

    def _mcq_draft(self, participation):
        return AssignmentDraft.objects.create(
            participation=participation, problem=self.mcq,
            content=json.dumps({'selected': [self.option.id]}),
        )

    def _code_draft(self, participation, source='print(1)'):
        return AssignmentDraft.objects.create(
            participation=participation, problem=self.code, content=source, language=self.python,
        )

    def _submissions(self, problem):
        return Submission.objects.filter(
            user=self.student.profile, contest_object=self.contest, problem=problem,
        )

    def test_finishing_a_part_collects_its_drafts(self):
        participation = self._join()
        self._start('non_code')
        draft = self._mcq_draft(participation)

        self._finish()

        draft.refresh_from_db()
        self.assertIsNotNone(draft.harvested_at)
        self.assertEqual(self._submissions(self.mcq).count(), 1)

    def test_the_locked_part_is_left_with_nothing_uncollected(self):
        participation = self._join()
        self._start('non_code')
        self._mcq_draft(participation)

        self._finish()

        self.assertFalse(
            AssignmentDraft.objects
            .filter(participation=participation, problem=self.mcq, harvested_at__isnull=True)
            .exists(),
        )
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.mcq.code])).status_code, 403)

    def test_finishing_the_first_part_leaves_the_second_part_alone(self):
        # The programming problems are only just being unlocked; collecting them now would submit
        # work the student has not started.
        participation = self._join()
        self._start('non_code')
        self._mcq_draft(participation)
        code_draft = self._code_draft(participation)

        self._finish()

        code_draft.refresh_from_db()
        self.assertIsNone(code_draft.harvested_at)
        self.assertFalse(self._submissions(self.code).exists())

    def test_the_second_part_is_collected_at_the_deadline(self):
        participation = self._join()
        self._start('non_code')
        self._mcq_draft(participation)
        code_draft = self._code_draft(participation)
        self._finish()

        harvest_participation(participation)

        code_draft.refresh_from_db()
        self.assertIsNotNone(code_draft.harvested_at)
        self.assertEqual(self._submissions(self.code).count(), 1)

    def test_finishing_the_code_part_collects_only_code(self):
        participation = self._join()
        self._start('code')
        code_draft = self._code_draft(participation)
        mcq_draft = self._mcq_draft(participation)

        self._finish()

        code_draft.refresh_from_db()
        mcq_draft.refresh_from_db()
        self.assertIsNotNone(code_draft.harvested_at)
        self.assertIsNone(mcq_draft.harvested_at)

    def test_a_student_who_has_not_chosen_a_part_submits_nothing(self):
        participation = self._join()

        self._finish()

        participation.refresh_from_db()
        self.assertIsNone(participation.non_code_part_completed_at)
        self.assertIsNone(participation.code_part_completed_at)
        self.assertFalse(Submission.objects.filter(contest_object=self.contest).exists())
        self.assertEqual(harvest_participation(participation), [])

    def test_a_failed_collection_leaves_the_part_open_to_retry(self):
        # Locking on a failed collection would put the answers somewhere the student cannot
        # reach and cannot submit; staying open costs them another click instead.
        participation = self._join()
        self._start('non_code')
        draft = self._mcq_draft(participation)

        with mock.patch('judge.views.contests.harvest_participation', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self._finish()

        participation.refresh_from_db()
        draft.refresh_from_db()
        self.assertIsNone(participation.non_code_part_completed_at)
        self.assertIsNone(draft.harvested_at)
        self.assertEqual(self.client.get(reverse('problem_detail', args=[self.mcq.code])).status_code, 200)

        self._finish()

        participation.refresh_from_db()
        draft.refresh_from_db()
        self.assertIsNotNone(participation.non_code_part_completed_at)
        self.assertIsNotNone(draft.harvested_at)

    def test_a_failed_lock_takes_the_collection_back_with_it(self):
        # Either both halves land or neither does. A collected draft next to a part that stayed
        # open would be submitted a second time when the student retries.
        participation = self._join()
        self._start('non_code')
        draft = self._mcq_draft(participation)

        # Fail only the timestamp write: grading a collected answer saves the participation too,
        # and breaking that would prove nothing about the two halves being tied together.
        real_save = ContestParticipation.save

        def fail_on_the_lock(participation, *args, **kwargs):
            if 'non_code_part_completed_at' in (kwargs.get('update_fields') or ()):
                raise RuntimeError('boom')
            return real_save(participation, *args, **kwargs)

        with mock.patch.object(ContestParticipation, 'save', autospec=True, side_effect=fail_on_the_lock):
            with self.assertRaises(RuntimeError):
                self._finish()

        participation.refresh_from_db()
        draft.refresh_from_db()
        self.assertIsNone(participation.non_code_part_completed_at)
        self.assertIsNone(draft.harvested_at)
        self.assertFalse(self._submissions(self.mcq).exists())

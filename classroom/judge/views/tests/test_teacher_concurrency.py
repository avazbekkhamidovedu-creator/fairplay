"""Teacher write paths under concurrency (500+ professors editing at once).

Each test replays one race deterministically: right before our INSERT (after
every "is it free?" check already passed), a "rival" row -- what another
teacher's request committed a millisecond earlier -- is put in place, so the
INSERT really hits the unique index, exactly as under load. The rival is
inserted when the code under test opens the savepoint around its INSERT (it
must be outside that savepoint, or rolling the savepoint back would remove it). Before the fixes these
requests answered HTTP 500 (IntegrityError), a confusing 400, or silently
overwrote another teacher's work. The real multi-request reproduction (threads
against gunicorn) is described in docs/classroom/reports/teacher-concurrency.md.
"""
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.db import IntegrityError, OperationalError, connection, transaction
from django.shortcuts import get_object_or_404
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from judge.models import (Class, ClassMaterial, Contest, ContestParticipation, ContestProblem, ContestSubmission,
                          Language, Problem, ProblemGroup, ProblemType, Submission, SubmissionManualGrade)
from judge.models.question import ProblemImportDraft, ProblemImportItem
from judge.models.tests.util import CommonDataMixin, create_contest, create_problem, create_user
from judge.utils.db_retry import retry_on_deadlock
from judge.utils.edit_conflict import contest_edit_fingerprint, problem_edit_fingerprint
from judge.utils.problem_create import default_problem_group, save_new_problem
from judge.utils.unique_ids import UniqueIdTaken, save_with_unique_id


class _AtomicShim:
    def __init__(self, nth, make_rival, state):
        self._nth, self._make_rival, self._state = nth, make_rival, state

    def __getattr__(self, name):
        return getattr(transaction, name)

    def atomic(self, *args, **kwargs):
        self._state['entered'] += 1
        if self._state['entered'] == self._nth:
            rival = self._make_rival()
            type(rival).objects.bulk_create([rival])
            self._state['fired'] = True
        return transaction.atomic(*args, **kwargs)


@contextmanager
def rival_committed_before(module, make_rival, nth=1, insert_model=None):
    """Another request commits ``make_rival()`` just before ``module`` opens its ``nth`` atomic block.

    ``insert_model``: also fire right before the first INSERT of that model if the atomic block
    never came (code without the savepoint, i.e. before the fix), so the race is still replayed.
    """
    state = {'entered': 0, 'fired': False}
    with mock.patch('%s.transaction' % module, _AtomicShim(nth, make_rival, state)):
        if insert_model is None:
            yield state
            return
        original = insert_model.save

        def save(self, *args, **kwargs):
            if not state['fired'] and self.pk is None:
                state['fired'] = True
                rival = make_rival()
                type(rival).objects.bulk_create([rival])
            return original(self, *args, **kwargs)

        with mock.patch.object(insert_model, 'save', save):
            yield state


UNIQUE_IDS = 'judge.utils.unique_ids'
CREATE_V2 = 'judge.views.problem_create_v2'


def rival_problem(code):
    return lambda: Problem(code=code, name='Rival teacher problem', description='theirs', points=1,
                           time_limit=1, memory_limit=262144, group=default_problem_group(), date=timezone.now())


def mcq_payload(**extra):
    payload = {
        'type': 'single', 'name': 'My question', 'description': 'Pick one.', 'points': 5,
        'problemTypes': ['Loops'], 'difficulty': 'easy',
        'options': [{'text': 'A', 'correct': True}, {'text': 'B', 'correct': False}],
    }
    payload.update(extra)
    return payload


class SaveWithUniqueIdTestCase(TestCase):
    def test_generated_code_taken_meanwhile_moves_to_next_free(self):
        problem = Problem(code='p000000', name='Mine', points=1, time_limit=1, memory_limit=262144,
                          group=default_problem_group(), date=timezone.now())
        with rival_committed_before(UNIQUE_IDS, rival_problem('p000000')) as state:
            save_new_problem(problem)
        self.assertTrue(state['fired'])
        self.assertNotEqual(problem.code, 'p000000')
        self.assertEqual(Problem.objects.get(code='p000000').name, 'Rival teacher problem')
        self.assertEqual(Problem.objects.get(pk=problem.pk).name, 'Mine')

    def test_typed_code_taken_meanwhile_is_reported(self):
        problem = Problem(code='myquiz', name='Mine', points=1, time_limit=1, memory_limit=262144,
                          group=default_problem_group(), date=timezone.now())
        with rival_committed_before(UNIQUE_IDS, rival_problem('myquiz')):
            with self.assertRaises(UniqueIdTaken) as raised:
                save_new_problem(problem, regenerate=False)
        self.assertEqual(raised.exception.value, 'myquiz')
        self.assertEqual(Problem.objects.filter(code='myquiz').count(), 1)

    def test_other_integrity_errors_propagate(self):
        problem = Problem(code='pfresh', name='Mine', points=1, time_limit=1, memory_limit=262144,
                          group=default_problem_group(), date=timezone.now())
        with mock.patch.object(Problem, 'save', side_effect=IntegrityError('fk')):
            with self.assertRaises(IntegrityError):
                save_with_unique_id(problem, 'code', lambda attempt: 'unused')


class RetryOnDeadlockTestCase(SimpleTestCase):
    """InnoDB error 1213 rolls back the whole transaction; the outermost caller runs it again."""

    def _deadlock(self):
        return OperationalError(1213, 'Deadlock found when trying to get lock; try restarting transaction')

    @mock.patch('judge.utils.db_retry.time.sleep')
    @mock.patch('judge.utils.db_retry.connection')
    def test_deadlock_victim_is_run_again(self, conn, sleep):
        conn.in_atomic_block = False
        calls, resets = [], []
        deadlock = self._deadlock()

        @retry_on_deadlock(before_retry=lambda value: resets.append(value))
        def save(value):
            calls.append(value)
            if len(calls) == 1:
                raise deadlock
            return 'saved'

        self.assertEqual(save('x'), 'saved')
        self.assertEqual(len(calls), 2)
        self.assertEqual(resets, ['x'])

    @mock.patch('judge.utils.db_retry.time.sleep')
    @mock.patch('judge.utils.db_retry.connection')
    def test_other_errors_and_inner_blocks_are_not_retried(self, conn, sleep):
        conn.in_atomic_block = False
        failing = mock.Mock(side_effect=OperationalError(2006, 'gone away'))
        with self.assertRaises(OperationalError):
            retry_on_deadlock(failing)()
        self.assertEqual(failing.call_count, 1)

        conn.in_atomic_block = True  # the transaction belongs to an outer caller
        inner = mock.Mock(side_effect=self._deadlock())
        with self.assertRaises(OperationalError):
            retry_on_deadlock(inner)()
        self.assertEqual(inner.call_count, 1)

    @mock.patch('judge.utils.db_retry.time.sleep')
    @mock.patch('judge.utils.db_retry.connection')
    def test_gives_up_after_the_last_attempt(self, conn, sleep):
        conn.in_atomic_block = False
        always = mock.Mock(side_effect=self._deadlock())
        with self.assertRaises(OperationalError):
            retry_on_deadlock(always, attempts=3)()
        self.assertEqual(always.call_count, 3)

    def test_write_paths_are_wrapped(self):
        from judge.utils import contest_grade
        from judge.views import contests, organization, problem, problem_create_v2, problem_import
        for func in (problem_create_v2.ProblemCreateV2View._save_request, contests.ContestCreate._update,
                     contests.ContestCreate._save_new_contest, contests.ContestClone.form_valid,
                     problem.ProblemClone.form_valid, organization.CreateClass._save_new_class,
                     organization.EditClass._save_class, contest_grade.apply_manual_grade,
                     contest_grade.release_manual_grades, problem_import.ProblemImportCreateAllView.post,
                     problem_import.ProblemImportItemActionView.post):
            self.assertTrue(hasattr(func, '__wrapped__'), func.__qualname__)


class ProblemCreateRaceTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.creator = create_user(username='race_creator',
                                  user_permissions=('create_private_problem', 'edit_own_problem'))

    def setUp(self):
        self.client.force_login(self.creator)

    def _post(self, payload, url=None):
        return self.client.post(url or reverse('problem_create_v2'), data=json.dumps(payload),
                                content_type='application/json')

    def test_suggested_code_taken_by_another_teacher_at_the_same_moment(self):
        # Both teachers opened the add page together: both forms carry p000000.
        with rival_committed_before(UNIQUE_IDS, rival_problem('p000000')):
            response = self._post(mcq_payload(code='p000000', code_auto=True, create_token='tab-one-token'))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertNotEqual(body['code'], 'p000000')
        self.assertTrue(body['id_changed'])
        mine = Problem.objects.get(code=body['code'])
        self.assertEqual(mine.name, 'My question')
        self.assertEqual(mine.mcq_options.count(), 2)
        self.assertEqual(Problem.objects.get(code='p000000').name, 'Rival teacher problem')

    def test_typed_code_taken_at_the_same_moment_is_a_400_and_nothing_half_saved(self):
        before = Problem.objects.count()
        with rival_committed_before(UNIQUE_IDS, rival_problem('myquiz')):
            response = self._post(mcq_payload(code='myquiz', code_auto=False))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('already exists', response.json()['error'])
        # Nothing of ours was kept. (The simulated rival lived inside this request's rolled-back
        # transaction, so it is gone too; a real rival is committed by another request.)
        self.assertEqual(Problem.objects.count(), before)
        self.assertFalse(Problem.objects.filter(name='My question').exists())

    def test_new_tag_created_by_another_teacher_at_the_same_moment(self):
        # Atomic #1 is the question's own transaction, #2 the tag INSERT.
        def rival():
            return ProblemType(name='race-recursion', full_name='Race Recursion')

        with rival_committed_before(CREATE_V2, rival, nth=2, insert_model=ProblemType) as state:
            response = self._post(mcq_payload(problemTypes=['Race Recursion'], create_token='tab-tag-token'))
        self.assertTrue(state['fired'])
        self.assertEqual(response.status_code, 200, response.content)
        problem = Problem.objects.get(code=response.json()['code'])
        self.assertEqual(ProblemType.objects.filter(full_name='Race Recursion').count(), 1)
        self.assertTrue(problem.types.filter(full_name='Race Recursion').exists())

    def test_add_tag_endpoint_race_is_a_400_not_a_500(self):
        with rival_committed_before(CREATE_V2, lambda: ProblemType(name='racetag', full_name='racetag')):
            response = self.client.post(reverse('problem_create_v2_problem_type'),
                                        data=json.dumps({'name': 'racetag'}), content_type='application/json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('already exists', response.json()['error'])

    def test_add_group_endpoint_race_takes_next_name(self):
        with rival_committed_before(CREATE_V2, lambda: ProblemGroup(name='week-3', full_name='theirs')):
            response = self.client.post(reverse('problem_create_v2_problem_group'),
                                        data=json.dumps({'full_name': 'Week 3'}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ProblemGroup.objects.filter(full_name='Week 3').count(), 1)
        self.assertEqual(ProblemGroup.objects.filter(full_name='theirs').count(), 1)

    def test_repeated_save_with_same_create_token_does_not_duplicate(self):
        payload = mcq_payload(code='p000000', code_auto=True, create_token='double-click-token')
        first = self._post(payload)
        second = self._post(payload)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()['already_saved'])
        self.assertEqual(second.json()['code'], first.json()['code'])
        self.assertEqual(Problem.objects.filter(name='My question').count(), 1)

    def test_repeat_while_first_save_is_in_flight_does_not_duplicate(self):
        from django.core.cache import cache
        key = 'problem-create-token:%d:%s' % (self.creator.id, 'inflight-token')
        cache.add(key, 0, 60)  # the first request claimed the token and is still saving
        with mock.patch('judge.views.problem_create_v2._CREATE_TOKEN_WAIT_SECONDS', 0):
            response = self._post(mcq_payload(create_token='inflight-token'))
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(Problem.objects.filter(name='My question').exists())
        cache.delete(key)

    def test_second_tab_with_same_suggested_code_is_not_lost(self):
        # Two tabs of one teacher opened together. Before, the second save was answered
        # "already saved" and its question silently dropped.
        first = self._post(mcq_payload(name='Tab one', code='p000000', code_auto=True, create_token='tab-one-xx'))
        second = self._post(mcq_payload(name='Tab two', code='p000000', code_auto=True, create_token='tab-two-xx'))
        self.assertEqual(second.status_code, 200, second.content)
        self.assertNotIn('already_saved', second.json())
        self.assertNotEqual(first.json()['code'], second.json()['code'])
        self.assertTrue(Problem.objects.filter(name='Tab two').exists())


class ProblemEditConflictTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.teacher = create_user(username='edit_teacher',
                                  user_permissions=('create_private_problem', 'edit_own_problem'))

    def setUp(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('problem_create_v2'), data=json.dumps(mcq_payload(name='Original')),
                                    content_type='application/json')
        self.code = response.json()['code']
        self.url = reverse('problem_edit_v2', args=[self.code])

    def _base(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        base = response.context['edit_base']
        self.assertIn("const EDIT_BASE = '%s';" % base, response.content.decode())
        return base

    def _save(self, name, base=None):
        payload = mcq_payload(name=name, edit_code=self.code)
        if base is not None:
            payload['edit_base'] = base
        return self.client.post(self.url, data=json.dumps(payload), content_type='application/json')

    def test_stale_page_gets_409_instead_of_overwriting(self):
        tab_a = self._base()
        tab_b = self._base()
        self.assertEqual(tab_a, tab_b)
        self.assertEqual(self._save('Saved by A', tab_a).status_code, 200)
        response = self._save('Saved by B', tab_b)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(response.json()['conflict'])
        self.assertIn('reload', response.json()['error'].lower())
        self.assertEqual(Problem.objects.get(code=self.code).name, 'Saved by A')
        # After a reload B can save.
        self.assertEqual(self._save('Saved by B', self._base()).status_code, 200)
        self.assertEqual(Problem.objects.get(code=self.code).name, 'Saved by B')

    def test_judging_and_stats_do_not_count_as_an_edit(self):
        base = self._base()
        problem = Problem.objects.get(code=self.code)
        problem._updating_stats_only = True
        problem.update_stats()
        Problem.objects.filter(pk=problem.pk).update(user_count=7, ac_rate=50)
        self.assertEqual(problem_edit_fingerprint(problem), base)
        self.assertEqual(self._save('Still mine', base).status_code, 200)

    def test_save_without_base_keeps_last_write_wins(self):
        self.assertEqual(self._save('API client').status_code, 200)

    def test_edit_save_locks_the_problem_row(self):
        with CaptureQueriesContext(connection) as queries:
            self._save('Locked', self._base())
        self.assertTrue(any('FOR UPDATE' in q['sql'] and 'judge_problem' in q['sql'] for q in queries))


class StaleFullSaveTestCase(CommonDataMixin, TestCase):
    """Background writers must not write back a request-old copy of a teacher's row."""

    def test_update_stats_keeps_a_concurrent_teacher_edit(self):
        problem = create_problem('stale-stats', name='Old name', points=1)
        loaded_by_judge = Problem.objects.get(pk=problem.pk)
        Problem.objects.filter(pk=problem.pk).update(name='Teacher edit', points=10)
        loaded_by_judge._updating_stats_only = True
        loaded_by_judge.update_stats()
        problem.refresh_from_db()
        self.assertEqual(problem.name, 'Teacher edit')
        self.assertEqual(problem.points, 10)

    def test_join_keeps_a_concurrent_assignment_edit(self):
        contest = create_contest(key='stalejoin', start_time=timezone.now() - timedelta(hours=1),
                                 end_time=timezone.now() + timedelta(hours=1))
        loaded_by_join = Contest.objects.get(pk=contest.pk)
        new_end = timezone.now() + timedelta(days=3)
        Contest.objects.filter(pk=contest.pk).update(name='Extended by teacher', end_time=new_end)
        loaded_by_join.update_user_count()
        contest.refresh_from_db()
        self.assertEqual(contest.name, 'Extended by teacher')
        self.assertEqual(contest.end_time, new_end)

    def test_recompute_keeps_scheduled_harvest_and_locks_row(self):
        contest = create_contest(key='stalerecompute', start_time=timezone.now() - timedelta(hours=1),
                                 end_time=timezone.now() + timedelta(hours=1))
        student = create_user(username='recompute_student')
        participation = ContestParticipation.objects.create(contest=contest, user=student.profile,
                                                            real_start=timezone.now())
        ContestParticipation.objects.filter(pk=participation.pk).update(harvest_scheduled_task_id='task-1')
        with CaptureQueriesContext(connection) as queries:
            participation.recompute_results()
        self.assertTrue(any('FOR UPDATE' in q['sql'] and 'judge_contestparticipation' in q['sql']
                            for q in queries))
        participation.refresh_from_db()
        self.assertEqual(participation.harvest_scheduled_task_id, 'task-1')


class ContestRaceTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='race_contest_editor', user_permissions=(
            'add_contest', 'edit_own_contest', 'edit_all_contest', 'clone_contest'))

    def setUp(self):
        self.client.force_login(self.editor)

    def _payload(self, **overrides):
        now = timezone.now()
        data = {'name': 'Quiz', 'key': '', 'key_auto': True,
                'start_time': (now + timedelta(hours=1)).isoformat(),
                'end_time': (now + timedelta(hours=3)).isoformat()}
        data.update(overrides)
        return data

    def test_auto_key_taken_before_validation_retries(self):
        # Another teacher committed c000050 between our "next free id" and our checks: before,
        # full_clean()'s unique check answered 400 "Contest with this id already exists".
        def generate(spread=0):
            if not Contest.objects.filter(key='c000050').exists():
                Contest.objects.bulk_create([Contest(key='c000050', name='Rival', start_time=timezone.now(),
                                                     end_time=timezone.now() + timedelta(hours=1))])
                return 'c000050'
            return 'c000051'

        with mock.patch.object(Contest, 'generate_unique_key', side_effect=generate):
            response = self.client.post(reverse('contest_create'), data=json.dumps(self._payload()),
                                        content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['key'], 'c000051')
        self.assertEqual(Contest.objects.get(key='c000050').name, 'Rival')

    def test_stale_edit_page_gets_409(self):
        self.client.post(reverse('contest_create'), data=json.dumps(self._payload(key='editrace', key_auto=False)),
                         content_type='application/json')
        url = reverse('contest_edit', args=['editrace'])
        page = self.client.get(url)
        base = page.context['edit_base']
        self.assertIn(json.dumps(base), page.content.decode())
        first = self.client.post(url, data=json.dumps(self._payload(name='Tab A', key='editrace', key_auto=False,
                                                                    edit_base=base)),
                                 content_type='application/json')
        self.assertEqual(first.status_code, 200, first.content)
        second = self.client.post(url, data=json.dumps(self._payload(name='Tab B', key='editrace', key_auto=False,
                                                                     edit_base=base)),
                                  content_type='application/json')
        self.assertEqual(second.status_code, 409, second.content)
        self.assertTrue(second.json()['conflict'])
        self.assertEqual(Contest.objects.get(key='editrace').name, 'Tab A')

    def test_student_joining_does_not_count_as_an_edit(self):
        contest = create_contest(key='joinbase', start_time=timezone.now() - timedelta(hours=1),
                                 end_time=timezone.now() + timedelta(hours=1))
        base = contest_edit_fingerprint(contest)
        Contest.objects.filter(pk=contest.pk).update(user_count=42, results_finalized=True)
        self.assertEqual(contest_edit_fingerprint(contest), base)

    def test_edit_locks_the_contest_row(self):
        self.client.post(reverse('contest_create'), data=json.dumps(self._payload(key='lockrace', key_auto=False)),
                         content_type='application/json')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(reverse('contest_edit', args=['lockrace']),
                                        data=json.dumps(self._payload(key='lockrace', key_auto=False)),
                                        content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(any('FOR UPDATE' in q['sql'] and 'judge_contest' in q['sql'] for q in queries))

    def test_clone_suggested_key_taken_meanwhile(self):
        source = create_contest(key='clonesrc', authors=('race_contest_editor',))
        url = reverse('contest_clone', args=['clonesrc'])
        suggested = self.client.get(url).context['form'].initial['key']

        def rival():
            return Contest(key=suggested, name='Rival clone', start_time=source.start_time,
                           end_time=source.end_time)

        with rival_committed_before(UNIQUE_IDS, rival):
            response = self.client.post(url, {'key': suggested})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Contest.objects.get(key=suggested).name, 'Rival clone')
        self.assertEqual(Contest.objects.filter(name=source.name).count(), 2)

    def test_double_join_does_not_500(self):
        contest = create_contest(key='doublejoin', start_time=timezone.now() - timedelta(hours=1),
                                 end_time=timezone.now() + timedelta(hours=1), is_visible=True)
        student = create_user(username='double_joiner')

        def rival():
            return ContestParticipation(contest=contest, user=student.profile, virtual=0, real_start=timezone.now())

        self.client.force_login(student)
        with rival_committed_before('judge.views.contests', rival, insert_model=ContestParticipation) as state:
            response = self.client.post(reverse('contest_join', args=['doublejoin']))
        self.assertTrue(state['fired'])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContestParticipation.objects.filter(contest=contest, user=student.profile).count(), 1)
        student.profile.refresh_from_db()
        self.assertIsNotNone(student.profile.current_contest)


class ProblemCloneRaceTestCase(CommonDataMixin, TestCase):
    def test_clone_suggested_code_taken_meanwhile(self):
        source = create_problem('clonesource', name='Source', authors=('superuser',))
        self.client.force_login(self.users['superuser'])
        url = reverse('problem_clone', args=[source.code])
        suggested = self.client.get(url).context['form'].initial['code']
        with rival_committed_before(UNIQUE_IDS, rival_problem(suggested)):
            response = self.client.post(url, {'code': suggested})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Problem.objects.get(code=suggested).name, 'Rival teacher problem')
        self.assertEqual(Problem.objects.filter(name='Source').count(), 2)


class GradeRaceTestCase(TestCase):
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.author = create_user(username='race_grader', user_permissions=('edit_own_contest',))
        cls.student = create_user(username='race_graded')
        problem = create_problem('race-grade-prob')
        cls.contest = create_contest(key='racegrade', start_time=timezone.now() - timedelta(days=1),
                                     end_time=timezone.now() + timedelta(days=1), authors=('race_grader',))
        cp = ContestProblem.objects.create(contest=cls.contest, problem=problem, points=100, order=1)
        participation = ContestParticipation.objects.create(contest=cls.contest, user=cls.student.profile,
                                                            real_start=timezone.now() - timedelta(hours=1))
        cls.submission = Submission.objects.create(user=cls.student.profile, problem=problem,
                                                   language=Language.objects.get(key='PY3'), result='WA',
                                                   status='D', contest_object=cls.contest, points=50.0,
                                                   case_total=100)
        cls.contest_submission = ContestSubmission.objects.create(submission=cls.submission, problem=cp,
                                                                  participation=participation, points=50.0)

    def setUp(self):
        self.client.force_login(self.author)

    def test_two_graders_saving_the_first_grade_at_once(self):
        def rival():
            return SubmissionManualGrade(submission=self.submission, points=10, machine_points=50.0)

        # Atomic #1 is the grade transaction (lock found no row), #2 the INSERT savepoint.
        with rival_committed_before('judge.utils.contest_grade', rival, nth=2):
            response = self.client.post(reverse('contest_grade_save', args=['racegrade']),
                                        {'submission': self.submission.id, 'score': '77'})
        self.assertEqual(response.status_code, 200, response.content)
        grades = SubmissionManualGrade.objects.filter(submission=self.submission)
        self.assertEqual(grades.count(), 1)
        self.assertEqual(grades.get().points, 77)

    def test_save_reads_the_released_flag_and_score_under_the_lock(self):
        SubmissionManualGrade.objects.create(submission=self.submission, points=60, machine_points=50.0,
                                             released=False)
        # Release All ran after the grade page loaded: this request's objects are older than that.
        submission = Submission.objects.get(pk=self.submission.pk)
        _ = submission.contest_or_none.points
        SubmissionManualGrade.objects.filter(submission=self.submission).update(released=True)
        ContestSubmission.objects.filter(pk=self.contest_submission.pk).update(points=60)
        from judge.utils.contest_grade import apply_manual_grade
        apply_manual_grade(self.author.profile, submission, 90)
        self.assertEqual(ContestSubmission.objects.get(pk=self.contest_submission.pk).points, 90)

    def _lock_order(self, queries):
        order = []
        for query in queries:
            sql = query['sql']
            if 'FOR UPDATE' not in sql:
                continue
            table = 'grade' if 'FROM `judge_submissionmanualgrade`' in sql else (
                'submission' if 'FROM `judge_submission`' in sql else None)
            if table and table not in order:
                order.append(table)
        return order

    def test_save_and_release_lock_in_the_same_order(self):
        # A save locks the submission, then its grade; Release All must do the same or the two
        # deadlock (seen under load as HTTP 500 "Deadlock found").
        with CaptureQueriesContext(connection) as save_queries:
            self.client.post(reverse('contest_grade_save', args=['racegrade']),
                             {'submission': self.submission.id, 'score': '40'})
        with CaptureQueriesContext(connection) as release_queries:
            self.client.post(reverse('contest_grade_release', args=['racegrade']))
        self.assertEqual(self._lock_order(save_queries), ['submission', 'grade'])
        self.assertEqual(self._lock_order(release_queries), ['submission', 'grade'])
        self.assertFalse(any(q['sql'].startswith('UPDATE') and 'judge_submissionmanualgrade' in q['sql']
                             and 'judge_submission`' in q['sql'].split('WHERE', 1)[-1]
                             for q in release_queries))

    def test_release_all_is_atomic_and_locks_grades(self):
        SubmissionManualGrade.objects.create(submission=self.submission, points=70, machine_points=50.0)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(reverse('contest_grade_release', args=['racegrade']))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('FOR UPDATE' in q['sql'] and 'judge_submissionmanualgrade' in q['sql']
                            for q in queries))
        self.assertEqual(ContestSubmission.objects.get(pk=self.contest_submission.pk).points, 70)


class ClassAndMaterialRaceTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']

    def setUp(self):
        self.client.force_login(self.users['superuser'])

    def _create_class(self, **data):
        form = {'name': '', 'academic_year': '2026-2027', 'term_session': 'Fall - Session 1',
                'instructor_names': 'Race Prof', 'is_active': 'on'}
        form.update(data)
        return self.client.post(reverse('organization_create_class', args=[self.org.id, self.org.slug]), form)

    def _rival_class(self, name):
        return lambda: Class(organization=self.org, name=name, slug='rival', is_active=True)

    def test_generated_class_name_taken_meanwhile_gets_next_suffix(self):
        with rival_committed_before(UNIQUE_IDS, self._rival_class('2026-2027 Fall - Session 1 Race Prof')):
            response = self._create_class()
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and
                         response.context['form'].errors)
        names = sorted(Class.objects.filter(organization=self.org).values_list('name', flat=True))
        self.assertIn('2026-2027 Fall - Session 1 Race Prof', names)
        self.assertTrue(any(name.startswith('2026-2027 Fall - Session 1 Race Prof ') for name in names))

    def test_typed_class_name_taken_meanwhile_is_a_form_error(self):
        with rival_committed_before(UNIQUE_IDS, self._rival_class('Section Typed')):
            response = self._create_class(name='Section Typed')
        self.assertEqual(response.status_code, 200)
        self.assertIn('already exists', str(response.context['form'].errors))
        # Nothing half-made: no class and no admins row from our request survives.
        self.assertFalse(Class.objects.filter(organization=self.org, slug__startswith='section-typed').exists())

    def test_upload_into_folder_deleted_meanwhile(self):
        klass = Class.objects.create(organization=self.org, name='Material Race', slug='material-race')
        folder = ClassMaterial.objects.create(class_obj=klass, name='Week 1', is_folder=True,
                                              uploaded_by=self.users['superuser'].profile)
        original = Class.get_absolute_url

        def deleted_by_co_teacher(self_):
            ClassMaterial.objects.filter(pk=folder.pk).delete()
            return original(self_)

        url = reverse('class_material_action', args=[self.org.id, self.org.slug, klass.id, klass.slug])
        with mock.patch.object(Class, 'get_absolute_url', deleted_by_co_teacher):
            response = self.client.post(url, {'action': 'mkdir', 'name': 'Late folder', 'folder': folder.id})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ClassMaterial.objects.filter(name='Late folder').exists())
        self.assertIn('deleted', ' '.join(str(m) for m in get_messages(response.wsgi_request)))


class ImportCreateRaceTestCase(CommonDataMixin, TestCase):
    def test_second_click_on_create_does_not_duplicate(self):
        teacher = create_user(username='import_racer', user_permissions=('create_private_problem',))
        self.client.force_login(teacher)
        draft = ProblemImportDraft.objects.create(owner=teacher.profile)
        item = ProblemImportItem.objects.create(draft=draft, row_number=2, name='Imported once',
                                                submission_type='S', description='Explain.', answer='x')
        existing = create_problem('alreadyimported', name='Imported once')
        stale = ProblemImportItem.objects.get(pk=item.pk)
        # The first click committed meanwhile; this request still holds the old row.
        ProblemImportItem.objects.filter(pk=item.pk).update(created_problem=existing)

        def lookup(klass, *args, **kwargs):
            if klass is ProblemImportDraft:
                return get_object_or_404(klass, *args, **kwargs)
            return stale

        with mock.patch('judge.views.problem_import.get_object_or_404', side_effect=lookup):
            self.client.post(reverse('problem_import_item_action', args=[draft.id, item.id, 'create']))
        self.assertEqual(Problem.objects.filter(name='Imported once').count(), 1)

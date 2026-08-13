"""Tests for the redesigned problem list filters/sorts and difficulty badge (judge.views.problem)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Language, Problem, ProblemGroup, ProblemSubmissionType, ProblemType, Submission
from judge.models.tests.util import (
    CommonDataMixin,
    create_contest,
    create_problem,
    create_problem_group,
    create_problem_type,
    create_solution,
    create_user,
)
from judge.question_types import all_types
from judge.views.problem import ProblemList


class ProblemListTestBase(CommonDataMixin, TestCase):
    """Shared fixtures and helpers for problem list filter/sort tests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        create_problem_group(name='filter-group')
        create_problem_group(name='filter-group-2')
        create_problem_type(name='easy', full_name='Easy')
        create_problem_type(name='medium', full_name='Medium')
        create_problem_type(name='hard', full_name='Hard')
        create_problem_type(name='code', full_name='Code')
        create_problem_type(name='fill_in_blank', full_name='Fill in Blank')
        create_problem_type(name='multiple_choices', full_name='Multiple Choice')
        create_problem_type(name='dp', full_name='Dynamic Programming')
        create_problem_type(name='greedy', full_name='Greedy')

        cls.type_ids = {
            name: ProblemType.objects.get(name=name).id
            for name in ('easy', 'medium', 'hard', 'code', 'multiple_choices', 'dp', 'greedy')
        }
        cls.group_id = ProblemGroup.objects.get(name='filter-group').id
        cls.group2_id = ProblemGroup.objects.get(name='filter-group-2').id

        cls.problem_easy = create_problem(
            code='filter-easy',
            name='Easy Filter Problem',
            is_public=True,
            group='filter-group',
            types=('easy', 'code', 'dp'),
            ac_rate=80,
            points=100,
            user_count=50,
            date=now - timedelta(days=50),
        )
        cls.problem_hard = create_problem(
            code='filter-hard',
            name='Hard Filter Problem',
            is_public=True,
            group='filter-group',
            types=('hard', 'code', 'dp'),
            ac_rate=20,
            points=200,
            user_count=10,
            date=now - timedelta(days=10),
        )
        cls.problem_greedy = create_problem(
            code='filter-greedy',
            name='Greedy Filter Problem',
            is_public=True,
            group='filter-group',
            types=('medium', 'multiple_choices', 'greedy'),
            # Answer format is the authoritative submission_type discriminator; every other
            # fixture problem keeps the default CODE format.
            submission_type=ProblemSubmissionType.MCQ,
            ac_rate=50,
            points=150,
            user_count=30,
            date=now - timedelta(days=30),
        )
        cls.problem_alpha = create_problem(
            code='filter-alpha',
            name='Alpha Problem',
            is_public=True,
            group='filter-group',
            types=('easy', 'code', 'greedy'),
            ac_rate=10,
            points=50,
            user_count=5,
            date=now - timedelta(days=100),
        )
        cls.problem_beta = create_problem(
            code='filter-beta',
            name='Beta Problem',
            is_public=True,
            group='filter-group',
            types=('hard', 'fill_in_blank', 'dp'),
            ac_rate=90,
            points=300,
            user_count=100,
            date=now - timedelta(days=1),
        )
        cls.problem_other_group = create_problem(
            code='filter-other',
            name='Other Group Problem',
            is_public=True,
            group='filter-group-2',
            types=('hard', 'code'),
            ac_rate=40,
            points=120,
        )
        cls.problem_desc_only = create_problem(
            code='filter-desc',
            name='Plain Title',
            description='unique-description-token-xyz',
            is_public=True,
            group='filter-group',
            types=('code',),
            date=now - timedelta(days=75),
        )
        create_solution(problem=cls.problem_easy)

    def setUp(self):
        self.factory = RequestFactory()
        self.client = Client()
        cache.clear()

    def _view(self, user, query_params):
        request = self.factory.get('/problems/', query_params)
        request.user = user
        if getattr(user, 'is_authenticated', False):
            request.profile = user.profile
        request.session = self.client.session
        request.LANGUAGE_CODE = 'en'
        view = ProblemList()
        view.request = request
        view.kwargs = {}
        order = query_params.get('order', '')
        if not ((not order.startswith('-') or order.count('-') == 1) and (order.lstrip('-') in view.all_sorts)):
            order = view.default_sort
        view.order = order
        view.setup_problem_list(request)
        return view

    def _context(self, user, query_params):
        view = self._view(user, query_params)
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def _filtered_codes(self, user, query_params):
        return set(self._view(user, query_params).get_normal_queryset().values_list('code', flat=True))

    def _sorted_codes(self, user, query_params):
        view = self._view(user, query_params)
        queryset = view.get_normal_queryset()
        paginator = view.get_paginator(queryset, view.paginate_by)
        return [p.code for p in paginator.object_list if p.code.startswith('filter-')]

    def _filter_group_codes(self, codes):
        return {code for code in codes if code.startswith('filter-')}

    def _mark_solved(self, problem, user='normal'):
        Submission.objects.create(
            user=self.users[user].profile,
            problem=problem,
            result='AC',
            case_points=1,
            case_total=1,
            language=Language.get_python3(),
        )
        cache.clear()

    def _mark_attempted(self, problem, user='normal'):
        Submission.objects.create(
            user=self.users[user].profile,
            problem=problem,
            result='WA',
            case_points=0,
            case_total=1,
            language=Language.get_python3(),
        )
        cache.clear()


class ProblemListFilterTestCase(ProblemListTestBase):
    def test_difficulty_filter_uses_or_semantics(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'type': [self.type_ids['easy'], self.type_ids['hard']],
                'category': str(self.group_id),
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-easy', 'filter-hard', 'filter-alpha', 'filter-beta'})

    def test_knowledge_and_format_filters_use_and_semantics(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'type': [self.type_ids['dp'], self.type_ids['code']],
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-easy', 'filter-hard'})

    def test_combined_difficulty_and_knowledge_filters(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'type': [self.type_ids['easy'], self.type_ids['dp']],
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-easy'})

    def test_format_type_filter(self):
        # The answer format is filtered via the dedicated ?format param backed by submission_type,
        # not via a ProblemType tag. Only the greedy fixture is an MCQ problem.
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'format': [ProblemSubmissionType.MCQ],
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-greedy'})

    def test_knowledge_type_filter(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'type': [self.type_ids['greedy']],
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-greedy', 'filter-alpha'})

    def test_category_filter(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'category': str(self.group2_id),
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-other'})

    def test_ac_rate_range_filter(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'ac_min': '30',
                'ac_max': '70',
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-greedy', 'filter-other'})

    def test_points_range_filter(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'point_start': '150',
                'point_end': '250',
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-hard', 'filter-greedy'})

    def test_keyword_search_matches_problem_code(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'search': 'filter-greedy',
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-greedy'})

    def test_keyword_search_matches_problem_name(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'search': 'Alpha Problem',
                'filtered': '1',
            },
        ))
        self.assertEqual(codes, {'filter-alpha'})

    def test_keyword_search_searches_description_by_default(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {'search': 'unique-description-token-xyz'},
        ))
        self.assertEqual(codes, {'filter-desc'})

    def test_keyword_search_respects_search_full_toggle(self):
        base_params = {'search': 'unique-description-token-xyz', 'filtered': '1'}
        without_desc = self._filter_group_codes(self._filtered_codes(self.users['normal'], base_params))
        with_desc = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {**base_params, 'search_full': '1'},
        ))
        self.assertEqual(without_desc, set())
        self.assertEqual(with_desc, {'filter-desc'})

    def test_solved_status_filter(self):
        self._mark_solved(self.problem_easy)

        solved_codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'status': 'solved',
                'filtered': '1',
            },
        ))
        unsolved_codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {
                'status': 'unsolved',
                'filtered': '1',
            },
        ))

        self.assertEqual(solved_codes, {'filter-easy'})
        self.assertTrue({'filter-hard', 'filter-greedy'}.issubset(unsolved_codes))

    def test_invalid_status_is_ignored(self):
        all_codes = self._filter_group_codes(self._filtered_codes(self.users['normal'], {'filtered': '1'}))
        invalid_codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {'status': 'invalid', 'filtered': '1'},
        ))
        self.assertEqual(invalid_codes, all_codes)

    def test_invalid_type_values_are_ignored(self):
        all_codes = self._filter_group_codes(self._filtered_codes(self.users['normal'], {'filtered': '1'}))
        invalid_codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {'type': ['not-a-number'], 'filtered': '1'},
        ))
        self.assertEqual(invalid_codes, all_codes)

    def test_hide_solved_excludes_completed_problems(self):
        self._mark_solved(self.problem_easy)
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {'hide_solved': '1'},
        ))
        self.assertNotIn('filter-easy', codes)

    def test_has_public_editorial_filter(self):
        codes = self._filter_group_codes(self._filtered_codes(
            self.users['normal'],
            {'has_public_editorial': '1'},
        ))
        self.assertEqual(codes, {'filter-easy'})

    def test_apply_full_text_falls_back_for_cjk_terms(self):
        cjk_problem = create_problem(
            code='filter-cjk',
            name='中文题目',
            is_public=True,
            group='filter-group',
            description='这是一道中文描述',
        )
        queryset = Problem.objects.filter(code__startswith='filter-')
        result = ProblemList.apply_full_text(queryset, '中文')
        self.assertIn(cjk_problem.code, set(result.values_list('code', flat=True)))

    def test_problem_list_page_renders_filter_ui(self):
        self.client.force_login(self.users['normal'])
        response = self.client.get(reverse('problem_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="filter-form"', html=False)
        self.assertContains(response, 'id="difficulty-multi-select"', html=False)
        self.assertContains(response, 'id="status-filter"', html=False)
        self.assertContains(response, 'class="difficulty-badge', html=False)

    def test_problem_list_context_splits_type_groups(self):
        context = self._context(self.users['normal'], {})

        difficulty_names = {t.name for t in context['difficulty_types']}
        knowledge_names = {t.name for t in context['knowledge_types']}
        # Format options come from the registered question types (submission_type), exposed as
        # {'code', 'label'} dicts, not from the ProblemType taxonomy. MCQ is additionally broken
        # down into single / multiple / true-false pseudo-codes on top of the whole-MCQ option.
        format_codes = {f['code'] for f in context['format_types']}

        self.assertEqual(difficulty_names, {'easy', 'medium', 'hard'})
        self.assertEqual(
            format_codes,
            {t.submission_type for t in all_types()} | {'single', 'multiple', 'truefalse'},
        )
        self.assertIn('dp', knowledge_names)
        self.assertIn('greedy', knowledge_names)
        # Format- and difficulty-named ProblemTypes are not surfaced as knowledge points.
        self.assertNotIn('code', knowledge_names)
        self.assertNotIn('multiple_choices', knowledge_names)
        self.assertNotIn('easy', knowledge_names)

    def test_problem_list_context_splits_selected_types(self):
        context = self._context(self.users['normal'], {
            'type': [self.type_ids['easy'], self.type_ids['greedy'], self.type_ids['code']],
            'format': [ProblemSubmissionType.MCQ],
            'filtered': '1',
        })
        self.assertEqual(context['selected_difficulty_types'], [self.type_ids['easy']])
        self.assertIn(self.type_ids['greedy'], context['selected_knowledge_types'])
        # Answer format is carried by submission_type, not the ProblemType taxonomy: a
        # format-named ProblemType selected via ?type is not counted as a knowledge point...
        self.assertNotIn(self.type_ids['code'], context['selected_knowledge_types'])
        # ...and the active format selection comes from the dedicated ?format param.
        self.assertEqual(context['selected_formats'], [ProblemSubmissionType.MCQ])

    def test_problem_list_context_exposes_group_and_type_counts(self):
        context = self._context(self.users['normal'], {})
        self.assertGreaterEqual(context['group_counts'].get(self.group_id, 0), 6)
        self.assertGreaterEqual(context['type_counts'].get(self.type_ids['dp'], 0), 3)
        self.assertGreaterEqual(context['total_problem_count'], 7)

    def test_problem_list_context_exposes_progress_counts(self):
        self._mark_solved(self.problem_easy)
        context = self._context(self.users['normal'], {'category': str(self.group_id), 'filtered': '1'})
        self.assertGreaterEqual(context['progress_total'], 1)
        self.assertGreaterEqual(context['progress_solved'], 1)

    def test_session_post_persists_hide_solved(self):
        self.client.force_login(self.users['normal'])
        response = self.client.post('{}?hide_solved=1'.format(reverse('problem_list')))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get('hide_solved'))


class ProblemListSortTestCase(ProblemListTestBase):
    def test_default_sort_is_code_ascending(self):
        codes = self._sorted_codes(self.users['normal'], {'category': str(self.group_id)})
        self.assertEqual(codes, sorted(codes))

    def test_invalid_sort_falls_back_to_code(self):
        codes = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'not-a-sort'},
        )
        self.assertEqual(codes, sorted(codes))

    def test_sort_by_code_descending(self):
        codes = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-code'},
        )
        self.assertEqual(codes, sorted(codes, reverse=True))

    def test_sort_by_points_ascending(self):
        codes = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'points'},
        )
        points = list(Problem.objects.filter(code__in=codes).order_by('points').values_list('code', flat=True))
        self.assertEqual(codes, list(points))

    def test_sort_by_points_descending(self):
        codes = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-points'},
        )
        points = list(Problem.objects.filter(code__in=codes).order_by('-points').values_list('code', flat=True))
        self.assertEqual(codes, list(points))

    def test_sort_by_ac_rate_ascending(self):
        codes = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'ac_rate'},
        )
        expected = list(
            Problem.objects.filter(code__in=codes).order_by('ac_rate', 'id').values_list('code', flat=True),
        )
        self.assertEqual(codes, expected)

    def test_sort_by_ac_rate_descending(self):
        codes = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-ac_rate'},
        )
        expected = list(
            Problem.objects.filter(code__in=codes).order_by('-ac_rate', 'id').values_list('code', flat=True),
        )
        self.assertEqual(codes, expected)

    def test_sort_by_user_count(self):
        asc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'user_count'},
        )
        desc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-user_count'},
        )
        self.assertEqual(asc, list(reversed(desc)))

    def test_sort_by_date(self):
        asc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'date'},
        )
        desc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-date'},
        )
        dates_by_code = dict(
            Problem.objects.filter(code__in=asc).values_list('code', 'date'),
        )
        asc_dates = [dates_by_code[code] for code in asc]
        desc_dates = [dates_by_code[code] for code in desc]
        self.assertEqual(asc_dates, sorted(asc_dates))
        self.assertEqual(desc_dates, sorted(desc_dates, reverse=True))
        self.assertEqual(asc[0], 'filter-alpha')
        self.assertEqual(desc[0], 'filter-beta')

    def test_sort_by_name(self):
        asc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'name'},
        )
        desc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-name'},
        )
        names_by_code = dict(
            Problem.objects.filter(code__in=asc).values_list('code', 'name'),
        )
        asc_names = [names_by_code[code] for code in asc]
        desc_names = [names_by_code[code] for code in desc]
        self.assertEqual(asc_names, sorted(asc_names))
        self.assertEqual(desc_names, sorted(desc_names, reverse=True))

    def test_sort_by_group(self):
        codes = self._sorted_codes(self.users['normal'], {'order': 'group'})
        group_names = [Problem.objects.get(code=code).group.name for code in codes]
        self.assertEqual(group_names, sorted(group_names))

    def test_sort_by_type(self):
        asc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'type'},
        )
        desc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-type'},
        )
        self.assertNotEqual(asc, desc)
        self.assertEqual(len(asc), len(desc))

    def test_sort_by_editorial(self):
        asc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'editorial'},
        )
        self.assertEqual(asc[-1], 'filter-easy')

    def test_sort_by_solved_status(self):
        self._mark_solved(self.problem_easy)
        self._mark_attempted(self.problem_hard)
        asc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': 'solved'},
        )
        desc = self._sorted_codes(
            self.users['normal'],
            {'category': str(self.group_id), 'order': '-solved'},
        )
        self.assertEqual(asc[-1], 'filter-easy')
        self.assertEqual(desc[0], 'filter-easy')
        self.assertLess(asc.index('filter-hard'), asc.index('filter-easy'))

    def test_filter_and_sort_combined(self):
        codes = self._sorted_codes(self.users['normal'], {
            'category': str(self.group_id),
            'type': [self.type_ids['dp']],
            'filtered': '1',
            'order': 'points',
        })
        self.assertEqual(codes, ['filter-easy', 'filter-hard', 'filter-beta'])

    def test_sort_links_are_exposed_in_context(self):
        context = self._context(self.users['normal'], {})
        for key in ('points', 'ac_rate', 'date', 'code', 'name'):
            self.assertIn(key, context['sort_links'])
            self.assertIn('order=', context['sort_links'][key])


class ProblemDetailDifficultyTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        create_problem_type(name='easy', full_name='Easy')
        create_problem_type(name='code', full_name='Code')
        cls.problem = create_problem(
            code='detail-easy',
            is_public=True,
            types=('easy', 'code'),
        )

    def setUp(self):
        self.client = Client()

    def test_problem_detail_shows_difficulty_badge(self):
        response = self.client.get(reverse('problem_detail', args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="difficulty-badge diff-easy"', html=False)
        self.assertContains(response, 'Easy', html=False)

    def test_problem_detail_hides_difficulty_when_unset(self):
        problem = create_problem(
            code='detail-no-diff',
            is_public=True,
            types=('code',),
        )
        response = self.client.get(reverse('problem_detail', args=[problem.code]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="difficulty-badge', html=False)


class ProblemBulkDeleteRedirectTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(
            username='bulk_del_editor',
            is_staff=True,
            user_permissions=('edit_own_problem', 'edit_all_problem'),
        )
        cls.problem = create_problem(code='bulk-del-safe', is_public=True, authors=('bulk_del_editor',))

    def test_rejects_external_next_redirect(self):
        self.client.force_login(self.editor)
        response = self.client.post(reverse('problem_bulk_delete'), {
            'codes': [self.problem.code],
            'next': 'https://evil.example/phish',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('problem_list'))
        self.assertFalse(Problem.objects.filter(code='bulk-del-safe').exists())

    def test_allows_same_host_relative_next(self):
        keep = create_problem(code='bulk-del-keep', is_public=True, authors=('bulk_del_editor',))
        self.client.force_login(self.editor)
        response = self.client.post(reverse('problem_bulk_delete'), {
            'codes': [keep.code],
            'next': '/problems/?category=1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/problems/?category=1')
        self.assertFalse(Problem.objects.filter(code='bulk-del-keep').exists())


class ProblemSubmitGuardTestCase(CommonDataMixin, TestCase):
    """A non-public problem attached to a started assignment is readable but
    not submittable outside a live contest window — except by its editors."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = create_user(username='guard_student')
        cls.editor = create_user(username='guard_editor', user_permissions=('edit_own_problem',))
        cls.problem = create_problem(code='guardprob', authors=('guard_editor',), allowed_languages=('PY3',))
        _now = timezone.now()
        cls.contest = create_contest(
            key='guardcontest', name='Guard Contest',
            start_time=_now - timedelta(days=1), end_time=_now + timedelta(days=1),
            is_visible=True, authors=('superuser',),
        )
        from judge.models import ContestProblem, Judge
        ContestProblem.objects.create(contest=cls.contest, problem=cls.problem, points=100, order=1)
        cls.lang = Language.objects.get(key='PY3')
        judge = Judge.objects.create(name='guardjudge', auth_key='guardkey', online=True)
        judge.problems.add(cls.problem)
        judge.runtimes.add(cls.lang)

    def setUp(self):
        self.client = Client()

    def test_student_blocked_from_regular_submit(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('problem_submit', args=['guardprob']),
            {'language': self.lang.id, 'source': 'print(1)'},
        )
        self.assertContains(response, 'Join the assignment to submit')
        self.assertFalse(Submission.objects.filter(user=self.student.profile).exists())

    def test_editor_not_blocked_from_regular_submit(self):
        # Maintainers (authors/editors) may still test-submit from the problem
        # page outside a contest — the guard must not overblock them.
        from unittest import mock

        with mock.patch('judge.models.submission.judge_submission'):
            self.client.force_login(self.editor)
            response = self.client.post(
                reverse('problem_submit', args=['guardprob']),
                {'language': self.lang.id, 'source': 'print(1)'},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Submission.objects.filter(user=self.editor.profile).exists())

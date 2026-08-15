import json
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestTag, Organization, Submission
from judge.models.tests.util import CommonDataMixin, create_contest, create_problem, create_user


class ContestListV2ViewTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.users.update({
            'staff_contest_edit_all': create_user(
                username='staff_contest_edit_all',
                is_staff=True,
                user_permissions=('edit_own_contest', 'edit_all_contest'),
            ),
            'normal_user': create_user(username='normal_user'),
        })

        _now = timezone.now()

        cls.past_contest = create_contest(
            key='past_contest',
            name='Past Contest',
            start_time=_now - timedelta(days=10),
            end_time=_now - timedelta(days=5),
            is_visible=True,
            authors=('superuser',),
        )

        cls.ongoing_contest = create_contest(
            key='ongoing_contest',
            name='Ongoing Contest',
            start_time=_now - timedelta(days=1),
            end_time=_now + timedelta(days=1),
            is_visible=True,
            authors=('superuser',),
        )

        cls.future_contest = create_contest(
            key='future_contest',
            name='Future Contest',
            start_time=_now + timedelta(days=5),
            end_time=_now + timedelta(days=10),
            is_visible=True,
            authors=('superuser',),
        )

        cls.tag1 = ContestTag.objects.create(name='tag1', color='#ff0000')
        cls.tag2 = ContestTag.objects.create(name='tag2', color='#00ff00')
        cls.past_contest.tags.add(cls.tag1)
        cls.ongoing_contest.tags.add(cls.tag2)

    def setUp(self):
        self.client = Client()

    def test_status_code(self):
        response = self.client.get(reverse('contest_list'))
        self.assertEqual(response.status_code, 200)

    def test_template(self):
        response = self.client.get(reverse('contest_list'))
        self.assertTemplateUsed(response, 'contest/list-v2.html')

    def test_context(self):
        response = self.client.get(reverse('contest_list'))
        context = response.context
        self.assertIn('past_contests', context)
        self.assertIn('current_contests', context)
        self.assertIn('future_contests', context)
        self.assertIn('organizations', context)
        self.assertIn('tags', context)
        self.assertIn('total_count', context)

    def test_search(self):
        response = self.client.get(reverse('contest_list') + '?search=past')
        self.assertEqual(response.status_code, 200)

    def test_sort(self):
        response = self.client.get(reverse('contest_list') + '?sort=name')
        self.assertEqual(response.status_code, 200)

    def test_course_group_head_renders(self):
        """By-course grouping (the default view) renders a course header
        labelled with the org name and places the assignment in that section."""
        create_contest(
            key='org_course_contest', name='Org Course Contest',
            organizations=('open',), is_visible=True,
        )
        self.client.force_login(self.users['normal_user'])
        response = self.client.get(reverse('contest_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<div class="cl-group-head">', html)
        # The course section header carries the org name...
        self.assertIn('<i class="fa fa-book"></i> open</span>', html)
        # ...and the assignment card is present inside that section.
        self.assertIn('Org Course Contest', html)

    def test_solved_column_wrapper_always_rendered(self):
        """Every card renders the solved column wrapper (even when empty) so
        the grid keeps four columns and the action buttons never shift."""
        self.client.force_login(self.users['normal_user'])
        response = self.client.get(reverse('contest_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('class="cl-solved-col"', html)
        # Without any solved data the span is hidden but the wrapper stays.
        self.assertNotIn('Solved 1/1', html)
        # cl-users must still precede cl-actions for every row.
        self.assertLess(html.index('class="cl-users"'), html.index('class="cl-actions"'))

    def test_solved_column_renders_before_users(self):
        """A user who ACed a contest problem sees 'Solved x/y' in its own
        column, positioned before the participant count."""
        from judge.models import ContestProblem, Submission, SubmissionSource
        from judge.models.tests.util import create_problem
        from judge.models import Language

        problem = create_problem('solved-prob', is_public=True)
        ContestProblem.objects.create(contest=self.past_contest, problem=problem, points=100, order=1)
        submission = Submission.objects.create(
            user=self.users['normal_user'].profile, problem=problem,
            language=Language.objects.get(key='PY3'), result='AC', points=1,
        )
        SubmissionSource.objects.create(submission=submission, source='print(1)')

        self.client.force_login(self.users['normal_user'])
        response = self.client.get(reverse('contest_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Solved 1/1', html)
        # Solved column comes before the users column in the card.
        self.assertLess(html.index('class="cl-solved-col"'), html.index('class="cl-users"'))


class ContestCreateViewTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.users.update({
            # Full contest editor: edit_all_contest plus the granular permissions
            # the Django admin gates behind (visibility, access codes, SEB,
            # ratings, org scoping...). Without them, ContestCreate._restricted_fields
            # correctly refuses to write those fields — so tests asserting
            # is_visible / organizations are saved must grant them.
            'staff_contest_edit_all': create_user(
                username='staff_contest_edit_all',
                is_staff=True,
                user_permissions=(
                    'edit_own_contest', 'edit_all_contest', 'contest_rating',
                    'lock_contest', 'contest_access_code', 'contest_seb_requirement',
                    'create_private_contest', 'change_contest_visibility',
                    'contest_problem_label', 'override_performance_ceiling',
                ),
            ),
            'normal_user': create_user(username='normal_user'),
            # Holds only create_private_contest (no edit_all_contest): the "Add
            # assignment" entry must be granted by this permission alone.
            'contest_creator': create_user(
                username='contest_creator',
                user_permissions=('create_private_contest',),
            ),
            # edit_own_contest but NOT create_private_contest: must NOT see the create entry.
            'edit_own_only': create_user(
                username='edit_own_only',
                user_permissions=('edit_own_contest',),
            ),
        })

        cls.organization = Organization.objects.create(name='Test Org', slug='test-org')
        cls.tag = ContestTag.objects.create(name='test-tag', color='#0000ff')

    def setUp(self):
        self.client = Client()

    def test_get_status_code(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        response = self.client.get(reverse('contest_create'))
        self.assertEqual(response.status_code, 200)

    def test_get_template(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        response = self.client.get(reverse('contest_create'))
        self.assertTemplateUsed(response, 'contest/create.html')

    def test_get_unauthorized(self):
        self.client.force_login(self.users['normal_user'])
        response = self.client.get(reverse('contest_create'))
        self.assertEqual(response.status_code, 403)

    def test_create_private_contest_grants_access(self):
        """A user holding only ``create_private_contest`` (no ``edit_all_contest``)
        may open the contest creator — the "Add assignment" entry follows this permission."""
        self.client.force_login(self.users['contest_creator'])
        response = self.client.get(reverse('contest_create'))
        self.assertEqual(response.status_code, 200)

    def test_edit_own_contest_without_create_is_denied(self):
        """``edit_own_contest`` alone must NOT grant the creator entry."""
        self.client.force_login(self.users['edit_own_only'])
        response = self.client.get(reverse('contest_create'))
        self.assertEqual(response.status_code, 403)

    def test_add_assignment_tab_gated_on_create_private_contest(self):
        """The 'Add Assignment' tab renders for create_private_contest holders and is absent
        otherwise (list page uses contest-list-tabs.html)."""
        self.client.force_login(self.users['contest_creator'])
        html = self.client.get(reverse('contest_list')).content.decode()
        self.assertIn('/contests/create/', html)
        self.assertIn('Add Assignment', html)

        self.client.force_login(self.users['edit_own_only'])
        html = self.client.get(reverse('contest_list')).content.decode()
        self.assertNotIn('/contests/create/', html)

    def test_post_success(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'New Contest',
            'key': 'newcontest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
            'description': 'A test contest',
            'is_visible': True,
            'is_rated': False,
            'format_name': 'default',
            'scoreboard_visibility': 'V',
            'use_clarifications': True,
            'allow_virtual_participation': True,
            'points_precision': 3,
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result['ok'])
        self.assertIn('redirect', result)

        contest = Contest.objects.get(key='newcontest')
        self.assertEqual(contest.name, 'New Contest')
        self.assertTrue(contest.is_visible)
        self.assertEqual(contest.authors.count(), 1)

    def test_post_missing_fields(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        data = {'name': 'Incomplete Contest'}

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_post_invalid_key(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'Invalid Key Contest',
            'key': 'INVALID-KEY!',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('lowercase', response.json()['error'])

    def test_post_duplicate_key(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        create_contest(
            key='existingcontest',
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1),
            authors=('superuser',),
        )

        now = timezone.now()
        data = {
            'name': 'Duplicate',
            'key': 'existingcontest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('already exists', response.json()['error'])

    def test_post_invalid_dates(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'Bad Dates',
            'key': 'baddates',
            'start_time': (now + timedelta(hours=3)).isoformat(),
            'end_time': (now + timedelta(hours=1)).isoformat(),
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('before end time', response.json()['error'])

    def test_post_unauthorized(self):
        self.client.force_login(self.users['normal_user'])
        now = timezone.now()
        data = {
            'name': 'Unauthorized',
            'key': 'unauthorized',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_post_with_duration(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'Duration Contest',
            'key': 'durationcontest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
            'duration_hours': 2,
            'duration_minutes': 30,
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='durationcontest')
        self.assertEqual(contest.time_limit, timedelta(hours=2, minutes=30))

    def test_post_with_organizations(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'Org Contest',
            'key': 'orgcontest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
            'organizations': [self.organization.id],
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='orgcontest')
        self.assertIn(self.organization, contest.organizations.all())

    def test_post_with_tags(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'Tagged Contest',
            'key': 'taggedcontest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
            'tags': [self.tag.id],
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='taggedcontest')
        self.assertIn(self.tag, contest.tags.all())

    def test_post_author_set(self):
        self.client.force_login(self.users['staff_contest_edit_all'])
        now = timezone.now()
        data = {
            'name': 'Author Test',
            'key': 'authortest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }

        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='authortest')
        self.assertEqual(contest.authors.first(), self.users['staff_contest_edit_all'].profile)


class ContestDetailSolvedStatusTestCase(TestCase):
    """Detail page: green checkmark for problems solved *within the contest*.

    Also covers the exam-style gating (a hidden result must not leak through the
    checkmark before ``results_finalized``) and the "No description." placeholder
    for contests without a statement.

    Rendered with social + comments disabled so the page only goes through the
    Jinja2 engine — on Python 3.14 + Django 4.2 the Django-DTL error path crashes
    the test client's template-context copy (pre-existing env issue), which would
    mask the real assertion below.
    """

    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        _now = timezone.now()
        cls.contest = create_contest(
            key='statcontest', name='Stats',
            start_time=_now - timedelta(days=1), end_time=_now + timedelta(days=1),
            is_visible=True, description='',
        )
        cls.solved_problem = create_problem('solved-problem')
        cls.attempted_problem = create_problem('attempted-problem')
        from judge.models import ContestProblem, Language
        cls.cp_solved = ContestProblem.objects.create(
            contest=cls.contest, problem=cls.solved_problem, points=100, order=1)
        cls.cp_attempted = ContestProblem.objects.create(
            contest=cls.contest, problem=cls.attempted_problem, points=100, order=2)
        cls.user = create_user(username='status_user')
        # AC (full points) → solved; WA → attempted only.
        cls.ac = Submission.objects.create(
            user=cls.user.profile, problem=cls.solved_problem,
            language=Language.objects.get(key='PY3'),
            result='AC', status='D', contest_object=cls.contest,
            case_points=100, case_total=100,
        )
        cls.wa = Submission.objects.create(
            user=cls.user.profile, problem=cls.attempted_problem,
            language=Language.objects.get(key='PY3'),
            result='WA', status='D', contest_object=cls.contest,
            case_points=0, case_total=100,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def _get_detail(self):
        return self.client.get(reverse('contest_view', args=['statcontest']))

    @override_settings(DMOJ_ENABLE_SOCIAL=False, DMOJ_ENABLE_COMMENTS=False)
    def test_solved_and_attempted_sets(self):
        response = self._get_detail()
        self.assertEqual(response.status_code, 200)
        solved = response.context['contest_solved_problem_ids']
        attempted = response.context['contest_attempted_problem_ids']
        self.assertIn(self.solved_problem.id, solved)
        self.assertNotIn(self.attempted_problem.id, solved)
        self.assertIn(self.solved_problem.id, attempted)
        self.assertIn(self.attempted_problem.id, attempted)

    @override_settings(DMOJ_ENABLE_SOCIAL=False, DMOJ_ENABLE_COMMENTS=False)
    def test_solved_problem_renders_green_check(self):
        response = self._get_detail()
        html = response.content.decode()
        # The AC problem's row carries a solved checkmark icon.
        self.assertIn('solved-problem-color fa fa-check-circle', html)
        # The WA-only problem must not render one.
        check_rows = html.count('solved-problem-color fa fa-check-circle')
        self.assertEqual(check_rows, 1)

    @override_settings(DMOJ_ENABLE_SOCIAL=False, DMOJ_ENABLE_COMMENTS=False)
    def test_empty_description_renders_placeholder(self):
        """A contest without a statement must show a placeholder, not an empty box."""
        response = self._get_detail()
        self.assertEqual(response.status_code, 200)
        self.assertIn('No description.', response.content.decode())

    @override_settings(DMOJ_ENABLE_SOCIAL=False, DMOJ_ENABLE_COMMENTS=False)
    def test_anonymous_gets_no_solved_state(self):
        client = Client()
        response = client.get(reverse('contest_view', args=['statcontest']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['contest_solved_problem_ids'], set())
        self.assertEqual(response.context['contest_attempted_problem_ids'], set())


class ContestDetailExamHiddenResultsTestCase(TestCase):
    """Exam-style contests must not leak verdicts through the checkmark before release."""

    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        _now = timezone.now()
        cls.contest = create_contest(
            key='examcontest', name='Exam',
            start_time=_now - timedelta(days=1), end_time=_now + timedelta(days=1),
            is_visible=True, format_name='exam',
        )
        cls.problem = create_problem('exam-problem')
        from judge.models import ContestProblem, Language
        cls.cp = ContestProblem.objects.create(
            contest=cls.contest, problem=cls.problem, points=100, order=1,
            hide_results=True,
        )
        cls.user = create_user(username='exam_user')
        cls.sub = Submission.objects.create(
            user=cls.user.profile, problem=cls.problem,
            language=Language.objects.get(key='PY3'),
            result='AC', status='D', contest_object=cls.contest,
            case_points=100, case_total=100,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def _get_detail(self):
        return self.client.get(reverse('contest_view', args=['examcontest']))

    @override_settings(DMOJ_ENABLE_SOCIAL=False, DMOJ_ENABLE_COMMENTS=False)
    def test_solved_hidden_before_finalize(self):
        """Before release the checkmark must be withheld (exam integrity)."""
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.results_finalized)
        response = self._get_detail()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['contest_solved_problem_ids'], set())

    @override_settings(DMOJ_ENABLE_SOCIAL=False, DMOJ_ENABLE_COMMENTS=False)
    def test_solved_revealed_after_finalize(self):
        self.contest.results_finalized = True
        self.contest.save(update_fields=['results_finalized'])
        response = self._get_detail()
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.problem.id, response.context['contest_solved_problem_ids'])

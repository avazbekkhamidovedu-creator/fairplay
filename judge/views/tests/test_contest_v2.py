import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestTag, Organization
from judge.models.tests.util import CommonDataMixin, create_contest, create_user


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

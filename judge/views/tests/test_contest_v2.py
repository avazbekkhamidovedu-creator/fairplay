from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestTag
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

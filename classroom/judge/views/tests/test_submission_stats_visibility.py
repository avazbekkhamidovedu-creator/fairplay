"""The Statistics chart on /submissions/ counts exactly the submissions the list shows the
viewer. It used to count every submission on the site (cached for everyone), so a visitor saw
"No submissions" next to "Total: 10" and learned how many answers private courses had."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Submission
from judge.models.tests.util import CommonDataMixin, create_problem, create_user


class SubmissionStatsVisibilityTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.author = create_user(username='st_author', user_permissions=('edit_own_problem',))
        cls.private = create_problem(code='st_private', is_public=False, authors=('st_author',))
        cls.public = create_problem(code='st_public', is_public=True)
        language = Language.get_python3()
        for result in ('AC', 'WA', 'AC'):
            Submission.objects.create(user=cls.author.profile, problem=cls.private, language=language,
                                      status='D', result=result, time=0.1, memory=1024)
        Submission.objects.create(user=cls.author.profile, problem=cls.public, language=language,
                                  status='D', result='AC', time=0.1, memory=1024)

    def setUp(self):
        cache.clear()

    def total(self):
        data = self.client.get(reverse('all_submissions') + '?results').json()
        return data['total']

    def test_anonymous_counts_only_what_the_list_shows(self):
        response = self.client.get(reverse('all_submissions'))
        self.assertEqual({s.problem.code for s in response.context['submissions']}, {'st_public'})
        self.assertEqual(self.total(), 1)

    def test_author_counts_their_private_problem_too(self):
        self.client.force_login(self.author)
        self.assertEqual(self.total(), 4)

    def test_signed_in_users_do_not_share_the_cached_chart(self):
        self.client.force_login(self.author)
        self.assertEqual(self.total(), 4)
        self.client.force_login(self.users['normal'])
        self.assertEqual(self.total(), 1)
        self.client.logout()
        self.assertEqual(self.total(), 1)

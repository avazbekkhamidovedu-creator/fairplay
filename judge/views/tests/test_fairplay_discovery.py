from unittest import mock

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.utils.fairplay import fairplay_hidden_for
from judge.views.contests import ContestList
from judge.views.fairplay_discovery import discovery_context
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin, SECRETS


@FAIRPLAY_ON
class FairPlayDiscoveryTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.student)

    def request(self, user=None, **params):
        request = RequestFactory().get('/contests/', params)
        request.user = user or self.student
        request.profile = request.user.profile
        return request

    def rows(self, user=None, **params):
        return discovery_context(self.request(user, **params), timezone.now(), filter_rows=True)['fairplay_discovery']

    def test_enrolled_student_sees_normal_card_and_two_action_launch(self):
        self.assertEqual([row['key'] for row in self.rows()], [self.exam.key])
        response = self.client.get(reverse('contest_list'))
        self.assertNotContains(response, 'Your FairPlay exams')
        self.assertContains(response, 'Start exam')
        self.assertContains(response, reverse('contest_fairplay_launch', args=[self.exam.key]))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        for secret in SECRETS:
            self.assertNotContains(response, secret)
        self.assertNotContains(response, 'data-authors=')
        self.assertNotContains(response, 'Write it.')
        row = self.rows()[0]
        for field, value in (('orgs', ','.join(row['orgs'])), ('classes', ','.join(row['classes'])),
                             ('tags', ','.join(row['tags'])), ('status', row['status']),
                             ('search', row['name'] + ' ' + row['key'])):
            self.assertContains(response, 'data-%s="%s"' % (field, value))
        self.assertEqual(response.context_data['total_count'], 1)
        self.assertNotContains(response, '<div class="cl-empty">There are no assignments.</div>', html=True)
        problem = self.client.get(reverse('problem_detail', args=[self.code.code]))
        self.assertNotContains(problem, 'Write it.', status_code=problem.status_code)
        self.assertIn(problem.status_code, (403, 404))
        launch = self.client.get(reverse('contest_fairplay_launch', args=[self.exam.key]))
        for label in ('Start exam in FairPlay', 'Download FairPlay'):
            self.assertContains(launch, label)
        self.assertNotContains(launch, "Download launch file")
        self.assertNotContains(launch, "access code")
        self.assertTrue(fairplay_hidden_for(self.student, self.exam))
        self.assertFalse(self.exam.is_accessible_by(self.student))

    def test_legacy_list_uses_same_metadata(self):
        request = self.client.get(reverse('contest_list')).wsgi_request
        response = ContestList.as_view()(request)
        response.render()
        self.assertContains(response, 'Your FairPlay exams')
        self.assertContains(response, reverse('contest_fairplay_launch', args=[self.exam.key]))
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_outsider_banned_and_revoked_membership_have_no_discovery(self):
        self.assertEqual(self.rows(self.outsider), [])
        self.exam.banned_users.add(self.student.profile)
        self.assertEqual(self.rows(), [])
        self.exam.banned_users.clear()
        self.klass.members.remove(self.student.profile)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.client.get(reverse('contest_fairplay_launch', args=[self.exam.key])).status_code, 404)

    def test_invisible_exam_is_not_listed_and_no_confirmation_is_needed(self):
        self.exam.is_visible = False
        self.exam.save(update_fields=['is_visible'])
        self.assertEqual(self.rows(), [])
        self.exam.is_visible = True
        self.exam.save(update_fields=['is_visible'])
        self.assertEqual(len(self.rows()), 1)

    def test_metadata_filters_and_student_author_privacy(self):
        row = self.rows()[0]
        for field, value in (('org', row['orgs'][0]), ('class', row['classes'][0]),
                             ('tag', 'fairplay'), ('search', self.exam.key), ('time', row['status'])):
            self.assertEqual(len(self.rows(**{field: value})), 1)
            self.assertEqual(self.rows(**{field: 'no-match'}), [] if field != 'time' else self.rows())
        self.assertEqual(self.rows(time='upcoming'), [])
        self.assertEqual(self.rows(author='999999')[0]['authors'], [])
        with mock.patch('judge.views.problem.user_can_see_problem_author_metadata', return_value=True):
            self.assertEqual(self.rows(author=str(self.teacher.profile.pk))[0]['authors'], [self.teacher.profile.pk])
            self.assertEqual(self.rows(author='999999'), [])

    def test_v2_filter_catalog_includes_only_authorized_exam_scope(self):
        response = self.client.get(reverse('contest_list'))
        self.assertIn(self.org, response.context_data['organizations'])
        self.assertIn(self.klass, response.context_data['classes'])
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('contest_list'))
        self.assertEqual(response.context_data['fairplay_discovery'], [])
        self.assertNotContains(response, reverse('contest_fairplay_launch', args=[self.exam.key]))

    def test_anonymous_and_outsider_cannot_discover_or_open(self):
        self.client.logout()
        response = self.client.get(reverse('contest_list'))
        self.assertEqual(response.context_data['fairplay_discovery'], [])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('contest_fairplay_launch', args=[self.exam.key])).status_code, 404)

import json

from django.test import TestCase
from django.urls import reverse

from judge.models import Contest, ContestParticipation
from judge.utils.fairplay import fairplay_hidden_for
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, SECRETS, create_contest


@FAIRPLAY_ON
class FairPlayLaunchTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.student)

    def url(self, file=False, key='fpexam'):
        return reverse('contest_fairplay_launch_file' if file else 'contest_fairplay_launch', args=[key])

    def test_two_matching_actions_without_codes_or_exam_contents(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        for text in ('Start exam in FairPlay', 'Download FairPlay', 'fairplay-download/releases/latest',
                     'fairplay://launch?v=1&amp;exam=fpexam'):
            self.assertContains(response, text)
        self.assertNotContains(response, "Download launch file")
        self.assertNotContains(response, "access code")
        for secret in SECRETS:
            self.assertNotContains(response, secret)
        self.assertIn('no-store', response['Cache-Control'])

    def test_launch_file_contains_only_version_and_exam_reference(self):
        response = self.client.get(self.url(file=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'version': 1, 'exam_key': 'fpexam'})
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="fpexam.fairplay"')
        self.assertIn('no-store', response['Cache-Control'])

    def test_anonymous_cannot_download_or_open(self):
        self.client.logout()
        for file in (False, True):
            self.assertEqual(self.client.get(self.url(file)).status_code, 302)

    def test_private_exam_is_hidden_from_outsiders(self):
        self.client.force_login(self.outsider)
        for file in (False, True):
            self.assertEqual(self.client.get(self.url(file)).status_code, 404)

    def test_banned_student_cannot_open_or_download(self):
        self.exam.banned_users.add(self.student.profile)
        for file in (False, True):
            self.assertEqual(self.client.get(self.url(file)).status_code, 404)

    def test_bridge_off_or_other_contest_is_not_a_launch_page(self):
        with FAIRPLAY_OFF:
            self.assertEqual(self.client.get(self.url()).status_code, 404)
        other = create_contest('normal-launch')
        self.assertEqual(self.client.get(self.url(key=other.key)).status_code, 404)

    def test_join_opens_launch_page_without_creating_participation(self):
        url = reverse('contest_join', args=['fpexam'])
        before = ContestParticipation.objects.filter(contest=self.exam, user=self.student.profile).count()
        for method in (self.client.get, self.client.post):
            response = method(url)
            self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        self.assertEqual(ContestParticipation.objects.filter(
            contest=self.exam, user=self.student.profile).count(), before)

    def test_contest_entry_points_at_the_launch_screen(self):
        response = self.client.get(reverse('contest_view', args=['fpexam']))
        self.assertContains(response, 'Start exam in FairPlay')
        self.assertContains(response, 'fairplay://launch?v=1&amp;exam=fpexam')
        self.assertNotContains(response, 'Write it.')
        self.assertTrue(fairplay_hidden_for(self.student, self.exam))
        self.assertFalse(self.exam.is_accessible_by(self.student))

    def test_private_exam_needs_class_membership_and_explicit_student_list(self):
        Contest.objects.filter(pk=self.exam.pk).update(is_private=True)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.exam.private_contestants.add(self.student.profile)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
        exam = Contest.objects.get(pk=self.exam.pk)
        apply_fairplay_editor(exam, parse_fairplay_payload(exam, {
            'fairplay_required': True, 'fairplay_audience_confirm': True,
            'fairplay_audience_mode': 'classes'}, set()))
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.exam.private_contestants.add(self.outsider.profile)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_org_only_exam_uses_org_membership_when_no_classes_are_assigned(self):
        self.exam.classes.clear()
        from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
        apply_fairplay_editor(self.exam, parse_fairplay_payload(self.exam, {
            'fairplay_required': True, 'fairplay_audience_confirm': True,
            'fairplay_audience_mode': 'organizations'}, set()))
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.outsider.profile.organizations.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_invisible_exam_has_no_student_launch_page(self):
        Contest.objects.filter(pk=self.exam.pk).update(is_visible=False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_testers_keep_the_existing_full_contest_view(self):
        self.client.force_login(self.tester)
        response = self.client.get(reverse('contest_view', args=['fpexam']))
        self.assertContains(response, 'class="contest-problems-wrap"')
        self.assertContains(response, self.code.name)
        self.assertContains(response, reverse('problem_detail', args=[self.code.code]))
        self.assertNotContains(response, 'Open exam in FairPlay')

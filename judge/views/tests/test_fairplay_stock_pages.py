"""FairPlay may gate only the protected content of its own exam; every other DKU OJ page stays stock."""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from judge.models import ContestParticipation, FairPlayDeviceSession, FairPlayDeviceSlot, Profile
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin, create_contest, set_fairplay

LISTS = ['/', '/contests/', '/problems/', '/submissions/', '/users/', '/user']
MARKERS = ('Open exam in FairPlay', 'fairplay://launch')


def interstitial(response):
    body = response.content.decode(errors='ignore')
    return any(marker in body for marker in MARKERS)


@FAIRPLAY_ON
class StockPagesTests(FairPlayDataMixin, TestCase):
    def fetch(self, user, path):
        client = Client()
        client.force_login(user)
        return client.get(path)

    def fetch_stock(self, user, path):
        with override_settings(FAIRPLAY_TOKEN='', FAIRPLAY_URL=''):
            return self.fetch(user, path)

    def attach(self, user, contest):
        participation = ContestParticipation.objects.create(contest=contest, user=user.profile,
                                                            real_start=timezone.now(), virtual=0)
        Profile.objects.filter(pk=user.profile.pk).update(current_contest=participation)
        return participation

    def assert_lists_stock(self, user, label):
        for path in LISTS:
            response, stock = self.fetch(user, path), self.fetch_stock(user, path)
            self.assertEqual(response.status_code, stock.status_code, (label, path))
            self.assertEqual(response.get('Location'), stock.get('Location'), (label, path))
            self.assertFalse(interstitial(response), (label, path))

    def test_student_without_a_participation_sees_stock_lists(self):
        self.assert_lists_stock(self.student, 'plain student')

    def test_student_attached_to_the_exam_but_not_admitted_sees_stock_lists(self):
        self.attach(self.student, self.exam)
        self.assert_lists_stock(self.student, 'attached student')

    def test_student_attached_to_a_non_fairplay_exam_sees_no_interstitial_anywhere(self):
        now = timezone.now()
        plain = create_contest('stockexam', name='Plain', start_time=now - timedelta(hours=1),
                               end_time=now + timedelta(hours=2), is_visible=True, format_name='exam')
        self.attach(self.student, plain)
        self.assert_lists_stock(self.student, 'non-fairplay')
        self.assertFalse(interstitial(self.fetch(self.student, '/contest/stockexam')))

    def test_instructor_sees_stock_lists_and_the_exam_itself(self):
        self.assert_lists_stock(self.teacher, 'instructor')
        self.assertFalse(interstitial(self.fetch(self.teacher, '/contest/fpexam')))

    def test_interstitial_belongs_only_to_the_exams_own_page(self):
        self.assertTrue(interstitial(self.fetch(self.student, '/contest/fpexam')))
        self.attach(self.student, self.exam)
        self.assertTrue(interstitial(self.fetch(self.student, '/contest/fpexam')))
        for path in LISTS:
            self.assertFalse(interstitial(self.fetch(self.student, path)), path)

    def test_assignments_list_keeps_the_exam_row_and_a_launch_link(self):
        body = self.fetch(self.student, '/contests/').content.decode()
        self.assertIn('CS 101 Midterm', body)


@FAIRPLAY_ON
class StaleAttachmentTests(FairPlayDataMixin, TestCase):
    def session(self, user=None, joined=False, lease_age=timedelta(hours=1), contest=None):
        user = user or self.student
        contest = contest or self.exam
        session = FairPlayDeviceSession.objects.create(
            user=user, contest=contest, fingerprint='a' * 64, browser_digest='b' * 64,
            lease_until=timezone.now() - lease_age)
        participation = ContestParticipation.objects.create(contest=contest, user=user.profile,
                                                            real_start=timezone.now(), virtual=0)
        if joined:
            session.participation = participation
            session.save(update_fields=['participation'])
        FairPlayDeviceSlot.objects.update_or_create(user=user, defaults={'session': session})
        Profile.objects.filter(pk=user.profile.pk).update(current_contest=participation)
        return session, participation

    def attached(self, user=None):
        return Profile.objects.get(user=user or self.student).current_contest_id

    def other_exam(self):
        now = timezone.now()
        other = create_contest('otherexam', name='Other', start_time=now - timedelta(hours=1),
                               end_time=now + timedelta(hours=2), is_visible=True, format_name='exam')
        set_fairplay(other, True)   # a claimable exam carries the lock
        return other

    def test_stale_unjoined_auto_release_clears_the_attachment(self):
        from judge.utils.fairplay_device_session import release_attachment
        session, participation = self.session()
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(state='released')
        self.assertEqual(release_attachment(FairPlayDeviceSession.objects.get(pk=session.pk)), 1)
        self.assertIsNone(self.attached())

    def test_claim_of_another_exam_frees_a_stale_attachment_of_the_old_one(self):
        from judge.utils.fairplay_device_session import claim_device
        session, participation = self.session()
        other = self.other_exam()
        claim_device(user_id=self.student.pk, contest=other, fingerprint='a' * 64, browser_digest='c' * 64)
        self.assertEqual(FairPlayDeviceSession.objects.get(pk=session.pk).state, 'released')
        self.assertIsNone(self.attached())

    def test_abort_clears_the_attachment_of_an_unjoined_session(self):
        from judge.utils.fairplay_device_session import abort_device
        session, participation = self.session(lease_age=timedelta(seconds=-30))
        abort_device(session_id=session.pk, user_id=self.student.pk, fingerprint='a' * 64, browser_digest='b' * 64)
        self.assertIsNone(self.attached())

    def test_live_joined_session_keeps_its_attachment(self):
        from judge.utils.fairplay_device_session import release_attachment
        session, participation = self.session(joined=True, lease_age=timedelta(seconds=-30))
        self.assertEqual(release_attachment(session), 0)
        self.assertEqual(self.attached(), participation.pk)

    def test_another_contests_attachment_is_never_cleared(self):
        from judge.utils.fairplay_device_session import release_attachment
        session, participation = self.session()
        other = self.other_exam()
        elsewhere = ContestParticipation.objects.create(contest=other, user=self.student.profile,
                                                        real_start=timezone.now(), virtual=0)
        Profile.objects.filter(pk=self.student.profile.pk).update(current_contest=elsewhere)
        self.assertEqual(release_attachment(session), 0)
        self.assertEqual(self.attached(), elsewhere.pk)

    def test_failsafe_release_after_the_deadline_clears_an_ended_participation(self):
        from judge.models import Contest
        from judge.views.fairplay_native_session import close_after_deadline
        session, participation = self.session(joined=True, lease_age=timedelta(seconds=-30))
        Contest.objects.filter(pk=self.exam.pk).update(end_time=timezone.now() - timedelta(minutes=20))
        with self.settings(ASSIGNMENT_HARVEST_ENABLED=False):
            self.assertEqual(close_after_deadline(session).state, 'released')
        self.assertIsNone(self.attached())

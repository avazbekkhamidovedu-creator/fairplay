from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from judge.models import FairPlayDeviceSession, FairPlayDeviceSlot, FairPlayDeviceAudit, ContestParticipation, AssignmentDraft
from judge.utils.fairplay_device_session import claim_device, admitted_session, instructor_unlock, DeviceSessionDenied, attach_participation, begin_finishing, confirm_completed
from judge.views.tests.fairplay_base import FairPlayDataMixin


class DeviceSessionTests(FairPlayDataMixin, TestCase):
    def claim(self, fingerprint='a' * 64, digest='b' * 64):
        return claim_device(user_id=self.student.pk, contest=self.exam, fingerprint=fingerprint, browser_digest=digest)

    def test_claim_is_idempotent_only_for_same_device_and_browser(self):
        first = self.claim()
        self.assertEqual(self.claim().pk, first.pk)
        with self.assertRaises(DeviceSessionDenied):
            self.claim('c' * 64, 'b' * 64)
        self.assertEqual(self.claim('a' * 64, 'c' * 64).pk, first.pk)
        self.assertEqual(FairPlayDeviceSession.objects.count(), 1)

    def test_expiry_denies_requests_without_allowing_device_takeover(self):
        session = self.claim()
        session.lease_until = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=['lease_until'])
        self.assertIsNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='b' * 64))
        with self.assertRaises(DeviceSessionDenied):
            self.claim('c' * 64)
        self.assertEqual(FairPlayDeviceSlot.objects.get(user_id=self.student.pk).session_id, session.pk)

    def test_same_browser_retry_renews_expired_lease_without_new_session(self):
        session = self.claim()
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(
            lease_until=timezone.now() - timedelta(seconds=60))
        resumed = self.claim()
        self.assertEqual(resumed.pk, session.pk)
        self.assertGreater(resumed.lease_until, timezone.now())
        self.assertIsNotNone(admitted_session(user_id=self.student.pk,
            fingerprint='a' * 64, browser_digest='b' * 64))
        self.assertEqual(FairPlayDeviceSession.objects.count(), 1)
        with self.assertRaises(DeviceSessionDenied):
            self.claim('c' * 64)

    def test_unlock_revokes_before_recording_teacher_instruction(self):
        session = self.claim()
        result = instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Device stuck')
        self.assertEqual(result.pk, session.pk)
        session.refresh_from_db()
        self.assertEqual(session.state, 'released')
        self.assertIsNone(FairPlayDeviceSlot.objects.get(user_id=self.student.pk).session_id)
        self.assertIsNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='b' * 64))
        self.assertEqual(FairPlayDeviceAudit.objects.get(session=session).actor, self.teacher)
        with self.assertRaises(DeviceSessionDenied):  # a plain unlock-device still ends the attempt
            self.claim()

    def test_student_cannot_unlock_or_transfer_another_session(self):
        self.claim()
        with self.assertRaises(DeviceSessionDenied):
            instructor_unlock(contest=self.exam, username=self.student.username, actor=self.student, reason='Forged request')
        self.assertEqual(FairPlayDeviceAudit.objects.count(), 0)
        self.assertEqual(FairPlayDeviceSession.objects.get().state, 'active')

    def test_identity_is_required(self):
        with self.assertRaises(DeviceSessionDenied):
            self.claim('browser-user-agent')
        self.assertEqual(FairPlayDeviceSession.objects.count(), 0)

    def bind(self):
        session = self.claim()
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
            real_start=timezone.now(), virtual=0)
        arguments = dict(session_id=session.pk, user_id=self.student.pk,
                         fingerprint='a' * 64, browser_digest='b' * 64)
        attach_participation(participation=participation, **arguments)
        return session, participation, arguments

    def test_a_draft_the_harvest_will_still_submit_prevents_completion(self):
        session, participation, arguments = self.bind()
        draft = AssignmentDraft.objects.create(participation=participation, problem=self.short,
            content='{"text":"An answer not yet received"}')
        begin_finishing(**arguments)
        self.assertIsNone(admitted_session(user_id=self.student.pk,
                                          fingerprint='a' * 64, browser_digest='b' * 64))
        with self.assertRaises(DeviceSessionDenied):
            confirm_completed(**arguments)
        session.refresh_from_db()
        self.assertEqual(session.state, 'finishing')
        self.assertEqual(FairPlayDeviceSlot.objects.get(user=self.student).session_id, session.pk)
        draft.refresh_from_db()
        self.assertIn('An answer not yet received', draft.content)

    def finish_with(self, make_draft, prepare=None):
        session, participation, arguments = self.bind()
        if prepare:
            prepare(participation)
        draft = make_draft(participation)
        begin_finishing(**arguments)
        confirm_completed(**arguments)
        session.refresh_from_db()
        self.assertEqual(session.state, 'completed')
        draft.refresh_from_db()
        self.assertTrue(draft.content)
        return participation

    def test_killed_harvest_draft_completes_but_leaves_a_signal(self):
        participation = self.finish_with(lambda p: AssignmentDraft.objects.create(
            participation=p, problem=self.short, content='{"text":"taken"}', harvested_at=timezone.now()))
        audit = FairPlayDeviceAudit.objects.get(action='completed-with-unsubmitted-draft')
        self.assertEqual(audit.reason, self.short.code)

    def test_a_normal_completion_writes_no_unsubmitted_signal(self):
        session, participation, arguments = self.bind()
        begin_finishing(**arguments)
        confirm_completed(**arguments)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='completed-with-unsubmitted-draft').exists())
        AssignmentDraft.objects.create(participation=participation, problem=self.short, content='', harvested_at=timezone.now())
        self.assertEqual(FairPlayDeviceAudit.objects.filter(action='completed-with-unsubmitted-draft').count(), 0)

    def test_a_draft_already_taken_by_a_killed_harvest_does_not_block_completion(self):
        self.finish_with(lambda p: AssignmentDraft.objects.create(
            participation=p, problem=self.short, content='{"text":"taken"}', harvested_at=timezone.now()))

    def test_a_draft_of_a_problem_removed_from_the_exam_does_not_block_completion(self):
        from judge.models import ContestProblem
        def make(participation):
            draft = AssignmentDraft.objects.create(participation=participation, problem=self.short,
                                                   content='{"text":"for a removed problem"}')
            ContestProblem.objects.filter(contest=self.exam, problem=self.short).delete()
            return draft
        self.finish_with(make)

    def test_a_draft_whose_submissions_are_used_up_does_not_block_completion(self):
        from judge.models import ContestProblem, ContestSubmission, Submission
        from judge.models.tests.util import create_user
        def prepare(participation):
            ContestProblem.objects.filter(contest=self.exam, problem=self.short).update(max_submissions=1)
            cp = ContestProblem.objects.get(contest=self.exam, problem=self.short)
            submission = Submission.objects.create(user=self.student.profile, problem=self.short,
                                                   language=__import__('judge.models', fromlist=['Language']).Language.get_answer_language())
            ContestSubmission.objects.create(submission=submission, problem=cp, participation=participation, points=0, is_pretest=False)
        self.finish_with(lambda p: AssignmentDraft.objects.create(
            participation=p, problem=self.short, content='{"text":"one too many"}'), prepare)

    def test_a_draft_of_a_disqualified_participation_does_not_block_completion(self):
        def prepare(participation):
            ContestParticipation.objects.filter(pk=participation.pk).update(is_disqualified=True)
        session, participation, arguments = self.bind()
        ContestParticipation.objects.filter(pk=participation.pk).update(is_disqualified=True)
        AssignmentDraft.objects.create(participation=ContestParticipation.objects.get(pk=participation.pk),
                                       problem=self.short, content='{"text":"disqualified"}')
        begin_finishing(**arguments)
        confirm_completed(**arguments)
        session.refresh_from_db()
        self.assertEqual(session.state, 'completed')

    def test_completion_requires_finishing_and_releases_exact_slot(self):
        session, participation, arguments = self.bind()
        with self.assertRaises(DeviceSessionDenied):
            confirm_completed(**arguments)
        begin_finishing(**arguments)
        confirm_completed(**arguments)
        session.refresh_from_db()
        self.assertEqual(session.state, 'completed')
        self.assertIsNone(FairPlayDeviceSlot.objects.get(user=self.student).session_id)
        with self.assertRaises(DeviceSessionDenied):
            self.claim()

    def test_teacher_can_release_stuck_hand_in_without_claiming_completion(self):
        session, participation, arguments = self.bind()
        begin_finishing(**arguments)
        instructor_unlock(contest=self.exam, username=self.student.username,
                          actor=self.teacher, reason='Hand-in needs technical assistance')
        with self.assertRaises(DeviceSessionDenied):
            confirm_completed(**arguments)
        session.refresh_from_db()
        self.assertEqual(session.state, 'released')
        self.assertFalse(FairPlayDeviceAudit.objects.filter(session=session, action='completed').exists())

    def test_retained_participation_does_not_allow_browser_draft_writes(self):
        session, participation, arguments = self.bind()
        profile = self.student.profile
        profile.current_contest = participation
        profile.save(update_fields=['current_contest'])
        self.client.force_login(self.student)
        response = self.client.post('/contest/fpexam/draft/fpshort',
                                    {'content': '{"text":"Unverified browser write"}'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AssignmentDraft.objects.filter(participation=participation).exists())

    def test_reopen_preserves_attempt_and_grants_one_new_session(self):
        from judge.utils.fairplay_device_session import instructor_reopen
        old, participation, arguments = self.bind()
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Recovery')
        instructor_reopen(contest=self.exam, username=self.student.username, actor=self.teacher, minutes=15, reason='Resume safely')
        new = self.claim('c' * 64, 'd' * 64)
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(new.participation_id, participation.pk)
        old.refresh_from_db()
        self.assertEqual(old.state, 'released')
        participation.refresh_from_db()
        from judge.utils.fairplay_device_session import normal_end
        self.assertEqual(participation.end_time, normal_end(participation))
        with self.assertRaises(DeviceSessionDenied):
            self.claim()
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='End resumed attempt')
        with self.assertRaises(DeviceSessionDenied):  # a plain unlock-device still ends the attempt
            self.claim('c' * 64, 'd' * 64)

    def window_of(self, session):
        from judge.utils.fairplay_device_session import reopen_until
        return reopen_until(session)

    def reopen_case(self, time_limit_minutes, started_minutes_ago, contest_end_minutes, minutes=30):
        from judge.models import Contest
        from judge.utils.fairplay_device_session import instructor_reopen
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(
            time_limit=timedelta(minutes=time_limit_minutes), end_time=now + timedelta(minutes=contest_end_minutes))
        self.exam.refresh_from_db()
        session = self.claim()
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
            real_start=now - timedelta(minutes=started_minutes_ago), virtual=0)
        attach_participation(participation=participation, session_id=session.pk, user_id=self.student.pk,
                             fingerprint='a' * 64, browser_digest='b' * 64)
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Recovery')
        previous = instructor_reopen(contest=self.exam, username=self.student.username, actor=self.teacher,
                                     minutes=minutes, reason='Resume safely')
        participation = ContestParticipation.objects.get(pk=participation.pk)
        return now, previous, participation

    def test_reopen_never_shortens_the_normal_deadline(self):
        from judge.utils.fairplay_device_session import personal_deadline
        now, previous, participation = self.reopen_case(180, 60, 600)
        normal = now + timedelta(hours=2)
        self.assertAlmostEqual(self.window_of(previous).timestamp(), normal.timestamp(), delta=5)
        self.assertAlmostEqual(participation.end_time.timestamp(), normal.timestamp(), delta=5)
        previous.participation = participation
        previous.contest = self.exam
        self.assertAlmostEqual(personal_deadline(previous).timestamp(), normal.timestamp(), delta=5)

    def test_reopen_after_the_normal_end_gives_the_requested_minutes(self):
        now, previous, participation = self.reopen_case(60, 120, 600)
        self.assertAlmostEqual(participation.real_start.timestamp(), (now - timedelta(minutes=30)).timestamp(), delta=5)
        self.assertAlmostEqual(self.window_of(previous).timestamp(), (now + timedelta(minutes=30)).timestamp(), delta=5)
        self.assertAlmostEqual(participation.end_time.timestamp(), self.window_of(previous).timestamp(), delta=5)

    def test_reopen_records_the_time_already_used(self):
        from judge.utils.fairplay_device_session import reopen_used_seconds
        now, previous, participation = self.reopen_case(60, 120, 600)
        self.assertAlmostEqual(reopen_used_seconds(previous), 120 * 60, delta=10)

    def test_extra_time_moves_the_end_of_an_active_native_student(self):
        from judge.models import Contest
        from judge.utils.fairplay_device_session import instructor_extra_time
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(time_limit=timedelta(minutes=60), end_time=now + timedelta(hours=10))
        self.exam.refresh_from_db()
        session = self.claim()
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
            real_start=now - timedelta(minutes=40), virtual=0)
        attach_participation(participation=participation, session_id=session.pk, user_id=self.student.pk,
                             fingerprint='a' * 64, browser_digest='b' * 64)
        before = ContestParticipation.objects.get(pk=participation.pk).end_time
        instructor_extra_time(contest=self.exam, username=self.student.username, actor=self.teacher,
                              minutes=15, reason='Network trouble')
        after = ContestParticipation.objects.get(pk=participation.pk).end_time
        self.assertAlmostEqual((after - before).total_seconds(), 15 * 60, delta=2)

    def test_extension_larger_than_the_time_used_never_starts_in_the_future_and_recomputes(self):
        from unittest.mock import patch
        now, previous, participation = self.reopen_case(60, 10, 600, minutes=1)
        from judge.utils.fairplay_device_session import extend_participation
        participation = ContestParticipation.objects.get(pk=participation.pk)
        with patch.object(ContestParticipation, 'recompute_results') as recompute:
            self.assertTrue(extend_participation(participation, now + timedelta(minutes=300)))
        recompute.assert_called_once_with()
        participation.refresh_from_db()
        self.assertLessEqual(participation.real_start, timezone.now())
        self.assertAlmostEqual(participation.end_time.timestamp(), (timezone.now() + timedelta(minutes=60)).timestamp(), delta=5)

    def test_extra_time_needs_a_timed_exam_and_an_active_student(self):
        from judge.utils.fairplay_device_session import instructor_extra_time
        with self.assertRaises(DeviceSessionDenied):
            instructor_extra_time(contest=self.exam, username=self.student.username, actor=self.teacher,
                                  minutes=15, reason='x')

    def test_reopen_is_capped_at_the_exam_end(self):
        now, previous, participation = self.reopen_case(60, 120, 10)
        self.assertAlmostEqual(self.window_of(previous).timestamp(), (now + timedelta(minutes=10)).timestamp(), delta=5)
        self.assertEqual(self.window_of(previous), self.exam.end_time)

    def test_reopen_message_shows_the_real_end_and_the_cap(self):
        from judge.views.fairplay import _reopen_note
        now, previous, participation = self.reopen_case(60, 120, 10)
        capped = _reopen_note(self.exam, previous, 30)
        self.assertIn('Reopened until ', capped)
        self.assertIn(self.exam.end_time.strftime('%Y-%m-%d %H:%M'), capped)
        self.assertIn('capped at the exam end', capped)
        self.assertNotIn('capped', _reopen_note(self.exam, previous, 5))

    def test_reopen_before_join_ends_with_the_normal_end_everywhere(self):
        from judge.models import Contest
        from judge.utils.fairplay_device_session import instructor_reopen, personal_deadline
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(time_limit=timedelta(minutes=120), end_time=now + timedelta(hours=10))
        self.exam.refresh_from_db()
        self.claim()
        instructor_unlock(contest=self.exam, username=self.student.username, actor=self.teacher, reason='Recovery')
        reopened = instructor_reopen(contest=self.exam, username=self.student.username, actor=self.teacher,
                                     minutes=30, reason='Resume safely')
        self.assertAlmostEqual(self.window_of(reopened).timestamp(), (now + timedelta(minutes=30)).timestamp(), delta=5)
        second = self.claim()
        self.assertAlmostEqual(personal_deadline(second).timestamp(), self.window_of(reopened).timestamp(), delta=5)
        participation = ContestParticipation.objects.create(contest=self.exam, user=self.student.profile,
            real_start=timezone.now(), virtual=0)
        attach_participation(participation=participation, session_id=second.pk, user_id=self.student.pk,
                             fingerprint='a' * 64, browser_digest='b' * 64)
        second = FairPlayDeviceSession.objects.select_related('contest', 'participation').get(pk=second.pk)
        normal = timezone.now() + timedelta(minutes=120)
        self.assertAlmostEqual(second.participation.end_time.timestamp(), normal.timestamp(), delta=10)
        self.assertEqual(personal_deadline(second), second.participation.end_time)

    def test_reopen_requires_teacher_and_no_active_account_slot(self):
        from judge.utils.fairplay_device_session import instructor_reopen
        self.claim()
        for actor in (self.teacher, self.student):
            with self.assertRaises(DeviceSessionDenied):
                instructor_reopen(contest=self.exam, username=self.student.username, actor=actor, minutes=15, reason='Invalid')
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='reopen').exists())

    def test_recovery_rebinds_only_same_device_and_revokes_old_cookie(self):
        from judge.utils.fairplay_device_session import recover_device
        old, participation, arguments = self.bind()
        with self.assertRaises(DeviceSessionDenied):
            recover_device(user_id=self.student.pk, contest=self.exam, fingerprint='c' * 64, browser_digest='d' * 64)
        recovered = recover_device(user_id=self.student.pk, contest=self.exam, fingerprint='a' * 64, browser_digest='d' * 64)
        self.assertEqual(recovered.pk, old.pk)
        self.assertEqual(recovered.participation_id, participation.pk)
        self.assertIsNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='b' * 64))
        self.assertIsNotNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='d' * 64))
        self.assertEqual(FairPlayDeviceSession.objects.count(), 1)

    def test_an_unclean_recover_admits_the_new_digest_at_once_and_holds_nothing(self):
        # Changed on purpose (owner's re-entry rule): a restart used to leave a lease-gap hold that only an instructor
        # could clear; now the same computer is simply back in, and only the old sign-in is revoked.
        from judge.utils.fairplay_device_session import recover_device
        session = self.claim()
        recover_device(user_id=self.student.pk, contest=self.exam, fingerprint='a' * 64, browser_digest='d' * 64, unclean=True)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(session=session, action='lease-gap').exists())
        self.assertGreater(FairPlayDeviceSession.objects.get(pk=session.pk).lease_until, timezone.now())
        self.assertIsNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='b' * 64))
        self.assertIsNotNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='d' * 64))

    def test_recover_without_gap_keeps_the_new_digest_admitted(self):
        from judge.utils.fairplay_device_session import recover_device
        self.claim()
        recover_device(user_id=self.student.pk, contest=self.exam, fingerprint='a' * 64, browser_digest='d' * 64)
        self.assertFalse(FairPlayDeviceAudit.objects.filter(action='lease-gap').exists())
        self.assertIsNotNone(admitted_session(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='d' * 64))

    def test_normal_renew_is_unchanged_and_stays_within_nine_queries(self):
        from django.db import connection, transaction
        from django.test.utils import CaptureQueriesContext
        from judge.utils.fairplay_device_session import renew_for_request
        session = self.claim()
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(lease_until=timezone.now() + timedelta(seconds=1))
        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            renewed, hold = renew_for_request(session_id=session.pk, user_id=self.student.pk,
                                              fingerprint='a' * 64, browser_digest='b' * 64)
        self.assertLessEqual(len(ctx.captured_queries), 9)
        self.assertFalse(hold)
        self.assertGreater(FairPlayDeviceSession.objects.get(pk=session.pk).lease_until, timezone.now() + timedelta(seconds=30))


class AdmittedAfterStartMovedLaterTests(FairPlayDataMixin, TestCase):
    def test_a_joined_session_stays_admitted_when_the_editor_moves_the_start_later(self):
        from judge.models import Contest
        session = claim_device(user_id=self.student.pk, contest=self.exam, fingerprint='a' * 64, browser_digest='b' * 64)
        args = dict(user_id=self.student.pk, fingerprint='a' * 64, browser_digest='b' * 64)
        Contest.objects.filter(pk=self.exam.pk).update(start_time=timezone.now() + timedelta(hours=1))
        self.assertIsNone(admitted_session(**args))
        participation = ContestParticipation.objects.create(
            contest=self.exam, user=self.student.profile, real_start=timezone.now() - timedelta(minutes=5), virtual=0)
        FairPlayDeviceSession.objects.filter(pk=session.pk).update(participation=participation)
        self.assertEqual(admitted_session(**args).pk, session.pk)

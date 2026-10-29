"""A reopened hand-in: revision 2 replaces revision 1 in the score, both stay on file and labelled."""
import json

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from judge.models import Contest, ContestParticipation, ContestSubmission, Submission
from judge.utils.contest_grade import scoring_submissions_for_contest
from judge.utils.fairplay_delivery import _KEY_HAND_IN, DeliveryError, deliver_submission, label_revision
from judge.views.tests.fairplay_base import FAIRPLAY_ON
from judge.views.tests.test_fairplay_api import FairPlayAPIMixin, qid


@FAIRPLAY_ON
class FairPlayRevisionTestCase(FairPlayAPIMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.bodies = {}

    def deliver(self, revision=None, attempt='fp_att_1', option=None, **extra):
        body = self.bodies.setdefault((revision, attempt, option), self.answers_body(attempt=attempt, **extra))
        body = json.loads(json.dumps(body))
        if revision is not None:
            body['revision'] = revision
        body['answers'] = [a for a in body['answers'] if a['question_id'] == qid(self.mcq)]
        body['answers'][0]['choice'] = {'selected': [(option or self.right).id]}
        return deliver_submission(self.exam, body)

    def participation(self):
        return ContestParticipation.objects.get(contest=self.exam, user=self.student.profile)

    def test_second_revision_replaces_the_first_in_the_score(self):
        Contest.objects.filter(pk=self.exam.pk).update(format_name='default')
        self.exam = Contest.objects.get(pk=self.exam.pk)
        first = self.deliver()
        cs = ContestSubmission.objects.get(submission_id=first['submissions'][0]['submission_id'])
        cs.points = 10
        cs.save()
        self.assertEqual(self.participation().score, 10)

        second = self.deliver(revision=2, option=self.wrong)
        new = ContestSubmission.objects.get(submission_id=second['submissions'][0]['submission_id'])
        new.points = 0
        new.save()

        self.assertEqual(self.participation().score, 0)
        scoring = scoring_submissions_for_contest(self.exam, live_only=True)
        self.assertEqual({s.id for s in scoring.values()}, {new.submission_id})

    def test_both_hand_ins_stay_on_file_and_are_labelled(self):
        first = self.deliver()
        second = self.deliver(revision=2)
        old = Submission.objects.get(pk=first['submissions'][0]['submission_id'])
        new = Submission.objects.get(pk=second['submissions'][0]['submission_id'])
        self.assertEqual(old.review_note, 'FairPlay hand-in 1, superseded')
        self.assertEqual(new.review_note, 'FairPlay hand-in 2')
        self.assertEqual(old.contest_object_id, self.exam.id)
        self.assertEqual(new.contest_object_id, self.exam.id)
        self.assertFalse(ContestSubmission.objects.filter(submission=old).exists())
        self.assertEqual(Submission.objects.filter(contest_object=self.exam, user=self.student.profile).count(), 2)
        self.assertEqual((label_revision(old.review_note), label_revision(new.review_note)), (1, 2))

    def test_rejudging_the_old_hand_in_does_not_score_it(self):
        first = self.deliver()
        self.deliver(revision=2, option=self.wrong)
        old = Submission.objects.get(pk=first['submissions'][0]['submission_id'])
        old.case_points, old.case_total = 1, 1
        old.update_contest()
        self.assertEqual(self.participation().score, 0)

    def test_same_revision_twice_is_idempotent(self):
        self.deliver()
        one = self.deliver(revision=2)
        two = self.deliver(revision=2)
        self.assertEqual(one, two)
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 2)

    def test_same_revision_other_answers_is_refused(self):
        self.deliver()
        self.deliver(revision=2)
        with self.assertRaises(DeliveryError) as caught:
            self.deliver(revision=2, option=self.wrong)
        self.assertEqual(caught.exception.status, 409)

    def test_lower_revision_is_refused(self):
        self.deliver(revision=2)
        with self.assertRaises(DeliveryError) as caught:
            self.deliver(revision=1, attempt='fp_att_other')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(Submission.objects.filter(contest_object=self.exam).count(), 1)

    def test_replay_of_the_first_revision_after_the_second(self):
        first = self.deliver()
        self.deliver(revision=2)
        self.assertEqual(self.deliver(), first)

    def test_a_new_attempt_id_with_a_higher_revision_is_accepted(self):
        self.deliver()
        self.deliver(revision=2, attempt='fp_att_2')
        self.assertEqual(ContestSubmission.objects.filter(participation=self.participation()).count(), 1)

    def test_revision_survives_a_lost_record(self):
        self.deliver()
        self.deliver(revision=2)
        cache.delete(_KEY_HAND_IN % (self.exam.id, self.student.profile.id))
        with self.assertRaises(DeliveryError) as caught:
            self.deliver(revision=2, attempt='fp_att_again')
        self.assertEqual(caught.exception.status, 409)
        self.deliver(revision=3, attempt='fp_att_again')
        notes = sorted(Submission.objects.filter(contest_object=self.exam).values_list('review_note', flat=True))
        self.assertEqual(notes, ['FairPlay hand-in 1, superseded', 'FairPlay hand-in 2, superseded',
                                 'FairPlay hand-in 3'])

    def test_a_failed_revision_keeps_the_first_hand_in_counting(self):
        first = self.deliver()
        body = self.answers_body(attempt='fp_att_1')
        body['revision'] = 2
        body['answers'] = [{'question_id': qid(self.mcq), 'choice': {'selected': [self.right.id]}},
                           {'question_id': qid(self.short), 'short_answer': {'text': 7}}]
        with self.assertRaises(DeliveryError):
            deliver_submission(self.exam, body)
        self.assertTrue(ContestSubmission.objects.filter(
            submission_id=first['submissions'][0]['submission_id']).exists())
        self.assertEqual(cache.get(_KEY_HAND_IN % (self.exam.id, self.student.profile.id))['revision'], 1)

    def test_bad_revision_is_a_422(self):
        for value in (0, -1, 'two', True, 1.5, 10 ** 6):
            with self.subTest(value=value):
                with self.assertRaises(DeliveryError) as caught:
                    self.deliver(revision=value)
                self.assertEqual(caught.exception.status, 422)

    def test_a_teacher_note_is_kept(self):
        first = self.deliver()
        Submission.objects.filter(pk=first['submissions'][0]['submission_id']).update(review_note='Good answer')
        self.deliver(revision=2)
        self.assertEqual(Submission.objects.get(pk=first['submissions'][0]['submission_id']).review_note,
                         'Good answer')


@FAIRPLAY_ON
class FairPlayRevisionListTestCase(FairPlayAPIMixin, TestCase):
    def test_the_professor_sees_both_hand_ins_labelled(self):
        body = self.answers_body()
        body['answers'] = [a for a in body['answers'] if a['question_id'] == qid(self.mcq)]
        deliver_submission(self.exam, body)
        deliver_submission(self.exam, dict(body, revision=2))
        self.client.force_login(self.teacher)
        html = self.client.get(reverse('contest_user_submissions',
                                       args=['fpexam', 'fp_student', self.mcq.code])).content.decode()
        self.assertIn('FairPlay hand-in 1, superseded', html)
        self.assertIn('FairPlay hand-in 2', html)
        self.client.force_login(self.student)
        self.assertNotIn('FairPlay hand-in', self.client.get(
            reverse('contest_user_submissions', args=['fpexam', 'fp_student', self.mcq.code])).content.decode())

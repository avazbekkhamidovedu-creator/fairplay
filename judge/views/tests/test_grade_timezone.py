"""Grade display uses the viewer's profile zone, including DST transitions."""
import json
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestSubmission, Submission
from judge.utils.contest_grade import contest_grade_queryset, final_submission_ids_for_contest
from judge.views.tests.test_contest_grade import GradeTestBase


class GradeTimezoneTestCase(GradeTestBase):
    def test_page_and_save_use_viewer_timezone(self):
        self.author.user_permissions.add(Permission.objects.get(codename='edit_own_problem'))
        # Same UTC clock time in summer and winter: New York's offset changes.
        for instant, ny in (
            (datetime(2026, 9, 29, 13, 26, tzinfo=datetime_timezone.utc), '2026-09-29 09:26'),
            (datetime(2026, 1, 29, 13, 26, tzinfo=datetime_timezone.utc), '2026-01-29 08:26'),
        ):
            Submission.objects.filter(pk=self.submission.pk).update(
                date=instant, reviewed_at=instant, reviewed_by=self.author.profile)
            expected_ids = final_submission_ids_for_contest(self.contest)
            for zone, expected in (
                ('America/New_York', ny),
                ('Asia/Shanghai', instant.strftime('%Y-%m-%d') + ' 21:26'),
            ):
                with self.subTest(instant=instant, viewer_zone=zone):
                    self.author.profile.timezone = zone
                    self.author.profile.save(update_fields=['timezone'])
                    # Deliberately different from the viewer's zone.
                    self.student.profile.timezone = 'Pacific/Honolulu'
                    self.student.profile.save(update_fields=['timezone'])
                    client = self._client(self.author)
                    for practice in (False, True):
                        with self.subTest(practice=practice):
                            Submission.objects.filter(pk=self.submission.pk).update(
                                contest_object=None if practice else self.contest)
                            self.problem.authors.add(self.author.profile)
                            page_url = reverse('practice_grade') if practice else self.grade_url
                            save_url = reverse('practice_grade_save') if practice else self.save_url
                            response = client.get(page_url)
                            self.assertEqual(response.status_code, 200)
                            # Parse the exact JSON the browser receives, after rendering.
                            rows = json.loads(response.content.decode().split(
                                'var submissions = ')[1].split(';\n')[0])
                            row = next(row for row in rows if row['id'] == self.submission.id)
                            self.assertEqual(row['date'], expected)
                            self.assertEqual(row['reviewed_at'], expected)
                            # Exercise the actual save API without changing the fixed instant.
                            with patch('judge.utils.contest_grade.timezone.now', return_value=instant):
                                saved = client.post(save_url, {
                                    'submission': self.submission.id, 'score': 50, 'note': 'timezone test',
                                })
                            self.assertEqual(saved.status_code, 200)
                            self.assertEqual(saved.json()['reviewed_at'], expected)
                            self.submission.refresh_from_db()
                            self.assertEqual(self.submission.date, instant)
                            self.assertEqual(self.submission.reviewed_at, instant)
                    Submission.objects.filter(pk=self.submission.pk).update(contest_object=self.contest)
                    with timezone.override(zone):
                        self.assertEqual(final_submission_ids_for_contest(self.contest), expected_ids)
                        self.assertEqual(contest_grade_queryset(self.contest).first().date, instant)

    def test_unreviewed_submission_has_null_reviewed_at(self):
        response = self._client(self.author).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        rows = json.loads(response.content.decode().split('var submissions = ')[1].split(';\n')[0])
        self.assertIsNone(rows[0]['reviewed_at'])

    def test_fall_back_does_not_change_submission_order_or_final_selection(self):
        # During fall-back 01:10 EST is later than 01:50 EDT.
        earlier = datetime(2026, 11, 1, 5, 50, tzinfo=datetime_timezone.utc)
        later = datetime(2026, 11, 1, 6, 10, tzinfo=datetime_timezone.utc)
        Submission.objects.filter(pk=self.submission.pk).update(date=earlier)
        newer = Submission.objects.create(
            user=self.student.profile, problem=self.problem, language=self.submission.language,
            contest_object=self.contest, points=50, result='WA', status='D', case_total=100,
        )
        Submission.objects.filter(pk=newer.pk).update(date=later)
        ContestSubmission.objects.create(
            submission=newer, problem=self.contest_problem, participation=self.participation, points=50,
        )
        for zone in ('America/New_York', 'Asia/Shanghai'):
            with self.subTest(zone=zone), timezone.override(zone):
                self.assertEqual(list(contest_grade_queryset(self.contest).values_list(
                    'id', flat=True)), [newer.id, self.submission.id])
                self.assertEqual(final_submission_ids_for_contest(self.contest), {newer.id})

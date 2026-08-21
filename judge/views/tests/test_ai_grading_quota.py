"""Tests for student AI grading quota and rate limiting."""
from django.test import TestCase, override_settings

from judge.ai_grading_quota import (
    get_remaining, is_rate_limited, try_reserve,
)
from judge.models.tests.util import CommonDataMixin, create_user


class AIGradingQuotaTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = create_user(username='ai_quota_student')

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=0)
    def test_zero_quota_blocks_reserve(self):
        ok, err = try_reserve(self.student.profile.id, 1)
        self.assertFalse(ok)
        self.assertEqual(err, 'quota_disabled')
        self.assertEqual(get_remaining(self.student.profile.id), 0)

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=10)
    def test_rate_limit_after_five_requests(self):
        pid = 42
        uid = self.student.profile.id
        for i in range(5):
            ok, err = try_reserve(uid, pid)
            self.assertTrue(ok, err)
        self.assertTrue(is_rate_limited(uid, pid))
        ok, err = try_reserve(uid, pid)
        self.assertFalse(ok)
        self.assertTrue(err.startswith('rate_limited'))
        # Soft limit: another attempt still counts as rate_limited (no ban key).
        ok2, err2 = try_reserve(uid, pid)
        self.assertFalse(ok2)
        self.assertTrue(err2.startswith('rate_limited'))

    @override_settings(DMOJ_AI_GRADING_STUDENT_QUOTA=10)
    def test_successful_reserve_decrements_quota(self):
        uid = self.student.profile.id
        self.assertEqual(get_remaining(uid), 10)
        ok, _ = try_reserve(uid, 7)
        self.assertTrue(ok)
        self.assertEqual(get_remaining(uid), 9)

"""Tests for CSV user import (judge.utils.import_export.process_user_csv)."""
import io

from django.contrib.auth.models import User
from django.test import TestCase

from judge.models import Profile
from judge.models.tests.util import create_user
from judge.utils.import_export import process_user_csv


class UserImportTestCase(TestCase):
    HEADER = 'username,first_name,last_name,email\n'

    def _import(self, rows, **kwargs):
        content = (self.HEADER + '\n'.join(rows)).encode('utf-8')
        return process_user_csv(io.BytesIO(content), **kwargs)

    def test_creates_users_with_profile_and_default_password(self):
        result = self._import(['alice,Alice,Liddell,alice@example.com'])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.errors, [])
        user = User.objects.get(username='alice')
        self.assertTrue(user.check_password('123456'))
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_duplicate_username_within_csv_reported(self):
        result = self._import([
            'bob,Bob,One,bob1@example.com',
            'bob,Bob,Two,bob2@example.com',
        ])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('duplicate username', result.errors[0])
        # First row wins; the second must not overwrite it.
        self.assertEqual(User.objects.get(username='bob').email, 'bob1@example.com')

    def test_existing_user_skipped_unless_update_requested(self):
        create_user(username='carol')
        result = self._import(['carol,Carol,New,carol@example.com'])
        self.assertEqual(result.skipped, 1)
        self.assertEqual(User.objects.get(username='carol').first_name, '')

        result = self._import(['carol,Carol,New,carol@example.com'], update_existing=True)
        self.assertEqual(result.updated, 1)
        carol = User.objects.get(username='carol')
        self.assertEqual(carol.first_name, 'Carol')
        self.assertEqual(carol.email, 'carol@example.com')

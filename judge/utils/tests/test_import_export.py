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

    def test_creates_users_with_unusable_password_for_netid(self):
        result = self._import(['alice,Alice,Liddell,alice@example.com'])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.errors, [])
        user = User.objects.get(username='alice')
        self.assertFalse(user.has_usable_password())
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_password_column_in_csv_is_ignored(self):
        """Roster import is SSO-only; a password column must not set a local password."""
        content = (
            'username,first_name,last_name,email,password\n'
            'dave,Dave,One,dave@example.com,secret123\n'
        ).encode('utf-8')
        result = process_user_csv(io.BytesIO(content))
        self.assertEqual(result.created, 1)
        user = User.objects.get(username='dave')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password('secret123'))

    def test_existing_user_is_linked_even_without_update(self):
        from judge.models.tests.util import create_organization
        org = create_organization(name='Import Course')
        create_user(username='erin')
        content = (
            'username,first_name,last_name,email\n'
            'erin,Erin,New,erin@example.com\n'
        ).encode('utf-8')
        result = process_user_csv(io.BytesIO(content), organization=org, update_existing=False)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.org_linked, 1)
        self.assertEqual(User.objects.get(username='erin').first_name, '')
        self.assertTrue(org.members.filter(user__username='erin').exists())

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

    def test_canvas_roster_headers(self):
        content = (
            'First name,Last Name,Display Name,Duke Unique ID,NetID,netid@duke.edu,Email,Section(s)\n'
            'Qixuan,Yuan,Qixuan Yuan,1234567,qy88,qy88@duke.edu,qy88@duke.edu,CS204\n'
        ).encode('utf-8')
        result = process_user_csv(io.BytesIO(content))
        self.assertEqual(result.errors, [], result.errors)
        self.assertEqual(result.created, 1)
        user = User.objects.get(username='qy88')
        self.assertEqual(user.first_name, 'Qixuan')
        self.assertEqual(user.last_name, 'Yuan')
        self.assertEqual(user.email, 'qy88@duke.edu')

    def test_canvas_prefers_email_over_netid_duke_column(self):
        content = (
            'First name,Last Name,NetID,netid@duke.edu,Email\n'
            'Ada,Lovelace,al100,al100@duke.edu,ada@example.com\n'
        ).encode('utf-8')
        result = process_user_csv(io.BytesIO(content))
        self.assertEqual(result.created, 1)
        self.assertEqual(User.objects.get(username='al100').email, 'ada@example.com')

    def test_canvas_falls_back_to_netid_duke_email(self):
        content = (
            'First name,Last Name,NetID,netid@duke.edu\n'
            'Grace,Hopper,gh200,gh200@duke.edu\n'
        ).encode('utf-8')
        result = process_user_csv(io.BytesIO(content))
        self.assertEqual(result.created, 1)
        self.assertEqual(User.objects.get(username='gh200').email, 'gh200@duke.edu')

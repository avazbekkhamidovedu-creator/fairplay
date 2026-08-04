"""Tests for the frontend contest editor (add + edit share ``ContestCreate``).

Covers the payload→model helpers, the edit mode added at
``/contests/<key>/edit/``, the newly registered model fields, and the
regressions fixed while refactoring (invalid dates, naive datetimes, bad M2M
ids). Uses ``force_login`` so the tests exercise view-level permission logic
rather than password authentication.
"""

import json
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestTag, Organization
from judge.models.tests.util import CommonDataMixin, create_contest, create_problem, create_user
from judge.views.contests import ContestCreate


class ContestEditorHelperTestCase(TestCase):
    """Unit tests for the static payload→model helpers on ``ContestCreate``."""

    def test_parse_datetime_aware_with_z(self):
        dt = ContestCreate._parse_datetime({'start_time': '2026-08-01T10:00:00Z'}, 'start_time')
        self.assertEqual(dt, datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))

    def test_parse_datetime_naive_treated_as_utc(self):
        # A timestamp without an offset must not produce a naive datetime (that
        # would crash when compared against aware values) — interpret as UTC.
        dt = ContestCreate._parse_datetime({'start_time': '2026-08-01T10:00:00'}, 'start_time')
        self.assertTrue(timezone.is_aware(dt))
        self.assertEqual(dt, datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))

    def test_parse_datetime_blank_returns_none(self):
        self.assertIsNone(ContestCreate._parse_datetime({'start_time': ''}, 'start_time'))
        self.assertIsNone(ContestCreate._parse_datetime({}, 'start_time'))

    def test_parse_datetime_invalid_raises_validation_error(self):
        # Regression: this used to raise TypeError inside gettext, 500ing the view.
        with self.assertRaises(ValidationError):
            ContestCreate._parse_datetime({'start_time': 'not-a-date'}, 'start_time')

    def test_parse_duration(self):
        self.assertEqual(ContestCreate._parse_duration({'duration_hours': '2', 'duration_minutes': '30'}),
                         timedelta(hours=2, minutes=30))
        self.assertIsNone(ContestCreate._parse_duration({'duration_hours': '0', 'duration_minutes': '0'}))
        self.assertIsNone(ContestCreate._parse_duration({}))

    def test_error_response_maps_validation_error_to_400(self):
        response = ContestCreate._error_response(ValidationError('boom'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('boom', json.loads(response.content)['error'])

    def test_error_response_masks_internal_errors(self):
        with self.assertLogs('judge.views.contests', level='ERROR'):
            response = ContestCreate._error_response(RuntimeError('internal secret'))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret', json.loads(response.content)['error'])


class ContestEditViewTestCase(CommonDataMixin, TestCase):
    """Edit mode: access control, prefill, in-place update, key rename, M2M."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='contest_editor', user_permissions=('edit_all_contest',))
        cls.curator = create_user(username='contest_curator', user_permissions=('edit_own_contest',))
        cls.normal = create_user(username='contest_normal')

        _now = timezone.now()
        cls.contest = create_contest(
            key='editablecontest', name='Editable',
            start_time=_now + timedelta(days=1), end_time=_now + timedelta(days=2),
            authors=('superuser',),
            curators=('contest_curator',),
            description='original description',
        )
        # A contest this user does NOT curate — an edit_own_contest holder must
        # not be able to touch it.
        cls.other_contest = create_contest(
            key='othercontest', name='Other',
            start_time=_now + timedelta(days=1), end_time=_now + timedelta(days=2),
            authors=('superuser',),
        )

    def setUp(self):
        self.client = Client()

    def _full_payload(self, **overrides):
        """A complete payload — every field the editor can save."""
        now = timezone.now()
        payload = {
            'name': 'Renamed', 'key': 'editablecontest',
            'start_time': (now + timedelta(days=3)).isoformat(),
            'end_time': (now + timedelta(days=4)).isoformat(),
            'duration_hours': 1, 'duration_minutes': 0,
            'description': 'updated description',
            'is_visible': True, 'is_rated': False, 'use_clarifications': True,
            'hide_problem_tags': False, 'hide_problem_authors': False,
            'allow_virtual_participation': True, 'run_pretests_only': True,
            'rate_all': False, 'tester_see_submissions': False, 'tester_see_scoreboard': True,
            'show_short_display': False, 'is_private': False, 'is_organization_private': False,
            'limit_join_organizations': False, 'seb_required': False,
            'format_name': 'default', 'scoreboard_visibility': 'V', 'points_precision': 3,
            'format_config': '', 'access_code': '', 'problem_label_script': '',
            'og_image': '', 'logo_override_image': '', 'summary': '',
            'rating_floor': '', 'rating_ceiling': '', 'performance_ceiling_override': '',
            'locked_after': '', 'seb_browser_exam_keys': '', 'seb_config_keys': '',
            'seb_quit_url': '', 'description_extra': None,
            'authors': [], 'curators': [], 'testers': [], 'spectators': [],
            'organizations': [], 'classes': [], 'join_organizations': [], 'tags': [],
            'private_contestants': [], 'rate_exclude': [], 'view_contest_scoreboard': [],
            'view_contest_submissions': [], 'banned_users': [],
        }
        payload.update(overrides)
        return payload

    def test_get_unknown_contest_returns_404(self):
        self.client.force_login(self.editor)
        response = self.client.get(reverse('contest_edit', args=['doesnotexist']))
        self.assertEqual(response.status_code, 404)

    def test_get_denied_for_user_without_permission(self):
        self.client.force_login(self.normal)
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.status_code, 403)

    def test_get_allowed_for_edit_all_holder(self):
        self.client.force_login(self.editor)
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.status_code, 200)

    def test_get_allowed_for_curator_of_that_contest(self):
        self.client.force_login(self.curator)
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.status_code, 200)

    def test_get_denied_for_curator_of_another_contest(self):
        # edit_own_contest is object-scoped: curating contest A does not grant
        # access to contest B.
        self.client.force_login(self.curator)
        response = self.client.get(reverse('contest_edit', args=['othercontest']))
        self.assertEqual(response.status_code, 403)

    def test_get_prefills_contest_in_context(self):
        self.client.force_login(self.editor)
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['contest'], self.contest)
        self.assertTrue(response.context['is_edit'])
        self.assertEqual(response.context['prefill']['name'], 'Editable')
        self.assertEqual(response.context['prefill']['description'], 'original description')

    def test_get_tmp_timezone_overrides_echo(self):
        """?tz=... only changes how stored UTC is echoed back, not the saved profile tz."""
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'America/Toronto'
        profile.save()

        # Sanity: without ?tz= the echo uses the profile zone (America/Toronto).
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['user_timezone'], 'America/Toronto')
        from zoneinfo import ZoneInfo
        start_utc = self.contest.start_time
        self.assertEqual(response.context['prefill']['start_time'],
                         start_utc.astimezone(ZoneInfo('America/Toronto')).strftime('%Y-%m-%dT%H:%M'))

        # ?tz=Asia/Shanghai → echo shifts to UTC+8 but profile stays Toronto.
        response = self.client.get(
            reverse('contest_edit', args=['editablecontest']) + '?tz=Asia/Shanghai')
        self.assertEqual(response.context['user_timezone'], 'Asia/Shanghai')
        self.assertEqual(response.context['prefill']['start_time'],
                         start_utc.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%dT%H:%M'))
        profile.refresh_from_db()
        self.assertEqual(profile.timezone, 'America/Toronto')

    def test_get_tmp_timezone_invalid_falls_back_to_profile(self):
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'America/Toronto'
        profile.save()
        response = self.client.get(
            reverse('contest_edit', args=['editablecontest']) + '?tz=Not/AZone')
        self.assertEqual(response.context['user_timezone'], 'America/Toronto')

    def test_get_tz_options_contains_curated_zones_and_current(self):
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'Asia/Shanghai'
        profile.save()
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        names = [name for name, _ in response.context['tz_options']]
        self.assertIn('Asia/Shanghai', names)          # current first
        self.assertIn('America/New_York', names)
        self.assertEqual(names[0], 'Asia/Shanghai')    # current labelled & first
        self.assertTrue(names[0] == 'Asia/Shanghai')

    def test_tmp_timezone_persists_across_requests_in_session(self):
        """Choosing ?tz= once must stick for the rest of the session, so a
        teacher does not have to re-pick the zone every time they reopen the
        form — while the profile timezone stays untouched."""
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'America/Toronto'
        profile.save()

        # First visit with an explicit zone → stored in session.
        response = self.client.get(
            reverse('contest_edit', args=['editablecontest']) + '?tz=Asia/Shanghai')
        self.assertEqual(response.context['user_timezone'], 'Asia/Shanghai')

        # Second visit WITHOUT ?tz= → the session choice is still in effect.
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['user_timezone'], 'Asia/Shanghai')

        # Explicit ?tz= still overrides the remembered one.
        response = self.client.get(
            reverse('contest_edit', args=['editablecontest']) + '?tz=Europe/Paris')
        self.assertEqual(response.context['user_timezone'], 'Europe/Paris')
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['user_timezone'], 'Europe/Paris')

        # Profile was never modified.
        profile.refresh_from_db()
        self.assertEqual(profile.timezone, 'America/Toronto')

    def test_tmp_timezone_session_ignores_invalid_value(self):
        """A bad zone in the session must not crash the page — fall back to
        the profile timezone."""
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'America/Toronto'
        profile.save()
        session = self.client.session
        session['contest_editor_tz'] = 'Not/AZone'
        session.save()
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['user_timezone'], 'America/Toronto')

    def test_tmp_timezone_session_not_set_for_anonymous_flow(self):
        """No ?tz= and no remembered zone → profile timezone (default)."""
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'America/Chicago'
        profile.save()
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['user_timezone'], 'America/Chicago')

    def test_post_wall_clock_in_selected_tz_roundtrips(self):
        """Regression: a teacher editing in a non-browser timezone must be able
        to set a wall-clock time that is stored as the matching UTC instant.

        Frontend contract: when the temporary timezone picker shows
        Asia/Shanghai, the datetime-local input ``2026-08-04T22:56`` is sent as
        ``2026-08-04T14:56:00.000Z`` (22:56 CST = 14:56 UTC). The backend must
        store that instant, and echoing back in Asia/Shanghai must reproduce
        22:56. Before the fix the browser-local timezone leaked into the
        conversion, so the stored instant was 12h off.
        """
        self.client.force_login(self.editor)
        profile = self.editor.profile
        profile.timezone = 'Asia/Shanghai'
        profile.save()
        from zoneinfo import ZoneInfo
        payload = self._full_payload(
            start_time='2026-08-04T14:56:00.000Z',
            end_time='2026-08-05T14:56:00.000Z',
        )
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.start_time,
                         datetime(2026, 8, 4, 14, 56, tzinfo=timezone.utc))
        # Echo back in the same wall-clock zone → the teacher sees 22:56 again.
        response = self.client.get(reverse('contest_edit', args=['editablecontest']))
        self.assertEqual(response.context['user_timezone'], 'Asia/Shanghai')
        self.assertEqual(response.context['prefill']['start_time'],
                         self.contest.start_time.astimezone(ZoneInfo('Asia/Shanghai'))
                         .strftime('%Y-%m-%dT%H:%M'))
        self.assertEqual(response.context['prefill']['start_time'], '2026-08-04T22:56')
        # A student in US Eastern must see the *same instant* (10:56 EDT), not
        # the 12h-shifted value the pre-fix conversion produced.
        self.assertEqual(self.contest.start_time.astimezone(ZoneInfo('America/New_York'))
                         .strftime('%Y-%m-%dT%H:%M'), '2026-08-04T10:56')

    def test_post_updates_scalar_fields(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(self._full_payload()),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.name, 'Renamed')
        self.assertEqual(self.contest.description, 'updated description')
        self.assertTrue(self.contest.is_visible)
        self.assertTrue(self.contest.run_pretests_only)
        self.assertTrue(self.contest.tester_see_scoreboard)
        self.assertEqual(self.contest.time_limit, timedelta(hours=1))

    def test_post_renames_key(self):
        self.client.force_login(self.editor)
        payload = self._full_payload(key='renamedkey')
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.key, 'renamedkey')

    def test_post_rename_key_conflict_returns_400(self):
        self.client.force_login(self.editor)
        payload = self._full_payload(key='othercontest')  # taken by another contest
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('already exists', json.loads(response.content)['error'])

    def test_post_replaces_m2m_fields(self):
        self.client.force_login(self.editor)
        payload = self._full_payload(curators=[self.curator.id], testers=[self.normal.id])
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(set(self.contest.curators.values_list('user_id', flat=True)), {self.curator.id})
        self.assertEqual(set(self.contest.testers.values_list('user_id', flat=True)), {self.normal.id})

    def test_post_clears_m2m_with_empty_list(self):
        self.client.force_login(self.editor)
        # First set a curator, then clear it via an empty list.
        payload = self._full_payload(curators=[self.curator.id])
        self.client.post(reverse('contest_edit', args=['editablecontest']),
                         data=json.dumps(payload), content_type='application/json')
        payload = self._full_payload(curators=[])
        response = self.client.post(reverse('contest_edit', args=['editablecontest']),
                                    data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.curators.count(), 0)

    def test_post_denied_for_user_without_permission(self):
        self.client.force_login(self.normal)
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(self._full_payload()), content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_post_invalid_date_returns_400(self):
        self.client.force_login(self.editor)
        payload = self._full_payload(start_time='not-a-date')
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)


class ContestCreateNewFieldsTestCase(CommonDataMixin, TestCase):
    """Create mode: newly registered model fields + regression fixes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='fields_editor', user_permissions=('edit_all_contest',))
        cls.user_a = create_user(username='fields_user_a')
        cls.user_b = create_user(username='fields_user_b')
        cls.organization = Organization.objects.create(name='Fields Org', slug='fields-org')
        cls.tag = ContestTag.objects.create(name='fields-tag', color='#00ff00')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)

    def _post(self, **overrides):
        now = timezone.now()
        data = {
            'name': 'New Contest', 'key': 'newcontest',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }
        data.update(overrides)
        return self.client.post(
            reverse('contest_create'),
            data=json.dumps(data), content_type='application/json',
        )

    def test_bool_flags_saved(self):
        response = self._post(
            name='Flags', key='flagtest',
            run_pretests_only=True, tester_see_submissions=True, tester_see_scoreboard=True,
            is_private=True, is_organization_private=True, limit_join_organizations=True,
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='flagtest')
        self.assertTrue(contest.run_pretests_only)
        self.assertTrue(contest.tester_see_submissions)
        self.assertTrue(contest.tester_see_scoreboard)
        self.assertTrue(contest.is_private)
        self.assertTrue(contest.is_organization_private)
        self.assertTrue(contest.limit_join_organizations)

    def test_seb_fields_saved(self):
        response = self._post(
            name='SEB', key='sebtest',
            seb_required=True, seb_browser_exam_keys='abc\ndef', seb_config_keys='xyz',
            seb_quit_url='https://example.com/quit',
        )
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='sebtest')
        self.assertTrue(contest.seb_required)
        self.assertEqual(contest.seb_browser_exam_keys, 'abc\ndef')
        self.assertEqual(contest.seb_config_keys, 'xyz')
        self.assertEqual(contest.seb_quit_url, 'https://example.com/quit')

    def test_format_config_json_saved(self):
        response = self._post(name='FC', key='fctest', format_config='{"points": [100, 200]}')
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='fctest')
        self.assertEqual(contest.format_config, {'points': [100, 200]})

    def test_format_config_invalid_json_returns_400(self):
        response = self._post(name='FC Bad', key='fcbad', format_config='{not json')
        self.assertEqual(response.status_code, 400)

    def test_profile_m2m_fields_saved(self):
        response = self._post(
            name='M2M', key='m2mtest',
            curators=[self.user_a.id], testers=[self.user_b.id], spectators=[self.user_a.id],
            rate_exclude=[self.user_b.id], view_contest_scoreboard=[self.user_a.id],
            view_contest_submissions=[self.user_b.id], banned_users=[self.user_a.id],
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='m2mtest')
        self.assertEqual(set(contest.curators.values_list('user_id', flat=True)), {self.user_a.id})
        self.assertEqual(set(contest.testers.values_list('user_id', flat=True)), {self.user_b.id})
        self.assertEqual(set(contest.spectators.values_list('user_id', flat=True)), {self.user_a.id})
        self.assertEqual(set(contest.rate_exclude.values_list('user_id', flat=True)), {self.user_b.id})
        self.assertEqual(set(contest.view_contest_scoreboard.values_list('user_id', flat=True)), {self.user_a.id})
        self.assertEqual(set(contest.view_contest_submissions.values_list('user_id', flat=True)), {self.user_b.id})
        self.assertEqual(set(contest.banned_users.values_list('user_id', flat=True)), {self.user_a.id})

    def test_invalid_date_string_returns_400(self):
        # Regression: a malformed date used to raise TypeError (500) because
        # gettext() rejects extra arguments.
        response = self._post(name='Bad', key='baddate', start_time='not-a-date')
        self.assertEqual(response.status_code, 400)
        self.assertIn('date format', json.loads(response.content)['error'])

    def test_naive_start_time_saved_as_utc(self):
        # A naive local timestamp (no Z) must be stored as UTC, not crash.
        response = self._post(
            name='Naive', key='naivetime',
            start_time='2026-08-01T10:00:00', end_time='2026-08-01T12:00:00',
        )
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='naivetime')
        self.assertEqual(contest.start_time, datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))

    def test_invalid_m2m_id_returns_400(self):
        response = self._post(name='Bad M2M', key='badm2m', curators=['not-an-int'])
        self.assertEqual(response.status_code, 400)

    def test_create_with_problems_saves_points(self):
        problem = create_problem('problems-save-test')
        response = self._post(name='With Prob', key='withprob', problems=[{'id': problem.id, 'points': 150}])
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='withprob')
        cp = contest.contest_problems.get()
        self.assertEqual(cp.problem, problem)
        self.assertEqual(cp.points, 150)
        self.assertEqual(cp.order, 1)

    def test_create_with_problem_ids_backward_compatible(self):
        # The legacy plain-id list (points default to 100) must still work.
        problem = create_problem('problems-legacy-test')
        response = self._post(name='Legacy Prob', key='legacyprob', problem_ids=[problem.id])
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='legacyprob')
        cp = contest.contest_problems.get()
        self.assertEqual(cp.points, 100)


class ContestEditProblemsTestCase(CommonDataMixin, TestCase):
    """Edit mode: problems are pre-filled and synced on save."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='prob_editor', user_permissions=('edit_all_contest',))
        cls.problem = create_problem('prob-edit-test')
        cls.contest = create_contest(
            key='probeditcontest', name='Prob Edit',
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=2),
            authors=('superuser',),
        )
        from judge.models import ContestProblem
        ContestProblem.objects.create(contest=cls.contest, problem=cls.problem, points=100, order=1)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)

    def _full_payload(self, **overrides):
        now = timezone.now()
        payload = {
            'name': 'Prob Edit', 'key': 'probeditcontest',
            'start_time': (now + timedelta(days=1)).isoformat(),
            'end_time': (now + timedelta(days=2)).isoformat(),
            'duration_hours': 0, 'duration_minutes': 0,
            'is_visible': False, 'is_rated': False, 'use_clarifications': True,
            'hide_problem_tags': False, 'hide_problem_authors': False,
            'allow_virtual_participation': True, 'run_pretests_only': False,
            'rate_all': False, 'tester_see_submissions': False, 'tester_see_scoreboard': False,
            'show_short_display': False, 'is_private': False, 'is_organization_private': False,
            'limit_join_organizations': False, 'seb_required': False,
            'format_name': 'default', 'scoreboard_visibility': 'V', 'points_precision': 3,
            'authors': [], 'curators': [], 'testers': [], 'spectators': [],
            'organizations': [], 'classes': [], 'join_organizations': [], 'tags': [],
            'private_contestants': [], 'rate_exclude': [], 'view_contest_scoreboard': [],
            'view_contest_submissions': [], 'banned_users': [],
        }
        payload.update(overrides)
        return payload

    def test_get_prefills_problems_in_context(self):
        response = self.client.get(reverse('contest_edit', args=['probeditcontest']))
        self.assertEqual(response.status_code, 200)
        problems = response.context['prefill']['problems']
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]['id'], self.problem.id)
        self.assertEqual(problems[0]['points'], 100)
        self.assertEqual(problems[0]['letter'], 'A')

    def test_post_updates_problem_points(self):
        response = self.client.post(
            reverse('contest_edit', args=['probeditcontest']),
            data=json.dumps(self._full_payload(problems=[{'id': self.problem.id, 'points': 250}])),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        cp = self.contest.contest_problems.get()
        self.assertEqual(cp.points, 250)
        self.assertEqual(cp.order, 1)

    def test_post_removes_problems_when_list_empty(self):
        response = self.client.post(
            reverse('contest_edit', args=['probeditcontest']),
            data=json.dumps(self._full_payload(problems=[])),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.contest_problems.count(), 0)

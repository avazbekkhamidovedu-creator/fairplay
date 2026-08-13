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
from django.core.files.uploadedfile import SimpleUploadedFile
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
        # A full contest editor: edit_all_contest plus every granular permission
        # the Django admin gates behind (contest_rating, lock_contest, access
        # codes, SEB, visibility, private contests, label script, perf ceiling).
        # Without them, ContestCreate._restricted_fields correctly refuses to
        # write those fields — see the permission-gating tests below.
        cls.editor = create_user(username='contest_editor', user_permissions=(
            'edit_all_contest', 'contest_rating', 'lock_contest', 'contest_access_code',
            'contest_seb_requirement', 'create_private_contest', 'change_contest_visibility',
            'contest_problem_label', 'override_performance_ceiling',
        ))
        cls.curator = create_user(username='contest_curator', user_permissions=('edit_own_contest',))
        cls.normal = create_user(username='contest_normal')
        cls.organization = Organization.objects.create(name='Edit Org', slug='edit-org')

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

    # ── security: granular permission gating (mirrors the Django admin) ─────

    def test_curator_cannot_escalate_visibility(self):
        # A curator holding only edit_own_contest must not be able to flip
        # is_visible (needs change_contest_visibility) — the admin makes the
        # field read-only for them, and so does the frontend editor.
        self.client.force_login(self.curator)
        payload = self._full_payload(is_visible=True)
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.is_visible)

    def test_curator_cannot_arm_seb_or_set_access_code(self):
        # contest_seb_requirement / contest_access_code are separate permissions.
        self.client.force_login(self.curator)
        payload = self._full_payload(
            seb_required=True, seb_browser_exam_keys='a' * 64,
            access_code='sneaky',
        )
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.seb_required)
        self.assertEqual(self.contest.access_code, '')

    def test_curator_cannot_mark_rated_or_lock(self):
        # contest_rating / lock_contest are separate permissions.
        self.client.force_login(self.curator)
        payload = self._full_payload(is_rated=True, rate_all=True, locked_after='2030-01-01T00:00:00Z')
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.is_rated)
        self.assertIsNone(self.contest.locked_after)

    def test_curator_cannot_change_organizations(self):
        # create_private_contest is required to scope a contest to an org.
        self.client.force_login(self.curator)
        payload = self._full_payload(organizations=[self.organization.id])
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.organizations.count(), 0)

    # ── vis_mode seg control is the single source of truth for visibility ──

    def test_vis_mode_public_sets_is_visible(self):
        self.client.force_login(self.editor)
        payload = self._full_payload(vis_mode='public')
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertTrue(self.contest.is_visible)
        self.assertFalse(self.contest.is_private)
        self.assertFalse(self.contest.is_organization_private)

    def test_vis_mode_org_sets_org_private(self):
        self.client.force_login(self.editor)
        payload = self._full_payload(vis_mode='org')
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertTrue(self.contest.is_visible)
        self.assertTrue(self.contest.is_organization_private)

    def test_vis_mode_hidden_hides(self):
        # First make the contest visible, then hide it again via the seg.
        self.contest.is_visible = True
        self.contest.save()
        self.client.force_login(self.editor)
        payload = self._full_payload(vis_mode='hidden')
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.is_visible)

    def test_duration_out_of_range_returns_400(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(self._full_payload(duration_hours=5000)), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_curator_cannot_attach_hidden_problem(self):
        # Security: a curator holding only edit_own_contest must not attach
        # another teacher's hidden problem to a contest students can see —
        # the contest detail page would otherwise leak it to them.
        hidden = create_problem('hidden-attach')  # is_public defaults False
        self.client.force_login(self.curator)
        payload = self._full_payload(problems=[{'id': hidden.id}])
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('not allowed', json.loads(response.content)['error'])

    def test_curator_keeps_existing_hidden_problem_when_saving(self):
        # Regression guard: an admin-attached hidden problem must not block a
        # curator from saving the contest (existing problems stay allowed).
        from judge.models import ContestProblem
        hidden = create_problem('hidden-existing')
        ContestProblem.objects.create(contest=self.contest, problem=hidden, points=100, order=99)
        self.client.force_login(self.curator)
        payload = self._full_payload(problems=[{'id': hidden.id, 'points': 100}])
        response = self.client.post(
            reverse('contest_edit', args=['editablecontest']),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertTrue(self.contest.contest_problems.filter(problem=hidden).exists())


class ContestCreateNewFieldsTestCase(CommonDataMixin, TestCase):
    """Create mode: newly registered model fields + regression fixes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Same full editor as ContestEditViewTestCase (see comment there).
        cls.editor = create_user(username='fields_editor', user_permissions=(
            'edit_all_contest', 'contest_rating', 'lock_contest', 'contest_access_code',
            'contest_seb_requirement', 'create_private_contest', 'change_contest_visibility',
            'contest_problem_label', 'override_performance_ceiling',
        ))
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
        # Keys must be valid 64-char hex hashes: model validation (full_clean)
        # is now enforced on save, rejecting anything shorter.
        bek = 'a' * 64
        cek = 'b' * 64
        response = self._post(
            name='SEB', key='sebtest',
            seb_required=True, seb_browser_exam_keys=bek + '\n' + cek, seb_config_keys=cek,
            seb_quit_url='https://example.com/quit',
        )
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='sebtest')
        self.assertTrue(contest.seb_required)
        self.assertEqual(contest.seb_browser_exam_keys, bek + '\n' + cek)
        self.assertEqual(contest.seb_config_keys, cek)
        self.assertEqual(contest.seb_quit_url, 'https://example.com/quit')

    def test_seb_required_without_keys_returns_400(self):
        # Regression: seb_required=True used to save with no keys (bricking the
        # contest for every student); model validation now rejects it.
        response = self._post(name='SEB Bad', key='sebbad', seb_required=True)
        self.assertEqual(response.status_code, 400)

    def test_format_config_json_saved(self):
        # ICPC accepts a config dict with a validated 'penalty' key — use it to
        # exercise the JSON round-trip (the default format rejects any config).
        response = self._post(name='FC', key='fctest', format_name='icpc',
                              format_config='{"penalty": 20}')
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='fctest')
        self.assertEqual(contest.format_name, 'icpc')
        self.assertEqual(contest.format_config, {'penalty': 20})

    def test_format_config_invalid_json_returns_400(self):
        response = self._post(name='FC Bad', key='fcbad', format_config='{not json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_format_name_returns_400(self):
        # Regression: an unregistered format name used to store fine and then
        # KeyError-crash every page rendering the contest.
        response = self._post(name='Fmt Bad', key='fmtbad', format_name='not-a-format')
        self.assertEqual(response.status_code, 400)
        self.assertIn('format', json.loads(response.content)['error'])

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
        # The problem must be one the editor may attach (public, or owned by
        # them) — the visibility rule rejects other teachers' hidden problems.
        problem = create_problem('problems-save-test', is_public=True)
        response = self._post(name='With Prob', key='withprob', problems=[{'id': problem.id, 'points': 150}])
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='withprob')
        cp = contest.contest_problems.get()
        self.assertEqual(cp.problem, problem)
        self.assertEqual(cp.points, 150)
        self.assertEqual(cp.order, 1)

    def test_create_with_problem_ids_backward_compatible(self):
        # The legacy plain-id list (points default to 100) must still work.
        problem = create_problem('problems-legacy-test', is_public=True)
        response = self._post(name='Legacy Prob', key='legacyprob', problem_ids=[problem.id])
        self.assertEqual(response.status_code, 200)
        contest = Contest.objects.get(key='legacyprob')
        cp = contest.contest_problems.get()
        self.assertEqual(cp.points, 100)

    # NOTE: attaching a *hidden* problem is only possible for trusted editors
    # (edit_all_contest / see_private_problem). Curators are blocked from it —
    # see ContestEditViewTestCase.test_curator_cannot_attach_hidden_problem.

    def test_create_saves_per_problem_options(self):
        problem = create_problem('problem-options', is_public=True)
        response = self._post(
            name='Opts', key='optstest',
            problems=[{'id': problem.id, 'points': 200, 'partial': False,
                       'is_pretested': True, 'max_submissions': 3}],
        )
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='optstest')
        cp = contest.contest_problems.get()
        self.assertEqual(cp.points, 200)
        self.assertFalse(cp.partial)
        self.assertTrue(cp.is_pretested)
        self.assertEqual(cp.max_submissions, 3)


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

    def test_get_renders_max_submissions_input_in_row(self):
        """The Max submissions input lives in the problem row's Max column,
        not as a duplicated label in the per-problem options strip below."""
        response = self.client.get(reverse('contest_edit', args=['probeditcontest']))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('class="cp-max-subs"', html)
        # The old duplicated "Max submissions" label was removed from the
        # options strip; only the header tooltip text (different wording)
        # may mention max submissions.
        self.assertNotIn('Max submissions', html)
        # The options strip still renders the other per-problem settings.
        for marker in ('cp-partial', 'cp-pretested', 'cp-results', 'cp-out-prefix'):
            self.assertIn(marker, html)

    def test_post_roundtrips_max_submissions_from_row_input(self):
        """A max_submissions value sent by the in-row input round-trips to the
        stored ContestProblem (blank → unlimited)."""
        response = self.client.post(
            reverse('contest_edit', args=['probeditcontest']),
            data=json.dumps(self._full_payload(problems=[
                {'id': self.problem.id, 'points': 100, 'max_submissions': 5},
            ])),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.contest_problems.get().max_submissions, 5)
        # Blank means unlimited.
        response = self.client.post(
            reverse('contest_edit', args=['probeditcontest']),
            data=json.dumps(self._full_payload(problems=[
                {'id': self.problem.id, 'points': 100, 'max_submissions': ''},
            ])),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertIsNone(self.contest.contest_problems.get().max_submissions)


class ContestEditorNewEndpointsTestCase(CommonDataMixin, TestCase):
    """New editor endpoints: tag create, .seb upload, finalize/unfinalize."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='endpoints_editor', user_permissions=(
            'edit_all_contest', 'contest_rating', 'lock_contest', 'contest_access_code',
            'contest_seb_requirement', 'create_private_contest', 'change_contest_visibility',
            'contest_problem_label', 'override_performance_ceiling',
        ))
        cls.curator = create_user(username='endpoints_curator', user_permissions=('edit_own_contest',))
        cls.normal = create_user(username='endpoints_normal')
        _now = timezone.now()
        cls.contest = create_contest(
            key='endpointcontest', name='Endpoint',
            start_time=_now + timedelta(days=1), end_time=_now + timedelta(days=2),
            authors=('superuser',),
            curators=('endpoints_curator',),
        )

    def setUp(self):
        self.client = Client()

    # ── tag create ──────────────────────────────────────────────────────────

    def test_tag_create_success(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_tag_create'),
            data=json.dumps({'name': 'midterm'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertEqual(data['tag']['name'], 'midterm')
        self.assertTrue(data['tag']['id'])
        self.assertTrue(data['tag']['color'].startswith('#'))
        self.assertTrue(ContestTag.objects.filter(name='midterm').exists())

    def test_tag_create_rejects_bad_name(self):
        self.client.force_login(self.editor)
        for bad in ('Midterm', 'mid term', 'midterm!', '中term'):
            response = self.client.post(
                reverse('contest_tag_create'),
                data=json.dumps({'name': bad}), content_type='application/json',
            )
            self.assertEqual(response.status_code, 400, bad)

    def test_tag_create_rejects_too_long(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_tag_create'),
            data=json.dumps({'name': 'a' * 21}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_tag_create_rejects_duplicate(self):
        ContestTag.objects.create(name='dup-tag', color='#ff0000')
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_tag_create'),
            data=json.dumps({'name': 'dup-tag'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('already exists', json.loads(response.content)['error'])

    def test_tag_create_denied_for_normal_user(self):
        self.client.force_login(self.normal)
        response = self.client.post(
            reverse('contest_tag_create'),
            data=json.dumps({'name': 'sneaky'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_tag_create_allowed_for_curator(self):
        # edit_own_contest holders may open the editor, so they may add tags.
        self.client.force_login(self.curator)
        response = self.client.post(
            reverse('contest_tag_create'),
            data=json.dumps({'name': 'curator-tag'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)

    # ── .seb upload ─────────────────────────────────────────────────────────

    def test_seb_upload_success(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_seb_upload', args=['endpointcontest']),
            data={'seb_config_file': SimpleUploadedFile('exam.seb', b'SEB config')},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)['ok'])
        self.contest.refresh_from_db()
        self.assertTrue(self.contest.seb_config_file)
        # MEDIA_ROOT persists across test runs, so Django's storage may suffix a
        # duplicate filename (exam_1KexZ2r.seb) — assert the basename loosely.
        basename = self.contest.seb_config_file.name.rsplit('/', 1)[-1]
        self.assertTrue(basename.startswith('exam') and basename.endswith('.seb'))

    def test_seb_upload_rejects_bad_extension(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_seb_upload', args=['endpointcontest']),
            data={'seb_config_file': SimpleUploadedFile('exam.txt', b'x')},
        )
        self.assertEqual(response.status_code, 400)

    def test_seb_upload_rejects_missing_file(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse('contest_seb_upload', args=['endpointcontest']),
            data={},
        )
        self.assertEqual(response.status_code, 400)

    def test_seb_upload_denied_for_curator_of_other_contest(self):
        # The curator curates endpointcontest — deny when the contest is not theirs.
        self.client.force_login(self.curator)
        _now = timezone.now()
        create_contest(
            key='othersebcontest', name='Other SEB',
            start_time=_now + timedelta(days=1), end_time=_now + timedelta(days=2),
            authors=('superuser',),
        )
        response = self.client.post(
            reverse('contest_seb_upload', args=['othersebcontest']),
            data={'seb_config_file': SimpleUploadedFile('exam.seb', b'x')},
        )
        self.assertEqual(response.status_code, 403)

    # ── finalize / unfinalize ───────────────────────────────────────────────

    def test_finalize_denied_for_curator(self):
        self.client.force_login(self.curator)
        response = self.client.post(reverse('contest_finalize', args=['endpointcontest']))
        self.assertEqual(response.status_code, 403)

    def test_finalize_denied_for_normal_user(self):
        self.client.force_login(self.normal)
        response = self.client.post(reverse('contest_finalize', args=['endpointcontest']))
        self.assertEqual(response.status_code, 403)

    def test_finalize_allowed_for_editor(self):
        self.client.force_login(self.editor)
        response = self.client.post(reverse('contest_finalize', args=['endpointcontest']))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)['ok'])

    def test_unfinalize_allowed_for_editor(self):
        self.client.force_login(self.editor)
        response = self.client.post(reverse('contest_unfinalize', args=['endpointcontest']))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)['ok'])


class ContestEditorActionFieldTestCase(CommonDataMixin, TestCase):
    """Save actions: save → contest page, continue → editor, another → no redirect."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='action_editor', user_permissions=('edit_all_contest',))

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)

    def _create_payload(self, key='actiontest'):
        now = timezone.now()
        return {
            'name': 'Action Test', 'key': key,
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time': (now + timedelta(hours=3)).isoformat(),
        }

    def test_action_save_redirects_to_contest_view(self):
        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps(self._create_payload()), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['action'], 'save')
        self.assertEqual(data['redirect'], reverse('contest_view', args=['actiontest']))

    def test_action_continue_redirects_to_editor(self):
        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps({**self._create_payload(), 'action': 'continue'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['action'], 'continue')
        self.assertEqual(data['redirect'], reverse('contest_edit', args=['actiontest']))

    def test_action_another_has_no_redirect(self):
        response = self.client.post(
            reverse('contest_create'),
            data=json.dumps({**self._create_payload(), 'action': 'another'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['action'], 'another')
        self.assertNotIn('redirect', data)
        # The contest still exists.
        self.assertTrue(Contest.objects.filter(key='actiontest').exists())


class ContestEditorProblemOptionsTestCase(CommonDataMixin, TestCase):
    """Per-problem output_prefix / hide_results round-trip."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='probopts_editor', user_permissions=('edit_all_contest',))
        cls.problem = create_problem('probopts-test', is_public=True)
        cls.contest = create_contest(
            key='proboptscontest', name='Prob Opts',
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=2),
            authors=('superuser',),
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)

    def _payload(self, **overrides):
        now = timezone.now()
        payload = {
            'name': 'Prob Opts', 'key': 'proboptscontest',
            'start_time': (now + timedelta(days=1)).isoformat(),
            'end_time': (now + timedelta(days=2)).isoformat(),
            'is_visible': False,
        }
        payload.update(overrides)
        return payload

    def test_create_saves_output_prefix_and_hide_results(self):
        response = self.client.post(
            reverse('contest_edit', args=['proboptscontest']),
            data=json.dumps(self._payload(problems=[
                {'id': self.problem.id, 'points': 100, 'output_prefix': 5, 'hide_results': True},
            ])), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        cp = self.contest.contest_problems.get()
        self.assertEqual(cp.output_prefix_override, 5)
        self.assertTrue(cp.hide_results)

    def test_hide_results_three_state(self):
        for raw, expected in ((None, None), ('default', None), ('hide', True), ('show', False), (False, False)):
            # Each POST fully replaces the problem list, so re-fetch after save.
            response = self.client.post(
                reverse('contest_edit', args=['proboptscontest']),
                data=json.dumps(self._payload(problems=[
                    {'id': self.problem.id, 'points': 100, 'hide_results': raw},
                ])), content_type='application/json',
            )
            self.assertEqual(response.status_code, 200, response.content)
            cp = self.contest.contest_problems.get()
            self.assertEqual(cp.hide_results, expected, raw)

    def test_prefill_includes_output_prefix_and_hide_results(self):
        from judge.models import ContestProblem
        ContestProblem.objects.create(
            contest=self.contest, problem=self.problem, points=100, order=1,
            output_prefix_override=7, hide_results=False,
        )
        response = self.client.get(reverse('contest_edit', args=['proboptscontest']))
        self.assertEqual(response.status_code, 200)
        problems = response.context['prefill']['problems']
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]['output_prefix'], 7)
        self.assertEqual(problems[0]['hide_results'], False)


class ContestDurationGuardTestCase(CommonDataMixin, TestCase):
    """"Only extend, never shorten" guard for duration / end_time.

    Once students have joined a contest, shrinking the personal time window
    or pulling the deadline earlier would retroactively end those students'
    participation mid-run (the middleware clears their current_contest on the
    next request). These tests lock in that the server refuses such edits.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='guard_editor', user_permissions=('edit_all_contest',))
        _now = timezone.now()
        cls.contest = create_contest(
            key='guardcontest', name='Guard',
            start_time=_now - timedelta(days=1),
            end_time=_now + timedelta(days=5),
            authors=('superuser',),
        )
        # The contest has a 2h personal time window.
        cls.contest.time_limit = timedelta(hours=2)
        cls.contest.save()

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.editor)

    def _payload(self, **overrides):
        now = timezone.now()
        payload = {
            'name': 'Guard', 'key': 'guardcontest',
            'start_time': (now - timedelta(days=1)).isoformat(),
            'end_time': (now + timedelta(days=5)).isoformat(),
            'duration_hours': 2, 'duration_minutes': 0,
            'is_visible': False,
        }
        payload.update(overrides)
        return payload

    def _add_participant(self):
        from judge.models import ContestParticipation
        return ContestParticipation.objects.create(
            contest=self.contest,
            user=create_user(username='guard_student_%s' % ContestParticipation.objects.count()).profile,
            real_start=timezone.now(),
        )

    # ── no participants yet → any edit allowed ────────────────────────────

    def test_no_participants_allows_shrinking(self):
        response = self.client.post(
            reverse('contest_edit', args=['guardcontest']),
            data=json.dumps(self._payload(duration_hours=1)), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.time_limit, timedelta(hours=1))

    # ── duration: only extend ─────────────────────────────────────────────

    def test_cannot_shrink_duration_with_participants(self):
        self._add_participant()
        response = self.client.post(
            reverse('contest_edit', args=['guardcontest']),
            data=json.dumps(self._payload(duration_hours=1)), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('only be increased', json.loads(response.content)['error'])
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.time_limit, timedelta(hours=2))  # unchanged

    def test_can_extend_duration_with_participants(self):
        self._add_participant()
        response = self.client.post(
            reverse('contest_edit', args=['guardcontest']),
            data=json.dumps(self._payload(duration_hours=3)), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.time_limit, timedelta(hours=3))

    def test_cannot_switch_unlimited_to_finite_with_participants(self):
        # A contest with no personal limit treats that as "already unlimited";
        # imposing a finite duration later is a shrink and must be refused.
        # Use a fresh contest so the shared setUpTestData object is untouched.
        _now = timezone.now()
        unlimited = create_contest(
            key='guardunlimited', name='Guard Unlimited',
            start_time=_now - timedelta(days=1),
            end_time=_now + timedelta(days=5),
            authors=('superuser',),
            time_limit=None,
        )
        from judge.models import ContestParticipation
        ContestParticipation.objects.create(
            contest=unlimited,
            user=create_user(username='guard_student_unlim').profile,
            real_start=timezone.now(),
        )
        response = self.client.post(
            reverse('contest_edit', args=['guardunlimited']),
            data=json.dumps({
                'name': 'Guard Unlimited', 'key': 'guardunlimited',
                'start_time': (_now - timedelta(days=1)).isoformat(),
                'end_time': (_now + timedelta(days=5)).isoformat(),
                'duration_hours': 2, 'duration_minutes': 0,
                'is_visible': False,
            }), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('unlimited', json.loads(response.content)['error'])

    def test_can_remove_finite_duration_with_participants(self):
        # Removing a personal limit always extends (window becomes "until the
        # contest ends"), so it stays allowed even with participants.
        self._add_participant()
        response = self.client.post(
            reverse('contest_edit', args=['guardcontest']),
            data=json.dumps(self._payload(duration_hours=0, duration_minutes=0)),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertIsNone(self.contest.time_limit)

    # ── end_time: only extend ─────────────────────────────────────────────

    def test_cannot_pull_end_time_earlier_with_participants(self):
        self._add_participant()
        now = timezone.now()
        earlier_end = now + timedelta(days=1)  # was now + 5 days
        response = self.client.post(
            reverse('contest_edit', args=['guardcontest']),
            data=json.dumps(self._payload(end_time=earlier_end.isoformat())),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('end time', json.loads(response.content)['error'])
        self.contest.refresh_from_db()
        self.assertGreater(self.contest.end_time, earlier_end)  # unchanged

    def test_can_push_end_time_later_with_participants(self):
        self._add_participant()
        now = timezone.now()
        later_end = now + timedelta(days=10)  # was now + 5 days
        response = self.client.post(
            reverse('contest_edit', args=['guardcontest']),
            data=json.dumps(self._payload(end_time=later_end.isoformat())),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.end_time, later_end)


class ContestTimeoutLogicTestCase(CommonDataMixin, TestCase):
    """Participation deadline computation and the middleware's kick-out rule.

    ``ContestParticipation.end_time`` decides when a student's window ends:
    min(real_start + time_limit, contest.end_time). Once ``ended`` is true the
    middleware clears ``profile.current_contest``, which removes the submit
    entry point (submissions no longer count toward the contest). These tests
    verify that computation — the gate that "expired students cannot submit".
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='timeout_editor', user_permissions=('edit_all_contest',))

    def _mk_contest(self, time_limit=None, end_delta_days=5):
        _now = timezone.now()
        contest = create_contest(
            key='timeout%s' % self._seq(),
            name='Timeout',
            start_time=_now - timedelta(days=1),
            end_time=_now + timedelta(days=end_delta_days),
            authors=('superuser',),
        )
        if time_limit is not None:
            contest.time_limit = time_limit
            contest.save()
        return contest

    _seq_counter = 0

    def _seq(self):
        ContestTimeoutLogicTestCase._seq_counter += 1
        return ContestTimeoutLogicTestCase._seq_counter

    def _mk_participation(self, contest, real_start=None):
        from judge.models import ContestParticipation
        return ContestParticipation.objects.create(
            contest=contest,
            user=create_user(username='timeout_student_%s' % self._seq()).profile,
            real_start=real_start or timezone.now(),
        )

    def test_no_limit_follows_contest_end(self):
        contest = self._mk_contest(time_limit=None)
        p = self._mk_participation(contest)
        self.assertEqual(p.end_time, contest.end_time)

    def test_duration_deadline_is_real_start_plus_limit(self):
        contest = self._mk_contest(time_limit=timedelta(hours=2))
        now = timezone.now()
        p = self._mk_participation(contest, real_start=now)
        self.assertEqual(p.end_time, min(now + timedelta(hours=2), contest.end_time))

    def test_duration_capped_by_contest_end(self):
        # A 10-day duration on a contest ending in 2 days must cap at contest end.
        contest = self._mk_contest(time_limit=timedelta(days=10), end_delta_days=2)
        now = timezone.now()
        p = self._mk_participation(contest, real_start=now)
        self.assertEqual(p.end_time, contest.end_time)

    def test_ended_true_after_deadline(self):
        contest = self._mk_contest(time_limit=timedelta(hours=1))
        started = timezone.now() - timedelta(hours=2)  # joined 2h ago, limit 1h
        p = self._mk_participation(contest, real_start=started)
        self.assertTrue(p.ended)

    def test_not_ended_before_deadline(self):
        contest = self._mk_contest(time_limit=timedelta(hours=3))
        started = timezone.now() - timedelta(hours=1)
        p = self._mk_participation(contest, real_start=started)
        self.assertFalse(p.ended)

    def test_extending_duration_rescues_participant(self):
        # The "only extend" rule is meaningful because extending the duration
        # after a student joined moves their deadline and un-expires them.
        contest = self._mk_contest(time_limit=timedelta(hours=1))
        started = timezone.now() - timedelta(hours=2)
        p = self._mk_participation(contest, real_start=started)
        self.assertTrue(p.ended)
        contest.time_limit = timedelta(hours=3)
        contest.save()
        # end_time/_now are cached_properties on the participation instance;
        # drop the cache so the extension is observed (mirrors a fresh request).
        try:
            del p.end_time
            del p._now
        except AttributeError:
            pass
        self.assertFalse(p.ended)

    def test_shrinking_duration_expires_participant(self):
        # And shrinking it retroactively expires someone mid-run — which is
        # exactly why the server refuses that edit once participants exist.
        contest = self._mk_contest(time_limit=timedelta(hours=3))
        started = timezone.now() - timedelta(hours=2)
        p = self._mk_participation(contest, real_start=started)
        self.assertFalse(p.ended)
        contest.time_limit = timedelta(hours=1)
        contest.save()
        # See above: cached_property on the participation must be dropped.
        try:
            del p.end_time
            del p._now
        except AttributeError:
            pass
        self.assertTrue(p.ended)

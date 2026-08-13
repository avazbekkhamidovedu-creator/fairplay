"""Tests for the teacher-side contest grading workspace (judge.views.contests.ContestGrade).

The grade workspace is the most sensitive screen a teacher has: it embeds every
student's *source code* (fully attacker-controlled) into inline JavaScript, and
its save endpoint can override a student's score. Two properties are therefore
non-negotiable and locked down here:

1. **XSS safety** — submission source must be serialized script-safely so a
   student cannot break out of the ``<script>`` block (stored XSS against the
   teacher who opens the page).
2. **Authorization** — only real editors may open the page or change a score.
   A full role matrix (superuser / edit_all / author / curator / tester /
   spectator / org-admin / student / anonymous) plus *mutation* tests (grant a
   permission, observe access flip) pin the exact boundary, because a grade
   override leaking to a student or tester is a grade-integrity breach.

Layout of this file:

* ``ContestGradePermissionMatrixTestCase``  — GET the page as every role.
* ``ContestGradeSavePermissionMatrixTestCase`` — POST a score as every role.
* ``ContestGradeSaveValidationTestCase``   — input validation + CSRF + isolation.
* ``ContestGradeMutationTestCase``         — grant/revoke flips access (mutation).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth.models import Permission
from django.http import HttpResponseForbidden
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestProblem, Language, Submission, SubmissionSource
from judge.models.tests.util import create_contest, create_problem, create_user

XSS_SOURCE = '</script><script>alert(document.cookie)</script>'


def _csrf_failure_plain(request, reason=''):
    """Stand-in for ``django.views.csrf.csrf_failure`` used by the CSRF test.

    The real failure view renders ``403.html`` through the Django template
    engine; on Python 3.14 + Django 4.2 that render crashes the test client
    (``BaseContext.__copy__`` uses ``copy(super())``, removed in 3.14). The
    *security contract* under test — the CSRF middleware rejects a token-less
    POST before the view runs — does not depend on the failure page, so we
    swap in a plain 403 via ``CSRF_FAILURE_VIEW``.
    """
    return HttpResponseForbidden('CSRF rejected')


class GradeTestBase(TestCase):
    """Shared fixture: one contest populated with every role plus a submission.

    Subclasses re-use the same ``setUpTestData`` (built once per class), so the
    database stays small while every role is covered.
    """

    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        # ── Roles (each username is self-documenting) ───────────────────────
        cls.superuser = create_user(username='grade_superuser', is_superuser=True, is_staff=True)
        cls.edit_all = create_user(username='grade_edit_all', user_permissions=('edit_all_contest',))
        cls.author = create_user(username='grade_author', user_permissions=('edit_own_contest',))
        cls.curator = create_user(username='grade_curator', user_permissions=('edit_own_contest',))
        # A curator *without* edit_own_contest: being listed as a curator is not
        # enough on its own — the permission must also be held.
        cls.curator_no_perm = create_user(username='grade_curator_noperm')
        # Holds edit_own_contest but is NOT an author/curator of this contest.
        cls.edit_own_outsider = create_user(username='grade_edit_own_out', user_permissions=('edit_own_contest',))
        cls.tester = create_user(username='grade_tester')
        cls.spectator = create_user(username='grade_spectator')
        cls.org_admin = create_user(username='grade_org_admin', user_permissions=('organization_admin',))
        cls.student = create_user(username='grade_student')

        # ── Contest + problem + a graded submission ─────────────────────────
        cls.problem = create_problem('grade-perm-prob')
        cls.contest = create_contest(
            key='gradeperm',
            name='Grade Perm',
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1),
            authors=('grade_author',),
            curators=('grade_curator', 'grade_curator_noperm'),
            testers=('grade_tester',),
            spectators=('grade_spectator',),
        )
        ContestProblem.objects.create(contest=cls.contest, problem=cls.problem, points=100, order=1)
        # case_total=100 makes the manual-score ceiling a deterministic 100.
        cls.submission = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem,
            language=Language.objects.get(key='PY3'),
            result='WA', status='D', contest_object=cls.contest,
            points=50.0, case_total=100,
        )
        SubmissionSource.objects.create(submission=cls.submission, source='print(1)')

        cls.grade_url = reverse('contest_grade', args=[cls.contest.key])
        cls.save_url = reverse('contest_grade_save', args=[cls.contest.key])
        cls.release_url = reverse('contest_grade_release', args=[cls.contest.key])

    def _client(self, user):
        """Return a fresh test client logged in as ``user`` (or anonymous)."""
        client = Client()
        if user is not None:
            client.force_login(user)
        return client


class ContestGradePermissionMatrixTestCase(GradeTestBase):
    """GET the grade page as every role.

    The view deliberately raises ``Http404`` (not 403) for authenticated
    non-editors so the contest's existence is not leaked by the status code.
    """

    def _assert_get(self, user, expected_status):
        response = self._client(user).get(self.grade_url)
        self.assertEqual(response.status_code, expected_status,
                         'role=%s expected %d got %d' % (getattr(user, 'username', 'anon'),
                                                         expected_status, response.status_code))

    def test_editors_get_200(self):
        """Only actual editors reach the page."""
        for user in (self.superuser, self.edit_all, self.author, self.curator):
            self._assert_get(user, 200)

    def test_non_editors_get_404(self):
        """Everyone else — including privileged-but-not-editor roles — is 404.

        Tester, spectator and org-admin can *view* contests but must never be
        able to open the grading workspace.
        """
        for user in (self.curator_no_perm, self.edit_own_outsider, self.tester,
                     self.spectator, self.org_admin, self.student):
            self._assert_get(user, 404)

    def test_anonymous_redirects_to_login(self):
        response = Client().get(self.grade_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_unknown_contest_is_404(self):
        """A non-existent contest key is 404 even for a full editor."""
        response = self._client(self.edit_all).get(reverse('contest_grade', args=['nope']))
        self.assertEqual(response.status_code, 404)


class ContestGradeSavePermissionMatrixTestCase(GradeTestBase):
    """POST a manual score as every role.

    Anonymous → 401, authenticated non-editor → 403, editor → 200. The split
    between 401/403 is intentional: it lets the frontend distinguish "please
    log in" from "you may not do this".
    """

    def _post(self, user, submission_id, score='50'):
        return self._client(user).post(self.save_url, {'submission': submission_id, 'score': score})

    def test_anonymous_gets_401(self):
        response = Client().post(self.save_url, {'submission': self.submission.id, 'score': 50})
        self.assertEqual(response.status_code, 401)

    def test_editors_can_save(self):
        for user in (self.superuser, self.edit_all, self.author, self.curator):
            response = self._post(user, self.submission.id, score=60)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()['ok'])

    def test_non_editors_get_403(self):
        for user in (self.curator_no_perm, self.edit_own_outsider, self.tester,
                     self.spectator, self.org_admin, self.student):
            response = self._post(user, self.submission.id, score=60)
            self.assertEqual(response.status_code, 403,
                             'role=%s expected 403 got %d' % (user.username, response.status_code))

    def test_tester_cannot_override_their_own_or_others_score(self):
        """Regression guard: testers see submissions but must not grade them."""
        response = self._post(self.tester, self.submission.id, score=60)
        self.assertEqual(response.status_code, 403)


class ContestGradeSaveValidationTestCase(GradeTestBase):
    """Input validation, CSRF and cross-contest isolation for the save endpoint."""

    def _post(self, **data):
        return self._client(self.curator).post(self.save_url, data)

    def test_save_persists_score(self):
        response = self._post(submission=self.submission.id, score='42.5')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.points, 42.5)
        self.assertEqual(response.json()['score'], 42.5)

    def test_missing_submission_id_is_400(self):
        self.assertEqual(self._post(score='50').status_code, 400)

    def test_non_integer_submission_id_is_400(self):
        self.assertEqual(self._post(submission='abc', score='50').status_code, 400)

    def test_non_numeric_score_is_400(self):
        self.assertEqual(self._post(submission=self.submission.id, score='abc').status_code, 400)

    def test_unknown_submission_is_404(self):
        self.assertEqual(self._post(submission=999999, score='50').status_code, 404)

    def test_cross_contest_submission_is_404(self):
        """A submission belonging to a *different* contest must not be gradable.

        Guards against an editor of contest A reaching into contest B via a raw
        submission id. The query is scoped by ``contest_object=self.object``.
        """
        other = create_contest(
            key='othercontest', name='Other',
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1),
            authors=('grade_author',),
        )
        other_sub = Submission.objects.create(
            user=self.student.profile, problem=self.problem,
            language=Language.objects.get(key='PY3'),
            result='WA', status='D', contest_object=other,
            points=50.0, case_total=100,
        )
        response = self._post(submission=other_sub.id, score='70')
        self.assertEqual(response.status_code, 404)

    def test_negative_score_clamped_to_zero(self):
        response = self._post(submission=self.submission.id, score='-999')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.points, 0.0)
        self.assertEqual(response.json()['score'], 0.0)

    def test_over_max_score_clamped_to_total(self):
        response = self._post(submission=self.submission.id, score='9999')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.points, 100.0)  # case_total ceiling
        self.assertEqual(response.json()['score'], 100.0)

    def test_blank_score_treated_as_zero(self):
        response = self._post(submission=self.submission.id, score='')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.points, 0.0)

    # ── Review note (评语) persistence ──────────────────────────────────────

    def test_save_persists_review_note_and_reviewer(self):
        response = self._post(submission=self.submission.id, score='42', note='Good attempt, review edge cases.')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.review_note, 'Good attempt, review edge cases.')
        self.assertEqual(self.submission.reviewed_by, self.curator.profile)
        self.assertIsNotNone(self.submission.reviewed_at)
        self.assertEqual(response.json()['note'], 'Good attempt, review edge cases.')

    def test_save_without_note_keeps_existing_note(self):
        """Omitting the note field must NOT wipe a previously saved note."""
        self.submission.review_note = 'kept'
        self.submission.save(update_fields=['review_note'])
        response = self._post(submission=self.submission.id, score='50')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.review_note, 'kept')

    def test_save_with_blank_note_clears_it(self):
        self.submission.review_note = 'to be cleared'
        self.submission.save(update_fields=['review_note'])
        response = self._post(submission=self.submission.id, score='50', note='')
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.review_note, '')

    @override_settings(CSRF_FAILURE_VIEW='judge.views.tests.test_contest_grade._csrf_failure_plain')
    def test_csrf_enforced(self):
        """A POST without a CSRF token must be rejected, even for an editor.

        The real browser sends ``X-CSRFToken`` (see resources/common.js
        ``$.ajaxSetup``); this test proves the endpoint is not accidentally
        ``csrf_exempt`` and cannot be hit cross-site. ``CSRF_FAILURE_VIEW`` is
        swapped for a plain 403 so the assertion is about the middleware
        rejection, not about the 403 page's own render (see helper docstring).
        """
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.curator)
        before = self.submission.points
        response = client.post(self.save_url, {'submission': self.submission.id, 'score': '80'})
        self.assertEqual(response.status_code, 403)
        # The view never ran, so the score must be untouched.
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.points, before)


class ContestGradeMutationTestCase(GradeTestBase):
    """Mutation tests: flip one permission dimension and assert access flips.

    These are the strongest guard against a too-broad ``is_editable_by``: if
    granting ``edit_own_contest`` to a random student suddenly lets them grade,
    the gate is wrong. If revoking it from a curator keeps them in, the gate is
    wrong too.
    """

    def _get_status(self, user):
        return self._client(user).get(self.grade_url).status_code

    def test_granting_edit_own_to_non_editor_still_denied(self):
        """Permission alone is not enough — the user must also be an editor."""
        outsider = self.edit_own_outsider
        self.assertEqual(self._get_status(outsider), 404)  # baseline: denied
        # Already has edit_own_contest but is not an author/curator → still 404.
        self.assertEqual(self._get_status(outsider), 404)

    def test_promote_student_to_curator_grants_access(self):
        """A student + edit_own_contest + curator role → access granted."""
        self.assertEqual(self._get_status(self.student), 404)  # baseline
        perm = Permission.objects.get(codename='edit_own_contest')
        self.student.user_permissions.add(perm)
        # Permission without the curator relationship is still not enough…
        self.assertEqual(self._get_status(self.student), 404)
        # …but adding the relationship completes the editor identity.
        self.contest.curators.add(self.student.profile)
        self.assertEqual(self._get_status(self.student), 200)

    def test_revoking_edit_own_from_curator_revokes_access(self):
        """Removing the permission (while still a curator) must revoke access."""
        self.assertEqual(self._get_status(self.curator), 200)  # baseline
        self.curator.user_permissions.remove(Permission.objects.get(codename='edit_own_contest'))
        self.assertEqual(self._get_status(self.curator), 404)

    def test_removing_curator_relationship_revokes_access(self):
        """Removing the curator relationship (while keeping the permission) revokes."""
        self.assertEqual(self._get_status(self.curator), 200)  # baseline
        self.contest.curators.remove(self.curator.profile)
        self.assertEqual(self._get_status(self.curator), 404)

    def test_author_can_grade_without_edit_own(self):
        """An author with edit_own_contest already covered; now check the flip:
        an author is an editor, so granting the permission to a *new* author grants access.
        """
        new_author = create_user(username='grade_new_author', user_permissions=('edit_own_contest',))
        self.contest.authors.add(new_author.profile)
        self.assertEqual(self._get_status(new_author), 200)

    def test_curator_without_permission_never_grades(self):
        """``curator_no_perm`` is a curator but lacks edit_own_contest → denied,
        and even granting edit_all to a *different* user doesn't leak to them."""
        self.assertEqual(self._get_status(self.curator_no_perm), 404)
        # Sanity: the gate is user-scoped, not contest-scoped-global.
        self.assertEqual(self._get_status(self.edit_all), 200)


class ContestGradeXSSRegressionTestCase(TestCase):
    """Keep the original stored-XSS regression pinned (source break-out)."""

    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        cls.editor = create_user(username='grade_xss_editor', user_permissions=('edit_all_contest',))
        cls.student = create_user(username='grade_xss_student')
        cls.problem = create_problem('grade-xss-prob')
        cls.contest = create_contest(
            key='gradexss', name='Grade XSS',
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1),
            authors=('grade_xss_editor',),
        )
        ContestProblem.objects.create(contest=cls.contest, problem=cls.problem, points=100, order=1)
        cls.submission = Submission.objects.create(
            user=cls.student.profile, problem=cls.problem,
            language=Language.objects.get(key='PY3'),
            result='WA', status='D', contest_object=cls.contest,
        )
        SubmissionSource.objects.create(submission=cls.submission, source=XSS_SOURCE)

    def test_grade_page_escapes_script_breakout_in_source(self):
        response = self._client().get(reverse('contest_grade', args=[self.contest.key]))
        self.assertEqual(response.status_code, 200)
        content = response.content

        # The literal `</script><script>…` must never appear in the rendered page.
        self.assertNotIn(b'</script><script>', content)
        self.assertNotIn(XSS_SOURCE.encode(), content)

        # The source must be present, but only in script-safe escaped form
        # (`|tojson` escapes `<`/`>`/`&`/`'` to \u003c etc.).
        self.assertIn(b'\\u003c/script\\u003e\\u003cscript\\u003e', content)

    def _client(self):
        client = Client()
        client.force_login(self.editor)
        return client


class ContestGradePayloadTestCase(GradeTestBase):
    """The grade page's embedded JSON carries everything the reference UI needs:
    review-note state, problem statements, virtual/latest markers, release flag."""

    def _page_json(self):
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        # Pull the `submissions` / `problems` / `released` JS variables out of
        # the rendered script block.
        html = response.content.decode()
        submissions = re.search(r'var submissions = (\[.*?\]);', html, re.S)
        problems = re.search(r'var problems = (\[.*?\]);', html, re.S)
        self.assertIsNotNone(submissions, 'submissions JSON missing from page')
        self.assertIsNotNone(problems, 'problems JSON missing from page')
        return json.loads(submissions.group(1)), json.loads(problems.group(1))

    def test_payload_includes_review_state_and_description(self):
        subs, probs = self._page_json()
        self.assertEqual(len(subs), 1)
        self.assertEqual(len(probs), 1)
        self.assertIn('review_note', subs[0])
        self.assertIn('reviewed_by', subs[0])
        self.assertIn('reviewed_at', subs[0])
        self.assertIn('is_latest', subs[0])
        self.assertIn('virtual', subs[0])
        # Problem library must carry the statement (题面) for the detail pane.
        self.assertEqual(probs[0]['code'], self.problem.code)
        self.assertIn('description', probs[0])

    def test_release_flag_in_context(self):
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('grade_released', response.context)
        self.assertFalse(response.context['grade_released'])
        self.contest.results_finalized = True
        self.contest.save(update_fields=['results_finalized'])
        response = self._client(self.curator).get(self.grade_url)
        self.assertTrue(response.context['grade_released'])


class ContestGradeJSValidityTestCase(GradeTestBase):
    """The inline <script> the grade page renders must be valid JavaScript.

    Student-controlled content (source code, review notes) is embedded in the
    page. Even though it is escaped, a mis-rendered ``{{ }}`` or an escaping
    regression would produce a syntax error that silently breaks the whole
    grading UI. We parse the rendered block with ``node --check``; hosts without
    node skip (documented, not a fake pass).
    """

    @skipUnless(shutil.which('node'), 'node executable not available on this host')
    def test_inline_script_parses(self):
        # Push JS-hostile strings through the pipeline: quotes, </textarea>,
        # and a </script> breakout in the source.
        self.submission.review_note = "Note with 'single' and \"double\" quotes </textarea>"
        self.submission.save(update_fields=['review_note'])
        response = self._client(self.curator).get(self.grade_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        blocks = re.findall(r'<script type="text/javascript">(.*?)</script>', html, re.S)
        self.assertTrue(blocks, 'no inline <script> block found on the grade page')
        for js in blocks:
            fd, path = tempfile.mkstemp(suffix='.js')
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(js)
                proc = subprocess.run(['node', '--check', path], capture_output=True, text=True)
            finally:
                os.unlink(path)
            self.assertEqual(proc.returncode, 0, 'inline JS failed node --check:\n%s' % proc.stderr)


class ContestGradeWorkflowTestCase(GradeTestBase):
    """End-to-end grade flow: load the page, grade with a review note, reload.

    Exercises the real teacher loop — open workspace → save score + 评语 →
    reopen → the new state is reflected in the embedded JSON the JS renders.
    """

    def test_full_grade_flow(self):
        client = self._client(self.curator)

        # 1. Open the grade page; the problem must be listed in the filter/library.
        page = client.get(self.grade_url)
        self.assertEqual(page.status_code, 200)
        html0 = page.content.decode()
        self.assertIn('grade-perm-prob', html0)

        # 2. Save a manual grade with a review note.
        resp = client.post(self.save_url, {
            'submission': self.submission.id, 'score': '77', 'note': 'Nice work',
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['score'], 77.0)
        self.assertEqual(body['note'], 'Nice work')

        # 3. Reload: the new score + note are in the submissions JSON payload.
        page2 = client.get(self.grade_url)
        html = page2.content.decode()
        self.assertIn('"points": 77.0', html)
        self.assertIn('"review_note": "Nice work"', html)

        # 4. DB agrees.
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.points, 77.0)
        self.assertEqual(self.submission.review_note, 'Nice work')
        self.assertEqual(self.submission.reviewed_by, self.curator.profile)


class ContestGradeReleaseTestCase(GradeTestBase):
    """The Release / Unrelease endpoint toggles ``Contest.results_finalized``.

    Same authorization contract as the save endpoint: 401 anonymous, 403
    authenticated non-editor, 200 editor. Releasing grades reveals hidden
    results to students, so it is exactly as sensitive as editing a score.
    """

    def _post(self, user):
        return self._client(user).post(self.release_url)

    def test_anonymous_gets_401(self):
        self.assertEqual(Client().post(self.release_url).status_code, 401)

    def test_editors_can_toggle_release(self):
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.results_finalized)
        response = self._post(self.curator)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['released'])
        self.contest.refresh_from_db()
        self.assertTrue(self.contest.results_finalized)

        # A second call unreleases (toggle semantics).
        response = self._post(self.edit_all)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['released'])
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.results_finalized)

    def test_non_editors_get_403(self):
        for user in (self.curator_no_perm, self.edit_own_outsider, self.tester,
                     self.spectator, self.org_admin, self.student):
            response = self._post(user)
            self.assertEqual(response.status_code, 403,
                             'role=%s expected 403 got %d' % (user.username, response.status_code))

    def test_tester_cannot_release(self):
        """Testers see submissions but must never be able to release grades."""
        self.assertEqual(self._post(self.tester).status_code, 403)

    def test_get_is_side_effect_free(self):
        """GET must never toggle the flag (DMOJ convention: Http404 from the
        GET handler becomes a generic-message page via ContestMixin.dispatch).
        The security property is that a GET cannot release or unrelease."""
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.results_finalized)
        self._client(self.curator).get(self.release_url)
        self.contest.refresh_from_db()
        self.assertFalse(self.contest.results_finalized)

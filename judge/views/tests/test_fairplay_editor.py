"""FairPlay in the assignment editor and admin, and the assignment-page box."""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils import timezone

from judge.models import Contest, ContestTag
from judge.models.tests.util import create_user
from judge.utils.fairplay import FAIRPLAY_TAG, fairplay_clean_admin_form, is_fairplay_exam
from judge.utils.fairplay_client import FairPlayUnreachable
from judge.utils.fairplay_delivery import deliver_submission
from judge.utils.fairplay_package import iso
from judge.views.tests.fairplay_base import FAIRPLAY_OFF, FAIRPLAY_ON, FairPlayDataMixin, grant, set_fairplay

FP_SETTINGS = {
    'calculator': 'graphing', 'open_book': True, 'websites': ['docs.python.org', 'duke.app.box.com'],
    'website_links': [{'host': 'duke.app.box.com', 'url': 'https://duke.app.box.com/folder/0', 'title': 'Box'}],
    'websites_blocked': [{'site': 'chatgpt.com', 'reason': 'AI assistant'}], 'upload_opens_days': 2,
    'strong_lock': True, 'resume_needs_approval': False, 'review': 'breakdown', 'companions': [],
    'set_in_fairplay': [],
}
EDITOR_SETTINGS = {
    'calculator': 'scientific', 'open_book': True,
    'websites': ['Docs.Python.org', 'Course https://duke.app.box.com/f/1'],
    'strong_lock': True, 'resume_needs_approval': False, 'upload_opens_days': 1, 'review': 'answers',
}


class FairPlayEditorMixin(FairPlayDataMixin):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.teacher)
        patcher = mock.patch('judge.utils.fairplay_client.FairPlayClient')
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.fp = self.client_class.return_value
        self.fp.exam.return_value = {'synced': True, 'status': 'draft', 'settings': dict(FP_SETTINGS)}
        self.fp.set_settings.return_value = {'synced': True, 'status': 'draft',
                                             'settings': dict(FP_SETTINGS, websites_blocked=[])}

    def payload(self, **overrides):
        exam = self.reload_exam()
        payload = {
            'name': exam.name, 'key': exam.key,
            'start_time': exam.start_time.isoformat(), 'end_time': exam.end_time.isoformat(),
            'duration_hours': 1, 'duration_minutes': 30, 'description': '',
            'vis_mode': 'org', 'format_name': 'exam', 'scoreboard_visibility': 'H', 'format_config': '',
            'use_clarifications': True, 'allow_virtual_participation': False,
            'seb_required': False, 'seb_browser_exam_keys': '', 'seb_config_keys': '', 'seb_quit_url': '',
            'organizations': [self.org.id], 'classes': [self.klass.id], 'tags': [],
            'fairplay_required': True,
        }
        payload.update(overrides)
        return {k: v for k, v in payload.items() if v is not Ellipsis}

    def before_the_window(self):
        """Move the exam to three days from now, before the editor lock."""
        start = timezone.now() + timedelta(days=3)
        Contest.objects.filter(pk=self.exam.pk).update(start_time=start, end_time=start + timedelta(hours=2))

    def save(self, url_key='fpexam', **overrides):
        return self.client.post(reverse('contest_edit', args=[url_key]), json.dumps(self.payload(**overrides)),
                                content_type='application/json')


@FAIRPLAY_ON
class FairPlayEditorTestCase(FairPlayEditorMixin, TestCase):
    def test_only_the_lock_choice(self):
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertIn('name="exam_lock" value="none"', html)
        self.assertIn('id="id_seb_required"', html)
        self.assertIn('id="id_fairplay_required" checked', html)
        for gone in ('fairplay-panel', 'fairplay_calculator', 'All optional', 'FairPlay holds this exam',
                     'also used elsewhere', 'not reachable', 'fairplay-edit-locked', 'fairplay-shared-warning'):
            self.assertNotIn(gone, html)
        self.fp.exam.assert_not_called()

    def test_no_standing_text_when_fairplay_is_down(self):
        self.fp.exam.side_effect = FairPlayUnreachable()
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('not reachable', html)
        self.assertEqual(self.save().status_code, 200)
        self.fp.set_settings.assert_not_called()
        self.assertTrue(is_fairplay_exam(self.reload_exam()))

    def test_not_a_fairplay_exam_asks_nothing(self):
        set_fairplay(self.exam, False)
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('id="fairplay-panel"', html)
        self.fp.exam.assert_not_called()

    def test_save_sends_changed_settings(self):
        response = self.save(fairplay_settings=EDITOR_SETTINGS)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('fairplay_warning', response.json())
        self.fp.set_settings.assert_called_once_with('fpexam', {
            'calculator': 'scientific', 'open_book': True, 'strong_lock': True, 'resume_needs_approval': False,
            'websites': ['Docs.Python.org', 'Course https://duke.app.box.com/f/1'], 'upload_opens_days': 1,
            'review': 'answers'}, read_timeout=3)
        self.assertTrue(is_fairplay_exam(self.reload_exam()))

    def test_unchanged_settings_not_sent(self):
        self.assertEqual(self.save().status_code, 200)
        self.fp.set_settings.assert_not_called()

    def test_settings_refused_by_fairplay_say_nothing(self):
        self.fp.set_settings.side_effect = FairPlayUnreachable()
        response = self.save(fairplay_settings=EDITOR_SETTINGS)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertNotIn('fairplay_warning', data)
        self.assertNotEqual(data['redirect'], reverse('contest_fairplay', args=['fpexam']))

    def test_new_key_takes_the_settings_along(self):
        self.before_the_window()
        response = self.save(key='fpexam2')
        self.assertEqual(response.status_code, 200, response.content)
        self.fp.exam.assert_called_with('fpexam', read_timeout=3)
        args = self.fp.set_settings.call_args.args
        self.assertEqual(args[0], 'fpexam2')
        self.assertEqual(args[1]['calculator'], 'graphing')
        self.assertEqual(args[1]['websites'], ['Box https://duke.app.box.com/folder/0', 'docs.python.org'])

    def test_switch_off_and_on(self):
        self.before_the_window()
        self.assertEqual(self.save(fairplay_required=False).status_code, 200)
        self.assertFalse(is_fairplay_exam(self.reload_exam()))
        self.assertEqual(self.save().status_code, 200)
        self.assertTrue(is_fairplay_exam(self.reload_exam()))
        self.assertEqual(ContestTag.objects.filter(name=FAIRPLAY_TAG).count(), 1)

    def test_tag_picker_never_sets_or_clears_the_lock(self):
        self.before_the_window()
        ContestTag.objects.create(name='exam', color='#123456')
        tag = ContestTag.objects.get(name=FAIRPLAY_TAG)
        exam_tag = ContestTag.objects.get(name='exam')
        # The picker's tags replace the others but leave the FairPlay tag as the lock option says.
        self.assertEqual(self.save(tags=[exam_tag.id], fairplay_required=Ellipsis).status_code, 200)
        exam = self.reload_exam()
        self.assertEqual(sorted(exam.tags.values_list('name', flat=True)), ['exam', FAIRPLAY_TAG])
        self.assertEqual(self.save(tags=[exam_tag.id, tag.id], fairplay_required=False).status_code, 200)
        self.assertFalse(is_fairplay_exam(self.reload_exam()))
        # Not offered in the picker, and not creatable from it.
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('>%s</option>' % FAIRPLAY_TAG, html)
        self.assertIn('>exam</option>', html)
        response = self.client.post(reverse('contest_tag_create'), json.dumps({'name': FAIRPLAY_TAG}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_omitted_lock_keeps_it(self):
        payload = self.payload()
        payload.pop('fairplay_required')
        response = self.client.post(reverse('contest_edit', args=['fpexam']), json.dumps(payload),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(is_fairplay_exam(self.reload_exam()))

    def test_validation(self):
        for overrides in ({'seb_required': True},
                          {'fairplay_settings': dict(EDITOR_SETTINGS, calculator='abacus')},
                          {'fairplay_settings': dict(EDITOR_SETTINGS, review='everything')},
                          {'fairplay_settings': dict(EDITOR_SETTINGS, upload_opens_days=9)},
                          {'fairplay_settings': dict(EDITOR_SETTINGS, websites=['x'] * 101)},
                          {'vis_mode': 'public'}):
            with self.subTest(overrides=overrides):
                response = self.save(**overrides)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('error', response.json())
        self.assertFalse(self.reload_exam().seb_required)
        self.fp.set_settings.assert_not_called()

    def test_needs_a_student_list(self):
        # No course picked: nobody could take it, so it is refused and the stored course stays.
        for overrides in ({'organizations': [], 'classes': []},
                          {'vis_mode': 'private', 'private_contestants': []}):
            with self.subTest(overrides=overrides):
                response = self.save(**overrides)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('student list', response.json()['error'])
        self.assertTrue(self.reload_exam().organizations.exists())
        self.assertTrue(is_fairplay_exam(self.reload_exam()))
        # a class alone (with its course) is fine
        self.assertEqual(self.save().status_code, 200)

    def test_needs_seb_permission(self):
        curator = create_user(username='fp_curator', user_permissions=('edit_own_contest',))
        self.exam.curators.add(curator.profile)
        set_fairplay(self.exam, False)
        client = Client()
        client.force_login(curator)
        html = client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('id="fairplay-panel"', html)
        response = client.post(reverse('contest_edit', args=['fpexam']),
                               json.dumps(self.payload(fairplay_settings=EDITOR_SETTINGS,
                                                       tags=[ContestTag.objects.get(name=FAIRPLAY_TAG).id])),
                               content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(is_fairplay_exam(self.reload_exam()))
        self.fp.set_settings.assert_not_called()

    def test_create_page_offers_fairplay(self):
        html = self.client.get(reverse('contest_create')).content.decode()
        self.assertIn('id="id_fairplay_required"', html)
        self.assertIn('name="exam_lock" value="none" id="id_lock_none" checked', html)
        self.fp.exam.assert_not_called()

    def test_create_with_settings(self):
        payload = self.payload(key='fpnew1', name='New exam', fairplay_settings=EDITOR_SETTINGS)
        response = self.client.post(reverse('contest_create'), json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        contest = Contest.objects.get(key='fpnew1')
        self.assertTrue(is_fairplay_exam(contest))
        self.assertEqual(self.fp.set_settings.call_args.args[0], 'fpnew1')


@FAIRPLAY_ON
class FairPlayEditorLockTestCase(FairPlayEditorMixin, TestCase):
    """Key, lock and problems are locked from 24 hours before the start until the end + 14 days."""

    def problems(self, *problems):
        return [{'id': p.id, 'points': p.points} for p in problems]

    def current_problems(self):
        return list(self.reload_exam().contest_problems.order_by('order').values_list('problem_id', flat=True))

    def all_problems(self):
        return (self.mcq, self.blank, self.short, self.file, self.code)

    def test_locked_changes_refused(self):
        before = self.current_problems()
        for label, overrides, text in (
                ('key', {'key': 'fpexam2'}, "The assignment ID can't be changed now."),
                ('lock off', {'fairplay_required': False}, "The lock can't be changed now."),
                ('problem removed', {'problems': self.problems(*self.all_problems()[:4])},
                 "The problems can't be changed now."),
                ('problem added', {'problems': self.problems(*self.all_problems(), self.extra)},
                 "The problems can't be changed now."),
                ('problem replaced', {'problems': self.problems(*self.all_problems()[:4], self.extra)},
                 "The problems can't be changed now.")):
            with self.subTest(label):
                response = self.save(**overrides)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()['error'], text)
                self.assertEqual(response.json().get('field'), 'exam_lock' if 'lock' in text else None)
                exam = self.reload_exam()
                self.assertEqual(exam.key, 'fpexam')
                self.assertTrue(is_fairplay_exam(exam))
                self.assertEqual(self.current_problems(), before)

    def test_other_changes_allowed(self):
        response = self.save(name='CS 101 Midterm (room 2)', problems=self.problems(*reversed(self.all_problems())))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.reload_exam().name, 'CS 101 Midterm (room 2)')

    def test_locked_until_the_delivery_window_is_over(self):
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(days=10),
                                                       end_time=now - timedelta(days=9))
        self.assertEqual(self.save(key='fpexam2').status_code, 400)
        Contest.objects.filter(pk=self.exam.pk).update(start_time=now - timedelta(days=16),
                                                       end_time=now - timedelta(days=15))
        self.assertEqual(self.save(key='fpexam2').status_code, 200)

    def test_not_locked_before_the_window(self):
        self.before_the_window()
        self.assertEqual(self.save(problems=self.problems(self.mcq, self.extra)).status_code, 200)
        self.assertEqual(self.current_problems(), [self.mcq.id, self.extra.id])

    def test_no_standing_lock_or_shared_text(self):
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('also used elsewhere', html)
        self.assertNotIn('FairPlay holds this exam', html)
        self.client.post(reverse('contest_edit', args=['fpexam']), json.dumps(self.payload()),
                         content_type='application/json')
        page = self.client.get(reverse('contest_view', args=['fpexam'])).content.decode()
        self.assertNotIn('also used elsewhere', page)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from judge.models.tests.util import create_problem
        cls.extra = create_problem('fpextra', name='Extra', submission_type='S', points=5, description='Extra.')
        cls.extra.authors.add(cls.teacher.profile)


@FAIRPLAY_ON
class FairPlayCloneAndTabTestCase(FairPlayDataMixin, TestCase):
    def test_clone_is_not_a_fairplay_exam(self):
        grant(self.teacher, 'clone_contest')
        client = Client()
        client.force_login(self.teacher)
        response = client.post(reverse('contest_clone', args=['fpexam']), {'key': 'fpclone'})
        self.assertIn(response.status_code, (200, 302))
        clone = Contest.objects.get(key='fpclone')
        self.assertFalse(is_fairplay_exam(clone))
        self.assertTrue(is_fairplay_exam(self.reload_exam()))

    def test_no_tab_for_anyone(self):
        for user, shown in ((self.teacher, False), (self.tester, False), (self.classmate, False)):
            client = Client()
            client.force_login(user)
            with self.subTest(user=user.username):
                html = client.get(reverse('contest_view', args=['fpexam'])).content.decode()
                self.assertEqual(reverse('contest_fairplay', args=['fpexam']) in html, shown)


@FAIRPLAY_OFF
class FairPlayEditorBridgeOffTestCase(FairPlayEditorMixin, TestCase):
    def test_hidden_on_create(self):
        html = self.client.get(reverse('contest_create')).content.decode()
        self.assertNotIn('id="id_fairplay_required"', html)
        self.assertNotIn('id="fairplay-panel"', html)
        self.assertIn('id="id_seb_required"', html)

    def test_cannot_switch_on(self):
        set_fairplay(self.exam, False)
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertNotIn('id="id_fairplay_required"', html)
        self.assertEqual(self.save().status_code, 200)
        self.assertFalse(is_fairplay_exam(self.reload_exam()))

    def test_existing_fairplay_assignment_can_be_switched_off(self):
        self.before_the_window()
        html = self.client.get(reverse('contest_edit', args=['fpexam'])).content.decode()
        self.assertIn('id="id_fairplay_required" checked', html)
        self.fp.exam.assert_not_called()
        self.assertEqual(self.save(fairplay_required=False, fairplay_settings=EDITOR_SETTINGS).status_code, 200)
        self.assertFalse(is_fairplay_exam(self.reload_exam()))
        self.fp.set_settings.assert_not_called()


class FairPlayAdminTestCase(FairPlayDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = create_user(username='fp_admin', is_superuser=True, is_staff=True)

    def form(self, **data):
        from judge.admin.contest import ContestForm
        exam = self.reload_exam()
        form = ContestForm.__new__(ContestForm)     # the admin builds the bound form; the check needs these
        form.instance = exam
        cleaned = {'banned_users': exam.banned_users.all(), 'organizations': exam.organizations.all(),
                   'classes': exam.classes.all(), 'tags': exam.tags.all()}
        cleaned.update(data)
        form.cleaned_data = cleaned
        return form

    def test_no_fairplay_fields_in_admin(self):
        client = Client()
        client.force_login(self.admin)
        url = reverse('admin:judge_contest_change', args=[self.exam.pk])
        with FAIRPLAY_ON:
            html = client.get(url).content.decode()
        self.assertNotIn('name="fairplay_', html)
        self.assertIn('name="tags"', html)

    def test_tag_checked_like_the_editor(self):
        from django import forms
        form = self.form()
        fairplay_clean_admin_form(form, form.cleaned_data)       # course + class: fine
        for data, text in (({'seb_required': True}, 'not both'),
                           ({'is_organization_private': False, 'is_private': False}, 'student list'),
                           ({'organizations': Contest.objects.none(), 'classes': Contest.objects.none()},
                            'course or class')):
            with self.subTest(data=data):
                form = self.form(**data)
                with self.assertRaisesMessage(forms.ValidationError, text):
                    fairplay_clean_admin_form(form, form.cleaned_data)
        set_fairplay(self.exam, False)
        form = self.form(seb_required=True)
        fairplay_clean_admin_form(form, form.cleaned_data)       # not a FairPlay exam: not checked


@FAIRPLAY_ON
class FairPlayBoxTestCase(FairPlayDataMixin, TestCase):
    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_no_box_on_the_assignment_page(self):
        now = timezone.now()
        response = deliver_submission(self.reload_exam(), {
            'username': 'fp_student', 'attempt_id': 'a1', 'handed_in_evidence': 'server',
            'handed_in_at': iso(now), 'answers': [{'question_id': 'p%06d' % self.short.id,
                                                   'short_answer': {'text': 'x'}}],
            'integrity_report_url': 'https://exam.example.edu/teacher/exams/1/attempts/7'})
        self.assertTrue(response['receipt'])
        html = self.client_for(self.teacher).get(reverse('contest_view', args=['fpexam'])).content.decode()
        self.assertNotIn('fairplay-box', html)
        self.assertNotIn(reverse('contest_fairplay', args=['fpexam']), html)
        self.assertNotIn('FairPlay report', html)

    def test_live_room_link_is_retired(self):
        # No signed live-room link, no route.
        html = self.client_for(self.teacher).get(reverse('contest_view', args=['fpexam'])).content.decode()
        self.assertNotIn('fairplay-live-btn', html)
        self.assertNotIn('Open FairPlay live room', html)
        self.assertNotIn('/integrations/dkuoj/live', html)
        with self.assertRaises(NoReverseMatch):
            reverse('contest_fairplay_live', args=['fpexam'])
        with self.assertRaises(Resolver404):
            resolve('/contest/fpexam/fairplay-live')

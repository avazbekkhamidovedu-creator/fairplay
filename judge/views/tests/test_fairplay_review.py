"""The FairPlay tab's settings and student review: kept by FairPlay, nothing stored in DKU OJ."""
import json
from unittest import mock

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from judge.utils.fairplay import FAIRPLAY_REVIEW_CHOICES, FAIRPLAY_REVIEW_HELP
from judge.utils.fairplay_client import FairPlayClient, FairPlayError, FairPlayUnreachable
from judge.utils.fairplay_package import build_exam_entry
from judge.views.tests.fairplay_base import FAIRPLAY_ON, set_fairplay
from judge.views.tests.test_fairplay_pages import PagesMixin, exam, fake_response


@FAIRPLAY_ON
class ReviewTabTestCase(PagesMixin, TestCase):
    def url(self):
        return reverse('contest_fairplay_action', args=['fpexam', 'review'])

    def post(self, user, data, csrf=False):
        return self.login(user, csrf=csrf).post(self.url(), data)

    def test_choices(self):
        self.assertEqual([v for v, _label in FAIRPLAY_REVIEW_CHOICES],
                         ['nothing', 'score', 'breakdown', 'answers', 'answers_and_results'])
        self.assertEqual(set(FAIRPLAY_REVIEW_HELP), {v for v, _label in FAIRPLAY_REVIEW_CHOICES})

    def test_form_on_the_tab_in_every_state(self):
        for status in ('draft', 'live', 'closed'):
            with self.subTest(status=status):
                self.fp.exam.return_value = exam(status=status, settings=dict(exam()['settings'], review='breakdown'))
                html = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
                self.assertIn('id="fairplay-review-form"', html)
                self.assertIn('action="%s"' % self.url(), html)
                self.assertIn("name='csrfmiddlewaretoken'", html)
                self.assertIn('aria-describedby="fairplay-review-help-breakdown" value="breakdown" checked', html)
                for value, label in FAIRPLAY_REVIEW_CHOICES:
                    self.assertIn('value="%s"' % value, html)
                    self.assertIn('id="fairplay-review-help-%s"' % value, html)
                    self.assertIn(str(FAIRPLAY_REVIEW_HELP[value]).replace("'", '&#39;'), html)

    def test_fairplay_down_hides_the_forms_without_text(self):
        self.fp.exam.side_effect = FairPlayUnreachable()
        html = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
        self.assertNotIn('id="fairplay-review-form"', html)
        self.assertNotIn('not reachable', html)

    def test_save_sends_the_signed_call(self):
        response = self.post(self.teacher, {'review': 'answers'})
        self.assertRedirects(response, reverse('contest_fairplay', args=['fpexam']), fetch_redirect_response=False)
        self.fp.set_settings.assert_called_once_with('fpexam', {'review': 'answers'})
        self.assert_acting(self.teacher)
        self.assertIn('Saved.', [str(m) for m in get_messages(response.wsgi_request)][0])
        self.post(self.staff, {'review': 'answers_and_results'})
        self.fp.set_settings.assert_called_with('fpexam', {'review': 'answers_and_results'})

    def test_not_saved_when_fairplay_is_down(self):
        self.fp.set_settings.side_effect = FairPlayUnreachable()
        response = self.post(self.teacher, {'review': 'answers_and_results'})
        self.assertEqual(response.status_code, 302)
        message = [str(m) for m in get_messages(response.wsgi_request)][0]
        self.assertIn('Not saved', message)

    def test_each_level(self):
        for value, label in FAIRPLAY_REVIEW_CHOICES:
            with self.subTest(value=value):
                response = self.post(self.teacher, {'review': value})
                self.fp.set_settings.assert_called_with('fpexam', {'review': value})
                self.assertIn(str(label), [str(m) for m in get_messages(response.wsgi_request)][-1])

    def test_permissions_csrf_and_validation(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher, self.outsider):
            self.assertEqual(self.post(user, {'review': 'answers'}).status_code, 404, user.username)
        self.assertEqual(Client().post(self.url(), {'review': 'answers'}).status_code, 302)    # sign-in
        self.assertEqual(self.post(self.teacher, {'review': 'answers'}, csrf=True).status_code, 403)
        self.assertEqual(self.login(self.teacher).get(self.url()).status_code, 405)
        for bad in ('', 'all', 'answers ', 'ANSWERS'):
            self.post(self.teacher, {'review': bad})
        self.fp.set_settings.assert_not_called()
        set_fairplay(self.exam, False)
        self.assertEqual(self.post(self.teacher, {'review': 'answers'}).status_code, 404)

    def test_not_in_the_exam_list(self):
        self.assertNotIn('settings', build_exam_entry(self.reload_exam()))


@FAIRPLAY_ON
class SettingsTabTestCase(PagesMixin, TestCase):
    def url(self):
        return reverse('contest_fairplay_action', args=['fpexam', 'settings'])

    def form(self, **overrides):
        data = {'calculator': 'scientific', 'websites': 'docs.python.org\r\nCourse https://duke.app.box.com/folder/0',
                'open_book': 'on', 'upload_opens_days': '2', 'resume_needs_approval': 'on'}
        data.update(overrides)
        return {k: v for k, v in data.items() if v is not None}

    def test_shown_from_fairplay(self):
        self.fp.exam.return_value = exam(settings=dict(exam()['settings'], calculator='graphing', open_book=True,
                                                       upload_opens_days=3, websites_input=['docs.python.org'],
                                                       companions=[{'for': 'duke.app.box.com', 'name': 'Duke Box',
                                                                    'sites': ['app.box.com']}]))
        html = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
        self.assertIn('id="fairplay-settings-form"', html)
        self.assertIn('<option value="graphing" selected>', html)
        self.assertIn('<option value="3" selected>', html)
        self.assertIn('>docs.python.org</textarea>', html)
        self.assertIn('id="fairplay-open-book" checked', html)
        self.assertIn('Also opened so Duke Box works:', html)
        self.assertIn('id="fairplay-settings-save"', html)

    def test_closed_exam_settings_read_only(self):
        self.fp.exam.return_value = exam(status='closed')
        html = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
        self.assertIn('<fieldset disabled', html)
        self.assertNotIn('id="fairplay-settings-save"', html)

    def test_not_synced_yet_shows_fairplay_answer(self):
        self.fp.exam.return_value = {'synced': False, 'settings': dict(exam()['settings'], calculator='scientific')}
        html = self.login(self.teacher).get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
        self.assertIn('id="fairplay-not-synced"', html)
        self.assertIn('<option value="scientific" selected>', html)
        self.assertIn('id="fairplay-settings-save"', html)

    def test_save(self):
        self.fp.set_settings.return_value = exam(settings=dict(exam()['settings'], websites_blocked=[
            {'site': 'chatgpt.com', 'reason': 'AI assistant'}]))
        response = self.login(self.teacher).post(self.url(), self.form())
        self.assertEqual(response.status_code, 302)
        self.fp.set_settings.assert_called_once_with('fpexam', {
            'calculator': 'scientific', 'open_book': True, 'strong_lock': False, 'resume_needs_approval': True,
            'websites': ['docs.python.org', 'Course https://duke.app.box.com/folder/0'], 'upload_opens_days': 2})
        self.assert_acting(self.teacher)
        self.assertIn('chatgpt.com', [str(m) for m in get_messages(response.wsgi_request)][0])

    def test_refused_or_unreachable_keeps_what_was_typed(self):
        for error in (FairPlayUnreachable(), FairPlayError("The exam has started: its settings can't change "
                                                           'any more.', status=409)):
            with self.subTest(error=error.detail):
                self.fp.set_settings.side_effect = error
                client = self.login(self.teacher)
                response = client.post(self.url(), self.form(calculator='graphing'))
                self.assertIn('Not saved', [str(m) for m in get_messages(response.wsgi_request)][0])
                html = client.get(reverse('contest_fairplay', args=['fpexam'])).content.decode()
                self.assertIn('id="fairplay-unsaved"', html)
                self.assertIn('<option value="graphing" selected>', html)

    def test_validation(self):
        for bad in (self.form(calculator='abacus'), self.form(upload_opens_days='9'),
                    self.form(websites='\n'.join(['x.example.com'] * 101))):
            response = self.login(self.teacher).post(self.url(), bad)
            self.assertEqual(response.status_code, 302)
        self.fp.set_settings.assert_not_called()

    def test_permissions(self):
        for user in (self.student, self.tester, self.course_admin, self.other_teacher, self.outsider):
            self.assertEqual(self.login(user).post(self.url(), self.form()).status_code, 404, user.username)
        self.assertEqual(self.login(self.teacher, csrf=True).post(self.url(), self.form()).status_code, 403)
        self.fp.set_settings.assert_not_called()


@FAIRPLAY_ON
class SettingsClientTestCase(PagesMixin, TestCase):
    def test_signed_call(self):
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, exam())) as call:
            FairPlayClient(acting_user='fp_teacher').set_settings('fpexam', {'review': 'score', 'calculator': 'off'})
        args, kwargs = call.call_args
        self.assertEqual(args, ('POST', 'https://exam.example.edu/api/integrations/dkuoj/v1/exams/fpexam/settings'))
        self.assertEqual(json.loads(kwargs['data']), {'review': 'score', 'calculator': 'off'})
        self.assertEqual(kwargs['headers']['X-FP-Acting-User'], 'fp_teacher')
        self.assertIn('X-FP-Signature', kwargs['headers'])

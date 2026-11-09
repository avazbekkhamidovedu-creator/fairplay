"""The signed settings call to FairPlay."""
import json
from unittest import mock

from django.test import TestCase

from judge.utils.fairplay_client import FairPlayClient
from judge.views.tests.fairplay_base import FAIRPLAY_ON, installation_path
from judge.views.tests.test_fairplay_pages import PagesMixin, exam, fake_response


@FAIRPLAY_ON
class SettingsClientTestCase(PagesMixin, TestCase):
    def test_signed_call(self):
        with mock.patch('judge.utils.fairplay_client.requests.request',
                        return_value=fake_response(200, exam())) as call:
            FairPlayClient(acting_user='fp_teacher').set_settings('fpexam', {'review': 'score', 'calculator': 'off'})
        args, kwargs = call.call_args
        self.assertEqual(args, ('POST', installation_path('https://exam.example.edu/api/integrations/dkuoj/v1/exams/fpexam/settings')))
        self.assertEqual(json.loads(kwargs['data']), {'review': 'score', 'calculator': 'off'})
        self.assertEqual(kwargs['headers']['X-FP-Acting-User'], 'fp_teacher')
        self.assertIn('X-FP-Signature', kwargs['headers'])

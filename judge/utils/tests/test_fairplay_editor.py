from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from judge.utils.fairplay_editor import BOX_CHANGE_REFUSED, FairPlaySaveResult, clean_settings, editor_settings, \
    fairplay_box_error

LINK = 'https://duke.box.com/s/abc123'


class OpenExamSettingsTestCase(SimpleTestCase):
    def test_partial_keeps_only_what_was_sent(self):
        self.assertEqual(clean_settings({'websites': ' a.org \n\nb.org', 'box_link': ' %s ' % LINK}, partial=True),
                         {'websites': ['a.org', 'b.org'], 'box_link': LINK})

    def test_box_link_is_optional_and_may_be_cleared(self):
        self.assertNotIn('box_link', clean_settings({}))
        self.assertEqual(clean_settings({'box_link': None}, partial=True), {'box_link': ''})
        self.assertEqual(clean_settings({'box_link': ''}, partial=True), {'box_link': ''})

    def test_both_box_hosts(self):
        for link in (LINK, 'https://duke.app.box.com/s/Zz_9-x', 'HTTPS://DUKE.BOX.COM/s/abc'):
            self.assertEqual(clean_settings({'box_link': link}, partial=True)['box_link'], link)

    def test_refused_links(self):
        for link in ('http://duke.box.com/s/a', 'https://box.com/s/a', 'https://duke.box.com/s/a/b',
                     'https://duke.box.com/s/a#x', 'https://duke.box.com:444/s/a', 'https://x.duke.box.com/s/a', 3):
            with self.assertRaises(ValidationError, msg=link):
                clean_settings({'box_link': link}, partial=True)

    def test_editor_settings_echo_the_box_link(self):
        self.assertEqual(editor_settings({'box_link': ' %s ' % LINK})['box_link'], LINK)
        self.assertNotIn('box_link', editor_settings({'box_link': ''}))
        self.assertNotIn('box_link', editor_settings(None))

    def test_box_error_only_for_the_box_link(self):
        sent = {'websites': [], 'box_link': LINK}
        self.assertEqual(fairplay_box_error(FairPlaySaveResult(not_saved='x', settings=sent, status=409)),
                         BOX_CHANGE_REFUSED)
        self.assertEqual(fairplay_box_error(FairPlaySaveResult(not_saved='box_link: no', settings=sent, status=400)),
                         'box_link: no')
        self.assertIsNone(fairplay_box_error(FairPlaySaveResult(not_saved='websites: no', settings=sent, status=400)))
        self.assertIsNone(fairplay_box_error(FairPlaySaveResult(not_saved='x', settings={'websites': []}, status=409)))
        self.assertIsNone(fairplay_box_error(FairPlaySaveResult()))

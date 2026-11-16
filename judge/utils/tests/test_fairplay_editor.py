from django.test import SimpleTestCase

from django.core.exceptions import ValidationError

from judge.utils.fairplay_editor import clean_settings, editor_settings

LINK = 'https://duke.box.com/s/abc123'


class OpenExamSettingsTestCase(SimpleTestCase):
    def test_partial_keeps_only_what_was_sent(self):
        self.assertEqual(clean_settings({'websites': ' a.org \n\nb.org'}, partial=True), {'websites': ['a.org', 'b.org']})

    def test_a_duke_box_link_is_an_ordinary_website_line(self):
        lines = ['https://duke.app.box.com/folder/0', LINK, 'https://duke.app.box.com/s/Zz_9-x', 'docs.python.org']
        self.assertEqual(clean_settings({'websites': lines}, partial=True), {'websites': lines})

    def test_the_separate_box_link_is_no_longer_sent_on(self):
        self.assertEqual(clean_settings({'websites': ['a.org'], 'box_link': LINK}, partial=True), {'websites': ['a.org']})
        self.assertNotIn('box_link', clean_settings({'box_link': LINK}))
        self.assertEqual(clean_settings({'box_link': 7}, partial=True), {})

    def test_a_box_link_saved_before_is_shown_as_the_first_website_line(self):
        self.assertEqual(editor_settings({'box_link': ' %s ' % LINK, 'websites': ['docs.python.org']})['websites'],
                         '%s\ndocs.python.org' % LINK)
        self.assertEqual(editor_settings({'box_link': LINK})['websites'], LINK)
        # not twice when the websites already hold that folder (any spelling of the host)
        self.assertEqual(editor_settings({'box_link': 'https://duke.app.box.com/s/abc123',
                                          'websites_input': [LINK, 'docs.python.org']})['websites'],
                         '%s\ndocs.python.org' % LINK)
        for settings in (None, {}, {'box_link': ''}, {'box_link': None}):
            self.assertNotIn('box_link', editor_settings(settings))
            self.assertEqual(editor_settings(settings)['websites'], '')

    def test_the_dedupe_compares_the_whole_token_and_the_host(self):
        for listed in ('https://example.org/abc123-notes', 'https://duke.box.com/s/abc1234', 'https://other.box.com/s/abc123',
                       'https://example.org/s/abc123', 'http://duke.box.com/s/abc123x'):
            text = editor_settings({'box_link': LINK, 'websites_input': [listed]})['websites']
            self.assertEqual(text, '%s\n%s' % (LINK, listed), listed)
        for listed in ('https://duke.app.box.com/s/abc123', 'Notes HTTPS://DUKE.BOX.COM./s/abc123/', LINK):
            self.assertEqual(editor_settings({'box_link': LINK, 'websites_input': [listed]})['websites'], listed)

    def test_a_carried_box_link_may_be_the_hundred_and_first_line(self):
        lines = ['site%d.example.org' % i for i in range(100)]
        text = editor_settings({'box_link': LINK, 'websites_input': lines})['websites']
        self.assertEqual(len(text.splitlines()), 101)
        self.assertEqual(len(clean_settings({'websites': text}, partial=True)['websites']), 101)
        with self.assertRaises(ValidationError):                      # not for any other 101st line
            clean_settings({'websites': lines + ['one.more.example.org']}, partial=True)
        with self.assertRaises(ValidationError):
            clean_settings({'websites': ['x.example.org'] + lines}, partial=True)
        with self.assertRaises(ValidationError):
            clean_settings({'websites': [LINK] + lines + ['one.more.example.org']}, partial=True)

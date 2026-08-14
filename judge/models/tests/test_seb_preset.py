import shutil
import tempfile

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.models import SEBConfigPreset
from judge.models.seb import DEFAULT_PRESET_CACHE_KEY
from judge.models.tests.util import create_contest

BEK = 'a' * 64
CFG = 'b' * 64
SEB_CONFIG_BYTES = b'<plist>fake seb config</plist>'


def create_preset(name='Default lockdown', **kwargs):
    kwargs.setdefault('browser_exam_keys', BEK)
    kwargs.setdefault('config_keys', CFG)
    preset = SEBConfigPreset(name=name, **kwargs)
    preset.config_file.save('%s.seb' % name.replace(' ', '_'), ContentFile(SEB_CONFIG_BYTES), save=False)
    preset.save()
    return preset


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SEBConfigPresetTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls._overridden_settings['MEDIA_ROOT'], ignore_errors=True)

    def setUp(self):
        cache.delete(DEFAULT_PRESET_CACHE_KEY)

    def test_single_default(self):
        first = create_preset(name='First', is_default=True)
        second = create_preset(name='Second', is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_get_default(self):
        self.assertIsNone(SEBConfigPreset.get_default())
        preset = create_preset(is_default=True)
        self.assertEqual(SEBConfigPreset.get_default(), preset)

    def test_get_default_ignores_inactive(self):
        create_preset(is_default=True, is_active=False)
        self.assertIsNone(SEBConfigPreset.get_default())

    def test_get_default_cache_invalidated_on_save(self):
        old = create_preset(name='Old', is_default=True)
        self.assertEqual(SEBConfigPreset.get_default(), old)
        # The post_save signal must drop the cached pointer, otherwise every
        # editor page would keep offering the superseded configuration.
        new = create_preset(name='New', is_default=True)
        self.assertEqual(SEBConfigPreset.get_default(), new)

    def test_get_default_cache_invalidated_on_delete(self):
        preset = create_preset(is_default=True)
        self.assertEqual(SEBConfigPreset.get_default(), preset)
        preset.delete()
        self.assertIsNone(SEBConfigPreset.get_default())

    def test_clean_requires_keys(self):
        preset = create_preset(browser_exam_keys='', config_keys='')
        with self.assertRaisesRegex(ValidationError, 'Browser Exam Key hash is required'):
            preset.full_clean()
        preset.browser_exam_keys = BEK
        with self.assertRaisesRegex(ValidationError, 'Config Key hash is required'):
            preset.full_clean()
        preset.config_keys = CFG
        preset.full_clean()

    def test_clean_rejects_malformed_keys(self):
        preset = create_preset(browser_exam_keys='not-a-hash')
        with self.assertRaisesRegex(ValidationError, 'not a 64-character hex hash'):
            preset.full_clean()

    def test_allowed_keys_strip_comments(self):
        preset = create_preset(browser_exam_keys='%s  # Windows 3.7\n\n' % BEK.upper())
        self.assertEqual(preset.allowed_bek(), [BEK])
        self.assertEqual(preset.allowed_config_keys(), [CFG])

    def test_live_exam_blocks_key_edit(self):
        preset = create_preset()
        # create_contest defaults to a window spanning now, i.e. a live exam.
        create_contest(key='liveseb', seb_required=True, seb_preset=preset)
        preset.browser_exam_keys = 'c' * 64
        with self.assertRaisesRegex(ValidationError, 'in progress'):
            preset.full_clean()

    def test_finished_exam_allows_key_edit(self):
        preset = create_preset()
        now = timezone.now()
        create_contest(key='pastseb', seb_required=True, seb_preset=preset,
                       start_time=now - timezone.timedelta(days=3),
                       end_time=now - timezone.timedelta(days=2))
        preset.browser_exam_keys = 'c' * 64
        preset.full_clean()

    def test_live_exam_allows_unrelated_edit(self):
        preset = create_preset()
        create_contest(key='liveseb2', seb_required=True, seb_preset=preset)
        # Renaming does not change any key students' clients are sending.
        preset.description = 'Now with a clearer explanation.'
        preset.full_clean()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ContestSEBPresetFallbackTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls._overridden_settings['MEDIA_ROOT'], ignore_errors=True)

    def setUp(self):
        cache.delete(DEFAULT_PRESET_CACHE_KEY)
        self.preset = create_preset(quit_url='https://example.com/quit')

    def test_inherits_keys_from_preset(self):
        contest = create_contest(key='inherit', seb_required=True, seb_preset=self.preset)
        self.assertEqual(contest.seb_allowed_bek(), [BEK])
        self.assertEqual(contest.seb_allowed_config_keys(), [CFG])
        self.assertEqual(contest.seb_effective_quit_url, 'https://example.com/quit')
        self.assertEqual(contest.seb_effective_config_file, self.preset.config_file)

    def test_contest_values_override_preset(self):
        contest = create_contest(key='override', seb_required=True, seb_preset=self.preset,
                                 seb_browser_exam_keys='c' * 64,
                                 seb_quit_url='https://example.com/own-quit')
        self.assertEqual(contest.seb_allowed_bek(), ['c' * 64])
        # Untouched fields still fall through to the preset.
        self.assertEqual(contest.seb_allowed_config_keys(), [CFG])
        self.assertEqual(contest.seb_effective_quit_url, 'https://example.com/own-quit')

    def test_no_preset_keeps_empty(self):
        contest = create_contest(key='nopreset')
        self.assertEqual(contest.seb_allowed_bek(), [])
        self.assertIsNone(contest.seb_effective_config_file)
        self.assertEqual(contest.seb_effective_quit_url, '')

    def test_clean_satisfied_by_preset(self):
        contest = create_contest(key='cleanpreset')
        contest.seb_required = True
        contest.seb_browser_exam_keys = ''
        contest.seb_config_keys = ''
        with self.assertRaisesRegex(ValidationError, 'Browser Exam Key hash is required'):
            contest.full_clean()
        # Linking a preset supplies both key lists, so no upload is needed.
        contest.seb_preset = self.preset
        contest.full_clean()

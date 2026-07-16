import hashlib
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from botocore.exceptions import EndpointConnectionError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from judge.models import StoredFileRecord
from judge.storage import generate_submission_key
from judge.storage.mirror import MirrorTerminalError, mirror_record, verify_record
from judge.storage.resilient import ResilientStorage, StorageUnavailableError

PAYLOAD = b'the quick brown fox jumps over the lazy dog'
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


class FlakyStorage(FileSystemStorage):
    """Simulates an unreachable S3 endpoint."""

    def _fail(self):
        raise EndpointConnectionError(endpoint_url='http://garage:3900')

    def size(self, name):
        self._fail()

    def open(self, name, mode='rb'):
        self._fail()

    def exists(self, name):
        self._fail()


class StorageTestCase(TestCase):
    def setUp(self):
        self.primary_root = tempfile.mkdtemp()
        self.backup_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.primary_root)
        self.addCleanup(shutil.rmtree, self.backup_root)
        self.primary = FileSystemStorage(location=self.primary_root)
        self.backup = FileSystemStorage(location=self.backup_root)
        self.storage = ResilientStorage(primary=self.primary, backup=self.backup,
                                        mirror_enabled=True)
        # mirror_record()/management commands resolve storage via this factory
        patcher = mock.patch('judge.storage.submission_storage', return_value=self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save_file(self, name='submissions/2026/07/1/test.pdf', payload=PAYLOAD):
        return self.storage.save(name, ContentFile(payload))


class ResilientSaveTestCase(StorageTestCase):
    def test_save_creates_pending_record_and_mirrors(self):
        with self.captureOnCommitCallbacks(execute=True):
            name = self.save_file()
        record = StoredFileRecord.objects.get(path=name)
        # eager Celery task ran on commit
        self.assertEqual(record.status, StoredFileRecord.STATUS_MIRRORED)
        self.assertEqual(record.sha256, PAYLOAD_SHA)
        self.assertEqual(record.size, len(PAYLOAD))
        self.assertIsNotNone(record.mirrored_at)
        with self.backup.open(name, 'rb') as f:
            self.assertEqual(f.read(), PAYLOAD)

    def test_save_without_mirror_enabled_stays_pending(self):
        storage = ResilientStorage(primary=self.primary, backup=None, mirror_enabled=False)
        with self.captureOnCommitCallbacks(execute=True):
            name = storage.save('submissions/2026/07/2/x.txt', ContentFile(PAYLOAD))
        record = StoredFileRecord.objects.get(path=name)
        self.assertEqual(record.status, StoredFileRecord.STATUS_PENDING)
        self.assertFalse(self.backup.exists(name))

    def test_delete_is_primary_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            name = self.save_file()
        self.storage.delete(name)
        self.assertFalse(self.primary.exists(name))
        self.assertTrue(self.backup.exists(name))
        record = StoredFileRecord.objects.get(path=name)
        self.assertEqual(record.status, StoredFileRecord.STATUS_DELETED)


class ReadFallbackTestCase(StorageTestCase):
    def test_open_falls_back_to_backup(self):
        with self.captureOnCommitCallbacks(execute=True):
            name = self.save_file()
        flaky = ResilientStorage(primary=FlakyStorage(location=self.primary_root),
                                 backup=self.backup, mirror_enabled=True)
        with flaky.open(name) as f:
            self.assertEqual(f.read(), PAYLOAD)

    def test_both_stores_down_raises(self):
        flaky = ResilientStorage(primary=FlakyStorage(location=self.primary_root),
                                 backup=FlakyStorage(location=self.backup_root),
                                 mirror_enabled=True)
        with self.assertRaises(StorageUnavailableError):
            flaky.open('submissions/2026/07/1/nope.pdf')

    def test_no_fallback_when_mirror_disabled(self):
        flaky = ResilientStorage(primary=FlakyStorage(location=self.primary_root),
                                 backup=None, mirror_enabled=False)
        with self.assertRaises(EndpointConnectionError):
            flaky.open('submissions/2026/07/1/nope.pdf')


class MirrorTestCase(StorageTestCase):
    def make_record(self, name='submissions/2026/07/3/m.bin', payload=PAYLOAD, **kwargs):
        self.primary.save(name, ContentFile(payload))
        defaults = {
            'path': name,
            'sha256': hashlib.sha256(payload).hexdigest(),
            'size': len(payload),
        }
        defaults.update(kwargs)
        return StoredFileRecord.objects.create(**defaults)

    def test_mirror_is_idempotent(self):
        record = self.make_record()
        mirror_record(record, self.primary, self.backup)
        first_mirrored_at = record.mirrored_at
        mirror_record(record, self.primary, self.backup)
        self.assertEqual(record.mirrored_at, first_mirrored_at)

    def test_partial_backup_copy_is_replaced(self):
        record = self.make_record()
        self.backup.save(record.path, ContentFile(PAYLOAD[:10]))  # simulated dead upload
        mirror_record(record, self.primary, self.backup)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_MIRRORED)
        with self.backup.open(record.path, 'rb') as f:
            self.assertEqual(f.read(), PAYLOAD)

    def test_zero_byte_fails_terminally(self):
        record = self.make_record(name='submissions/2026/07/3/empty.bin', payload=b'')
        with self.assertRaises(MirrorTerminalError):
            mirror_record(record, self.primary, self.backup)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_FAILED)

    def test_missing_primary_object_fails_terminally(self):
        record = StoredFileRecord.objects.create(path='submissions/2026/07/3/gone.bin',
                                                 sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        with self.assertRaises(MirrorTerminalError):
            mirror_record(record, self.primary, self.backup)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_FAILED)

    def test_verify_record_localizes_corruption(self):
        record = self.make_record()
        mirror_record(record, self.primary, self.backup)
        self.assertEqual(verify_record(record, self.primary, self.backup),
                         {'primary': True, 'backup': True})
        self.backup.delete(record.path)
        self.backup.save(record.path, ContentFile(b'corrupted'))
        self.assertEqual(verify_record(record, self.primary, self.backup),
                         {'primary': True, 'backup': False})


class SweepCommandTestCase(StorageTestCase):
    def test_sweep_recovers_lost_task(self):
        # A PENDING record whose Celery task was never delivered.
        name = 'submissions/2026/07/4/lost.bin'
        self.primary.save(name, ContentFile(PAYLOAD))
        record = StoredFileRecord.objects.create(path=name, sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        StoredFileRecord.objects.filter(id=record.id).update(
            created_on=timezone.now() - timedelta(hours=1))

        with self.settings(DMOJ_BACKUP_MIRROR_ENABLED=True):
            call_command('storage_mirror_sweep')

        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_MIRRORED)
        self.assertTrue(self.backup.exists(name))

    def test_sweep_exits_nonzero_on_persistent_failure(self):
        StoredFileRecord.objects.create(path='submissions/2026/07/4/hopeless.bin',
                                        sha256=PAYLOAD_SHA, size=len(PAYLOAD),
                                        status=StoredFileRecord.STATUS_FAILED)
        with self.settings(DMOJ_BACKUP_MIRROR_ENABLED=True):
            with self.assertRaises(CommandError):
                call_command('storage_mirror_sweep')


class KeySchemeTestCase(TestCase):
    def test_key_shape(self):
        now = timezone.datetime(2026, 7, 14, tzinfo=timezone.utc)
        key = generate_submission_key(12345, 'My Report (final).PDF', now=now)
        self.assertRegex(key, r'^submissions/2026/07/12345/[0-9a-f]{32}\.pdf$')

    def test_hostile_extension_is_dropped(self):
        now = timezone.datetime(2026, 7, 14, tzinfo=timezone.utc)
        for bad in ('x.tar.gz../../etc', 'noext', 'x.', 'x.' + 'a' * 20, 'x.exe‮'):
            key = generate_submission_key(1, bad, now=now)
            self.assertRegex(key, r'^submissions/2026/07/1/[0-9a-f]{32}(\.[a-z0-9]{1,10})?$')
            self.assertNotIn('..', key)

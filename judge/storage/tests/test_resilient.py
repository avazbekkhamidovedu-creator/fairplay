import hashlib
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.models import StoredFileRecord
from judge.storage import generate_submission_key
from judge.storage.mirror import MirrorTerminalError, mirror_record, verify_record
from judge.storage.resilient import ResilientStorage, StorageUnavailableError
from judge.tasks import mirror_to_backup

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


class ClientErrorStorage(FileSystemStorage):
    """Simulates an S3 endpoint returning a specific ClientError on HEAD (size)."""

    def __init__(self, *args, code='404', http_status=404, **kwargs):
        super().__init__(*args, **kwargs)
        self._code, self._http = code, http_status

    def size(self, name):
        raise ClientError({'Error': {'Code': self._code},
                           'ResponseMetadata': {'HTTPStatusCode': self._http}}, 'HeadObject')


# Run the mirror task inline regardless of settings module — CI runs the suite
# without a broker, so a real .delay() would fail with ConnectionRefusedError.
@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
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

    def test_non_seekable_content_is_rejected(self):
        class NonSeekable(ContentFile):
            def seekable(self):
                return False
        with self.assertRaises(ValueError):
            self.storage.save('submissions/2026/07/2/ns.bin', NonSeekable(PAYLOAD))


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

    def test_s3_404_falls_back_to_backup(self):
        with self.captureOnCommitCallbacks(execute=True):
            name = self.save_file()
        storage = ResilientStorage(
            primary=ClientErrorStorage(location=self.primary_root, code='404', http_status=404),
            backup=self.backup, mirror_enabled=True)
        with storage.open(name) as f:
            self.assertEqual(f.read(), PAYLOAD)

    def test_s3_403_does_not_fall_back(self):
        # A permissions error must bubble up, never silently serve backup data.
        with self.captureOnCommitCallbacks(execute=True):
            name = self.save_file()
        storage = ResilientStorage(
            primary=ClientErrorStorage(location=self.primary_root, code='403', http_status=403),
            backup=self.backup, mirror_enabled=True)
        with self.assertRaises(ClientError):
            storage.open(name)

    def test_deleted_record_is_not_resurrected(self):
        # After delete(), the backup still holds the bytes, but a read must not serve
        # them — the record is DELETED.
        with self.captureOnCommitCallbacks(execute=True):
            name = self.save_file()
        self.storage.delete(name)
        storage = ResilientStorage(primary=FlakyStorage(location=self.primary_root),
                                   backup=self.backup, mirror_enabled=True)
        with self.assertRaises(EndpointConnectionError):
            storage.open(name)


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

    def test_zero_byte_is_abandoned(self):
        record = self.make_record(name='submissions/2026/07/3/empty.bin', payload=b'')
        with self.assertRaises(MirrorTerminalError):
            mirror_record(record, self.primary, self.backup)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_ABANDONED)

    def test_missing_primary_object_is_abandoned(self):
        record = StoredFileRecord.objects.create(path='submissions/2026/07/3/gone.bin',
                                                 sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        with self.assertRaises(MirrorTerminalError):
            mirror_record(record, self.primary, self.backup)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_ABANDONED)

    def test_mark_mirrored_does_not_resurrect_deleted(self):
        # An in-flight mirror that finishes *after* a concurrent del() flipped the row
        # to DELETED must not overwrite it back to MIRRORED.
        from judge.storage.mirror import _mark_mirrored
        record = self.make_record(name='submissions/2026/07/3/race.bin')
        StoredFileRecord.objects.filter(id=record.id).update(status=StoredFileRecord.STATUS_DELETED)
        _mark_mirrored(record)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_DELETED)

    def test_verify_record_localizes_corruption(self):
        record = self.make_record()
        mirror_record(record, self.primary, self.backup)
        self.assertEqual(verify_record(record, self.primary, self.backup),
                         {'primary': True, 'backup': True})
        self.backup.delete(record.path)
        self.backup.save(record.path, ContentFile(b'corrupted'))
        self.assertEqual(verify_record(record, self.primary, self.backup),
                         {'primary': True, 'backup': False})

    def test_verify_record_unreadable_is_none_not_false(self):
        # A transient read failure must be None (unknown), not False (corrupt), so
        # an outage during --verify is not mistaken for data corruption.
        record = self.make_record()
        mirror_record(record, self.primary, self.backup)
        result = verify_record(record, self.primary,
                               FlakyStorage(location=self.backup_root))
        self.assertEqual(result, {'primary': True, 'backup': None})

    def test_task_marks_failed_when_retries_exhausted(self):
        # Regression: celery's retry(exc=...) re-raises the original exc (not
        # MaxRetriesExceededError) on exhaustion, so the task must mark FAILED itself
        # on the final attempt.
        record = self.make_record(name='submissions/2026/07/3/exhaust.bin')
        with mock.patch('judge.tasks.storage_mirror.mirror_record',
                        side_effect=OSError('box unreachable')):
            mirror_to_backup.apply(args=[record.id], retries=8)
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_FAILED)


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

    def test_sweep_abandons_terminal_and_stops_reattempting(self):
        # A stale PENDING zero-byte record can never be mirrored: it should become
        # ABANDONED and the next sweep must not touch it again (no attempts growth).
        name = 'submissions/2026/07/4/empty.bin'
        self.primary.save(name, ContentFile(b''))
        record = StoredFileRecord.objects.create(
            path=name, sha256=hashlib.sha256(b'').hexdigest(), size=0)
        StoredFileRecord.objects.filter(id=record.id).update(
            created_on=timezone.now() - timedelta(hours=1))

        with self.settings(DMOJ_BACKUP_MIRROR_ENABLED=True):
            with self.assertRaises(CommandError):
                call_command('storage_mirror_sweep')
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_ABANDONED)
        attempts = record.attempts

        with self.settings(DMOJ_BACKUP_MIRROR_ENABLED=True):
            with self.assertRaises(CommandError):
                call_command('storage_mirror_sweep')
        record.refresh_from_db()
        self.assertEqual(record.attempts, attempts)  # not re-attempted

    def test_sweep_verify_requeues_corrupt_backup(self):
        # Primary intact, backup corrupt -> record queued back to PENDING for re-mirror.
        name = 'submissions/2026/07/4/heal.bin'
        self.primary.save(name, ContentFile(PAYLOAD))
        record = StoredFileRecord.objects.create(path=name, sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        mirror_record(record, self.primary, self.backup)
        self.backup.delete(name)
        self.backup.save(name, ContentFile(b'corrupted'))

        with self.settings(DMOJ_BACKUP_MIRROR_ENABLED=True):
            call_command('storage_mirror_sweep', '--verify', '10')
        record.refresh_from_db()
        self.assertEqual(record.status, StoredFileRecord.STATUS_PENDING)


class RestoreCommandTestCase(StorageTestCase):
    def test_force_restore_preserves_primary_when_backup_corrupt(self):
        name = 'submissions/2026/07/5/rf.bin'
        self.primary.save(name, ContentFile(PAYLOAD))
        record = StoredFileRecord.objects.create(path=name, sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        mirror_record(record, self.primary, self.backup)
        self.backup.delete(name)
        self.backup.save(name, ContentFile(b'bad'))  # corrupt backup

        with self.settings(DMOJ_BACKUP_MIRROR_ENABLED=True):
            with self.assertRaises(CommandError):
                call_command('storage_restore_backup', '--force')

        # The good primary object must be untouched.
        self.assertTrue(self.primary.exists(name))
        with self.primary.open(name, 'rb') as f:
            self.assertEqual(f.read(), PAYLOAD)


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

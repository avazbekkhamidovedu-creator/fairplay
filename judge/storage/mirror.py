import hashlib
import logging

from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.utils import timezone

logger = logging.getLogger('judge.storage')

CHUNK_SIZE = 1024 * 1024


class MirrorTerminalError(Exception):
    """Mirroring this record can never succeed (zero-byte file, primary object gone).
    The record is marked FAILED; retrying is pointless."""


def _stream_sha256(fileobj):
    hasher = hashlib.sha256()
    size = 0
    while True:
        chunk = fileobj.read(CHUNK_SIZE)
        if not chunk:
            break
        hasher.update(chunk)
        size += len(chunk)
    return hasher.hexdigest(), size


def _backup_verifies(backup, record):
    if backup.size(record.path) != record.size:
        return False
    with backup.open(record.path, 'rb') as f:
        digest, _ = _stream_sha256(f)
    return digest == record.sha256


def mirror_record(record, primary=None, backup=None):
    """Copy one StoredFileRecord's object primary -> backup and sha256-verify it.

    Idempotent: a matching backup copy is a no-op; a wrong-size/hash copy (Box has no
    resume, so a killed upload leaves a partial file) is deleted and re-uploaded whole.
    Shared by the Celery task and the cron sweep. Transient trouble re-raises so the
    caller can retry; hopeless records raise MirrorTerminalError after being FAILED.
    """
    from judge.models.stored_file import StoredFileRecord

    if primary is None or backup is None:
        from judge.storage import submission_storage
        storage = submission_storage()
        primary = primary or storage.primary
        backup = backup or storage.backup
    if backup is None:
        raise ImproperlyConfigured('no backup storage configured; enable DMOJ_BACKUP_MIRROR_ENABLED')

    if record.status in (StoredFileRecord.STATUS_MIRRORED, StoredFileRecord.STATUS_DELETED):
        return record

    record.attempts += 1
    try:
        if record.size == 0:
            _fail(record, 'zero-byte file: Box SFTP cannot store empty files')
        if not primary.exists(record.path):
            _fail(record, 'object missing from primary storage')

        if backup.exists(record.path):
            if _backup_verifies(backup, record):
                return _mark_mirrored(record)
            logger.warning('partial/corrupt backup copy of %s; deleting and re-uploading', record.path)
            backup.delete(record.path)

        with primary.open(record.path, 'rb') as src:
            backup._save(record.path, File(src, name=record.path))

        if not _backup_verifies(backup, record):
            backup.delete(record.path)
            raise OSError(f'backup verification failed for {record.path} (size/sha256 mismatch after upload)')

        return _mark_mirrored(record)
    except MirrorTerminalError:
        raise
    except Exception as e:
        record.last_error = f'{type(e).__name__}: {e}'[:5000]
        record.save(update_fields=['attempts', 'last_error'])
        raise


def _mark_mirrored(record):
    from judge.models.stored_file import StoredFileRecord
    record.status = StoredFileRecord.STATUS_MIRRORED
    record.mirrored_at = timezone.now()
    record.last_error = ''
    record.save(update_fields=['status', 'mirrored_at', 'attempts', 'last_error'])
    return record


def _fail(record, message):
    from judge.models.stored_file import StoredFileRecord
    logger.error('mirror of %s failed terminally: %s', record.path, message)
    record.status = StoredFileRecord.STATUS_FAILED
    record.last_error = message
    record.save(update_fields=['status', 'attempts', 'last_error'])
    raise MirrorTerminalError(message)


def verify_record(record, primary=None, backup=None):
    """Integrity spot-check of a MIRRORED record against both stores.

    Returns a dict of {'primary': bool, 'backup': bool} so a mismatch can be
    localized to the corrupt side.
    """
    if primary is None or backup is None:
        from judge.storage import submission_storage
        storage = submission_storage()
        primary = primary or storage.primary
        backup = backup or storage.backup

    result = {}
    for side, store in (('primary', primary), ('backup', backup)):
        try:
            with store.open(record.path, 'rb') as f:
                digest, size = _stream_sha256(f)
            result[side] = digest == record.sha256 and size == record.size
        except Exception:
            logger.exception('verify: cannot read %s from %s', record.path, side)
            result[side] = False
    return result

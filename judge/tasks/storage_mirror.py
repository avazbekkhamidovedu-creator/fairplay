import logging

import paramiko
from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from judge.models import StoredFileRecord
from judge.storage.mirror import MirrorTerminalError, mirror_record

__all__ = ('mirror_to_backup',)

logger = logging.getLogger('judge.storage')

#: Transient trouble worth retrying; anything else fails the task immediately.
RETRYABLE_ERRORS = (OSError, EOFError, paramiko.SSHException, BotoCoreError, ClientError)


@shared_task(bind=True, acks_late=True, max_retries=8)
def mirror_to_backup(self, record_id):
    """Copy one submission file to the Box backup. Idempotent (safe under acks_late
    redelivery); retries with exponential backoff for up to several hours, then marks
    the record FAILED and leaves it to the storage_mirror_sweep cron."""
    try:
        record = StoredFileRecord.objects.get(id=record_id)
    except StoredFileRecord.DoesNotExist:
        return

    try:
        mirror_record(record)
    except MirrorTerminalError:
        pass  # already marked FAILED with a message
    except RETRYABLE_ERRORS as exc:
        try:
            # 60s, 120s, ..., capped at 1h; ~4.5h total before giving up.
            raise self.retry(exc=exc, countdown=min(60 * 2 ** self.request.retries, 3600))
        except MaxRetriesExceededError:
            record.status = StoredFileRecord.STATUS_FAILED
            record.save(update_fields=['status'])
            logger.error('mirror of %s exhausted retries; last error: %s', record.path, record.last_error)

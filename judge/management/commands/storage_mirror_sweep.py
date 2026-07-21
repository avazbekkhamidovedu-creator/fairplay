import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from judge.models import StoredFileRecord
from judge.storage.mirror import MirrorTerminalError, mirror_record, verify_record


class Command(BaseCommand):
    help = ('Reconciliation sweep for the submission-file backup mirror: re-mirrors stale '
            'PENDING records (lost Celery tasks) and FAILED records inline, without needing '
            'a Celery worker. ABANDONED records (terminal: zero-byte / primary gone) are left '
            'alone so they are not re-attempted forever. Run from cron (recommended: */15). '
            'Exits nonzero while any record is FAILED or ABANDONED, so cron MAILTO doubles as '
            'the alert channel.')

    def add_arguments(self, parser):
        parser.add_argument('--stale-minutes', type=int, default=15,
                            help='re-mirror PENDING records older than this (default 15)')
        parser.add_argument('--verify', type=int, default=0, metavar='N',
                            help='also sha256-verify N random MIRRORED files on both stores')

    def handle(self, *args, **options):
        if not settings.DMOJ_BACKUP_MIRROR_ENABLED:
            self.stdout.write('DMOJ_BACKUP_MIRROR_ENABLED is off; nothing to do.')
            return

        cutoff = timezone.now() - timedelta(minutes=options['stale_minutes'])
        stale = StoredFileRecord.objects.filter(status=StoredFileRecord.STATUS_PENDING,
                                                created_on__lt=cutoff)
        failed = StoredFileRecord.objects.filter(status=StoredFileRecord.STATUS_FAILED)

        swept = errors = 0
        for record in list(stale) + list(failed):
            swept += 1
            try:
                mirror_record(record)
            except MirrorTerminalError:
                errors += 1
            except Exception as e:
                errors += 1
                self.stderr.write(f'{record.path}: {type(e).__name__}: {e}')

        corrupt = healed = 0
        if options['verify']:
            mirrored_ids = list(StoredFileRecord.objects.filter(
                status=StoredFileRecord.STATUS_MIRRORED).values_list('id', flat=True))
            sample = random.sample(mirrored_ids, min(options['verify'], len(mirrored_ids)))
            for record in StoredFileRecord.objects.filter(id__in=sample):
                result = verify_record(record)
                if result['primary'] is True and result['backup'] is False:
                    # Primary intact, backup corrupt: queue a re-mirror from the primary
                    # (next sweep re-uploads). None (unreadable) is left alone as transient.
                    StoredFileRecord.objects.filter(id=record.id).update(
                        status=StoredFileRecord.STATUS_PENDING, mirrored_at=None)
                    healed += 1
                    self.stdout.write(f'VERIFY: backup corrupt, queued re-mirror {record.path}')
                elif result['primary'] is False or result['backup'] is False:
                    corrupt += 1
                    self.stderr.write(f'VERIFY FAILED {record.path}: {result}')

        pending = StoredFileRecord.objects.filter(status=StoredFileRecord.STATUS_PENDING)
        failed_count = StoredFileRecord.objects.filter(status=StoredFileRecord.STATUS_FAILED).count()
        abandoned_count = StoredFileRecord.objects.filter(
            status=StoredFileRecord.STATUS_ABANDONED).count()
        oldest = pending.order_by('created_on').first()
        lag = (timezone.now() - oldest.created_on) if oldest else None
        self.stdout.write(f'swept={swept} errors={errors} healed={healed} pending={pending.count()} '
                          f'failed={failed_count} abandoned={abandoned_count} '
                          f'oldest_pending_age={lag or "-"}')

        if failed_count or abandoned_count or corrupt:
            raise CommandError(f'{failed_count} FAILED, {abandoned_count} ABANDONED '
                               f'(manual action needed), {corrupt} corrupt')

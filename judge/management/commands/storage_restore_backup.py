import hashlib

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from judge.models import StoredFileRecord
from judge.storage import submission_storage


class Command(BaseCommand):
    help = ('Restore submission files from the Box backup to the primary S3 store. Driven '
            'by MIRRORED StoredFileRecord rows (never lists the backup remote), verified '
            'against the stored sha256. By default only copies objects missing from the '
            'primary; --force re-copies everything matched.')

    def add_arguments(self, parser):
        parser.add_argument('--prefix', default='', help='only restore paths with this prefix '
                                                         '(e.g. submissions/2026/)')
        parser.add_argument('--force', action='store_true',
                            help='overwrite objects that already exist on the primary')
        parser.add_argument('--dry-run', action='store_true', help='report what would be restored')

    def handle(self, *args, **options):
        storage = submission_storage()
        primary, backup = storage.primary, storage.backup
        if backup is None:
            raise CommandError('no backup storage configured (DMOJ_BACKUP_MIRROR_ENABLED off?)')

        records = StoredFileRecord.objects.filter(status=StoredFileRecord.STATUS_MIRRORED)
        if options['prefix']:
            records = records.filter(path__startswith=options['prefix'])

        restored = skipped = failed = 0
        for record in records.iterator():
            if not options['force'] and primary.exists(record.path):
                skipped += 1
                continue
            if options['dry_run']:
                self.stdout.write(f'would restore {record.path}')
                restored += 1
                continue
            try:
                if options['force'] and primary.exists(record.path):
                    primary.delete(record.path)
                with backup.open(record.path, 'rb') as src:
                    primary._save(record.path, File(src, name=record.path))
                hasher = hashlib.sha256()
                with primary.open(record.path, 'rb') as check:
                    for chunk in iter(lambda: check.read(1024 * 1024), b''):
                        hasher.update(chunk)
                if hasher.hexdigest() != record.sha256:
                    raise ValueError('sha256 mismatch after restore')
                restored += 1
                self.stdout.write(f'restored {record.path}')
            except Exception as e:
                failed += 1
                self.stderr.write(f'FAILED {record.path}: {type(e).__name__}: {e}')

        self.stdout.write(f'restored={restored} skipped={skipped} failed={failed}')
        if failed:
            raise CommandError(f'{failed} files failed to restore')

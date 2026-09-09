"""Print assignment drafts in a form a teacher can read without the Drafts page.

``judge_assignmentdraft.content`` stores MCQ option row ids and file answers live
in object storage, so a raw SQL dump is not enough. This command joins those
pieces and can copy uploaded files onto disk.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from judge.models import AssignmentDraft, Contest, ContestParticipation
from judge.utils.assignment_draft import format_draft_preview


class Command(BaseCommand):
    help = 'Show saved assignment drafts with choice text and original filenames.'

    def add_arguments(self, parser):
        parser.add_argument('contest_key', help='Assignment key, e.g. c000018')
        parser.add_argument('--user', dest='username', default='',
                            help='Limit to this username.')
        parser.add_argument('--save-files', dest='save_files', default='',
                            help='Directory to copy file-upload drafts into.')

    def handle(self, *args, **options):
        contest = Contest.objects.filter(key=options['contest_key']).first()
        if contest is None:
            raise CommandError('No assignment with key %s' % options['contest_key'])
        drafts = (
            AssignmentDraft.objects
            .filter(
                participation__contest=contest,
                participation__virtual=ContestParticipation.LIVE,
            )
            .select_related('participation__user__user', 'problem', 'language')
            .prefetch_related('upload', 'problem__mcq_options')
            .order_by('participation__user__user__username', 'problem__code')
        )
        username = (options['username'] or '').strip()
        if username:
            drafts = drafts.filter(participation__user__user__username=username)

        save_dir = Path(options['save_files']).expanduser() if options['save_files'] else None
        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for draft in drafts:
            upload = getattr(draft, 'upload', None)
            if not draft.has_content() and upload is None:
                continue
            count += 1
            user = draft.participation.user.username
            self.stdout.write('---')
            self.stdout.write('student: %s' % user)
            self.stdout.write('problem: %s (%s)' % (draft.problem.code, draft.problem.name))
            self.stdout.write('type: %s' % draft.problem.submission_type)
            self.stdout.write('saved: %s' % draft.updated_on)
            if draft.language_id:
                self.stdout.write('language: %s' % draft.language.key)
            try:
                self.stdout.write(format_draft_preview(draft))
            except Exception as exc:
                self.stdout.write('unreadable: %s' % exc)
            if save_dir is not None and upload is not None and upload.file:
                dest = save_dir / ('%s_%s_%s' % (user, draft.problem.code, upload.original_name))
                with upload.file.open('rb') as src, dest.open('wb') as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                self.stdout.write('copied: %s' % dest)
        if count == 0:
            self.stdout.write('No saved drafts.')
        else:
            self.stdout.write('---')
            self.stdout.write('%s draft(s)' % count)

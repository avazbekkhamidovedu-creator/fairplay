from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from judge.models import StoredFileRecord


class StoredFileRecordAdmin(admin.ModelAdmin):
    list_display = ('path', 'status', 'size', 'attempts', 'created_on', 'mirrored_at')
    list_filter = ('status',)
    search_fields = ('path',)
    readonly_fields = ('path', 'sha256', 'size', 'status', 'created_on', 'mirrored_at',
                       'attempts', 'last_error')
    actions = ('retry_mirror',)
    date_hierarchy = 'created_on'

    def has_add_permission(self, request):
        return False  # rows are created by the storage layer only

    @admin.action(description=_('Retry mirror to backup now'))
    def retry_mirror(self, request, queryset):
        from judge.tasks import mirror_to_backup
        count = 0
        for record in queryset.exclude(status=StoredFileRecord.STATUS_DELETED):
            mirror_to_backup.delay(record.id)
            count += 1
        self.message_user(request, ngettext('%d mirror task queued.', '%d mirror tasks queued.',
                                            count) % count, messages.SUCCESS)

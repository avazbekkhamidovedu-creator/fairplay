from django.contrib import admin
from django.utils.translation import gettext_lazy as _


class AssignmentDraftAdmin(admin.ModelAdmin):
    """A read-only window for answering "what did the student have when the assignment closed?".

    Drafts are written by the student's workspace and consumed by auto-submit; letting staff edit
    them here would put words in a student's mouth and change what gets submitted, so every field
    is read-only and rows can be neither added nor changed.
    """
    list_display = ('participation', 'problem', 'language', 'updated_on', 'harvested_at')
    list_filter = ('harvested_at', 'problem__submission_type')
    list_select_related = ('participation__user__user', 'participation__contest', 'problem', 'language')
    search_fields = ('participation__user__user__username', 'problem__code')
    readonly_fields = ('participation', 'problem', 'language', 'content', 'updated_on', 'harvested_at')
    date_hierarchy = 'updated_on'

    @admin.display(description=_('assignment'))
    def contest(self, obj):
        return obj.participation.contest.name

    def has_add_permission(self, request):
        return False  # drafts are written by the student's workspace only

    def has_change_permission(self, request, obj=None):
        return False

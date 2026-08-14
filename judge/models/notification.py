from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models.profile import Profile

# Auth group that receives teacher-verification applications.
SITE_ADMINISTRATORS_GROUP = 'Site Administrators'
# Group granted when a teacher-verification request is approved.
TEACHERS_GROUP = 'Teachers'

# Django permissions attached to Teachers (course/class management + private problems).
# Intentionally excludes edit_all_organization / delete_organization (site-admin scope).
TEACHER_PERMISSION_CODENAMES = (
    'add_organization',
    'change_organization',
    'organization_admin',
    'add_class',
    'change_class',
    'delete_class',
    'create_private_problem',
    'edit_own_problem',
)


def ensure_teachers_group():
    """Create Teachers group and attach teacher permissions. Returns the Group."""
    from django.contrib.auth.models import Group, Permission

    group, _created = Group.objects.get_or_create(name=TEACHERS_GROUP)
    perms = Permission.objects.filter(
        content_type__app_label='judge',
        codename__in=TEACHER_PERMISSION_CODENAMES,
    )
    group.permissions.add(*perms)
    return group


class TeacherVerificationRequest(models.Model):
    STATES = (
        ('P', _('Pending')),
        ('A', _('Approved')),
        ('R', _('Rejected')),
    )

    user = models.ForeignKey(
        Profile, verbose_name=_('user'), related_name='teacher_verification_requests',
        on_delete=models.CASCADE,
    )
    reason = models.TextField(verbose_name=_('reason'))
    time = models.DateTimeField(verbose_name=_('request time'), auto_now_add=True)
    state = models.CharField(max_length=1, verbose_name=_('state'), choices=STATES, default='P')
    reviewed_by = models.ForeignKey(
        Profile, verbose_name=_('reviewed by'), null=True, blank=True,
        related_name='reviewed_teacher_verifications', on_delete=models.SET_NULL,
    )
    reviewed_time = models.DateTimeField(verbose_name=_('reviewed time'), null=True, blank=True)

    class Meta:
        verbose_name = _('teacher verification request')
        verbose_name_plural = _('teacher verification requests')
        ordering = ('-time',)

    def __str__(self):
        return 'TeacherVerification(%s, %s)' % (self.user_id, self.state)


class AdminNotification(models.Model):
    """Simple admin inbox messages for course/class join and teacher verification."""

    KIND_TEACHER_VERIFY = 'teacher_verify'
    KIND_COURSE_JOIN = 'course_join'
    KIND_CLASS_JOIN = 'class_join'
    KIND_CHOICES = (
        (KIND_TEACHER_VERIFY, _('Teacher verification')),
        (KIND_COURSE_JOIN, _('Course join request')),
        (KIND_CLASS_JOIN, _('Class join request')),
    )

    recipient = models.ForeignKey(
        Profile, verbose_name=_('recipient'), related_name='admin_notifications',
        on_delete=models.CASCADE,
    )
    actor = models.ForeignKey(
        Profile, verbose_name=_('actor'), null=True, blank=True,
        related_name='sent_admin_notifications', on_delete=models.SET_NULL,
    )
    kind = models.CharField(max_length=32, verbose_name=_('kind'), choices=KIND_CHOICES, db_index=True)
    title = models.CharField(max_length=200, verbose_name=_('title'))
    body = models.TextField(verbose_name=_('body'), blank=True)
    link = models.CharField(max_length=300, verbose_name=_('link'), blank=True)
    # Optional pointers for approve actions / deep links.
    organization_request = models.ForeignKey(
        'judge.OrganizationRequest', null=True, blank=True, on_delete=models.CASCADE,
        related_name='notifications',
    )
    teacher_verification = models.ForeignKey(
        TeacherVerificationRequest, null=True, blank=True, on_delete=models.CASCADE,
        related_name='notifications',
    )
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(verbose_name=_('read at'), null=True, blank=True)

    class Meta:
        verbose_name = _('admin notification')
        verbose_name_plural = _('admin notifications')
        ordering = ('-created',)
        indexes = [
            models.Index(fields=['recipient', 'read_at', '-created']),
        ]

    def __str__(self):
        return '%s → %s' % (self.kind, self.recipient_id)

    @property
    def is_unread(self):
        return self.read_at is None

    def resolve_link(self):
        if self.link:
            return self.link
        if self.organization_request_id:
            return reverse('organization_join_requests') + '?status=P'
        if self.teacher_verification_id:
            return reverse('organization_teacher_requests') + '?status=P'
        return reverse('organization_notifications')

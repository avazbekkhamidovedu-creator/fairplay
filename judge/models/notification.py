from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models.profile import Profile

# Auth group that receives instructor-verification applications.
SITE_ADMINISTRATORS_GROUP = 'Site Administrators'
# Optional site-wide group for creating courses / private problems (manual grant only).
# Instructor verification no longer adds users here — approval is scoped to one class.
INSTRUCTORS_GROUP = 'Instructors'
# Backward-compatible alias (historical migrations / older code).
TEACHERS_GROUP = INSTRUCTORS_GROUP

# Django permissions attached to Instructors (course/class management + private problems).
# Intentionally excludes edit_all_organization / delete_organization (site-admin scope).
INSTRUCTOR_PERMISSION_CODENAMES = (
    'add_organization',
    'change_organization',
    'organization_admin',
    'add_class',
    'change_class',
    'delete_class',
    'create_private_problem',
    'edit_own_problem',
)
TEACHER_PERMISSION_CODENAMES = INSTRUCTOR_PERMISSION_CODENAMES


def ensure_instructors_group():
    """Create Instructors group and attach instructor permissions. Returns the Group."""
    from django.contrib.auth.models import Group, Permission

    group, _created = Group.objects.get_or_create(name=INSTRUCTORS_GROUP)
    perms = Permission.objects.filter(
        content_type__app_label='judge',
        codename__in=INSTRUCTOR_PERMISSION_CODENAMES,
    )
    group.permissions.add(*perms)
    return group


ensure_teachers_group = ensure_instructors_group


class InstructorVerificationRequest(models.Model):
    """Request to instruct a specific course class (not site-wide Instructors access)."""

    STATES = (
        ('P', _('Pending')),
        ('A', _('Approved')),
        ('R', _('Rejected')),
    )

    user = models.ForeignKey(
        Profile, verbose_name=_('user'), related_name='instructor_verification_requests',
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        'judge.Organization', verbose_name=_('course'),
        related_name='instructor_verification_requests', on_delete=models.CASCADE,
        null=True, blank=True,
    )
    request_class = models.ForeignKey(
        'judge.Class', verbose_name=_('class'),
        related_name='instructor_verification_requests', on_delete=models.CASCADE,
        null=True, blank=True,
    )
    reason = models.TextField(verbose_name=_('reason'))
    time = models.DateTimeField(verbose_name=_('request time'), auto_now_add=True)
    state = models.CharField(max_length=1, verbose_name=_('state'), choices=STATES, default='P')
    reviewed_by = models.ForeignKey(
        Profile, verbose_name=_('reviewed by'), null=True, blank=True,
        related_name='reviewed_instructor_verifications', on_delete=models.SET_NULL,
    )
    reviewed_time = models.DateTimeField(verbose_name=_('reviewed time'), null=True, blank=True)
    # Track what this approval actually granted so revoke does not strip unrelated access.
    granted_class_admin = models.BooleanField(
        verbose_name=_('granted class admin'), default=False,
        help_text=_('True if approval added the user to class.admins.'),
    )
    granted_staff = models.BooleanField(
        verbose_name=_('granted staff'), default=False,
        help_text=_('True if approval flipped user.is_staff from False to True.'),
    )

    class Meta:
        verbose_name = _('instructor verification request')
        verbose_name_plural = _('instructor verification requests')
        ordering = ('-time',)

    def __str__(self):
        return 'InstructorVerification(%s, %s, class=%s)' % (
            self.user_id, self.state, self.request_class_id,
        )

    @property
    def target_label(self):
        org = self.organization
        klass = self.request_class
        if org is None and klass is None:
            return ''
        course = org.short_name or org.name if org is not None else ''
        if klass is None:
            return course
        session = klass.offering_label or klass.name
        if course:
            return '%s · %s' % (course, session)
        return session


# Backward-compatible alias.
TeacherVerificationRequest = InstructorVerificationRequest


class AdminNotification(models.Model):
    """Simple admin inbox messages for course/class join and instructor verification."""

    KIND_INSTRUCTOR_VERIFY = 'instructor_verify'
    KIND_TEACHER_VERIFY = KIND_INSTRUCTOR_VERIFY  # legacy alias
    KIND_COURSE_JOIN = 'course_join'
    KIND_CLASS_JOIN = 'class_join'
    KIND_CHOICES = (
        (KIND_INSTRUCTOR_VERIFY, _('Instructor verification')),
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
    instructor_verification = models.ForeignKey(
        InstructorVerificationRequest, null=True, blank=True, on_delete=models.CASCADE,
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
        if self.instructor_verification_id:
            return reverse('organization_instructor_requests') + '?status=P'
        return reverse('organization_notifications')

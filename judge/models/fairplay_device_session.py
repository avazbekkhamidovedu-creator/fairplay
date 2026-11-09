"""Durable native browser ownership and revocation records."""
import uuid
from django.conf import settings
from django.db import models


class FairPlayDeviceSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    contest = models.ForeignKey('judge.Contest', on_delete=models.PROTECT)
    participation = models.ForeignKey('judge.ContestParticipation', null=True, blank=True, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    browser_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default='active', choices=[
        ('active', 'Active'), ('finishing', 'Finishing'), ('completed', 'Completed'), ('released', 'Instructor unlocked'),
    ])
    lease_until = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)


class FairPlayDeviceSlot(models.Model):
    # A portable SQL uniqueness constraint, including MySQL: one active slot per account.
    user = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.PROTECT)
    session = models.OneToOneField(FairPlayDeviceSession, null=True, blank=True, on_delete=models.PROTECT)


class FairPlayDeviceAudit(models.Model):
    session = models.ForeignKey(FairPlayDeviceSession, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    reason = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

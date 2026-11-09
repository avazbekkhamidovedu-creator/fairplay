from django.core.exceptions import ValidationError
from django.db import transaction

from judge.models import ContestTag

PREFIX = 'fp-aud-'
MODES = {'classes': PREFIX + 'classes', 'organizations': PREFIX + 'orgs', 'selected': PREFIX + 'selected'}
CONFIRMED = PREFIX + 'confirmed'
SELECTED_ONLY = PREFIX + 'selected-only'


def audience_descriptor(contest):
    tags = set(contest.tags.filter(name__startswith=PREFIX).values_list('name', flat=True))
    modes = [mode for mode, tag in MODES.items() if tag in tags]
    class_ids = sorted(contest.classes.values_list('id', flat=True))
    organization_ids = sorted(contest.organizations.values_list('id', flat=True))
    mode = modes[0] if len(modes) == 1 else ('classes' if class_ids else 'organizations' if contest.is_organization_private else 'selected')
    valid = False
    if mode == 'classes':
        valid = bool(contest.is_organization_private and class_ids and organization_ids and
                     not contest.classes.exclude(organization_id__in=organization_ids).exists())
    elif mode == 'organizations':
        valid = bool(contest.is_organization_private and organization_ids and not class_ids)
    elif mode == 'selected':
        valid = bool(contest.is_private and not contest.is_organization_private and
                     contest.private_contestants.exists())
    if contest.is_private and not contest.private_contestants.exists():
        valid = False
    selected_only = SELECTED_ONLY in tags
    valid = valid and selected_only == bool(contest.is_private)
    return {'mode': mode, 'confirmed': len(modes) == 1 and CONFIRMED in tags and valid,
            'class_ids': class_ids, 'organization_ids': organization_ids, 'selected_only': selected_only}


def confirm_audience(contest, mode):
    if mode not in MODES:
        raise ValidationError('Choose classes, organizations or selected students for the FairPlay audience.')
    current = set(contest.tags.filter(name__startswith=PREFIX).values_list('id', flat=True))
    names = [MODES[mode], CONFIRMED] + ([SELECTED_ONLY] if contest.is_private else [])
    tags = [ContestTag.objects.get_or_create(name=name, defaults={'color': '#64748b', 'description': ''})[0]
            for name in names]
    with transaction.atomic():
        contest.tags.remove(*current)
        contest.tags.add(*tags)
        if not audience_descriptor(contest)['confirmed']:
            raise ValidationError('Confirm a non-empty matching FairPlay audience; classes cannot fall back to organizations.')

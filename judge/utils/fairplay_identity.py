from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def installation_id():
    value = getattr(settings, 'FAIRPLAY_INSTALLATION_ID', '')
    try:
        normalized = str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ImproperlyConfigured('Set FAIRPLAY_INSTALLATION_ID to the stable UUID agreed with FairPlay.') from None
    if normalized != value:
        raise ImproperlyConfigured('FAIRPLAY_INSTALLATION_ID must be a canonical UUID.')
    return normalized

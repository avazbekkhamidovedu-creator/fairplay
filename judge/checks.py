"""System checks of the judge app (registered in ``JudgeAppConfig.ready``)."""
from django.conf import settings
from django.core.checks import Error, Tags, register

PER_PROCESS_CACHES = (
    'django.core.cache.backends.locmem.LocMemCache',
    'django.core.cache.backends.dummy.DummyCache',
    'django.core.cache.backends.filebased.FileBasedCache',
)


@register(Tags.security, Tags.caches, deploy=False)
def fairplay_bridge_check(app_configs=None, **kwargs):
    """The bridge needs a shared cache, for the nonces and hand-in records, and an https FAIRPLAY_URL."""
    from judge.utils.fairplay import fairplay_tokens, fairplay_url_insecure
    if not fairplay_tokens():
        return []
    errors = []
    backend = (getattr(settings, 'CACHES', {}) or {}).get('default', {}).get(
        'BACKEND', 'django.core.cache.backends.locmem.LocMemCache')
    if backend in PER_PROCESS_CACHES:
        errors.append(Error(
            'The FairPlay bridge needs a cache shared by all workers (e.g. Redis); the default cache is %s.' % backend,
            hint='A per-process cache lets a captured FairPlay request be replayed to another worker and '
                 'loses FairPlay hand-in records. Configure CACHES["default"] with Redis (or Memcached), '
                 'or leave FAIRPLAY_TOKEN empty.',
            id='judge.E101',
        ))
    if not (getattr(settings, 'FAIRPLAY_URL', '') or '').strip():
        errors.append(Error(
            'FAIRPLAY_TOKEN is set but FAIRPLAY_URL is empty.',
            hint="Set FAIRPLAY_URL to the FairPlay server's base URL, or leave FAIRPLAY_TOKEN empty.",
            id='judge.E103',
        ))
    elif fairplay_url_insecure():
        errors.append(Error(
            'FAIRPLAY_URL uses http:// to another machine.',
            hint='Use https://. For a local test set-up only, set FAIRPLAY_ALLOW_INSECURE = True.',
            id='judge.E102',
        ))
    return errors

# Appended to dmoj/local_settings.py by scripts/demo/prepare.sh (Dockerfile.demo only).
import os as _os  # noqa: E402

# Off unless CLASSROOM_DEBUG=1 (only the seed step at start sets it).
DEBUG = _os.environ.get('CLASSROOM_DEBUG', '0') == '1'
ALLOWED_HOSTS = ['*']
CSRF_TRUSTED_ORIGINS = ['https://*.onrender.com', 'http://localhost:8000', 'http://127.0.0.1:8000'] + [
    o for o in _os.environ.get('DEMO_TRUSTED_ORIGINS', '').split(',') if o]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Static files: served by WhiteNoise from STATIC_ROOT (collected at build time), gzipped and
# cacheable by the browser, so a page reload does not download several MB again. Files are
# served one by one (no django-compressor bundles: those are written at run time, after
# WhiteNoise has indexed STATIC_ROOT).
COMPRESS_ENABLED = False
# GZip for the HTML too (the assignment editor page is ~220 KB); Django 4.2's GZipMiddleware
# pads responses against BREACH.
MIDDLEWARE = MIDDLEWARE[:1] + ('whitenoise.middleware.WhiteNoiseMiddleware',  # noqa: F821
                               'django.middleware.gzip.GZipMiddleware') + MIDDLEWARE[1:]  # noqa: F821
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
WHITENOISE_MAX_AGE = 3600
# One persistent DB connection per thread instead of a new one per request.
DATABASES['default']['CONN_MAX_AGE'] = 60  # noqa: F821

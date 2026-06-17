# Local test settings: run the test suite without the MySQL server that
# dmoj/local_settings.py points at.
#
#   DJANGO_SETTINGS_MODULE=dmoj.test_settings python manage.py test judge
#
# Several historical migrations (0085, 0089, 0090) contain MySQL-only SQL, so
# migrations for the judge app are disabled and the test schema is created
# directly from the current models (Django's --run-syncdb behavior).
from dmoj.settings import *  # noqa: F401, F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

MIGRATION_MODULES = {
    'judge': None,
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    },
}

# Tests that render site templates need the statici18n bundle. Generate it once:
#   DJANGO_SETTINGS_MODULE=dmoj.test_settings python manage.py compilejsi18n
STATIC_ROOT = os.path.join(BASE_DIR, '.test_static')  # noqa: F405
STATICI18N_ROOT = STATIC_ROOT

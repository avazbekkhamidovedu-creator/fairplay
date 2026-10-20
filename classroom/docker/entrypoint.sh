#!/bin/sh
# Role dispatcher for the DKUOJ site image.
# Usage: entrypoint <web|celery|bridge> ...  (default: web)
set -e

role="${1:-web}"
shift 2>/dev/null || true

case "$role" in
  web)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    python manage.py compilemessages
    python manage.py compilejsi18n
    # COMPRESS_OFFLINE installs may also need: python manage.py compress --force
    # Worker model: gthread (threads per worker) by default. With the mysqlclient driver a
    # gevent worker blocks on every query and queues requests unfairly; gthread cut the
    # worst-case latency from 28 s to 9 s in docs/classroom/reports/load-test.md. Pair it
    # with DATABASES['default']['CONN_MAX_AGE'] = 60 in local_settings.py. Rule of thumb:
    # GUNICORN_WORKERS = 2 x CPU cores, GUNICORN_THREADS = 4 (docs/classroom/reports/scaling.md).
    # GUNICORN_WORKER_CLASS=gevent still works for a deploy that wants the old model.
    exec gunicorn dmoj.wsgi \
        --bind "0.0.0.0:${GUNICORN_PORT:-8000}" \
        --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
        --workers "${GUNICORN_WORKERS:-4}" \
        --threads "${GUNICORN_THREADS:-4}" \
        --timeout "${GUNICORN_TIMEOUT:-120}" \
        "$@"
    ;;
  celery)
    exec celery -A dmoj_celery worker --loglevel="${CELERY_LOGLEVEL:-info}" "$@"
    ;;
  bridge)
    exec python dmoj_bridge_async.py "$@"
    ;;
  *)
    # Fall through: run an arbitrary command (e.g. manage.py shell).
    exec "$role" "$@"
    ;;
esac

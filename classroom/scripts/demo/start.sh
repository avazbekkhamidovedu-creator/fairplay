#!/bin/sh
# Runtime start of Dockerfile.demo: database + Redis, fresh demo data, web server on $PORT.
set -e
cd /app
sh scripts/demo/services.sh
CLASSROOM_DEBUG=1 python manage.py seed_dev_data
echo "[classroom] demo ready (users admin, prof_li, ta_wang, ab101..ab105; password fairplay-dev)"
# gunicorn with threads: several requests at once (runserver handles them one by one in
# practice on a tiny instance). WhiteNoise serves /static/ (see scripts/demo/settings.py).
exec gunicorn dmoj.wsgi --bind "0.0.0.0:${PORT:-10000}" --worker-class gthread \
    --workers "${DEMO_WORKERS:-1}" --threads "${DEMO_THREADS:-8}" --timeout 120 --access-logfile -

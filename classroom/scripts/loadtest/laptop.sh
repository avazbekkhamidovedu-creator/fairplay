#!/bin/bash
# Run one load test on your own computer with Docker (docs/classroom/reports/scaling.md).
#
#   bash scripts/loadtest/laptop.sh students    # 300 students sit an exam while a teacher grades
#   bash scripts/loadtest/laptop.sh teachers    # 500 teachers create, edit and grade at once
#
# Uses docker-compose.local.yml (MariaDB, Redis, the app image) and a separate database
# classroom_lt, so the demo data you click around in is not touched. Inside the web container it
# seeds the test data, starts gunicorn (production settings: DEBUG off, gthread, persistent DB
# connections) on port 8201 and runs Locust against it. Results: summary on screen and the CSV
# files in RESULTS_DIR (default: ./loadtest-results/<test>-<time>).
#
# Knobs (env): LT_USERS, LT_RAMP (seconds), LT_DURATION (e.g. 6m), LT_THINK_MIN / LT_THINK_MAX
# (teachers), GUNICORN_WORKERS (default: 2 x the container's CPUs), RESULTS_DIR.
set -euo pipefail
TEST=${1:-students}
case "$TEST" in
  students) USERS=${LT_USERS:-300}; RAMP=${LT_RAMP:-120}; DURATION=${LT_DURATION:-6m} ;;
  teachers) USERS=${LT_USERS:-500}; RAMP=${LT_RAMP:-120}; DURATION=${LT_DURATION:-6m} ;;
  *) echo "usage: $0 students|teachers" >&2; exit 2 ;;
esac
cd "$(dirname "$0")/../.."
DC="docker compose -f docker-compose.local.yml"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT=${RESULTS_DIR:-./loadtest-results/$TEST-$STAMP}
mkdir -p "$OUT"
log() { printf '\n[loadtest] %s\n' "$*"; }

log "starting the database, Redis and the app (first time: the image build takes 10-20 minutes)"
$DC up -d --build
until curl -s -o /dev/null http://127.0.0.1:8000/; do sleep 3; done

LTENV=(-e CLASSROOM_DB_NAME=classroom_lt -e CLASSROOM_DEBUG=0 -e CLASSROOM_DB_CONN_MAX_AGE=60
       -e CLASSROOM_ALLOWED_HOSTS=127.0.0.1,localhost)
log "preparing the load-test database classroom_lt"
$DC exec -T db mariadb -uroot -pclassroom -e \
  "CREATE DATABASE IF NOT EXISTS classroom_lt CHARACTER SET utf8mb4; GRANT ALL ON classroom_lt.* TO 'classroom'@'%';"
$DC exec -T web sh -c 'python -c "import locust" 2>/dev/null || pip install -q locust psutil'
$DC exec -T "${LTENV[@]}" web sh -c '
  set -e
  python manage.py migrate --noinput -v 0
  python manage.py loaddata navbar language_small demo -v 0
  python manage.py collectstatic --noinput -v 0
  python manage.py compilejsi18n -v 0 >/dev/null'

log "seeding the $TEST test data"
if [ "$TEST" = students ]; then
  $DC exec -T "${LTENV[@]}" web python scripts/loadtest/seed.py --students "$USERS" --reset
  LOCUSTFILE=scripts/loadtest/locustfile.py
  LOCUST_USERS=$((USERS + 1))  # + the grading teacher
else
  $DC exec -T "${LTENV[@]}" web python scripts/loadtest/seed_teacher_load.py --teachers "$USERS" --reset
  LOCUSTFILE=scripts/loadtest/locust_teachers.py
  LOCUST_USERS=$USERS
fi
$DC exec -T redis redis-cli FLUSHALL >/dev/null

CPUS=$($DC exec -T web nproc | tr -d '\r')
WORKERS=${GUNICORN_WORKERS:-$((CPUS * 2))}
log "starting gunicorn: $WORKERS workers x 4 threads on $CPUS CPUs"
$DC exec -T web sh -c 'test -f /tmp/lt-gunicorn.pid && kill $(cat /tmp/lt-gunicorn.pid) 2>/dev/null; sleep 1; true'
$DC exec -d "${LTENV[@]}" web gunicorn dmoj.wsgi --bind 127.0.0.1:8201 --worker-class gthread \
  --workers "$WORKERS" --threads 4 --timeout 120 --pid /tmp/lt-gunicorn.pid --log-file /tmp/lt-gunicorn.log
$DC exec -T web sh -c 'for i in $(seq 1 60); do python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8201/\")" 2>/dev/null && exit 0; sleep 2; done; exit 1'

SPAWN=$(awk "BEGIN { printf \"%.3f\", $LOCUST_USERS / $RAMP }")
log "running Locust: $LOCUST_USERS users, ramp ${RAMP}s, for $DURATION (watch CPU in Activity Monitor)"
$DC exec -T -e LT_STUDENTS="$USERS" -e LT_THINK_MIN="${LT_THINK_MIN:-10}" -e LT_THINK_MAX="${LT_THINK_MAX:-30}" web \
  sh -c "rm -rf /tmp/lt-out && mkdir -p /tmp/lt-out && locust -f $LOCUSTFILE --headless -u $LOCUST_USERS -r $SPAWN \
         -t $DURATION -H http://127.0.0.1:8201 --csv /tmp/lt-out/stats --only-summary > /tmp/lt-out/summary.txt 2>&1; true"

$DC cp web:/tmp/lt-out/. "$OUT/" >/dev/null
$DC cp web:/tmp/lt-gunicorn.log "$OUT/gunicorn.log" >/dev/null 2>&1 || true
$DC exec -T web sh -c 'kill $(cat /tmp/lt-gunicorn.pid) 2>/dev/null; true'

log "results ($OUT)"
grep -A40 "Response time percentiles" "$OUT/summary.txt" | head -32 || tail -40 "$OUT/summary.txt"
grep -E "Aggregated" "$OUT/summary.txt" | head -1 || true
grep -A12 "Error report" "$OUT/summary.txt" || echo "No errors."
log "done. Files: $OUT"

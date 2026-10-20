#!/bin/bash
# One teacher load-test round against an already running app server (docs/classroom/reports/scaling.md).
#
#   bash scripts/loadtest/run_teachers.sh OUTDIR [users] [ramp_seconds] [duration]
#
# Seed first: python scripts/loadtest/seed_teacher_load.py --teachers 500 --reset
# Env: LT_LOCUST (locust binary), LT_MON_PY (python with psutil), LT_HOST (default
# http://127.0.0.1:8201), LT_THINK_MIN / LT_THINK_MAX (seconds between a teacher's steps).
set -euo pipefail
OUT=${1:?outdir}; USERS=${2:-500}; RAMP=${3:-120}; DURATION=${4:-6m}
HERE=$(cd "$(dirname "$0")" && pwd)
LOCUST=${LT_LOCUST:-locust}
HOST=${LT_HOST:-http://127.0.0.1:8201}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
SPAWN=$(python3 -c "print(round($USERS / $RAMP, 3))")
"${LT_MON_PY:-python3}" "$HERE/monitor.py" "$OUT/monitor.csv" --redis-port "${LT_REDIS_PORT:-6379}" &
MON=$!
trap 'kill $MON 2>/dev/null || true' EXIT
"$LOCUST" -f "$HERE/locust_teachers.py" --headless -u "$USERS" -r "$SPAWN" -t "$DURATION" -H "$HOST" \
  --csv "$OUT/stats" --csv-full-history --only-summary > "$OUT/summary.txt" 2>&1 || true
kill $MON 2>/dev/null || true
tail -60 "$OUT/summary.txt"

#!/bin/bash
# One-shot local demo of the student access-code sign-in. Needs no DKU OJ and never touches
# server/static or a real database: everything lives in one temp folder.
#
#   server/tools/run_demo.sh            # exam opens in 2 minutes
#   OPENS_IN=5 PORT=8100 FAIRPLAY_DEMO_PYTHON=/path/to/venv/bin/python server/tools/run_demo.sh
#
# Starts the server in the background on 127.0.0.1:$PORT with FAIRPLAY_DEV=1,
# FAIRPLAY_REQUIRE_APP=0 and FAIRPLAY_BROWSER_GATE=off, so a plain browser can open /student
# (the web page then uses a stand-in for the app). The exam is opened by the server's own DKU OJ
# loop at its start time; DKUOJ_URL points at a closed loopback port only so the loop runs.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$(dirname "$HERE")"
ROOT="$(dirname "$SERVER")"
PY="${FAIRPLAY_DEMO_PYTHON:-}"
[ -n "$PY" ] || { [ -x "$SERVER/.venv/bin/python" ] && PY="$SERVER/.venv/bin/python" || PY="$(command -v python3)"; }
PORT="${PORT:-8100}"
OPENS_IN="${OPENS_IN:-2}"
WORK="${DEMO_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/fairplay-demo.XXXXXX")}"
mkdir -p "$WORK"

if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  echo "Something already listens on port $PORT. Stop it or set PORT=<other>." >&2
  exit 1
fi
[ ! -e "$WORK/fairplay.db" ] || { echo "$WORK/fairplay.db exists; pick a new DEMO_DIR." >&2; exit 1; }

echo "==> Building the web into $WORK/static (server/static is not touched)"
cd "$ROOT/web"
[ -d node_modules ] || npm ci >/dev/null
npx vite build --outDir "$WORK/static" --emptyOutDir >"$WORK/web-build.log" 2>&1 || { tail -20 "$WORK/web-build.log"; exit 1; }

export FAIRPLAY_DEV=1 FAIRPLAY_REQUIRE_APP=0 FAIRPLAY_BROWSER_GATE=off FAIRPLAY_SINGLE_PROCESS=1
export FAIRPLAY_SEED_DEMO=1
export PROCTORA_SECRET="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="sqlite+aiosqlite:///$WORK/fairplay.db"
export FAIRPLAY_UPLOADS_DIR="$WORK/uploads"
export PROCTORA_STATIC_DIR="$WORK/static"
export DKUOJ_URL="http://127.0.0.1:9"
export DKUOJ_TOKEN="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(40))')"
export DKUOJ_SYNC_SECONDS=3600

cd "$SERVER"
echo "==> Seeding the demo exam"
"$PY" tools/seed_demo.py --db "$WORK/fairplay.db" --pdf "$WORK/access-codes.pdf" \
  --opens-in-minutes "$OPENS_IN" 2>"$WORK/seed.log" | tee "$WORK/codes.txt"

echo "==> Starting the server on port $PORT"
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$WORK/server.log" 2>&1 &
echo $! >"$WORK/server.pid"
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  kill -0 "$(cat "$WORK/server.pid")" 2>/dev/null || { tail -20 "$WORK/server.log"; exit 1; }
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null || { tail -20 "$WORK/server.log"; exit 1; }

cat <<INFO

Student page   http://127.0.0.1:$PORT/student      (type a code from $WORK/codes.txt)
Teacher page   http://127.0.0.1:$PORT/teacher      login teacher / teacher123
Codes table    $WORK/codes.txt
Code sheet     $WORK/access-codes.pdf
The exam opens by itself about $OPENS_IN minute(s) after the seed; until then students see "Waiting for the exam to open".
Server PID     $(cat "$WORK/server.pid")   log $WORK/server.log   stop: kill $(cat "$WORK/server.pid")
INFO

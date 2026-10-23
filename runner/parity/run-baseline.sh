#!/usr/bin/env bash
# Judge every answer on the real DMOJ judge and write results/dmoj-baseline.json.
# Copies the kit to /srv/classroom-judge/parity so the unprivileged judge user can read it.
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
DEST=/srv/classroom-judge/parity
[ -d problems ] || python3 "$KIT/gen.py"
rm -rf "$DEST" && mkdir -p "$DEST/results"
cp -r "$KIT/problems" "$KIT/answers" "$KIT/parity-judge.yml" "$KIT/judge_dmoj.py" "$DEST/"
chown -R classroom-judge:classroom-judge "$DEST"
cd "$DEST"
setpriv --reuid=classroom-judge --regid=classroom-judge --clear-groups \
  env -u JAVA_TOOL_OPTIONS HOME=/srv/classroom-judge \
  /srv/classroom-judge/venv/bin/python judge_dmoj.py --config parity-judge.yml --kit "$DEST" \
  --out "$DEST/results/dmoj-baseline.json" "$@"
mkdir -p "$KIT/results" && cp "$DEST/results/dmoj-baseline.json" "$KIT/results/"

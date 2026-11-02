#!/usr/bin/env bash
set -euo pipefail
MODE=${1:---check}
IDENTITY=${2:-}
case "$MODE" in --check|--dry-run|--apply) ;; *) echo 'Usage: sudo bash apply-professor.sh --check OR --apply UUID'; exit 2;; esac
[ "$(id -u)" = 0 ] || { echo 'Run on the professor VM with sudo.'; exit 1; }
[ "$(hostname -s)" = dku-vcm-4158 ] || { echo 'Wrong host: expected professor VM dku-vcm-4158.'; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=/srv/dkuoj-build-20261101-113155/src
ORIGIN=/home/sd540/DKUOJ
ENVF=/srv/dkuoj/.env
SETTINGS=/srv/dkuoj/local_settings.py
EXPECTED=dkuoj-local:fairplayui-20261101-113155
PATCH="$HERE/0028-FairPlay-launch-and-numbered-history.patch"
LIFECYCLE_PATCH="$HERE/0029-FairPlay-installation-audience-lifecycle.patch"
[ -f "$LIFECYCLE_PATCH" ] && [ -f "$HERE/baseline-0029.json" ] || { echo "Lifecycle patch/manifest missing."; exit 1; }
[ -d "$SRC" ] && [ -f "$ENVF" ] && [ -f "$SETTINGS" ] && [ -f "$PATCH" ] || { echo 'Missing expected source/settings/bundle; stopped.'; exit 1; }
PREV=$(sed -n 's/^DKUOJ_IMAGE=//p' "$ENVF")
[ "$PREV" = "$EXPECTED" ] || { echo 'The deployed image changed since this patch was prepared. Rebase/check before applying.'; exit 1; }
for C in dkuoj-web-1 dkuoj-celery-1; do
    [ "$(docker inspect --format '{{.Config.Image}}' "$C")" = "$PREV" ] || { echo "$C does not use the expected image; stopped."; exit 1; }
done
python3 - "$HERE/baseline.json" "$SRC" "$SETTINGS" <<'PY'
import ast, hashlib, json, pathlib, sys
manifest, root, settings = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
for name, digest in json.loads(manifest.read_text()).items():
    path = root / name
    if digest is None:
        assert not path.exists(), 'A new patch file already exists: ' + name
    else:
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Baseline changed: ' + name
values = []
for node in ast.parse(settings.read_text()).body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FAIRPLAY_URL' for t in node.targets):
        values.append(ast.literal_eval(node.value))
assert values == ['https://dku.speakupai.uz'], 'Expected the verified beta FairPlay domain; no setting changed.'
print('PASS: deployed source matches patch baseline; beta domain is unchanged.')
PY
(cd "$SRC" && git apply --check "$PATCH")
curl -fsS --max-time 15 https://dku.speakupai.uz/api/health | python3 -c 'import json,sys; assert json.load(sys.stdin).get("ok"); print("PASS: FairPlay domain health.")'
echo 'CHECK PASSED. This bundle contains only DKU OJ changes; it does not change bridge settings or tokens.'
[ "$MODE" = --apply ] || exit 0
[ -n "$IDENTITY" ] || { echo 'Supply the stable UUID already configured on the FairPlay server: sudo bash apply-professor.sh --apply <UUID>'; exit 1; }
python3 - "$IDENTITY" "$SETTINGS" <<'IDENTITY_CHECK'
import ast, pathlib, sys, uuid
identity, path = sys.argv[1], pathlib.Path(sys.argv[2])
assert str(uuid.UUID(identity)) == identity and uuid.UUID(identity).int, 'Use the canonical non-zero UUID agreed with FairPlay.'
for node in ast.parse(path.read_text()).body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FAIRPLAY_INSTALLATION_ID' for t in node.targets):
        current = ast.literal_eval(node.value)
        assert current in ('', identity), 'Existing installation UUID differs; refusing identity rotation.'
print('Operator installation UUID validated; no identity generated or changed.')
IDENTITY_CHECK
printf 'Existing source: dku-vcm-4158; installation UUID: %s\n' "$IDENTITY"
read -r -p 'Approve this exact existing-installation mapping. Type APPLY: ' CONFIRMED_ID
[ "$CONFIRMED_ID" = APPLY ] || { echo 'Identity mapping was not approved; nothing changed.'; exit 1; }
docker exec dkuoj-web-1 python manage.py shell -c 'from django.utils import timezone; from judge.models import Contest; n=timezone.now(); print("Scheduled live contests:", Contest.objects.filter(start_time__lte=n,end_time__gte=n).count())'
read -r -p 'Only when NO student is taking an exam, type APPLY to build/test/restart DKU OJ: ' ANSWER
[ "$ANSWER" = APPLY ] || { echo 'Cancelled; nothing changed.'; exit 0; }
FREE=$(df --output=avail -BG /srv | tail -1 | tr -dc 0-9)
[ "$FREE" -ge 5 ] || { echo 'Need at least 5 GB free space.'; exit 1; }
TS=$(date +%Y%m%d-%H%M%S)
B="/srv/dkuoj-fairplay-launch-$TS"
TAG="dkuoj-local:fairplaylaunch-$TS"
NET="dkuoj-fplaunch-test-$TS"
DB="dkuoj-fplaunch-db-$TS"
TEST="dkuoj-fplaunch-tests-$TS"
BACKUP="$ENVF.before-launch-$TS"
SWITCHED=0
FINISHED=0
DB_STARTED=0
finish() {
    local rc=$?
    if [ "$DB_STARTED" = 1 ]; then docker stop "$DB" >/dev/null 2>&1 || true; fi
    if [ "$SWITCHED" = 1 ] && [ "$FINISHED" != 1 ]; then
        local restored=0 code c matches
        if cp "$BACKUP" "$ENVF" && (cd "$ORIGIN" && docker compose --env-file "$ENVF" up -d --no-deps web celery); then
            for _ in $(seq 1 60); do
                matches=1
                for c in dkuoj-web-1 dkuoj-celery-1; do
                    [ "$(docker inspect --format '{{.Config.Image}}|{{.State.Running}}' "$c" 2>/dev/null)" = "$PREV|true" ] || matches=0
                done
                code=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ || true)
                case "$code" in 200|301|302) if [ "$matches" = 1 ]; then restored=1; break; fi;; esac
                sleep 2
            done
        fi
        if [ "$restored" = 1 ]; then
            echo "Verification failed. Original image restored: $PREV; both containers running and site healthy. All artifacts retained."
        else
            echo 'FAILURE: rollback did not restore verified healthy containers. Manual recovery is required.'
            echo "Recovery: sudo cp '$BACKUP' '$ENVF' && cd '$ORIGIN' && sudo docker compose --env-file '$ENVF' up -d --no-deps web celery"
        fi
        [ "$rc" != 0 ] || rc=1
    fi
    return "$rc"
}
trap finish EXIT
mkdir -p "$B/src"
tar --exclude=.git --exclude=node_modules --exclude=venv --exclude=.venv \
    --exclude='.env*' --exclude='*credentials*' --exclude='*.key' --exclude='*.pem' \
    --exclude=dmoj/local_settings.py --exclude=media --exclude=submission_files --exclude=uploads \
    -C "$SRC" -cf - . | tar -C "$B/src" -xf -
(cd "$B/src" && git apply "$PATCH")
python3 - "$HERE/baseline-0029.json" "$B/src" <<'LIFECYCLE_BASELINE'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[2])
for name, digest in json.loads(pathlib.Path(sys.argv[1]).read_text()).items():
    path = root / name
    assert not path.exists() if digest is None else path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Lifecycle baseline mismatch: ' + name
LIFECYCLE_BASELINE
(cd "$B/src" && git apply --check "$LIFECYCLE_PATCH" && git apply "$LIFECYCLE_PATCH")
docker build --target web -t "$TAG" "$B/src"
docker image inspect mariadb:11 >/dev/null 2>&1 || docker pull mariadb:11
docker network create "$NET" >/dev/null
python3 - "$B/test-settings.py" <<'PY'
import pathlib, secrets, sys
path = pathlib.Path(sys.argv[1])
password = secrets.token_urlsafe(24)
path.write_text('DEBUG = True\nSECRET_KEY = "isolated-launch-test-only"\n'
                'DATABASES = ' + repr({'default': {'ENGINE': 'django.db.backends.mysql',
                'NAME': 'launch_isolated', 'USER': 'root', 'PASSWORD': password,
                'HOST': 'launch-db', 'PORT': '3306', 'OPTIONS': {'charset': 'utf8mb4'}}}) + '\n'
                'FAIRPLAY_INSTALLATION_ID = \"eaab2f6e-7072-4c86-968c-a9c624cc676d\"\n'
                'CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}\n'
                'STATIC_ROOT = "/tmp/fairplay-launch-static"\nSTATICI18N_ROOT = STATIC_ROOT\n'
                'DMOJ_SUBMISSION_FILE_ROOT = "/tmp/fairplay-launch-submissions"\n'
                'MEDIA_ROOT = "/tmp/fairplay-launch-media"\n'
                'PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]\n'
                'COMPRESS_ENABLED = False\nEVENT_DAEMON_USE = False\n')
path.chmod(0o600)
env = path.with_name('test-db.env')
env.write_text('MARIADB_ROOT_PASSWORD=' + password + '\nMARIADB_DATABASE=launch_isolated\n')
env.chmod(0o600)
PY
docker run -d --name "$DB" --network "$NET" --network-alias launch-db \
    --memory=768m --cpus=1 --pids-limit=256 --env-file "$B/test-db.env" mariadb:11 >/dev/null
DB_STARTED=1
READY=0
for _ in $(seq 1 60); do
    if docker exec "$DB" sh -c 'mariadb --protocol=tcp -h127.0.0.1 -uroot -p"$MARIADB_ROOT_PASSWORD" --connect-timeout=3 --batch --skip-column-names -e "SELECT 1"' >/dev/null 2>&1; then READY=1; break; fi
    sleep 2
done
[ "$READY" = 1 ] || { echo 'Isolated test database did not become ready. Nothing live changed.'; exit 1; }
docker run --name "$TEST" --network "$NET" --memory=1g --cpus=1 \
    --mount "type=bind,src=$B/test-settings.py,dst=/app/dmoj/local_settings.py,readonly" \
    --entrypoint sh "$TAG" -c 'python manage.py collectstatic --noinput && python manage.py compilejsi18n && python manage.py test judge.views.tests.test_fairplay_launch judge.utils.tests.test_fairplay_revision judge.views.tests.test_seb judge.views.tests.test_fairplay_hiding judge.views.tests.test_fairplay_lifecycle --noinput --keepdb'
docker run --name "dkuoj-fplaunch-check-$TS" --network "$NET" --memory=1g --cpus=1 \
    --mount "type=bind,src=$B/test-settings.py,dst=/app/dmoj/local_settings.py,readonly" \
    --entrypoint python "$TAG" manage.py check
cp -p "$ENVF" "$BACKUP"
chmod 600 "$BACKUP"
echo "Backup: $BACKUP"
echo "Rollback: sudo cp '$BACKUP' '$ENVF' && cd '$ORIGIN' && sudo docker compose --env-file '$ENVF' up -d --no-deps web celery"
python3 - "$SETTINGS" "$IDENTITY" "$TS" <<'PROVISION_IDENTITY'
import ast, pathlib, shutil, sys
path, identity, timestamp = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(); present = False
for node in ast.parse(text).body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FAIRPLAY_INSTALLATION_ID' for t in node.targets):
        current = ast.literal_eval(node.value)
        assert current in ('', identity), 'Existing identity changed during the build; stopped.'
        present |= current == identity
if not present:
    backup = path.with_name(path.name + '.before-installation-' + timestamp)
    shutil.copy2(path, backup); backup.chmod(0o600)
    path.write_text(text.rstrip() + '\nFAIRPLAY_INSTALLATION_ID = ' + repr(identity) + '\n')
print('Stable installation UUID provisioned once; token and beta URL unchanged.')
PROVISION_IDENTITY
SWITCHED=1
python3 - "$ENVF" "$TAG" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1]); lines = path.read_text().splitlines(keepends=True)
assert sum(line.startswith('DKUOJ_IMAGE=') for line in lines) == 1
path.write_text(''.join('DKUOJ_IMAGE=' + sys.argv[2] + '\n' if line.startswith('DKUOJ_IMAGE=') else line for line in lines))
PY
(cd "$ORIGIN" && docker compose --env-file "$ENVF" up -d --no-deps web celery)
HEALTHY=0
for _ in $(seq 1 60); do
    CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ || true)
    case "$CODE" in 200|301|302) HEALTHY=1; break;; esac
    sleep 2
done
[ "$HEALTHY" = 1 ] || { echo 'New image failed the site health check.'; exit 1; }
for C in dkuoj-web-1 dkuoj-celery-1; do
    docker exec -e "FP_EXPECTED_INSTALLATION=$IDENTITY" "$C" python manage.py shell -c 'import os; from django.conf import settings; from django.urls import reverse; assert settings.FAIRPLAY_INSTALLATION_ID == os.environ["FP_EXPECTED_INSTALLATION"]; assert settings.FAIRPLAY_URL == "https://dku.speakupai.uz"; assert reverse("contest_fairplay_launch",args=["probe"]); print("PASS: launch route and unchanged beta connection")'
done
FINISHED=1
echo "PASS: DKU OJ updated to $TAG. First/second hand-ins retained; latest labelled."
echo 'No FairPlay application source transferred. Bridge token, beta URLs and schema unchanged; installation UUID explicitly provisioned.'
echo "Installation UUID: $IDENTITY"
echo "Source endpoint: http://10.200.31.64/api/fairplay/v1/installation?installation_id=$IDENTITY"
echo 'Automatic app launch requires the new Mac/Windows release; v0.7.8 users have manual-open instructions.'
echo "Build, stopped isolated test DB, test containers and network retained at $B / $NET. Nothing deleted."

# Deploy runbook: FairPlay server on the Duke VM, DKU OJ patches, release

Drafted on 2026-10-30 from read-only inspection of the VM and the code. The commands were not run: check each one before use. Nothing in this file holds a secret; the VM env file holds them (names only below).

## Facts found on the VM (read-only)

- FairPlay runs as user `fairplay` (systemd user unit, linger on), single uvicorn process on 0.0.0.0:8200, release `rel-20261029-140057`; nginx serves 443 for dku.speakupai.uz (Let's Encrypt, expires 2027-01-26) and for dku-vcm-4228.vm.duke.edu.
- `~/fairplay.env` names: PROCTORA_SECRET, FAIRPLAY_APP_KEY, DATABASE_URL, FAIRPLAY_SINGLE_PROCESS=1, WEB_CONCURRENCY=1, DKUOJ_URL (plain http), DKUOJ_TOKEN, DKUOJ_ALLOW_INSECURE=1, DKUOJ_SYNC_SECONDS, FAIRPLAY_INITIAL_USERS, FAIRPLAY_PUBLIC_URL, FAIRPLAY_ADMIN_TEST_CODE (was 6 characters on 2026-10-30; now 20 characters, which meets the 12-character minimum).
- Not set (all optional): FAIRPLAY_HOMEWORK_LOGIN, DKUOJ_PUBLIC_URL, HOMEWORK_*, BOX_PROFILE, REDIS_URL. (`DKUOJ_SETUP_ACCESS_CODE` is no longer read: the per-student exam access code was removed; delete the line if it is ever set.)
- No Redis, no backups, no crontab, no certificate renewal. sudo allows only `systemctl reload nginx`. /home has about 17 GB free. Postgres is local.

## Hard blocker before deploying

`FAIRPLAY_ADMIN_TEST_CODE` must be empty/unset or at least 12 characters, or the new server refuses to start (deploy.sh would roll back after its health check). The VM value is 20 characters today, so this is met; keep it at 12 or more if it is ever changed. To set a new value use (for example `python3 -c 'import secrets;print(secrets.token_urlsafe(18))'`) or delete the line.

## Order

1. **Back up (needs the owner).** `pg_dump -Fc "$U" > ~/pre-078.dump` on the VM (`U` = DATABASE_URL without `+asyncpg`), then copy it off the VM.
2. **Check the env (needs the owner).** The admin test code must stay at 12 or more characters. Leave FAIRPLAY_HOMEWORK_LOGIN unset for the first exams: it needs an https DKUOJ_PUBLIC_URL and the bridge is plain http today. Keep FAIRPLAY_SINGLE_PROCESS=1 and WEB_CONCURRENCY=1 (no Redis).
3. **Pre-flight** (read-only): env names present, secret length, admin code length, tokens 32+ characters, DKUOJ_URL http needs DKUOJ_ALLOW_INSECURE=1, service active, health OK, `previous.txt` present, disk space, certificate not within 30 days of expiry.
4. **Deploy the server** with `~/code/dku-vm-deploy/deploy.sh` (rsyncs `server/`, restarts, health check for 40 s, rolls back by itself on failure). Startup creates the new column (`exams.box_link`), the new tables (`homework_remember`, `oj_student_grants`) and swaps the `external_deliveries` unique key to `(attempt_id, revision)` under an advisory lock.
5. **Smoke test**: `/api/health` answers ok and now contains the key `homework_login` (null); `/api/app/version` answers; the journal has no tracebacks; teacher sign-in works; constraint `uq_delivery_attempt_revision` exists.
6. **DKU OJ patches (the professor, sudo).** `sudo bash apply.sh` applies every `.patch` in order (0013-0018 and 0019 on this branch; 0016, 0018 and 0020 are docs and are skipped). Before that in `/srv/dkuoj/local_settings.py`: FAIRPLAY_URL reachable from the OJ container, FAIRPLAY_TOKEN equal to the VM's DKUOJ_TOKEN, FAIRPLAY_OJ_PUBLIC_URL equal to DKUOJ_PUBLIC_URL (https) if homework login is used, and CACHES must be a cache shared by web and celery (Redis or memcached, not LocMem). Check: a contest with Lock = FairPlay syncs and appears on FairPlay.
7. **Release 0.7.8** (only on the owner's go): both repos carry the bump locally; push the release branches, then check the assets on the public `fairplay-download` repo (Mac, iPad and Windows must all be there).
8. **Account setup**: there is no access-code sheet any more. Students sign in with their class account (or DKU OJ for homework); nothing needs to be printed or handed out.

## Rollback

- Server: `ln -sfn "$(cat ~/previous.txt)" ~/current` and restart the user unit. The venv is shared, so package upgrades are not undone.
- Database: the new columns and tables are harmless to the old code. The one non-additive change is the `external_deliveries` key: after a rollback, hand-ins sent to DKU OJ break until the old key is back: `ALTER TABLE external_deliveries ADD CONSTRAINT external_deliveries_attempt_id_key UNIQUE (attempt_id);` (works only while no revision-2 rows exist; otherwise restore the dump).
- DKU OJ: apply.sh prints its own rollback line.
- Release: mark v0.7.7 Latest again on the download repo; do not raise MIN_APP_VERSION_*.

## Needs the owner or sudo (not for an agent)

- TLS for the DKU OJ bridge (plain http carries rosters, packages, exit codes and grades).
- Nightly backups (cron pg_dump, copied off the VM).
- Certificate renewal before 2027-01-26 (a certbot dry run, then a daily cron line with the deploy hook).
- Redis (sudo) and WEB_CONCURRENCY=2, after the first exams.
- Port 8200 listens on all interfaces with forwarded-allow-ips=*: see "Do this on the VM: bind port 8200 to localhost" below.
- The Duke Box host list: record a real Duke Box sign-in as a HAR, run `server/scripts/box_trace.py` on it and update `server/app/box_profiles.py` (the Duke profile is marked "confirm with HAR").

## Do this on the VM: bind port 8200 to localhost

Why: with `--host 0.0.0.0 --forwarded-allow-ips=*` anyone who can reach port 8200 directly can send a fake
`X-Forwarded-For`, which defeats every per-IP limit (lock registration, logins) and the school-network check.

As user `fairplay`, edit `~/.config/systemd/user/fairplay.service` (the `ExecStart` line):

    --host 0.0.0.0  ...  --forwarded-allow-ips=*
    -->
    --host 127.0.0.1  ...  --forwarded-allow-ips=127.0.0.1

then:

    systemctl --user daemon-reload && systemctl --user restart fairplay

Why it is safe: nginx proxies 443 to `127.0.0.1:8200` (same host), and the OJ calls `https://dku.speakupai.uz`
(through nginx), never `:8200`. Only call sites that use `<vm-ip>:8200` directly stop working; check
`grep -rn ":8200"` in the OJ settings and the apps' base URL first.

Verify:
- `ss -ltnp | grep 8200` shows `127.0.0.1:8200`, not `0.0.0.0:8200` or `*:8200`.
- From another machine, `curl -m 5 http://<vm-ip>:8200/api/health` fails (refused or times out).
- On the VM, `curl -s http://127.0.0.1:8200/api/health` and `curl -s https://dku.speakupai.uz/api/health` both answer.
- A signed OJ call and an app start still work.

Revert: put back `--host 0.0.0.0 ... --forwarded-allow-ips=*` in the unit, run the same
`daemon-reload` and `restart`.

## Go / no-go

GO: pre-flight shows no FAIL, health shows the `homework_login` key, no tracebacks, a DKU OJ contest syncs, one test exam on each platform locks, hands in and appears on DKU OJ, and the dump is off the VM. NO-GO: a failed health check or rollback, a delivery stuck in a non-sent status, the bridge answering 403 (token or clock), or a missing 0.7.8 asset.

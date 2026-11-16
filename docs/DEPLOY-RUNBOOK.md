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
- Port 8200 listens on all interfaces with forwarded-allow-ips=*: see "Do this on the VM: bind port 8200 to localhost" below (the server logs a warning at start while this is so).
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

## Behind nginx: uvicorn flags and proxy headers (pilot 0.8.8)

The only safe pair when nginx is in front: `--host 127.0.0.1 --forwarded-allow-ips=127.0.0.1` (also
`--timeout-keep-alive 75` so it is longer than nginx's upstream keep-alive of 60 s). nginx must **overwrite**
the header: `proxy_set_header X-Forwarded-For $remote_addr;` (never `$proxy_add_x_forwarded_for`). The server now
logs a startup warning when `--forwarded-allow-ips=*` (or `FORWARDED_ALLOW_IPS=*`) is combined with a public listener
(`0.0.0.0`). Do not add nginx `limit_req` keyed by IP: a whole campus shares one address. Set
`FAIRPLAY_LOGIN_SCHOOL_NETS=<campus egress CIDRs>` instead: the sign-in, homework-login (start/finish/exchange) and
class-login wrong-password limits are then much higher for those addresses (`Settings.ip_limit`), while the global
and per-account caps stay.

## Forced app updates (pilot 0.8.8)

Set on the server (nothing is set by this repo; the owner sets them, outside exam windows):

| variable | meaning |
|---|---|
| `MIN_APP_VERSION_MAC`, `MIN_APP_VERSION_WINDOWS` (`MIN_APP_VERSION_IPAD`) | oldest version allowed. Below it: no sign-in (`/api/auth/login` answers 426 to an app), no exam start, no beats; the app shows the update screen with **Update now** (not while locked) and the download link. A browser is never gated. `""` = no minimum. |
| `FAIRPLAY_LATEST_APP_VERSION_MAC` / `_WINDOWS` | newest published version (else read from GitHub) |
| `FAIRPLAY_APP_UPDATE_URL_MAC` / `_WINDOWS` | Mac: URL of `FairPlay-Mac.zip`; Windows: Velopack feed folder (`/app/windows/` or https on this host) |
| `FAIRPLAY_APP_UPDATE_SHA256_MAC` / `_WINDOWS` | SHA-256 of the Mac zip / of `FairPlay-Windows-Setup.exe` |
| `FAIRPLAY_APP_UPDATE_SETUP_URL_WINDOWS`, `FAIRPLAY_APP_UPDATE_PACKAGES_WINDOWS` | Windows setup link; JSON `{"<package>.nupkg": "<sha256>"}` |
| `FAIRPLAY_APP_UPDATE_GITHUB` | `owner/repo` of the download repo (default the public one) or `off`; `GITHUB_TOKEN` optional |

All empty = the server reads GitHub's latest release at most every 10 minutes and serves that. Format and rules:
docs/APP-UPDATE-CONTRACT.md. How a client below the minimum is moved: at launch the app asks `GET /api/app/update`
within seconds (not after the 6 h timer), and any 426 triggers an immediate check; the update installs by itself
when the Mac is not locked, not in a device test and not in an exam. To force everybody: publish the release, wait
for `latest_version` to show it (`curl -s https://<server>/api/app/update?platform=mac`), then raise
`MIN_APP_VERSION_*` to it (never before the release exists, or students are blocked without an update to take).
Rollback: lower or clear `MIN_APP_VERSION_*`.

## Client clocks (pilot 0.8.8)

A student's Mac or PC clock is no longer a requirement. The apps sign, seal and compare deadlines with the server's
clock (`X-Server-Time` / `Date`). The server judges every deadline on its own clock, never accepts a client time as
truth (`client_time` is only shown to the teacher next to the server's `server_time`), and notes a client whose time
is more than 5 minutes off as `clock_off` (beats) or `sealed_clock_off` / `sealed_clock_ahead` (sealed answers)
without refusing it. A sealed hand-in with a time before the attempt started is judged as of its arrival.
Attempts still writing 15 minutes after their own deadline are handed in by the server's periodic sweep
(`auto_submitted_deadline` event, marked late); sealed answers that arrive after that are kept as late answers.

## Several workers and Redis (example only)

`ops/fairplay.env.example` has the variables for Redis + 2 workers. Defaults are unchanged (one process, in-memory
store, `FAIRPLAY_SINGLE_PROCESS` guard). Switch outside exam windows; see server/NOTES.md for the pool sizes.

## Go / no-go

GO: pre-flight shows no FAIL, health shows the `homework_login` key, no tracebacks, a DKU OJ contest syncs, one test exam on each platform locks, hands in and appears on DKU OJ, and the dump is off the VM. NO-GO: a failed health check or rollback, a delivery stuck in a non-sent status, the bridge answering 403 (token or clock), or a missing 0.7.8 asset.

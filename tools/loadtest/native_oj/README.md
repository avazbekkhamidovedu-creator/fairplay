# Native OJ load test (synthetic Macs)

Simulates thousands of FairPlay Macs against a STAGING copy of DKU OJ and the FairPlay server.
One asyncio task per Mac, one shared httpx client. Timings are those of
`mac/Sources/Proctora/NativeRenewSchedule.swift`: renew every 20 s +/-20% (16-24 s), backoff
2/4/8/16 s capped at 20 with full jitter on timeout/429/5xx, Retry-After honoured up to 30 s,
hand-in page GET then renews at +0, +2, +5 s.

Per client: escrow `POST /api/native-exit/locks` (random 92-byte blob, HMAC signature over
`nativelock|<lock_id>|<device>`), OJ login, 3 page GETs, claim, renew loop, hand-in GET, 3 renews.

## Run

```
export LOADTEST_ALLOWED_HOSTS=oj-staging.example.edu,fp-staging.example.edu
export LOADTEST_APP_KEY=...        # staging FAIRPLAY_APP_KEY
export LOADTEST_INGRESS_KEY=...    # staging FAIRPLAY_NATIVE_INGRESS_SECRET (ingress-bypass only)
ulimit -n 20000
python run.py --i-understand-this-is-staging --clients 5000 --start-window 120 --duration 600 \
  --fairplay-url https://fp-staging.example.edu --oj-url https://oj-staging.example.edu \
  --ojmode ingress-bypass --contest EXAMKEY --users-file users.txt --out result.json
```

Use `/Users/ava/code/fairplay-release-0.7.9/server/.venv/bin/python` (httpx, cryptography) or any
Python 3.11+ with `httpx` and `cryptography`. Ramp: `--clients 100`, `500`, `1000`, `5000`.
`--fairplay-url` or `--oj-url` alone runs just that half. `--loss 0.05` simulates 5% lost
requests; they are reported as `simulated_timeouts`, are not sent, and are not counted as errors,
but they do trigger the Mac backoff.

## Safety

The run is refused (exit 2) unless all of these hold:

- every target host (`host` or `host:port`) is in `LOADTEST_ALLOWED_HOSTS` (no wildcards);
- the host is never `dku.speakupai.uz`, `10.200.31.64` or `10.200.30.218`, and does not resolve
  (DNS is resolved, IPv4-mapped IPv6 normalised) to any of those or to the addresses the live name
  resolves to, even when allowlisted;
- `--i-understand-this-is-staging` is given, `--clients` is at most 6000;
- the resolved target is printed and the run waits 5 s (`--yes` skips this, for tests).

Connections are pinned: the client connects only to the addresses that passed the check (URL host
swapped for the address, `Host` and the TLS server name kept), so DNS cannot change between check
and connection; any other host is refused. Proxy environment variables and netrc are ignored
(`trust_env=False`). The deny check fails closed: if `dku.speakupai.uz` cannot be resolved, the run
is refused (the machine needs working DNS). IPv6 forms that carry an IPv4 (mapped, compatible,
NAT64, 6to4, Teredo, ISATAP) are mapped back and denied when that IPv4 is live.

DNS is resolved again when the run starts (after the wait) and once more at half time of the
steady state (start window + duration/2). If a target now resolves to a live address the run
or if the answer holds an address outside the pinned set, the run aborts at once with `ABORTED: ...` (exit 3) and no result is judged. Redirects are not followed.

## OJ modes

- `ingress-bypass`: needs a staging OJ that accepts the ingress headers
  (`FAIRPLAY_NATIVE_INGRESS_MODE=nginx-mtls`, trusted peer = the load generator address, request
  over TLS or a proxy that marks it secure). The harness sends `X-FairPlay-Ingress-Key`,
  `X-FairPlay-Client-Verify: SUCCESS` and a fresh self-signed client certificate per Mac
  (`X-FairPlay-Client-Cert`), so every Mac has its own device fingerprint. It needs one staging
  account per client in `--users-file` (`user:password` per line), a visible FairPlay contest the
  accounts can start, and a Redis cache on staging. The signature on the OJ response is not
  verified by the harness (that is client CPU, not server load).
- `plain`: public pages and login only (login page, optional login POST, then page GETs every
  ~20 s). No claim or renew. A full claim over real mTLS needs a staging OJ with a test CA that
  issues the client certificates; this harness does not mint those.

## Result

Printed and written as JSON with `--out`: rps, p50/p95/p99 per endpoint, status counts
(timeouts as `timeout`), 429 count, clients that saw lease-expired (a non-active state, or no
successful renew for 90 s) and, separately, a hold signal.

The same thresholds apply at every size: renew p99 < 1 s, 0 lease-expired, 0 hold signals,
error rate < 0.1%, 0 unintended 429s (each failure is its own reason). The verdict line is one of:

- `PASS: 5,000-client bar met` (only with >= 5000 clients)
- `PASS at N clients (this is NOT a 5,000-client proof)`
- `FAIL at N clients: <reasons>`
- `escrow-only: renew thresholds not evaluated` (a run with only escrow requests; errors and 429s
  still fail it)

Exit code: 0 for PASS or escrow-only, 1 for FAIL, 2 refused, 3 aborted by the DNS recheck.

Escrow note: the server caps escrows per source IP per hour
(`FAIRPLAY_NATIVE_EXIT_LOCKS_PER_IP_HOUR`, default 2000) unless the source is in a school net, and
20 per device. A single load-generator IP at 5000 clients will see 429s from that cap; set the
cap higher or the generator address in the school nets on staging, or the run correctly fails.

## What staging must provide for a valid 5000 run

- OJ with the same code, settings, workers, database size and Redis as production; test CA (for
  full mTLS) or the ingress bypass for the generator; 5000 test accounts enrolled in the exam.
- FairPlay server with the same settings, `FAIRPLAY_NATIVE_EXIT_PRIVATE_KEY` set.
- Host monitoring: record OJ and database CPU, connections and queue depth during the run. The
  harness cannot measure them.
- A generator with enough file descriptors and CPU (check it is not the bottleneck), ideally in
  the same network position as the Macs.

## Tests

```
/Users/ava/code/fairplay-release-0.7.9/server/.venv/bin/python -m pytest tools/loadtest/native_oj
```

Offline; a fake server runs on loopback.

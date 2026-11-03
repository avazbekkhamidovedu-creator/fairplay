# Isolated HTTP admission fixture

This is executable mock infrastructure, not a production university integration. No existing FairPlay server imports it. It combines a fake LMS, authenticated profile delivery, pending launch, simulated native lock proof, one-use admission, protected questions, heartbeats and submission receipt in one ASGI process.

Run from the artifact root with a Python environment containing FastAPI and httpx:

```
PYTHONPATH=server:fixture python -m unittest discover -s fixture -p 'test_*.py' -v
PYTHONPATH=server python -m unittest discover -s server/tests -v
```

Tests make real HTTP request/response calls through FastAPI's ASGI TestClient, without opening a network listener. The fixture's `create_app(native_key=..., student_key=...)` factory can also be served by a local harness. If running a listener, bind only to `127.0.0.1`; use random throwaway fixture keys and never university credentials. The profile's example HTTPS origins are policy fixtures, not live DNS or deployed services.

## Endpoints

- GET `/api/university/profile`: fixture-native credential required; emits validated profile and protected launch map, explicitly marked `mock_only` and `production_gate_verified=false`.
- POST `/mock-lms/launch`: fixture-student credential plus an exact registered launch URL; creates a question-free pending attempt and nonce, bound to a fixture device/session.
- POST `/api/attempts/{id}/lock`: native simulator submits the challenge-bound HMAC only after simulated lock succeeds. Failed/expired/replayed/wrong-binding proofs cannot transition. The proof message includes a domain separator, attempt, student, session, device, profile revision, nonce and locked bit.
- POST `/mock-lms/admit`: atomically redeems one-use admission for the matching device/session and current lock.
- GET `/mock-lms/attempts/{id}/questions`: protected mock question bytes appear only for an active, fresh session.
- POST `/api/attempts/{id}/heartbeat`: current simulated lock refresh or immediate revocation.
- POST `/mock-lms/attempts/{id}/submit`: returns a receipt and ends question access.

## What the tests establish

Twelve HTTP integration tests cover profile authentication, the full launch/lock/admit/question/submit flow, no pre-lock question release, forged/failed lock, wrong device/session/browser, two concurrent lock requests, concurrent admission replay, challenge/admission expiration, lost lock, heartbeat expiry/refresh and context override attempts. Fourteen existing profile/contract tests remain separate.

## Exact limits

The native app has not been connected to these endpoints. There is no macOS lock attestation: a test-only shared HMAC key simulates the native proof, and fixture heartbeat authentication simulates a trusted native transport. This fixture deliberately assumes its local test principals, single student, one exam and single-process mutex; production requires enrolled identities, real key provisioning/signature validation, tenant isolation, durable atomic storage, rate limits, deployment-safe configuration and LMS gate integration. App restart clears fixture state. It does not implement retries/idempotent recovery, real answers, LMS signing keys or submission integration. No production profile readiness can be inferred from passing it.

No browser UI, physical Mac/WebKit, code-signed build, accessibility lock, real SSO, Canvas Classic/New Quizzes or direct native Canvas API bypass tests were run here. Those remain necessary before claiming end-to-end production support.

Tested with Python 3.12 and the versions in `requirements-test.txt`. The run emits Starlette's httpx deprecation warning; all 12 HTTP tests pass. The test harness uses in-process HTTP rather than a TCP/TLS deployment, so network/TLS behavior is not covered.

## Loopback TCP and native test harness

`test_fixture_tcp.py` now starts a separate loopback-only process, uses an ephemeral TCP port and throwaway credentials, exercises profile → launch → simulated lock → admission → questions → receipt, verifies questions are denied before admission and after submission, then stops the process. Together there are 13 fixture tests (12 ASGI and 1 real TCP) plus 14 validator tests. TCP is covered; TLS is not.

The opt-in Mac test harness uses these same environment variables:

```
FAIRPLAY_FIXTURE_PORT=8765
FAIRPLAY_FIXTURE_NATIVE_KEY=<throwaway native test key>
FAIRPLAY_FIXTURE_STUDENT_KEY=<different throwaway student test key>
PYTHONPATH=server:fixture python fixture/run_fixture.py
```

Export the variables into both processes. No production credentials should be used. Run the Mac test target with the same three variables while the fixture process is alive. The runner binds only `127.0.0.1` and has no configurable host. Port `0` selects an available port for automated Python tests; `--ready-file PATH` writes only the selected host/port, never keys. The Mac harness should use a known fixed port or read that selected port first. Authentication headers are never logged by this runner. Stop the runner after testing.

The native harness is test-target-only, validates the `mock_only` marker and false production readiness, and uses simulated lock success. It does not make the release application's university browser active or attest actual macOS lockdown. Native-to-fixture test results must be reported separately after execution; the Python TCP test is not a native test.

# FairPlay Bridge Fixes Needed on DKU OJ

Hello,

We've completed our implementation of the FairPlay integration and found a few issues on DKU OJ's side that cause data loss and incorrect behavior. Below are the issues we've identified, ordered by severity. Each includes what happens, a one-line scenario, the fix, and where in your code it lives.

## 1. Receipt written before commit, causing lost answers on timeout

**What happens:** A delivery writes the receipt to Redis before the database transaction commits. If another request arrives for the same attempt while the database is still rolling back, it gets a 200 with the receipt, FairPlay marks the submission delivered, and the student's answers are lost.

**Scenario:** Two copies of the same hand-in arrive at almost the same moment (for example a retry). The first writes its receipt and starts saving; the second sees the receipt and gets a 200; then the first save fails and is undone. FairPlay believes the work was delivered, but DKU OJ has nothing.

**Fix:** Store the record as `pending` when first written. Mark it `final` only in `transaction.on_commit()`. Replay check should refuse to answer for `pending` records and return 503 instead.

**Location:** `judge/utils/fairplay_delivery.py:636` (receipt written) and `:550–552` (replay check outside the lock).

---

## 2. Minutes accounting loses hand-in windows and marks answers late

**What happens:** Minutes added on the FairPlay tab are counted only after FairPlay receives a 200. If a response is lost or time is added later in FairPlay, students get false "late" marks. DKU OJ also ignores the close time FairPlay reports once it has its own count, shrinking the delivery window.

**Scenario:** After a dropped response, professor adds 10 minutes in FairPlay; student gets marked late and loses the 410 delivery window.

**Fix:** Use the larger of DKU OJ's added-minutes count and FairPlay's reported close time, still capped at 8 hours.

**Location:** `judge/views/fairplay.py:446–449` (when minutes are added) and `fairplay_delivery.py:135–145` (how they are used).

---

## 3. Clear hand-in leaves attempt keys in Redis, breaking redelivery

**What happens:** When a professor clears a hand-in, the `fpb:handin:*` key is deleted, but the `fpb:attempt:*` key stays. Redelivering the same attempt returns the old receipt and files nothing.

**Scenario:** Professor clears a student's hand-in; FairPlay redelivers the same attempt; student gets the old receipt but no new submission.

**Fix:** Delete the attempt key when clearing the hand-in.

**Location:** `judge/utils/fairplay_delivery.py:304–314` (clear_hand_in function).

---

## 4. Package and asset APIs serve hidden exams (drafts)

**What happens:** The endpoints that serve package, assets, uploads, and submissions accept any exam tagged `fairplay`, including hidden (draft) assignments. The bridge spec says drafts are not given to FairPlay.

**Scenario:** A professor creates a draft assignment; before publishing, FairPlay can already read and cache the package.

**Fix:** Apply the exam list's `is_visible` filter to `_exam()` lookups in GET endpoints.

**Location:** `judge/views/api/fairplay.py:118–122` (_exam function).

---

## 5. Idempotency: professor clicks again, time added twice

**What happens:** If FairPlay times out waiting for a response to add-time, open, new-code, or approve, the professor clicks the button again. DKU OJ processes both requests, and time can be added twice (or the exam opened twice, etc.).

**Scenario:** Network hiccup; professor clicks "Add time" again; student gets 20 minutes instead of 10.

**Fix:** DKU OJ should send a unique request ID (or keep-alive token) in each call to FairPlay, and FairPlay will deduplicate on it. We will add that support on our side. Please include a unique request-id header and keep it the same on retry.

**Location:** `judge/utils/fairplay_client.py:133–146` (the idempotent POST methods). This is coordinated work—we'll handle FairPlay's side once you send the request ID.

---

## 6. Signature verification uses Django's re-escaped path

**What happens:** Signature verification uses `request.get_full_path()`, which Django re-escapes. A reverse proxy that normalizes paths (e.g., double-decoding) causes signatures to mismatch, returning 401 to every FairPlay request.

**Scenario:** Proxy between FairPlay and DKU OJ normalizes /%2Fapi/… to /api/…; every call fails 401.

**Fix:** Verify against the raw request URI (WSGI environ's `REQUEST_URI` or equivalent), or document the escaping rule as normative so proxies are configured correctly.

**Location:** `judge/utils/fairplay_signing.py:90` (verify_signature function).

---

## 7. DEBUG mode silently allows insecure FAIRPLAY_URL

**What happens:** If `DEBUG=True`, plain-http FairPlay URLs are allowed even outside localhost. Bridge responses are not signed, so a production site accidentally left in DEBUG mode sends rosters and grades in clear.

**Scenario:** Developer deploys with `DEBUG=True` left on; DKU OJ talks to FairPlay over http; network tap reads student rosters.

**Fix:** Depend only on an explicit `FAIRPLAY_ALLOW_INSECURE` flag, not DEBUG.

**Location:** `judge/utils/fairplay.py:94–96` (fairplay_url_insecure function).

---

## 8. Redis eviction allows replays and loses records

**What happens:** Nonces and hand-in records live in the default Redis cache. If it has `maxmemory-policy=allkeys-lru`, records are evicted, and an attacker can replay a request inside the 60-second nonce window. Also, professors see lost hand-in records.

**Scenario:** Cache is full; nonce key is evicted; attacker replays a delivery request; answers filed twice.

**Fix:** Check for `noeviction` policy in the system check (`judge.E101`) or use a dedicated cache alias that guarantees retention.

**Location:** `judge/utils/fairplay.py:66–76` (nonces and records TTL).

---

## 9. Short tokens silently dropped, causing confusing 404s

**What happens:** Tokens shorter than 32 characters are silently dropped. DKU OJ continues running with zero tokens, answering 404 to every FairPlay request, while logs show the bridge is "configured."

**Scenario:** Admin pastes a 16-character token by mistake; FairPlay gets 404; ticket filed: "DKU OJ won't talk to us."

**Fix:** Fail startup with a clear error if a token is under 32 characters. (We will add the same check on the FairPlay side.)

**Location:** `judge/utils/fairplay.py:66–76` (fairplay_tokens function).

---

## Closing note

Tokens should be handed over only to your site admin, never pasted into chat, email, or shared documents. Treat them as you would a database password.

The FairPlay side is also fixing related issues: we are adding retry-on-401/403/404, enforcing NetID validation, and safer student setup flows. You do not need to wait for those or take any action on them.

Thank you,
FairPlay Team

# Protected browser: basic client-only prototype

Open **View → Protected Browser…** in the Mac app. This separate entry leaves the existing student login, exam codes, start-exam flow and server unchanged.

1. Enter the university HTTPS URL. Add exact HTTPS origins needed for SSO and resources; no wildcard domains.
2. Check this Mac. Save work before choosing **Lock and open**; the existing native lock may close other apps.
3. Website restrictions compile first, native pre-lock must succeed, then a separate ephemeral WKWebView opens the university page.
4. **Leave and unlock** destroys the WebView first, then releases this mode's pre-lock. It never submits answers. The existing emergency pre-lock exit remains available.

Configuration is in-memory client data, not university registration. No tokens, exam attempts, question imports, submission calls or institution adapters are added. The WebView has no FairPlay JavaScript message handler, no shared persistent website store, and no cross-mode navigation authority. While its native overlay is open, the retained FairPlay page cannot drive bridge actions or navigation.

The university remains responsible for login, exam content and submission. Only the configured origins load; additional CDN/SSO origins may be necessary. New windows are flattened into the same view. Opener-dependent SSO, file upload/download, camera/microphone and some LMS features are intentionally unsupported in this basic prototype. Leaving is always possible and is not proof of exam completion. This does not prevent another browser/device from reaching the university, protect already-downloaded files, or attest enrollment/lock state to the university.

Tests cover policy parsing, lookalike/credential/port denial, cancellation and stale callbacks, lock-before-content ordering, clear-before-unlock ordering, and actual WebKit content-rule compilation. Tests use fake lock effects, never lock a CI machine. A physical Mac walkthrough with Accessibility enabled and a permitted test university account is still required before production use.

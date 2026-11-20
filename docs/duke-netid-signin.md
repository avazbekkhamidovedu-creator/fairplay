# Duke NetID sign-in in the DKU OJ tab

Students click "Log in with NetID" on the DKU OJ login page and finish the whole sign-in inside the lockdown app.
Mac: `mac/Sources/Proctora/DukeNetIDSignIn.swift` (used by `ProtectedBrowserController.verdict` and `ProtectedBrowserPolicy.contentRules`).
Windows: `src/FairPlay.Core/DukeNetIDSignIn.cs` (used by `UniversityBrowserPolicy.AllowsMain*` and `UniversityBrowserView`). Same lists on both.

## Chain

OJ `/accounts/login_netid_redirect/` -> `https://oj.dukekunshan.edu.cn:4431/auth/redirect` -> `https://shib.oit.duke.edu/idp/...`
-> Duke MFA (Duo) -> SAML POST to `https://oj.dukekunshan.edu.cn:4431/Shibboleth.sso/SAML2/POST` -> the auth service redirects to the OJ `/accounts/login_netid/?token=...`.

## Allowed in the main (OJ) tab as pages, https only, no user name

| Host | Port | Paths |
|---|---|---|
| `oj.dukekunshan.edu.cn` | 4431 exactly | any |
| `shib.oit.duke.edu` | 443 | any |
| `*.duosecurity.com` (subdomains only, not the bare domain) | 443 | any |
| `oj.dukekunshan.edu.cn` (the real OJ) | 443 | only `/accounts/login_netid`, `/accounts/login_netid/...`, `/accounts/login_netid_...`, top-level document only (the return step) |

Rules: only the first (OJ) tab; a chain page is reachable only while the tab shows the OJ or a chain page (never from a blank or other page); chain pages
load their own subresources and frames from chain hosts only (Duo iframe, shib same-host assets); the real OJ return page has no subresources or frames.
Other tabs never get any of this, and the Box chain (`BoxSignIn`) is unchanged. The site deny-list still wins (no chain host is on it). Refused: any other
port (4432), http, look-alikes (`evil-duosecurity.com`, `duosecurity.com.evil.com`), user info, trailing dot, punycode, encoded separators.

The real OJ (`https://oj.dukekunshan.edu.cn`) is deliberately not an exam page: a student could use it outside the pilot. If the auth service returns the
browser to the pilot host (`http://10.200.31.64/accounts/login_netid/...`) that is the normal OJ origin and needs no extra rule.

## Popups, passkeys

Popups follow the existing rules (a popup opens in a new tab, which does not get the chain). WebAuthn/passkeys: Windows WebView2 uses the OS dialog and is not
blocked by the app (`PermissionRequested` denies only the permission API). macOS WKWebView passkeys need the browser passkey entitlement the app does not
have, so Duke falls back to its other methods (Duo). Not changed.

## Unverified

The exact Duo host (any `*.duosecurity.com` is allowed) and where the auth service returns after sign-in (pilot host, or the real OJ only on `/accounts/login_netid...`).

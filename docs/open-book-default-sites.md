# Open-book exams: default allowed websites and the sign-in flows they need

Status: implemented locally on `local/pilot-0.8.8`, NOT yet verified on real devices (see the last section).

## The defaults

An open-book DKU OJ exam starts with this list (from the Safe Exam Browser URL filter the university used: every row
Active, Regex, Allow; "Filter also embedded content" off). The instructor can still edit and extend it.

| SEB row | FairPlay entry | What the lock does with it |
|---|---|---|
| `oj.dukekunshan.edu.cn/*` | `oj.dukekunshan.edu.cn` | the OJ itself (the exam tab's own origin) |
| `shib.oit.duke.edu/*` | `shib.oit.duke.edu` | page; Duke Shibboleth IdP |
| `https://duke.app.box.com/*` | `duke.app.box.com` | page; opens as a Box tab |
| `https://duke.account.box.com/*` | `duke.account.box.com` | listed, never a page: Box sign-in chain only |
| `https://sso.services.box.net/*` | `sso.services.box.net` | listed, never a page: Box sign-in chain only |
| `https://duke.box.com/*` | `duke.box.com` | page; opens as a Box tab |

A FairPlay entry is a host name: every path of the host, https only, default port, any letter case. Differences from the
SEB rows: `http://` is never loaded (the apps load https only), a non-default port never matches, and the SEB rows are
regular expressions where FairPlay uses host entries. The original rows are kept in
`server/app/integrations/dkuoj/open_book_defaults.py` (`SEB_FILTER`).

### When they are added

- The defaults are SEEDED, not forced. `POST /exams/<key>/settings` with `open_book: true` on an exam that was not open
  book and has no websites yet (and sends none) gets the six entries as its websites. Also for an exam FairPlay has not
  synced yet (the pending settings). An exam that already has websites keeps them; a later call never seeds again.
- DKU OJ instructor panel (`templates/contest/create.html`): ticking "Open exam" with an empty website box fills the
  six lines (`OPEN_BOOK_DEFAULT_WEBSITES` in `judge/utils/fairplay_editor.py`, the same list). Unticking leaves the exam
  closed book with no sites. The instructor edits the text box as before.
- A closed-book exam (no open book, no websites) never gets them.
- FairPlay-native exams are not touched.
- `duke.account.box.com` is added to `DEFAULT_EXCEPTIONS` of `site_policy.py` so the deny-list ("box.com") does not
  report it as refused. An administrator who sets `FAIRPLAY_SITE_POLICY_EXCEPTIONS` (also empty) still decides alone; Box
  in general (`app.box.com`, `*.box.com`, other tenants) stays refused. `FAIRPLAY_EXTERNAL_SITE_ALLOWLIST`, when set,
  must cover the defaults too.
- With a Box folder link (`box_link`) set, the Box hosts of the list are not shown as refused (the folder link wins).

## What the lock sends (`lock_sites`) and what the apps allow

`build_lock_sites` gives `pages` (approved pages) and `resources` (origins loaded as subresources). For the defaults:
pages `https://oj.dukekunshan.edu.cn/`, `https://shib.oit.duke.edu/`, `https://duke.app.box.com/`,
`https://duke.box.com/`; resources the Box chain hosts and `https://*.boxcdn.net`, `https://*.boxcloud.com`,
`https://*.duosecurity.com`.

Mac and Windows apply the Box sign-in chain (`BoxSignIn`) only in a tab opened on an approved Box page, only while that
tab is on the chain, https on 443, no user name. That is stricter than the SEB list, which allows every row everywhere,
and it is deliberate: Box and the Duke sign-in must not be reachable from the exam tab or from other sites.

### Hosts each login flow needs

| Flow | Navigations (top frame / popup) | Embedded content |
|---|---|---|
| DKU OJ sign-in (the exam tab) | `oj.dukekunshan.edu.cn` only; the signed launch signs the student in, no external IdP | everything the OJ page loads, except the deny-list (`ProtectedBrowserPolicy` OJ-page rule; Windows equivalent) |
| Duke Shibboleth | `shib.oit.duke.edu` (any path: SAML SSO redirect, POST back to the service provider) | its own static files; Duo prompt in a frame or page from `*.duosecurity.com` (`api-<id>.duosecurity.com`) |
| Box sign-in with Duke SSO | `duke.app.box.com` or `duke.box.com` -> `duke.account.box.com/login*` -> `sso.services.box.net/*` -> `shib.oit.duke.edu/*` -> Duo -> `sso.services.box.net/*` (ACS) -> back to the Box page | scripts, fonts, icons from `*.boxcdn.net` (`cdn01`, `e3`, `static`, others); previews and downloads from `*.boxcloud.com`; `api.box.com` |
| Box browsing after sign-in | `duke.app.box.com`, `duke.box.com` (every path) | as above |

Cookies are kept by the page's own WebView/WKWebView store across these hosts (they are ordinary third-party and
first-party cookies of the sites; nothing in the apps strips them). Popups (`window.open`, `about:blank` first, then the
sign-in host) are covered by `BoxSignIn.allowsTopLevel`; form POSTs are navigations to the hosts above.

### What changed in the apps (Mac `BoxSignIn.swift`, Windows `BoxSignIn.cs`)

A Box tab used to load only `cdn01.boxcdn.net`, `e3.boxcdn.net`, `api.box.com` and `*.boxcloud.com` as embedded
content. It now also loads every host under `boxcdn.net` (`tabAssetSubdomainsOf` / `TabAssetSubdomainsOf`), and drops
`https://*.boxcdn.net` as a plain list resource like the other Box hosts (it is only for Box tabs). The Duke Box PANE
(`BoxPolicy`) keeps its narrow ceiling on purpose.

### Not added, on purpose

`app.box.com` / `account.box.com` as pages (anyone's personal Box would open), Box upload hosts (`upload.*`, `fupload-*`),
Box Notes, `*.box.com`, `*.box.net` as wildcards. Plain `http://`.

## Final verification (required)

The host lists come from Box's firewall documentation, Duke OIT's Shibboleth documentation and the code, NOT from a
recorded login. Before an exam, log in on:

1. a real Mac, and
2. a real Windows PC,

with an open-book exam that has these defaults: open the OJ, open the Box tab from the page menu, sign in with Duke
(Shibboleth + Duo), open a Box folder and a file preview, return. Record the hosts the browser actually used (HAR or the
app's blocked-request log). Anything refused shows as a blocked request; add its host here, in `BoxSignIn` (Mac and
Windows, same lists) and in `lock_sites.BOX_TAB_ASSET_HOSTS`, together with a test. Unconfirmed: whether Box returns
to `duke.account.box.com` on a path other than `/login*` after SSO (the apps allow `/login*` only), and any Duke
host beyond `shib.oit.duke.edu` and `*.duosecurity.com`.

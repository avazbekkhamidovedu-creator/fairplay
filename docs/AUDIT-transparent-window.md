# Transparent-window audit (Mac design-b, Windows design-b, DKU OJ design-b), 2026-11-09

Principle: FairPlay is a window around DKU OJ plus a lock. OJ errors and issues show exactly as in a plain browser.
Status: R = read from code, T = read from code and a unit test exists (not run in this audit), U = unverified. No app was launched.
Paths: Mac = mac/Sources/Proctora/, Win = fairplay-windows-design-b/src/FairPlay.Shell/ (Core = src/FairPlay.Core/), OJ = dkuoj-design-b/.

## Headline
1. Release builds load the OJ through the mTLS gateway https://10.200.31.64:8443/ after getting a device certificate from dku.speakupai.uz. If either is down the student is blocked even when the OJ works in a browser.
2. The check before the lock (GET accounts/login/) replaces a 5xx or network failure with app text. Windows shows "connect the VPN" even for a server 500.
3. Network failure during the exam: Mac shows a blank or stale page plus "Could not load this page. Try Reload."; Windows shows Edge's own error page plus a status line.
4. Copy, cut, paste blocked everywhere (Mac clears the clipboard every 0.2 s); file upload, downloads, attachments refused. Owner decisions.
5. HTTP 4xx/5xx pages from the OJ pass through untouched on both platforms.

Mac and Windows match on allowlist, admission, injected scripts, 4xx/5xx pass-through, upload/download/paste blocks. They differ on: error display (3), JS dialogs (Mac draws its own, Windows closes native dialogs every 300 ms, unverified), blocked page resources (Mac drops silently, Windows returns a fake 403), wording before the lock, print (Windows disables window.print, Mac blocks Cmd-P).

## Errors and failures
| deviation | plat | file:line | student sees vs plain browser | lock need | action | st |
|---|---|---|---|---|---|---|
| HTTP 4xx/5xx main page shown as-is | Mac | ProtectedBrowserController.swift:1731-1743 | same | n/a | keep | T |
| HTTP 4xx/5xx main page shown as-is | Win | UniversityBrowserView.cs:436-442 | same | n/a | keep | T |
| Pre-lock GET accounts/login/: 5xx or transport error -> "DKU OJ is unavailable..." | Mac | ProtectedBrowserController.swift:294-307,253-256 | app text instead of the OJ 500 page | partly | keep for transport errors; pass 5xx status and OJ title through | T |
| Same check; 5xx -> StartMessages.Network ("connect the VPN") | Win | MainWindow.University.cs:93-101,143-144; Core/NetworkResilience.cs:55-60 | misleading VPN advice for a 500 | partly | separate wording for 5xx | R |
| Failure during the exam: blank/stale page + "Could not load this page. Try Reload." + auto retry + VPN sheet | Mac | ProtectedBrowserController.swift:1842-1873,686-698,1068-1078 | no page, no reason | no | show the WebKit error description or a minimal error page; keep retry | R |
| Same: Edge built-in error page + status line | Win | UniversityBrowserView.cs:456-477 | browser error page plus extra status | no | keep (Mac should match) | R |
| TLS/cert error: no click-through | Mac/Win | VPNLauncher.swift:147-156; UniversityBrowserView.cs:446-455 | browser offers "proceed anyway" | yes | keep | T |
| Claim refused with a non-JSON body -> neutral app sentence; launch navigation cancelled | Mac/Win | ProtectedBrowserController.swift:1244-1263,1600-1610; UniversityBrowserView.cs:265-276 | OJ HTML error (e.g. 404 "No such contest") not shown | admission yes; hiding no | pass HTTP status and page title into the notice | R |
| On hold: page removed, hold text shown | Mac/Win | ProtectedBrowserController.swift:1486-1498 | OJ page hidden | yes | keep | R |

## Navigation and features
| deviation | plat | file:line | lock need | action | st |
|---|---|---|---|---|---|
| Host allowlist; other hosts cancelled with a notice | Mac/Win | ProtectedBrowserPolicy.swift:127-138; Core/UniversityBrowserPolicy.cs:57-62 | yes | keep | T |
| Content blocker for resources not on the OJ/asset list; Win returns fake 403 | Mac/Win | ProtectedBrowserPolicy.swift:210-243; UniversityBrowserView.cs:280-284 | yes | keep; check the signed assets list covers every OJ CDN; align Win with Mac | U |
| Popups limited (8 tabs, click only) | Mac/Win | ProtectedBrowserController.swift:675,1746-1777 | yes | keep | R |
| Downloads and attachments cancelled | Mac/Win | ProtectedBrowserController.swift:1625,1733; UniversityBrowserView.cs:308 | yes | keep (owner) | R |
| File upload refused (picker, input, drag-drop) | Mac/Win | ProtectedBrowserController.swift:1779-1782; UniversityBrowserView.cs:218-225,333-349 | unclear | OWNER DECISION: OJ file-answer problems cannot be done | R |
| Win dialog closer may close WebView2 alert/confirm boxes | Win | UniversityBrowserView.cs:140,333-349 | no | verify on Windows; skip script dialogs | U |
| JS alert/confirm/prompt re-hosted as an app sheet | Mac | ProtectedBrowserController.swift:1689-1720 | yes | keep | R |
| Context menu removed; Cmd-S/P/O swallowed; print no-op | Mac/Win | ProtectedBrowserController.swift:5-15; UniversityBrowserView.cs:239,245,320 | yes | keep | R |
| Copy/cut/paste blocked, Mac clipboard cleared every 0.2 s | Mac/Win | EventTap.swift:366-378; LockdownController.swift:827-840; Core/KeyPolicy.cs:170-173 | partly | OWNER DECISION: allow in-page copy/paste inside the OJ code editor | T |
| Camera/mic/notification denied; mailto etc. cancelled | Mac/Win | ProtectedBrowserController.swift:1784-1786 | yes | keep | R |
| DevTools, autofill, password save, translate off | Mac/Win | ProtectedBrowserController.swift:676-679 | yes | keep | R |

## Injection, headers, session
| deviation | plat | file:line | lock need | action | st |
|---|---|---|---|---|---|
| Ephemeral cookie store / InPrivate profile (sign in to the OJ each time) | Mac/Win | ProtectedBrowserController.swift:674; UniversityBrowserView.cs:197 | yes | keep | R |
| No user-agent change in the protected browser | Mac/Win | n/a | n/a | keep | R |
| /contest/X/fairplay-launch intercepted -> native claim POST -> injected form POST /contest/X/join | Mac/Win | ProtectedBrowserController.swift:1594-1611,1266-1275; UniversityBrowserView.cs:265-276,764 | yes (admission) | keep | T |
| Periodic same-origin fetch /api/fairplay/browser-context, renew/abort POSTs | Mac/Win | ProtectedBrowserController.swift:820-822,1193-1199 | yes | keep | R |
| Answer capture every 1 s (only #workspace with data-native-user) + restore event | Mac/Win | BrowserDraftAdapter.swift:6-28; UniversityBrowserView.cs:820,848 | yes (crash safety) | keep | R |
| localhost:18443 / 127.0.0.1:18081 mapping | Mac | UniversityDestination.swift:7-10 | test only (#if FAIRPLAY_ISOLATED_TEST) | keep | R |

## Gateway, mTLS, readiness
| deviation | plat | file:line | lock need | action | st |
|---|---|---|---|---|---|
| OJ address replaced by the gateway https://10.200.31.64:8443/ | Mac/Win | UniversityDestination.swift:11; NativeBrowserProfile.swift:16; Core/UniversityBrowserPolicy.cs:49-51 | yes | keep; gateway must proxy OJ errors unchanged | R |
| Gateway TLS trusted only via the bundled authority | Mac/Win | NativeBrowserIdentity.swift:103-108 | yes | keep | R |
| Device certificate automatic; none -> "Device verification is unavailable" | Mac/Win | ProtectedBrowserController.swift:278-290; MainWindow.University.cs:89-91 | yes | keep | R |
| Release build needs https + profile | Mac | ProtectedBrowserController.swift:242-250 | yes | keep | T |
| Windows: one screen, no remote session, close apps, watchdog before the lock | Win | MainWindow.University.cs:104-125 | yes | keep | R |

## DKU OJ edits that reach ordinary users (all invisible)
| edit | file:line | effect | action | st |
|---|---|---|---|---|
| Cache-Control private, no-store on contest lists | judge/views/contests.py ContestListMixin.render_to_response | back button reloads the list | keep (or scope to discovery rows) | T |
| update_contest checks for a device session | judge/models/profile.py:468-482 | one extra query when a current contest is set | keep | T |
| workspace_native_admitted computed on every workspace render | judge/views/problem.py:503-504,572-573 | extra lookup, always False for plain users | keep | T |
| ContestLeave: record_browser_completion for every leave when FAIRPLAY_BROWSER_SIGNING_KEY is set | judge/views/contests.py; fairplay_browser_context.py:16-28 | invisible session key | gate on is_fairplay_exam | R |
All other edits are gated to FairPlay-tagged exams, rostered students or admitted native requests (middleware returns early for anyone without an admitted device session).

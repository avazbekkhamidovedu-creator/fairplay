# FairPlay: planned later (not now)

## 1. Mac: switch the lock to Apple's assessment mode (Bluebook's method)

**Decision (2026-10-20):** the pilot and the near term use **our own lock**: kiosk mode,
the keyboard tap that needs Accessibility, and closing non-allowed apps. Once FairPlay is a
registered organization, request Apple's entitlement and make assessment mode the main lock.
Our lock then stays as the fallback.

### Why switch later
- macOS enforces the lock itself: Dock, menu bar, Mission Control, other apps, screenshots,
  screen recording and sharing are blocked by the system, not by our app.
- Only participant apps get network access, so a hidden background app can't talk to anyone.
- No Accessibility permission, which is today's most common setup failure.
- Apple keeps it working across macOS updates; our own lock can break with each release.
- Teacher-allowed apps map one-to-one onto Apple's participants (`AEAssessmentApplication`).
- Easy to explain to universities: "the same macOS assessment mode Bluebook uses".

### Costs and risks to plan for
- **Apple gate.** It needs the restricted *Automatic Assessment Configuration* entitlement, an
  organization Apple Developer account (D-U-N-S number), and approval that takes weeks. Ask
  whether Developer ID distribution is covered; otherwise it means App Store distribution.
  Request form: https://developer.apple.com/contact/request/automatic-assessment-configuration/
- **No real testing before approval.** A build without the entitlement just falls back.
- **Depends on the macOS version.** The basic mode works from macOS 10.15.4. The detailed
  controls are macOS 27 only (WWDC26 session 230): `allowsVirtualMachine`, `allowsScreenshots`,
  `allowsOnlyParticipantsToRun`, `requiresSIP`, `allowsMenuBar`, `allowsDock`,
  `allowedDirectoriesAndFiles`, `allowsUserScriptExecution`. Put them behind
  `if #available(macOS 27, *)` and keep our own checks for older Macs.
- **Never set `requiresManagedDevice`.** Students use their own Macs.
- **VPN (biggest real-world risk).** Apple warns that app-based VPNs can lose the network.
  Add Cisco Secure Client / AnyConnect as a participant **with network access**, check the
  real bundle ids on a campus Mac, and test on campus Wi-Fi before anything else.
- **Black box.** macOS blocks silently and tells us nothing, so keep our monitoring in both
  modes: signed beats, process list (report only), display, VM and clipboard checks, watchdog.
- **Debugging.** Never stop at a breakpoint inside a session; the Mac can stay stuck until
  restart. Test in a macOS VM first.

### How to build it (summary of plan item M1)
1. Build: `mac/FairPlay.entitlements` with
   `com.apple.developer.automatic-assessment-configuration`. With `FAIRPLAY_PROFILE` set,
   embed the provisioning profile before signing, sign with `--entitlements`, and pass
   `-Xswiftc -DFAIRPLAY_AAC`. Link `AutomaticAssessmentConfiguration` in `Package.swift`.
   Dev builds stay exactly as today.
2. `mac/Sources/Proctora/AssessmentMode.swift`: a wrapper around `AEAssessmentSession`.
   `begin(completion:)` and `end()`, plus the delegate:
   - began → continue;
   - failed to begin → fall back to our lock;
   - interrupted → blocking screen, event `aac_interrupted`;
   - ended → log.
3. Config:
   - FairPlay keeps network.
   - Teacher-allowed apps (`allowed_apps`) become participants with network off.
   - The VPN client is a participant with network on.
   - The watchdog may be added as an `AEAssessmentBinaryExecutable`.
4. `LockdownController.lock()` becomes two-phase:
   - Try assessment mode first.
   - If it began, skip the event-tap requirement and kiosk options.
   - If it failed, use today's lock unchanged and report `client_lock_mode = "fallback: <error>"`.
   - Beats report `sep=1` (assessment mode) or `sep=0` (fallback); the server's
     "require strong lock" exam setting uses this.
5. `unlock()`: `AssessmentMode.end()` first, then today's teardown.
6. Emergency exit (Ctrl-Opt-Cmd-E): in assessment mode use `NSEvent.addLocalMonitorForEvents`
   (no permission needed); keep the tap path for fallback.
7. Apple's checklist:
   - Clear `NSUserDictionaryReplacementItems` early in `main.swift`.
   - `windowShouldClose` returns false while locked.
   - Cmd-Q, Cmd-W and Cmd-H do nothing while locked.
   - Ask for every permission before the session starts.
8. Show the Accessibility prompt only when assessment mode isn't available.
9. Hand tests:
   - Lock blocks Dock, menu bar, Mission Control, Cmd-Tab and screenshots.
   - The exam saves over campus Wi-Fi **and** AnyConnect.
   - The emergency exit works.
   - Hand-in ends the mode.
   - A force-kill ends the mode and the watchdog relaunches the app.
   - A non-entitled build falls back and reports `sep=0`.

### Pros and cons, kept for reference
| | Apple assessment mode | Our own lock (today) |
|---|---|---|
| Strength | System-enforced; blocks network for other apps | App-level: detect and react |
| Setup | No Accessibility permission | Needs Accessibility |
| Availability | Needs Apple approval; full controls on macOS 27+ | Works now, any supported macOS |
| Visibility | Silent; we must keep our own monitoring | Every blocked attempt is an event |
| Control | Apple's rules | Ours (VPN kept, teacher apps, Finder hidden) |
| Maintenance | Apple keeps it working | Can break with macOS updates |
| Windows parity | No equivalent on Windows | Same design as Windows |

Sources:
- https://developer.apple.com/documentation/automaticassessmentconfiguration/aeassessmentconfiguration
- https://developer.apple.com/documentation/automaticassessmentconfiguration/build-an-educational-assessment-app
- https://developer.apple.com/videos/play/wwdc2026/230

## 2. Mac auto-update (Sparkle), plan item M6
Needs Developer ID signing and notarization first, so it waits for the organization account.
- Sparkle 2 via SwiftPM, an EdDSA key (public key in Info.plist), and an appcast at
  `https://<server>/updates/mac/appcast.xml`.
- Check for updates at launch, never while locked.
- A version below the server's `min_app_version` must update before sign-in.

## 3. Making the apps harder to fake (decided 2026-10-28: planned, not started)
No client can be made impossible to copy; the aim is that faking it costs more than it is worth,
and that nothing the app holds is a secret that matters. Order of value:
1. **Assessment mode on Mac and iPad** (section 1). The OS blocks other apps, screenshots and
   sharing, and a cloned app cannot fake that. Needs the organization Apple account and Apple's
   approval (weeks): start the request first.
2. **A key per device, kept in hardware.** First run creates a key pair in the Secure Enclave
   (Mac, iPad) or the TPM (Windows); the server stores the public key and the app signs challenges.
   A key pulled out of the app then proves nothing. Apple App Attest on iPad and, where
   supported, recent macOS; TPM key attestation on Windows (weaker). Real work: the start and
   beat proofs in `app_client.py` change.
3. **Rotate the shared app key with every release** (`FAIRPLAY_APP_KEY` already takes a list).
   Old builds are then refused through `MIN_APP_VERSION_*`. Cheap; can go into the next release.
4. **Developer ID signing and notarization** with the hardened runtime: macOS refuses a modified
   app, and students no longer need "Open Anyway". Same Apple account as item 1.
5. **Obfuscate the Windows app** (ConfuserEx or Native AOT). Slows decompiling, does not stop
   it. Mac and iPad are native code already. Strip symbols everywhere; Windows keeps
   `DebugType none`.
6. **Server and people.** Answer keys and grading stay on the server. Keep integrity events and
   teacher alerts; add checks for impossible patterns (one device signing for two students).
7. **Browser gate** (`FAIRPLAY_BROWSER_GATE`, CONTRACT.md): default `headers`. Switch to `strict`
   once the first Windows build with the `FairPlay-Windows` user agent is the minimum
   (`MIN_APP_VERSION_WINDOWS`). Until then any Edge on Windows still reaches the student pages.
Not solvable by software alone: a virtual machine, a second computer, or a phone photo of the
screen. Locking plus invigilation is the answer for those.

## 4. B2B readiness (plan of 2026-10-26; DKU OJ is the first client, not the product's shape)
Order before a second paying institution:
1. Sentry and alerts (started: `/metrics`, Sentry, alert rules in `server/NOTES.md`; counters
   are per worker, so use one scrape per instance or a shared store).
2. Tenancy: an `organizations` table and `org_id` on users, exams, classes, LMS and integration
   tables; uniqueness becomes `(org_id, username)` and `(org_id, netid)`; row-level security as a
   backstop; files move from local disk to object storage. Needs a real migration tool
   (today: add-column on startup). Everything touching `models.py` and `db.py` goes through one owner.
3. Admin roles (`org_admin`, `platform_admin`) and an append-only audit log.
4. Per-org config and integrations (`org_integrations`, `org_settings`) with a test-connection
   button, instead of env vars (`DKUOJ_URL`, `DKUOJ_TOKEN`, Canvas sign-in hosts).
5. Grade delivery: leased claiming, retry on 401/403/404 and resend are done for DKU OJ. Still to
   do: Canvas and Moodle grade pushes through the same outbox; a dead-letter page; test the claim
   query on Supabase staging (it passed on a local Postgres).
6. Split secrets: a token-signing key with a key id, a separate data-encryption key with
   versioned sealed values, per-org bridge tokens. Rotating `PROCTORA_SECRET` today signs
   everyone out and destroys sealed credentials.
7. Load proof on Render plus the Supabase pooler: 1,500 students signing in and starting within
   60 s, start p95 under 2 s, 0 5xx; then 3,000 writing across 3 orgs.
8. Data residency and retention: a legal question for a person (DKU students are in China;
   cross-border transfer; controller and processor; sensitive data). Options: nearest region, a
   China cell, or a school-hosted copy. Retention defaults to be decided before onboarding.
9. Uptime and backup targets (99.5%, 99.9% in booked exam windows), point-in-time recovery,
   a quarterly restore drill, a support runbook.

## 5. Open items from the DKU OJ review (2026-10-26)
- Student set-up can be claimed by a classmate (shared class login plus NetID plus name). Needs a
  per-student one-time code or signed link from DKU OJ, or the professor handing out codes.
- Idempotency key on DKU OJ's add-time, open, new-code and approve calls (both sides).
- The nine DKU-side fixes are in `docs/dkuoj-fix-requests-2026-10-26.md`.
- The shared bridge token lives in plaintext on a Desktop file: hand it to the site admin only,
  delete the file, rotate after handover.

## 6. Release checklist (learned 2026-10-27)
A release needs ALL of these to carry the same version, or a workflow stops or ships the wrong
number inside the app:
- `RELEASE_VERSION` in `fairplay` and in `fairplay-windows`;
- `mac/Sources/Proctora/Config.swift` and `ios/FairPlay/Config.swift` (the Mac workflow fails
  when they differ from the tag);
- `AppConfig.Version` in `fairplay-windows/src/FairPlay.Core/AppConfig.cs`.
Pushing the deploy branch also redeploys the server on Render. Only installers and update files
go to the public download repo; no code, ever.

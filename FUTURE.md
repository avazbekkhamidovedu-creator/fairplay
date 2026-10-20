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

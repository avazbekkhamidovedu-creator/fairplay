# FairPlay Native Locked Webview Vision: Roadmap & Readiness (2026-11-09)

## Summary

FairPlay locked webview for Canvas/LMS exams is 70% complete (Mac app, server, OJ bridge) with all core flows built and tested locally. DKU OJ production use is 55% ready: the native lock works, Canvas mode locks and restricts to the quiz, resources panel works, but critical blockers remain (lock ID registration trap fix uncommitted, OJ migration violates schema rules, Windows uncompiled, nothing deployed to VM, real device testing pending). The vision — unlock-at-start, full-screen lock with countdown and resources, offline tolerance — requires easy-unlock design, sealed hand-in, and Canvas verification before launch.

**Readiness for DKU OJ production (A): 55%** | **Readiness for full vision (B): 70%**

## Video Steps Status

| Step | Status | Evidence | What Remains |
|---|---|---|---|
| Open FairPlay as normal Mac window | Done | mac/Sources/Proctora/MainWindowController.swift | Real Mac testing, macOS 14/15 hardware |
| Browse to Canvas (canvas.duke.edu) | Done | CanvasMode.swift, Canvas host allow-list | Real Canvas, SAML/Duo login flow |
| Sign in with university NetID (unlocked) | Done | Web view before lock, no restrictions | Real DKU NetID test, Duo MFA handling |
| Open Quizzes list | Done | CanvasNavigation.swift, quiz phase detection | Real Canvas quiz list rendering |
| Open quiz instructions page | Done | CanvasScope restricts to course+quiz | Real Canvas rendered instructions, "approved formula sheet" detection |
| Press Start quiz → fullscreen lock | Partial | Lock activates at quiz `take` page; fullscreen works | Easy-unlock design (being written elsewhere), verify real Mac window behavior |
| Locked bar (FairPlay + green badge + quiz name + timer) | Done | ProtectedBrowserController.swift header bar | Real Mac rendering at 60:00 countdown |
| Resources button + Materials (PDF) + Websites tabs | Partial | Materials panel built; PDF.js viewer in frame | Duke Box overlay tested only on localhost; real Box folder links unverified |
| Finish exam button (top right) | Done | NativeLeaveController.swift Leave sheet | Real Mac Leave UI, code-entry on locked Mac |
| Question types: MCQ, typed answer, essay | Done | Canvas question rendering pass-through | Real Canvas editor shortcuts, rich text in essays |
| Autosave + "Saved" indicator | Done | web/src/Canvas*.tsx answer save debounce | Real Canvas autosave sync; offline tolerance not yet built |
| Question list + checkmarks | Done | Canvas quiz page sidebar navigation | Real Canvas quiz nav rendering |
| Review answers page | Done | Canvas quiz review page (university's site) | Real Canvas review rendering |
| Submit quiz + confirmation dialog | Done | Canvas finish detection + confirm sheet | Real Canvas submit flow, receipt generation |
| Success page | Done | Canvas results/score page (university's site) | Real Canvas success page rendering |
| Mac unlocks automatically + window returns to normal | Partial | Native unlock on receipt; window unfullscreen works | Real Mac window state recovery, no lingering lock |

## Readiness Scorecard by Area (Estimates)

| Area | % Complete | Formula & Notes |
|---|---|---|
| **Mac app (native lock, Canvas mode, UI)** | 80% | 582 unit tests pass (verified); Canvas scope/lock/finish implemented; native-exit fixes uncommitted; real Mac not tested; no dev-build signing for Keychain/ATS |
| **Windows app** | 30% | Device test clean-up done; shell/guard/watchdog not compiled on this Mac; core logic tested in isolation; no real Windows hardware |
| **iPad app** | 45% | 214 core tests pass (verified 2026-10-30); Assessment Mode not available without Apple entitlement; Canvas integration same as Mac |
| **FairPlay server** | 85% | 1307 tests pass (verified 2026-10-30); native-exit endpoints, Canvas, DKU OJ bridge live; admin key length bug not deployed; no TLS cert automation; unverified on real 10+ student load |
| **DKU OJ bridge** | 60% | Homework login, rescue actions (access codes removed 2026-11), device-key remember-me built; patch 0222 adds migration (violates zero-schema rule); patches 0013–0020 applied but not committed to OJ repo; roster sync and professor materials patches unstarted |
| **Professor tooling** | 40% | Unlock code, move device, reopen, extra time built and untested (issue code removed with the access codes); no professor materials upload (PDF handouts) |
| **LMS support (Canvas)** | 60% | Canvas mode locks, restricts scope, finish detection; SAML/Duo never tested; New Quizzes not tested; real Canvas quiz rendering unverified |
| **Ops & deploy** | 40% | deploy.sh works for prior version; no-prune script needed; native-exit keys generated; cert renewal manual; VM rollback process documented but untested; secret .acmedns.json cleanup pending |
| **Security audit** | 45% | Phase 1 recon complete (both repos); architecture.md and findings.json pending; 21 confirmed findings, none fixed yet; no validation on real hardware; shared app key still in binaries |
| **Testing on real devices** | 5% | Local simulator and unit tests only; no real Mac, iPad, or Windows; no real Canvas or DKU OJ; no network loss, SSO, or Duo scenarios |

## Must-Fix Before DKU OJ Production Use (A)

1. **Native-exit lock ID registration trap** (Blocker: app crash / 404 after relock). Fixes done today to MainWindowController, NativeExit, NativeLock (background registrar with retry-after, honest Leave text) — uncommitted. Impact: high (blocks real exam). *Verify on real Mac, then commit and deploy.*

2. **OJ migration 0222 violates schema rule** (Blocker: owner decision on zero-change rule). DKU OJ patch touches core table; breaking the contract. Impact: high (rule violation). *Owner must decide: approve the migration, request workaround, or block.*

3. **Windows app uncompiled** (.NET toolchain not on this Mac). impact: low for DKU (OJ bridge works Mac-first), medium for future Windows launch. *Delegate Windows build to CI or dotnet-equipped machine.*

4. **Deploy to VM and verify** (Native-exit server, admin key length, native-lock schema). Server committed locally, not pushed or deployed. Impact: high (nothing live yet). *After native-exit trap fix commit, push to GitHub and deploy via deploy-native.sh.*

5. **Real device lock/unlock test** (Canvas login, start → fullscreen lock, Leave + code, auto-unlock). No real Mac tested. Impact: high (only simulator tested). *Test on a real Mac 13/14/15 with real DKU Canvas and OJ.*

6. **Canvas/DKU OJ login flow verification** (SAML + NetID + Duo MFA). Password-only login tested; real SAML/Duo never tested. Impact: high (production gates on SSO). *Test with real DKU credentials.*

7. **Easy-unlock design** (Being written by another agent at docs/DESIGN-easy-unlock.md). Unlocked-until-start-exam not built. Impact: medium (optional feature, but in vision). *Merge easy-unlock design doc when ready.*

## Path to Vision (B): Ordered Milestones

### Milestone 1: DKU OJ Production Ready (2–3 weeks, medium effort)
- Commit and deploy native-exit trap fixes (today's uncommitted changes).
- Resolve OJ migration 0222 (owner decision: approve or request workaround).
- Real device test: Mac, Canvas, DKU OJ, Leave + code exit.
- Deploy to VM (server key, admin code, native-lock schema).
- 5–10 volunteer exam pilots with real students.

### Milestone 2: Windows & iPad Parity (3–4 weeks, large effort)
- Compile and test Windows (Shell/Guard/Watchdog on real hardware or CI).
- iPad Assessment Mode integration (needs Apple entitlement; owner org account decision).
- Real Windows and iPad device tests (Canvas, OJ, lock/unlock, offline tolerance).

### Milestone 3: Vision Completeness (4–6 weeks, medium effort)
- Easy-unlock design & implementation (unlocked-until-start-exam with https and signed start-links).
- Sealed hand-in and offline answer safety for external websites (local recorder, encrypted journal, page snapshots).
- Professor materials upload (PDF handouts).
- Duo and multi-factor authentication real flow testing.
- Connection/"Saved" indicator in the locked bar (offline tolerance UI).

### Milestone 4: B2B & Security (8+ weeks, large effort)
- Security audit findings validation and fixes (21 findings pending; shared app key, code signing, escrow recovery on real HW).
- Signed updates (Sparkle Mac, Updater Windows, in-app iOS).
- Canvas + 2–3 other LMS profiles (Moodle, Blackboard, Brightspace) with test matrix.
- Tenancy, per-org config, audit log (B2B platform).
- Load test on real hardware (stress target 3,000+ writes/s with 10,000+ students across 100+ exams).

## Risks & Open Decisions

### Risks
- **Lock registration trap recurrence** (unverified). Background registrar fixes it, but no guarantee on real Mac. If Keychain loses the stored state after relock, the new registration will also fail. Real Mac Keychain test needed.
- **Canvas SAML/Duo never tested**. DKU uses real NetID. If MFA redirects to a page outside FairPlay or pops a native window, the lock may be broken. Real test mandatory.
- **OJ schema change rule conflict** (high impact). Patch 0222 is valuable (hand-in revisions, materials, professor teaching tools) but breaks the contract. Owner must choose.
- **Windows untested on hardware**. Shell/Guard/Watchdog compiled but never run. Guard permission elevation logic and watchdog relock on crash unverified.
- **Offline answer safety not built**. For Canvas/external sites, if the student loses internet, we have no guarantee their answers are saved; local recorder not yet implemented. Vision promises this.
- **Easy-unlock design unmerged**. Being written separately; if it conflicts with current native-exit logic, rework needed.
- **App key still shared** (security audit finding). All binaries contain the same HMAC key for signatures. Extracted key = ability to forge exit codes. Fix deferred; impact: critical on a compromised device.

### Open Decisions
1. **OJ migration 0222**: Approve, reject, or request a workaround (owner call, blocks production).
2. **Easy-unlock design**: How to implement unlocked-until-start-exam safely? Requires https-only start links with signed device binding.
3. **Canvas SAML test account**: Do we have real DKU NetID + Duo credentials to test live? If not, request or use a mock IdP.
4. **Windows build in CI**: Should we set up .NET in GitHub Actions, or delegate to a separate Windows machine?
5. **iPad entitlement**: Does the owner's org account have Apple Assessment Mode entitlement yet? Needed for full iPad lock.
6. **Professor materials scope**: PDF handouts in FairPlay server or stored in DKU OJ? Design decision pending.
7. **TLS cert automation**: Set up certbot renewal on VM before 2027-01-26 (Let's Encrypt expiry), or request owner sudo?

## Verification Status

- **Verified (tested):** Mac 582 unit tests pass; server 1307 tests pass; local simulator 41/41 OJ e2e; Canvas scope/lock/finish logic; native-exit fixes on code review
- **Read from code only:** Canvas mode implementation; native-lock schema; DKU OJ bridge flow; Windows core logic; iPad core tests
- **Unverified:** All real device testing (Mac, iPad, Windows); real Canvas/DKU OJ/SAML/Duo flows; offline tolerance; load test; Windows Shell/Guard/Watchdog; easy-unlock design (not merged); app key hardening

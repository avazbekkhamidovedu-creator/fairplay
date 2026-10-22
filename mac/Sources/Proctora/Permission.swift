import AppKit

/// 2026-10-22: why "One permission before your exam" could stay up after the student turned
/// FairPlay on, and the fixes.
///
/// macOS grants Accessibility to one signed copy of an app. Ad-hoc signed builds get a new
/// signature with every build, and a Mac can hold several copies called "FairPlay" (an
/// old download, a test copy, the dev build). The single "FairPlay" switch in System
/// Settings can then belong to a different copy than the one running, so turning it on
/// changes nothing for this copy. Fixes offered on the permission screen:
///  - show which copy is running and list other copies (delete them);
///  - "Fix permission": `tccutil reset Accessibility <bundle id>` removes every stale
///    FairPlay entry, then macOS asks again and adds exactly this copy;
///  - "Restart FairPlay" for the rare case where macOS applies the switch only to new runs.
/// Release builds signed with one fixed certificate (build.sh FAIRPLAY_SIGN_SELF) keep the
/// grant across updates, which removes the cause for the next versions.
enum Permission {
    static var bundleId: String { Bundle.main.bundleIdentifier ?? "ai.proctora.student" }

    static var thisCopy: URL { Bundle.main.bundleURL.standardizedFileURL.resolvingSymlinksInPath() }

    /// Other app bundles on this Mac with the same bundle id.
    static func otherCopies() -> [URL] {
        let me = thisCopy.path
        let all = NSWorkspace.shared.urlsForApplications(withBundleIdentifier: bundleId)
        var seen = Set<String>()
        return all.map { $0.standardizedFileURL.resolvingSymlinksInPath() }
            .filter { $0.path != me && !$0.path.contains("/.Trash/") && seen.insert($0.path).inserted }
    }

    /// Removes every Accessibility entry for FairPlay, then asks macOS to add this copy.
    /// Returns false when tccutil failed (the student can still use Open settings).
    @discardableResult
    static func resetAndAsk() -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tccutil")
        p.arguments = ["reset", "Accessibility", bundleId]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        var ok = false
        do {
            try p.run()
            p.waitUntilExit()
            ok = p.terminationStatus == 0
        } catch {
            ok = false
        }
        DebugLog.log("lock", "tccutil reset Accessibility \(bundleId): \(ok ? "ok" : "FAILED")")
        // Short delay: tccd applies the reset before the new request is shown.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            EventTap.requestTrust()
            openSettings()
        }
        return ok
    }

    static func openSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Starts a fresh FairPlay from this same copy and quits this one. Never while locked.
    static func relaunch() {
        guard !LockdownController.shared.isLocked else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-n", thisCopy.path]
        do {
            try p.run()
            DebugLog.log("app", "relaunching FairPlay for the Accessibility permission")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
        } catch {
            DebugLog.log("app", "relaunch failed: \(error.localizedDescription)")
        }
    }

    /// Short path for the screen ("~/Downloads/FairPlay.app").
    static func display(_ url: URL) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let p = url.path
        return p.hasPrefix(home) ? "~" + p.dropFirst(home.count) : p
    }
}

/// 2026-10-22: "Open settings" buttons next to a pre-exam problem ("Turn off Remote Login..."):
/// the page names a pane, the app opens exactly that System Settings pane. Fixed list only;
/// never while locked (the problems are fixed before the lock).
enum SettingsPane {
    static let urls: [String: String] = [
        "sharing": "x-apple.systempreferences:com.apple.Sharing-Settings.extension",
        "datetime": "x-apple.systempreferences:com.apple.Date-Time-Settings.extension",
        "displays": "x-apple.systempreferences:com.apple.Displays-Settings.extension",
        "keyboard": "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    ]

    static func url(_ pane: String) -> URL? { urls[pane].flatMap(URL.init(string:)) }

    @discardableResult
    static func open(_ pane: String) -> Bool {
        guard !LockdownController.shared.isLocked, let url = url(pane) else { return false }
        DebugLog.log("click", "open System Settings: \(pane)")
        return NSWorkspace.shared.open(url)
    }
}

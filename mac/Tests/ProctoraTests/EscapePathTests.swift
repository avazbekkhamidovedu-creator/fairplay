import Testing
import Foundation
import AppKit
import WebKit
@testable import Proctora

enum Source {
    static let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    static func text(_ relative: String) throws -> String {
        try String(contentsOf: root.appendingPathComponent(relative), encoding: .utf8)
    }
    /// The text between the `#if FAIRPLAY_DEV` that opens before `marker` and its closing `#else`/`#endif`.
    static func devOnly(_ text: String, containing marker: String) -> Bool {
        guard let at = text.range(of: marker)?.lowerBound else { return false }
        let before = text[..<at]
        guard let open = before.range(of: "#if FAIRPLAY_DEV", options: .backwards)?.lowerBound else { return false }
        let between = before[open...]
        return !between.contains("#else") && !between.contains("#endif")
    }
}

@Suite struct EscapePathGuardTests {
    @Test func releaseSwallowsTheInvigilatorShortcutAndDoesNothing() throws {
        #expect(!EventTap.postsEmergencyExit(isDev: false, nativeLock: true))
        #expect(!Config.isDevBuild)
        let tap = try Source.text("Sources/Proctora/EventTap.swift")
        #expect(tap.contains("EventTap.postsEmergencyExit(isDev: Config.isDevBuild, nativeLock: EventTap.nativeLockHeld)"))
        let main = try Source.text("Sources/Proctora/main.swift")
        #expect(main.contains("guard EventTap.postsEmergencyExit(isDev: Config.isDevBuild, nativeLock: EventTap.nativeLockHeld) else { return }"))
    }

    @Test func theShortcutStaysForOrdinaryExamLocksAndOnlyTheNativeLockSwallowsIt() {
        #expect(EventTap.postsEmergencyExit(isDev: false, nativeLock: false))
        #expect(EventTap.postsEmergencyExit(isDev: true, nativeLock: false))
        #expect(EventTap.postsEmergencyExit(isDev: true, nativeLock: true))
        #expect(!EventTap.postsEmergencyExit(isDev: false, nativeLock: true))
    }

    @Test func noTextPointsToTheShortcutOrToRestartingTheMac() throws {
        for file in ["LockdownController", "main", "ProtectedBrowserController", "NativeLock"] {
            let text = try Source.text("Sources/Proctora/\(file).swift")
            #expect(!text.contains("Stuck? Invigilator"), "\(file)")
            #expect(!text.contains("Ctrl-Opt-Cmd-E,"), "\(file)")
            #expect(!text.lowercased().contains("restart the mac"), "\(file)")
            #expect(!text.contains("Restarting the Mac"), "\(file)")
        }
        let screen = try Source.text("Sources/Proctora/NativeLock.swift")
        #expect(screen.contains("Ask your instructor for an exit code."))
    }

    @Test func theOfflineExitDialogAndThePromptAreOutOfTheNativeLockPath() throws {
        let window = try Source.text("Sources/Proctora/MainWindowController.swift")
        let offline = try #require(window.range(of: "func promptOfflineExit(reason: String?) {"))
        #expect(window[offline.upperBound...].prefix(120).contains("nativeLockActive"))
        let prompt = try #require(window.range(of: "func promptEmergencyExit() {"))
        #expect(window[prompt.upperBound...].prefix(400).contains("nativeLockActive"))
    }

    @Test func leaveHasNoFreeOrHelpOnlyRoute() throws {
        let browser = try Source.text("Sources/Proctora/ProtectedBrowserController.swift")
        for gone in ["confirmLeave", "showHelp", "helpText", "leaveRoute", "Restarting the Mac", "Cancel attempt", "/finish\""] {
            #expect(!browser.contains(gone), "\(gone)")
        }
        let abort = try #require(browser.range(of: "/abort\""))
        let accepted = try #require(browser.range(of: "NativeExit.settle(result, ticket: ticket, store: effects.setup.store, canRelease: true)"))
        #expect(browser.distance(from: browser.startIndex, to: abort.lowerBound) > browser.distance(from: browser.startIndex, to: accepted.lowerBound))
        #expect(browser.components(separatedBy: "/abort\"").count == 2)
    }

    @Test func devOnlyCodeAndKeyAreCompiledOutOfRelease() throws {
        #expect(!OfflineExit.devCodeMatches("0000"))
        #expect(!OfflineExit.devCodeMatches(""))
        #expect(Config.safetyUnlockMinutes == 0)
        #expect(!Config.floatingLockedWindow)
        let offline = try Source.text("Sources/Proctora/OfflineExit.swift")
        #expect(Source.devOnly(offline, containing: "\"DevExitCode\""))
        #expect(Source.devOnly(offline, containing: "Data(Config.appKey.utf8)"))
        let config = try Source.text("Sources/Proctora/Config.swift")
        #expect(Source.devOnly(config, containing: "\"SafetyUnlockMinutes\""))
        #expect(Source.devOnly(config, containing: "\"WindowLevel\""))
    }

    @Test func nativeLockHasNoUnlockOtherThanItsRelease() throws {
        let lock = try Source.text("Sources/Proctora/LockdownController.swift")
        let unlock = try #require(lock.range(of: "    func unlock() {\n        guard isLocked else { return }\n"))
        #expect(lock[unlock.upperBound...].prefix(80).contains("native.allowsUnlock"))
    }

    @Test func nativeBrowserProfileHasNoPlainHttpAtsException() throws {
        let script = try Source.text("build.sh")
        let condition = try #require(script.range(of: "elif [ -n \"${FAIRPLAY_NATIVE_BROWSER_PROFILE:-}\" ]; then\n"))
        let branch = String(script[condition.upperBound...].prefix(400))
        let line = try #require(branch.components(separatedBy: "\n").first { $0.trimmingCharacters(in: .whitespaces).hasPrefix("ATS=") })
        #expect(line.trimmingCharacters(in: .whitespaces) == "ATS=''")
        #expect(!script.contains("NSExceptionAllowsInsecureHTTPLoads"))
        #expect(!script.contains("NSExceptionDomains"))
    }

    @Test func buildScriptTakesTheNativeExitKey() throws {
        let script = try Source.text("build.sh")
        #expect(script.contains("FAIRPLAY_NATIVE_EXIT_PUBLIC_KEY"))
        #expect(script.contains("NativeExitPublicKey"))
    }
}

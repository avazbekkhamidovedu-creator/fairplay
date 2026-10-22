// 2026-10-22: the permission screen names the running copy (paths under the home folder
// shortened to ~) so students can tell several "FairPlay" copies apart.
import Testing
import Foundation
@testable import Proctora

@Suite struct PermissionTests {
    @Test func homePathsAreShortened() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let app = home.appendingPathComponent("Downloads/FairPlay.app")
        #expect(Permission.display(app) == "~/Downloads/FairPlay.app")
        #expect(Permission.display(URL(fileURLWithPath: "/Applications/FairPlay.app")) == "/Applications/FairPlay.app")
    }

    @Test func thisCopyIsAnAbsoluteFileURL() {
        #expect(Permission.thisCopy.isFileURL)
        #expect(Permission.thisCopy.path.hasPrefix("/"))
    }
}

@Suite struct SettingsPaneTests {
    @Test func onlyKnownPanesOpen() {
        #expect(SettingsPane.url("sharing")?.absoluteString == "x-apple.systempreferences:com.apple.Sharing-Settings.extension")
        #expect(SettingsPane.url("datetime") != nil)
        #expect(SettingsPane.url("displays") != nil)
        #expect(SettingsPane.url("keyboard") != nil)
        #expect(SettingsPane.url("accessibility") != nil)
        #expect(SettingsPane.url("https://example.com") == nil)
        #expect(SettingsPane.url("") == nil)
        #expect(!SettingsPane.open("nope"))
    }
}

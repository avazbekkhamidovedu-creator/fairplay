import Foundation
import Testing
@testable import Proctora

@Suite struct IsolatedProfileTests {
    @Test func persistentPathsAndKeyServicesMatchCompiledProfile() {
        let isolated = Config.isIsolatedTestProfile
        #expect(Config.supportFolderName == (isolated ? "FairPlay-PR4-Isolated" : "FairPlay"))
        #expect(Config.deviceKeyNamespace == (isolated ? "ai.proctora.student.pr4-isolated.mac" : "ai.proctora.student.mac"))
        #expect(Config.rememberKeyService == (isolated ? "FairPlay.pr4-isolated.homeworkRemember" : "FairPlay.homeworkRemember"))
        #expect(Watchdog.markerURL.deletingLastPathComponent().lastPathComponent == Config.supportFolderName)
        #expect(Sealed.folder.lastPathComponent == "sealed")
        #expect(Sealed.folder.deletingLastPathComponent().lastPathComponent == Config.supportFolderName)
        #expect(Config.supportFolderName != (isolated ? "FairPlay" : "FairPlay-PR4-Isolated"))
    }
    @Test func isolatedServerIgnoresInheritedProductionOverride() {
        #if FAIRPLAY_ISOLATED_TEST && FAIRPLAY_DEV
        #expect(Config.isDevBuild)
        #expect(Config.serverURL.absoluteString == "http://127.0.0.1:18080")
        #expect(Config.serverURL.host == "127.0.0.1")
        #expect(Config.serverURL.port == 18080)
        #else
        #expect(!Config.isIsolatedTestProfile)
        #endif
    }
}

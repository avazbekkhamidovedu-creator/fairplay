import Foundation
import Testing
@testable import Proctora

@Suite struct VPNEndToEndTests {
    private func source(_ file: String) throws -> String {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        return try String(contentsOf: root.appendingPathComponent("Sources/Proctora/" + file), encoding: .utf8)
    }

    @Test func noSessionOverridesTheSystemProxyConfiguration() throws {
        for file in ["Net.swift", "NativeBrowserIdentity.swift", "NativeBrowserProvisioning.swift", "NativeLock.swift",
                     "Updater.swift", "ProtectedBrowserController.swift"] {
            let text = try source(file)
            #expect(!text.contains("connectionProxyDictionary"), "\(file)")
            #expect(!text.contains("proxyConfigurations"), "\(file)")
        }
    }

    @Test func nativeCallsAllowASlowVPNPath() {
        #expect(LoadTimeouts.request >= 20)
    }

    @Test func certificateProblemsAreNotAVPNProblem() {
        for code in [NSURLErrorServerCertificateUntrusted, NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateHasBadDate] {
            #expect(LoadOutcome.isTLSFailure(domain: NSURLErrorDomain, code: code))
            #expect(LoadOutcome.isUnreachable(domain: NSURLErrorDomain, code: code))
        }
        #expect(!LoadOutcome.isTLSFailure(domain: NSURLErrorDomain, code: NSURLErrorTimedOut))
        #expect(!LoadOutcome.isTLSFailure(domain: NSURLErrorDomain, code: NSURLErrorCannotFindHost))
        #expect(LoadOutcome.tlsNotice.contains("certificate"))
    }

    @Test func onlyUsableTunnelAddressesCountAsAVPN() {
        func ip(_ a: UInt32, _ b: UInt32, _ c: UInt32, _ d: UInt32) -> UInt32 { a << 24 | b << 16 | c << 8 | d }
        #expect(VPNPromptGate.isVPNCandidateAddress(ip(10, 200, 5, 7)))
        #expect(VPNPromptGate.isVPNCandidateAddress(ip(172, 16, 1, 1)))
        #expect(!VPNPromptGate.isVPNCandidateAddress(ip(169, 254, 3, 3)))
        #expect(!VPNPromptGate.isVPNCandidateAddress(ip(100, 100, 1, 1)))
        #expect(VPNPromptGate.isVPNCandidateAddress(ip(100, 63, 1, 1)))
        #expect(!VPNPromptGate.isVPNCandidateAddress(ip(127, 0, 0, 1)))
        #expect(!VPNPromptGate.isVPNCandidateAddress(0))
        #expect(VPNPromptGate.isTunnelName("utun4") && !VPNPromptGate.isTunnelName("en0") && !VPNPromptGate.isTunnelName("awdl0"))
    }

    @Test func aPathChangeIsDetectedBySignatureAndOnlyReloadsAStuckPage() {
        let wifi = NetworkWatch.signature(satisfied: true, interfaces: ["en0"])
        let vpn = NetworkWatch.signature(satisfied: true, interfaces: ["utun3", "en0"])
        #expect(wifi != vpn)
        #expect(vpn == NetworkWatch.signature(satisfied: true, interfaces: ["en0", "utun3"]))
        #expect(NetworkWatch.signature(satisfied: false, interfaces: []) != wifi)
        #expect(NetworkWatch.shouldReload(afterChangeTo: true, phase: .failed))
        #expect(NetworkWatch.shouldReload(afterChangeTo: true, phase: .loading))
        #expect(!NetworkWatch.shouldReload(afterChangeTo: true, phase: .ready))
        #expect(!NetworkWatch.shouldReload(afterChangeTo: false, phase: .failed))
        #expect(!NetworkWatch.shouldReload(afterChangeTo: true, phase: nil))
        #expect(NetworkWatch.debounce >= 1)
    }

    @Test func universityVPNClientsNeverMatchTheRemoteAccessLists() {
        for bundle in ["com.cisco.secureclient.gui", "com.cisco.anyconnect.gui", "com.cisco.cisco-anyconnect-secure-mobility-client",
                       "com.paloaltonetworks.globalprotect.client", "net.openvpn.connect.app", "com.wireguard.macos", "com.fortinet.forticlient"] {
            #expect(!SystemChecks.isDeniedBundleId(bundle), "\(bundle)")
        }
        for process in ["vpnagentd", "vpnui", "csc_ui", "cisco secure client", "acsock", "aciseposture", "globalprotect", "pangpd",
                        "openvpn", "wireguard-go", "nesessionmanager"] {
            #expect(!SystemChecks.isDeniedProcessName(process), "\(process)")
        }
        for bundle in ["com.teamviewer.teamviewer", "com.anydesk.anydesk", "com.apple.screensharing"] {
            #expect(SystemChecks.isDeniedBundleId(bundle), "\(bundle)")
        }
        for process in ["teamviewer_desktop", "anydesk", "sshd", "screensharingd"] {
            #expect(SystemChecks.isDeniedProcessName(process), "\(process)")
        }
    }
}

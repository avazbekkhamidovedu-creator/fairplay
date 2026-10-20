import Foundation
import DeviceCheck

/// Apple App Attest (CONTRACT.md "App session", M-20): the Secure Enclave proves to the
/// server that requests come from THIS genuine, unmodified FairPlay build on a real
/// Apple device, which closes the gap an extracted HMAC key leaves open.
///
/// Status in the beat ("att="): "0" = not supported here (simulator, old device),
/// "1" = key attested this launch, "?" = supported but not attested (yet).
@MainActor
final class AppAttest {
    static let shared = AppAttest()

    var status: String {
        guard DCAppAttestService.shared.isSupported else { return "0" }
        return attested ? "1" : "?"
    }

    private(set) var attested = false
}

import Foundation
import DeviceCheck

/// Apple App Attest (CONTRACT.md "App session", M-20): NOT BUILT YET.
///
/// Planned: the Secure Enclave proves to the server that requests come from this genuine,
/// unmodified FairPlay build on a real Apple device, which closes the gap an extracted
/// HMAC app key leaves open. That needs the client (generateKey / attestKey / assertions)
/// AND server verification of Apple's attestation chain, plus the App Attest entitlement,
/// which a free Apple ID can't have, so it can only be built and tested on the paid
/// developer account (TestFlight). Until then nothing is attested and no server check
/// relies on it: the app key alone signs, as on the Mac and Windows apps.
///
/// Status in the beat ("att="): "0" = not supported here (simulator, old device),
/// "?" = supported but not attested (always, for now).
@MainActor
final class AppAttest {
    static let shared = AppAttest()

    var status: String {
        guard DCAppAttestService.shared.isSupported else { return "0" }
        return attested ? "1" : "?"
    }

    private(set) var attested = false
}

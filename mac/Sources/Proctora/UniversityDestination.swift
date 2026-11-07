import Foundation

/// Student choices are fixed. Institution and instructor policy owns extra sites.
enum UniversityDestination: String, CaseIterable {
    case dukeCanvas, dkuOJ
    var url: URL {
        #if FAIRPLAY_ISOLATED_TEST
        if self == .dkuOJ, let value = Bundle.main.object(forInfoDictionaryKey: "LocalDKUPreviewURL") as? String,
           let url = URL(string: value), (url.host == "127.0.0.1" && url.scheme == "http") || (url.host == "localhost" && url.scheme == "https" && url.port == 18443) { return url }
        #endif
        if self == .dkuOJ, let profile = NativeBrowserProfile.bundled { return profile.gatewayURL }
        return URL(string: self == .dukeCanvas ? "https://canvas.duke.edu" : "http://10.200.31.64/")!
    }
    var additionalOrigins: String {
        self == .dukeCanvas ? "https://go.canvas.duke.edu, https://shib.oit.duke.edu" : ""
    }
    var note: String {
        self == .dukeCanvas
            ? "Your Mac locks before Canvas opens."
            : url.scheme == "http" ? "DKU OJ uses HTTP on the campus network. The connection is not encrypted."
                                   : "DKU OJ uses a secure connection on the campus network."
    }
}

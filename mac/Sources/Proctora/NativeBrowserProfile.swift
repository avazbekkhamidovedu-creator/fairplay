import Foundation

/// Public deployment configuration sealed inside the signed app bundle.
struct NativeBrowserProfile: Decodable {
    let gateway: String
    let enrollment: String
    let authority: String
    let policyKey: String
    var gatewayURL: URL { URL(string: gateway)! }
    var enrollmentURL: URL { URL(string: enrollment)! }
    var authorityDER: Data { Data(base64Encoded: authority)! }
    private static func approved(_ profile: Self) -> Bool {
        #if FAIRPLAY_ISOLATED_TEST
        if profile.gateway == "https://localhost:18443/" && profile.enrollment == "http://127.0.0.1:18082/api/native-browser/enrollment/" { return true }
        #endif
        return profile.gateway == "https://10.200.31.64:8443/" && profile.enrollment == "https://dku.speakupai.uz/api/native-browser/enrollment/"
    }
    static var bundled: Self? {
        guard let url = Bundle.main.url(forResource: "native-browser-profile", withExtension: "json"),
              let data = try? Data(contentsOf: url), data.count < 20000,
              let profile = try? JSONDecoder().decode(Self.self, from: data),
              approved(profile),
              let policy = Data(base64Encoded: profile.policyKey), policy.count == 32,
              let der = Data(base64Encoded: profile.authority), !der.isEmpty, der.count <= 8192 else { return nil }
        return profile
    }
}

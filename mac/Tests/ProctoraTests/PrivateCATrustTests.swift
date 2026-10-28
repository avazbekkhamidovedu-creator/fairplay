import Testing
import Foundation
import Security
@testable import Proctora

private let caPEM = """
-----BEGIN CERTIFICATE-----
MIIB6jCCAY+gAwIBAgIUU515QOe/W0Gu1PY/FjuZtsY8HNAwCgYIKoZIzj0EAwIw
NTEgMB4GA1UEAwwXRmFpclBsYXkgREtVIFByaXZhdGUgQ0ExETAPBgNVBAoMCEZh
aXJQbGF5MB4XDTI2MTAyODE3NTkwN1oXDTM2MTAyNTE3NTkwN1owNTEgMB4GA1UE
AwwXRmFpclBsYXkgREtVIFByaXZhdGUgQ0ExETAPBgNVBAoMCEZhaXJQbGF5MFkw
EwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE7tnE8f4DkFbKYnZ88LK3VL1D+w+60bXQ
WXQSRxHy32PTrg4gHjdJZ0uUztTSP+ZQcwDzo72SoHe0yde74LhquqN9MHswEgYD
VR0TAQH/BAgwBgEB/wIBADAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFPenCf5v
ZhRmD6mzPZ0EnsbMrLbiMDYGA1UdHgEB/wQsMCqgKDAaghhka3UtdmNtLTQyMjgu
dm0uZHVrZS5lZHUwCocICsge2v////8wCgYIKoZIzj0EAwIDSQAwRgIhAP18WOJx
GYHVcmufrItFweftzAmmCSlCIVvw6k2OF5s+AiEAirZthCmgd2BWSQRsEMe1yEk4
wuNhD0xpzj6SIiHpexk=
-----END CERTIFICATE-----
"""

private let leafPEM = """
-----BEGIN CERTIFICATE-----
MIIB+zCCAaKgAwIBAgIUL3lrwI3z8AohcA1gYeB8hj2KdakwCgYIKoZIzj0EAwIw
NTEgMB4GA1UEAwwXRmFpclBsYXkgREtVIFByaXZhdGUgQ0ExETAPBgNVBAoMCEZh
aXJQbGF5MB4XDTI2MTAyODE3NTkwN1oXDTI4MTAyNzE3NTkwN1owIzEhMB8GA1UE
AwwYZGt1LXZjbS00MjI4LnZtLmR1a2UuZWR1MFkwEwYHKoZIzj0CAQYIKoZIzj0D
AQcDQgAEH4Ccne1rIJ4TP9n+hgmFOaS9clY34LfcmBsnsf90/bYySCzdNI+AZf0G
zqneuBjU6qlJidhC7fCcARif1L3zk6OBoTCBnjAMBgNVHRMBAf8EAjAAMA4GA1Ud
DwEB/wQEAwIHgDATBgNVHSUEDDAKBggrBgEFBQcDATApBgNVHREEIjAgghhka3Ut
dmNtLTQyMjgudm0uZHVrZS5lZHWHBArIHtowHQYDVR0OBBYEFOhwl/CZWt6zpfbp
nMU2Ew9/5LIpMB8GA1UdIwQYMBaAFPenCf5vZhRmD6mzPZ0EnsbMrLbiMAoGCCqG
SM49BAMCA0cAMEQCIGnWr39G56eOfBWBoiCyK4TmNk/ZndOZ8zShRfAUgn+rAiAV
DoIMWLTmH3gCeO3MeUzPtbTNkLq2nlzQUzCNvt6d0w==
-----END CERTIFICATE-----
"""

private let caPin = "lvVS9i2PYsmXkR/U/VqDDJW5+zaAbJmrg3stDr9YL9c="
private let wrongPin = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
private let serverHost = "dku-vcm-4228.vm.duke.edu"

private func trust(for host: String) throws -> SecTrust {
    let leaf = try #require(Pinning.certificate(fromPEM: leafPEM))
    var t: SecTrust?
    let status = SecTrustCreateWithCertificates(leaf, SecPolicyCreateSSL(true, host as CFString), &t)
    #expect(status == errSecSuccess, "\(status)")
    return try #require(t)
}

private func root() throws -> SecCertificate {
    try #require(Pinning.certificate(fromPEM: caPEM))
}

@Suite struct PrivateCATrustTests {
    @Test func acceptsServerHostWithRightPin() throws {
        let ok = Pinning.evaluate(try trust(for: serverHost), host: serverHost, serverHost: serverHost,
                        pins: [caPin], extraRoot: try root())
        #expect(ok, "private CA chain with the CA pin must pass")
    }

    @Test func hostComparisonIgnoresCase() throws {
        let ok = Pinning.evaluate(try trust(for: serverHost), host: serverHost.uppercased(), serverHost: serverHost,
                        pins: [caPin], extraRoot: try root())
        #expect(ok, "host case must not matter")
    }

    @Test func rejectsWrongPin() throws {
        let ok = Pinning.evaluate(try trust(for: serverHost), host: serverHost, serverHost: serverHost,
                        pins: [wrongPin], extraRoot: try root())
        #expect(!ok, "a pin that is not in the chain must fail")
    }

    @Test func rejectsWhenExtraRootNotConfigured() throws {
        let ok = Pinning.evaluate(try trust(for: serverHost), host: serverHost, serverHost: serverHost,
                        pins: [caPin], extraRoot: nil)
        #expect(!ok, "without the extra root the private CA is not trusted")
    }

    @Test func rejectsOtherHostNameEvenWithValidChain() throws {
        let ok = Pinning.evaluate(try trust(for: "other.example.edu"), host: serverHost, serverHost: serverHost,
                        pins: [caPin], extraRoot: try root())
        #expect(!ok, "the leaf is not valid for another host name")
    }

    @Test func otherHostsDoNotGetTheExtraRoot() throws {
        let ok = Pinning.evaluate(try trust(for: "other.example.edu"), host: "other.example.edu", serverHost: serverHost,
                        pins: [caPin], extraRoot: try root())
        #expect(!ok, "the private CA only anchors the exam server host")
        let same = Pinning.evaluate(try trust(for: serverHost), host: "exam.example.edu", serverHost: serverHost,
                          pins: [caPin], extraRoot: try root())
        #expect(!same, "a different host keeps normal validation")
    }

    @Test func chainIncludesTheAnchor() throws {
        let t = try trust(for: serverHost)
        SecTrustSetAnchorCertificates(t, [try root()] as CFArray)
        SecTrustSetAnchorCertificatesOnly(t, false)
        #expect(SecTrustEvaluateWithError(t, nil), "chain must verify")
        let chain = try #require(SecTrustCopyCertificateChain(t) as? [SecCertificate])
        #expect(chain.count == 2, "\(chain.count)")
    }
}

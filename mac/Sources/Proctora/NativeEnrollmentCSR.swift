import Foundation

/// PKCS#10 request using an existing P-256 Keychain-backed key.
/// The server must authenticate the enrollment grant and choose the issued
/// certificate's subject and extensions. A CSR proves key possession only.
enum NativeEnrollmentCSR {
    private static let p256Prefix = Data([
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01,
        0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ])
    private static let ecdsaWithSHA256 = Data([0x30, 0x0a, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x04, 0x03, 0x02])

    private static func length(_ count: Int) -> Data {
        precondition(count >= 0 && count < 65536)
        if count < 128 { return Data([UInt8(count)]) }
        if count < 256 { return Data([0x81, UInt8(count)]) }
        return Data([0x82, UInt8(count >> 8), UInt8(count & 0xff)])
    }

    private static func wrap(_ tag: UInt8, _ content: Data) -> Data {
        Data([tag]) + length(content.count) + content
    }

    static func make(using backing: DeviceKeyBacking) throws -> Data {
        let spki = backing.publicKeyDER
        guard spki.count == 91, spki.starts(with: p256Prefix), spki[p256Prefix.count] == 0x04 else {
            throw DeviceKeyError("enrollment requires a P-256 public key")
        }
        // version=0, empty subject, DER SubjectPublicKeyInfo, empty attributes.
        let requestInfo = wrap(0x30, Data([0x02, 0x01, 0x00, 0x30, 0x00]) + spki + Data([0xa0, 0x00]))
        let signature = try backing.signDER(requestInfo)
        guard !signature.isEmpty && signature.count <= 80 else {
            throw DeviceKeyError("enrollment signature is invalid")
        }
        return wrap(0x30, requestInfo + ecdsaWithSHA256 + wrap(0x03, Data([0x00]) + signature))
    }
}

import FairPlayCore
import Foundation
import LocalAuthentication
import Security

final class KeychainRememberStore: RememberStore {
    private let service = "FairPlay.homeworkRemember"
    private let account = "token"

    private var base: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account,
         kSecAttrSynchronizable as String: false]
    }

    var isWorking: Bool {
        var q = base
        q[kSecReturnAttributes as String] = true
        let status = SecItemCopyMatching(q as CFDictionary, nil)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    func save(token: String, name: String) throws {
        let update: [String: Any] = [kSecValueData as String: Data(token.utf8), kSecAttrComment as String: name]
        var status = SecItemUpdate(base as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            let add = base.merging(update) { _, b in b }
                .merging([kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]) { _, b in b }
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }

    func load() throws -> String? {
        var q = base
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = out as? Data else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
        return String(data: data, encoding: .utf8)
    }

    func forget() throws {
        let status = SecItemDelete(base as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }

    func name() -> String? {
        var q = base
        q[kSecReturnAttributes as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let attrs = out as? [String: Any] else { return nil }
        return attrs[kSecAttrComment as String] as? String
    }
}

enum RememberDeviceGate {
    static func canGate() -> Bool {
        var error: NSError?
        return LAContext().canEvaluatePolicy(.deviceOwnerAuthentication, error: &error)
    }

    static func prompt(_ reason: String, done: @escaping (RememberGateResult) -> Void) {
        let context = LAContext()
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, error in
            DispatchQueue.main.async {
                if ok { return done(.passed) }
                switch (error as? LAError)?.code {
                case .userCancel, .systemCancel, .appCancel, .authenticationFailed, .userFallback: done(.cancelled)
                default: done(.failed)
                }
            }
        }
    }
}

extension HomeworkRemember {
    static func system() -> HomeworkRemember {
        HomeworkRemember(store: KeychainRememberStore(), canGate: RememberDeviceGate.canGate,
                         gate: RememberDeviceGate.prompt)
    }
}

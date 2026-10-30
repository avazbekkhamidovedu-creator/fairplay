import Foundation
import LocalAuthentication
import Security

/// Homework "remember me": the web page keeps a remember token in the device's secure store and
/// gets it back only after Touch ID / Face ID / the device passcode. Pure decisions live here;
/// the Keychain and LAContext part is at the bottom. The same rules, line for line, are the iPad's HomeworkRemember.swift.
protocol RememberStore: AnyObject {
    /// False when the secure store cannot be used right now.
    var isWorking: Bool { get }
    /// Replaces any older token and name in one step.
    func save(token: String, name: String) throws
    func load() throws -> String?
    func forget() throws
    func name() -> String?
}

enum RememberGateResult: Equatable {
    case passed
    case cancelled
    case failed
}

final class HomeworkRemember {
    static let notDuringExam = "Not available during an exam"
    static let unavailable = "Remember me is not available on this device"
    static let nothingStored = "Nothing is remembered on this device"
    static let storeFailed = "The secure store could not be read"
    static let maxTokenLength = 4096
    static let maxNameLength = 200

    private let store: RememberStore
    private let canGate: () -> Bool
    private let gate: (String, @escaping (RememberGateResult) -> Void) -> Void
    private var unlocking = false

    init(store: RememberStore,
                canGate: @escaping () -> Bool,
                gate: @escaping (String, @escaping (RememberGateResult) -> Void) -> Void) {
        self.store = store
        self.canGate = canGate
        self.gate = gate
    }

    var isAvailable: Bool { canGate() && store.isWorking }

    /// The `homeworkRemember` value of the native state message. Never carries the token.
    var stateValue: [String: Any] {
        let available = isAvailable
        let name: Any = (available ? store.name() : nil) ?? NSNull()
        return ["available": available, "name": name]
    }

    /// Handles rememberSave / rememberUnlock / rememberForget; returns false for other actions.
    /// `reply` gets the rememberResult message: the token (or an error) for rememberUnlock, {saved} for rememberSave.
    /// A save that is skipped or fails deletes the older item: the server replaced that token, so keeping it
    /// would make the next unlock present a spent one.
    /// `busy` is asked again after the device prompt: a lock or exam that started meanwhile gets no token.
    @discardableResult
    func handle(action: String, body: [String: Any], busy: @autoclosure @escaping () -> Bool,
                       reply: @escaping ([String: Any]) -> Void) -> Bool {
        switch action {
        case "rememberSave":
            var saved = false
            if isAvailable, let token = body["token"] as? String, let name = body["name"] as? String,
               !token.isEmpty, token.count <= Self.maxTokenLength {
                let shown = String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(Self.maxNameLength))
                saved = (try? store.save(token: token, name: shown)) != nil
            }
            if !saved { try? store.forget() }
            reply(["type": "rememberResult", "saved": saved])
        case "rememberForget":
            try? store.forget()
        case "rememberUnlock":
            unlock(busy: busy, reply: reply)
        default:
            return false
        }
        return true
    }

    private func unlock(busy: @escaping () -> Bool, reply: @escaping ([String: Any]) -> Void) {
        if busy() { return reply(Self.result(error: Self.notDuringExam)) }
        guard isAvailable else { return reply(Self.result(error: Self.unavailable)) }
        guard store.name() != nil else { return reply(Self.result(error: Self.nothingStored)) }
        guard !unlocking else { return reply(Self.result(error: Self.unavailable)) }
        unlocking = true
        gate("Sign in to your homework") { [weak self] outcome in
            guard let self else { return }
            self.unlocking = false
            switch outcome {
            case .cancelled, .failed:
                reply(["type": "rememberResult", "cancelled": true])
            case .passed:
                if busy() { return reply(Self.result(error: Self.notDuringExam)) }
                do {
                    if let token = try self.store.load() {
                        reply(["type": "rememberResult", "token": token])
                    } else {
                        reply(Self.result(error: Self.nothingStored))
                    }
                } catch {
                    reply(Self.result(error: Self.storeFailed))
                }
            }
        }
    }

    private static func result(error: String) -> [String: Any] {
        ["type": "rememberResult", "error": error]
    }
}

final class MemoryRememberStore: RememberStore {
    var isWorking = true
    var failLoad = false
    private var token: String?
    private var savedName: String?
    init() {}
    func save(token: String, name: String) throws { self.token = token; savedName = name }
    func load() throws -> String? {
        if failLoad { throw NSError(domain: "RememberStore", code: 1) }
        return token
    }
    func forget() throws { token = nil; savedName = nil }
    func name() -> String? { token == nil ? nil : savedName }
}

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

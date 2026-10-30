import Foundation

/// Homework "remember me": the web page keeps a remember token in the device's secure store and
/// gets it back only after Touch ID / Face ID / the device passcode. Pure decisions live here;
/// the Keychain and LAContext sit in the app target. The same rules are the Mac's HomeworkRemember.swift.
public protocol RememberStore: AnyObject {
    /// False when the secure store cannot be used right now.
    var isWorking: Bool { get }
    /// Replaces any older token and name in one step.
    func save(token: String, name: String) throws
    func load() throws -> String?
    func forget() throws
    func name() -> String?
}

public enum RememberGateResult: Equatable {
    case passed
    case cancelled
    case failed
}

public final class HomeworkRemember {
    public static let notDuringExam = "Not available during an exam"
    public static let unavailable = "Remember me is not available on this device"
    public static let nothingStored = "Nothing is remembered on this device"
    public static let storeFailed = "The secure store could not be read"
    public static let maxTokenLength = 4096
    public static let maxNameLength = 200

    private let store: RememberStore
    private let canGate: () -> Bool
    private let gate: (String, @escaping (RememberGateResult) -> Void) -> Void
    private var unlocking = false

    public init(store: RememberStore,
                canGate: @escaping () -> Bool,
                gate: @escaping (String, @escaping (RememberGateResult) -> Void) -> Void) {
        self.store = store
        self.canGate = canGate
        self.gate = gate
    }

    public var isAvailable: Bool { canGate() && store.isWorking }

    /// The `homeworkRemember` value of the native state message. Never carries the token.
    public var stateValue: [String: Any] {
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
    public func handle(action: String, body: [String: Any], busy: @autoclosure @escaping () -> Bool,
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

public final class MemoryRememberStore: RememberStore {
    public var isWorking = true
    public var failLoad = false
    private var token: String?
    private var savedName: String?
    public init() {}
    public func save(token: String, name: String) throws { self.token = token; savedName = name }
    public func load() throws -> String? {
        if failLoad { throw NSError(domain: "RememberStore", code: 1) }
        return token
    }
    public func forget() throws { token = nil; savedName = nil }
    public func name() -> String? { token == nil ? nil : savedName }
}

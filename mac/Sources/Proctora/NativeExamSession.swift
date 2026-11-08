import Foundation

/// Binding survives a failed request. An ambiguous claim may have reached DKU,
/// so it also blocks local unlock until a signed terminal response is received.
struct NativeExamSession {
    private(set) var contest: String?
    private(set) var binding: NativeDeviceContext.Binding?
    private(set) var context: NativeDeviceContext?
    var requiresTerminalReceipt: Bool { contest != nil }
    init() {}
    /// A relaunch restores the saved claim; a terminal state must then match the saved binding.
    init(restoring contest: String?, binding: NativeLockState.Binding?) {
        self.contest = contest
        self.binding = binding.map(NativeDeviceContext.Binding.init)
    }
    /// Signed "on hold" while the claim is still live: the lock stays and the page is not shown.
    var onHold: Bool { context?.hold == true && !permitsUnlock }
    mutating func beginClaim(_ key: String) -> Bool {
        guard key.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil,
              contest == nil || contest == key else { return false }
        contest = key
        return true
    }
    mutating func rejectedClaim() {
        if binding == nil { contest = nil; context = nil }
    }
    mutating func accept(_ value: NativeDeviceContext) -> Bool {
        guard value.contest == contest else { return false }
        if let binding {
            guard value.state != "rejected" else { return false }
            guard value.binding == binding else { return false }
        } else {
            // Only a live claim (active, or finishing after a lost reply) can establish an unknown binding;
            // a terminal state needs the saved one.
            guard value.state == "active" || value.state == "finishing" else { return false }
            binding = value.binding
        }
        context = value
        return true
    }
    var permitsUnlock: Bool {
        guard let context, let binding else { return false }
        return context.permitsUnlock(for: binding)
    }
}

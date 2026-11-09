import AppKit

/// Exactly-once dialog completions, independent of WebKit and the real Mac lock.
final class ProtectedBrowserDialogSession {
    enum Kind: Equatable { case alert, confirm, prompt }
    enum Result: Equatable { case acknowledged, confirmed(Bool), text(String?) }
    private struct Pending {
        let id: UUID
        let kind: Kind
        let completion: (Result) -> Void
    }
    private var queue: [Pending] = []
    var activeID: UUID? { queue.first?.id }
    /// Called when a queued dialog becomes the active one after the previous finished.
    var onActivate: ((UUID) -> Void)?

    /// Dialogs run one at a time in arrival order. The caller shows the dialog now when `activeID` equals the returned id.
    @discardableResult
    func begin(_ kind: Kind, completion: @escaping (Result) -> Void) -> UUID {
        let id = UUID()
        queue.append(Pending(id: id, kind: kind, completion: completion))
        return id
    }

    func finish(_ id: UUID, result: Result) {
        guard let current = queue.first, current.id == id else { return }
        queue.removeFirst() // Clear first: completions may synchronously start another dialog.
        let next = queue.first?.id
        current.completion(result)
        if let next, queue.first?.id == next { onActivate?(next) }
    }

    func cancel() {
        let all = queue
        queue = []
        for item in all { item.completion(Self.cancelled(item.kind)) }
    }

    static func cancelled(_ kind: Kind) -> Result {
        switch kind {
        case .alert: return .acknowledged
        case .confirm: return .confirmed(false)
        case .prompt: return .text(nil)
        }
    }
}

/// Native, origin-labelled sheets. No automatic approval of website confirmations.
final class ProtectedBrowserDialogs {
    typealias Kind = ProtectedBrowserDialogSession.Kind
    typealias Result = ProtectedBrowserDialogSession.Result
    private let session = ProtectedBrowserDialogSession()
    private var alert: NSAlert?
    private weak var parent: NSWindow?

    private var queued: [UUID: () -> Void] = [:]

    init() {
        session.onActivate = { [weak self] id in
            DispatchQueue.main.async { self?.queued.removeValue(forKey: id)?() }
        }
    }

    func present(_ kind: Kind, message: String, defaultText: String? = nil, origin: String,
                 in window: NSWindow?, stillAllowed: @escaping () -> Bool,
                 leave: @escaping () -> Void, completion: @escaping (Result) -> Void) {
        guard let window, alert != nil || window.attachedSheet == nil, stillAllowed() else {
            completion(ProtectedBrowserDialogSession.cancelled(kind)); return
        }
        let id = session.begin(kind, completion: completion)
        let show = { [weak self, weak window] in
            guard let self, self.session.activeID == id else { return }
            guard let window, window.attachedSheet == nil, stillAllowed() else {
                self.session.finish(id, result: ProtectedBrowserDialogSession.cancelled(kind)); return
            }
            self.display(id, kind, message: message, defaultText: defaultText, origin: origin,
                         in: window, stillAllowed: stillAllowed, leave: leave)
        }
        if session.activeID == id { show() } else { queued[id] = show }
    }

    private func display(_ id: UUID, _ kind: Kind, message: String, defaultText: String?, origin: String,
                         in window: NSWindow, stillAllowed: @escaping () -> Bool, leave: @escaping () -> Void) {
        let sheet = NSAlert()
        sheet.messageText = "Website message · " + origin
        sheet.informativeText = String(message.prefix(8192))
        sheet.alertStyle = .informational
        sheet.addButton(withTitle: "OK")
        if kind != .alert { sheet.addButton(withTitle: "Cancel") }
        sheet.addButton(withTitle: "Leave")
        var field: NSTextField?
        if kind == .prompt {
            let input = NSTextField(string: String((defaultText ?? "").prefix(8192)))
            input.frame = NSRect(x: 0, y: 0, width: 360, height: 24)
            sheet.accessoryView = input
            field = input
        }
        alert = sheet
        parent = window
        sheet.beginSheetModal(for: window) { [weak self, weak sheet] response in
            guard let self, self.session.activeID == id, self.alert === sheet else { return }
            self.alert = nil
            self.parent = nil
            let leaving = response == (kind == .alert ? .alertSecondButtonReturn : .alertThirdButtonReturn)
            if leaving {
                // Tear down the page through native leave before its canceled JS resumes.
                leave()
                self.session.finish(id, result: ProtectedBrowserDialogSession.cancelled(kind))
                return
            }
            guard response == .alertFirstButtonReturn, stillAllowed() else {
                self.session.finish(id, result: ProtectedBrowserDialogSession.cancelled(kind))
                return
            }
            switch kind {
            case .alert: self.session.finish(id, result: .acknowledged)
            case .confirm: self.session.finish(id, result: .confirmed(true))
            case .prompt: self.session.finish(id, result: .text(field?.stringValue ?? ""))
            }
        }
        if let field { sheet.window.makeFirstResponder(field) }
    }

    func cancel() {
        let current = alert
        let window = parent
        alert = nil
        parent = nil
        if let current, let window, current.window.sheetParent === window {
            window.endSheet(current.window, returnCode: .abort)
        }
        queued.removeAll()
        session.cancel()
    }
}

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
    private var pending: Pending?
    var activeID: UUID? { pending?.id }

    @discardableResult
    func begin(_ kind: Kind, completion: @escaping (Result) -> Void) -> UUID? {
        guard pending == nil else { completion(Self.cancelled(kind)); return nil }
        let id = UUID()
        pending = Pending(id: id, kind: kind, completion: completion)
        return id
    }

    func finish(_ id: UUID, result: Result) {
        guard let current = pending, current.id == id else { return }
        pending = nil // Clear first: completions may synchronously start another dialog.
        current.completion(result)
    }

    func cancel() {
        guard let current = pending else { return }
        finish(current.id, result: Self.cancelled(current.kind))
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

    func present(_ kind: Kind, message: String, defaultText: String? = nil, origin: String,
                 in window: NSWindow?, stillAllowed: @escaping () -> Bool,
                 leave: @escaping () -> Void, completion: @escaping (Result) -> Void) {
        guard let window, window.attachedSheet == nil, stillAllowed() else {
            completion(ProtectedBrowserDialogSession.cancelled(kind)); return
        }
        guard let id = session.begin(kind, completion: completion) else { return }
        let sheet = NSAlert()
        sheet.messageText = "Website message · " + origin
        sheet.informativeText = String(message.prefix(8192))
        sheet.alertStyle = .informational
        sheet.addButton(withTitle: "OK")
        if kind != .alert { sheet.addButton(withTitle: "Cancel") }
        sheet.addButton(withTitle: "Leave and unlock")
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
        session.cancel()
    }
}

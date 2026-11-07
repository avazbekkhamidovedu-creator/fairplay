import AppKit

/// What the Leave sheet needs: the Mac code to read out and a way to try the instructor's code.
struct NativeLeavePrompt {
    let lockCode: String
    /// Development builds without an escrow key only: the code that opens this lock.
    var developmentCode: String? = nil
    let submit: (String) -> NativeExitAttempt
    /// When set, the sheet uses this instead of `submit`: the check runs off the main thread and answers on it.
    var submitAsync: ((String, @escaping (NativeExitAttempt) -> Void) -> Void)? = nil
}

enum NativeLeaveText {
    static let hint = "Ask your instructor for an exit code"

    /// Shown after a code that did not unlock; nil for an accepted one.
    static func message(for attempt: NativeExitAttempt, now: Date) -> String? {
        switch attempt {
        case .accepted:
            return nil
        case .wrong(let remaining):
            return "That code is not right. " + (remaining == 1 ? "1 attempt left." : "\(remaining) attempts left.")
        case .wait(let until):
            let seconds = Int(until.timeIntervalSince(now).rounded(.up))
            return "Too many wrong codes. Try again in \(ExitCodeInput.waitText(seconds: seconds))."
        case .malformed:
            return "Enter the 6-digit code."
        case .refused:
            return "Too many attempts. Ask your instructor to release your Mac."
        }
    }
}

/// One sheet for the whole attempt: a wrong code is answered inline and the sheet stays open.
final class NativeLeavePanel: NSObject {
    private let prompt: NativeLeavePrompt
    private weak var window: NSWindow?
    private let panel: NSPanel
    private let field = NSTextField(frame: NSRect(x: 20, y: 96, width: 340, height: 24))
    private let status = NSTextField(wrappingLabelWithString: "")
    private let spinner = NSProgressIndicator(frame: NSRect(x: 336, y: 62, width: 20, height: 20))
    private let leave = NSButton(title: "Leave", target: nil, action: nil)
    private let cancel = NSButton(title: "Cancel", target: nil, action: nil)
    private static var active: NativeLeavePanel?

    init(prompt: NativeLeavePrompt, window: NSWindow) {
        self.prompt = prompt
        self.window = window
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 380, height: 270), styleMask: [.titled], backing: .buffered, defer: false)
        super.init()
        let title = NSTextField(labelWithString: "Leave")
        title.font = .boldSystemFont(ofSize: 15)
        title.frame = NSRect(x: 20, y: 236, width: 340, height: 20)
        let info = NSTextField(wrappingLabelWithString: "Mac code:  \(prompt.lockCode)\n\n\(NativeLeaveText.hint), then type its 6 digits here. If DKU OJ rejected or paused your session, your instructor must release it."
            + (prompt.developmentCode.map { "\n\nDevelopment build, exit code: \($0)" } ?? ""))
        info.frame = NSRect(x: 20, y: 130, width: 340, height: 100)
        field.placeholderString = NativeLeaveText.hint
        field.target = self; field.action = #selector(leavePressed)
        status.frame = NSRect(x: 20, y: 56, width: 310, height: 34)
        status.textColor = .secondaryLabelColor
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        leave.frame = NSRect(x: 280, y: 14, width: 80, height: 30)
        leave.bezelStyle = .rounded
        leave.keyEquivalent = "\r"
        leave.target = self; leave.action = #selector(leavePressed)
        cancel.frame = NSRect(x: 190, y: 14, width: 80, height: 30)
        cancel.bezelStyle = .rounded
        cancel.keyEquivalent = "\u{1b}"
        cancel.target = self; cancel.action = #selector(cancelPressed)
        for view in [title, info, field, status, spinner, leave, cancel] as [NSView] { panel.contentView?.addSubview(view) }
        panel.initialFirstResponder = field
    }

    func show(reason: String?) {
        guard let window, window.attachedSheet == nil else { return }
        status.stringValue = reason ?? ""
        Self.active = self
        window.beginSheet(panel) { _ in Self.active = nil }
    }

    private func busy(_ on: Bool) {
        field.isEnabled = !on
        leave.isEnabled = !on
        cancel.isEnabled = !on
        if on { spinner.startAnimation(nil); status.stringValue = "Checking the code..." } else { spinner.stopAnimation(nil) }
    }

    @objc private func cancelPressed() { close() }

    private func close() { window?.endSheet(panel) }

    @objc private func leavePressed() {
        guard leave.isEnabled else { return }
        let typed = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        busy(true)
        if let submitAsync = prompt.submitAsync {
            submitAsync(typed) { [weak self] in self?.finished($0) }
        } else {
            finished(prompt.submit(typed))
        }
    }

    private func finished(_ result: NativeExitAttempt) {
        if result == .accepted { close(); return }
        NSSound.beep()
        busy(false)
        status.stringValue = NativeLeaveText.message(for: result, now: Date()) ?? ""
        field.stringValue = ""
        if result == .refused {
            field.isEnabled = false
            leave.isEnabled = false
        } else {
            panel.makeFirstResponder(field)
        }
    }
}

enum NativeLeaveSheet {
    static func present(_ prompt: NativeLeavePrompt, in window: NSWindow?, reason: String? = nil) {
        guard let window, window.attachedSheet == nil else { return }
        NativeLeavePanel(prompt: prompt, window: window).show(reason: reason)
    }
}

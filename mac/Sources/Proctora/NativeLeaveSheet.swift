import AppKit

/// What the Leave sheet needs: the Mac code to read out and a way to try the instructor's code.
struct NativeLeavePrompt {
    let lockCode: String
    /// Development builds without an escrow key only: the code that opens this lock.
    var developmentCode: String? = nil
    /// Changes whenever the lock id (and so the Mac code) was replaced after the sheet opened.
    var lockVersion: () -> Int = { 0 }
    /// The version read together with the code text shown; a later `lockVersion` means the shown code is stale. Nil: taken when the panel is made.
    var shownVersion: Int? = nil
    /// True when the lock now saved has used up its attempts; read on main after a reload.
    var isRefused: () -> Bool = { false }
    /// Reads the current code text and development code; answers on the main thread.
    var reload: (@escaping (String, String?) -> Void) -> Void = { _ in }
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
        case .stale:
            return "This lock changed while the code was checked. Try again."
        }
    }
}

/// One sheet for the whole attempt: a wrong code is answered inline and the sheet stays open.
final class NativeLeavePanel: NSObject {
    private let prompt: NativeLeavePrompt
    private weak var window: NSWindow?
    private let panel: NSPanel
    let field = NSTextField(frame: NSRect(x: 20, y: 96, width: 340, height: 24))
    private let status = NSTextField(wrappingLabelWithString: "")
    let info: NSTextField
    private var shownVersion: Int
    private var reloading = false
    private var refusedLatch = false
    private var watch: Timer?
    private let spinner = NSProgressIndicator(frame: NSRect(x: 336, y: 62, width: 20, height: 20))
    private let leave = NSButton(title: "Leave", target: nil, action: nil)
    private let cancel = NSButton(title: "Cancel", target: nil, action: nil)
    private static var active: NativeLeavePanel?

    init(prompt: NativeLeavePrompt, window: NSWindow) {
        self.prompt = prompt
        self.window = window
        shownVersion = prompt.shownVersion ?? prompt.lockVersion()
        info = NSTextField(wrappingLabelWithString: "")
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 380, height: 270), styleMask: [.titled], backing: .buffered, defer: false)
        super.init()
        let title = NSTextField(labelWithString: "Leave")
        title.font = .boldSystemFont(ofSize: 15)
        title.frame = NSRect(x: 20, y: 236, width: 340, height: 20)
        info.stringValue = Self.infoText(code: prompt.lockCode, development: prompt.developmentCode)
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

    private static func infoText(code: String, development: String?) -> String {
        "Mac code:  \(code)\n\n\(NativeLeaveText.hint), then type its 6 digits here. If DKU OJ rejected or paused your session, your instructor must release it."
            + (development.map { "\n\nDevelopment build, exit code: \($0)" } ?? "")
    }

    static let codeChangedText = "The Mac code changed. Read the new code above to your instructor, then enter the code you get for it."

    func show(reason: String?) {
        guard let window, window.attachedSheet == nil else { return }
        status.stringValue = reason ?? ""
        Self.active = self
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            self?.reopenIfNoLongerRefused()
            _ = self?.refreshIfCodeChanged()
        }
        RunLoop.main.add(timer, forMode: .common)
        watch = timer
        window.beginSheet(panel) { [weak self] _ in
            self?.watch?.invalidate(); self?.watch = nil
            Self.active = nil
        }
        refreshIfCodeChanged()
    }

    /// True while the shown code is stale or being replaced; the displayed code is then updated and a typed code is not sent.
    @discardableResult
    func refreshIfCodeChanged() -> Bool {
        let version = prompt.lockVersion()
        if reloading { return true }
        guard version != shownVersion else { return false }
        reloading = true
        prompt.reload { [weak self] code, development in
            guard let self else { return }
            self.info.stringValue = Self.infoText(code: code, development: development)
            self.status.stringValue = Self.codeChangedText
            self.shownVersion = version
            self.reloading = false
            self.field.stringValue = ""
            if self.refusedLatch && !self.prompt.isRefused() {
                self.refusedLatch = false
                self.field.isEnabled = true
                self.leave.isEnabled = true
            }
        }
        return true
    }

    private func reopenIfNoLongerRefused() {
        guard refusedLatch, !prompt.isRefused() else { return }
        refusedLatch = false
        field.isEnabled = true
        leave.isEnabled = true
        status.stringValue = ""
    }

    private func busy(_ on: Bool) {
        field.isEnabled = !on
        leave.isEnabled = !on
        cancel.isEnabled = !on
        if on { spinner.startAnimation(nil); status.stringValue = "Checking the code..." } else { spinner.stopAnimation(nil) }
    }

    @objc private func cancelPressed() { close() }

    private func close() { window?.endSheet(panel) }

    @objc private func leavePressed() { submit() }

    func submit() {
        guard leave.isEnabled else { return }
        if refreshIfCodeChanged() { status.stringValue = Self.codeChangedText; return }
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
            refusedLatch = true
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

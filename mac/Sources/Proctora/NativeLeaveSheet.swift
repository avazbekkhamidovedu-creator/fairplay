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
    /// One read of the saved lock for both flags: runs off the main thread, answers on main.
    var availability: (@escaping (NativeLeaveAvailability) -> Void) -> Void = { $0(.open) }
    /// Reads the current code text and development code; answers on the main thread.
    var reload: (@escaping (String, String?) -> Void) -> Void = { _ in }
    /// The check runs off the main thread and answers on it.
    var submitAsync: (String, @escaping (NativeExitAttempt) -> Void) -> Void = { _, done in done(.malformed) }
}

/// Whether a typed code can help right now.
enum NativeLeaveAvailability: Equatable {
    case open, refused, releaseOnly, noLock

    /// Both flags derived from one loaded state.
    static func of(_ state: NativeLockState?, now: Date = Date(), mono: NativeExit.Monotonic = NativeExit.systemMonotonic()) -> NativeLeaveAvailability {
        guard let state else { return .noLock }
        if state.verifier == nil { return .releaseOnly }
        return NativeExit.isRefused(state, now: now, mono: mono) ? .refused : .open
    }
}

enum NativeLeaveText {
    static let title = "Emergency exit"

    /// Red, readable in light and dark appearance: white text on a red bezel.
    static func styleEmergency(_ button: NSButton) {
        button.bezelStyle = .rounded
        button.isBordered = true
        button.bezelColor = .systemRed
        button.contentTintColor = .white
        button.attributedTitle = NSAttributedString(string: button.title, attributes: [
            .foregroundColor: NSColor.white, .font: NSFont.systemFont(ofSize: NSFont.systemFontSize, weight: .semibold)])
        button.setAccessibilityLabel(title)
    }
    static let notice = "Your instructor can release this Mac from DKU OJ. It unlocks by itself, usually within about 25 seconds. If you finished, use the hand-in on the exam page."
    static let offlineNotice = "No internet? Read the lock code below to your instructor, then type the 6-digit exit code you get. This works without Wi-Fi."
    static let hint = "Instructor's 6-digit exit code"
    static let unlockTitle = "Unlock"
    static let releaseOnlyMessage = "This lock has no exit code yet. Only your instructor's release from DKU OJ can end it. FairPlay keeps trying to register it in the background."
    static let noLockMessage = "No saved lock on this Mac. Only an instructor release from DKU OJ can end it."
    static let refusedMessage = "Too many attempts. Only your instructor's release from DKU OJ can end this lock."


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
            return refusedMessage
        case .noVerifier:
            return releaseOnlyMessage
        case .stale:
            return "This lock changed while the code was checked. Try again."
        }
    }
}

/// One sheet for the whole attempt: a wrong code is answered inline and the sheet stays open.
final class NativeLeavePanel: NSObject, NSTextFieldDelegate {
    private let prompt: NativeLeavePrompt
    private weak var window: NSWindow?
    private let panel: NSPanel
    let field = NSTextField(frame: NSRect(x: 20, y: 96, width: 340, height: 24))
    let status = NSTextField(wrappingLabelWithString: "")
    let info: NSTextField
    private var shownVersion: Int
    private var reloading = false
    /// What the field and status currently show; the status text is only rewritten when this changes.
    private var shownAvailability: NativeLeaveAvailability = .open
    private(set) var checking = false
    private var ticks = 0
    /// The reason or code-changed hint stays until the student types or submits.
    private var keepStatus = false
    private var hintText = ""
    static let availabilityEvery = 5
    private var syncing = false
    private var watch: Timer?
    private let spinner = NSProgressIndicator(frame: NSRect(x: 336, y: 62, width: 20, height: 20))
    let leave = NSButton(title: NativeLeaveText.unlockTitle, target: nil, action: nil)
    private let cancel = NSButton(title: "Cancel", target: nil, action: nil)
    private static var active: NativeLeavePanel?

    static func endActive(in window: NSWindow?) {
        guard let window, let panel = active?.panel, window.attachedSheet === panel else { return }
        window.endSheet(panel)
    }

    init(prompt: NativeLeavePrompt, window: NSWindow) {
        self.prompt = prompt
        self.window = window
        shownVersion = prompt.shownVersion ?? prompt.lockVersion()
        info = NSTextField(wrappingLabelWithString: "")
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 380, height: 360), styleMask: [.titled], backing: .buffered, defer: false)
        super.init()
        let title = NSTextField(labelWithString: NativeLeaveText.title)
        title.font = .boldSystemFont(ofSize: 15)
        title.frame = NSRect(x: 20, y: 326, width: 340, height: 20)
        info.stringValue = Self.infoText(code: prompt.lockCode, development: prompt.developmentCode)
        info.frame = NSRect(x: 20, y: 130, width: 340, height: 190)
        field.placeholderString = NativeLeaveText.hint
        field.delegate = self
        field.target = self; field.action = #selector(leavePressed)
        status.frame = NSRect(x: 20, y: 50, width: 310, height: 42)
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
        "\(NativeLeaveText.notice)\n\n\(NativeLeaveText.offlineNotice)\n\nLock code:  \(code)"
            + (development.map { "\n\nDevelopment build, exit code: \($0)" } ?? "")
    }

    static let codeChangedText = "The Mac code changed. Read the new code above to your instructor, then enter the code you get for it."

    func show(reason: String?) {
        guard let window, window.attachedSheet == nil else { return }
        status.stringValue = reason ?? ""
        keepStatus = !(reason ?? "").isEmpty; hintText = reason ?? ""
        Self.active = self
        syncAvailability()
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            // The code version is a cheap read every second; the saved lock is only loaded every 5 s.
            self.ticks += 1
            if self.ticks % Self.availabilityEvery == 0 { self.syncAvailability() }
            _ = self.refreshIfCodeChanged()
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
            self.keepStatus = true; self.hintText = Self.codeChangedText
            self.shownVersion = version
            self.reloading = false
            self.field.stringValue = ""
            self.syncAvailability()    // a new lock id: look at once
        }
        return true
    }

    /// No typed code can help while the attempt cap is used up or the lock has no verifier: the field is off and the
    /// reason is shown. It comes back by itself when that changes (the registration finished, the window has room).
    /// The store is read once per tick, off the main thread; nothing changes while a code is being checked, and the
    /// status text is only rewritten when the availability itself changes.
    func syncAvailability() {
        guard !checking, !syncing else { return }
        syncing = true
        prompt.availability { [weak self] state in
            guard let self else { return }
            self.syncing = false
            self.apply(state)
        }
    }

    func apply(_ state: NativeLeaveAvailability) {
        guard !checking, state != shownAvailability else { return }
        shownAvailability = state
        switch state {
        case .open:
            field.isEnabled = true; leave.isEnabled = true
            status.stringValue = keepStatus ? hintText : ""
        case .refused:
            field.isEnabled = false; leave.isEnabled = false
            status.stringValue = NativeLeaveText.refusedMessage
        case .releaseOnly:
            field.isEnabled = false; leave.isEnabled = false
            status.stringValue = NativeLeaveText.releaseOnlyMessage
        case .noLock:
            field.isEnabled = false; leave.isEnabled = false
            status.stringValue = NativeLeaveText.noLockMessage
        }
    }

    private func busy(_ on: Bool) {
        checking = on
        cancel.isEnabled = !on
        field.isEnabled = !on && shownAvailability == .open
        leave.isEnabled = !on && shownAvailability == .open
        if on { spinner.startAnimation(nil); status.stringValue = "Checking the code..." } else { spinner.stopAnimation(nil) }
    }

    func controlTextDidChange(_ obj: Notification) { keepStatus = false }

    @objc private func cancelPressed() { close() }

    private func close() { window?.endSheet(panel) }

    @objc private func leavePressed() { submit() }

    func submit() {
        guard leave.isEnabled, !checking else { return }
        if refreshIfCodeChanged() { status.stringValue = Self.codeChangedText; keepStatus = true; hintText = Self.codeChangedText; return }
        keepStatus = false
        let typed = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        busy(true)
        prompt.submitAsync(typed) { [weak self] in self?.finished($0) }
    }

    private func finished(_ result: NativeExitAttempt) {
        if result == .accepted { close(); return }
        NSSound.beep()
        busy(false)
        status.stringValue = NativeLeaveText.message(for: result, now: Date()) ?? ""
        field.stringValue = ""
        defer { syncAvailability() }    // an attempt may have used up the cap: look at once
        if result == .refused || result == .noVerifier {
            shownAvailability = result == .refused ? .refused : .releaseOnly
            field.isEnabled = false
            leave.isEnabled = false
        } else {
            panel.makeFirstResponder(field)
        }
    }
}

enum NativeLeaveSheet {
    /// A sheet (the Emergency exit one) must never outlive the lock it belongs to: every lock end ends it.
    /// Only the Emergency exit sheet this app opened: a file panel or another sheet is not ours to end.
    static func end(in window: NSWindow?) {
        NativeLeavePanel.endActive(in: window)
    }

    static func present(_ prompt: NativeLeavePrompt, in window: NSWindow?, reason: String? = nil) {
        guard let window, window.attachedSheet == nil else { return }
        NativeLeavePanel(prompt: prompt, window: window).show(reason: reason)
    }
}

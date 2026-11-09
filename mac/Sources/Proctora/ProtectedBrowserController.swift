import AppKit
import WebKit

private final class ProtectedUniversityWebView: WKWebView {
    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        menu.removeAllItems()
        super.willOpenMenu(menu, with: event)
    }
    override func quickLook(with event: NSEvent) {}
    /// Save, Save As, Print and Open stay with the page: a PDF from an allowed site is read-only here.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if flags.contains(.command), ["s", "p", "o"].contains(event.charactersIgnoringModifiers?.lowercased() ?? "") { return true }
        return super.performKeyEquivalent(with: event)
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { [] }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { [] }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool { false }
}

/// Separate opt-in native browser. Never receives FairPlay bridge scripts, tokens or an exam session.
final class ProtectedBrowserController: NSObject, WKNavigationDelegate, WKUIDelegate {
    private weak var container: NSView?
    private var overlay: NSView?
    private var browser: WKWebView?
    private var parents: [WKWebView] = []
    private var orderedTabs: [WKWebView] = []
    private var nativeExam = NativeExamSession()
    private var nativeRequestInFlight = false
    private var nativeGeneration = UUID()
    private lazy var nativeIdentity = NativeBrowserIdentity(origin: UniversityDestination.dkuOJ.url,
        ownsLock: { [weak self] in self?.lockIsOwned == true })
    private var failureDescription: String?
    private var errorPageNavigation: WKNavigation?
    private lazy var nativeHTTP = nativeIdentity.makeSession()
    private var failureLog = RepeatedFailureLog()
    private var renewCancel: (() -> Void)?
    private var holdCancel: (() -> Void)?
    private var handInCancels: [() -> Void] = []
    private lazy var renewSchedule = NativeRenewSchedule(random: effects.random)
    private var contextInFlight = false
    private var activeExamBinding: String?
    private var approvedSites: [String] = []
    private var approvedResources: [String] = []
    private var approvedAssets: [String] = []
    private var refusedContest: String?
    private var failsafe = DeadlineFailsafe()
    private var hostAnsweredAt: Date?
    private var failsafeCancel: (() -> Void)?
    private var pickerSites: [String] = []
    private var sitePicker: NSPopUpButton?
    private var nativeProvisioned = false
    private var retainedWebsiteStore: WKWebsiteDataStore?
    private var policyRevision = UUID()
    private var policyExpires: Date?
    private let drafts = BrowserDraftStore()
    private var draftTimer: Timer?
    private var draftStatus: NSTextField?
    private var restoreButton: NSButton?
    private var draftScope: BrowserDraftScope?
    private var draftFields: [String: String]?
    private var pendingDraft: BrowserDraftStore.Record?
    private var draftReadInFlight = false
    private var tabStrip: NSStackView?
    private var recoveryAttempts: Set<ObjectIdentifier> = []
    private var closePopupButton: NSButton?
    private var retryButton: NSButton?
    private var vpnButton: NSButton?
    private var loadRetry = LoadRetryPolicy()
    private var loadRetryCancel: (() -> Void)?
    private var networkWatch: NetworkWatch?
    private var policy: ProtectedBrowserPolicy?
    private var flow = ProtectedBrowserFlow()
    private var destinationPicker: NSSegmentedControl?
    private var testDeviceButton: NSButton?
    private var selection = UniversityDestination.dkuOJ
    private let testDevice: () -> Void
    private let stopDeviceTest: () -> Void
    private var customSetup = false
    private var startField: NSTextField?
    private var originsField: NSTextField?
    private var httpOption: NSButton?
    private var notice: NSTextField?
    private var startButton: NSButton?
    private var backButton: NSButton?
    private var reloadButton: NSButton?
    private var navigationState: ProtectedBrowserNavigation?
    private var activeNavigation: WKNavigation?
    private var policyDenial: ProtectedBrowserPolicyDenial?
    private var activeDownloads: [ObjectIdentifier: (download: WKDownload, watch: NSKeyValueObservation?, file: URL?)] = [:]
    private var ruleIdentifier: String?
    private var pendingRuleIdentifier: String?
    private var held = false
    private let dialogs = ProtectedBrowserDialogs()
    private let busy: () -> Bool
    private let didClose: () -> Void
    struct Effects {
        let checks: () -> [String]
        let acquire: () -> Bool
        let ownsLock: () -> Bool
        let release: () -> Void
        let load: (WKWebView, URL) -> Void
        var configuration: (WKContentRuleList) -> WKWebViewConfiguration = ProtectedBrowserController.websiteConfiguration
        var diagnostic: (String) -> Void = { DebugLog.log("protected-browser", $0) }
        var reload: (WKWebView, Bool) -> Void = { view, hard in
            if hard { view.reloadFromOrigin() } else { view.reload() }
        }
        var showError: (WKWebView, String, URL) -> WKNavigation? = { $0.loadHTMLString($1, baseURL: $2) }
        var presentLeave: (NativeLeavePrompt, NSWindow?) -> Void = { _, _ in }
        var setup: NativeLockSetup = .dev
        var now: () -> Date = Date.init
        var monotonic: () -> NativeExit.Monotonic = NativeExit.systemMonotonic
        var random: () -> Double = { Double.random(in: 0..<1) }
        var schedule: (TimeInterval, @escaping () -> Void) -> () -> Void = { delay, fire in
            let timer = Timer(timeInterval: max(delay, 0), repeats: false) { _ in fire() }
            RunLoop.main.add(timer, forMode: .common)
            return { timer.invalidate() }
        }
        static var live: Effects {
            var effects = Effects(checks: { LockdownController.shared.preflightProblems().map { $0.message } },
                    acquire: {
                        Allowlist.setTeacherAllowed(nil)
                        guard LockdownController.shared.preflightProblems().isEmpty else { return false }
                        return LockdownController.shared.acquireNativeLock()
                    },
                    ownsLock: { LockdownController.shared.isLocked && LockdownController.shared.nativeLockActive && AppSession.shared.exam == nil },
                    release: { LockdownController.shared.releaseNativeLock() },
                    load: { $0.load(URLRequest(url: $1)) })
            effects.setup = .live
            effects.presentLeave = { NativeLeaveSheet.present($0, in: $1) }
            return effects
        }
    }
    private let effects: Effects
    var isOpen: Bool { overlay != nil }
    var holdsNativeLock: Bool { isOpen && lockIsOwned }

    init(busy: @escaping () -> Bool, didClose: @escaping () -> Void,
         testDevice: @escaping () -> Void = {}, stopDeviceTest: @escaping () -> Void = {}) {
        self.busy = busy
        self.didClose = didClose
        self.effects = .live
        self.testDevice = testDevice
        self.stopDeviceTest = stopDeviceTest
    }

    // Dependency injection for isolated tests; production callers always use the live initializer above.
    init(busy: @escaping () -> Bool, didClose: @escaping () -> Void, effects: Effects) {
        self.effects = effects
        self.testDevice = {}
        self.stopDeviceTest = {}
        self.customSetup = true
        self.busy = busy
        self.didClose = didClose
    }

    func show(in container: NSView) {
        guard !isOpen, !busy(), !held else { return }
        self.container = container
        flow = ProtectedBrowserFlow()
        let root = NSView(frame: container.bounds)
        root.autoresizingMask = [.width, .height]
        root.wantsLayer = true
        root.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        container.addSubview(root)
        overlay = root
        VersionLabel.add(to: root)

        if customSetup { showFixtureSetup(in: root, container: container); return }
        selection = .dkuOJ
        func label(_ text: String, size: CGFloat = 15, bold: Bool = false) -> NSTextField {
            let view = NSTextField(wrappingLabelWithString: text)
            view.font = .systemFont(ofSize: size, weight: bold ? .semibold : .regular)
            view.textColor = bold ? .labelColor : .secondaryLabelColor
            return view
        }
        let left = NSStackView(views: [BrandMark.view(size: 64),
            label("Focus on your exam.", size: 30, bold: true),
            label("Your familiar DKU website, inside FairPlay."),
            label("✓  A locked workspace during your exam"),
            label("✓  Local recovery for supported text answers"),
            label("✓  Instructor-approved websites in tabs"),
            label("Sign in with your usual DKU OJ account. No extra exam code.")])
        left.orientation = .vertical; left.alignment = .leading; left.spacing = 24
        let address = label(selection.url.absoluteString, size: 12)
        startField = address
        let start = DeviceTestPill(title: "Continue with DKU OJ", target: self, action: #selector(reviewBeforeStart))
        start.fillColor = BrandMark.blue; start.showsDeviceIcon = false
        start.bezelStyle = .rounded; start.controlSize = .large
        start.bezelColor = BrandMark.blue; start.contentTintColor = .white
        start.font = .systemFont(ofSize: 16, weight: .semibold)
        startButton = start
        let status = label("Save your other work first. We’ll check this Mac before locking.", size: 13)
        notice = status
        let institutionMark = NSImageView()
        if let path = Bundle.main.path(forResource: "DKUOJ", ofType: "ico") {
            institutionMark.image = NSImage(contentsOfFile: path)
        }
        institutionMark.setAccessibilityLabel("DKU OJ logo")
        institutionMark.widthAnchor.constraint(equalToConstant: 48).isActive = true
        institutionMark.heightAnchor.constraint(equalToConstant: 48).isActive = true
        let right = NSStackView(views: [institutionMark, label("DKU OJ", size: 32, bold: true),
            label("Duke Kunshan University", size: 16, bold: true),
            label("Your courses, assignments and exams."), start, status])
        right.orientation = .vertical; right.alignment = .leading; right.spacing = 20
        right.edgeInsets = NSEdgeInsets(top: 32, left: 32, bottom: 32, right: 32)
        right.wantsLayer = true; right.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        right.layer?.cornerRadius = 20
        right.layer?.borderWidth = 1; right.layer?.borderColor = NSColor.separatorColor.cgColor
        let columns = NSStackView(views: [left, right])
        columns.spacing = 56; columns.alignment = .centerY
        columns.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(columns)
        let check = DeviceTestPill(title: "Test my device", target: self, action: #selector(testDevicePressed))
        check.bezelStyle = .rounded; check.controlSize = .large
        check.bezelColor = NSColor(srgbRed: 245/255, green: 183/255, blue: 0, alpha: 1)
        check.contentTintColor = .white; check.font = .systemFont(ofSize: 16, weight: .bold)
        check.translatesAutoresizingMaskIntoConstraints = false
        testDeviceButton = check; root.addSubview(check)
        NSLayoutConstraint.activate([
            check.topAnchor.constraint(equalTo: root.topAnchor, constant: 24),
            check.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -32),
            columns.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            columns.topAnchor.constraint(greaterThanOrEqualTo: check.bottomAnchor, constant: 16),
            columns.bottomAnchor.constraint(lessThanOrEqualTo: root.bottomAnchor, constant: -32),
            columns.leadingAnchor.constraint(greaterThanOrEqualTo: root.leadingAnchor, constant: 32),
            columns.trailingAnchor.constraint(lessThanOrEqualTo: root.trailingAnchor, constant: -32),
            left.widthAnchor.constraint(equalToConstant: 340),
            right.widthAnchor.constraint(equalToConstant: 360)
        ])
        let centered = columns.centerYAnchor.constraint(equalTo: root.centerYAnchor)
        centered.priority = .defaultHigh
        centered.isActive = true
    }

    static let keychainMessage = "This copy of FairPlay is not set up for secure exams. Contact your instructor. Your Mac was not locked."

    /// Without the OJ's public key no signed hand-in receipt can ever unlock the Mac, so the lock must not start.
    static func startBlocker(customSetup: Bool, policyKeyPresent: Bool, isDevBuild: Bool,
                             requireDataProtection: Bool = false, hasKeychainEntitlement: Bool = true) -> String? {
        if !customSetup, !isDevBuild, requireDataProtection, !hasKeychainEntitlement { return keychainMessage }
        guard !customSetup, !policyKeyPresent, !isDevBuild else { return nil }
        return "This copy of FairPlay cannot confirm your hand-in with DKU OJ, so it will not lock your Mac. Install the latest FairPlay from DKU OJ and try again. Your Mac is still unlocked."
    }

    static let secureConnectionMessage = "This exam needs a secure connection (https). Contact your instructor. Your Mac was not locked."

    /// A release build locks for an exam destination only over https and with the bundled device
    /// identity profile; plain http stays possible in development builds and custom browsing setups.
    /// The address is never rewritten.
    static func secureBlocker(customSetup: Bool, isDevBuild: Bool, scheme: String?, profileBundled: Bool) -> String? {
        guard !customSetup, !isDevBuild else { return nil }
        return scheme?.lowercased() == "https" && profileBundled ? nil : secureConnectionMessage
    }

    /// Any answer below 500 means the OJ server responded; only transport errors and 5xx read as down.
    static func ojReachable(status: Int?, failed: Bool) -> Bool {
        guard !failed, let status else { return false }
        return status < 500
    }

    static let transportUnavailableText = "DKU OJ is unavailable. Check your connection and try again. Your Mac is still unlocked."

    /// A transport error keeps the app text; an HTTP answer is shown with its status and the OJ page's own title.
    static func preLockUnavailableNotice(status: Int?, failed: Bool, data: Data?) -> String {
        guard !failed, let status else { return transportUnavailableText }
        return "DKU OJ answered HTTP \(status)" + (OJPage.summary(data).map { ": " + $0 } ?? "") + ". Your Mac is still unlocked."
    }

    static func errorPageHTML(description: String, url: URL?) -> String {
        func esc(_ t: String) -> String {
            t.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
        }
        return "<!doctype html><html><head><meta charset=\"utf-8\"><title>" + esc(description)
            + "</title></head><body style=\"font-family:-apple-system,sans-serif;margin:12vh auto;max-width:560px;color:#222\">"
            + "<h2>This page can\u{2019}t be reached</h2><p>" + esc(description) + "</p>"
            + (url.map { "<p style=\"color:#666;word-break:break-all\">" + esc($0.absoluteString) + "</p>" } ?? "")
            + "</body></html>"
    }

    var examBinding: String? { activeExamBinding }
    var approvedSitesExpiry: Date? { policyExpires }
    var restoredBinding: NativeDeviceContext.Binding? { nativeExam.binding }

    @objc private func reviewBeforeStart() {
        guard flow.state == .setup, !busy() else { return }
        if let blocker = Self.startBlocker(customSetup: customSetup, policyKeyPresent: Config.universityPolicyPublicKey != nil,
                                           isDevBuild: Config.isDevBuild,
                                           requireDataProtection: Config.requiresDataProtectionKeychain,
                                           hasKeychainEntitlement: KeychainNativeLockStore.signedWithKeychainEntitlement) {
            notice?.stringValue = blocker
            return
        }
        if let blocker = Self.secureBlocker(customSetup: customSetup, isDevBuild: Config.isDevBuild,
                                            scheme: selection.url.scheme, profileBundled: NativeBrowserProfile.bundled != nil) {
            notice?.stringValue = blocker
            return
        }
        startButton?.isEnabled = false
        notice?.stringValue = "Connecting to DKU OJ…"
        if NativeBrowserProfile.bundled != nil, !nativeProvisioned {
            Task { [weak self] in
                guard let self else { return }
                do {
                    try await self.nativeIdentity.prepare()
                    await MainActor.run { self.nativeProvisioned = true; self.reviewBeforeStart() }
                } catch {
                    await MainActor.run {
                        self.startButton?.isEnabled = true
                        self.diagnostic("native provisioning failed: \(error)", url: nil)
                        self.notice?.stringValue = "Device verification is unavailable. Try again before starting your exam."
                    }
                }
            }
            return
        }
        var probe = selection.url
        #if FAIRPLAY_ISOLATED_TEST
        if probe.host == "localhost", probe.port == 18443 { probe = URL(string: "http://127.0.0.1:18081/")! }
        #endif
        var request = URLRequest(url: probe.appendingPathComponent("accounts/login/"))
        request.timeoutInterval = LoadTimeouts.request
        nativeHTTP.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self, self.isOpen, self.flow.state == .setup else { return }
                self.startButton?.isEnabled = true
                let status = (response as? HTTPURLResponse)?.statusCode
                guard Self.ojReachable(status: status, failed: error != nil) else {
                    self.notice?.stringValue = Self.preLockUnavailableNotice(status: status, failed: error != nil, data: data)
                    return
                }
                self.prepareDraftStorage(allowInteraction: false)
            }
        }.resume()
    }

    @objc private func prepareRecoveryPressed() { prepareDraftStorage(allowInteraction: true) }
    private func prepareDraftStorage(allowInteraction: Bool) {
        guard !lockIsOwned, flow.state == .setup else { return }
        startButton?.isEnabled = false
        notice?.stringValue = "Checking local answer recovery…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let ready = (try? self.drafts.prepare(allowInteraction: allowInteraction)) != nil
            DispatchQueue.main.async {
                guard self.isOpen, self.flow.state == .setup, !self.lockIsOwned else { return }
                self.startButton?.isEnabled = true
                if ready {
                    self.startButton?.title = "Continue with DKU OJ"
                    self.startButton?.action = #selector(self.reviewBeforeStart)
                    self.presentDeviceReview()
                } else {
                    self.startButton?.title = "Enable local recovery"
                    self.startButton?.action = #selector(self.prepareRecoveryPressed)
                    self.notice?.stringValue = "macOS needs permission to read your existing recovery key. Enable local recovery before locking."
                }
            }
        }
    }

    private func presentDeviceReview() {
        guard flow.state == .setup, !busy() else { return }
        let problems = checks()
        let apps = LockdownController.names(LockdownController.shared.appsToClose())
        let alert = NSAlert()
        alert.messageText = "Before you begin"
        if !problems.isEmpty {
            alert.informativeText = "Your Mac was not locked. Fix this, then press Continue again:\n\n" + problems.joined(separator: "\n")
                + "\n\n" + Config.versionLabelText()
            alert.addButton(withTitle: "Back")
            alert.runModal()
            return
        }
        alert.informativeText = "Device checks passed. Remote access checks passed."
            + (apps.isEmpty ? "\nNo apps need closing." : "\n\nApps to close:\n" + apps.joined(separator: "\n"))
            + "\n\nSave your work first. Closing apps may lose unsaved changes."
            + "\n\n" + Self.beginText + "\n\n" + Config.versionLabelText()
        alert.addButton(withTitle: apps.isEmpty ? "Lock and open" : "Quit all and continue")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        if apps.isEmpty && LockdownController.shared.appsToClose().isEmpty { startPressed(); return }
        startButton?.isEnabled = false
        LockdownController.shared.quitAppsBeforeLock { [weak self] remaining in
            guard let self else { return }
            self.startButton?.isEnabled = true
            if remaining.isEmpty { self.startPressed() }
            else { self.notice?.stringValue = "Close these apps and try again: " + remaining.joined(separator: ", ") }
        }
    }

    private func showFixtureSetup(in root: NSView, container: NSView) {
        let title = NSTextField(labelWithString: "Protected university browser")
        title.font = .systemFont(ofSize: 25, weight: .semibold)
        let detail = NSTextField(wrappingLabelWithString: "Local client setup, not university registration. Your university keeps its own login, questions and submissions. No FairPlay exam code is needed in this separate mode.")
        let url = NSTextField(string: "")
        url.placeholderString = "https://your-university.example.edu"
        let origins = NSTextField(string: "")
        origins.placeholderString = "https://login.example.edu, https://assets.example.edu"
        let safety = NSTextField(wrappingLabelWithString: "Save your work first. Locking restricts this Mac and may close other apps. Only the listed sites can load. You can leave and unlock at any time; leaving does not submit answers. Duke Canvas redirects are included. Other universities need their exact login, Duo and resource addresses here. Some sign-in methods and uploads require institution-specific support. This does not protect other devices or previously downloaded files. HTTP is unencrypted and can be read or changed by others. Never enter passwords or sensitive information on HTTP.")
        let http = NSButton(checkboxWithTitle: "Allow HTTP websites (unencrypted)", target: nil, action: nil)
        http.isEnabled = true
        http.toolTip = "Enable only when your university explicitly uses HTTP. Traffic can be read or changed by others."
        httpOption = http
        let status = NSTextField(wrappingLabelWithString: "Check this Mac, then choose Lock and open. No university page opens before the lock succeeds.")
        notice = status
        startField = url
        originsField = origins
        let check = NSButton(title: "Check this Mac", target: self, action: #selector(checkPressed))
        let start = NSButton(title: "Lock and open", target: self, action: #selector(startPressed))
        startButton = start
        let cancel = NSButton(title: "Back to FairPlay", target: self, action: #selector(leavePressed))
        let buttons = NSStackView(views: [check, start, cancel])
        buttons.spacing = 12
        let stack = NSStackView(views: [title, detail, NSTextField(labelWithString: "University start URL"), url,
                                      NSTextField(labelWithString: "Additional login / resource origins (Duke Canvas login included)"), origins,
                                      http, safety, status, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: root.centerYAnchor),
            stack.widthAnchor.constraint(equalToConstant: 700),
            url.widthAnchor.constraint(equalTo: stack.widthAnchor),
            origins.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
        container.window?.makeFirstResponder(url)
    }

    func selectDKUExam(_ key: String) {
        guard flow.state == .setup, AppSession.isSafeToken(key, min: 1, max: 128) else { return }
        selection = .dkuOJ
        destinationPicker?.selectedSegment = 1
        startField?.stringValue = selection.url.appendingPathComponent("contest/" + key).absoluteString
        notice?.stringValue = "Open this assignment on DKU OJ after locking. " + selection.note
    }

    @objc private func destinationChanged() {
        guard flow.state == .setup else { return }
        selection = destinationPicker?.selectedSegment == 0 ? .dukeCanvas : .dkuOJ
        startField?.stringValue = selection.url.absoluteString
        notice?.stringValue = selection.note
    }

    @objc private func testDevicePressed() { testDevice() }

    func setReleasedPracticeVisible(_ visible: Bool) { overlay?.isHidden = visible }

    func deviceTestChanged(_ message: [String: Any]) {
        guard flow.state == .setup else { return }
        let active = message["state"] as? String == "started"
        startButton?.isEnabled = !active
        destinationPicker?.isEnabled = !active
        testDeviceButton?.isEnabled = !active
        notice?.stringValue = active
            ? "Device test running. It ends automatically; no other apps are closed."
            : "Device test finished. " + selection.note
    }

    private func checks() -> [String] {
        effects.checks()
    }

    @objc private func checkPressed() {
        guard flow.state == .setup else { return }
        let problems = checks()
        notice?.stringValue = problems.isEmpty ? "Mac checks passed. Save your work before locking." : problems.joined(separator: "\n")
    }

    @objc private func startPressed() {
        guard flow.state == .setup, !busy(),
              let parsed = ProtectedBrowserPolicy(start: startField?.stringValue ?? "",
                  additionalOrigins: customSetup ? (originsField?.stringValue ?? "") : selection.additionalOrigins, forbiddenOrigin: Config.serverURL,
                  allowHTTP: customSetup ? httpOption?.state == .on : selection.url.scheme == "http") else {
            notice?.stringValue = "Enter a valid address and exact login/resource origins. Bare addresses use HTTPS. HTTP requires the Allow HTTP checkbox. Wildcards, credentials and the FairPlay server are not allowed."
            return
        }
        if let blocker = Self.secureBlocker(customSetup: customSetup, isDevBuild: Config.isDevBuild,
                                            scheme: parsed.startURL.scheme, profileBundled: NativeBrowserProfile.bundled != nil) {
            notice?.stringValue = blocker
            return
        }
        let problems = checks()
        guard problems.isEmpty else { notice?.stringValue = problems.joined(separator: "\n"); return }
        guard !escrowInFlight else { return }
        escrowInFlight = true
        startButton?.isEnabled = false
        destinationPicker?.isEnabled = false
        notice?.stringValue = "Setting up the emergency exit…"
        effects.setup.prepare(now: effects.now(), progress: { [weak self] in self?.notice?.stringValue = NativeLockSetup.preparingMessage }) { [weak self] result in
            guard let self else { return }
            self.escrowInFlight = false
            guard self.isOpen, self.flow.state == .setup else { self.discardSavedLock(); return }
            switch result {
            case .failure(let failure):
                self.startButton?.isEnabled = true
                self.destinationPicker?.isEnabled = true
                self.notice?.stringValue = failure.message
            case .success:
                guard let token = self.flow.prepare() else { self.discardSavedLock(); return }
                self.compileAndActivate(parsed, token: token, acquire: { [weak self] in
                    guard let self, !self.busy(), !self.held else { return false }
                    guard self.checks().isEmpty else { return false }
                    return self.effects.acquire()
                })
            }
        }
    }

    private var escrowInFlight = false

    private func discardSavedLock() {
        effects.setup.clear()
    }

    private func compileAndActivate(_ parsed: ProtectedBrowserPolicy, token: UUID, acquire: @escaping () -> Bool) {
        notice?.stringValue = "Preparing website restrictions…"
        let identifier = "fairplay.protected-browser." + UUID().uuidString
        pendingRuleIdentifier = identifier
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: identifier,
            encodedContentRuleList: parsed.contentRules) { [weak self] rules, error in
            DispatchQueue.main.async {
                guard let self, self.isOpen, self.flow.state == .preparing,
                      self.pendingRuleIdentifier == identifier else {
                    WKContentRuleListStore.default().removeContentRuleList(forIdentifier: identifier) { _ in }
                    return
                }
                self.pendingRuleIdentifier = nil
                guard let rules, error == nil else {
                    self.flow.preparationFailed(token)
                    self.discardSavedLock()
                    self.startButton?.isEnabled = true
                    self.destinationPicker?.isEnabled = true
                    self.notice?.stringValue = "Website restrictions could not be prepared. The Mac has not been locked."
                    return
                }
                self.ruleIdentifier = identifier
                let opened = self.flow.activate(token, acquireLock: acquire, open: { self.open(parsed, rules: rules) })
                if opened { self.effects.setup.registerLoginItem() }
                if !opened {
                    self.removeRules()
                    if !self.recoveringLock { self.discardSavedLock() }
                    self.startButton?.isEnabled = true
                    self.destinationPicker?.isEnabled = true
                    self.notice?.stringValue = self.recoveringLock
                        ? "FairPlay could not reopen DKU OJ. This Mac stays locked. Ask your instructor for an exit code."
                        : "The Mac could not be locked. Recheck permissions and try again. No university page was opened."
                }
            }
        }
    }

    /// Relaunch with a saved lock (main.swift): the Mac is already locked, no setup page is shown,
    /// and the saved claim and binding come back, so only a receipt matching them can unlock.
    func resumeNativeLock(_ state: NativeLockState, in container: NSView) {
        guard !isOpen, !held else { return }
        self.container = container
        flow = ProtectedBrowserFlow()
        let root = NSView(frame: container.bounds)
        root.autoresizingMask = [.width, .height]
        root.wantsLayer = true
        root.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        container.addSubview(root)
        overlay = root
        VersionLabel.add(to: root)
        selection = .dkuOJ
        recoveringLock = true
        nativeExam = NativeExamSession(restoring: state.contest, binding: state.binding)
        recoverUncleanPending = state.unclean && state.contest != nil
        let notice = NSTextField(wrappingLabelWithString: "Locking again…")
        notice.font = .systemFont(ofSize: 18, weight: .semibold)
        notice.frame = NSRect(x: 40, y: root.bounds.height / 2, width: root.bounds.width - 80, height: 30)
        notice.autoresizingMask = [.width, .minYMargin, .maxYMargin]
        root.addSubview(notice)
        self.notice = notice
        guard let parsed = ProtectedBrowserPolicy(start: selection.url.absoluteString, additionalOrigins: selection.additionalOrigins,
                                                  forbiddenOrigin: Config.serverURL, allowHTTP: selection.url.scheme == "http"),
              let token = flow.prepare() else {
            notice.stringValue = "FairPlay could not reopen DKU OJ. This Mac stays locked. Ask your instructor for an exit code."
            return
        }
        compileAndActivate(parsed, token: token, acquire: { [weak self] in self?.effects.ownsLock() == true })
    }

    private var recoveringLock = false
    private var recoverUncleanPending = false
    var pendingUncleanRecover: Bool { recoverUncleanPending }

    private func open(_ policy: ProtectedBrowserPolicy, rules: WKContentRuleList) {
        guard let root = overlay, !held else {
            notice?.stringValue = "A security check interrupted startup. Use Leave with your instructor's exit code, then start again."
            return
        }
        self.policy = policy
        root.subviews.forEach { $0.removeFromSuperview() }
        startField = nil; originsField = nil; httpOption = nil; startButton = nil
        let config = effects.configuration(rules)
        if let retainedWebsiteStore { config.websiteDataStore = retainedWebsiteStore }
        retainedWebsiteStore = config.websiteDataStore
        let view = ProtectedUniversityWebView(frame: NSRect(x: 0, y: 0, width: root.bounds.width, height: max(0, root.bounds.height - 100)), configuration: config)
        view.autoresizingMask = [.width, .height]
        view.navigationDelegate = self
        view.uiDelegate = self
        view.allowsBackForwardNavigationGestures = false
        view.allowsLinkPreview = false
        root.addSubview(view)
        browser = view
        orderedTabs = [view]
        navigationState = ProtectedBrowserNavigation(startURL: policy.startURL)
        let strip = NSStackView()
        strip.orientation = .horizontal
        strip.spacing = 8
        strip.frame = NSRect(x: 16, y: root.bounds.height - 98, width: root.bounds.width - 32, height: 46)
        strip.autoresizingMask = [.width, .minYMargin]
        let status = NSTextField(labelWithString: "")
        status.lineBreakMode = .byTruncatingTail
        notice = status
        let back = NSButton(image: NSImage(systemSymbolName: "arrow.left", accessibilityDescription: "Back")!, target: self, action: #selector(backPressed))
        let reload = NSButton(image: NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Reload")!, target: self, action: #selector(reloadPressed))
        back.bezelStyle = .rounded; reload.bezelStyle = .rounded
        back.identifier = NSUserInterfaceItemIdentifier("browser.back")
        reload.identifier = NSUserInterfaceItemIdentifier("browser.reload")
        backButton = back; reloadButton = reload
        strip.addArrangedSubview(back)
        strip.addArrangedSubview(reload)
        status.alignment = .center
        status.setContentHuggingPriority(.defaultLow, for: .horizontal)
        status.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        strip.addArrangedSubview(status)
        let references = NSPopUpButton(frame: .zero, pullsDown: true)
        references.addItem(withTitle: "Allowed websites")
        references.isHidden = true
        references.target = self; references.action = #selector(openReference(_:))
        sitePicker = references
        strip.addArrangedSubview(references)
        let closePopup = NSButton(title: "Close tab", target: self, action: #selector(closePopupPressed))
        closePopupButton = closePopup
        strip.addArrangedSubview(closePopup)
        let retry = NSButton(title: "Retry now", target: self, action: #selector(retryNowPressed))
        retry.bezelStyle = .rounded
        retry.isHidden = true
        retryButton = retry
        strip.addArrangedSubview(retry)
        let vpn = NSButton(title: "Open VPN", target: self, action: #selector(openVPNPressed))
        vpn.bezelStyle = .rounded
        vpn.isHidden = true
        vpnButton = vpn
        strip.addArrangedSubview(vpn)
        let draftLabel = NSTextField(labelWithString: "")
        draftLabel.font = .systemFont(ofSize: 11)
        draftStatus = draftLabel
        strip.addArrangedSubview(draftLabel)
        let leave = NSButton(title: "Leave", target: self, action: #selector(leavePressed))
        leave.bezelStyle = .rounded
        leave.identifier = NSUserInterfaceItemIdentifier("browser.leave")
        leave.frame = NSRect(x: root.bounds.width - 96, y: root.bounds.height - 44, width: 80, height: 32)
        leave.autoresizingMask = [.minXMargin, .minYMargin]
        root.addSubview(leave)
        let restore = NSButton(title: "Recover draft", target: self, action: #selector(restoreDraftPressed))
        restore.isHidden = true
        restoreButton = restore
        strip.addArrangedSubview(restore)
        root.addSubview(strip)
        let tabs = NSStackView()
        tabs.orientation = .horizontal
        tabs.spacing = 6
        tabs.frame = NSRect(x: 16, y: root.bounds.height - 48, width: root.bounds.width - 32 - 96, height: 40)
        tabs.autoresizingMask = [.width, .minYMargin]
        root.addSubview(tabs)
        tabStrip = tabs
        rebuildTabs()
        root.window?.makeFirstResponder(view)
        updateNavigation()
        effects.load(view, policy.startURL)
        loadRetry = LoadRetryPolicy()
        let watch = NetworkWatch()
        watch.start { [weak self] satisfied in
            guard let self, self.isOpen else { return }
            self.diagnostic("network-path-changed up=\(satisfied)", url: nil)
            if NetworkWatch.shouldReload(afterChangeTo: satisfied, phase: self.navigationState?.phase) { self.retryFailedLoad(force: true) }
        }
        networkWatch = watch
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in self?.captureDraft(); self?.refreshEditShortcuts() }
        RunLoop.main.add(timer, forMode: .common)
        draftTimer = timer
        if Config.universityPolicyPublicKey != nil, policy.startURL.host == UniversityDestination.dkuOJ.url.host {
            if renewCancel == nil {
                let first: TimeInterval = nativeExam.requiresTerminalReceipt || recoverUncleanPending ? 0 : renewSchedule.interval(.renew)
                renewCancel = effects.schedule(first) { [weak self] in self?.renewCancel = nil; self?.refreshExamContext() }
            }
        }
    }

    static func websiteConfiguration(rules: WKContentRuleList) -> WKWebViewConfiguration {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        if #available(macOS 15.0, *) {
            if config.responds(to: NSSelectorFromString("setWritingToolsBehavior:")) {
                config.setValue(NSWritingToolsBehavior.none.rawValue, forKey: "writingToolsBehavior")
            }
        }
        config.userContentController = WKUserContentController()
        config.userContentController.add(rules)
        return config
    }

    private func updateNavigation() {
        switch navigationState?.phase {
        case .ready: notice?.stringValue = activeExamBinding == nil ? "Protected browsing" : "Exam in progress"
        case .loading: notice?.stringValue = "Loading…"
        case .failed: notice?.stringValue = failureDescription.map { "Could not load this page: " + $0 } ?? "Could not load this page. Try Reload."
        case .unavailable: notice?.stringValue = "Website stopped. Use Leave with your instructor's exit code."
        case nil: notice?.stringValue = ""
        }
        closePopupButton?.isHidden = orderedTabs.first === browser
        let failed = navigationState?.phase == .failed && lockIsOwned
        retryButton?.isHidden = !failed
        vpnButton?.isHidden = !(failed && navigationState?.failureDiagnostic != LoadOutcome.tlsNotice && LockdownController.shared.vpnWindowAvailable && VPNLauncher.installed() != nil
            && VPNPromptGate.allowed(vpnInterfaceUp: VPNPromptGate.vpnInterfaceUp(routeTarget: selection.url.host), hostAnsweredAt: hostAnsweredAt, now: effects.now()))
        rebuildTabs()
        backButton?.isEnabled = browser?.canGoBack == true && lockIsOwned
        EventTap.reloadShortcutAllowed = isOpen && lockIsOwned && !LockdownController.shared.vpnYieldOpen
        refreshEditShortcuts()
        reloadButton?.isEnabled = browser != nil && reloadTarget != nil && lockIsOwned
    }

    /// Undo, Redo, Select All and Find pass only while this app is active and the page has the keyboard focus.
    private func refreshEditShortcuts() {
        var responder: NSResponder? = overlay?.window?.firstResponder
        var inPage = false
        while let r = responder { if r is WKWebView { inPage = true; break }; responder = r.nextResponder }
        EventTap.editShortcutsAllowed = isOpen && lockIsOwned && NSApp.isActive && inPage
            && overlay?.window?.attachedSheet == nil
    }

    private func dropExpiredSites() {
        if let expiry = policyExpires, expiry < effects.now(), !approvedSites.isEmpty || !approvedResources.isEmpty {
            installReferenceSites([])
        }
    }

    /// Exam text must not be pasteable into an allowed site: leaving the exam (first) tab clears it once.
    static func clearsClipboard(from current: WKWebView?, to target: WKWebView, examTab: WKWebView?) -> Bool {
        guard let current, let examTab else { return false }
        return current === examTab && target !== examTab
    }

    /// A signed state already verified by the caller (signature, nonce, device, lock): a state that unlocks
    /// (released by the instructor, completed) ends the lock and returns to the first screen.
    func receiveVerifiedState(_ context: NativeDeviceContext) {
        guard lockIsOwned, nativeExam.accept(context), nativeExam.permitsUnlock else { return }
        close(release: true)
    }

    private func noteDeadline(_ context: NativeDeviceContext) {
        failsafe.accept(context, at: effects.monotonic())
        armFailsafe()
    }

    private func armFailsafe(minimum: TimeInterval = 1) {
        failsafeCancel?(); failsafeCancel = nil
        guard lockIsOwned, let wait = failsafe.secondsUntilDue(at: effects.monotonic()) else { return }
        failsafeCancel = effects.schedule(max(wait, 0) + minimum) { [weak self] in
            self?.failsafeCancel = nil
            self?.checkFailsafe()
        }
    }

    /// Once due, ask the OJ again until a VERIFIED answer comes back: only that may release (or, if it
    /// moved the deadline, postpones). Errors and unverifiable replies just retry; a hard bound ends it.
    private func checkFailsafe() {
        guard lockIsOwned else { return }
        if nativeRequestInFlight || contextInFlight { armFailsafe(minimum: 5); return }
        switch failsafe.decide(at: effects.monotonic()) {
        case .wait: armFailsafe()
        case .probe:
            refreshExamContext()
            armFailsafe(minimum: DeadlineFailsafe.probeRetry)
        case .release:
            diagnostic("deadline-failsafe-unlock", url: nil)
            EventLog.shared.record(.deadlineUnlock)
            close(release: true, deadlineOver: true)
        }
    }

    private var certRenewalInFlight = false
    private var certRenewalFailures = 0
    private var certRenewalNotBefore: Date?

    /// Every renew tick: a device certificate with under 6 hours left is issued again in the background
    /// (same key), with backoff on failure, so a long exam never loses the device. The lock is never touched.
    private func renewDeviceCertificateIfNeeded() {
        let now = effects.now()
        let remaining = nativeIdentity.certificateExpiry.map { $0.timeIntervalSince(now) }
        guard CertificateRenewal.shouldTryNow(remaining: remaining, inFlight: certRenewalInFlight, now: now, notBefore: certRenewalNotBefore) else { return }
        certRenewalInFlight = true
        let identity = nativeIdentity
        Task { [weak self] in
            do {
                try await identity.renewIfNeeded(threshold: CertificateRenewal.duringExam, now: now)
                await MainActor.run { self?.certRenewalInFlight = false; self?.certRenewalFailures = 0; self?.certRenewalNotBefore = nil
                                       self?.diagnostic("device-certificate-renewed", url: nil) }
            } catch {
                await MainActor.run {
                    guard let self else { return }
                    self.certRenewalInFlight = false
                    self.certRenewalFailures += 1
                    self.certRenewalNotBefore = self.effects.now().addingTimeInterval(CertificateRenewal.backoff(afterFailures: self.certRenewalFailures))
                    self.diagnostic("device-certificate-renewal-failed", url: nil)
                }
            }
        }
    }

    private func refreshExamContext() {
        renewDeviceCertificateIfNeeded()
        dropExpiredSites()
        if recoverUncleanPending, let contest = nativeExam.contest {
            if !requestNative("recover/" + contest) { scheduleNext(.other) }
            return
        }
        if nativeExam.contest == nil, let contest = refusedContest, nativeExam.beginClaim(contest) {
            persistNativeProgress()
            if !requestNative("claim/" + contest, joining: true) { scheduleNext(.refused(after: 30)) }
            return
        }
        if nativeExam.requiresTerminalReceipt {
            var started = false
            if let session = nativeExam.binding?.session { started = requestNative("session/" + session + "/renew") }
            else if let contest = nativeExam.contest { started = requestNative("claim/" + contest, joining: true) }
            if !started { scheduleNext(.other) }
            return
        }
        if contextInFlight { return }
        guard lockIsOwned, let key = Config.universityPolicyPublicKey,
              let page = orderedTabs.first, page.url?.host == UniversityDestination.dkuOJ.url.host, !page.isLoading else {
            scheduleNext(.other)
            return
        }
        contextInFlight = true
        let nonce = UUID().uuidString
        let script = "const r = await fetch('/api/fairplay/browser-context?nonce=' + nonce, {credentials:'same-origin',cache:'no-store'}); if (!r.ok) return null; return await r.json();"
        page.callAsyncJavaScript(script, arguments: ["nonce": nonce], in: nil, in: .defaultClient) { [weak self, weak page] result in
            guard let self else { return }
            self.contextInFlight = false
            guard self.lockIsOwned, page === self.orderedTabs.first,
                  case .success(let raw) = result, let envelope = raw as? [String: Any],
                  let context = BrowserExamContext.verify(envelope, publicKey: key, nonce: nonce) else {
                if self.lockIsOwned, self.isOpen { self.scheduleNext(.other) }
                return
            }
            self.applyExamContext(context, url: page?.url)
            if self.lockIsOwned, self.isOpen { self.scheduleNext(.success) }
        }
    }

    func applyExamContext(_ context: BrowserExamContext, url: URL?) {
        if context.state == "completed", context.binding == activeExamBinding {
            // The page-readable v1 context is not a receipt. Only the signed native state unlocks.
            diagnostic("exam-completion-page-context-ignored", url: url)
            return
        }
        if context.state == "active" {
            // A different account/exam invalidates the preceding reference policy.
            activeExamBinding = context.binding
            policyExpires = NativeRenewSchedule.policyExpiry(signedExpires: context.expires, receivedAt: effects.now())
            policy?.sitesExpire = policyExpires
            if context.sites != approvedSites || (context.resources ?? []) != approvedResources {
                installReferenceSites(context.sites, resources: context.resources ?? [])
            }
        } else {
            activeExamBinding = nil
            if !approvedSites.isEmpty { installReferenceSites([]) }
        }
        updateNavigation()
    }

    private func installReferenceSites(_ sites: [String], resources: [String] = [], assets: [String]? = nil) {
        let cdns = assets ?? approvedAssets
        guard let current = policy,
              var updated = ProtectedBrowserPolicy(start: current.startURL.absoluteString,
                  additionalOrigins: selection.additionalOrigins, forbiddenOrigin: Config.serverURL,
                  allowHTTP: current.startURL.scheme == "http", referenceSites: sites, resourceOrigins: resources, assetOrigins: cdns),
              let page = orderedTabs.first else { return }
        updated.sitesExpire = sites.isEmpty ? nil : policyExpires
        updated.clock = { [weak self] in self?.effects.now() ?? Date() }
        let revision = UUID(); policyRevision = revision
        let identifier = "fairplay.protected-browser." + revision.uuidString
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: identifier, encodedContentRuleList: updated.contentRules) { [weak self, weak page] rules, error in
            DispatchQueue.main.async {
                guard let self, let page, self.lockIsOwned, self.policyRevision == revision,
                      page === self.orderedTabs.first, let rules, error == nil else {
                    WKContentRuleListStore.default().removeContentRuleList(forIdentifier: identifier) { _ in }
                    return
                }
                let content = page.configuration.userContentController
                content.removeAllContentRuleLists(); content.add(rules)
                if let previous = self.ruleIdentifier { WKContentRuleListStore.default().removeContentRuleList(forIdentifier: previous) { _ in } }
                self.ruleIdentifier = identifier
                self.policy = updated
                self.approvedSites = sites
                self.approvedResources = resources
                self.approvedAssets = cdns
                self.pickerSites = updated.acceptedSites
                self.sitePicker?.removeAllItems()
                self.sitePicker?.addItem(withTitle: "Allowed websites")
                for raw in updated.acceptedSites { self.sitePicker?.addItem(withTitle: ProtectedBrowserPolicy.normalizedURL(raw)?.host ?? raw) }
                self.sitePicker?.isHidden = updated.acceptedSites.isEmpty
                // Close revoked resource tabs, preserving the university page and its drafts.
                let revoked = self.orderedTabs.dropFirst().filter { updated.allows($0.url, mainFrame: true) == false }
                for tab in revoked {
                    if self.browser === tab {
                        if let rootIndex = self.orderedTabs.firstIndex(where: { $0 === page }) {
                            let button = NSButton(); button.tag = rootIndex; self.selectTab(button)
                        }
                    }
                    self.parents.removeAll { $0 === tab }; self.orderedTabs.removeAll { $0 === tab }
                    tab.stopLoading(); tab.navigationDelegate = nil; tab.uiDelegate = nil; tab.removeFromSuperview()
                }
                self.rebuildTabs()
            }
        }
    }

    @objc private func openReference(_ sender: NSPopUpButton) {
        let index = sender.indexOfSelectedItem - 1
        guard pickerSites.indices.contains(index), let url = ProtectedBrowserPolicy.normalizedURL(pickerSites[index]) else { return }
        openReferenceTab(url)
    }

    private func openReferenceTab(_ url: URL) {
        guard lockIsOwned, orderedTabs.count < 8, let root = orderedTabs.first, let current = browser,
              policy?.allows(url, mainFrame: true) == true else { return }
        let child = ProtectedUniversityWebView(frame: current.frame, configuration: root.configuration)
        child.autoresizingMask = [.width, .height]; child.navigationDelegate = self; child.uiDelegate = self
        child.allowsLinkPreview = false
        if Self.clearsClipboard(from: current, to: child, examTab: root) { NSPasteboard.general.clearContents() }
        current.isHidden = true; parents.append(current); orderedTabs.append(child); browser = child
        overlay?.addSubview(child, positioned: .below, relativeTo: current)
        navigationState = ProtectedBrowserNavigation(startURL: url)
        effects.load(child, url); updateNavigation()
    }

    private func captureDraft() {
        guard lockIsOwned, let page = browser, !page.isLoading, !draftReadInFlight,
              page.url?.host == UniversityDestination.dkuOJ.url.host else { return }
        draftReadInFlight = true
        page.callAsyncJavaScript(BrowserDraftAdapter.collect, arguments: ["allowedOrigin": page.url.map { URLComponents(url: $0, resolvingAgainstBaseURL: false).map { components in "\(components.scheme ?? "")://\(components.host ?? "")" + (components.port.map { ":\($0)" } ?? "") } ?? "" } ?? ""], in: nil, in: .defaultClient) { [weak self, weak page] result in
            guard let self else { return }
            self.draftReadInFlight = false
            guard let page, page === self.browser, self.lockIsOwned else { return }
            guard case .success(let raw) = result, let value = raw as? [String: Any],
                  let origin = value["origin"] as? String,
                  ProtectedBrowserPolicy.origin(page.url ?? URL(string: "about:blank")!) == origin,
                  let user = value["user"] as? String, let context = value["context"] as? String,
                  let problem = value["problem"] as? String, let fields = value["fields"] as? [String: String] else {
                self.draftScope = nil; self.draftFields = nil; self.pendingDraft = nil
                self.restoreButton?.isHidden = true
                self.draftStatus?.stringValue = ""
                return
            }
            let scope = BrowserDraftScope(origin: origin, user: user, context: context, problem: problem)
            do {
                if self.draftScope != scope {
                    self.draftScope = scope
                    self.draftFields = nil
                    self.pendingDraft = try self.drafts.load(scope: scope)
                    if self.pendingDraft?.fields == fields { self.pendingDraft = nil }
                }
                if self.pendingDraft != nil {
                    self.restoreButton?.isHidden = false
                    self.draftStatus?.stringValue = "Recovery copy available"
                    return // Never replace a recovery copy with the initial empty editor.
                }
                guard fields != self.draftFields else { return }
                try self.drafts.save(scope: scope, fields: fields)
                self.draftFields = fields
                self.draftStatus?.stringValue = "Draft saved on this Mac"
            } catch {
                self.draftStatus?.stringValue = "Local draft not saved — keep this page open"
            }
        }
    }

    @objc private func restoreDraftPressed() {
        guard lockIsOwned, let record = pendingDraft, let page = browser, let window = overlay?.window else { return }
        let alert = NSAlert()
        alert.messageText = "Recover your saved draft?"
        alert.informativeText = "This replaces the answer currently in this editor. It does not submit an answer."
        alert.addButton(withTitle: "Recover draft")
        alert.addButton(withTitle: "Keep current answer")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { [weak self, weak page] response in
            guard let self, let page, page === self.browser, self.lockIsOwned,
                  self.draftScope == record.scope else { return }
            if response == .alertFirstButtonReturn {
                let args: [String: Any] = ["origin": record.scope.origin, "user": record.scope.user,
                    "context": record.scope.context, "problem": record.scope.problem, "fields": record.fields]
                page.callAsyncJavaScript(BrowserDraftAdapter.restore, arguments: args, in: nil, in: .page) { [weak self] result in
                    guard let self, self.draftScope == record.scope else { return }
                    if case .success(let restored) = result, restored as? Bool == true {
                        self.pendingDraft = nil; self.draftFields = nil
                        self.restoreButton?.isHidden = true
                        self.draftStatus?.stringValue = "Draft recovered — not submitted"
                    }
                }
            } else if response == .alertSecondButtonReturn {
                self.pendingDraft = nil; self.draftFields = nil
                self.restoreButton?.isHidden = true
                self.captureDraft()
            }
        }
    }

    private func rebuildTabs() {
        guard let strip = tabStrip else { return }
        strip.arrangedSubviews.forEach { strip.removeArrangedSubview($0); $0.removeFromSuperview() }
        for (index, page) in orderedTabs.enumerated() {
            let title = page.title.flatMap { $0.isEmpty ? nil : String($0.prefix(28)) }
                ?? page.url?.host ?? (index == 0 ? "University" : "New tab")
            let button = NSButton(title: title, target: self, action: #selector(selectTab(_:)))
            button.bezelStyle = .rounded
            button.isBordered = false
            button.wantsLayer = true
            button.layer?.cornerRadius = 10
            button.layer?.backgroundColor = (page === browser ? NSColor.controlBackgroundColor : NSColor.clear).cgColor
            button.font = .systemFont(ofSize: 14, weight: page === browser ? .medium : .regular)
            button.heightAnchor.constraint(equalToConstant: 36).isActive = true
            button.widthAnchor.constraint(equalToConstant: 230).isActive = true
            if index == 0, let path = Bundle.main.path(forResource: "DKUOJ", ofType: "ico"), let image = NSImage(contentsOfFile: path) {
                image.size = NSSize(width: 18, height: 18)
                button.image = image; button.imagePosition = .imageLeft
            }
            button.tag = index
            button.state = page === browser ? .on : .off
            button.contentTintColor = page === browser ? BrandMark.blue : .labelColor
            button.setAccessibilityLabel("Tab: " + title)
            strip.addArrangedSubview(button)
        }
    }

    @objc private func selectTab(_ sender: NSButton) {
        guard lockIsOwned, orderedTabs.indices.contains(sender.tag), let current = browser else { return }
        let target = orderedTabs[sender.tag]
        guard target !== current else { return }
        if Self.clearsClipboard(from: current, to: target, examTab: orderedTabs.first) { NSPasteboard.general.clearContents() }
        dialogs.cancel()
        current.isHidden = true
        parents.removeAll { $0 === target }
        parents.append(current)
        browser = target
        target.isHidden = false
        activeNavigation = nil
        policyDenial = nil
        navigationState = ProtectedBrowserNavigation(startURL: target.url ?? policy!.startURL)
        if !target.isLoading { navigationState?.finished() }
        updateNavigation()
        overlay?.window?.makeFirstResponder(target)
    }

    private var lockIsOwned: Bool {
        !held && flow.ownsLock && effects.ownsLock()
    }

    @objc private func backPressed() {
        guard lockIsOwned, let previous = browser?.backForwardList.backItem?.url,
              policy?.allows(previous, mainFrame: true) == true else { return }
        browser?.goBack()
    }
    private var reloadTarget: URL? {
        guard let state = navigationState, state.canReload else { return nil }
        let target = state.phase == .failed ? state.requestedURL : browser?.url
        guard policy?.allows(target, mainFrame: true) == true else { return nil }
        return target
    }

    @objc private func retryNowPressed() { retryFailedLoad() }

    @objc private func openVPNPressed() { launchVPN() }

    private func launchVPN() {
        guard lockIsOwned, let client = VPNLauncher.installed() else { return }
        guard LockdownController.shared.yieldFrontToVPN(for: VPNLauncher.frontGrace) else { return }
        diagnostic("vpn-open " + client.name, url: nil)
        if !VPNLauncher.open(client) { LockdownController.shared.endVPNYield() }
    }

    /// Once per lock, after the site stayed unreachable: the lock stays on, only the VPN app may come forward.
    private func offerVPN() {
        guard let client = VPNLauncher.installed(), let window = overlay?.window, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "DKU OJ cannot be reached"
        alert.informativeText = "If you are off campus, connect to the university VPN with \(client.name), then come back. FairPlay keeps your Mac locked and reloads the page when the connection is back."
        alert.addButton(withTitle: "Open \(client.name)")
        alert.addButton(withTitle: "Not now")
        alert.beginSheetModal(for: window) { [weak self] response in
            if response == .alertFirstButtonReturn { self?.launchVPN() }
        }
    }

    private func scheduleLoadRetry(offerVPNPrompt: Bool = true) {
        let delay = loadRetry.failed()
        loadRetryCancel?()
        loadRetryCancel = effects.schedule(delay) { [weak self] in
            self?.loadRetryCancel = nil
            self?.retryFailedLoad()
        }
        let tunnel = VPNPromptGate.vpnInterface(routeTarget: selection.url.host)
        if let tunnel { diagnostic("vpn-prompt-suppressed tunnel=" + tunnel, url: nil) }
        let gate = VPNPromptGate.allowed(vpnInterfaceUp: tunnel != nil, hostAnsweredAt: hostAnsweredAt, now: effects.now())
        if offerVPNPrompt, gate, loadRetry.shouldPrompt(vpnInstalled: VPNLauncher.installed() != nil) { offerVPN() }
    }

    /// The OJ host sent an HTTP answer (any status): the failure state, retry timer and prompts are over.
    private func ojAnswered() {
        hostAnsweredAt = effects.now()
        loadRetry.succeeded()
        loadRetryCancel?(); loadRetryCancel = nil
    }

    private func retryFailedLoad(force: Bool = false) {
        guard isOpen, navigationState?.phase == .failed || (force && navigationState?.phase == .loading) else { return }
        reloadPressed()
    }

    @objc private func reloadPressed() { reload(hard: false) }

    /// Cmd-R / Cmd-Shift-R, forwarded by the app menu while the protected browser holds the lock.
    @discardableResult
    func reloadFromKeyboard(hard: Bool) -> Bool {
        guard isOpen else { return false }
        reload(hard: hard)
        return true
    }

    private func reload(hard: Bool) {
        guard lockIsOwned, let view = browser, let target = reloadTarget else { return }
        loadRetryCancel?(); loadRetryCancel = nil
        let retryFailedRequest = navigationState?.phase == .failed
        navigationState?.started()
        updateNavigation()
        // reload() has no committed page after a first-load failure, and may otherwise
        // reload the old document instead of the failed destination. Retry the permitted request.
        if retryFailedRequest { effects.load(view, target) }
        else { effects.reload(view, hard) }
    }

    /// Before the lock there is nothing to leave. Once the Mac is locked, Leave always needs
    /// the instructor's exit code (or an OJ-signed receipt, which arrives on its own).
    @objc private func leavePressed() {
        guard flow.ownsLock else { close(release: false); return }
        let setup = effects.setup
        setup.background { [weak self] in
            let snapshot = setup.leaveSnapshot()
            let code = snapshot.text, development = snapshot.development, version = snapshot.version
            setup.onMain {
                guard let self, self.flow.ownsLock else { return }
                self.effects.presentLeave(NativeLeavePrompt(lockCode: code, developmentCode: development,
                                                            lockVersion: { setup.registrar.lockIdChanges }, shownVersion: version,
                                                            isRefused: { NativeExit.isRefused(setup.store.load()) }, reload: setup.reloadLeaveCode,
                                                            submit: { [weak self] in self?.submitLeaveCode($0) ?? .malformed },
                                                            submitAsync: { [weak self] typed, done in
                                                                guard let self else { done(.malformed); return }
                                                                self.submitLeaveCodeInBackground(typed, completion: done)
                                                            }),
                                          self.overlay?.window)
            }
        }
    }

    /// Only an accepted code unlocks. Wrong, locked-out and malformed codes change nothing here.
    @discardableResult
    func submitLeaveCode(_ typed: String) -> NativeExitAttempt {
        guard flow.ownsLock else { return .malformed }
        let (result, ticket) = NativeExit.attemptTicketed(typed: typed, store: effects.setup.store, now: effects.now(), monotonic: effects.monotonic)
        if result == .accepted { return completeLeaveIfCurrent(ticket) ? .accepted : .stale }
        return result
    }

    private var leaveInFlight = false

    /// Same rules as `submitLeaveCode`, but the slow comparison runs off the main thread.
    /// Only one check at a time; `completion` runs on the main thread.
    func submitLeaveCodeInBackground(_ typed: String, completion: @escaping (NativeExitAttempt) -> Void) {
        guard flow.ownsLock, !leaveInFlight else { completion(.malformed); return }
        leaveInFlight = true
        NativeExit.attemptInBackground(typed: typed, store: effects.setup.store, now: effects.now(), monotonic: effects.monotonic()) { [weak self] result, ticket in
            guard let self else { completion(result); return }
            self.leaveInFlight = false
            if result == .accepted {
                guard self.flow.ownsLock, self.completeLeaveIfCurrent(ticket) else { completion(.stale); return }
            }
            completion(result)
        }
    }

    /// Web view first, then the lock, then both saved copies; the OJ is told last and its answer
    /// (409, offline, anything) never undoes the unlock.
    private func completeLeaveIfCurrent(_ ticket: NativeExit.Ticket?) -> Bool {
        NativeExit.releaseIfCurrent(ticket, store: effects.setup.store) { completeLeave() }
    }

    private func completeLeave() {
        let session = nativeExam.binding?.session
        let cookies = retainedWebsiteStore
        discardBrowser()
        effects.release()
        effects.setup.clear()
        if let session { sendDetachedAbort(session: session, store: cookies) }
        close(release: false)
    }

    private func sendDetachedAbort(session: String, store: WKWebsiteDataStore?) {
        guard let store, nativeIdentity.fingerprint != nil, selection.url.scheme == "https",
              AppSession.isSafeToken(session, min: 1, max: 64) else { return }
        let endpoint = selection.url.appendingPathComponent("api/fairplay/native/session/" + session + "/abort")
        let http = nativeHTTP
        store.httpCookieStore.getAllCookies { [selection] cookies in
            let request = Self.nativeRequest(endpoint: endpoint, origin: selection.url, nonce: UUID().uuidString,
                                             unclean: false, cookies: cookies)
            http.dataTask(with: request) { _, _, _ in }.resume()
        }
    }

    private static func matchingCookies(_ cookies: [HTTPCookie], origin: URL, endpoint: URL) -> [HTTPCookie] {
        cookies.filter { $0.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == origin.host && endpoint.path.hasPrefix($0.path) }
    }

    static let lockConflictText = "This Mac code belongs to another exam or student. Your Mac stays locked. Ask your instructor for help."

    static func isLockConflict(status: Int?, data: Data?) -> Bool {
        guard status == 409, let data, data.count <= 4096,
              let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let message = body["error"] as? String else { return false }
        return message.contains("Mac code belongs to another")
    }

    static let claimConflictText = "DKU OJ could not confirm this exam request. Your Mac remains locked. Retry, or ask your instructor to move you to this computer."
    private var claimWasConflict = false

    struct OJRefusal: Equatable {
        let text: String
        let retryAfter: TimeInterval
    }

    /// The OJ's unsigned 403 on claim/recover. Shown to the student only; it never changes lock state.
    static func ojRefusal(status: Int?, data: Data?) -> OJRefusal? {
        guard status == 403, let data, data.count <= 4096,
              let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let reason = body["reason"] as? String,
              let raw = body["error"] as? String else { return nil }
        let retry: TimeInterval
        switch reason {
        case "instructor", "not_fairplay", "other_device", "handed_in", "on_hold": retry = 30
        case "not_open": retry = 15
        default: return nil
        }
        let text = String(String.UnicodeScalarView(raw.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }
            .prefix(200))).trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }
        return OJRefusal(text: text, retryAfter: retry)
    }

    /// The OJ's own `error` text from an unsigned 4xx/5xx JSON body, cleaned for display; nil when it gave none.
    static func ojErrorText(status: Int?, data: Data?) -> String? {
        guard let status, status >= 400, let data, data.count <= 4096,
              let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let raw = body["error"] as? String else { return nil }
        let text = String(String.UnicodeScalarView(raw.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }
            .prefix(200))).trimmingCharacters(in: .whitespaces)
        return text.isEmpty ? nil : text
    }

    static let startNotAcceptedText = "DKU OJ did not accept this start request. Check that you are signed in with your student account and that the exam is open for you, then try again, or ask your instructor."

    /// The notice for a refused or failed native call. The OJ's own text always wins; a bare HTTP refusal of
    /// claim/recover gets one neutral sentence; renew with no text leaves the notice alone; a failure to
    /// reach the OJ keeps the old wording.
    static func nativeFailureNotice(sendsLock: Bool, httpAnswered: Bool, status: Int?, data: Data?, recoverAfterConflict: Bool) -> String? {
        if let own = ojErrorText(status: status, data: data) { return own }
        if httpAnswered {
            guard sendsLock else { return nil }
            guard let status else { return startNotAcceptedText }
            return "DKU OJ answered HTTP \(status)" + (OJPage.summary(data).map { ": " + $0 } ?? "") + "."
        }
        return recoverAfterConflict ? claimConflictText
            : "DKU OJ could not confirm this exam request. Your Mac remains locked. Retry or ask your instructor for help."
    }

    static func nativeRequest(endpoint: URL, origin: URL, nonce: String, unclean: Bool, lockId: String? = nil, cookies: [HTTPCookie]) -> URLRequest {
        let matching = matchingCookies(cookies, origin: origin, endpoint: endpoint)
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.httpBody = ("nonce=\(nonce)&confirm=1" + (unclean ? "&unclean=1" : "")
            + (lockId.flatMap(NativeExit.parseLockId).map { "&lock=\($0)" } ?? "")).data(using: .utf8)
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue(origin.absoluteString, forHTTPHeaderField: "Referer")
        request.setValue(matching.first(where: { $0.name == "csrftoken" })?.value, forHTTPHeaderField: "X-CSRFToken")
        for (name, value) in HTTPCookie.requestHeaderFields(with: matching) { request.setValue(value, forHTTPHeaderField: name) }
        return request
    }

    /// False when nothing was sent and nothing is pending, so the caller must schedule the next try.
    @discardableResult
    private func requestNative(_ path: String, joining: Bool = false) -> Bool {
        guard lockIsOwned else { return false }
        if nativeRequestInFlight { return true }
        guard let cookieStore = (orderedTabs.first?.configuration.websiteDataStore ?? retainedWebsiteStore)?.httpCookieStore,
              let device = nativeIdentity.fingerprint, selection.url.scheme == "https",
              let key = Config.universityPolicyPublicKey else { return false }
        let page = orderedTabs.first
        nativeRequestInFlight = true
        let generation = nativeGeneration, nonce = UUID().uuidString
        let endpoint = selection.url.appendingPathComponent("api/fairplay/native/" + path)
        let reportsUnclean = path.hasPrefix("recover/") && recoverUncleanPending
        let setup = effects.setup
        let sendsLock = path.hasPrefix("claim/") || path.hasPrefix("recover/")
        setup.background {
        let lockId = sendsLock ? setup.store.load()?.lockId : nil
        setup.onMain {
        cookieStore.getAllCookies { [weak self, weak page] cookies in
            guard let self, self.lockIsOwned, generation == self.nativeGeneration else { return }
            let matching = Self.matchingCookies(cookies, origin: self.selection.url, endpoint: endpoint)
            let request = Self.nativeRequest(endpoint: endpoint, origin: self.selection.url, nonce: nonce,
                                             unclean: reportsUnclean, lockId: lockId, cookies: cookies)
            self.nativeHTTP.dataTask(with: request) { [weak self, weak page] data, response, error in
                DispatchQueue.main.async {
                    guard let self, generation == self.nativeGeneration else { return }
                    self.nativeRequestInFlight = false
                    guard self.lockIsOwned else { return }
                    let status = (response as? HTTPURLResponse)?.statusCode
                    let refusal: NativeRenewSchedule.Outcome = NativeRenewSchedule.isOverloaded(status: status, transportFailed: error != nil)
                        ? .overloaded(retryAfter: NativeRenewSchedule.retryAfter((response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Retry-After")))
                        : .other
                    if error != nil || status != 200 {
                        let summary = "native-request-failed http=\(status ?? 0) transport=\((error as NSError?)?.code ?? 0)"
                        if let line = self.failureLog.failure(summary, now: self.effects.now()) { self.diagnostic(line, url: endpoint) }
                    } else if let line = self.failureLog.success() {
                        self.diagnostic(line, url: endpoint)
                    }
                    let hadBinding = self.nativeExam.binding != nil
                    if path.hasPrefix("claim/") { self.claimWasConflict = status == 409 }
                    let envelope = data.flatMap { $0.count <= 24000 ? (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] : nil }
                    guard error == nil, status == 200, let envelope,
                          let context = NativeDeviceContext.verify(envelope, publicKey: key, nonce: nonce, device: device),
                          self.nativeExam.accept(context) else {
                        if sendsLock, Self.isLockConflict(status: status, data: data) {
                            self.notice?.stringValue = Self.lockConflictText
                            self.scheduleNext(.refused(after: 30))
                            return
                        }
                        if sendsLock, error == nil, let shown = Self.ojRefusal(status: status, data: data) {
                            self.notice?.stringValue = shown.text
                            self.claimRefused()
                            self.scheduleNext(.refused(after: shown.retryAfter))
                            return
                        }
                        if (path.hasPrefix("claim/") || path.hasSuffix("/renew")), error == nil, let status, [400,401,403,404,409].contains(status),
                           let contest = self.nativeExam.contest {
                            if !self.requestNative("recover/" + contest, joining: joining) { self.scheduleNext(refusal) }
                            return
                        }
                        if let envelope, NativeDeviceContext.clockIsOff(envelope, publicKey: key, nonce: nonce, device: device) {
                            self.notice?.stringValue = NativeDeviceContext.clockOffMessage
                            self.scheduleNext(refusal)
                            return
                        }
                        if let envelope, self.nativeExam.binding != nil,
                           let refused = NativeDeviceContext.verify(envelope, publicKey: key, nonce: nonce, device: device),
                           refused.state == "rejected" {
                            self.notice?.stringValue = Self.rejectedText
                            self.scheduleNext(refusal)
                            return
                        }
                        // A denial cannot disprove an earlier committed claim with a lost response.
                        let httpAnswer = error == nil && (status ?? 0) >= 400
                        if let shown = Self.nativeFailureNotice(sendsLock: sendsLock, httpAnswered: httpAnswer, status: status, data: data,
                                                                recoverAfterConflict: path.hasPrefix("recover/") && self.claimWasConflict) {
                            self.notice?.stringValue = shown
                        }
                        if sendsLock && httpAnswer { self.claimRefused() }
                        self.scheduleNext(sendsLock && httpAnswer ? .refused(after: 30) : refusal)
                        return
                    }
                    if reportsUnclean {
                        self.recoverUncleanPending = false
                        self.effects.setup.markRecovered()
                    }
                    self.persistNativeProgress()
                    if self.nativeExam.permitsUnlock {
                        if joining && !hadBinding {
                            // A closed previous attempt is not a finish command for this browser.
                            self.nativeExam = NativeExamSession()
                            self.notice?.stringValue = "This exam is closed. Ask your instructor to reopen it, or choose another assignment."
                            self.scheduleNext(.success)
                            return
                        }
                        self.close(release: true); return
                    }
                    let entered = path.hasPrefix("claim/") || path.hasPrefix("recover/")
                    self.refusedContest = nil
                    self.noteDeadline(context)
                    if self.nativeExam.onHold { self.applyHold(true); self.scheduleNext(.success); return }
                    self.applyHold(false)
                    self.activeExamBinding = context.session
                    self.policyExpires = NativeRenewSchedule.policyExpiry(signedExpires: TimeInterval(context.expires), receivedAt: self.effects.now())
                    self.policy?.sitesExpire = self.policyExpires
                    let sites = context.state == "active" ? (context.sites ?? []) : []
                    let assets = context.state == "active" ? (context.resources ?? []) : []
                    let cdns = context.assets ?? []
                    if sites != self.approvedSites || assets != self.approvedResources || cdns != self.approvedAssets {
                        self.installReferenceSites(sites, resources: assets, assets: cdns)
                    }
                    if joining, context.state == "active", let page {
                        let script = "const f=document.createElement('form'); f.method='POST'; f.action='/contest/'+exam+'/join'; const c=document.createElement('input'); c.type='hidden'; c.name='csrfmiddlewaretoken'; c.value=csrf; f.appendChild(c); document.body.appendChild(f); f.submit();"
                        page.callAsyncJavaScript(script, arguments: ["exam": context.contest, "csrf": matching.first(where: { $0.name == "csrftoken" })?.value ?? ""], in: nil, in: .defaultClient) { _ in }
                    }
                    self.notice?.stringValue = context.state == "finishing" ? "DKU OJ is still receiving your answers." : "Exam in progress"
                    self.scheduleNext(.success, immediate: entered)
                }
            }.resume()
        }
        }
        }
        return true
    }

    /// One pending request at a time: every answer decides when the next one is sent.
    private func scheduleNext(_ outcome: NativeRenewSchedule.Outcome, immediate: Bool = false) {
        guard lockIsOwned, isOpen else { return }
        let pace: NativeRenewSchedule.Pace = holdLabel != nil ? .hold : .renew
        let computed = renewSchedule.next(after: outcome, pace: pace)
        let delay = immediate && pace == .renew ? 0 : computed
        if pace == .hold {
            holdCancel?()
            holdCancel = effects.schedule(delay) { [weak self] in self?.holdCancel = nil; self?.refreshExamContext() }
        } else {
            renewCancel?()
            renewCancel = effects.schedule(delay) { [weak self] in self?.renewCancel = nil; self?.refreshExamContext() }
        }
    }

    /// The OJ hand-in answer page only asks the OJ sooner; unlocking still needs its signed completed state.
    func handInPageFinished(url: URL?, title: String?) {
        guard lockIsOwned, handInCancels.isEmpty, nativeExam.requiresTerminalReceipt,
              NativeRenewSchedule.looksLikeHandIn(url: url, title: title, contest: nativeExam.contest) else { return }
        diagnostic("hand-in-page", url: url)
        for delay in NativeRenewSchedule.completionFollowUps {
            handInCancels.append(effects.schedule(delay) { [weak self] in
                guard let self else { return }
                if delay == NativeRenewSchedule.completionFollowUps.last { self.handInCancels.removeAll() }
                self.refreshExamContext()
            })
        }
        refreshExamContext()
    }

    private func cancelHandIn() {
        handInCancels.forEach { $0() }
        handInCancels.removeAll()
    }

    /// A refused start (instructor, not FairPlay, not on the roster ...) with no claim ever accepted: forget the exam
    /// key so another exam can be started, and keep asking slowly for this one until the student picks a different one.
    private func claimRefused() {
        guard nativeExam.binding == nil, let contest = nativeExam.contest else { return }
        refusedContest = contest
        nativeExam.rejectedClaim()
        persistNativeProgress()
    }

    private func persistNativeProgress() {
        effects.setup.persist(contest: nativeExam.contest, binding: nativeExam.binding)
    }

    func lockEnded() { if isOpen { close(release: false) } }

    func requestLeave() { leavePressed() }

    /// Existing lock-integrity/permission blockers must remain above this browser too.
    func setHeld(_ held: Bool) {
        self.held = held
        overlay?.isHidden = held
        if held, browser != nil {
            // Hiding is insufficient: a hidden page can keep fetching and submitting.
            // Destroy this page, keep the native lock, and require an explicit fresh session.
            discardBrowser()
            notice?.stringValue = "Browsing paused for a security check. Your exam session is retained."
        } else if !held, browser == nil, holdLabel == nil {
            restoreBrowser()
        }
    }

    private func restoreBrowser() {
        guard !held, browser == nil, lockIsOwned, let policy, let identifier = ruleIdentifier else { return }
        WKContentRuleListStore.default().lookUpContentRuleList(forIdentifier: identifier) { [weak self] rules, _ in
            guard let self, !self.held, self.lockIsOwned, self.browser == nil, self.holdLabel == nil, let rules else { return }
            self.open(policy, rules: rules)
            if let contest = self.nativeExam.contest { self.requestNative("recover/" + contest, joining: true) }
        }
    }

    static let rejectedText = "DKU OJ rejected this session. Your Mac remains locked. Ask your instructor to release it."
    static let holdText = "Session on hold. Your instructor can unlock and reopen your exam."
    static let beginText = "Continue locks this Mac until you finish the exam or your instructor gives you an exit code. Start only when your exam is about to begin."
    private var holdLabel: NSTextField?
    var isOnHold: Bool { holdLabel != nil }

    /// A signed "hold": the page is destroyed, the lock stays, and the OJ is asked again until it
    /// releases the session (a signed released/completed unlocks as always) or clears the hold.
    func applyHold(_ on: Bool) {
        if on {
            guard holdLabel == nil, let root = overlay else { return }
            discardBrowser()
            let label = NSTextField(wrappingLabelWithString: Self.holdText)
            label.font = .systemFont(ofSize: 28, weight: .semibold)
            label.alignment = .center
            label.identifier = NSUserInterfaceItemIdentifier("browser.hold")
            label.frame = NSRect(x: 40, y: root.bounds.height / 2 - 40, width: root.bounds.width - 80, height: 80)
            label.autoresizingMask = [.width, .minYMargin, .maxYMargin]
            root.addSubview(label)
            holdLabel = label
            notice?.stringValue = Self.holdText
            holdCancel?()
            holdCancel = effects.schedule(renewSchedule.interval(.hold)) { [weak self] in self?.holdCancel = nil; self?.refreshExamContext() }
        } else if let label = holdLabel {
            label.removeFromSuperview()
            holdLabel = nil
            holdCancel?(); holdCancel = nil
            restoreBrowser()
        }
    }

    private func discardBrowser() {
        renewCancel?(); renewCancel = nil
        contextInFlight = false; approvedSites = []; approvedResources = []; approvedAssets = []; pickerSites = []; sitePicker = nil
        policyRevision = UUID(); policyExpires = nil
        draftTimer?.invalidate(); draftTimer = nil
        draftScope = nil; draftFields = nil; pendingDraft = nil
        draftReadInFlight = false; draftStatus = nil; restoreButton = nil
        activeNavigation = nil
        policyDenial = nil
        let pages = parents + (browser.map { [$0] } ?? [])
        parents.removeAll()
        orderedTabs.removeAll()
        tabStrip = nil
        recoveryAttempts.removeAll()
        browser = nil
        for page in pages {
            page.stopLoading()
            page.navigationDelegate = nil
            page.uiDelegate = nil
            page.removeFromSuperview()
        }
        navigationState?.discarded()
        updateNavigation()
        dialogs.cancel()
    }

    private func close(release: Bool, deadlineOver: Bool = false) {
        if release, !deadlineOver, !nativeExam.permitsUnlock {
            notice?.stringValue = "DKU OJ must confirm the end of this exam before unlocking."
            return
        }
        // A release arriving under the Leave sheet or the VPN prompt closes it with the rest of the screen.
        if let window = overlay?.window, let sheet = window.attachedSheet { window.endSheet(sheet, returnCode: .cancel) }
        nativeGeneration = UUID(); nativeRequestInFlight = false
        failsafeCancel?(); failsafeCancel = nil; failsafe.clear(); refusedContest = nil
        nativeExam = NativeExamSession()
        activeExamBinding = nil
        retainedWebsiteStore = nil
        stopDeviceTest()
        flow.close(clear: {
            self.holdCancel?(); self.holdCancel = nil
            self.loadRetryCancel?(); self.loadRetryCancel = nil
            self.networkWatch?.stop(); self.networkWatch = nil
            LockdownController.shared.endVPNYield()
            self.cancelHandIn(); self.renewSchedule.reset(); self.failureLog = RepeatedFailureLog()
            EventTap.reloadShortcutAllowed = false
            EventTap.editShortcutsAllowed = false
            self.holdLabel = nil
            self.discardBrowser()
            self.overlay?.removeFromSuperview()
            self.overlay = nil
            self.policy = nil
            self.notice = nil
            self.startField = nil; self.originsField = nil; self.httpOption = nil; self.startButton = nil
            self.backButton = nil; self.reloadButton = nil; self.closePopupButton = nil; self.retryButton = nil; self.vpnButton = nil; self.navigationState = nil
            self.removeRules()
        }, releaseLock: {
            if release {
                self.effects.release()
                self.discardSavedLock()
            }
        })
        didClose()
    }

    private func removeRules() {
        if let id = ruleIdentifier { WKContentRuleListStore.default().removeContentRuleList(forIdentifier: id) { _ in } }
        ruleIdentifier = nil
        // A pending compiler callback cleans up its own result after cancellation.
        pendingRuleIdentifier = nil
    }

    private func diagnostic(_ event: String, url: URL?) {
        effects.diagnostic(event + " origin=" + (url.flatMap(ProtectedBrowserPolicy.origin) ?? "unavailable"))
    }

    private func refuse(_ url: URL? = nil) {
        diagnostic("policy-denied", url: url)
        if let origin = url.flatMap(ProtectedBrowserPolicy.origin) {
            notice?.stringValue = "Blocked site: " + origin + ". Leave and add this exact origin only if you trust it."
        } else {
            notice?.stringValue = "Blocked navigation or feature. Only configured site origins are allowed."
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if tracked(webView), lockIsOwned, action.targetFrame?.isMainFrame == true,
           let url = action.request.url,
           ProtectedBrowserPolicy.origin(url) == ProtectedBrowserPolicy.origin(selection.url) {
            let components = url.path.split(separator: "/").map(String.init)
            if components.count == 3, components[0] == "contest", components[2] == "fairplay-launch" {
                decisionHandler(.cancel)
                guard nativeIdentity.fingerprint != nil, Config.universityPolicyPublicKey != nil, selection.url.scheme == "https" else {
                    notice?.stringValue = "Protected exam admission is not configured on this copy. Contact your instructor."; return
                }
                if nativeExam.beginClaim(components[1]) {
                    refusedContest = nil
                    persistNativeProgress()
                    requestNative("claim/" + components[1], joining: true)
                }
                return
            }
        }
        if tracked(webView), lockIsOwned, action.shouldPerformDownload, let url = action.request.url,
           let policy, DownloadPolicy.originAllowed(url, trusted: policy.downloadOrigins),
           policy.allows(url, mainFrame: action.targetFrame?.isMainFrame ?? true) {
            decisionHandler(.download); return
        }
        let initialPopupBlank = !parents.isEmpty && webView === browser
            && webView.url == nil && action.request.url?.absoluteString == "about:blank"
        let trustedPopupRequest = action.targetFrame == nil && webView === browser
            && policy?.allows(webView.url, mainFrame: true) == true
            && action.request.url?.absoluteString == "about:blank"
        let mainTabNeedsOwnTab = webView === orderedTabs.first && action.targetFrame?.isMainFrame == true
            && policy?.opensInOwnTab(action.request.url) == true
        if mainTabNeedsOwnTab, lockIsOwned, !action.shouldPerformDownload, let url = action.request.url {
            decisionHandler(.cancel)
            openReferenceTab(url)
            return
        }
        guard tracked(webView), lockIsOwned, !action.shouldPerformDownload,
              (initialPopupBlank || trustedPopupRequest) || policy?.allows(action.request.url, mainFrame: action.targetFrame?.isMainFrame ?? true) == true else {
            if webView === browser, lockIsOwned {
                if action.targetFrame?.isMainFrame == true {
                    policyDenial = ProtectedBrowserPolicyDenial(view: webView, navigation: activeNavigation, target: action.request.url)
                }
                refuse(action.request.url)
            }
            decisionHandler(.cancel); return
        }
        if webView === browser, action.targetFrame?.isMainFrame == true,
           let url = action.request.url, policy?.allows(url, mainFrame: true) == true {
            policyDenial = nil
            navigationState?.requested(url)
            diagnostic("navigation-allowed", url: url)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard tracked(webView), lockIsOwned else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        nativeIdentity.respond(challenge, completion: completionHandler)
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard webView === browser, lockIsOwned else { return }
        if navigation === errorPageNavigation { return }
        failureDescription = nil
        activeNavigation = navigation
        dialogs.cancel()
        navigationState?.started()
        updateNavigation()
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        if navigation != nil, navigation === errorPageNavigation { return }
        guard webView === browser, lockIsOwned, let url = webView.url,
              policy?.allows(url, mainFrame: true) == true else { return }
        navigationState?.committed(url)
        diagnostic("navigation-committed", url: url)
        updateNavigation()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === browser, lockIsOwned else { return }
        if navigation === errorPageNavigation { return }
        navigationState?.finished()
        loadRetry.succeeded()
        loadRetryCancel?(); loadRetryCancel = nil
        if let url = webView.url, ProtectedBrowserPolicy.origin(url) == ProtectedBrowserPolicy.origin(selection.url) { hostAnsweredAt = effects.now() }
        LockdownController.shared.endVPNYield()
        updateNavigation()
        handInPageFinished(url: webView.url, title: webView.title)
    }

    private func dialogOrigin(_ webView: WKWebView, frame: WKFrameInfo) -> String? {
        guard webView === browser, lockIsOwned else { return nil }
        let source = frame.securityOrigin
        var components = URLComponents()
        components.scheme = source.protocol
        components.host = source.host
        if source.port != 0 { components.port = source.port }
        guard let url = components.url, policy?.allows(url, mainFrame: true) == true,
              let origin = ProtectedBrowserPolicy.origin(url) else { return nil }
        return origin
    }

    private func showDialog(_ kind: ProtectedBrowserDialogSession.Kind, message: String,
                            defaultText: String? = nil, webView: WKWebView, frame: WKFrameInfo,
                            completion: @escaping (ProtectedBrowserDialogSession.Result) -> Void) {
        guard let origin = dialogOrigin(webView, frame: frame) else {
            completion(ProtectedBrowserDialogSession.cancelled(kind)); return
        }
        dialogs.present(kind, message: message, defaultText: defaultText, origin: origin,
                        in: overlay?.window, stillAllowed: { [weak self, weak webView] in
            guard let self, let webView else { return false }
            return self.dialogOrigin(webView, frame: frame) == origin
        }, leave: { [weak self] in self?.leavePressed() }, completion: completion)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        showDialog(.alert, message: message, webView: webView, frame: frame) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        showDialog(.confirm, message: message, webView: webView, frame: frame) { result in
            if case .confirmed(let value) = result { completionHandler(value) } else { completionHandler(false) }
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        showDialog(.prompt, message: prompt, defaultText: defaultText, webView: webView, frame: frame) { result in
            if case .text(let value) = result { completionHandler(value) } else { completionHandler(nil) }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        let initialPopupBlank = !parents.isEmpty && webView === browser
            && response.response.url?.absoluteString == "about:blank"
        let httpStatus = (response.response as? HTTPURLResponse)?.statusCode
        if response.isForMainFrame, httpStatus != nil, ProtectedBrowserPolicy.origin(response.response.url ?? selection.url) == ProtectedBrowserPolicy.origin(selection.url) {
            ojAnswered()
        }
        if tracked(webView), lockIsOwned, let policy, let url = response.response.url, (httpStatus ?? 200) < 400,
           AllowedSites.isAttachment(contentDisposition: disposition) || !response.canShowMIMEType,
           DownloadPolicy.originAllowed(url, trusted: policy.downloadOrigins),
           policy.allows(url, mainFrame: response.isForMainFrame) {
            decisionHandler(.download); return
        }
        // An HTTP error page is the site's own page: show it, never cancel it for its content type.
        let showable = response.canShowMIMEType || (httpStatus ?? 0) >= 400
        guard tracked(webView), lockIsOwned, showable, !AllowedSites.isAttachment(contentDisposition: disposition),
              initialPopupBlank || policy?.allows(response.response.url, mainFrame: response.isForMainFrame) == true else {
            if webView === browser, lockIsOwned {
                if response.isForMainFrame {
                    policyDenial = ProtectedBrowserPolicyDenial(view: webView, navigation: activeNavigation, target: response.response.url)
                }
                refuse(response.response.url)
            }
            decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard webView === browser, lockIsOwned, orderedTabs.count < 8,
              let opener = webView.url, policy?.allows(opener, mainFrame: true) == true,
              action.request.url?.absoluteString == "about:blank" || policy?.allows(action.request.url, mainFrame: true) == true else {
            refuse(action.request.url); return nil
        }
        // WebKit supplies the configuration to preserve window.opener and the ephemeral session.
        // Do not replace it with a fresh configuration or flatten the navigation into its opener.
        guard !configuration.websiteDataStore.isPersistent,
              configuration.websiteDataStore === webView.configuration.websiteDataStore,
              configuration.userContentController === webView.configuration.userContentController else { refuse(); return nil }
        dialogs.cancel()
        let child = ProtectedUniversityWebView(frame: webView.frame, configuration: configuration)
        child.autoresizingMask = [.width, .height]
        child.navigationDelegate = self
        child.uiDelegate = self
        child.allowsBackForwardNavigationGestures = false
        child.allowsLinkPreview = false
        if Self.clearsClipboard(from: webView, to: child, examTab: orderedTabs.first) { NSPasteboard.general.clearContents() }
        webView.isHidden = true
        parents.append(webView)
        orderedTabs.append(child)
        activeNavigation = nil
        policyDenial = nil
        browser = child
        overlay?.addSubview(child, positioned: .below, relativeTo: webView)
        navigationState = ProtectedBrowserNavigation(startURL: opener)
        updateNavigation()
        overlay?.window?.makeFirstResponder(child)
        return child
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        refuse(); completionHandler(nil)
    }

    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard tracked(webView), lockIsOwned else { return }
        diagnostic("web-process-stopped", url: webView.url)
        // Keep the other tabs and the lock. One automatic recovery per tab;
        // repeated crashes leave a manual Reload action instead of a crash loop.
        if recoveryAttempts.insert(ObjectIdentifier(webView)).inserted,
           policy?.allows(webView.url, mainFrame: true) == true {
            webView.reload()
        } else if webView === browser {
            navigationState?.failed(diagnostic: "Page process stopped")
            updateNavigation()
        }
    }

    private func tracked(_ webView: WKWebView) -> Bool {
        webView === browser || parents.contains { $0 === webView }
    }

    @objc private func closePopupPressed() {
        guard lockIsOwned, let child = browser, child !== orderedTabs.first, let parent = parents.popLast() else { return }
        dialogs.cancel()
        child.stopLoading()
        child.navigationDelegate = nil
        child.uiDelegate = nil
        child.removeFromSuperview()
        orderedTabs.removeAll { $0 === child }
        browser = parent
        activeNavigation = nil
        policyDenial = nil
        parent.isHidden = false
        navigationState = ProtectedBrowserNavigation(startURL: parent.url ?? policy!.startURL)
        if !parent.isLoading { navigationState?.finished() }
        updateNavigation()
        overlay?.window?.makeFirstResponder(parent)
    }

    func webViewDidClose(_ webView: WKWebView) {
        if webView === browser { closePopupPressed() }
        else if let index = parents.firstIndex(where: { $0 === webView }), webView !== orderedTabs.first {
            // A hidden popup may close after opening another; detach it without touching the root.
            parents.remove(at: index)
            orderedTabs.removeAll { $0 === webView }
            rebuildTabs()
            webView.stopLoading()
            webView.navigationDelegate = nil
            webView.uiDelegate = nil
            webView.removeFromSuperview()
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, navigation: navigation, error: error)
    }

    private func navigationFailed(_ webView: WKWebView, navigation: WKNavigation?, error: Error) {
        guard webView === browser, lockIsOwned else { return }
        if navigation != nil, navigation === errorPageNavigation { return }
        let failure = error as NSError
        guard failure.domain != NSURLErrorDomain || failure.code != NSURLErrorCancelled else { return }
        let domain = [NSURLErrorDomain, WKError.errorDomain, "WebKitErrorDomain"].contains(failure.domain) ? failure.domain : "NavigationError"
        let failedURL = (failure.userInfo[NSURLErrorFailingURLErrorKey] as? URL)
            ?? (failure.userInfo[NSURLErrorFailingURLStringErrorKey] as? String).flatMap(URL.init(string:))
        if failure.domain == "WebKitErrorDomain", failure.code == 102,
           let denial = policyDenial, denial.matches(view: webView, navigation: navigation, failedURL: failedURL) {
            policyDenial = nil
            diagnostic("policy-cancelled domain=WebKitErrorDomain code=102", url: denial.target)
            return
        }
        diagnostic("navigation-failed domain=" + domain + " code=" + String(failure.code), url: failedURL)
        // A reply the server did send (bad body, interrupted frame, redirect loop) is not "unreachable":
        // no notice, no retry loop, no VPN prompt; the page stays as the server left it.
        if policy?.allows(failedURL, mainFrame: true) != false,
           LoadOutcome.classify(status: nil, error: failure) != .unreachable {
            navigationState?.finished()
            updateNavigation()
            return
        }
        let blockedOrigin = failedURL.flatMap(ProtectedBrowserPolicy.origin).flatMap { origin in
            policy?.allows(failedURL, mainFrame: true) == false ? " · Blocked site: " + origin : nil
        } ?? ""
        let tls = LoadOutcome.isTLSFailure(domain: failure.domain, code: failure.code)
        navigationState?.failed(diagnostic: tls ? LoadOutcome.tlsNotice : domain + " (" + String(failure.code) + ")" + blockedOrigin)
        failureDescription = failure.localizedDescription
        updateNavigation()
        if blockedOrigin.isEmpty, let failedURL, policy?.allows(failedURL, mainFrame: true) == true {
            errorPageNavigation = effects.showError(webView, Self.errorPageHTML(description: failure.localizedDescription, url: failedURL), failedURL)
        }
        let ojHost = failedURL.flatMap(ProtectedBrowserPolicy.origin) == ProtectedBrowserPolicy.origin(selection.url)
        // A certificate problem means the host answered: retry, but a VPN is not the fix, so no prompt.
        if blockedOrigin.isEmpty { scheduleLoadRetry(offerVPNPrompt: ojHost && webView === orderedTabs.first && !tls) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, navigation: navigation, error: error)
    }
}

enum OJPage {
    /// The page's own <title>, else its first heading, else its first line of text; nil when there is none.
    static func summary(_ data: Data?) -> String? {
        guard let data, !data.isEmpty else { return nil }
        let html = String(decoding: data.prefix(65536), as: UTF8.self)
        func match(_ pattern: String) -> String? {
            guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]),
                  let m = re.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
                  let r = Range(m.range(at: 1), in: html) else { return nil }
            return clean(String(html[r]))
        }
        return match("<title[^>]*>(.*?)</title>") ?? match("<h1[^>]*>(.*?)</h1>")
            ?? clean(html.replacingOccurrences(of: "<(script|style)[^>]*>.*?</\\1>", with: " ", options: [.regularExpression, .caseInsensitive]))
    }

    private static func clean(_ raw: String) -> String? {
        var t = raw.replacingOccurrences(of: "<[^>]*>", with: " ", options: .regularExpression)
        for (e, c) in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#39;", "'")] { t = t.replacingOccurrences(of: e, with: c) }
        let line = t.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty } ?? ""
        let flat = String(String.UnicodeScalarView(line.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }))
            .split(separator: " ").joined(separator: " ")
        return flat.isEmpty ? nil : String(flat.prefix(120))
    }
}

extension ProtectedBrowserController: WKDownloadDelegate {
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        adopt(download)
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        adopt(download)
    }

    private func adopt(_ download: WKDownload) {
        download.delegate = self
        let watch = download.progress.observe(\.completedUnitCount) { [weak self, weak download] progress, _ in
            guard progress.completedUnitCount > DownloadPolicy.maxBytes, let download else { return }
            DispatchQueue.main.async { self?.abort(download, "The download was stopped: it is larger than \(DownloadPolicy.maxBytes / 1_048_576) MB.") }
        }
        activeDownloads[ObjectIdentifier(download)] = (download, watch, nil)
    }

    private func abort(_ download: WKDownload, _ message: String) {
        guard activeDownloads[ObjectIdentifier(download)] != nil else { return }
        download.cancel(nil)
        finish(download, message)
    }

    private func finish(_ download: WKDownload, _ message: String?, removePartial: Bool = true) {
        guard let entry = activeDownloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        entry.watch?.invalidate()
        if removePartial, let file = entry.file { try? FileManager.default.removeItem(at: file) }
        if let message, lockIsOwned { notice?.stringValue = message }
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        guard lockIsOwned, let policy, DownloadPolicy.originAllowed(response.url, trusted: policy.downloadOrigins) else {
            finish(download, nil); completionHandler(nil); return
        }
        switch DownloadPolicy.decide(suggestedName: suggestedFilename, expectedBytes: response.expectedContentLength) {
        case .refuse(let reason):
            finish(download, reason); completionHandler(nil)
        case .save(let name):
            let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
                ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Downloads")
            let folder = downloads.appendingPathComponent(DownloadPolicy.folderName, isDirectory: true)
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            } catch {
                finish(download, "Download failed: the folder \(DownloadPolicy.folderName) could not be created."); completionHandler(nil); return
            }
            let unique = DownloadPolicy.uniqueName(name) { FileManager.default.fileExists(atPath: folder.appendingPathComponent($0).path) }
            let file = folder.appendingPathComponent(unique)
            activeDownloads[ObjectIdentifier(download)]?.file = file
            completionHandler(file)
        }
    }

    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                  decisionHandler: @escaping (WKDownload.RedirectPolicy) -> Void) {
        if lockIsOwned, let policy, DownloadPolicy.originAllowed(request.url, trusted: policy.downloadOrigins) {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
            finish(download, "Download failed: it was redirected to another site.")
        }
    }

    func download(_ download: WKDownload, didReceive challenge: URLAuthenticationChallenge,
                  completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard lockIsOwned else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        nativeIdentity.respond(challenge, completion: completionHandler)
    }

    func downloadDidFinish(_ download: WKDownload) {
        let name = activeDownloads[ObjectIdentifier(download)]?.file?.lastPathComponent
        finish(download, name.map(DownloadPolicy.statusLine(saved:)), removePartial: false)
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        finish(download, "Download failed: \((error as NSError).localizedDescription)")
    }
}

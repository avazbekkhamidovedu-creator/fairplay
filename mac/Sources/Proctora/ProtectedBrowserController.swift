import AppKit
import WebKit

private final class ProtectedUniversityWebView: WKWebView {
    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        menu.removeAllItems()
        super.willOpenMenu(menu, with: event)
    }
    override func quickLook(with event: NSEvent) {}
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { [] }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { [] }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool { false }
}

/// Separate opt-in native browser. Never receives FairPlay bridge scripts, tokens or an exam session.
final class ProtectedBrowserController: NSObject, WKNavigationDelegate, WKUIDelegate {
    private weak var container: NSView?
    private var overlay: NSView?
    private var browser: WKWebView?
    private var policy: ProtectedBrowserPolicy?
    private var flow = ProtectedBrowserFlow()
    private var startField: NSTextField?
    private var originsField: NSTextField?
    private var notice: NSTextField?
    private var startButton: NSButton?
    private var ruleIdentifier: String?
    private var pendingRuleIdentifier: String?
    private var held = false
    private let busy: () -> Bool
    private let didClose: () -> Void
    var isOpen: Bool { overlay != nil }

    init(busy: @escaping () -> Bool, didClose: @escaping () -> Void) {
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

        let title = NSTextField(labelWithString: "Protected university browser")
        title.font = .systemFont(ofSize: 25, weight: .semibold)
        let detail = NSTextField(wrappingLabelWithString: "Local client setup, not university registration. Your university keeps its own login, questions and submissions. No FairPlay exam code is needed in this separate mode.")
        let url = NSTextField(string: "")
        url.placeholderString = "https://your-university.example.edu"
        let origins = NSTextField(string: "")
        origins.placeholderString = "https://login.example.edu, https://assets.example.edu"
        let safety = NSTextField(wrappingLabelWithString: "Save your work first. Locking restricts this Mac and may close other apps. Only the listed HTTPS sites can load. You can leave and unlock at any time; leaving does not submit answers. Some SSO, popups and uploads are not supported. This does not protect other devices or previously downloaded files.")
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
                                      NSTextField(labelWithString: "Additional login / resource origins (optional)"), origins,
                                      safety, status, buttons])
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

    private func checks() -> [String] {
        LockdownController.shared.preflightProblems().map { $0.message }
    }

    @objc private func checkPressed() {
        guard flow.state == .setup else { return }
        let problems = checks()
        notice?.stringValue = problems.isEmpty ? "Mac checks passed. Save your work before locking." : problems.joined(separator: "\n")
    }

    @objc private func startPressed() {
        guard flow.state == .setup, !busy(),
              let parsed = ProtectedBrowserPolicy(start: startField?.stringValue ?? "",
                  additionalOrigins: originsField?.stringValue ?? "", forbiddenOrigin: Config.serverURL) else {
            notice?.stringValue = "Enter an HTTPS university URL and exact HTTPS login/resource origins. Wildcards, IP addresses, custom ports and the FairPlay server are not allowed."
            return
        }
        let problems = checks()
        guard problems.isEmpty else { notice?.stringValue = problems.joined(separator: "\n"); return }
        guard let token = flow.prepare() else { return }
        startButton?.isEnabled = false
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
                    self.startButton?.isEnabled = true
                    self.notice?.stringValue = "Website restrictions could not be prepared. The Mac has not been locked."
                    return
                }
                self.ruleIdentifier = identifier
                let opened = self.flow.activate(token, acquireLock: {
                    guard !self.busy(), !self.held else { return false }
                    Allowlist.setTeacherAllowed(nil)
                    guard self.checks().isEmpty else { return false }
                    return LockdownController.shared.preLock()
                }, open: { self.open(parsed, rules: rules) })
                if !opened {
                    self.removeRules()
                    self.startButton?.isEnabled = true
                    self.notice?.stringValue = "The Mac could not be locked. Recheck permissions and try again. No university page was opened."
                }
            }
        }
    }

    private func open(_ policy: ProtectedBrowserPolicy, rules: WKContentRuleList) {
        guard let root = overlay, !held else {
            notice?.stringValue = "A security check interrupted startup. Leave and unlock, then start again."
            return
        }
        self.policy = policy
        root.subviews.forEach { $0.removeFromSuperview() }
        startField = nil; originsField = nil; startButton = nil
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
        let view = ProtectedUniversityWebView(frame: NSRect(x: 0, y: 0, width: root.bounds.width, height: max(0, root.bounds.height - 58)), configuration: config)
        view.autoresizingMask = [.width, .height]
        view.navigationDelegate = self
        view.uiDelegate = self
        view.allowsBackForwardNavigationGestures = false
        view.allowsLinkPreview = false
        root.addSubview(view)
        browser = view
        let strip = NSStackView()
        strip.orientation = .horizontal
        strip.spacing = 14
        strip.frame = NSRect(x: 16, y: root.bounds.height - 52, width: root.bounds.width - 32, height: 46)
        strip.autoresizingMask = [.width, .minYMargin]
        let status = NSTextField(labelWithString: "Locked browser · " + (policy.startURL.host ?? ""))
        status.lineBreakMode = .byTruncatingTail
        notice = status
        strip.addArrangedSubview(status)
        strip.addArrangedSubview(NSButton(title: "Back", target: self, action: #selector(backPressed)))
        strip.addArrangedSubview(NSButton(title: "Reload", target: self, action: #selector(reloadPressed)))
        strip.addArrangedSubview(NSButton(title: "Leave and unlock", target: self, action: #selector(leavePressed)))
        root.addSubview(strip)
        root.window?.makeFirstResponder(view)
        view.load(URLRequest(url: policy.startURL))
    }

    private var lockIsOwned: Bool {
        !held && flow.ownsLock && LockdownController.shared.isLocked && LockdownController.shared.isPreLock
            && AppSession.shared.exam == nil
    }

    @objc private func backPressed() {
        guard lockIsOwned, let previous = browser?.backForwardList.backItem?.url,
              policy?.allows(previous, mainFrame: true) == true else { return }
        browser?.goBack()
    }
    @objc private func reloadPressed() { if lockIsOwned { browser?.reload() } }

    @objc private func leavePressed() {
        // No submission endpoint is called. The native view is destroyed before unlocking.
        close(release: true)
    }

    func lockEnded() { if isOpen { close(release: false) } }

    /// Existing lock-integrity/permission blockers must remain above this browser too.
    func setHeld(_ held: Bool) {
        self.held = held
        overlay?.isHidden = held
        if held, browser != nil {
            // Hiding is insufficient: a hidden page can keep fetching and submitting.
            // Destroy this page, keep the native lock, and require an explicit fresh session.
            discardBrowser()
            notice?.stringValue = "A security check interrupted browsing. Leave and unlock, then start again."
        }
    }

    private func discardBrowser() {
        browser?.stopLoading()
        browser?.navigationDelegate = nil
        browser?.uiDelegate = nil
        browser?.removeFromSuperview()
        browser = nil
    }

    private func close(release: Bool) {
        flow.close(clear: {
            self.discardBrowser()
            self.overlay?.removeFromSuperview()
            self.overlay = nil
            self.policy = nil
            self.notice = nil
            self.startField = nil; self.originsField = nil; self.startButton = nil
            self.removeRules()
        }, releaseLock: {
            if release { LockdownController.shared.endPreLock() }
        })
        didClose()
    }

    private func removeRules() {
        if let id = ruleIdentifier { WKContentRuleListStore.default().removeContentRuleList(forIdentifier: id) { _ in } }
        ruleIdentifier = nil
        // A pending compiler callback cleans up its own result after cancellation.
        pendingRuleIdentifier = nil
    }

    private func refuse() { notice?.stringValue = "Blocked: this site or action is not in your browser configuration." }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard lockIsOwned, !action.shouldPerformDownload,
              policy?.allows(action.request.url, mainFrame: action.targetFrame?.isMainFrame ?? true) == true else {
            refuse(); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        guard lockIsOwned, response.canShowMIMEType, !AllowedSites.isAttachment(contentDisposition: disposition),
              policy?.allows(response.response.url, mainFrame: response.isForMainFrame) == true else {
            refuse(); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard lockIsOwned, policy?.allows(action.request.url, mainFrame: true) == true else { refuse(); return nil }
        webView.load(action.request) // Basic one-view mode; no external browser or privileged popup.
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        refuse(); completionHandler(nil)
    }

    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        browser?.isHidden = true
        notice?.stringValue = "The website stopped. Leave and unlock, then start again."
    }
}

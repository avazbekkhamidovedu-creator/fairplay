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
    private var parents: [WKWebView] = []
    private var closePopupButton: NSButton?
    private var policy: ProtectedBrowserPolicy?
    private var flow = ProtectedBrowserFlow()
    private var startField: NSTextField?
    private var originsField: NSTextField?
    private var httpOption: NSButton?
    private var notice: NSTextField?
    private var startButton: NSButton?
    private var backButton: NSButton?
    private var reloadButton: NSButton?
    private var navigationState: ProtectedBrowserNavigation?
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
        static var live: Effects {
            Effects(checks: { LockdownController.shared.preflightProblems().map { $0.message } },
                    acquire: {
                        Allowlist.setTeacherAllowed(nil)
                        guard LockdownController.shared.preflightProblems().isEmpty else { return false }
                        return LockdownController.shared.preLock()
                    },
                    ownsLock: { LockdownController.shared.isLocked && LockdownController.shared.isPreLock && AppSession.shared.exam == nil },
                    release: { LockdownController.shared.endPreLock() },
                    load: { $0.load(URLRequest(url: $1)) })
        }
    }
    private let effects: Effects
    var isOpen: Bool { overlay != nil }

    init(busy: @escaping () -> Bool, didClose: @escaping () -> Void) {
        self.busy = busy
        self.didClose = didClose
        self.effects = .live
    }

    // Dependency injection for isolated tests; production callers always use the live initializer above.
    init(busy: @escaping () -> Bool, didClose: @escaping () -> Void, effects: Effects) {
        self.effects = effects
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
        let safety = NSTextField(wrappingLabelWithString: "Save your work first. Locking restricts this Mac and may close other apps. Only the listed sites can load. You can leave and unlock at any time; leaving does not submit answers. Some SSO, popups and uploads are not supported. This does not protect other devices or previously downloaded files. HTTP is unencrypted and can be read or changed by others. Never enter passwords or sensitive information on HTTP.")
        let http = NSButton(checkboxWithTitle: "Allow local/intranet HTTP (development only)", target: nil, action: nil)
        http.isEnabled = Config.isDevBuild
        http.toolTip = Config.isDevBuild ? "HTTP is available only for IP addresses, unqualified intranet names and .local sites. Traffic can be read or changed by others." : "HTTP is unavailable in this build. Use HTTPS."
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
                                      NSTextField(labelWithString: "Additional login / resource origins (optional)"), origins,
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
                  additionalOrigins: originsField?.stringValue ?? "", forbiddenOrigin: Config.serverURL,
                  allowHTTP: Config.isDevBuild && httpOption?.state == .on) else {
            notice?.stringValue = "Enter a valid address and exact login/resource origins. Bare addresses use HTTPS. Local/intranet HTTP requires the development build’s HTTP checkbox; other HTTP sites are unsupported. Wildcards, credentials and the FairPlay server are not allowed."
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
                    guard self.checks().isEmpty else { return false }
                    return self.effects.acquire()
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
        startField = nil; originsField = nil; httpOption = nil; startButton = nil
        let config = effects.configuration(rules)
        let view = ProtectedUniversityWebView(frame: NSRect(x: 0, y: 0, width: root.bounds.width, height: max(0, root.bounds.height - 58)), configuration: config)
        view.autoresizingMask = [.width, .height]
        view.navigationDelegate = self
        view.uiDelegate = self
        view.allowsBackForwardNavigationGestures = false
        view.allowsLinkPreview = false
        root.addSubview(view)
        browser = view
        navigationState = ProtectedBrowserNavigation(startURL: policy.startURL)
        let strip = NSStackView()
        strip.orientation = .horizontal
        strip.spacing = 14
        strip.frame = NSRect(x: 16, y: root.bounds.height - 52, width: root.bounds.width - 32, height: 46)
        strip.autoresizingMask = [.width, .minYMargin]
        let status = NSTextField(labelWithString: "")
        status.lineBreakMode = .byTruncatingTail
        notice = status
        strip.addArrangedSubview(status)
        let back = NSButton(title: "Back", target: self, action: #selector(backPressed))
        let reload = NSButton(title: "Reload", target: self, action: #selector(reloadPressed))
        backButton = back; reloadButton = reload
        strip.addArrangedSubview(back)
        strip.addArrangedSubview(reload)
        let closePopup = NSButton(title: "Close sign-in window", target: self, action: #selector(closePopupPressed))
        closePopupButton = closePopup
        strip.addArrangedSubview(closePopup)
        strip.addArrangedSubview(NSButton(title: "Leave and unlock", target: self, action: #selector(leavePressed)))
        root.addSubview(strip)
        root.window?.makeFirstResponder(view)
        updateNavigation()
        effects.load(view, policy.startURL)
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
        notice?.stringValue = navigationState?.message ?? ""
        closePopupButton?.isHidden = parents.isEmpty
        backButton?.isEnabled = browser?.canGoBack == true && lockIsOwned
        reloadButton?.isEnabled = browser != nil && reloadTarget != nil && lockIsOwned
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

    @objc private func reloadPressed() {
        guard lockIsOwned, let view = browser, let target = reloadTarget else { return }
        let retryFailedRequest = navigationState?.phase == .failed
        navigationState?.started()
        updateNavigation()
        // reload() has no committed page after a first-load failure, and may otherwise
        // reload the old document instead of the failed destination. Retry the permitted request.
        if retryFailedRequest { effects.load(view, target) }
        else { view.reload() }
    }

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
        let pages = parents + (browser.map { [$0] } ?? [])
        parents.removeAll()
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

    private func close(release: Bool) {
        flow.close(clear: {
            self.discardBrowser()
            self.overlay?.removeFromSuperview()
            self.overlay = nil
            self.policy = nil
            self.notice = nil
            self.startField = nil; self.originsField = nil; self.httpOption = nil; self.startButton = nil
            self.backButton = nil; self.reloadButton = nil; self.closePopupButton = nil; self.navigationState = nil
            self.removeRules()
        }, releaseLock: {
            if release { self.effects.release() }
        })
        didClose()
    }

    private func removeRules() {
        if let id = ruleIdentifier { WKContentRuleListStore.default().removeContentRuleList(forIdentifier: id) { _ in } }
        ruleIdentifier = nil
        // A pending compiler callback cleans up its own result after cancellation.
        pendingRuleIdentifier = nil
    }

    private func refuse(_ url: URL? = nil) {
        if let origin = url.flatMap(ProtectedBrowserPolicy.origin) {
            notice?.stringValue = "Blocked site: " + origin + ". Leave and add this exact origin only if you trust it."
        } else {
            notice?.stringValue = "Blocked navigation or feature. Only configured site origins are allowed."
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let initialPopupBlank = !parents.isEmpty && webView === browser
            && webView.url == nil && action.request.url?.absoluteString == "about:blank"
        let trustedPopupRequest = action.targetFrame == nil && webView === browser
            && policy?.allows(webView.url, mainFrame: true) == true
            && action.request.url?.absoluteString == "about:blank"
        guard tracked(webView), lockIsOwned, !action.shouldPerformDownload,
              (initialPopupBlank || trustedPopupRequest) || policy?.allows(action.request.url, mainFrame: action.targetFrame?.isMainFrame ?? true) == true else {
            refuse(action.request.url); decisionHandler(.cancel); return
        }
        if webView === browser, action.targetFrame?.isMainFrame == true,
           let url = action.request.url, policy?.allows(url, mainFrame: true) == true {
            navigationState?.requested(url)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard webView === browser, lockIsOwned else { return }
        dialogs.cancel()
        navigationState?.started()
        updateNavigation()
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        guard webView === browser, lockIsOwned, let url = webView.url,
              policy?.allows(url, mainFrame: true) == true else { return }
        navigationState?.committed(url)
        updateNavigation()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === browser, lockIsOwned else { return }
        navigationState?.finished()
        updateNavigation()
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
        }, leave: { [weak self] in self?.close(release: true) }, completion: completion)
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
        guard tracked(webView), lockIsOwned, response.canShowMIMEType, !AllowedSites.isAttachment(contentDisposition: disposition),
              initialPopupBlank || policy?.allows(response.response.url, mainFrame: response.isForMainFrame) == true else {
            refuse(response.response.url); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard webView === browser, lockIsOwned, parents.count < 4,
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
        webView.isHidden = true
        parents.append(webView)
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
        guard tracked(webView) else { return }
        discardBrowser()
    }

    private func tracked(_ webView: WKWebView) -> Bool {
        webView === browser || parents.contains { $0 === webView }
    }

    @objc private func closePopupPressed() {
        guard lockIsOwned, let child = browser, let parent = parents.popLast() else { return }
        dialogs.cancel()
        child.stopLoading()
        child.navigationDelegate = nil
        child.uiDelegate = nil
        child.removeFromSuperview()
        browser = parent
        parent.isHidden = false
        navigationState = ProtectedBrowserNavigation(startURL: parent.url ?? policy!.startURL)
        if !parent.isLoading { navigationState?.finished() }
        updateNavigation()
        overlay?.window?.makeFirstResponder(parent)
    }

    func webViewDidClose(_ webView: WKWebView) {
        if webView === browser { closePopupPressed() }
        else if let index = parents.firstIndex(where: { $0 === webView }), index > 0 {
            // A hidden popup may close after opening another; detach it without touching the root.
            parents.remove(at: index)
            webView.stopLoading()
            webView.navigationDelegate = nil
            webView.uiDelegate = nil
            webView.removeFromSuperview()
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, error: error)
    }

    private func navigationFailed(_ webView: WKWebView, error: Error) {
        guard webView === browser, lockIsOwned else { return }
        let failure = error as NSError
        guard failure.domain != NSURLErrorDomain || failure.code != NSURLErrorCancelled else { return }
        let domain = [NSURLErrorDomain, WKError.errorDomain].contains(failure.domain) ? failure.domain : "NavigationError"
        let failedURL = (failure.userInfo[NSURLErrorFailingURLErrorKey] as? URL)
            ?? (failure.userInfo[NSURLErrorFailingURLStringErrorKey] as? String).flatMap(URL.init(string:))
        let blockedOrigin = failedURL.flatMap(ProtectedBrowserPolicy.origin).flatMap { origin in
            policy?.allows(failedURL, mainFrame: true) == false ? " · Blocked site: " + origin : nil
        } ?? ""
        navigationState?.failed(diagnostic: domain + " (" + String(failure.code) + ")" + blockedOrigin)
        updateNavigation()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, error: error)
    }
}

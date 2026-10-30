import AppKit
import WebKit

/// Owns the one Duke Box pane (BoxPolicy.swift has the rules). The pane is a view INSIDE the locked
/// exam window, above the exam page, so no lock rule that hides or kills other windows touches it.
/// MainWindowController only forwards the two bridge actions and closes it on unlock.
final class BoxCoordinator {
    private var overlay: BoxOverlayView?
    private var generation = 0
    private var pending: (([String: Any]) -> Void)?
    private var held = false
    private let ruleListID = "fairplay-box"

    var isOpen: Bool { overlay != nil }

    /// A red blocking or permission screen is up: the pane is hidden until it clears, and an open_box is refused.
    func setHeld(_ on: Bool) {
        held = on
        overlay?.isHidden = on
        if !on { overlay?.focusPage() }
    }

    func open(body: [String: Any], locked: Bool, preLock: Bool, hasAttempt: Bool, container: NSView?, above: NSView?,
              send: @escaping ([String: Any]) -> Void) {
        if let text = BoxPolicy.openRefusal(locked: locked, preLock: preLock, hasAttempt: hasAttempt, covered: held) {
            send(BoxPolicy.message("error", error: text))
            return
        }
        if overlay != nil || pending != nil {
            send(BoxPolicy.message("error", error: BoxPolicy.alreadyOpen))
            return
        }
        guard let container, let policy = BoxPolicy(body: body["box"] as? [String: Any], allowDev: Config.isDevBuild) else {
            send(BoxPolicy.message("error", error: BoxPolicy.notAvailable))
            return
        }
        generation += 1
        let mine = generation
        let id = ruleListID
        pending = send
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: id, encodedContentRuleList: policy.contentRuleListJSON()) { [weak self] list, _ in
            DispatchQueue.main.async {
                guard let self, self.generation == mine else { return }
                self.pending = nil
                guard self.held == false, LockdownController.shared.isLocked, !LockdownController.shared.isPreLock, let list else {
                    send(BoxPolicy.message("error", error: BoxPolicy.notAvailable))
                    return
                }
                let view = BoxOverlayView(policy: policy, rules: list, frame: container.bounds) { [weak self] state, error in
                    self?.finish(state: state, error: error, send: send)
                }
                container.addSubview(view, positioned: .above, relativeTo: above)
                self.overlay = view
                view.begin()
                EventLog.shared.record(.boxOpened(BoxPolicy.openedDetail(policy.startURL)))
                send(BoxPolicy.message("opened"))
            }
        }
    }

    /// Closes and wipes the pane. `notify` answers the page (a close_box); an unlock, a new lock message
    /// or the app quitting closes without a close answer. A compile still running is answered with an error.
    func close(notify: ((([String: Any]) -> Void))? = nil) {
        generation += 1
        let was = overlay
        let waiting = pending
        pending = nil
        was?.end()
        overlay = nil
        if was != nil { WKContentRuleListStore.default().removeContentRuleList(forIdentifier: ruleListID) { _ in } }
        waiting?(BoxPolicy.message("error", error: BoxPolicy.notAvailable))
        notify?(BoxPolicy.message("closed"))
    }

    private func finish(state: String, error: String?, send: @escaping ([String: Any]) -> Void) {
        generation += 1
        overlay?.end()
        overlay = nil
        WKContentRuleListStore.default().removeContentRuleList(forIdentifier: ruleListID) { _ in }
        send(BoxPolicy.message(state, error: error))
    }
}

final class BoxOverlayView: NSView, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private let policy: BoxPolicy
    private let store = WKWebsiteDataStore.nonPersistent()
    private var webView: ExamWebView?
    private let hostLabel = NSTextField(labelWithString: "")
    private let noteLabel = NSTextField(labelWithString: "")
    private var onEnd: ((String, String?) -> Void)?
    private var urlObservation: NSKeyValueObservation?
    private var prefixWatch = BoxPolicy.PrefixWatch()
    private var enforcing: URL?
    private static let handlerName = "fpbox"

    init(policy: BoxPolicy, rules: WKContentRuleList, frame: NSRect, onEnd: @escaping (String, String?) -> Void) {
        self.policy = policy
        self.onEnd = onEnd
        super.init(frame: frame)
        autoresizingMask = [.width, .height]
        wantsLayer = true
        layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        let config = WKWebViewConfiguration()
        config.websiteDataStore = store
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        let ucc = WKUserContentController()
        ucc.add(WeakScriptHandler(self), name: Self.handlerName)
        ucc.addUserScript(WKUserScript(source: policy.guardScript(handler: Self.handlerName),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
        ucc.add(rules)
        config.userContentController = ucc
        let wv = ExamWebView(frame: .zero, configuration: config)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.allowsBackForwardNavigationGestures = false
        wv.allowsMagnification = false
        wv.allowsLinkPreview = false
        if #available(macOS 13.3, *) { wv.isInspectable = false }
        webView = wv

        hostLabel.font = .monospacedSystemFont(ofSize: 13, weight: .medium)
        hostLabel.lineBreakMode = .byTruncatingMiddle
        hostLabel.isSelectable = false
        noteLabel.font = .systemFont(ofSize: 12)
        noteLabel.textColor = .systemRed
        noteLabel.alignment = .right
        noteLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let close = NSButton(title: "Close", target: self, action: #selector(closePressed))
        close.bezelStyle = .rounded
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let strip = NSStackView(views: [hostLabel, spacer, noteLabel, close])
        strip.orientation = .horizontal
        strip.edgeInsets = NSEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
        strip.wantsLayer = true
        strip.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        for v in [strip, wv] as [NSView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            addSubview(v)
        }
        NSLayoutConstraint.activate([
            strip.topAnchor.constraint(equalTo: topAnchor),
            strip.leadingAnchor.constraint(equalTo: leadingAnchor),
            strip.trailingAnchor.constraint(equalTo: trailingAnchor),
            wv.topAnchor.constraint(equalTo: strip.bottomAnchor),
            wv.leadingAnchor.constraint(equalTo: leadingAnchor),
            wv.trailingAnchor.constraint(equalTo: trailingAnchor),
            wv.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }

    required init?(coder: NSCoder) { nil }

    func begin() {
        hostLabel.stringValue = BoxPolicy.displayHost(policy.startURL)
        urlObservation = webView?.observe(\.url, options: [.new]) { [weak self] wv, _ in self?.enforcePrefix(wv.url) }
        webView?.load(URLRequest(url: policy.startURL))
        focusPage()
    }

    func focusPage() { window?.makeFirstResponder(webView) }

    /// A single-page app can change its address without a page load (history.pushState): every URL change
    /// is checked, and a page outside the shared item's path is replaced by the start page (or closed when it keeps happening).
    private func enforcePrefix(_ url: URL?) {
        guard let webView, onEnd != nil else { return }
        if !policy.isOutsidePrefix(url) {
            enforcing = nil
            return
        }
        if enforcing == url { return }
        enforcing = url
        refuse(url)
        switch policy.urlChange(url, watch: &prefixWatch) {
        case .stay: break
        case .reloadStart: webView.load(URLRequest(url: policy.startURL))
        case .close:
            let done = onEnd
            onEnd = nil
            done?("error", BoxPolicy.notAvailable)
        }
    }

    /// Stop, wipe the private data store and leave. No answer.
    func end() {
        onEnd = nil
        urlObservation = nil
        webView?.navigationDelegate = nil
        webView?.uiDelegate = nil
        webView?.stopLoading()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: Self.handlerName)
        webView?.configuration.userContentController.removeAllUserScripts()
        webView?.configuration.userContentController.removeAllContentRuleLists()
        store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) {}
        webView?.removeFromSuperview()
        webView = nil
        removeFromSuperview()
    }

    @objc private func closePressed() {
        let done = onEnd
        onEnd = nil
        done?("closed", nil)
    }

    private func refuse(_ url: URL?) {
        record(BoxPolicy.blockedDetail(url))
    }

    private func record(_ detail: String) {
        EventLog.shared.record(.blockedNavigation(detail))
        noteLabel.stringValue = "This page isn't available here"
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.shouldPerformDownload {
            refuse(url)
            decisionHandler(.cancel)
            return
        }
        let main = navigationAction.targetFrame?.isMainFrame ?? true
        if policy.decide(url, mainFrame: main, method: navigationAction.request.httpMethod ?? "GET") == .allow {
            decisionHandler(.allow)
        } else {
            refuse(url)
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        let download = AllowedSites.isAttachment(contentDisposition: disposition) || !navigationResponse.canShowMIMEType
        if download { refuse(navigationResponse.response.url) }
        decisionHandler(download ? .cancel : .allow)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        hostLabel.stringValue = BoxPolicy.displayHost(webView.url)
        if !policy.isOutsidePrefix(webView.url) { noteLabel.stringValue = "" }
        enforcePrefix(webView.url)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        enforcePrefix(webView.url)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if code != NSURLErrorCancelled && code != 102 { noteLabel.stringValue = "This page could not load" }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        let done = onEnd
        onEnd = nil
        done?("error", BoxPolicy.notAvailable)
    }

    // MARK: - WKUIDelegate

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url, policy.popup(url, method: navigationAction.request.httpMethod ?? "GET") == .loadHere else {
            refuse(navigationAction.request.url)
            return nil
        }
        webView.load(navigationAction.request)
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        refuse(frame.request.url)
        completionHandler(nil)
    }

    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.deny)
    }

    // MARK: - Script refusals (a method the content rule list cannot see)

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == Self.handlerName, let d = message.body as? [String: Any],
              let host = d["host"] as? String, let path = d["path"] as? String else { return }
        record(BoxPolicy.blockedDetail(host: host, path: path))
    }
}

import UIKit
import WebKit
import FairPlayCore

/// Owns the one Duke Box pane (FairPlayCore BoxPolicy has the rules). The pane is a view INSIDE the
/// locked exam screen, above the exam page, not a presented controller or a window, so assessment
/// mode and the lock checks see nothing new. ExamViewController only forwards the two bridge actions
/// and closes it on unlock.
@MainActor
final class BoxCoordinator {
    private var overlay: BoxOverlayView?
    private var generation = 0
    private let ruleListID = "fairplay-box"

    var isOpen: Bool { overlay != nil }

    func open(body: [String: Any], locked: Bool, preLock: Bool, hasAttempt: Bool, container: UIView,
              send: @escaping ([String: Any]) -> Void) {
        if let text = BoxPolicy.openRefusal(locked: locked, preLock: preLock, hasAttempt: hasAttempt) {
            send(BoxPolicy.message("error", error: text))
            return
        }
        guard let policy = BoxPolicy(body: body["box"] as? [String: Any], allowDev: Config.isDevBuild) else {
            send(BoxPolicy.message("error", error: BoxPolicy.notAvailable))
            return
        }
        if overlay != nil {
            send(BoxPolicy.message("opened"))
            return
        }
        generation += 1
        let mine = generation
        let id = ruleListID
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: id, encodedContentRuleList: policy.contentRuleListJSON()) { [weak self] list, _ in
            Task { @MainActor in
                guard let self, self.generation == mine, self.overlay == nil,
                      LockdownController.shared.isLocked, !LockdownController.shared.isPreLock else { return }
                guard let list else {
                    send(BoxPolicy.message("error", error: BoxPolicy.notAvailable))
                    return
                }
                let view = BoxOverlayView(policy: policy, rules: list) { [weak self] state, error in
                    self?.finish(state: state, error: error, send: send)
                }
                view.translatesAutoresizingMaskIntoConstraints = false
                container.addSubview(view)
                NSLayoutConstraint.activate([
                    view.topAnchor.constraint(equalTo: container.topAnchor),
                    view.bottomAnchor.constraint(equalTo: container.bottomAnchor),
                    view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                    view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                ])
                self.overlay = view
                view.begin()
                EventLog.shared.record("box_opened", BoxPolicy.openedDetail(policy.startURL))
                send(BoxPolicy.message("opened"))
            }
        }
    }

    /// Closes and wipes the pane. `notify` answers the page (close_box); an unlock, a new lock message
    /// or the app quitting closes without an answer.
    func close(notify: (([String: Any]) -> Void)? = nil) {
        generation += 1
        guard let was = overlay else { return }
        was.end()
        overlay = nil
        WKContentRuleListStore.default().removeContentRuleList(forIdentifier: ruleListID) { _ in }
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

final class BoxOverlayView: UIView, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private let policy: BoxPolicy
    private let store = WKWebsiteDataStore.nonPersistent()
    private var webView: WKWebView?
    private let hostLabel = UILabel()
    private let noteLabel = UILabel()
    private var onEnd: ((String, String?) -> Void)?
    private static let handlerName = "fpbox"

    init(policy: BoxPolicy, rules: WKContentRuleList, onEnd: @escaping (String, String?) -> Void) {
        self.policy = policy
        self.onEnd = onEnd
        super.init(frame: .zero)
        backgroundColor = .systemBackground

        let config = WKWebViewConfiguration()
        config.websiteDataStore = store
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        config.dataDetectorTypes = []
        config.allowsInlineMediaPlayback = true
        let ucc = WKUserContentController()
        ucc.add(WeakScriptHandler(self), name: Self.handlerName)
        ucc.addUserScript(WKUserScript(source: policy.guardScript(handler: Self.handlerName),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
        ucc.add(rules)
        config.userContentController = ucc
        let wv = WKWebView(frame: .zero, configuration: config)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.allowsBackForwardNavigationGestures = false
        wv.allowsLinkPreview = false
        wv.isFindInteractionEnabled = false
        wv.isInspectable = false
        webView = wv

        hostLabel.font = .monospacedSystemFont(ofSize: 15, weight: .medium)
        hostLabel.lineBreakMode = .byTruncatingMiddle
        hostLabel.text = BoxPolicy.displayHost(policy.startURL)
        noteLabel.font = .systemFont(ofSize: 13)
        noteLabel.textColor = .systemRed
        noteLabel.textAlignment = .right
        noteLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        var closeConfig = UIButton.Configuration.plain()
        closeConfig.title = "Close"
        let close = UIButton(configuration: closeConfig, primaryAction: UIAction { [weak self] _ in self?.closePressed() })
        close.setContentHuggingPriority(.required, for: .horizontal)
        let strip = UIStackView(arrangedSubviews: [hostLabel, noteLabel, close])
        strip.axis = .horizontal
        strip.alignment = .center
        strip.spacing = 12
        strip.isLayoutMarginsRelativeArrangement = true
        strip.directionalLayoutMargins = NSDirectionalEdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 8)
        strip.backgroundColor = .secondarySystemBackground

        for v in [strip, wv] as [UIView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            addSubview(v)
        }
        NSLayoutConstraint.activate([
            strip.topAnchor.constraint(equalTo: safeAreaLayoutGuide.topAnchor),
            strip.leadingAnchor.constraint(equalTo: leadingAnchor),
            strip.trailingAnchor.constraint(equalTo: trailingAnchor),
            wv.topAnchor.constraint(equalTo: strip.bottomAnchor),
            wv.leadingAnchor.constraint(equalTo: leadingAnchor),
            wv.trailingAnchor.constraint(equalTo: trailingAnchor),
            wv.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }

    required init?(coder: NSCoder) { nil }

    func begin() { webView?.load(URLRequest(url: policy.startURL)) }

    /// Stop, wipe the private data store and leave. No answer.
    func end() {
        onEnd = nil
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

    private func closePressed() {
        let done = onEnd
        onEnd = nil
        done?("closed", nil)
    }

    private func refuse(_ url: URL?) {
        MainActor.assumeIsolated { EventLog.shared.record("blocked_navigation", BoxPolicy.blockedDetail(url)) }
        noteLabel.text = "This page isn't available here"
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.shouldPerformDownload {
            refuse(url)
            decisionHandler(.cancel)
            return
        }
        let main = navigationAction.targetFrame?.isMainFrame ?? true
        if policy.decide(url, mainFrame: main, method: navigationAction.request.httpMethod ?? "GET") == .allow {
            decisionHandler(ExamViewController.allowInPlace)
        } else {
            refuse(url)
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        let download = AllowedSites.isAttachment(contentDisposition: disposition) || !navigationResponse.canShowMIMEType
        if download { refuse(navigationResponse.response.url) }
        decisionHandler(download ? .cancel : .allow)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        hostLabel.text = BoxPolicy.displayHost(webView.url)
        noteLabel.text = ""
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if code != NSURLErrorCancelled && code != 102 { noteLabel.text = "This page could not load" }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        let done = onEnd
        onEnd = nil
        done?("error", BoxPolicy.notAvailable)
    }

    // MARK: - WKUIDelegate

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url,
              policy.popup(url, method: navigationAction.request.httpMethod ?? "GET") == .loadHere else {
            refuse(navigationAction.request.url)
            return nil
        }
        webView.load(navigationAction.request)
        return nil
    }

    func webView(_ webView: WKWebView, contextMenuConfigurationForElement elementInfo: WKContextMenuElementInfo,
                 completionHandler: @escaping (UIContextMenuConfiguration?) -> Void) {
        completionHandler(nil)
    }

    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.deny)
    }

    // MARK: - Script refusals (a method the content rule list cannot see)

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == Self.handlerName, let d = message.body as? [String: Any],
              let host = d["host"] as? String, let path = d["path"] as? String,
              var comps = URLComponents(string: "https://x.invalid") else { return }
        comps.host = host
        comps.percentEncodedPath = path.hasPrefix("/") ? path : "/" + path
        refuse(comps.url)
    }
}

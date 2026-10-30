import UIKit
import WebKit
import FairPlayCore

/// Owns the one homework sign-in panel (FairPlayCore HomeworkLogin has the rules). The panel is a view
/// INSIDE the exam screen, above the page web view and below the blocking screen and privacy cover.
/// ExamViewController forwards the two bridge actions to `open` / `clear`, tells it when a cover is
/// up (`setHeld`) and calls `dismiss` when a lock, session or device test starts.
@MainActor
final class HomeworkLoginCoordinator {
    private var panel: HomeworkLoginPanel?
    private var held = false

    var isOpen: Bool { panel != nil }

    func setHeld(_ on: Bool) {
        held = on
        panel?.isHidden = on
    }

    func open(body: [String: Any], busy: Bool, deviceTestRunning: Bool, container: UIView, above: UIView,
              top: NSLayoutYAxisAnchor, raise: () -> Void, send: @escaping ([String: Any]) -> Void) {
        let state = body["state"] as? String
        let login = HomeworkLogin(body: body)
        switch HomeworkPanelPolicy.open(examActive: busy, deviceTestRunning: deviceTestRunning,
                                        panelOpen: panel != nil, parsed: login != nil) {
        case .refuse(let text):
            send(HomeworkLogin.message(state: state, outcome: .failed(text)))
            return
        case .start:
            break
        }
        guard let login else { return }
        let view = HomeworkLoginPanel(login: login) { [weak self] outcome in
            self?.panel = nil
            if let outcome { send(login.message(for: outcome)) }
        }
        view.translatesAutoresizingMaskIntoConstraints = false
        container.insertSubview(view, aboveSubview: above)
        NSLayoutConstraint.activate([
            view.topAnchor.constraint(equalTo: top),
            view.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
        ])
        raise()
        panel = view
        view.isHidden = held
        view.begin()
    }

    /// Sign-out or an exam starting: close the panel without an answer and wipe its data store.
    func clear() {
        panel?.end(nil)
        panel = nil
    }

    func dismiss() { clear() }
}

final class HomeworkLoginPanel: UIView, WKNavigationDelegate, WKUIDelegate {

    private var login: HomeworkLogin
    private var webView: WKWebView?
    private let store = WKWebsiteDataStore.nonPersistent()
    private let hostLabel = UILabel()
    private let noteLabel = UILabel()
    private var completion: ((HomeworkLogin.Outcome?) -> Void)?

    init(login: HomeworkLogin, completion: @escaping (HomeworkLogin.Outcome?) -> Void) {
        self.login = login
        self.completion = completion
        super.init(frame: .zero)
        backgroundColor = .systemBackground
        build()
    }

    required init?(coder: NSCoder) { nil }

    private func build() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = store
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        config.dataDetectorTypes = []
        config.allowsInlineMediaPlayback = true
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(source: FilePicker.guardScript(origin: "https://none.invalid", post: "void"),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
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
        hostLabel.text = HomeworkLogin.displayHost(login.startURL)
        noteLabel.font = .systemFont(ofSize: 13)
        noteLabel.textColor = .systemRed
        noteLabel.textAlignment = .right
        noteLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        var closeConfig = UIButton.Configuration.plain()
        closeConfig.title = "Close"
        let close = UIButton(configuration: closeConfig, primaryAction: UIAction { [weak self] _ in self?.end(.cancelled) })
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

    func begin() {
        guard let webView else { return }
        webView.load(URLRequest(url: login.startURL))
    }

    /// Leave, wipe the data store and answer once. nil = no answer (sign-out, exam start).
    func end(_ outcome: HomeworkLogin.Outcome?) {
        let done = completion
        completion = nil
        webView?.navigationDelegate = nil
        webView?.uiDelegate = nil
        webView?.stopLoading()
        store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) {}
        webView?.removeFromSuperview()
        webView = nil
        removeFromSuperview()
        if let done { done(outcome) }
    }

    private func handle(_ nav: HomeworkLogin.Navigation) -> Bool {
        switch nav {
        case .allow:
            return true
        case .block:
            noteLabel.text = HomeworkLogin.notPartOfSignIn
        case .ignore:
            break
        case .finish(let ticket):
            DispatchQueue.main.async { [weak self] in self?.end(.ticket(ticket)) }
        }
        return false
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard !navigationAction.shouldPerformDownload, let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        let main = navigationAction.targetFrame?.isMainFrame ?? true
        decisionHandler(handle(login.decide(url, mainFrame: main)) ? ExamViewController.allowInPlace : .cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        let download = AllowedSites.isAttachment(contentDisposition: disposition) || !navigationResponse.canShowMIMEType
        decisionHandler(download ? .cancel : .allow)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        hostLabel.text = HomeworkLogin.displayHost(webView.url)
        noteLabel.text = ""
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        hostLabel.text = HomeworkLogin.displayHost(webView.url)
        if let again = login.resume(afterFinishing: webView.url) { webView.load(URLRequest(url: again)) }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if code != NSURLErrorCancelled && code != 102 { noteLabel.text = "This page could not load" }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        end(.failed(HomeworkLogin.pageFailed))
    }

    // MARK: - WKUIDelegate

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, handle(login.decide(url, mainFrame: true)) {
            webView.load(navigationAction.request)
        }
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
}

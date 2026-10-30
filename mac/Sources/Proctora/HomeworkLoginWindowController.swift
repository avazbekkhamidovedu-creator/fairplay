import AppKit
import WebKit

/// Owns the one homework sign-in panel (HomeworkLogin.swift has the rules). The panel is a view INSIDE
/// the main window, above the page web view and below the blocking and permission screens.
/// MainWindowController forwards the two bridge actions to `open` / `clear`, tells it when a cover
/// is up (`setHeld`) and calls `dismiss` when a lock, session or device test starts.
final class HomeworkLoginCoordinator {
    private var panel: HomeworkLoginPanel?
    private var held = false
    private weak var page: NSView?

    var isOpen: Bool { panel != nil }

    func setHeld(_ on: Bool) {
        held = on
        panel?.isHidden = on
        updatePage()
        if !on { panel?.focusPage() }
    }

    private func updatePage() {
        page?.isHidden = HomeworkPanelPolicy.pageHidden(panelOpen: panel != nil, held: held)
        if panel == nil, let page, let win = page.window, win.firstResponder !== page { win.makeFirstResponder(page) }
    }

    func open(body: [String: Any], busy: Bool, deviceTestRunning: Bool, container: NSView?, above: NSView?,
              send: @escaping ([String: Any]) -> Void) {
        let state = body["state"] as? String
        let login = HomeworkLogin(body: body)
        switch HomeworkPanelPolicy.open(examActive: busy, deviceTestRunning: deviceTestRunning,
                                        panelOpen: panel != nil, parsed: login != nil && container != nil) {
        case .refuse(let text):
            send(HomeworkLogin.message(state: state, outcome: .failed(text)))
            return
        case .start:
            break
        }
        guard let login, let container else { return }
        let view = HomeworkLoginPanel(login: login, frame: container.bounds) { [weak self] outcome in
            self?.panel = nil
            self?.updatePage()
            if let outcome { send(login.message(for: outcome)) }
        }
        container.addSubview(view, positioned: .above, relativeTo: above)
        panel = view
        page = above
        view.isHidden = held
        updatePage()
        view.begin()
    }

    /// Sign-out: close the panel without an answer and wipe its data store.
    func clear() { panel?.end(nil); panel = nil; updatePage() }

    func dismiss() { clear() }
}

final class HomeworkLoginPanel: NSView, WKNavigationDelegate, WKUIDelegate {

    private var login: HomeworkLogin
    private var webView: ExamWebView?
    private let store = WKWebsiteDataStore.nonPersistent()
    private let hostLabel = NSTextField(labelWithString: "")
    private let noteLabel = NSTextField(labelWithString: "")
    private var completion: ((HomeworkLogin.Outcome?) -> Void)?

    init(login: HomeworkLogin, frame: NSRect, completion: @escaping (HomeworkLogin.Outcome?) -> Void) {
        self.login = login
        self.completion = completion
        super.init(frame: frame)
        autoresizingMask = [.width, .height]
        wantsLayer = true
        layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        build()
    }

    required init?(coder: NSCoder) { nil }

    private func build() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = store
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(source: FilePicker.guardScript(origin: "https://none.invalid", post: "void"),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
        ucc.addUserScript(WKUserScript(source: "document.addEventListener('contextmenu',function(e){e.preventDefault();},true);",
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
        config.userContentController = ucc
        let wv = ExamWebView(frame: .zero, configuration: config)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.allowsBackForwardNavigationGestures = false
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

    func begin() {
        hostLabel.stringValue = HomeworkLogin.displayHost(login.startURL)
        webView?.load(URLRequest(url: login.startURL))
        focusPage()
    }

    func focusPage() {
        guard let webView else { return }
        window?.makeFirstResponder(webView)
        DispatchQueue.main.async { [weak self] in
            guard let self, self.completion != nil, !self.isHidden, let wv = self.webView else { return }
            if self.window?.firstResponder !== wv { self.window?.makeFirstResponder(wv) }
        }
    }

    private var focusedAfterLoad = false

    @objc private func closePressed() { end(.cancelled) }

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
        done?(outcome)
    }

    private func show(note: String) { noteLabel.stringValue = note }

    private func handle(_ nav: HomeworkLogin.Navigation) -> Bool {
        switch nav {
        case .allow:
            return true
        case .block:
            show(note: HomeworkLogin.notPartOfSignIn)
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
        decisionHandler(handle(login.decide(url, mainFrame: main)) ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        let download = AllowedSites.isAttachment(contentDisposition: disposition) || !navigationResponse.canShowMIMEType
        decisionHandler(download ? .cancel : .allow)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        hostLabel.stringValue = HomeworkLogin.displayHost(webView.url)
        noteLabel.stringValue = ""
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        hostLabel.stringValue = HomeworkLogin.displayHost(webView.url)
        if !focusedAfterLoad { focusedAfterLoad = true; focusPage() }
        if let again = login.resume(afterFinishing: webView.url) { webView.load(URLRequest(url: again)) }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if code != NSURLErrorCancelled && code != 102 { show(note: "This page could not load") }
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

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        completionHandler(nil)
    }

    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.deny)
    }
}

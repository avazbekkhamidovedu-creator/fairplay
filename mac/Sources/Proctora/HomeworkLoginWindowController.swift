import AppKit
import WebKit

/// Owns the one homework sign-in window (HomeworkLogin.swift has the rules). MainWindowController
/// only forwards the two bridge actions to `open` / `clear` and calls `dismiss` when an exam starts.
final class HomeworkLoginCoordinator {
    private var window: HomeworkLoginWindowController?

    func open(body: [String: Any], busy: Bool, deviceTestRunning: Bool, send: @escaping ([String: Any]) -> Void) {
        let state = body["state"] as? String
        if let text = HomeworkLogin.refusal(examActive: busy, deviceTestRunning: deviceTestRunning) {
            send(HomeworkLogin.message(state: state, outcome: .failed(text)))
            return
        }
        guard window == nil else {
            window?.bringToFront()
            return
        }
        guard let login = HomeworkLogin(body: body) else {
            send(HomeworkLogin.message(state: state, outcome: .failed(HomeworkLogin.startFailed)))
            return
        }
        let controller = HomeworkLoginWindowController(login: login) { [weak self] outcome in
            self?.window = nil
            if let outcome { send(login.message(for: outcome)) }
        }
        window = controller
        controller.show()
    }

    /// Sign-out: close the window without an answer and wipe its data store.
    func clear() { window?.end(nil); window = nil }

    func dismiss() { clear() }
}

final class HomeworkLoginWindowController: NSWindowController, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {

    private var login: HomeworkLogin
    private var webView: ExamWebView?
    private let store = WKWebsiteDataStore.nonPersistent()
    private let hostLabel = NSTextField(labelWithString: "")
    private let noteLabel = NSTextField(labelWithString: "")
    private var completion: ((HomeworkLogin.Outcome?) -> Void)?

    init(login: HomeworkLogin, completion: @escaping (HomeworkLogin.Outcome?) -> Void) {
        self.login = login
        self.completion = completion
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 720),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        win.title = "Sign in to DKU OJ"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 400, height: 480)
        super.init(window: win)
        win.delegate = self
        build(in: win)
    }

    required init?(coder: NSCoder) { nil }

    private func build(in win: NSWindow) {
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
        let strip = NSStackView(views: [hostLabel, noteLabel])
        strip.orientation = .horizontal
        strip.edgeInsets = NSEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
        strip.wantsLayer = true
        strip.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        let content = NSView()
        for v in [strip, wv] as [NSView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(v)
        }
        NSLayoutConstraint.activate([
            strip.topAnchor.constraint(equalTo: content.topAnchor),
            strip.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            strip.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            wv.topAnchor.constraint(equalTo: strip.bottomAnchor),
            wv.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            wv.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            wv.bottomAnchor.constraint(equalTo: content.bottomAnchor),
        ])
        win.contentView = content
    }

    func show() {
        window?.center()
        showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
        hostLabel.stringValue = HomeworkLogin.displayHost(login.startURL)
        webView?.load(URLRequest(url: login.startURL))
    }

    func bringToFront() {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Close, wipe the data store and answer once. nil = no answer (sign-out, exam start).
    func end(_ outcome: HomeworkLogin.Outcome?) {
        let done = completion
        completion = nil
        window?.delegate = nil
        webView?.navigationDelegate = nil
        webView?.uiDelegate = nil
        webView?.stopLoading()
        store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) {}
        webView?.removeFromSuperview()
        webView = nil
        window?.close()
        done?(outcome)
    }

    func windowWillClose(_ notification: Notification) {
        if completion != nil { end(.cancelled) }
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

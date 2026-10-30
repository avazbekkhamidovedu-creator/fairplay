import UIKit
import WebKit
import PhotosUI
import FairPlayCore

/// While locked the edit menu offers only Select / Select All / Undo: no Copy, Paste,
/// Look Up, Translate, Search Web, Share, Speak, Writing Tools or AutoFill.
final class ExamWebView: WKWebView {
    private static let allowedWhileLocked: Set<String> = ["select:", "selectAll:", "delete:", "undo:", "redo:"]

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        if MainActor.assumeIsolated({ LockdownController.shared.isLocked }),
           !ExamWebView.allowedWhileLocked.contains(NSStringFromSelector(action)) {
            return false
        }
        return super.canPerformAction(action, withSender: sender)
    }

    func disableDragAndDrop() {
        let views: [UIView] = [self, scrollView] + scrollView.subviews
        for view in views {
            for interaction in view.interactions {
                if let drag = interaction as? UIDragInteraction {
                    drag.isEnabled = false
                } else if interaction is UIDropInteraction {
                    view.removeInteraction(interaction)
                }
            }
        }
    }

    override func buildMenu(with builder: UIMenuBuilder) {
        if MainActor.assumeIsolated({ LockdownController.shared.isLocked }) {
            let ids: [UIMenu.Identifier] = [.lookup, .learn, .share, .speech, .replace, .find, .format,
                                            .standardEdit, .autoFill, .transformations, .spelling, .substitutions]
            ids.forEach { builder.remove(menu: $0) }
        }
        super.buildMenu(with: builder)
    }
}

/// WKUserContentController retains its handlers; this breaks the cycle.
final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        target?.userContentController(c, didReceive: m)
    }
}

/// The one screen: student web UI in a WKWebView plus the native bridge (CONTRACT.md
/// "Native bridge"), identical to the Mac app's so the same web pages drive both.
final class ExamViewController: UIViewController, LockSurface,
                                WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {

    private var shield: CaptureShieldView!
    private var webView: ExamWebView!
    private let bar = LockBar()
    private var barHeight: NSLayoutConstraint!
    private var blockingView: UIView?
    private var blockingLabel: UILabel?
    private var blockingButton: UIButton?
    private var blockingAction: (() -> Void)?
    private var privacyCover: UIView?
    private var unlockRetry: Timer?
    private var urlWatch: NSKeyValueObservation?
    /// Set only while WE load an about:/data: page (Mac audit M-18).
    private var ownPageLoadPending = false
    /// Mac audit W-10: per-launch nonce stamped on every message to the page.
    private let bridgeNonce = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    private let server = Config.serverURL
    private lazy var external = ExternalExamController(server: Config.serverURL)
    private var launchURL = Config.startURL
    /// "Connecting to <server>…" over the web view until the first page commits (the web
    /// view alone shows a blank white page while it waits).
    private var connecting: UIView?
    private var connectingDetail: UILabel?
    private var connectingTimer: Timer?
    /// The page the offline screen's "Try again" loads (kept here, never in its HTML).
    private var retryURL: URL?
    /// Page loads from the exam server give up after this long (WebKit's own default is 60 s)
    /// and show the offline screen, which keeps retrying.
    static let loadTimeout: TimeInterval = 20
    /// Links our own offline / connecting pages use to talk to the app (only honoured while
    /// one of our own pages is shown).
    static let appScheme = "fairplay-app"

    var hostWindow: UIWindow? { view.window }
    var captureShieldActive: Bool { shield?.isShielding ?? false }
    /// Origin of the document actually on screen (review L2): webView.url already shows
    /// a pending load, so bridge messages are gated on what has committed.
    private var committedURL: URL?
    /// 2026-10-23: the exam's teacher-allowed websites (FairPlayCore AllowedSites), shown in
    /// the exam page's side pane. Used only while locked; cleared when the lock ends.
    private var allowedSites: [String] = []
    private var batteryObservers: [NSObjectProtocol] = []
    /// The exam id of the "Before you start" page the pre-exam lock was taken on: the one
    /// start page it allows and where it sends a route change it does not allow
    /// (PreLockState.homePath). 2026-10-23 (review): set when the pre-exam lock is asked for
    /// (never by a page shown during it), cleared at unlock and when that lock is cancelled.
    private var lastStartExamId: String?
    /// The last page the pre-exam lock allowed and showed (not the sign-in page): where a
    /// route change goes back to when the exam id is not known. Cleared with it.
    private var lastPreLockPage: URL?

    // MARK: - Setup

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.957, green: 0.961, blue: 0.973, alpha: 1)
        shield = CaptureShieldView(shield: Config.captureShield)
        shield.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(shield)
        NSLayoutConstraint.activate([
            shield.leadingAnchor.constraint(equalTo: view.leadingAnchor), shield.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            shield.topAnchor.constraint(equalTo: view.topAnchor), shield.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        buildWebView()
        bar.onInvigilator = { [weak self] in self?.promptEmergencyExit() }
        external.attach(webView: webView, bar: bar)
        external.resetScripts = { [weak self] in
            guard let self else { return }
            let ucc = self.webView.configuration.userContentController
            ucc.removeAllUserScripts()
            self.addPageScripts(to: ucc)
        }
        external.confirmFinish = { [weak self] done in self?.confirmFinish(done) }
        startBatteryUpdates()
    }

    // MARK: - Battery for the exam top bar (2026-10-23)

    /// On every page load (didFinish) and whenever iPadOS reports a level or state change.
    private func startBatteryUpdates() {
        guard batteryObservers.isEmpty else { return }
        UIDevice.current.isBatteryMonitoringEnabled = true
        let nc = NotificationCenter.default
        for name in [UIDevice.batteryLevelDidChangeNotification, UIDevice.batteryStateDidChangeNotification] {
            batteryObservers.append(nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.pushBattery() }
            })
        }
    }

    /// Only into the exam server's own pages; the script holds number and boolean literals
    /// only. An unknown level (-1, e.g. the simulator) sends nothing.
    private func pushBattery() {
        let device = UIDevice.current
        device.isBatteryMonitoringEnabled = true
        let charging = device.batteryState == .charging || device.batteryState == .full
        guard let webView, isServerURL(committedURL), isServerURL(webView.url),
              let js = BatteryScript.make(level: Double(device.batteryLevel), charging: charging) else { return }
        webView.evaluateJavaScript(js)
    }

    private func addPageScripts(to ucc: WKUserContentController) {
        let guardJS = """
        (function () {
          document.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
          document.addEventListener('dragstart', function (e) { e.preventDefault(); }, true);
          var s = document.createElement('style');
          s.textContent = '*{-webkit-touch-callout:none!important}' +
            'body :not(input):not(textarea):not([contenteditable]):not([contenteditable] *){-webkit-user-select:none}';
          (document.head || document.documentElement).appendChild(s);
        })();
        """
        ucc.addUserScript(WKUserScript(source: guardJS, injectionTime: .atDocumentEnd, forMainFrameOnly: false))
        // 2026-10-24: window.open never opens a window (the exam page gets null as before); in
        // the allowed-site pane, links and window.open that ask for a new tab open in the pane
        // and are checked like any frame navigation (FairPlayCore FileAccess.swift).
        ucc.addUserScript(WKUserScript(source: SiteFrameScript.make(origin: FilePicker.origin(server)),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
        // 2026-10-24: files are picked / dropped only while unlocked, in the exam server's own
        // page (FilePicker). Off by default in every new page; didFinish and the lock turn it on
        // or off (applyFileAccess). present(_:) refuses the pickers on its own too.
        ucc.addUserScript(WKUserScript(source: FilePicker.guardScript(origin: FilePicker.origin(server),
                                                                      post: "window.webkit.messageHandlers.proctora.postMessage"),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: false))
        ucc.addUserScript(WKUserScript(source: ExamPaths.nonceScript(nonce: bridgeNonce, server: server),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: true))
        // 2026-10-23: where the bundled files are (window.fairplayAssets), for the exam
        // server's own pages only, before their scripts. Nothing when this build has no pack.
        if let assetsJS = AssetPack.injectionScript(version: BundledAssets.version, server: server) {
            ucc.addUserScript(WKUserScript(source: assetsJS, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        } else {
            DebugLog.log("assets", "no asset pack in this build: the exam page downloads its files")
        }
    }

    private func buildWebView() {
        let content = shield.content
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()          // localStorage keeps offline answers across a crash
        config.applicationNameForUserAgent = "FairPlay/\(Config.version) FairPlay-iPad"
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.dataDetectorTypes = []
        config.allowsAirPlayForMediaPlayback = false
        config.allowsPictureInPictureMediaPlayback = false
        config.allowsInlineMediaPlayback = true
        // No Apple Intelligence rewrite / proofread in answers (KVC like the Mac app: some
        // SDKs don't expose it to Swift).
        if #available(iOS 18.0, *) {
            let sel = NSSelectorFromString("setWritingToolsBehavior:")
            if config.responds(to: sel) { config.setValue(UIWritingToolsBehavior.none.rawValue, forKey: "writingToolsBehavior") }
        }

        // 2026-10-23: fairplay-asset://bundle/... serves the asset pack inside the app (the
        // Python engine, pdf.js): subresources, workers and fetches only; decidePolicyFor
        // refuses navigations to it.
        config.setURLSchemeHandler(AssetSchemeHandler(), forURLScheme: AssetPack.scheme)   // config keeps it

        let ucc = WKUserContentController()
        ucc.add(WeakScriptHandler(self), name: "proctora")
        addPageScripts(to: ucc)
        config.userContentController = ucc

        let wv = ExamWebView(frame: .zero, configuration: config)
        // Same light grey as the pages, instead of white while nothing has loaded yet.
        wv.isOpaque = false
        wv.backgroundColor = view.backgroundColor
        wv.scrollView.backgroundColor = view.backgroundColor
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.allowsBackForwardNavigationGestures = false
        wv.allowsLinkPreview = false
        wv.isFindInteractionEnabled = false
        wv.isInspectable = Config.isDevBuild          // release: no Safari Web Inspector
        wv.translatesAutoresizingMaskIntoConstraints = false
        bar.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(bar)
        content.addSubview(wv)
        barHeight = bar.heightAnchor.constraint(equalToConstant: 0)
        NSLayoutConstraint.activate([
            bar.leadingAnchor.constraint(equalTo: content.leadingAnchor), bar.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            bar.topAnchor.constraint(equalTo: content.safeAreaLayoutGuide.topAnchor), barHeight,
            wv.leadingAnchor.constraint(equalTo: content.leadingAnchor), wv.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            wv.topAnchor.constraint(equalTo: bar.bottomAnchor), wv.bottomAnchor.constraint(equalTo: content.bottomAnchor),
        ])
        bar.isHidden = true
        bar.clipsToBounds = true
        webView = wv
        // In-page (pushState) route changes never reach decidePolicyFor: watch the URL too.
        urlWatch = wv.observe(\.url, options: [.new]) { [weak self] view, _ in
            DispatchQueue.main.async {
                guard let self, let u = view.url else { return }
                guard self.isServerURL(u) else { self.deviceTest.end(.leave); return }
                if ExamPaths.isTeacherPath(u.path) {
                    EventLog.shared.record("blocked_navigation", "teacher page \(u.path)")
                    view.load(Self.serverRequest(Config.startURL))
                    return
                }
                self.noteRoute(u)        // 2026-10-24: FairPlay sign-out clears the other sites' sign-ins
                let lock = LockdownController.shared
                let aid = AppSession.shared.exam?.attemptId
                guard lock.isLocked else { return }
                if ExamPaths.allowedWhileLocked(path: u.path, attemptId: aid, preLock: lock.isPreLock,
                                                examId: self.lastStartExamId) {
                    if lock.isPreLock, aid == nil, let page = Self.preLockFallback(u, examId: self.lastStartExamId) {
                        self.lastPreLockPage = page
                    }
                    return
                }
                EventLog.shared.record("blocked_navigation", "left the exam: \(u.path)")
                if let aid {
                    view.load(Self.serverRequest(Config.serverURL.appendingPathComponent("student/exam/\(aid)")))
                } else if lock.isPreLock {
                    // 2026-10-23: the pre-exam lock has no attempt yet: back to the start page
                    // it was taken on, else (review) the last page it allowed. Never the
                    // student home: that is not allowed while pre-locked.
                    let back = PreLockState.homePath(examId: self.lastStartExamId)
                        .map { Config.serverURL.appendingPathComponent($0) } ?? self.lastPreLockPage
                    DebugLog.log("web", "pre-exam lock: in-page route \(u.path) sent back to \(back?.path ?? "(nowhere)")")
                    if let back { view.load(Self.serverRequest(back)) }
                }
            }
        }
    }

    func start(resumeAttempt: String?, resumePage: URL? = nil) {
        guard Config.serverIsAcceptable else {
            loadOwnHTML(Self.page(title: "This copy of FairPlay is not set up",
                                  body: "It has no secure (https) exam server. Ask your school for the correct FairPlay app."))
            return
        }
        if let page = resumePage {
            launchURL = page
        } else if let aid = resumeAttempt {
            DebugLog.log("app", "last run ended while locked: resuming \(aid)")
            launchURL = server.appendingPathComponent("student/exam/\(aid)")
        }
        // 2026-10-24: allowed-site sign-ins are kept for the day (SiteData). Not when this run
        // resumes an exam that was locked: that exam keeps them.
        let endedLocked = resumeAttempt != nil || resumePage != nil
        if SiteData.shouldClearAtLaunch(lastDay: UserDefaults.standard.string(forKey: SiteData.dayKey),
                                        today: SiteData.day(Date()), endedLocked: endedLocked) {
            clearOtherSitesData(reason: "new day")
        }
        loadFromServer(launchURL)
    }

    // MARK: - Files and allowed-site sign-ins (2026-10-24, FairPlayCore FileAccess.swift)

    /// A signed-in student page was shown since the last sign-out (SiteData.isSignOut).
    private var sawSignedInPage = false

    /// Files on in the exam page while unlocked, off while locked (pre-exam lock included).
    private func applyFileAccess() {
        guard let webView, isServerURL(webView.url) else { return }
        webView.evaluateJavaScript(FilePicker.setScript(allowed: !LockdownController.shared.isLocked))
    }

    private func noteRoute(_ url: URL) {
        let locked = LockdownController.shared.isLocked
        if SiteData.isSignedInPath(url.path) {
            sawSignedInPage = true
        } else if SiteData.isSignOut(sawSignedInPage: sawSignedInPage, path: url.path, locked: locked) {
            sawSignedInPage = false
            clearOtherSitesData(reason: "FairPlay sign-out")
        }
    }

    /// Removes every site's web data (cookies, storage, cache) except the exam server's
    /// (SiteData.keeps): the allowed-site sign-ins go, the offline answers stay.
    private func clearOtherSitesData(reason: String) {
        UserDefaults.standard.set(SiteData.day(Date()), forKey: SiteData.dayKey)
        let store = WKWebsiteDataStore.default()
        let serverHost = (server.host ?? "").lowercased()
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        store.fetchDataRecords(ofTypes: types) { records in
            let others = records.filter { !SiteData.keeps(domain: $0.displayName, serverHost: serverHost) }
            guard !others.isEmpty else { return }
            store.removeData(ofTypes: types, for: others) {
                DebugLog.log("web", "other sites' data cleared (\(reason)): \(others.count) site(s)")
            }
        }
    }

    /// 2026-10-24: WebKit shows the photo / file / camera pickers of an <input type=file> from
    /// this view controller. While locked (pre-exam lock included) they never appear: the
    /// picker is cancelled through its own delegate (WebKit then ends the file choice with no
    /// file) and the page hears {type:"fileRefused"}. The page guard normally refuses earlier.
    override func present(_ viewControllerToPresent: UIViewController, animated flag: Bool,
                          completion: (() -> Void)? = nil) {
        if external.isCanvas, viewControllerToPresent is UIActivityViewController {
            DebugLog.log("web", "share sheet REFUSED during a canvas exam")
            return
        }
        if LockdownController.shared.isLocked, Self.cancelIfFilePicker(viewControllerToPresent) {
            DebugLog.log("web", "file picker REFUSED while locked: \(type(of: viewControllerToPresent))")
            external.noteUploadRefused()
            send(FilePicker.refusedMessage)
            return
        }
        super.present(viewControllerToPresent, animated: flag, completion: completion)
    }

    /// True (and the picker told it was cancelled) for the pickers WebKit uses for files.
    private static func cancelIfFilePicker(_ vc: UIViewController) -> Bool {
        if let p = vc as? UIDocumentPickerViewController {
            p.delegate?.documentPickerWasCancelled?(p)
            return true
        }
        if let p = vc as? UIImagePickerController {
            p.delegate?.imagePickerControllerDidCancel?(p)
            return true
        }
        if let p = vc as? PHPickerViewController {
            p.delegate?.picker(p, didFinishPicking: [])
            return true
        }
        return false
    }

    func reload() {
        guard Config.serverIsAcceptable else { return }
        if external.isCanvas, let start = external.startURL {
            webView.load(URLRequest(url: start))
            return
        }
        if webView.url == nil || webView.url?.scheme == "about" {
            // 2026-10-24: while locked the sign-in page is refused (decidePolicyFor) and the
            // page stayed blank: back to the last page the lock showed instead.
            let locked = LockdownController.shared.isLocked
            loadFromServer(locked ? (ExamViewController.lastLockedPage ?? Config.startURL) : Config.startURL)
        } else {
            webView.reload()
        }
    }

    /// 2026-10-23 (review): `url` when the pre-exam lock allows it and it is not the sign-in
    /// page: a page a route change may go back to.
    static func preLockFallback(_ url: URL?, examId: String?) -> URL? {
        guard let url, !url.path.lowercased().hasPrefix("/student/login"),
              PreLockState.allowedWhilePreLocked(path: url.path, examId: examId) else { return nil }
        return url
    }

    static func serverRequest(_ url: URL) -> URLRequest {
        URLRequest(url: url, cachePolicy: .useProtocolCachePolicy, timeoutInterval: loadTimeout)
    }

    private func loadFromServer(_ url: URL) {
        showConnecting()
        webView.load(Self.serverRequest(url))
    }

    // MARK: - Connecting screen

    private func showConnecting() {
        connectingTimer?.invalidate()
        connectingDetail?.text = nil
        let t = Timer(timeInterval: 8, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.connectingDetail?.text = "Still connecting… A test server that was asleep can take up to a minute to wake up."
            }
        }
        RunLoop.main.add(t, forMode: .common)
        connectingTimer = t
        guard connecting == nil else { return }
        let v = UIView()
        v.backgroundColor = view.backgroundColor
        v.translatesAutoresizingMaskIntoConstraints = false
        v.accessibilityIdentifier = "connectingScreen"
        let spinner = UIActivityIndicatorView(style: .large)
        spinner.startAnimating()
        let title = UILabel()
        title.text = "Connecting to \(Config.serverLabel)…"
        title.font = .systemFont(ofSize: 20, weight: .semibold)
        title.textColor = UIColor(red: 0.067, green: 0.094, blue: 0.153, alpha: 1)
        title.textAlignment = .center
        title.numberOfLines = 0
        let detail = UILabel()
        detail.font = .systemFont(ofSize: 15)
        detail.textColor = UIColor(red: 0.29, green: 0.33, blue: 0.39, alpha: 1)
        detail.textAlignment = .center
        detail.numberOfLines = 0
        var rows: [UIView] = [spinner, title, detail]
        if Config.isDevBuild {
            let b = UIButton(type: .system)
            b.setTitle("Change server (dev build)", for: .normal)
            b.addTarget(self, action: #selector(changeServerTapped), for: .touchUpInside)
            rows.append(b)
        }
        let stack = UIStackView(arrangedSubviews: rows)
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        shield.content.insertSubview(v, aboveSubview: webView)
        shield.content.bringSubviewToFront(bar)
        if let blockingView { shield.content.bringSubviewToFront(blockingView) }
        if let privacyCover { shield.content.bringSubviewToFront(privacyCover) }
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: webView.leadingAnchor), v.trailingAnchor.constraint(equalTo: webView.trailingAnchor),
            v.topAnchor.constraint(equalTo: webView.topAnchor), v.bottomAnchor.constraint(equalTo: webView.bottomAnchor),
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor), stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: v.widthAnchor, constant: -80),
        ])
        connecting = v
        connectingDetail = detail
    }

    private func hideConnecting() {
        connectingTimer?.invalidate(); connectingTimer = nil
        connecting?.removeFromSuperview()
        connecting = nil; connectingDetail = nil
    }

    // MARK: - Dev builds: change the server without Xcode

    @objc private func changeServerTapped() { promptServerChange() }

    private func promptServerChange() {
        guard Config.isDevBuild, !LockdownController.shared.isLocked, presentedViewController == nil else { return }
        let a = UIAlertController(title: "Exam server (dev build)",
                                  message: "Now: \(Config.serverURL.absoluteString)\n\nExample: http://192.168.1.20:8000 (the Mac running "
                                    + "\"Run Server for iPad.command\"). Takes effect when FairPlay is opened again.",
                                  preferredStyle: .alert)
        a.addTextField { f in
            f.text = Config.serverURL.absoluteString
            f.keyboardType = .URL
            f.autocapitalizationType = .none
            f.autocorrectionType = .no
            f.clearButtonMode = .whileEditing
        }
        a.addAction(UIAlertAction(title: "Save", style: .default) { [weak self, weak a] _ in
            let text = a?.textFields?.first?.text ?? ""
            guard Config.parseServer(text) != nil else {
                DispatchQueue.main.async { self?.message("Not a server address", "Use http://<address>:<port> or https://<name>.") }
                return
            }
            DevServer.save(text)
            DispatchQueue.main.async { self?.message("Saved", "Close FairPlay (swipe it away in the app switcher) and open it again.") }
        })
        a.addAction(UIAlertAction(title: "Forget it (use \(BuildConfig.serverURL))", style: .destructive) { [weak self] _ in
            DevServer.save(nil)
            DispatchQueue.main.async { self?.message("Forgotten", "Close FairPlay and open it again.") }
        })
        a.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        present(a, animated: true)
    }

    private func message(_ title: String, _ text: String) {
        guard presentedViewController == nil else { return }
        let a = UIAlertController(title: title, message: text, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "OK", style: .default))
        present(a, animated: true)
    }

    // MARK: - System chrome while locked

    override var prefersStatusBarHidden: Bool { LockdownController.shared.isLocked || deviceTestImmersive }
    override var prefersHomeIndicatorAutoHidden: Bool { LockdownController.shared.isLocked || deviceTestImmersive }
    override var preferredScreenEdgesDeferringSystemGestures: UIRectEdge { LockdownController.shared.isLocked ? .all : [] }

    override var keyCommands: [UIKeyCommand]? {
        let k = UIKeyCommand(input: "e", modifierFlags: [.control, .alternate, .command], action: #selector(invigilatorKey))
        k.wantsPriorityOverSystemBehavior = true
        return [k]
    }

    @objc private func invigilatorKey() { promptEmergencyExit() }

    // MARK: - Device test (preview of the exam screen: no lock, no network, no attempt)

    private var deviceTestImmersive = false
    private let homework = HomeworkLoginCoordinator()
    private let box = BoxCoordinator()
    private var deviceTestTimer: Timer?
    private var deviceTestObservers: [NSObjectProtocol] = []

    private let remember = HomeworkRemember.system()
    private lazy var deviceTest = DeviceTest(
        begin: { [weak self] in self?.deviceTestBegan() },
        restore: { [weak self] in self?.deviceTestRestored() },
        emit: { [weak self] m in
            switch m {
            case .started(let remaining):
                self?.send(["type": "deviceTest", "state": "started", "remaining": remaining])
            case .ended(let reason, let remaining):
                self?.send(["type": "deviceTest", "state": "ended", "reason": reason.rawValue, "remaining": remaining])
            }
        })

    private func deviceTestBegan() {
        deviceTestImmersive = true
        UIApplication.shared.isIdleTimerDisabled = true
        refreshSystemChrome()
        deviceTestTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.deviceTest.tick() }
        }
        let nc = NotificationCenter.default
        let hooks: [(NSNotification.Name, () -> Void)] = [
            (UIApplication.willResignActiveNotification, { [weak self] in self?.deviceTest.appBackgrounded() }),
            (UIApplication.didBecomeActiveNotification, { [weak self] in self?.deviceTest.appForegrounded() }),
            (UIApplication.willTerminateNotification, { [weak self] in self?.deviceTest.end(.leave) }),
        ]
        deviceTestObservers = hooks.map { name, action in
            nc.addObserver(forName: name, object: nil, queue: .main) { _ in MainActor.assumeIsolated { action() } }
        }
    }

    private func deviceTestRestored() {
        deviceTestTimer?.invalidate(); deviceTestTimer = nil
        deviceTestObservers.forEach(NotificationCenter.default.removeObserver)
        deviceTestObservers = []
        deviceTestImmersive = false
        UIApplication.shared.isIdleTimerDisabled = LockdownController.shared.isLocked
        refreshSystemChrome()
    }

    // MARK: - Bridge (JS <-> native)

    private func isServerURL(_ url: URL?) -> Bool { ExamPaths.isServerURL(url, server: server) }

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        // Only the exam server's own pages (same scheme, host, port; main frame) drive the lock.
        let origin = message.frameInfo.securityOrigin
        func defaultPort(_ s: String?) -> Int { s?.lowercased() == "https" ? 443 : 80 }
        let originPort = origin.port == 0 ? defaultPort(origin.protocol) : origin.port
        let fromServer = message.frameInfo.isMainFrame
            && origin.protocol.lowercased() == server.scheme?.lowercased()
            && origin.host.lowercased() == server.host?.lowercased()
            && originPort == (server.port ?? defaultPort(server.scheme))
        guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        if action == "deviceTestStart" {
            homework.dismiss()
            let lock = LockdownController.shared
            deviceTest.start(seconds: body["seconds"], context: DeviceTestContext(
                fromServerPage: fromServer,
                examActive: lock.isLocked || lock.lockInFlight || lock.preLockInFlight || AppSession.shared.exam != nil,
                websiteMode: external.isActive))
            return
        }
        guard fromServer else { return }
        if action == "log" { return }
        DebugLog.log("bridge", "page -> app: \(action)")
        if ["lock", "session", "prelock", "openExternal"].contains(action) { deviceTest.end(.leave) }
        if HomeworkPanelPolicy.closes(on: action) { homework.dismiss() }
        if ["prelock", "openExternal", "seal"].contains(action) { box.close() }
        if action == "session", (body["attempt_id"] as? String) != AppSession.shared.exam?.attemptId { box.close() }

        switch action {
        case "deviceTestLeave":
            deviceTest.end(.leave)
        case "openHomeworkLogin":
            let lock = LockdownController.shared
            homework.open(body: body, busy: lock.isLocked || lock.lockInFlight || lock.preLockInFlight
                                            || AppSession.shared.exam != nil || external.isActive,
                          deviceTestRunning: deviceTest.isRunning, container: shield.content, above: webView,
                          top: bar.bottomAnchor, raise: { self.raiseCovers() }) { [weak self] in self?.send($0) }
        case "clearHomeworkLogin":
            homework.clear()
        case "open_box":
            let lock = LockdownController.shared
            box.open(body: body, locked: lock.isLocked, preLock: lock.isPreLock || lock.preLockInFlight,
                     hasAttempt: AppSession.shared.exam?.attemptId != nil, container: shield.content, above: webView,
                     top: bar.bottomAnchor, raise: { [weak self] in self?.raiseCovers() }) { [weak self] in self?.send($0) }
        case "close_box":
            box.close { [weak self] in self?.send($0) }
        case "rememberSave", "rememberUnlock", "rememberForget":
            let lock = LockdownController.shared
            remember.handle(action: action, body: body,
                            busy: lock.isLocked || lock.lockInFlight || lock.preLockInFlight
                                || AppSession.shared.exam != nil || self.external.isActive || self.deviceTest.isRunning) { [weak self] in self?.send($0) }
            if action != "rememberUnlock" { sendState() }
        case "hello":
            sendState()
        case "flushEvents":
            flushEventQueue()
        case "fileRefused":
            // 2026-10-24: the page guard refused a file (FilePicker.guardScript): tell the page.
            if LockdownController.shared.isLocked { send(FilePicker.refusedMessage) }
        case "attest":
            send(AppSession.shared.attestation(nonce: (body["nonce"] as? String) ?? ""))
        case "session":
            let promote = PreLockState.onExamLock(locked: LockdownController.shared.isLocked,
                                                  preLocked: LockdownController.shared.isPreLock) == .promote
            if AppSession.shared.setExam(attemptId: body["attempt_id"] as? String, token: body["token"] as? String,
                                         session: body["session"] as? String) {
                allowedSites = AllowedSites.updated(allowedSites, from: body)
                if promote {
                    // 2026-10-23: the exam is attached to the pre-exam lock: it is the exam's lock now.
                    LockdownController.shared.promoteToExamLock(participants: ExamPaths.allowedParticipants(body["allowed_apps"]))
                }
            }
        case "prelock":
            // 2026-10-23: lock before the start code (FairPlayCore PreLock.swift): the same
            // lock as an exam's (Assessment Mode, "lockPending" while iPadOS asks), no session.
            let lock = LockdownController.shared
            switch PreLockState.onPreLock(locked: lock.isLocked, preLocked: lock.isPreLock,
                                          hasExam: AppSession.shared.exam != nil) {
            case .examLocked:
                send(["type": "locked", "prelock": false])
            case .alreadyPreLocked:
                send(["type": "locked", "prelock": true])
            case .lock:
                allowedSites = AllowedSites.updated(allowedSites, from: body)
                // 2026-10-23 (review): the exam of the page that asked (nil when that is not a
                // start page: then any exam's start page is allowed). A prelock joining one
                // already coming up keeps what that one set.
                if !lock.preLockInFlight {
                    lastStartExamId = committedURL.flatMap { PreLockState.startPageExamId(path: $0.path) }
                    lastPreLockPage = ExamViewController.preLockFallback(committedURL, examId: lastStartExamId)
                }
                lock.preLock(participants: ExamPaths.allowedParticipants(body["allowed_apps"])) { [weak self] r in
                    switch r {
                    // false when an exam lock asked meanwhile and this became its lock.
                    case .locked: self?.send(["type": "locked", "prelock": LockdownController.shared.isPreLock])
                    case .failed(let reason): self?.send(["type": "lockFailed", "reason": reason])
                    }
                }
            }
        case "preunlock":
            // 2026-10-23: "Not ready? Leave and unlock": only the pre-exam lock with no exam
            // attached, and then with no server check (there is no attempt).
            // Review 2026-10-23: never "unlocked" while a lock is still coming up (the
            // Assessment Mode prompt is up): a pre-exam lock is cancelled first, an exam's is
            // refused.
            let lock = LockdownController.shared
            switch PreLockState.onPreUnlock(locked: lock.isLocked, preLocked: lock.isPreLock,
                                            hasExam: AppSession.shared.exam != nil,
                                            lockInFlight: lock.lockInFlight, preLockInFlight: lock.preLockInFlight) {
            case .unlock:
                lock.endPreLock()            // onUnlocked sends "unlocked"
                sendState()
            case .notLocked:
                send(["type": "unlocked"])
                sendState()
            case .cancelPending:
                // Its waiters (the "prelock" above) answer lockFailed "cancelled" first.
                if lock.cancelPendingPreLock() {
                    lastStartExamId = nil
                    lastPreLockPage = nil
                    send(["type": "unlocked"])
                    sendState()
                } else {
                    DebugLog.log("lock", "preunlock refused: the lock coming up could not be cancelled")
                    send(["type": "preunlockRefused"])
                }
            case .refuse:
                DebugLog.log("lock", "preunlock refused: this is an exam lock (or one is coming up)")
                send(["type": "preunlockRefused"])
            }
        case "lock":
            let offlineStart = (body["offline"] as? Bool) == true && body["attempt_id"] == nil && body["session"] == nil
            // Review L1: an offline start has no server session yet; drop any older one so
            // its beats and its "submitted" status can't speak for this lock.
            if offlineStart && !LockdownController.shared.isLocked { AppSession.shared.clearExam() }
            if !offlineStart && !AppSession.shared.setExam(attemptId: body["attempt_id"] as? String,
                                                          token: body["token"] as? String,
                                                          session: body["session"] as? String) {
                send(["type": "lockFailed", "reason": "bad_session"])
                return
            }
            allowedSites = AllowedSites.updated(allowedSites, from: body)
            LockdownController.shared.lock(participants: ExamPaths.allowedParticipants(body["allowed_apps"])) { [weak self] r in
                switch r {
                case .locked: self?.send(["type": "locked"])
                case .failed(let reason): self?.send(["type": "lockFailed", "reason": reason])
                }
            }
        case "openExternal":
            guard AppSession.shared.setExam(attemptId: body["attempt_id"] as? String, token: body["token"] as? String,
                                            session: body["session"] as? String) else {
                send(["type": "externalFailed", "reason": "bad_session"])
                return
            }
            allowedSites = AllowedSites.updated(allowedSites, from: body)
            LockdownController.shared.lock(participants: ExamPaths.allowedParticipants(body["allowed_apps"])) { [weak self] r in
                guard let self else { return }
                guard r == .locked else {
                    if case .failed(let reason) = r { self.send(["type": "lockFailed", "reason": reason]) }
                    return
                }
                self.send(["type": "locked"])
                if let problem = self.external.start(body: body) { self.send(["type": "externalFailed", "reason": problem]) }
            }
        case "preflight":
            // Audit M-11 + S1: problems to fix before locking, and the device-check rows.
            LockdownController.shared.preflight { [weak self] problems, rows in
                // apps_to_close (2026-10-22, Mac / Windows): iPadOS lets no app list or close
                // other apps, and Assessment Mode closes them itself: always [] here.
                self?.send(["type": "preflight", "problems": problems, "checks": rows.map(\.dict), "platform": "ipad",
                            "app_version": Config.version, "os_version": UIDevice.current.systemVersion,
                            "apps_to_close": [String]()])
            }
        case "quitApps":
            send(["type": "appsQuit", "still_running": [String]()])
        case "seal":
            let aid = (body["attempt_id"] as? String) ?? ""
            let seq = (body["client_seq"] as? NSNumber)?.int64Value ?? -1
            if let e = AppSession.shared.seal(attemptId: aid, answersJSON: (body["answers_json"] as? String) ?? "", clientSeq: seq) {
                send(["type": "sealed", "attempt_id": aid, "ok": false, "reason": e.rawValue])
            } else {
                send(["type": "sealed", "attempt_id": aid, "ok": true])
            }
        case "sealedToken":
            // S5: a fresh token from a signed-in page for sealed files from an earlier run.
            if let token = body["token"] as? String, !token.isEmpty, token.count < 4000, Sealed.hasPending {
                Sealed.uploadAll(token: token)
            }
        case "unlock":
            requestUnlock()
        default:
            break
        }
    }

    /// The page alone can't unlock: the server must confirm the hand-in (signed).
    private func requestUnlock() {
        guard LockdownController.shared.isLocked else { send(["type": "unlocked"]); return }
        AppSession.shared.confirmHandIn { [weak self] result in
            guard let self, LockdownController.shared.isLocked else { return }
            switch result {
            case .confirmed:
                self.endLock()
            case .notYet, .unreachable:
                self.send(["type": "unlockPending", "reason": result == .unreachable ? "offline" : "not_submitted"])
                if self.unlockRetry == nil {
                    let t = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
                        MainActor.assumeIsolated { self?.requestUnlockOnce() }
                    }
                    RunLoop.main.add(t, forMode: .common)
                    self.unlockRetry = t
                }
            }
        }
    }

    private func requestUnlockOnce() {
        guard LockdownController.shared.isLocked else { unlockRetry?.invalidate(); unlockRetry = nil; return }
        AppSession.shared.confirmHandIn { [weak self] result in
            guard let self, result == .confirmed, LockdownController.shared.isLocked else { return }
            self.endLock()
        }
    }

    /// Unlock, then drop the exam session (its last beat, locked=0, still needs it).
    private func endLock() {
        unlockRetry?.invalidate(); unlockRetry = nil
        LockdownController.shared.unlock()       // onUnlocked sends "unlocked"
        AppSession.shared.clearExam()
    }

    /// Server status from the app's own beats: a teacher close also unlocks.
    func handleServerStatus(_ status: String) {
        if status == "submitted", LockdownController.shared.isLocked, !external.isActive { requestUnlockOnce() }
        if status == GoneCheck.sessionGone, LockdownController.shared.isLocked, goneCheck.shouldCheck() {
            requestUnlockOnce()
        }
    }
    private var goneCheck = GoneCheck()

    func closeBox() { box.close() }

    func sendState() {
        send(["type": "state", "locked": LockdownController.shared.isLocked, "trusted": true,
              "version": Config.version, "app_version": Config.version, "platform": "ipad",
              "sealed_pending": Sealed.hasPending, "prelock": LockdownController.shared.isPreLock,
              "deviceTest": true, "homeworkLogin": true, "boxSupported": true, "homeworkRemember": remember.stateValue])
    }

    private var eventsReady = false
    private var eventQueue: [[String: Any]] = []

    func sendEvent(type: String, detail: String, time: String) {
        if external.isActive { external.queueEvent(type: type, detail: detail, time: time); return }
        let event: [String: Any] = ["type": type, "detail": detail, "client_time": time]
        if eventsReady, isServerURL(committedURL) {
            send(["type": "event", "event": event])
        } else {
            eventQueue.append(event)
            if eventQueue.count > 300 { eventQueue.removeFirst(eventQueue.count - 300) }
        }
    }

    private func flushEventQueue() {
        eventsReady = true
        let queued = eventQueue
        eventQueue.removeAll()
        for e in queued { send(["type": "event", "event": e]) }
    }

    func send(_ detail: [String: Any]) {
        guard let webView, isServerURL(committedURL), isServerURL(webView.url),
              let data = try? JSONSerialization.data(withJSONObject: detail.merging(["bridge_nonce": bridgeNonce]) { _, b in b }),
              let json = String(data: data, encoding: .utf8) else { return }
        if let t = detail["type"] as? String, t != "event" { DebugLog.log("bridge", "app -> page: \(t)") }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('proctora-native', { detail: \(json) }));")
    }

    // MARK: - Navigation: only the exam server (+ the website exam's sites)

    /// Review L10: "allow, without trying app links" (WebKit's value 3, the documented
    /// .allow + 2), so a universal link (e.g. a Google Docs URL) opens here and is never
    /// handed to another app. Falls back to .allow if WebKit ever drops the value.
    static let allowInPlace = WKNavigationActionPolicy(rawValue: WKNavigationActionPolicy.allow.rawValue + 2) ?? .allow

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        let mainFrame = navigationAction.targetFrame?.isMainFrame ?? true
        // 2026-10-23: the bundled files are never a page or a frame (only subresources).
        if url.scheme?.lowercased() == AssetPack.scheme {
            DebugLog.log("web", "navigation BLOCKED (asset file as a page): \(url.path)")
            decisionHandler(.cancel)
            return
        }
        if url.scheme?.lowercased() == Self.appScheme {
            decisionHandler(.cancel)
            // Only from our own offline page (about:blank), never from a server or exam site.
            guard mainFrame, committedURL == nil || committedURL?.scheme == "about" else { return }
            switch url.host?.lowercased() {
            case "retry":
                if let u = retryURL { loadFromServer(u) }
            case "autoretry":
                // Every 3 s from the offline page: quietly, the offline text stays up.
                if let u = retryURL { webView.load(Self.serverRequest(u)) }
            case "server":
                promptServerChange()
            default:
                break
            }
            return
        }
        if isServerURL(url) && ExamPaths.isTeacherPath(url.path) {
            EventLog.shared.record("blocked_navigation", "teacher page \(url.path)")
            decisionHandler(.cancel)
            return
        }
        let isLocal = url.scheme == "about" || url.scheme == "data"
        let ownLocalPage = isLocal && ownPageLoadPending && navigationAction.navigationType == .other && mainFrame
        if ownLocalPage { ownPageLoadPending = false }
        if isServerURL(url), mainFrame, LockdownController.shared.isLocked,
           !ExamPaths.allowedWhileLocked(path: url.path, attemptId: AppSession.shared.exam?.attemptId,
                                         preLock: LockdownController.shared.isPreLock, examId: lastStartExamId) {
            EventLog.shared.record("blocked_navigation", "left the exam: \(url.path)")
            decisionHandler(.cancel)
            return
        }
        // 2026-10-23 (review): while locked for an exam on the exam server (pre-exam lock
        // included, not a website exam) a sub-frame never loads an exam-server page: only
        // about:blank / about:srcdoc and the allowed sites below (as on Windows).
        if !mainFrame, LockdownController.shared.isLocked, !external.isActive, isServerURL(url) {
            EventLog.shared.record("blocked_navigation", "frame \(url.path)")
            DebugLog.log("web", "frame BLOCKED while locked (exam server page): \(url.path)")
            decisionHandler(.cancel)
            return
        }
        // 2026-10-23: the exam page's side pane of teacher-allowed websites (FairPlayCore
        // AllowedSites): sub-frames only, only while locked, https on a listed site (or
        // about:blank / about:srcdoc). No downloads from there.
        if !mainFrame, LockdownController.shared.isLocked, !isServerURL(url), !external.allows(url),
           AllowedSites.frameAllowed(url, allowed: allowedSites) {
            if navigationAction.shouldPerformDownload {
                EventLog.shared.record("blocked_navigation", "download from \(url.host ?? "an allowed site")")
                decisionHandler(.cancel)
            } else {
                decisionHandler(ExamViewController.allowInPlace)
            }
            return
        }
        if external.isCanvas {
            if isServerURL(url) {
                EventLog.shared.record("blocked_navigation", "exam server page during a canvas exam")
                decisionHandler(.cancel)
                return
            }
            if mainFrame, navigationAction.shouldPerformDownload {
                external.reportDownloadBlocked(host: url.host)
                decisionHandler(.cancel)
                return
            }
            if !mainFrame, external.allows(url), external.blocksExamHostFrame(url) {
                DebugLog.log("web", "frame BLOCKED (canvas scope): \(url.host ?? "")\(url.path)")
                decisionHandler(.cancel)
                return
            }
            if mainFrame, external.allows(url), external.blocksMainFrame(url) {
                DebugLog.log("web", "navigation BLOCKED (canvas scope): \(url.host ?? "")\(url.path)")
                external.reportBlocked(url)
                decisionHandler(.cancel)
                return
            }
        }
        if ownLocalPage || isServerURL(url) || external.allows(url) || (isLocal && !mainFrame && url.absoluteString == "about:blank") {
            // Review 2026-10-24 (as on the Mac): eventsReady is reset in didCommit. A
            // navigation that never commits (a cancelled download, a 204, a fragment
            // link) left the page on screen without events.
            if mainFrame {
                if !external.isCanvas { external.checkFinishURL(url) }
            } else if external.isCanvas {
                external.checkFrameFinish(url)
            }
            decisionHandler(ExamViewController.allowInPlace)
        } else {
            EventLog.shared.record("blocked_navigation", url.host ?? url.scheme ?? "?")
            DebugLog.log("web", "navigation BLOCKED: \(url.scheme ?? "")://\(url.host ?? "")\(url.path)")
            decisionHandler(.cancel)
        }
    }

    /// 2026-10-23: nothing downloads from a sub-frame while locked (the allowed-site pane):
    /// a response WebKit cannot show, or one sent as an attachment, is cancelled. Everything
    /// else as WebKit's own default (show what it can show, cancel the rest).
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        if external.isCanvas, navigationResponse.isForMainFrame, let u = navigationResponse.response.url, !isServerURL(u) {
            if !external.childAllows(u) {
                if external.allows(u) {
                    external.reportBlocked(u)
                } else {
                    EventLog.shared.record("blocked_navigation", u.host ?? u.absoluteString)
                }
                decisionHandler(.cancel)
                return
            }
            let disposition = (navigationResponse.response as? HTTPURLResponse)?
                .value(forHTTPHeaderField: "Content-Disposition")
            if AllowedSites.isAttachment(contentDisposition: disposition) {
                external.reportDownloadBlocked(host: u.host)
                decisionHandler(.cancel)
                return
            }
        }
        if !navigationResponse.isForMainFrame, LockdownController.shared.isLocked {
            let disposition = (navigationResponse.response as? HTTPURLResponse)?
                .value(forHTTPHeaderField: "Content-Disposition")
            if !navigationResponse.canShowMIMEType || AllowedSites.isAttachment(contentDisposition: disposition) {
                let host = navigationResponse.response.url?.host ?? "?"
                EventLog.shared.record("blocked_navigation", "download from \(host)")
                DebugLog.log("web", "download BLOCKED in a frame: \(host)")
                decisionHandler(.cancel)
                return
            }
        }
        if external.isCanvas, !navigationResponse.isForMainFrame, navigationResponse.canShowMIMEType,
           let u = navigationResponse.response.url {
            external.checkFrameFinish(u)
        }
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping @MainActor (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        Pinning.handle(challenge) { d, c in DispatchQueue.main.async { completionHandler(d, c) } }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if external.isCanvas {
            let url = navigationAction.request.url
            switch external.popupPlan(url: url) {
            case .sameView:
                if let url {
                    if CanvasPopup.isCrossHost(url, openerHost: webView.url?.host) {
                        EventLog.shared.record("canvas_sso_popup", url.host ?? "?")
                    }
                    webView.load(URLRequest(url: url))
                }
            case .block:
                EventLog.shared.record("blocked_navigation", "popup \(url?.host ?? url?.absoluteString ?? "?")")
                external.showNotice()
            }
            return nil
        }
        if let url = navigationAction.request.url, isServerURL(url) || external.allows(url) {
            webView.load(URLRequest(url: url))
        } else if let url = navigationAction.request.url, LockdownController.shared.isLocked,
                  AllowedSites.isAllowedPopup(url, allowed: allowedSites) {
            // 2026-10-23: a new window / target=_blank from the allowed-site pane: never a
            // window, and not the exam's main frame either. Ignored (not a cheating attempt).
            DebugLog.log("web", "new window from an allowed site ignored: \(url.host ?? "")")
        } else if let url = navigationAction.request.url {
            EventLog.shared.record("blocked_navigation", url.host ?? url.absoluteString)
        }
        return nil
    }

    func webView(_ webView: WKWebView, contextMenuConfigurationForElement elementInfo: WKContextMenuElementInfo,
                 completionHandler: @escaping @MainActor (UIContextMenuConfiguration?) -> Void) {
        completionHandler(nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        deviceTest.end(.leave)
        committedURL = webView.url
        eventsReady = false                  // the new page asks for them ("flushEvents")
        hideConnecting()
        hidePageCrashed()                    // a page is back
        external.canvasCommitted(webView.url)
        if LockdownController.shared.isLocked, let u = webView.url, isServerURL(u) || external.allows(u) {
            ExamViewController.lastLockedPage = u
        }
    }

    /// Re-review N4: the last page a locked exam showed, so a rebuilt scene can reopen it
    /// (an offline start has no attempt id to build a URL from).
    static var lastLockedPage: URL?

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let u = webView.url { DebugLog.log("web", "page shown: \(u.host ?? u.scheme ?? "")\(u.path)") }
        pushBattery()
        applyFileAccess()
        external.doneLoaded(webView.url)
        if external.isCanvas { self.webView.disableDragAndDrop() }
        external.canvasPageLoaded()
    }

    /// Review 2026-10-24: when the exam page's process crashed (last `crashWindow` seconds).
    private var pageCrashes: [Date] = []
    private var pageCrashedView: UIView?
    static let crashLimit = 3
    static let crashWindow: TimeInterval = 60

    /// Pure: the crash times still inside the window, plus the one at `now`.
    static func recentCrashes(_ earlier: [Date], adding now: Date) -> [Date] {
        earlier.filter { now.timeIntervalSince($0) < crashWindow && $0 <= now } + [now]
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        // Review 2026-10-24: reloaded every time, without limit, a page that crashes at once
        // (memory, a bad page) looped forever. After `crashLimit` crashes within
        // `crashWindow` a native screen asks the student to retry or get the invigilator.
        // The lock is untouched; the lock bar (invigilator hold) stays above it.
        eventsReady = false                  // no page listens now: queue until one asks again
        pageCrashes = Self.recentCrashes(pageCrashes, adding: Date())
        let n = pageCrashes.count
        EventLog.shared.record("page_crashed", "the exam page stopped (\(n)x in \(Int(Self.crashWindow)) s)")
        if n >= Self.crashLimit {
            DebugLog.log("crash", "exam page process crashed \(n)x in \(Int(Self.crashWindow)) s; showing the retry screen")
            showPageCrashed()
        } else {
            DebugLog.log("crash", "exam page process crashed; reloading")
            reload()
        }
    }

    private func showPageCrashed() {
        guard pageCrashedView == nil else { return }
        hideConnecting()
        let v = UIView()
        v.backgroundColor = view.backgroundColor
        v.translatesAutoresizingMaskIntoConstraints = false
        v.accessibilityViewIsModal = true
        v.accessibilityIdentifier = "pageCrashedScreen"
        let title = UILabel()
        title.text = "Something went wrong"
        title.font = .systemFont(ofSize: 22, weight: .semibold)
        title.textColor = UIColor(red: 0.067, green: 0.094, blue: 0.153, alpha: 1)
        title.textAlignment = .center
        title.numberOfLines = 0
        let detail = UILabel()
        detail.text = "The exam page stopped several times. Tap Try again. If it keeps happening, ask the invigilator. "
            + "Answers you already typed are saved on this iPad."
        detail.font = .systemFont(ofSize: 17)
        detail.textColor = UIColor(red: 0.29, green: 0.33, blue: 0.39, alpha: 1)
        detail.textAlignment = .center
        detail.numberOfLines = 0
        let b = UIButton(type: .system)
        var cfg = UIButton.Configuration.filled()
        cfg.baseBackgroundColor = UIColor(red: 0.184, green: 0.294, blue: 0.851, alpha: 1)
        cfg.baseForegroundColor = .white
        cfg.contentInsets = NSDirectionalEdgeInsets(top: 12, leading: 22, bottom: 12, trailing: 22)
        b.configuration = cfg
        b.setTitle("Try again", for: .normal)
        b.accessibilityIdentifier = "pageCrashedRetry"
        b.addTarget(self, action: #selector(pageCrashedRetryTapped), for: .touchUpInside)
        let stack = UIStackView(arrangedSubviews: [title, detail, b])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        // Over the web view (it swallows touches there), under the lock bar, the red
        // blocking screen and the privacy cover (same order as the connecting screen).
        shield.content.insertSubview(v, aboveSubview: webView)
        shield.content.bringSubviewToFront(bar)
        if let blockingView { shield.content.bringSubviewToFront(blockingView) }
        if let privacyCover { shield.content.bringSubviewToFront(privacyCover) }
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: webView.leadingAnchor), v.trailingAnchor.constraint(equalTo: webView.trailingAnchor),
            v.topAnchor.constraint(equalTo: webView.topAnchor), v.bottomAnchor.constraint(equalTo: webView.bottomAnchor),
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor), stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: v.widthAnchor, constant: -80),
        ])
        pageCrashedView = v
        UIAccessibility.post(notification: .screenChanged, argument: title)
    }

    private func hidePageCrashed() {
        pageCrashedView?.removeFromSuperview()
        pageCrashedView = nil
    }

    /// One reload per tap. The crash times are kept: a page that crashes again at once
    /// brings this screen straight back instead of looping.
    @objc private func pageCrashedRetryTapped() {
        DebugLog.log("click", "\"Try again\" after the exam page crashed")
        hidePageCrashed()
        reload()
    }

    private func showOffline(_ error: Error) {
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain && ns.code == NSURLErrorCancelled { return }
        if ns.domain == "WebKitErrorDomain" && ns.code == 102 { return }
        DebugLog.log("web", "page load failed: \(ns.domain) \(ns.code) \(ns.localizedDescription)")
        var retry = (ns.userInfo[NSURLErrorFailingURLErrorKey] as? URL) ?? Config.startURL
        if external.isCanvas, let start = external.startURL,
           isServerURL(retry) || !external.allows(retry) || external.blocksMainFrame(retry) {
            retry = start
        }
        if !isServerURL(retry) && !external.allows(retry) { retry = Config.startURL }
        retryURL = retry
        let why = ns.domain == NSURLErrorDomain && ns.code == NSURLErrorTimedOut
            ? "No answer within \(Int(Self.loadTimeout)) seconds." : ns.localizedDescription
        loadOwnHTML(Self.page(title: "Cannot reach the exam server",
                              body: "FairPlay is trying again every 3 seconds. Answers you already typed are saved on this iPad.",
                              small: "\(server.absoluteString) · \(why)", retry: true, changeServer: Config.isDevBuild))
    }

    private func loadOwnHTML(_ html: String) {
        hideConnecting()
        ownPageLoadPending = true
        webView.loadHTMLString(html, baseURL: nil)
    }

    /// Our own pages (offline, not set up). Review L3: all text is HTML-escaped. The page
    /// holds no URL: "Try again" and "Change server" are fairplay-app:// links the app
    /// handles itself (decidePolicyFor), only while one of these pages is shown.
    static func page(title: String, body: String, small: String? = nil, retry: Bool = false,
                     changeServer: Bool = false) -> String {
        let esc: (String) -> String = {
            $0.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
                .replacingOccurrences(of: "'", with: "&#39;")
        }
        var extra = ""
        if retry {
            extra += """
            <button id="retry">Try again now</button><script>(function(){\
            document.getElementById('retry').addEventListener('click',function(){location.href='\(appScheme)://retry';});\
            setTimeout(function(){location.href='\(appScheme)://autoretry';},3000);})();</script>
            """
        }
        if changeServer {
            extra += "<p><a href=\"\(appScheme)://server\" style=\"font-size:15px\">Change server (dev build)</a></p>"
        }
        return """
        <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        font:17px -apple-system,system-ui,sans-serif;background:#f4f5f8;color:#111827}
        .card{background:#fff;border-radius:16px;padding:32px 36px;max-width:480px;margin:24px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
        h1{font-size:24px;margin:0 0 10px}p{color:#4b5563;line-height:1.5}button{margin-top:12px;font:inherit;font-weight:600;
        padding:12px 20px;border:0;border-radius:12px;background:#2f4bd9;color:#fff}a{color:#2f4bd9}</style></head>
        <body><div class="card">\(BrandMark.svg(size: 44))<h1>\(esc(title))</h1><p>\(esc(body))</p>\(small.map { "<p style=\"font-size:13px\">\(esc($0))</p>" } ?? "")\(extra)</div></body></html>
        """
    }

    // MARK: - LockSurface

    func enterLockedMode() {
        #if FAIRPLAY_DEV
        if UserDefaults.standard.bool(forKey: "ShieldSelfTest") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.shield.selfTest() }
        }
        #endif
        barHeight.constant = LockBar.height
        bar.isHidden = false
        bar.startClock()
        UIApplication.shared.isIdleTimerDisabled = true     // the iPad doesn't auto-lock mid-exam
        refreshSystemChrome()
        lockStateChanged()
        applyFileAccess()                                   // 2026-10-24: no files while locked
    }

    func exitLockedMode() {
        ExamViewController.lastLockedPage = nil
        allowedSites = []                            // the next exam sends its own
        lastStartExamId = nil                        // 2026-10-23 (review): the next pre-exam lock sets its own
        lastPreLockPage = nil
        external.abort()
        barHeight.constant = 0
        bar.isHidden = true
        bar.stopClock()
        UIApplication.shared.isIdleTimerDisabled = false
        refreshSystemChrome()
        applyFileAccess()                            // 2026-10-24: files work again
    }

    private func refreshSystemChrome() {
        UIMenuSystem.main.setNeedsRebuild()          // review L12: edit menus follow the lock
        UIMenuSystem.context.setNeedsRebuild()
        setNeedsStatusBarAppearanceUpdate()
        setNeedsUpdateOfHomeIndicatorAutoHidden()
        setNeedsUpdateOfScreenEdgesDeferringSystemGestures()
        view.layoutIfNeeded()
    }

    func lockStateChanged() {
        let s = LockdownController.shared.state
        if s.softLock {
            bar.setLockText(Config.isDevBuild ? "Watched (dev build, no Assessment Mode)"
                                              : "Watched (test build, no Assessment Mode)", soft: true)
        } else if s.assessment {
            bar.setLockText("Locked · Assessment Mode", soft: false)
        } else if s.singleApp {
            bar.setLockText("Locked · Single App Mode", soft: false)
        } else {
            bar.setLockText("Locked mode interrupted", soft: true)
        }
    }

    func showBlocking(reason: String, actionTitle: String?, action: (() -> Void)?) {
        blockingAction = action
        if let blockingLabel {
            blockingLabel.text = reason
            blockingButton?.setTitle(actionTitle, for: .normal)
            blockingButton?.isHidden = actionTitle == nil
            return
        }
        DebugLog.log("lock", "red blocking screen shown")
        let v = UIView()
        v.backgroundColor = UIColor(red: 0.55, green: 0.15, blue: 0.05, alpha: 1)
        v.accessibilityViewIsModal = true
        v.translatesAutoresizingMaskIntoConstraints = false
        v.accessibilityIdentifier = "blockingScreen"
        let l = UILabel()
        l.text = reason
        l.numberOfLines = 0
        l.textAlignment = .center
        l.textColor = .white
        l.font = .systemFont(ofSize: 22, weight: .semibold)
        let b = UIButton(type: .system)
        var cfg = UIButton.Configuration.filled()
        cfg.baseBackgroundColor = .white
        cfg.baseForegroundColor = UIColor(red: 0.55, green: 0.15, blue: 0.05, alpha: 1)
        cfg.contentInsets = NSDirectionalEdgeInsets(top: 12, leading: 22, bottom: 12, trailing: 22)
        b.configuration = cfg
        b.setTitle(actionTitle, for: .normal)
        b.isHidden = actionTitle == nil
        b.addTarget(self, action: #selector(blockingTapped), for: .touchUpInside)
        let stack = UIStackView(arrangedSubviews: [l, b])
        stack.axis = .vertical
        stack.spacing = 24
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        // Above the web view (it must cover and swallow touches), below the lock bar (the
        // invigilator hold must keep working). Review C1: it used to land under the web view.
        shield.content.insertSubview(v, aboveSubview: webView)
        shield.content.bringSubviewToFront(bar)
        if let privacyCover { shield.content.bringSubviewToFront(privacyCover) }
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: shield.content.leadingAnchor), v.trailingAnchor.constraint(equalTo: shield.content.trailingAnchor),
            v.topAnchor.constraint(equalTo: bar.bottomAnchor), v.bottomAnchor.constraint(equalTo: shield.content.bottomAnchor),
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor), stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: v.widthAnchor, constant: -80),
        ])
        blockingView = v; blockingLabel = l; blockingButton = b
        updateBoxHold()
        // Re-review N3: covering is not enough; the exam also loses the keyboard (on-screen
        // and hardware), touches and VoiceOver until the problem is fixed.
        webView.endEditing(true)
        webView.isUserInteractionEnabled = false
        webView.accessibilityElementsHidden = true
        UIAccessibility.post(notification: .screenChanged, argument: l)
    }

    @objc private func blockingTapped() { blockingAction?() }

    func hideBlocking() {
        guard blockingView != nil else { return }
        DebugLog.log("lock", "red blocking screen hidden")
        blockingView?.removeFromSuperview()
        blockingView = nil; blockingLabel = nil; blockingButton = nil; blockingAction = nil
        updateBoxHold()
        webView.isUserInteractionEnabled = true
        webView.accessibilityElementsHidden = false
    }

    func setPrivacyCover(_ on: Bool) {
        if !on { privacyCover?.removeFromSuperview(); privacyCover = nil; updateBoxHold(); return }
        guard privacyCover == nil else { return }
        let v = UIView()
        v.backgroundColor = UIColor(red: 0.08, green: 0.09, blue: 0.12, alpha: 1)
        let l = UILabel()
        l.text = "FairPlay · exam in progress"
        l.textColor = .white
        l.font = .systemFont(ofSize: 20, weight: .semibold)
        l.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(l)
        v.translatesAutoresizingMaskIntoConstraints = false
        shield.content.addSubview(v)
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: shield.content.leadingAnchor), v.trailingAnchor.constraint(equalTo: shield.content.trailingAnchor),
            v.topAnchor.constraint(equalTo: shield.content.topAnchor), v.bottomAnchor.constraint(equalTo: shield.content.bottomAnchor),
            l.centerXAnchor.constraint(equalTo: v.centerXAnchor), l.centerYAnchor.constraint(equalTo: v.centerYAnchor),
        ])
        privacyCover = v
        updateBoxHold()
    }

    private func updateBoxHold() {
        let covered = HomeworkPanelPolicy.hidden(blockingShown: blockingView != nil, privacyShown: privacyCover != nil)
        box.setHeld(covered)
        homework.setHeld(covered)
    }

    private func raiseCovers() {
        shield.content.bringSubviewToFront(bar)
        if let blockingView { shield.content.bringSubviewToFront(blockingView) }
        if let privacyCover { shield.content.bringSubviewToFront(privacyCover) }
    }

    // MARK: - Website exam finish confirmation

    private func confirmFinish(_ done: @escaping () -> Void) {
        guard presentedViewController == nil else { return }
        let a = UIAlertController(title: "Finish the exam?",
                                  message: "Make sure you submitted your answers on the website first. After this, the exam closes and your iPad unlocks.",
                                  preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "Keep working", style: .cancel))
        a.addAction(UIAlertAction(title: "Finish exam", style: .destructive) { _ in done() })
        present(a, animated: true)
    }

    // MARK: - Invigilator exit (hold the lock bar 3 s, or Ctrl-Opt-Cmd-E)

    /// The code is per exam, lives only on the server (teacher's live room) and is
    /// rate-limited there. No server / an error: the offline code instead.
    func promptEmergencyExit() {
        DebugLog.log("exit", "invigilator exit opened (locked=\(LockdownController.shared.isLocked))")
        // 2026-10-23: the pre-exam lock has no attempt to protect: this just unlocks (no
        // code, no server, no exit event).
        if PreLockState.emergencyExitUnlocksAtOnce(locked: LockdownController.shared.isLocked,
                                                   preLocked: LockdownController.shared.isPreLock,
                                                   hasExam: AppSession.shared.exam != nil) {
            DebugLog.log("exit", "pre-exam lock: unlocked by the invigilator exit (no exam attached)")
            LockdownController.shared.endPreLock()   // onUnlocked sends "unlocked"
            sendState()
            return
        }
        guard LockdownController.shared.isLocked, presentedViewController == nil else { return }
        let a = UIAlertController(title: "Invigilator exit",
                                  message: "Enter the invigilator code from the teacher's live room to end locked mode. The teacher will see this."
                                    + "\n\nNo connection or an error? Choose \"Offline code\"."
                                    + (Config.isDevBuild ? "\n\nDev build: 0000 also works." : ""),
                                  preferredStyle: .alert)
        a.addTextField { f in
            f.placeholder = "Invigilator code"
            f.isSecureTextEntry = true
            f.keyboardType = .asciiCapableNumberPad
            f.accessibilityIdentifier = "invigilatorCode"
        }
        a.addAction(UIAlertAction(title: "End locked mode", style: .destructive) { [weak self, weak a] _ in
            self?.checkInvigilatorCode(a?.textFields?.first?.text ?? "")
        })
        a.addAction(UIAlertAction(title: "Offline code…", style: .default) { [weak self] _ in
            DispatchQueue.main.async { self?.promptOfflineExit(reason: nil) }
        })
        a.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        present(a, animated: true)
    }

    private func checkInvigilatorCode(_ typed: String) {
        guard !typed.isEmpty, LockdownController.shared.isLocked else { return }
        if OfflineExit.devCodeMatches(typed) {
            DebugLog.log("exit", "dev test code accepted (dev build only)")
            EventLog.shared.record("offline_exit", "dev test code")
            endLock()
            return
        }
        AppSession.shared.checkExitCode(typed) { [weak self] result in
            DebugLog.log("exit", "server exit-code result: \(result)")
            guard let self, LockdownController.shared.isLocked else { return }
            switch result {
            case .ok: self.endLock()
            case .wrong: self.notice("Wrong invigilator code. Hold the lock bar again to retry.")
            case .tooMany: self.notice("Too many wrong codes. Wait 5 minutes.")
            case .unreachable: self.promptOfflineExit(reason: "The exam server cannot be reached.")
            case .noSession: self.promptOfflineExit(reason: "This exam has not reached the server yet.")
            case .serverError(let status): self.promptOfflineExit(reason: "The exam server answered with an error (\(status)).")
            }
        }
    }

    func promptOfflineExit(reason: String?) {
        guard LockdownController.shared.isLocked, presentedViewController == nil else { return }
        guard OfflineExit.isAvailable else {
            notice((reason.map { $0 + "\n\n" } ?? "")
                   + "Offline exit is not available: this iPad never reached the exam server for this exam. "
                   + "If you must end the exam here, restart the iPad. Answers are saved on it.")
            return
        }
        let a = UIAlertController(title: "Offline invigilator exit",
                                  message: (reason.map { $0 + "\n\n" } ?? "")
                                    + "iPad code:  \(OfflineExit.challengeForDisplay)\n\n"
                                    + "Invigilator: in the teacher's live room choose \"Mac offline or stuck?\", pick this student, "
                                    + "type the iPad code and enter the 8-digit code here. Answers stay saved on this iPad. This is logged.",
                                  preferredStyle: .alert)
        a.addTextField { f in
            f.placeholder = "8-digit offline code"
            f.isSecureTextEntry = true
            f.keyboardType = .asciiCapableNumberPad
        }
        a.addAction(UIAlertAction(title: "End locked mode", style: .destructive) { [weak self, weak a] _ in
            guard let self, LockdownController.shared.isLocked else { return }
            switch OfflineExit.check(a?.textFields?.first?.text ?? "") {
            case .ok:
                EventLog.shared.record("offline_exit", "offline invigilator code accepted")
                self.endLock()
            case .wrong:
                EventLog.shared.record("offline_exit_rejected", "wrong offline invigilator code")
                DispatchQueue.main.async { self.promptOfflineExit(reason: "Wrong code, try again.") }
            case .tooMany:
                EventLog.shared.record("offline_exit_rejected", "too many wrong codes")
                DispatchQueue.main.async { self.notice("Too many wrong codes. Wait 5 minutes, then try again.") }
            case .unavailable:
                DispatchQueue.main.async { self.promptOfflineExit(reason: nil) }
            }
        })
        a.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        present(a, animated: true)
    }

    private func notice(_ text: String) {
        guard presentedViewController == nil else { return }
        let a = UIAlertController(title: "Invigilator exit", message: text, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "OK", style: .default))
        present(a, animated: true)
    }
}

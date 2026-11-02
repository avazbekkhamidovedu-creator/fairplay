import UIKit
import FairPlayCore

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        DebugLog.log("app", "FairPlay \(Config.version) (\(Config.isDevBuild ? "dev" : "release") build) server: \(Config.serverURL.absoluteString) device: \(AppSession.shared.deviceId)")
        Sealed.uploadAll()      // S5: answers sealed in an earlier run go up as soon as possible
        return true
    }

    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        if session.role == .windowExternalDisplayNonInteractive {
            // An external display gets a blank scene; the lock blocks the exam meanwhile.
            let c = UISceneConfiguration(name: "External", sessionRole: session.role)
            c.delegateClass = ExternalDisplaySceneDelegate.self
            return c
        }
        let c = UISceneConfiguration(name: "Main", sessionRole: session.role)
        c.delegateClass = SceneDelegate.self
        return c
    }

    func application(_ application: UIApplication, open url: URL,
                     options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        let scenes = application.connectedScenes.compactMap { $0.delegate as? SceneDelegate }
        guard scenes.count == 1 else { return false }
        return scenes[0].openLaunchURLs([url])
    }

    /// Third-party keyboards (they can read and send everything typed) are never allowed;
    /// Apple's own keyboards and input methods (e.g. Pinyin) are not extensions and work.
    func application(_ application: UIApplication,
                     shouldAllowExtensionPointIdentifier extensionPointIdentifier: UIApplication.ExtensionPointIdentifier) -> Bool {
        if extensionPointIdentifier == .keyboard {
            DebugLog.log("app", "third-party keyboard refused")
            return false
        }
        return true
    }

    /// No state restoration: a relaunch always starts from the server's view of the exam.
    func application(_ application: UIApplication, shouldSaveSecureApplicationState coder: NSCoder) -> Bool { false }
    func application(_ application: UIApplication, shouldRestoreSecureApplicationState coder: NSCoder) -> Bool { false }
}

final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private var exam: ExamViewController?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        let vc = ExamViewController()
        window.rootViewController = vc
        window.makeKeyAndVisible()
        self.window = window
        exam = vc

        let lock = LockdownController.shared
        lock.surface = vc
        lock.onUnlocked = { [weak vc] in vc?.launchRecoveryActive = false; vc?.closeBox(); vc?.send(["type": "unlocked"]) }
        lock.onLockPending = { [weak vc] in vc?.send(["type": "lockPending", "reason": "assessment_prompt"]) }
        EventLog.shared.onEvent = { [weak vc] type, detail, time in vc?.sendEvent(type: type, detail: detail, time: time) }
        AppSession.shared.onStatus = { [weak vc] status in vc?.handleServerStatus(status) }

        // Review L9: iPadOS can rebuild the scene while the app keeps running. If a lock is
        // in force, the new screen goes straight back to it (no relaunch marker involved).
        if lock.isLocked {
            vc.launchRecoveryActive = true
            vc.start(resumeAttempt: AppSession.shared.exam?.attemptId, resumePage: ExamViewController.lastLockedPage)
            vc.enterLockedMode()
            lock.runChecks(atLock: false)
            return
        }
        let leftover = LockMarker.takeLeftover()
        vc.launchRecoveryActive = leftover.wasLocked
        if leftover.wasLocked {
            // The last run ended while locked (crash, forced restart, flat battery). The
            // page forwards this once the student is back in; the server saw the beat gap.
            EventLog.shared.record("previous_run_killed", "FairPlay ended while locked last time")
        }
        vc.start(resumeAttempt: leftover.attemptId)
        if !leftover.wasLocked { _ = openLaunchURLs(options.urlContexts.map(\.url)) }
    }

    func scene(_ scene: UIScene, openURLContexts contexts: Set<UIOpenURLContext>) {
        _ = openLaunchURLs(contexts.map(\.url))
    }

    @discardableResult
    func openLaunchURLs(_ urls: [URL]) -> Bool {
        guard urls.count == 1, let url = urls.first, let exam else { return false }
        let key: String?
        if url.isFileURL {
            guard (url.host == nil || url.host == "" || url.host == "localhost"),
                  url.pathExtension.lowercased() == "fairplay" else { return false }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
                  let file = try? FileHandle(forReadingFrom: url) else { return false }
            defer { try? file.close() }
            guard let data = try? file.read(upToCount: 4097) else { return false }
            key = ExamLaunch.parseFile(data)
        } else { key = ExamLaunch.parse(url) }
        guard let key else { return false }
        return exam.openExamLaunch(key)
    }
}

/// Nothing of the exam is ever shown on an external screen.
final class ExternalDisplaySceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let w = UIWindow(windowScene: windowScene)
        let vc = UIViewController()
        vc.view.backgroundColor = .black
        w.rootViewController = vc
        w.isHidden = false
        window = w
    }
}

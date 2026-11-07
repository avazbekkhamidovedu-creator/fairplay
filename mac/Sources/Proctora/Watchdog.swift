import Foundation
import Darwin

/// Makes killing FairPlay during an exam loud (audit 2026-10-12 #6).
///
/// While locked, a second copy of our own executable runs as `--watchdog <pid>`. It
/// ignores SIGTERM/SIGHUP/SIGINT (so a plain `killall` misses it) and, if the app dies
/// while the lock marker file still exists, reports `watchdog_app_killed` to the server
/// and reopens FairPlay. The app itself also ignores SIGTERM while locked. `kill -9` on
/// both still works -- then the server flags the gap in the app's signed beats.
enum Watchdog {

    private static var child: Process?

    private static var folder: URL {
        let fm = FileManager.default
        let base = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fm.temporaryDirectory
        return base.appendingPathComponent(Config.supportFolderName, isDirectory: true)
    }

    /// Exists exactly while the app is locked: "<attempt>\n<token>\n<session>" (may be empty lines),
    /// plus a 4th line "prelock" during the pre-exam lock (PreLock.swift).
    static var markerURL: URL { folder.appendingPathComponent("locked.marker") }

    /// 2026-10-23: the lock is the pre-exam lock (no attempt). If the app ends meanwhile,
    /// nothing is reported, FairPlay is not reopened and the next launch is not "killed".
    /// Review 2026-10-23: the watchdog learns this from its launch argument ("--prelock"),
    /// never from the marker (the student can rewrite that file).
    private static var preLock = false
    static let preLockLine = "prelock"
    static let preLockArgument = "--prelock"

    /// The native OJ lock (NativeLock.swift): neither the marker nor any file decides. If the app
    /// dies the watchdog reopens it with `--relock`, unless the app said "released" over the pipe
    /// it handed down as the watchdog's standard input (a descriptor the student has no way to write to).
    private static var nativeLock = false
    private static var releasePipe: Pipe?
    static let releaseByte: UInt8 = 0x52
    static let nativeArgument = "--native"
    static let relockArgument = "--relock"

    // MARK: - In the app

    static func lockStarted(preLock: Bool = false, native: Bool = false) {
        signal(SIGTERM, SIG_IGN)
        signal(SIGHUP, SIG_IGN)
        signal(SIGINT, SIG_IGN)
        Watchdog.preLock = preLock
        Watchdog.nativeLock = native
        writeMarker(AppSession.shared.exam)
        spawn()
    }

    /// The pre-exam lock became the exam lock: from now on the marker is a normal one, and
    /// (review 2026-10-23, as on Windows) a fresh exam watchdog takes over: the new one is
    /// started first, then the "--prelock" one is retired, so there is no gap.
    static func preLockEnded() {
        guard preLock else { return }
        preLock = false
        guard FileManager.default.fileExists(atPath: markerURL.path) else { return }
        writeMarker(AppSession.shared.exam)
        let old = child
        child = nil
        spawn()
        if let old, old.isRunning {
            // It ignores SIGTERM; SIGKILL on our own child always works.
            let pid = old.processIdentifier
            let ok = kill(pid, SIGKILL) == 0
            DebugLog.log("lock", "pre-exam watchdog pid \(pid) retired: \(ok ? "ok" : "FAILED errno \(errno)")")
        }
    }

    static func lockEnded() {
        preLock = false
        if nativeLock { signalReleased() }
        nativeLock = false
        try? FileManager.default.removeItem(at: markerURL)
        signal(SIGTERM, SIG_DFL)
        signal(SIGHUP, SIG_DFL)
        signal(SIGINT, SIG_DFL)
        // The watchdog sees the marker gone and exits by itself.
        child = nil
    }

    /// Tells a native watchdog that the lock ended on purpose, then drops the pipe.
    static func signalReleased() {
        guard let pipe = releasePipe else { return }
        try? pipe.fileHandleForWriting.write(contentsOf: Data([releaseByte]))
        try? pipe.fileHandleForWriting.close()
        releasePipe = nil
    }

    /// Called when the exam session becomes known/changes while locked.
    static func update(exam: AppSession.Exam?) {
        guard FileManager.default.fileExists(atPath: markerURL.path) else { return }
        writeMarker(exam)
    }

    /// At launch: a marker left behind means the last run died while locked (2026-10-23:
    /// not when that was only the pre-exam lock; then there is nothing to report).
    static func cleanUpAtLaunch() -> Bool {
        let existed = FileManager.default.fileExists(atPath: markerURL.path)
        let text = existed ? (try? String(contentsOf: markerURL, encoding: .utf8)) : nil
        try? FileManager.default.removeItem(at: markerURL)
        if let text, markerIsPreLock(text) {
            DebugLog.log("app", "last run ended during the pre-exam lock (no exam): nothing to report")
            return false
        }
        return existed
    }

    /// The marker's text (pure, for the tests).
    static func markerText(exam: AppSession.Exam?, preLock: Bool) -> String {
        var lines = [exam?.attemptId ?? "", exam?.token ?? "", exam?.session ?? ""]
        if preLock { lines.append(preLockLine) }
        return lines.joined(separator: "\n")
    }

    /// A pre-exam lock marker: the 4th line says so and no attempt is named.
    static func markerIsPreLock(_ text: String) -> Bool {
        let parts = text.components(separatedBy: "\n")
        return parts.count >= 4 && parts[3] == preLockLine && parts[0].isEmpty
    }

    private static func writeMarker(_ exam: AppSession.Exam?) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let text = markerText(exam: exam, preLock: preLock)
        // Audit 2026-10-13: the marker holds the token, so it is 0600 from the first byte
        // (was: written with default permissions, then chmodded). Temp file + rename keeps
        // the update atomic for the watchdog reading it.
        let tmp = folder.appendingPathComponent("locked.marker.\(getpid()).tmp")
        try? FileManager.default.removeItem(at: tmp)
        let fd = open(tmp.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard fd >= 0 else { return }
        let bytes = Array(text.utf8)
        let wrote = bytes.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        close(fd)
        guard wrote == bytes.count else { try? FileManager.default.removeItem(at: tmp); return }
        if rename(tmp.path, markerURL.path) != 0 { try? FileManager.default.removeItem(at: tmp) }
    }

    /// While locked: true when the watchdog runs. If it died (killed), it is started
    /// again and false is returned once so the lock can report it.
    @discardableResult
    static func ensureRunning() -> Bool {
        guard shouldKeepWatchdog(native: nativeLock, markerExists: FileManager.default.fileExists(atPath: markerURL.path)) else { return true }
        if let c = child, c.isRunning { return true }
        let hadOne = child != nil
        spawn()
        return !hadOne
    }

    /// Review 2026-10-14: the watchdog runs as Contents/MacOS/FairPlayGuard (a copy of the
    /// app's executable made by build.sh), so `killall Proctora` doesn't end it too.
    static var guardExecutable: URL? {
        Bundle.main.url(forAuxiliaryExecutable: "FairPlayGuard") ?? Bundle.main.executableURL
    }

    /// A native lock keeps its watchdog alive whatever happens to the marker file.
    static func shouldKeepWatchdog(native: Bool, markerExists: Bool) -> Bool { native || markerExists }

    /// The watchdog's arguments (pure, for the tests).
    static func arguments(parent: pid_t, preLock: Bool, native: Bool = false) -> [String] {
        ["--watchdog", String(parent)] + (native ? [nativeArgument] : preLock ? [preLockArgument] : [])
    }

    /// `--watchdog <pid> [--prelock]` (main.swift), or nil when this is not a watchdog launch.
    static func parseArguments(_ args: [String]) -> (parent: pid_t, preLock: Bool, native: Bool)? {
        guard args.count >= 3, args[1] == "--watchdog", let pid = Int32(args[2]) else { return nil }
        let rest = args.dropFirst(3)
        return (pid, rest.contains(preLockArgument), rest.contains(nativeArgument))
    }

    private static func spawn() {
        if let c = child, c.isRunning { return }
        guard let exe = guardExecutable else { return }
        let p = Process()
        p.executableURL = exe
        p.arguments = arguments(parent: ProcessInfo.processInfo.processIdentifier, preLock: preLock, native: nativeLock)
        if nativeLock {
            signal(SIGPIPE, SIG_IGN)
            let pipe = Pipe()
            p.standardInput = pipe
            releasePipe = pipe
        }
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run(); child = p; DebugLog.log("lock", "watchdog started pid \(p.processIdentifier)") }
        catch { NSLog("[FairPlay] watchdog not started: \(error)"); DebugLog.log("error", "watchdog not started: \(error)") }
    }

    // MARK: - In the watchdog process

    /// What a watchdog does when the app is gone while the marker exists (pure, for the
    /// tests). Review 2026-10-23: the mode comes from the launch argument only. An exam
    /// watchdog always reports and reopens, whatever the marker says ("\n\n\nprelock" written
    /// into it during an exam changes nothing). A "--prelock" one never reports; it removes
    /// the marker only while it is still the pre-exam lock's (a retired one that has not
    /// exited yet never touches the exam's marker: the exam watchdog handles that).
    enum ParentGone: Equatable {
        case silent(removeMarker: Bool)
        case reportAndReopen
    }

    static func onParentGone(preLockMode: Bool, markerText: String?, nativeMode: Bool = false) -> ParentGone {
        guard !nativeMode else { return .reportAndReopen }
        guard preLockMode else { return .reportAndReopen }
        return .silent(removeMarker: markerText.map(markerIsPreLock) ?? false)
    }

    enum NativeStep: Equatable { case keepWatching, relock, exit }

    /// The native watchdog's whole decision (pure, for the tests). Only the app's own "released"
    /// message ends the watch for good; an app that is gone without it is always locked again.
    static func nativeStep(parentAlive: Bool, released: Bool) -> NativeStep {
        if released { return .exit }
        return parentAlive ? .keepWatching : .relock
    }

    /// What the watchdog read from its standard input so far.
    static func readReleased(_ bytes: [UInt8]) -> Bool { bytes.contains(releaseByte) }

    private static func runNative(parent: pid_t) -> Never {
        _ = fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK)
        var released = false
        var inputClosed = false
        func drain() {
            var buf = [UInt8](repeating: 0, count: 16)
            while true {
                let n = read(0, &buf, buf.count)
                if n > 0 { if readReleased(Array(buf[0..<n])) { released = true } }
                else if n == 0 { inputClosed = true; return }
                else { return }
            }
        }
        while true {
            sleep(1)
            drain()
            let alive = !(kill(parent, 0) != 0 && errno == ESRCH) && !inputClosed
            if !alive { usleep(200_000); drain() }
            switch nativeStep(parentAlive: alive, released: released) {
            case .keepWatching: continue
            case .exit: exit(0)
            case .relock:
                try? FileManager.default.removeItem(at: markerURL)
                reopen(relock: true)
                exit(0)
            }
        }
    }

    /// Entry point for `--watchdog <pid> [--prelock|--native]`; never returns.
    static func run(parent: pid_t, preLock preLockMode: Bool = false, native: Bool = false) -> Never {
        signal(SIGTERM, SIG_IGN)
        signal(SIGHUP, SIG_IGN)
        signal(SIGINT, SIG_IGN)
        if native { runNative(parent: parent) }
        while true {
            sleep(1)
            let markerExists = FileManager.default.fileExists(atPath: markerURL.path)
            if !markerExists { exit(0) }                       // lock ended normally
            if kill(parent, 0) != 0 && errno == ESRCH {         // app gone while locked
                // 2026-10-23: only the pre-exam lock (no attempt): no report, no reopen.
                let markerText = try? String(contentsOf: markerURL, encoding: .utf8)
                if case .silent(let removeMarker) = onParentGone(preLockMode: preLockMode, markerText: markerText) {
                    if removeMarker { try? FileManager.default.removeItem(at: markerURL) }
                    exit(0)
                }
                if let text = markerText,
                   let aid = text.components(separatedBy: "\n").first,
                   AppSession.isSafeToken(aid, min: 3, max: 32) {
                    resumeAttemptId = aid
                }
                report()
                try? FileManager.default.removeItem(at: markerURL)
                reopen()
                exit(0)
            }
        }
    }

    private static func report() {
        guard let text = try? String(contentsOf: markerURL, encoding: .utf8) else { return }
        let parts = text.components(separatedBy: "\n")
        guard parts.count >= 3, !parts[0].isEmpty, !parts[1].isEmpty else { return }
        let iso = ISO8601DateFormatter()
        // Audit M-17: a crash and a kill look the same from here: say what is known.
        let body: [String: Any] = ["events": [["type": "watchdog_app_killed",
                                               "detail": "FairPlay ended while locked (force-quit or crash)",
                                               "client_time": iso.string(from: Date())]]]
        var req = URLRequest(url: Config.serverURL.appendingPathComponent("api/student/attempts/\(parts[0])/events"),
                             timeoutInterval: 8)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(parts[1])", forHTTPHeaderField: "Authorization")
        if !parts[2].isEmpty { req.setValue(parts[2], forHTTPHeaderField: "X-FairPlay-Session") }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let done = DispatchSemaphore(value: 0)
        Net.session.dataTask(with: req) { _, _, _ in done.signal() }.resume()
        _ = done.wait(timeout: .now() + 10)
    }

    /// Audit M-17: reopen straight into the attempt (the page resumes it and locks again)
    /// instead of the login page.
    private static func reopen(relock: Bool = false) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        var args = [Bundle.main.bundleURL.path]
        if relock { args += ["--args", relockArgument] }
        else if let aid = resumeAttemptId { args += ["--args", "--resume-attempt", aid] }
        p.arguments = args
        try? p.run()
    }

    /// The attempt id from the marker (read before the marker is removed).
    private static var resumeAttemptId: String?
}

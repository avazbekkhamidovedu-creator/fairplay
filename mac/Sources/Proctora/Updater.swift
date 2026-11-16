import AppKit
import CryptoKit
import Security

/// 2026-10-22: in-app updates (release builds only).
///
/// Check: a few seconds after launch and then every 6 hours, ask the exam server first
/// (GET api/app/latest?platform=mac, ETag kept on disk), and GitHub's API only when the server
/// gives no answer (older server: 404; offline; error), after a random delay. Pilot 0.8.8:
/// this keeps a campus full of Macs off GitHub's 60-requests-per-hour limit. A 426 from the
/// server checks again at once. An update is offered only when
///  - the tag is vX.Y.Z or vX.Y.Z.W and newer than Config.version,
///  - it has exactly one asset "FairPlay-Mac.zip" whose download URL is
///    https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/<tag>/FairPlay-Mac.zip,
///  - GitHub gives that asset a "sha256:<64 hex>" digest (no digest: no update, never an
///    unverified install).
/// The page learns about it from the "state" message (`update: {version}`).
///
/// Install ({action:"installUpdate"}), never while locked or while an exam session is bound:
/// download -> SHA-256 must equal the digest -> `ditto -x -k` into a fresh folder, which
/// must hold exactly FairPlay.app -> Info.plist bundle id = ours, version = the release's ->
/// `codesign --verify --deep --strict` -> when this copy is signed with a real certificate
/// (not ad hoc), the new copy must satisfy this copy's designated requirement (an update
/// can't swap the signer) -> the running bundle goes to the Trash, the new one takes its
/// place (the old one is put back on any failure) -> `open -n` the new copy, quit this one.
/// Files URLSession downloads carry no quarantine attribute, so the new copy opens without
/// a Gatekeeper prompt; nothing here removes quarantine from any file.
final class Updater {
    static let shared = Updater()

    struct Release: Equatable {
        let version: String
        let zipURL: URL
        /// Lower-case hex SHA-256 of the zip (from the asset's GitHub digest).
        let sha256: String
    }

    enum Failure: String, Error {
        case download, verify, locked, busy
        case notWritable = "not_writable"
    }

    static let latestAPI = URL(string: "https://api.github.com/repos/avazbekkhamidovedu-creator/fairplay-download/releases/latest")!
    static let releasePage = URL(string: "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest")!
    static let assetName = "FairPlay-Mac.zip"
    static let assetPathPrefix = "/avazbekkhamidovedu-creator/fairplay-download/releases/download/"
    static let appName = "FairPlay.app"
    static let checkEvery: TimeInterval = 6 * 3600
    static let retryAfterFailure: TimeInterval = 3600
    /// Largest random wait before the GitHub fallback (many Macs behind one address).
    static let githubJitter: TimeInterval = 20 * 60

    /// "+-25 %" around `seconds`, so Macs that started together do not stay in step.
    static func jittered(_ seconds: TimeInterval, random: Double = Double.random(in: -0.25...0.25)) -> TimeInterval {
        seconds * (1 + random)
    }

    /// The last answer of each source and its ETag, kept across launches so a 304 costs nothing.
    struct Cache {
        var defaults: UserDefaults = .standard
        func load(_ source: String) -> (etag: String?, body: Data)? {
            guard let body = defaults.data(forKey: "FairPlayUpdateBody." + source) else { return nil }
            return (defaults.string(forKey: "FairPlayUpdateETag." + source), body)
        }
        func save(_ source: String, etag: String?, body: Data) {
            defaults.set(body, forKey: "FairPlayUpdateBody." + source)
            if let etag { defaults.set(etag, forKey: "FairPlayUpdateETag." + source) }
            else { defaults.removeObject(forKey: "FairPlayUpdateETag." + source) }
        }
    }
    static var cache = Cache()

    /// The newer release, once a check found one (main thread).
    private(set) var available: Release?
    /// True from "installUpdate" until it failed or the app quits (main thread).
    private(set) var installing = false
    /// Called on the main thread when `available` changes (the window re-sends "state").
    var onAvailable: (() -> Void)?
    /// Called on the main thread after every finished check, found or not (the 426 screen uses it).
    var onChecked: (() -> Void)?

    private var timer: Timer?
    private var checking = false

    private static var userAgent: String { "FairPlay/\(Config.version) (Mac)" }

    private lazy var checkSession: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 10
        cfg.timeoutIntervalForResource = 20
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: cfg)
    }()

    private lazy var downloadSession: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 60
        cfg.timeoutIntervalForResource = 30 * 60
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: cfg)
    }()

    // MARK: - Checking

    /// At launch. Dev builds never check.
    func start() {
        guard !Config.isDevBuild else {
            DebugLog.log("update", "dev build: no update checks")
            return
        }
        // The server answers in a few ms (and with a 304 most times): ask early so a Mac
        // below the minimum is offered the update right at launch.
        schedule(after: 2 + Double.random(in: 0...6))
    }

    private func schedule(after seconds: TimeInterval) {
        timer?.invalidate()
        let t = Timer(timeInterval: seconds, repeats: false) { [weak self] _ in self?.check() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// True while the device-test preview runs (set by the window controller). An update must not
    /// swap the app under the preview any more than under a lock.
    static var devicePreview = false

    static func mayUpdateNow(locked: Bool, examBound: Bool, devicePreview: Bool) -> Bool {
        !locked && !examBound && !devicePreview
    }

    /// `now`: after a 426 -- no waiting, including before the GitHub fallback.
    func check(now: Bool = false) {
        guard !Config.isDevBuild, !checking, !installing else { return }
        guard Updater.mayUpdateNow(locked: LockdownController.shared.isLocked || LockdownController.shared.nativeLockActive, examBound: false,
                                   devicePreview: Updater.devicePreview) else {
            DebugLog.log("update", "check skipped while locked or in a device test")
            schedule(after: Updater.jittered(Updater.retryAfterFailure))
            return
        }
        checking = true
        askServer(immediately: now)
    }

    private func askServer(immediately: Bool) {
        var comps = URLComponents(url: Config.serverURL.appendingPathComponent("api/app/latest"), resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "platform", value: "mac")]
        guard let url = comps?.url else { fallBackToGitHub(immediately: immediately); return }
        var req = URLRequest(url: url, timeoutInterval: 10)
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue(Config.version, forHTTPHeaderField: "X-FairPlay-Version")
        req.setValue("mac", forHTTPHeaderField: "X-FairPlay-Platform")
        req.setValue(Updater.userAgent, forHTTPHeaderField: "User-Agent")
        let stored = Updater.cache.load("server")
        if let etag = stored?.etag { req.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        DebugLog.log("update", "asking the exam server \(url.absoluteString)")
        Net.session.dataTask(with: req) { [weak self] data, response, error in
            let http = response as? HTTPURLResponse
            if let t = http?.value(forHTTPHeaderField: "X-Server-Time") { ServerClock.shared.update(serverTime: t) }
            let tag = http?.value(forHTTPHeaderField: "ETag")
            DispatchQueue.main.async {
                guard let self else { return }
                switch http?.statusCode {
                case 200?:
                    guard let data else { self.fallBackToGitHub(immediately: immediately); return }
                    Updater.cache.save("server", etag: tag, body: data)
                    self.finish(release: Updater.parseManifest(json: data, serverHost: Config.serverURL.host))
                case 304?:
                    guard let body = stored?.body else { self.fallBackToGitHub(immediately: immediately); return }
                    self.finish(release: Updater.parseManifest(json: body, serverHost: Config.serverURL.host))
                default:
                    let what = http.map { "HTTP \($0.statusCode)" } ?? "no response (\(error?.localizedDescription ?? "?"))"
                    DebugLog.log("update", "the exam server gave no update answer (\(what)): GitHub next")
                    self.fallBackToGitHub(immediately: immediately)
                }
            }
        }.resume()
    }

    private func fallBackToGitHub(immediately: Bool) {
        let wait = immediately ? 0 : Double.random(in: 0...Updater.githubJitter)
        DebugLog.log("update", "GitHub check in \(Int(wait)) s")
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in self?.askGitHub() }
    }

    private func askGitHub() {
        var req = URLRequest(url: Updater.latestAPI)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        req.setValue(Updater.userAgent, forHTTPHeaderField: "User-Agent")
        // A 304 answer doesn't count against GitHub's hourly limit for this address.
        let stored = Updater.cache.load("github")
        if let etag = stored?.etag { req.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        DebugLog.log("update", "checking \(Updater.latestAPI.absoluteString)")
        checkSession.dataTask(with: req) { [weak self] data, response, error in
            let http = response as? HTTPURLResponse
            let tag = http?.value(forHTTPHeaderField: "ETag")
            DispatchQueue.main.async {
                guard let self else { return }
                var body: Data?
                if http?.statusCode == 200, let data {
                    body = data
                    Updater.cache.save("github", etag: tag, body: data)
                } else if http?.statusCode == 304 {
                    body = stored?.body
                }
                guard let body else {
                    let what = http.map { "HTTP \($0.statusCode)" } ?? "no response (\(error?.localizedDescription ?? "?"))"
                    DebugLog.log("update", "check failed: \(what); next try in about 1 h")
                    self.checking = false
                    self.schedule(after: Updater.jittered(Updater.retryAfterFailure))
                    self.onChecked?()
                    return
                }
                self.finish(release: Updater.parseRelease(json: body))
            }
        }.resume()
    }

    private func finish(release: Release?) {
        checking = false
        schedule(after: Updater.jittered(Updater.checkEvery))
        let found = release.flatMap { Updater.isNewer($0.version, than: Config.version) ? $0 : nil }
        if let found {
            DebugLog.log("update", "FairPlay \(found.version) is available (this is \(Config.version))")
        } else {
            DebugLog.log("update", "up to date (\(Config.version)) or the latest release is not usable")
        }
        if found != available {
            available = found
            onAvailable?()
        }
        onChecked?()
    }

    // MARK: - Parsing and decisions (pure, tested)

    /// "v0.5.4" / "0.5.4" -> "0.5.4"; nil unless it is three or four dot-separated numbers.
    static func version(fromTag tag: String) -> String? {
        let v = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
        return isPlainVersion(v) ? v : nil
    }

    /// ^\d+(\.\d+){2,3}$ with ASCII digits (at most 9 per part).
    static func isPlainVersion(_ s: String) -> Bool {
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        guard (3...4).contains(parts.count) else { return false }
        return parts.allSatisfy { p in
            !p.isEmpty && p.count <= 9 && p.unicodeScalars.allSatisfy { $0.value >= 48 && $0.value <= 57 }
        }
    }

    static func isNewer(_ latest: String, than current: String) -> Bool {
        AppVersion.compare(latest, current) == .orderedDescending
    }

    /// "sha256:<64 hex>" -> lower-case hex; nil for anything else.
    static func sha256(fromDigest digest: String) -> String? {
        let prefix = "sha256:"
        guard digest.lowercased().hasPrefix(prefix) else { return nil }
        let hex = digest.dropFirst(prefix.count).lowercased()
        guard hex.count == 64, hex.unicodeScalars.allSatisfy({ ($0.value >= 48 && $0.value <= 57) || ($0.value >= 97 && $0.value <= 102) })
        else { return nil }
        return String(hex)
    }

    /// Only https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/<tag>/FairPlay-Mac.zip
    /// (GitHub then redirects to its file host; URLSession follows that).
    static func isAllowedAssetURL(_ url: URL, tag: String) -> Bool {
        guard url.scheme?.lowercased() == "https", url.host?.lowercased() == "github.com",
              url.port == nil || url.port == 443, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else { return false }
        return url.path == assetPathPrefix + tag + "/" + assetName
    }

    /// The GitHub "latest release" JSON -> the Mac update in it, or nil when anything is off.
    static func parseRelease(json: Data) -> Release? {
        guard let obj = (try? JSONSerialization.jsonObject(with: json)) as? [String: Any] else { return nil }
        if (obj["draft"] as? Bool) == true || (obj["prerelease"] as? Bool) == true { return nil }
        guard let tag = obj["tag_name"] as? String, let ver = Updater.version(fromTag: tag),
              let assets = obj["assets"] as? [[String: Any]] else { return nil }
        let matches = assets.filter { ($0["name"] as? String) == assetName }
        guard matches.count == 1, let asset = matches.first else { return nil }
        if let state = asset["state"] as? String, state != "uploaded" { return nil }
        guard let raw = asset["browser_download_url"] as? String, let url = URL(string: raw),
              isAllowedAssetURL(url, tag: tag),
              let digest = asset["digest"] as? String, let sha = sha256(fromDigest: digest) else { return nil }
        return Release(version: ver, zipURL: url, sha256: sha)
    }

    /// The exam server's GET /api/app/latest answer -> the Mac update in it, or nil when anything
    /// is off. The file must be on this repo's GitHub release path or on the exam server's own
    /// host, over https, and come with a 64-hex SHA-256 (checked again after the download).
    static func parseManifest(json: Data, serverHost: String?) -> Release? {
        guard let obj = (try? JSONSerialization.jsonObject(with: json)) as? [String: Any] else { return nil }
        if let platform = obj["platform"] as? String, platform != "mac" { return nil }
        guard let ver = obj["version"] as? String, isPlainVersion(ver),
              let raw = obj["url"] as? String, let url = URL(string: raw),
              let shaRaw = obj["sha256"] as? String else { return nil }
        let sha = shaRaw.lowercased()
        guard sha.count == 64, sha.unicodeScalars.allSatisfy({ ($0.value >= 48 && $0.value <= 57) || ($0.value >= 97 && $0.value <= 102) })
        else { return nil }
        let onGitHub = isAllowedAssetURL(url, tag: "v" + ver) || isAllowedAssetURL(url, tag: ver)
        let onServer = url.scheme?.lowercased() == "https" && serverHost != nil
            && url.host?.lowercased() == serverHost?.lowercased() && url.user == nil && url.password == nil
        guard onGitHub || onServer else { return nil }
        return Release(version: ver, zipURL: url, sha256: sha)
    }

    /// Lower-case hex SHA-256 of a file, read in 1 MB pieces.
    static func sha256Hex(of file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func fileMatches(_ file: URL, sha256 expected: String) -> Bool {
        guard let got = try? sha256Hex(of: file) else { return false }
        return got == expected.lowercased()
    }

    /// The unpacked zip must hold exactly FairPlay.app (a real folder, not a link). A
    /// "__MACOSX" folder (resource forks from `ditto --sequesterRsrc`) is ignored.
    static func singleApp(in folder: URL) -> URL? {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.isSymbolicLinkKey, .isDirectoryKey])
        else { return nil }
        let rest = items.filter { $0.lastPathComponent != "__MACOSX" }
        guard rest.count == 1, let app = rest.first, app.lastPathComponent == appName,
              let v = try? app.resourceValues(forKeys: [.isSymbolicLinkKey, .isDirectoryKey]),
              v.isSymbolicLink != true, v.isDirectory == true else { return nil }
        return app
    }

    static func readInfo(_ app: URL) -> [String: Any]? {
        let url = app.appendingPathComponent("Contents/Info.plist")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any]
    }

    static func infoMatches(_ info: [String: Any]?, bundleId: String, version: String) -> Bool {
        guard let info, !bundleId.isEmpty else { return false }
        return (info["CFBundleIdentifier"] as? String) == bundleId
            && (info["CFBundleShortVersionString"] as? String) == version
    }

    /// True when this bundle can be swapped in place (not a translocated or read-only copy).
    static func isReplaceable(_ bundle: URL) -> Bool {
        let fm = FileManager.default
        let path = bundle.path
        guard !path.contains("/AppTranslocation/"), fm.fileExists(atPath: path) else { return false }
        return fm.isWritableFile(atPath: bundle.deletingLastPathComponent().path) && fm.isWritableFile(atPath: path)
    }

    // MARK: - Installing

    /// {action:"installUpdate"} from the page. `send` posts a message to the page.
    func install(send: @escaping ([String: Any]) -> Void) {
        let fail: (Failure) -> Void = { [weak self] reason in
            DebugLog.log("update", "install FAILED: \(reason.rawValue)")
            self?.installing = false
            if reason == .notWritable { NSWorkspace.shared.open(Updater.releasePage) }
            send(["type": "updateFailed", "reason": reason.rawValue])
        }
        DebugLog.log("update", "install requested")
        guard !Config.isDevBuild else { fail(.download); return }
        guard Updater.mayUpdateNow(locked: LockdownController.shared.isLocked || LockdownController.shared.nativeLockActive, examBound: AppSession.shared.exam != nil,
                                   devicePreview: Updater.devicePreview) else { fail(.locked); return }
        guard !installing else {
            DebugLog.log("update", "install already running")
            send(["type": "updateFailed", "reason": Failure.busy.rawValue])
            return
        }
        guard let release = available else { fail(.download); return }
        let target = Permission.thisCopy
        guard Updater.isReplaceable(target) else {
            DebugLog.log("update", "this copy can't be replaced: \(target.path)")
            fail(.notWritable)
            return
        }
        installing = true
        send(["type": "updateProgress", "stage": "downloading"])
        DebugLog.log("update", "downloading \(release.zipURL.absoluteString)")

        var req = URLRequest(url: release.zipURL)
        req.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        req.setValue(Updater.userAgent, forHTTPHeaderField: "User-Agent")
        downloadSession.downloadTask(with: req) { [weak self] tmp, response, error in
            // Runs on the session's queue; the system deletes `tmp` when this returns.
            let http = response as? HTTPURLResponse
            guard let tmp, http?.statusCode == 200, response?.url?.scheme?.lowercased() == "https" else {
                let what = http.map { "HTTP \($0.statusCode)" } ?? (error?.localizedDescription ?? "no response")
                DebugLog.log("update", "download failed: \(what)")
                DispatchQueue.main.async { fail(.download) }
                return
            }
            let fm = FileManager.default
            let work = fm.temporaryDirectory.appendingPathComponent("FairPlay-update-\(UUID().uuidString)", isDirectory: true)
            let zip = work.appendingPathComponent(Updater.assetName)
            do {
                try fm.createDirectory(at: work, withIntermediateDirectories: true)
                try fm.moveItem(at: tmp, to: zip)
            } catch {
                DebugLog.log("update", "could not keep the download: \(error.localizedDescription)")
                try? fm.removeItem(at: work)
                DispatchQueue.main.async { fail(.download) }
                return
            }
            DispatchQueue.main.async { send(["type": "updateProgress", "stage": "installing"]) }
            let result = Updater.prepare(zip: zip, release: release, target: target)
            try? fm.removeItem(at: work)
            DispatchQueue.main.async {
                switch result {
                case .failure(let reason): fail(reason)
                case .success(let (app, folder)):
                    self?.replace(target: target, with: app, folder: folder, fail: fail)
                }
            }
        }.resume()
    }

    /// Off the main thread: verify, unpack and check the new copy. Returns the new app and
    /// the folder holding it (removed by the caller when done).
    private static func prepare(zip: URL, release: Release, target: URL) -> Result<(URL, URL), Failure> {
        guard fileMatches(zip, sha256: release.sha256) else {
            DebugLog.log("update", "SHA-256 does NOT match the release digest")
            return .failure(.verify)
        }
        DebugLog.log("update", "SHA-256 matches")
        let fm = FileManager.default
        // Unpack next to the running copy's volume when possible, so the final move is a rename.
        let base = (try? fm.url(for: .itemReplacementDirectory, in: .userDomainMask, appropriateFor: target, create: true))
            ?? fm.temporaryDirectory
        let folder = base.appendingPathComponent("FairPlay-new-\(UUID().uuidString)", isDirectory: true)
        let failed: (String) -> Result<(URL, URL), Failure> = { why in
            DebugLog.log("update", why)
            try? fm.removeItem(at: folder)
            return .failure(.verify)
        }
        do { try fm.createDirectory(at: folder, withIntermediateDirectories: true) } catch {
            return failed("could not make the unpack folder: \(error.localizedDescription)")
        }
        let unzip = run("/usr/bin/ditto", ["-x", "-k", zip.path, folder.path])
        guard unzip.status == 0 else { return failed("ditto failed (\(unzip.status)): \(unzip.output)") }
        guard let app = singleApp(in: folder) else { return failed("the zip does not hold exactly \(appName)") }
        guard infoMatches(readInfo(app), bundleId: Bundle.main.bundleIdentifier ?? "", version: release.version) else {
            return failed("Info.plist bundle id or version does not match \(release.version)")
        }
        let verify = run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path])
        guard verify.status == 0 else { return failed("codesign --verify failed: \(verify.output)") }
        guard sameSigner(app) else { return failed("the new copy is not signed like this one") }
        DebugLog.log("update", "new copy verified: \(release.version)")
        return .success((app, folder))
    }

    /// Ad-hoc signed copies have no signer to keep. A copy signed with a certificate only
    /// accepts a new copy that satisfies its own designated requirement.
    private static func sameSigner(_ app: URL) -> Bool {
        var me: SecCode?
        guard SecCodeCopySelf([], &me) == errSecSuccess, let me else { return false }
        var mine: SecStaticCode?
        guard SecCodeCopyStaticCode(me, [], &mine) == errSecSuccess, let mine else { return false }
        var info: CFDictionary?
        // 1 << 1 = kSecCSSigningInformation
        guard SecCodeCopySigningInformation(mine, SecCSFlags(rawValue: 1 << 1), &info) == errSecSuccess,
              let dict = info as? [String: Any] else { return false }
        let flags = (dict[kSecCodeInfoFlags as String] as? NSNumber)?.uint32Value ?? 0
        if flags & 0x0002 != 0 {   // kSecCodeSignatureAdhoc
            DebugLog.log("update", "this copy is ad-hoc signed: no signer requirement")
            return true
        }
        var req: SecRequirement?
        guard SecCodeCopyDesignatedRequirement(mine, [], &req) == errSecSuccess, let req else { return false }
        var candidate: SecStaticCode?
        guard SecStaticCodeCreateWithPath(app as CFURL, [], &candidate) == errSecSuccess, let candidate else { return false }
        // kSecCSCheckAllArchitectures | kSecCSCheckNestedCode | kSecCSStrictValidate
        let check = SecCSFlags(rawValue: (1 << 0) | (1 << 3) | (1 << 4))
        let status = SecStaticCodeCheckValidity(candidate, check, req)
        DebugLog.log("update", "designated requirement check: \(status == errSecSuccess ? "ok" : "FAILED (\(status))")")
        return status == errSecSuccess
    }

    /// Main thread: swap the bundles, then start the new copy and quit.
    private func replace(target: URL, with app: URL, folder: URL, fail: @escaping (Failure) -> Void) {
        let fm = FileManager.default
        defer { try? fm.removeItem(at: folder) }
        // The download took a while: an exam may have started meanwhile.
        guard !LockdownController.shared.isLocked, AppSession.shared.exam == nil else { fail(.locked); return }
        guard Updater.isReplaceable(target) else { fail(.notWritable); return }
        var trashed: NSURL?
        do {
            try fm.trashItem(at: target, resultingItemURL: &trashed)
        } catch {
            DebugLog.log("update", "could not move the old copy to the Trash: \(error.localizedDescription)")
            fail(.notWritable)
            return
        }
        DebugLog.log("update", "old copy moved to \((trashed as URL?)?.path ?? "the Trash")")
        do {
            try fm.moveItem(at: app, to: target)
        } catch {
            DebugLog.log("update", "could not put the new copy in place: \(error.localizedDescription); restoring")
            if fm.fileExists(atPath: target.path) { try? fm.removeItem(at: target) }
            if let back = trashed as URL? {
                do { try fm.moveItem(at: back, to: target) } catch {
                    DebugLog.log("update", "RESTORE FAILED: \(error.localizedDescription)")
                }
            }
            fail(.notWritable)
            return
        }
        DebugLog.log("update", "new copy in place at \(target.path); relaunching")
        relaunch(target)
    }

    private func relaunch(_ app: URL) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-n", app.path]
        do {
            try p.run()
            // Wait here (main thread) so nothing else, such as a lock request, runs before we quit.
            p.waitUntilExit()
        } catch {
            DebugLog.log("update", "open -n failed: \(error.localizedDescription)")
            NSWorkspace.shared.open(app)
        }
        NSApp.terminate(nil)
    }

    /// Runs a tool and returns its exit status and combined output (trimmed).
    static func run(_ exe: String, _ args: [String]) -> (status: Int32, output: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (-1, error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return (p.terminationStatus, String(text.prefix(600)))
    }
}

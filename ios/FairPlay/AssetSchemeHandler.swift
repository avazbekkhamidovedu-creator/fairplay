import Foundation
import WebKit
import FairPlayCore

/// 2026-10-23: the asset pack inside this app (the app-assets folder of the bundle, see
/// FairPlayCore AssetPack and the Xcode "Asset pack" build phase). Read once at launch.
enum BundledAssets {
    /// The pack folder, when this build has one (its manifest.json is there).
    static let root: URL? = {
        guard let dir = Bundle.main.resourceURL?.appendingPathComponent(AssetPack.folderName, isDirectory: true),
              FileManager.default.fileExists(atPath: dir.appendingPathComponent(AssetPack.manifestName).path)
        else { return nil }
        return dir
    }()

    /// manifest.json "version"; nil without a pack (then the page gets no script and loads
    /// the files from the server as before).
    static let version: String? = {
        guard let root, let data = try? Data(contentsOf: root.appendingPathComponent(AssetPack.manifestName))
        else { return nil }
        // 0.6.2 review: a manifest without files serves nothing, so don't tell the page to try it
        guard let files = AssetPack.manifestFiles(data), !files.isEmpty else { return nil }
        return AssetPack.manifestVersion(data)
    }()

    /// The files the manifest lists: the only ones served (0.6.2 review). A pack whose
    /// manifest has no usable list serves nothing (the page then uses the server's copy).
    static let files: Set<String>? = {
        guard let root else { return nil }
        guard let data = try? Data(contentsOf: root.appendingPathComponent(AssetPack.manifestName)) else { return [] }
        return AssetPack.manifestFiles(data) ?? []
    }()
}

/// Tasks WebKit has started and not stopped. Checked on the main queue right before every
/// call on a task (WebKit calls start/stop there too), so nothing is sent after stop.
final class AssetTaskRegistry: @unchecked Sendable {
    private let lock = NSLock()
    private var active = Set<ObjectIdentifier>()

    func insert(_ id: ObjectIdentifier) { lock.lock(); active.insert(id); lock.unlock() }
    func remove(_ id: ObjectIdentifier) { lock.lock(); active.remove(id); lock.unlock() }
    func contains(_ id: ObjectIdentifier) -> Bool { lock.lock(); defer { lock.unlock() }; return active.contains(id) }
}

/// Serves fairplay-asset://bundle/<path> from the bundled pack to the exam web view
/// (subresources, workers, fetch). Navigations to the scheme are refused by the
/// navigation delegate. Files are read off the main thread in chunks.
final class AssetSchemeHandler: NSObject, WKURLSchemeHandler {
    private let root: URL?
    private let allowed: Set<String>?
    private let registry = AssetTaskRegistry()
    private static let readQueue = DispatchQueue(label: "fairplay.assets.read", qos: .userInitiated)
    private static let chunkSize = 512 * 1024

    init(root: URL? = BundledAssets.root, allowed: Set<String>? = BundledAssets.files) {
        self.root = root
        self.allowed = allowed
        super.init()
    }

    func webView(_ webView: WKWebView, start urlSchemeTask: WKURLSchemeTask) {
        let task = urlSchemeTask
        let id = ObjectIdentifier(task as AnyObject)
        let registry = self.registry
        registry.insert(id)
        guard let url = task.request.url else {
            registry.remove(id)
            task.didFailWithError(URLError(.badURL))
            return
        }
        switch AssetPack.reply(method: task.request.httpMethod, url: url, root: root, allowed: allowed) {
        case .preflight:
            finish(task, id: id, url: url, status: 204, headers: AssetPack.preflightHeaders)
        case .methodNotAllowed:
            var h = AssetPack.notFoundHeaders
            h["Allow"] = "GET, HEAD, OPTIONS"
            finish(task, id: id, url: url, status: 405, headers: h)
        case .notFound:
            DebugLog.log("assets", "not in the pack: \(url.path)")
            finish(task, id: id, url: url, status: 404, headers: AssetPack.notFoundHeaders)
        case .file(let file, let mime, let bodyless):
            let size = ((try? FileManager.default.attributesOfItem(atPath: file.path))?[.size] as? NSNumber)?.intValue ?? 0
            guard let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1",
                                                 headerFields: AssetPack.headers(mime: mime, length: size)) else {
                registry.remove(id)
                task.didFailWithError(URLError(.cannotParseResponse))
                return
            }
            task.didReceive(response)
            if bodyless || size == 0 {
                registry.remove(id)
                task.didFinish()
                return
            }
            let chunk = AssetSchemeHandler.chunkSize
            AssetSchemeHandler.readQueue.async {
                guard let handle = try? FileHandle(forReadingFrom: file) else {
                    DispatchQueue.main.async {
                        guard registry.contains(id) else { return }
                        registry.remove(id)
                        task.didFailWithError(URLError(.cannotOpenFile))
                    }
                    return
                }
                defer { try? handle.close() }
                while registry.contains(id) {
                    let data: Data
                    do {
                        data = try handle.read(upToCount: chunk) ?? Data()
                    } catch {
                        DispatchQueue.main.async {
                            guard registry.contains(id) else { return }
                            registry.remove(id)
                            task.didFailWithError(URLError(.cannotOpenFile))
                        }
                        return
                    }
                    if data.isEmpty {
                        DispatchQueue.main.async {
                            guard registry.contains(id) else { return }
                            registry.remove(id)
                            task.didFinish()
                        }
                        return
                    }
                    DispatchQueue.main.async {
                        guard registry.contains(id) else { return }
                        task.didReceive(data)
                    }
                }
            }
        }
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {
        registry.remove(ObjectIdentifier(urlSchemeTask as AnyObject))
    }

    /// A response with no body, then done.
    private func finish(_ task: WKURLSchemeTask, id: ObjectIdentifier, url: URL, status: Int, headers: [String: String]) {
        registry.remove(id)
        if let r = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers) {
            task.didReceive(r)
            task.didFinish()
        } else {
            task.didFailWithError(URLError(.cannotParseResponse))
        }
    }
}

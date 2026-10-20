import Foundation
import os
import FairPlayCore

/// Dev trace (Xcode console / `log stream`). Never typed text, codes, tokens or answers.
enum DebugLog {
    private static let logger = Logger(subsystem: "ai.proctora.student.ipad", category: "fairplay")

    static func log(_ area: String, _ text: String) {
        #if FAIRPLAY_DEV
        logger.notice("[\(area, privacy: .public)] \(text, privacy: .public)")
        #endif
    }
}

/// Integrity events: kept in Application Support/FairPlay/events.log on the iPad and
/// forwarded to the exam page (which sends them to the server) or, during a website
/// exam, by the app itself. Same throttle as the Mac app.
@MainActor
final class EventLog {
    static let shared = EventLog()

    /// Called for every event that goes out.
    var onEvent: ((_ type: String, _ detail: String, _ time: String) -> Void)?

    private let url: URL
    private var throttle = EventThrottle()
    private let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private let io = DispatchQueue(label: "fairplay.eventlog")

    private init() {
        let fm = FileManager.default
        var folder = (fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fm.temporaryDirectory)
            .appendingPathComponent("FairPlay", isDirectory: true)
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? folder.setResourceValues(values)
        url = folder.appendingPathComponent("events.log")
    }

    func record(_ type: String, _ detail: String = "") {
        let at = Date()
        let now = iso.string(from: at)
        let line = "\(now) \(type) \(detail)\n"
        let url = self.url
        io.async {
            guard let data = line.data(using: .utf8) else { return }
            if let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.uint64Value,
               size > 5_000_000 {
                let old = url.appendingPathExtension("1")
                try? FileManager.default.removeItem(at: old)
                try? FileManager.default.moveItem(at: url, to: old)
            }
            if let h = try? FileHandle(forWritingTo: url) {
                h.seekToEndOfFile(); h.write(data); try? h.close()
            } else {
                try? data.write(to: url, options: [.completeFileProtectionUntilFirstUserAuthentication])
            }
        }
        DebugLog.log("event", "\(type) \(detail)")
        for (t, d) in throttle.decide(type: type, detail: detail, at: at) { onEvent?(t, d, now) }
    }
}

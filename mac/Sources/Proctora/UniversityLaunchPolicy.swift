import Foundation

/// Disabled integration building block, not an authorization or question-content gate.
/// No bridge action installs this policy. A future authenticated FairPlay profile
/// provider must establish LMS-side admission enforcement before enabling browsing.
struct UniversityLaunchPolicy {
    struct Launch {
        let url: URL
        let examId: String
    }

    enum Decision: Equatable {
        case browse
        case activate(URL)
        case block
    }

    private let fairplayOrigin: String
    private let browseOrigins: Set<String>
    private let launches: [Launch]

    /// Configuration is useful only after its source and adapter readiness are verified
    /// outside this type. A page-supplied profile must never reach this initializer.
    init?(fairplay: URL, browseOrigins: [URL], launches: [Launch]) {
        guard let server = Self.origin(fairplay), fairplay.path.isEmpty || fairplay.path == "/",
              !browseOrigins.isEmpty, browseOrigins.count <= 50,
              !launches.isEmpty, launches.count <= 1000 else { return nil }
        var origins = Set<String>()
        for url in browseOrigins {
            guard let value = Self.origin(url), url.path.isEmpty || url.path == "/",
                  value != server else { return nil }
            origins.insert(value)
        }
        var seen = Set<String>()
        for launch in launches {
            guard let origin = Self.origin(launch.url), origins.contains(origin),
                  let parts = Self.canonicalSegments(launch.url), parts.count == 4,
                  parts[0] == "courses", CanvasScope.isId(parts[1]),
                  ["quizzes", "assignments"].contains(parts[2]), CanvasScope.isId(parts[3]),
                  !launch.examId.isEmpty, launch.examId.count <= 128,
                  launch.examId.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0)
                      || (97...122).contains($0) || $0 == 45 || $0 == 95 }),
                  seen.insert(origin + "/" + parts.joined(separator: "/")).inserted else { return nil }
        }
        self.fairplayOrigin = server
        self.browseOrigins = origins
        self.launches = launches
    }

    /// Cancel protected navigations first, then load the returned FairPlay preflight
    /// URL. The existing session -> lock -> openExternal flow alone releases the quiz.
    /// This policy does not inspect fetch/XHR, cached content, or SPA-rendered questions.
    func decide(_ url: URL, mainFrame: Bool) -> Decision {
        guard let origin = Self.origin(url, allowQuery: true), browseOrigins.contains(origin),
              let parts = Self.canonicalSegments(url) else { return .block }
        for launch in launches {
            guard Self.origin(launch.url) == origin,
                  let target = Self.canonicalSegments(launch.url),
                  parts.starts(with: target) else { continue }
            guard mainFrame,
                  let preflight = URL(string: fairplayOrigin + "/student/exams/" + launch.examId + "/start")
            else { return .block }
            return .activate(preflight)
        }
        // A newly published or unconfigured exam is not silently treated as ordinary browsing.
        if parts.contains("quizzes") || parts.contains("assignments") || parts.first == "api" {
            return .block
        }
        return mainFrame ? .browse : .block
    }

    /// The pilot accepts literal route segments only. Encoded letters can conceal
    /// quizzes/assignments/API routes from string matching; decoding once also misses
    /// multiply encoded values. Reject all encoded paths rather than guess how an LMS
    /// or intermediary will normalize them. Queries do not participate in routing.
    private static func canonicalSegments(_ url: URL) -> [String]? {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              !components.percentEncodedPath.contains("%") else { return nil }
        return CanvasNavigation.segments(url)
    }

    private static func origin(_ url: URL, allowQuery: Bool = false) -> String? {
        guard url.scheme?.lowercased() == "https", url.user == nil, url.password == nil,
              let host = url.host?.lowercased(), CanvasScope.isHostPattern(host),
              !host.contains("*"), url.port == nil || url.port == 443,
              allowQuery || (url.query == nil && url.fragment == nil) else { return nil }
        return "https://" + host
    }
}

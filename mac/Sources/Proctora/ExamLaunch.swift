import Foundation

enum ExamLaunch {
    static func key(_ value: String) -> String? {
        guard (1...128).contains(value.utf8.count), value.utf8.allSatisfy({
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
        }) else { return nil }
        return value
    }

    static func parse(_ url: URL) -> String? {
        guard url.absoluteString.utf8.count <= 512,
              let c = URLComponents(url: url, resolvingAgainstBaseURL: false),
              c.scheme?.lowercased() == "fairplay", c.host?.lowercased() == "launch",
              c.user == nil, c.password == nil, c.port == nil, (c.path.isEmpty || c.path == "/"),
              c.fragment == nil, let items = c.queryItems, items.count == 2,
              items.filter({ $0.name == "v" && $0.value == "1" }).count == 1,
              items.filter({ $0.name == "exam" }).count == 1,
              let exam = items.first(where: { $0.name == "exam" })?.value else { return nil }
        return key(exam)
    }

    static func parseFile(_ data: Data) -> String? {
        struct Payload: Decodable { let version: Int; let exam_key: String }
        guard data.count <= 4096,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["version", "exam_key"]),
              let payload = try? JSONDecoder().decode(Payload.self, from: data), payload.version == 1 else { return nil }
        return key(payload.exam_key)
    }

    static func mayOpen(locked: Bool, prelocked: Bool, bound: Bool, external: Bool, preview: Bool) -> Bool {
        !locked && !prelocked && !bound && !external && !preview
    }
}

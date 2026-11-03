import Testing
import Foundation
@testable import Proctora

@Suite struct UniversityLaunchPolicyTests {
    private let server = URL(string: "https://exam.example.com")!
    private let portal = URL(string: "https://canvas.example.edu")!
    private let item = URL(string: "https://canvas.example.edu/courses/123/quizzes/456")!

    @Test func protectedNavigationIsCancelledForTrustedPreflight() throws {
        let policy = try #require(UniversityLaunchPolicy(fairplay: server, browseOrigins: [portal],
                                                        launches: [.init(url: item, examId: "e_1")]))
        let activation = UniversityLaunchPolicy.Decision.activate(
            URL(string: "https://exam.example.com/student/exams/e_1/start")!)
        #expect(policy.decide(item, mainFrame: true) == activation)
        #expect(policy.decide(URL(string: item.absoluteString + "/take?attempt=1")!, mainFrame: true) == activation)
        #expect(policy.decide(item, mainFrame: false) == .block)
        #expect(policy.decide(portal, mainFrame: true) == .browse)
        for raw in ["https://canvas.example.edu/courses/123/quizzes/4567",
                    "https://canvas.example.edu/courses/999/quizzes/456",
                    "https://canvas.example.edu/api/v1/courses/123/quizzes/456",
                    "https://canvas.example.edu/courses/123/%71uizzes/456",
                    "https://canvas.example.edu/courses/123/%61ssignments/456",
                    "https://canvas.example.edu/%61pi/v1/courses/123/quizzes/456",
                    "https://canvas.example.edu/%2561pi/v1/courses/123/quizzes/456",
                    "https://canvas.example.edu/courses/123/%2571uizzes/456",
                    "https://canvas.example.edu/courses/%31%32%33/quizzes/456",
                    "https://canvas.example.edu/courses/123/quizzes/456/../789",
                    "https://canvas.example.edu.evil.com/", "http://canvas.example.edu/",
                    "https://canvas.example.edu:8443/", "https://user:pw@canvas.example.edu/",
                    "https://exam.example.com/student", "javascript:alert(1)"] {
            #expect(policy.decide(URL(string: raw)!, mainFrame: true) == .block, "\(raw)")
        }
    }

    @Test func malformedAndAmbiguousProfilesFailClosed() {
        for examId in ["", "../teacher", "a/b", "a?x=1", "a#fragment", String(repeating: "a", count: 129)] {
            #expect(UniversityLaunchPolicy(fairplay: server, browseOrigins: [portal],
                                          launches: [.init(url: item, examId: examId)]) == nil)
        }
        #expect(UniversityLaunchPolicy(fairplay: server, browseOrigins: [server],
                                      launches: [.init(url: item, examId: "e1")]) == nil)
        #expect(UniversityLaunchPolicy(fairplay: server, browseOrigins: [portal], launches: []) == nil)
        #expect(UniversityLaunchPolicy(fairplay: server, browseOrigins: [portal],
                                      launches: [.init(url: item, examId: "e1"), .init(url: item, examId: "e2")]) == nil)
        #expect(UniversityLaunchPolicy(fairplay: server, browseOrigins: [portal],
                                      launches: [.init(url: URL(string: "https://canvas.example.edu/courses/123/%71uizzes/456")!,
                                                       examId: "e1")]) == nil)
    }
}

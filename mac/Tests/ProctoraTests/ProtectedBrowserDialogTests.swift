import Testing
import AppKit
@testable import Proctora

@Suite struct ProtectedBrowserDialogSessionTests {
    @Test func explicitConfirmationCompletesExactlyOnce() throws {
        let session = ProtectedBrowserDialogSession()
        var results: [ProtectedBrowserDialogSession.Result] = []
        let id = session.begin(.confirm) { results.append($0) }
        #expect(results.isEmpty)
        session.finish(id, result: .confirmed(true))
        session.finish(id, result: .confirmed(false))
        session.cancel()
        #expect(results == [.confirmed(true)])
        #expect(session.activeID == nil)
    }

    @Test func cancellationNeverApprovesConfirmOrPrompt() throws {
        for (kind, expected) in [(ProtectedBrowserDialogSession.Kind.alert, ProtectedBrowserDialogSession.Result.acknowledged),
                                 (.confirm, .confirmed(false)), (.prompt, .text(nil))] {
            let session = ProtectedBrowserDialogSession()
            var results: [ProtectedBrowserDialogSession.Result] = []
            let id = session.begin(kind) { results.append($0) }
            session.cancel()
            session.finish(id, result: .confirmed(true)) // stale sheet callback after leave/blocker
            #expect(results == [expected])
        }
    }

    @Test func secondDialogWaitsInOrderAndIsNeverAutoCancelled() {
        let session = ProtectedBrowserDialogSession()
        var first: [ProtectedBrowserDialogSession.Result] = []
        var second: [ProtectedBrowserDialogSession.Result] = []
        var activated: [UUID] = []
        session.onActivate = { activated.append($0) }
        let a = session.begin(.prompt) { first.append($0) }
        let b = session.begin(.confirm) { second.append($0) }
        #expect(session.activeID == a)
        #expect(second.isEmpty)
        session.finish(b, result: .confirmed(true))
        #expect(second.isEmpty)
        session.finish(a, result: .text("student-entered answer"))
        #expect(first == [.text("student-entered answer")])
        #expect(activated == [b] && session.activeID == b)
        session.finish(b, result: .confirmed(true))
        #expect(second == [.confirmed(true)])
        #expect(session.activeID == nil)
    }

    @Test func cancelAnswersEveryQueuedDialogNegatively() {
        let session = ProtectedBrowserDialogSession()
        var results: [ProtectedBrowserDialogSession.Result] = []
        session.begin(.confirm) { results.append($0) }
        session.begin(.prompt) { results.append($0) }
        session.cancel()
        #expect(results == [.confirmed(false), .text(nil)])
        #expect(session.activeID == nil)
    }

    @Test func completionMayStartNextDialogWithoutLosingIt() throws {
        let session = ProtectedBrowserDialogSession()
        var next: UUID?
        let id = session.begin(.alert) { _ in next = session.begin(.confirm) { _ in } }
        session.finish(id, result: .acknowledged)
        #expect(next != nil)
        #expect(session.activeID == next)
        session.cancel()
    }
}

@MainActor @Suite struct ProtectedBrowserDialogUITests {
    @Test func missingWindowRefusesDialog() {
        let dialogs = ProtectedBrowserDialogs()
        var results: [ProtectedBrowserDialogSession.Result] = []
        dialogs.present(.confirm, message: "Submit?", origin: "https://university.example.edu", in: nil,
                        stillAllowed: { true }, leave: {}, completion: { results.append($0) })
        #expect(results == [.confirmed(false)])
    }

    @Test func nativeConfirmSheetReturnsExplicitChoice() async {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 480),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let dialogs = ProtectedBrowserDialogs()
        for response in [NSApplication.ModalResponse.alertFirstButtonReturn, .alertSecondButtonReturn] {
            let result: ProtectedBrowserDialogSession.Result = await withCheckedContinuation { continuation in
                dialogs.present(.confirm, message: "Submit this test answer?", origin: "https://university.example.edu",
                                in: window, stillAllowed: { true }, leave: {}, completion: { continuation.resume(returning: $0) })
                if let sheet = window.attachedSheet { window.endSheet(sheet, returnCode: response) }
                else { dialogs.cancel() }
            }
            #expect(result == .confirmed(response == .alertFirstButtonReturn))
        }
        window.close()
    }

    @Test func nativePromptCanBeCancelledByLockLoss() async {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 480),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let dialogs = ProtectedBrowserDialogs()
        let result: ProtectedBrowserDialogSession.Result = await withCheckedContinuation { continuation in
            dialogs.present(.prompt, message: "Answer", defaultText: "initial", origin: "https://university.example.edu",
                            in: window, stillAllowed: { true }, leave: {}, completion: { continuation.resume(returning: $0) })
            dialogs.cancel()
        }
        #expect(result == .text(nil))
        #expect(window.attachedSheet == nil)
        window.close()
    }
}

import Foundation
import UIKit
@preconcurrency import AutomaticAssessmentConfiguration
import FairPlayCore

/// Layer 1: Apple's Automatic Assessment Configuration (the system mode College Board's
/// Bluebook uses on iPad). While active, iPadOS itself keeps FairPlay the only app:
/// no Home / app switcher / Control Center / Spotlight / Siri, no notifications,
/// no screenshots or screen recording, no AirPlay, no Universal Clipboard / Handoff.
/// We also turn off everything the configuration exposes: autocorrect, spell check,
/// predictive text, keyboard shortcuts, dictation, Speak Selection, Password AutoFill,
/// slide-to-type and (iPadOS 26) typing feedback and the emoji keyboard.
///
/// Teacher-allowed apps (e.g. Calculator) become Assessment Mode participants without
/// network access (iPadOS 17.5+), so the student can switch only to them.
///
/// Needs the com.apple.developer.automatic-assessment-configuration entitlement (Apple
/// grants it on request) in the provisioning profile.
///
/// Review M3: exactly one session is ever pending or active. Callbacks from any other
/// session (a begin that timed out, a replaced one) are ignored, and such a session is
/// ended if it comes up, so the iPad can never stay in Assessment Mode after unlock.
@MainActor
final class AssessmentMode: NSObject, AEAssessmentSessionDelegate {

    /// The session that is on.
    private var active: AEAssessmentSession?
    /// The session whose begin() we are waiting for, and who waits for it.
    private var pending: AEAssessmentSession?
    private var waiters: [(Error?) -> Void] = []
    /// Sessions we gave up on (timeout) or replaced: ended as soon as they come up.
    private var abandoned: [AEAssessmentSession] = []
    private var ending = false

    var isActive: Bool { active?.isActive ?? false }
    var isPending: Bool { pending != nil }
    /// iPadOS ended Assessment Mode on its own (it can't be ended by the student).
    var onInterrupted: ((String) -> Void)?
    /// An abandoned (timed-out) begin came up after all. Return true to keep it.
    var onLateBegin: (() -> Bool)?

    /// iPadOS asks the student to allow Assessment Mode and gives up after 60 s itself.
    static let beginTimeout: TimeInterval = 75

    static func configuration(participants: [String]) -> AEAssessmentConfiguration {
        let c = AEAssessmentConfiguration()
        c.autocorrectMode = []
        c.allowsSpellCheck = false
        c.allowsPredictiveKeyboard = false
        c.allowsKeyboardShortcuts = false
        c.allowsActivityContinuation = false
        c.allowsDictation = false
        c.allowsAccessibilitySpeech = false
        c.allowsPasswordAutoFill = false
        c.allowsContinuousPathKeyboard = false
        if #available(iOS 26.0, *) { c.allowsAccessibilityTypingFeedback = false }
        // iPadOS 26.4 setting, by name (like writingToolsBehavior in ExamViewController):
        // the 26.2 SDK in GitHub's newest runner Xcode (26.3) doesn't declare it yet.
        if #available(iOS 26.4, *), c.responds(to: NSSelectorFromString("setAllowsEmojiKeyboard:")) {
            c.setValue(false, forKey: "allowsEmojiKeyboard")
        }
        c.mainParticipantConfiguration.allowsNetworkAccess = true     // the exam server
        if AEAssessmentSession.supportsMultipleParticipants {
            for id in participants {
                let p = AEAssessmentParticipantConfiguration()
                p.allowsNetworkAccess = false
                c.setConfiguration(p, for: AEAssessmentApplication(bundleIdentifier: id))
            }
        }
        return c
    }

    /// Completion: nil = Assessment Mode is on. A begin already in flight is joined,
    /// never doubled (the consent prompt itself makes the app inactive/active).
    func begin(participants: [String], completion: @escaping (Error?) -> Void) {
        if isActive { completion(nil); return }
        waiters.append(completion)
        guard pending == nil else { return }
        let s = AEAssessmentSession(configuration: AssessmentMode.configuration(participants: participants))
        s.delegate = self
        pending = s
        ending = false
        DebugLog.log("lock", "Assessment Mode: begin (participants: \(participants.joined(separator: ", ")))")
        s.begin()
        DispatchQueue.main.asyncAfter(deadline: .now() + AssessmentMode.beginTimeout) { [weak self] in
            guard let self, self.pending === s else { return }
            self.pending = nil
            self.abandoned.append(s)
            self.finish(NSError(domain: "FairPlay", code: 1,
                                userInfo: [NSLocalizedDescriptionKey: "Assessment Mode did not start"]))
        }
    }

    private func finish(_ error: Error?) {
        let w = waiters
        waiters.removeAll()
        w.forEach { $0(error) }
    }

    /// Ends every session we know about. One whose begin is still in flight stays in
    /// `abandoned` (re-review N2) so it is ended the moment it comes up; sessions leave
    /// that list only when iPadOS reports them ended or failed.
    func end() {
        ending = true
        if let a = active, a.isActive { a.end() }
        if let a = active, !abandoned.contains(where: { $0 === a }) { abandoned.append(a) }
        active = nil
        if let p = pending {
            pending = nil
            abandoned.append(p)
            finish(NSError(domain: "FairPlay", code: 2, userInfo: [NSLocalizedDescriptionKey: "cancelled"]))
        }
        for s in abandoned where s.isActive { s.end() }
    }

    /// No session is on, pending, or still winding down: a new begin won't collide.
    var isIdle: Bool { active == nil && pending == nil && !abandoned.contains { $0.isActive } }

    nonisolated func assessmentSessionDidBegin(_ session: AEAssessmentSession) {
        DispatchQueue.main.async { self.didBegin(session) }
    }

    private func didBegin(_ s: AEAssessmentSession) {
        if s === pending {
            DebugLog.log("lock", "Assessment Mode: ON")
            pending = nil
            active = s
            finish(nil)
        } else if let i = abandoned.firstIndex(where: { $0 === s }) {
            abandoned.remove(at: i)
            if active == nil, !ending, onLateBegin?() == true {
                DebugLog.log("lock", "Assessment Mode: ON (late)")
                active = s
            } else {
                DebugLog.log("lock", "Assessment Mode: late session not wanted, ending it")
                s.end()
            }
        } else if s !== active {
            s.end()
        }
    }

    nonisolated func assessmentSession(_ session: AEAssessmentSession, failedToBeginWithError error: Error) {
        let text = (error as NSError).localizedDescription
        DispatchQueue.main.async {
            self.abandoned.removeAll { $0 === session }
            guard session === self.pending else { return }
            DebugLog.log("lock", "Assessment Mode: failed to begin: \(text)")
            self.pending = nil
            self.finish(error)
        }
    }

    /// Re-review N1: an interrupted session stays active until WE end it (Apple's
    /// contract). End it, keep it in `abandoned` until iPadOS confirms, then report.
    nonisolated func assessmentSession(_ session: AEAssessmentSession, wasInterruptedWithError error: Error) {
        let text = (error as NSError).localizedDescription
        DispatchQueue.main.async {
            guard session === self.active else {
                if session.isActive { session.end() }
                return
            }
            self.active = nil
            self.abandoned.append(session)
            if session.isActive { session.end() }
            DebugLog.log("lock", "Assessment Mode: interrupted (\(text)), ended it")
            if !self.ending { self.onInterrupted?(text) }
        }
    }

    nonisolated func assessmentSessionDidEnd(_ session: AEAssessmentSession) {
        DispatchQueue.main.async {
            self.abandoned.removeAll { $0 === session }
            guard session === self.active else { return }
            self.active = nil
            DebugLog.log("lock", "Assessment Mode: ended by the system")
            if !self.ending { self.onInterrupted?("ended by the system") }
        }
    }
}

/// Layer 2: Single App Mode. Counts ONLY when FairPlay itself turned it on with
/// requestGuidedAccessSession (supervised school iPads with an Autonomous Single App Mode
/// payload for FairPlay; the student can't end it). Guided Access the student started
/// with their own passcode never counts (review H1): they could end it at will.
enum SingleAppMode {
    /// Guided Access / Single App Mode of any kind is on right now.
    static var anyOn: Bool { UIAccessibility.isGuidedAccessEnabled }

    @MainActor
    static func request(_ enabled: Bool, completion: @escaping (Bool) -> Void) {
        UIAccessibility.requestGuidedAccessSession(enabled: enabled) { ok in
            DispatchQueue.main.async { completion(ok) }
        }
    }
}

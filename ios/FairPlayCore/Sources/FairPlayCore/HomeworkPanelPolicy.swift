import Foundation

/// Open, hold and close decisions for the in-window homework sign-in panel. Pure, for the tests;
/// the same rules, line for line, are the Mac's HomeworkPanelPolicy.swift.
public enum HomeworkPanelPolicy {

    public enum Open: Equatable {
        case refuse(String)
        case start
    }

    public static func open(examActive: Bool, deviceTestRunning: Bool, panelOpen: Bool, parsed: Bool) -> Open {
        if let text = HomeworkLogin.refusal(examActive: examActive, deviceTestRunning: deviceTestRunning) { return .refuse(text) }
        if panelOpen || !parsed { return .refuse(HomeworkLogin.startFailed) }
        return .start
    }

    /// A blocking screen or the privacy cover covers the page: the panel is hidden until it clears.
    public static func hidden(blockingShown: Bool, privacyShown: Bool) -> Bool { blockingShown || privacyShown }

    public static func pageHidden(panelOpen: Bool, held: Bool) -> Bool { panelOpen && !held }

    public static let closingActions: Set<String> = ["lock", "prelock", "session", "openExternal", "deviceTestStart"]

    /// The panel closes without an answer for these bridge actions.
    public static func closes(on action: String) -> Bool { closingActions.contains(action) }
}

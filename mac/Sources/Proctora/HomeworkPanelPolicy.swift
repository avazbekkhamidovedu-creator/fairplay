import Foundation

/// Open, hold and close decisions for the in-window homework sign-in panel. Pure, for the tests;
/// the same rules, line for line, are FairPlayCore's HomeworkPanelPolicy.swift.
enum HomeworkPanelPolicy {

    enum Open: Equatable {
        case refuse(String)
        case start
    }

    static func open(examActive: Bool, deviceTestRunning: Bool, panelOpen: Bool, parsed: Bool) -> Open {
        if let text = HomeworkLogin.refusal(examActive: examActive, deviceTestRunning: deviceTestRunning) { return .refuse(text) }
        if panelOpen || !parsed { return .refuse(HomeworkLogin.startFailed) }
        return .start
    }

    /// A blocking or permission screen covers the page: the panel is hidden until it clears.
    static func hidden(blockingShown: Bool, permissionShown: Bool) -> Bool { blockingShown || permissionShown }

    static let closingActions: Set<String> = ["lock", "prelock", "session", "openExternal", "quitApps", "deviceTestStart"]

    /// The panel closes without an answer for these bridge actions.
    static func closes(on action: String) -> Bool { closingActions.contains(action) }
}

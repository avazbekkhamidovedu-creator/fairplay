import AppKit

/// Device test: does the preview window really cover every screen? Pure decisions, no AppKit state,
/// so they can be tested without a display. Mirrors the Windows shell (ApplyLockedBounds).
enum PreviewCoverage {

    /// Rounding and Retina scaling can leave the window a point off the screen edge.
    static let tolerance: CGFloat = 2

    static let refusedMessage = "The test could not cover your whole screen, so it did not start. "
        + "Close extra windows or change the display scale, then try again."

    /// Everything the check needs, read from AppKit once.
    struct Snapshot {
        var window: CGRect
        var screens: [CGRect]            // NSScreen.frame of every display (global coordinates, may be negative)
        var presentation: NSApplication.PresentationOptions
        var isVisible: Bool
        var isKey: Bool
    }

    enum Failure: String {
        case noScreens, windowShort, menuBarOrDockShown, notVisible, notKey
    }

    /// The area to cover: the union of every display's full frame (not visibleFrame: the menu bar and Dock area count).
    /// Empty or non-finite frames are ignored; `fallback` is used when no display is usable.
    static func target(screens: [CGRect], fallback: CGRect? = nil) -> CGRect? {
        let usable = screens.filter { !$0.isEmpty && !$0.isNull && !$0.isInfinite && $0.width.isFinite && $0.height.isFinite }
        if let first = usable.first { return usable.dropFirst().reduce(first) { $0.union($1) } }
        if let f = fallback, !f.isEmpty { return f }
        return nil
    }

    static func covers(_ window: CGRect, _ target: CGRect, tolerance: CGFloat = tolerance) -> Bool {
        guard !window.isEmpty, !target.isEmpty else { return false }
        return window.minX <= target.minX + tolerance && window.minY <= target.minY + tolerance
            && window.maxX >= target.maxX - tolerance && window.maxY >= target.maxY - tolerance
    }

    static let requiredHidden: NSApplication.PresentationOptions = [.hideDock, .hideMenuBar]

    /// nil = covered. Otherwise the first thing that is wrong.
    static func evaluate(_ s: Snapshot) -> Failure? {
        guard let t = target(screens: s.screens) else { return .noScreens }
        if !covers(s.window, t) { return .windowShort }
        if !s.presentation.isSuperset(of: requiredHidden) { return .menuBarOrDockShown }
        if !s.isVisible { return .notVisible }
        // Not being the key window (a system dialog took focus) does not uncover the screen: logged, never a refusal.
        return nil
    }

    private static func r(_ f: CGRect) -> String {
        "\(Int(f.minX.rounded())),\(Int(f.minY.rounded())) \(Int(f.width.rounded()))x\(Int(f.height.rounded()))"
    }

    /// One line per decision for the log. Key fields first.
    static func line(why: String, failure: Failure?, snapshot s: Snapshot, scales: [CGFloat]) -> String {
        let t = target(screens: s.screens)
        return "placement \(failure == nil ? "covered" : "NOT covered (\(failure!.rawValue))") [\(why)] "
            + "window=\(r(s.window)) target=\(t.map(r) ?? "none") "
            + "screens=\(s.screens.map(r).joined(separator: " | ")) "
            + "scale=\(scales.map { String(format: "%g", Double($0)) }.joined(separator: ",")) "
            + "hideDock=\(s.presentation.contains(.hideDock)) hideMenuBar=\(s.presentation.contains(.hideMenuBar)) "
            + "visible=\(s.isVisible) key=\(s.isKey)"
    }

    /// Always on (unified log, `log show --predicate 'eventMessage CONTAINS "[FairPlay]"'`), and the dev log too.
    static func diag(_ message: String) {
        NSLog("[FairPlay] devicetest %@", message)
        DebugLog.log("devicetest", message)
    }
}

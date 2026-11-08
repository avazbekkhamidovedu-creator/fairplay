import AppKit

/// Small corner label that lets the student see which FairPlay build is running.
private final class PassiveLabel: NSTextField {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

enum VersionLabel {
    static func add(to root: NSView) {
        let label = PassiveLabel(labelWithString: Config.versionLabelText())
        label.font = .systemFont(ofSize: 10)
        label.textColor = .secondaryLabelColor
        label.isSelectable = false
        label.setAccessibilityIdentifier("fairplay.version")
        label.frame = NSRect(x: 16, y: 8, width: 260, height: 12)
        label.autoresizingMask = [.maxXMargin, .maxYMargin]
        root.addSubview(label)
    }
}

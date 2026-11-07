import AppKit

/// Matches the released sign-in screen's yellow device-test pill.
final class DeviceTestPill: NSButton {
    var fillColor = NSColor(srgbRed: 245/255, green: 183/255, blue: 0, alpha: 1)
    var showsDeviceIcon = true
    override var intrinsicContentSize: NSSize { NSSize(width: showsDeviceIcon ? 218 : 292, height: 50) }
    override func draw(_ dirtyRect: NSRect) {
        let shape = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 22, yRadius: 22)
        fillColor.withAlphaComponent(isEnabled ? (isHighlighted ? 0.85 : 1) : 0.5).setFill()
        shape.fill()
        NSColor.white.setStroke()
        let inner = NSBezierPath(roundedRect: bounds.insetBy(dx: 5, dy: 5), xRadius: 20, yRadius: 20)
        inner.lineWidth = 1.5; inner.stroke()
        let shadow = NSShadow(); shadow.shadowColor = NSColor.black.withAlphaComponent(0.55)
        shadow.shadowOffset = NSSize(width: 0, height: -1); shadow.shadowBlurRadius = 2
        let text = NSAttributedString(string: title, attributes: [.font: NSFont.systemFont(ofSize: 16, weight: .bold), .foregroundColor: NSColor.white, .shadow: shadow])
        text.draw(at: NSPoint(x: showsDeviceIcon ? 52 : (bounds.width - text.size().width)/2, y: (bounds.height - text.size().height)/2))
        guard showsDeviceIcon else { return }
        let icon = NSImage(systemSymbolName: "laptopcomputer", accessibilityDescription: nil)
        let whiteIcon = icon?.withSymbolConfiguration(NSImage.SymbolConfiguration(paletteColors: [.white]))
        whiteIcon?.draw(in: NSRect(x: 22, y: 15, width: 22, height: 20))
    }
}

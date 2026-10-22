import AppKit

/// The FairPlay logo mark (brand/mark.svg): a white circle split into two equal halves on a
/// blue rounded square. Drawn in code so it is sharp at any size and needs no image file.
enum BrandMark {
    static let blue = NSColor(srgbRed: 0x2F / 255.0, green: 0x4B / 255.0, blue: 0xD8 / 255.0, alpha: 1)

    /// Draws the mark into `rect` (64-unit design scaled to the rect).
    static func draw(in rect: NSRect) {
        let s = rect.width / 64
        func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: rect.minX + x * s, y: rect.minY + y * s) }
        blue.setFill()
        NSBezierPath(roundedRect: rect, xRadius: 12.3 * s, yRadius: 12.3 * s).fill()
        // White disc r = 18.5 at the centre, minus a 4.6-wide blue gap down the middle.
        let disc = NSBezierPath(ovalIn: NSRect(x: rect.minX + (32 - 18.5) * s, y: rect.minY + (32 - 18.5) * s,
                                               width: 37 * s, height: 37 * s))
        NSColor.white.setFill()
        disc.fill()
        blue.setFill()
        NSBezierPath(rect: NSRect(origin: p(29.7, 12), size: NSSize(width: 4.6 * s, height: 40 * s))).fill()
    }

    /// The same mark as inline SVG for FairPlay's own HTML pages (no server needed).
    static func svg(size: Int) -> String {
        "<svg width=\"\(size)\" height=\"\(size)\" viewBox=\"0 0 64 64\" role=\"img\" aria-label=\"FairPlay\" "
            + "style=\"display:block;margin:0 0 16px\"><rect width=\"64\" height=\"64\" rx=\"12.3\" fill=\"#2F4BD8\"/>"
            + "<path d=\"M29.7 13.64A18.5 18.5 0 0 0 29.7 50.36Z\" fill=\"#fff\"/>"
            + "<path d=\"M34.3 13.64A18.5 18.5 0 0 1 34.3 50.36Z\" fill=\"#fff\"/></svg>"
    }

    static func image(size: CGFloat) -> NSImage {
        let img = NSImage(size: NSSize(width: size, height: size), flipped: false) { r in
            draw(in: r)
            return true
        }
        img.accessibilityDescription = "FairPlay"
        return img
    }

    /// A fixed-size image view of the mark, for native bars.
    static func view(size: CGFloat) -> NSImageView {
        let v = NSImageView(image: image(size: size))
        v.imageScaling = .scaleProportionallyUpOrDown
        v.translatesAutoresizingMaskIntoConstraints = false
        v.setAccessibilityLabel("FairPlay")
        NSLayoutConstraint.activate([
            v.widthAnchor.constraint(equalToConstant: size),
            v.heightAnchor.constraint(equalToConstant: size),
        ])
        return v
    }
}

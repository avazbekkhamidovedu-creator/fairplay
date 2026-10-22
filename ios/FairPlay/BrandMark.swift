import UIKit

/// The FairPlay logo mark (brand/mark.svg): a white circle split into two equal halves on a
/// blue rounded square. Drawn in code so it is sharp at any size; the app icon uses the same
/// drawing (Assets.xcassets/AppIcon).
enum BrandMark {
    static let blue = UIColor(red: 0x2F / 255.0, green: 0x4B / 255.0, blue: 0xD8 / 255.0, alpha: 1)

    static func image(size: CGFloat) -> UIImage {
        let img = UIGraphicsImageRenderer(size: CGSize(width: size, height: size)).image { _ in
            let s = size / 64
            blue.setFill()
            UIBezierPath(roundedRect: CGRect(x: 0, y: 0, width: size, height: size), cornerRadius: 12.3 * s).fill()
            UIColor.white.setFill()
            UIBezierPath(ovalIn: CGRect(x: 13.5 * s, y: 13.5 * s, width: 37 * s, height: 37 * s)).fill()
            blue.setFill()
            UIBezierPath(rect: CGRect(x: 29.7 * s, y: 12 * s, width: 4.6 * s, height: 40 * s)).fill()
        }
        img.accessibilityLabel = "FairPlay"
        return img
    }

    /// A fixed-size image view of the mark, for the lock bar.
    static func view(size: CGFloat) -> UIImageView {
        let v = UIImageView(image: image(size: size))
        v.translatesAutoresizingMaskIntoConstraints = false
        v.isAccessibilityElement = true
        v.accessibilityLabel = "FairPlay"
        NSLayoutConstraint.activate([
            v.widthAnchor.constraint(equalToConstant: size),
            v.heightAnchor.constraint(equalToConstant: size),
        ])
        return v
    }

    /// The same mark as inline SVG for FairPlay's own HTML pages.
    static func svg(size: Int) -> String {
        "<svg width=\"\(size)\" height=\"\(size)\" viewBox=\"0 0 64 64\" role=\"img\" aria-label=\"FairPlay\" "
            + "style=\"display:block;margin:0 0 16px\"><rect width=\"64\" height=\"64\" rx=\"12.3\" fill=\"#2F4BD8\"/>"
            + "<path d=\"M29.7 13.64A18.5 18.5 0 0 0 29.7 50.36Z\" fill=\"#fff\"/>"
            + "<path d=\"M34.3 13.64A18.5 18.5 0 0 1 34.3 50.36Z\" fill=\"#fff\"/></svg>"
    }
}

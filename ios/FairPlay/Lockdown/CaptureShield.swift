import UIKit

/// Layer 3: the exam is drawn into the render layer iPadOS uses for password fields, which
/// the system leaves out of screenshots, screen recordings, AirPlay / cable mirroring and
/// the app-switcher snapshot. So even if Assessment Mode is unavailable or interrupted,
/// a capture shows a blank screen, not the questions.
///
/// The secure layer is private UIKit layout, so it may be missing on some iPadOS version.
/// Review M4: we try again once the view is in a window, and `isShielding` goes into
/// every beat (`shield=`); release builds block the exam when neither the shield nor
/// Assessment Mode protects it.
final class CaptureShieldView: UIView {

    /// Put the exam UI in here.
    let content = UIView()
    private(set) var isShielding = false
    private let wantsShield: Bool
    private let field = UITextField()
    private var canvasClass = "none"

    init(shield: Bool) {
        wantsShield = shield
        super.init(frame: .zero)
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
        pin(content, to: self)
        if shield {
            field.isSecureTextEntry = true
            #if FAIRPLAY_DEV
            // Dev only: skip the first try to exercise the window retry (-ShieldForceRetry YES).
            if !UserDefaults.standard.bool(forKey: "ShieldForceRetry") { attachSecureLayer() }
            #else
            attachSecureLayer()
            #endif
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        // Some iPadOS versions create the secure canvas only once the field is laid out
        // in a window: give it one more chance there.
        if wantsShield, !isShielding, window != nil {
            if field.superview == nil {
                field.isHidden = true
                addSubview(field)
            }
            field.layoutIfNeeded()
            attachSecureLayer()
        }
    }

    /// Moves `content` into the secure canvas when it exists.
    private func attachSecureLayer() {
        // Re-review N6: only the text field's canvas view carries the secure layer; never
        // take some other subview (and report shield=1) by position alone.
        guard !isShielding, let canvas = field.subviews.first(where: {
            $0 !== content && String(describing: type(of: $0)).contains("CanvasView")
        }) else {
            DebugLog.log("app", "capture shield: secure layer not found (yet)")
            return
        }
        canvasClass = String(describing: type(of: canvas))
        canvas.subviews.forEach { $0.removeFromSuperview() }
        canvas.isUserInteractionEnabled = true
        canvas.translatesAutoresizingMaskIntoConstraints = false
        insertSubview(canvas, at: 0)
        pin(canvas, to: self)
        content.removeFromSuperview()
        canvas.addSubview(content)
        pin(content, to: canvas)
        if field.superview != nil { field.removeFromSuperview() }
        isShielding = true
        DebugLog.log("app", "capture shield ON")
    }

    #if FAIRPLAY_DEV
    /// Dev builds only (-ShieldSelfTest YES): render the window the way iPadOS snapshots it
    /// and log whether the exam's pixels show up. The simulator's own screenshots read the
    /// display itself, so they can't tell; this asks the snapshot path the shield targets.
    func selfTest() {
        guard let window else { return }
        let img = UIGraphicsImageRenderer(bounds: window.bounds).image { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
        }
        // Sample a grid over the content area: a blank (one-colour) snapshot means hidden.
        guard let cg = img.cgImage, let data = cg.dataProvider?.data, let ptr = CFDataGetBytePtr(data) else { return }
        let bpr = cg.bytesPerRow, bpp = cg.bitsPerPixel / 8
        var colours = Set<UInt32>()
        for gy in 1..<20 {
            for gx in 1..<20 {
                let x = cg.width * gx / 20, y = cg.height * gy / 20
                let o = y * bpr + x * bpp
                colours.insert(UInt32(ptr[o]) << 16 | UInt32(ptr[o + 1]) << 8 | UInt32(ptr[o + 2]))
            }
        }
        DebugLog.log("app", "shield self-test: shielding=\(isShielding) colours=\(colours.count) "
                     + "-> \(colours.count <= 2 ? "HIDDEN from snapshots" : "VISIBLE in snapshots") canvas=\(canvasClass)")
    }
    #endif

    private func pin(_ v: UIView, to p: UIView) {
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: p.leadingAnchor), v.trailingAnchor.constraint(equalTo: p.trailingAnchor),
            v.topAnchor.constraint(equalTo: p.topAnchor), v.bottomAnchor.constraint(equalTo: p.bottomAnchor),
        ])
    }
}

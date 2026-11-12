import AppKit

/// What one tab shows.
struct SafariTabItem: Equatable {
    let id: ObjectIdentifier
    var title: String
    var selected: Bool
    var closable: Bool
    /// Asset name of the favicon stand-in: the exam tab shows the DKU OJ mark, pages a globe (no network fetch for icons).
    var useExamIcon: Bool = false
    var loading: Bool = false
}

/// One tab, drawn like a Safari compact tab: rounded, icon and title, a close button on hover.
/// An NSButton so a click, `performClick` and the accessibility "button" role behave as before.
final class SafariTabButton: NSButton {
    /// Read from the bundle once, not on every update.
    static let examIcon: NSImage? = Bundle.main.path(forResource: "DKUOJ", ofType: "ico").flatMap { NSImage(contentsOfFile: $0) }
    let iconView = NSImageView()
    let label = NSTextField(labelWithString: "")
    let closeButton = NSButton()
    private(set) var item: SafariTabItem
    private var hovered = false
    var onClose: () -> Void = {}

    init(item: SafariTabItem) {
        self.item = item
        super.init(frame: .zero)
        title = ""
        isBordered = false
        setButtonType(.momentaryChange)
        wantsLayer = true
        layer?.cornerRadius = 8
        layer?.masksToBounds = false
        iconView.imageScaling = .scaleProportionallyDown
        iconView.setAccessibilityElement(false)
        label.font = .systemFont(ofSize: 12)
        label.lineBreakMode = .byTruncatingTail
        label.setAccessibilityElement(false)
        closeButton.isBordered = false
        closeButton.image = NSImage(systemSymbolName: "xmark", accessibilityDescription: "Close tab")
        closeButton.imageScaling = .scaleProportionallyDown
        closeButton.contentTintColor = .secondaryLabelColor
        closeButton.target = self; closeButton.action = #selector(closePressed)
        closeButton.isHidden = true
        addSubview(iconView); addSubview(label); addSubview(closeButton)
        apply(item)
    }
    required init?(coder: NSCoder) { fatalError() }

    @objc private func closePressed() { onClose() }

    func apply(_ item: SafariTabItem) {
        self.item = item
        label.stringValue = item.title
        let symbol = item.useExamIcon ? nil : NSImage(systemSymbolName: item.loading ? "ellipsis.circle" : "globe", accessibilityDescription: nil)
        if item.useExamIcon, let image = Self.examIcon {
            iconView.image = image
        } else {
            iconView.image = symbol ?? NSImage(systemSymbolName: "globe", accessibilityDescription: nil)
            iconView.contentTintColor = .secondaryLabelColor
        }
        label.font = .systemFont(ofSize: 12, weight: item.selected ? .semibold : .regular)
        label.textColor = item.selected ? .labelColor : .secondaryLabelColor
        setAccessibilityLabel("Tab: " + item.title)
        closeButton.setAccessibilityLabel("Close tab: " + item.title)
        state = item.selected ? .on : .off
        refreshCloseVisibility()
        updateColors()
        needsLayout = true
    }

    private func refreshCloseVisibility() { closeButton.isHidden = !(item.closable && hovered) }

    private func updateColors() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            if item.selected {
                layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
                layer?.borderColor = NSColor.separatorColor.cgColor
                layer?.borderWidth = 0.5
                layer?.shadowOpacity = 0.12; layer?.shadowRadius = 1.5; layer?.shadowOffset = CGSize(width: 0, height: -0.5)
            } else {
                layer?.backgroundColor = (hovered ? NSColor.labelColor.withAlphaComponent(0.07) : NSColor.clear).cgColor
                layer?.borderWidth = 0
                layer?.shadowOpacity = 0
            }
        }
    }

    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); updateColors() }

    override func layout() {
        super.layout()
        let h = bounds.height, pad: CGFloat = 8, icon: CGFloat = 14, closeSize: CGFloat = 16
        iconView.frame = NSRect(x: pad, y: (h - icon) / 2, width: icon, height: icon)
        closeButton.frame = NSRect(x: bounds.width - pad - closeSize + 2, y: (h - closeSize) / 2, width: closeSize, height: closeSize)
        let textX = pad + icon + 6
        let right = item.closable ? closeSize + pad : pad
        label.frame = NSRect(x: textX, y: (h - 16) / 2, width: max(0, bounds.width - textX - right), height: 16)
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect], owner: self, userInfo: nil))
    }
    override func mouseEntered(with event: NSEvent) { hovered = true; refreshCloseVisibility(); updateColors() }
    override func mouseExited(with event: NSEvent) { hovered = false; refreshCloseVisibility(); updateColors() }

    /// A press on the visible close button closes; anywhere else on the tab selects it (the labels never take clicks).
    override func hitTest(_ point: NSPoint) -> NSView? {
        let local = convert(point, from: superview)
        guard bounds.contains(local) else { return nil }
        if !closeButton.isHidden, closeButton.frame.insetBy(dx: -4, dy: -4).contains(local) { return closeButton }
        return self
    }
}

/// Safari's tab bar: equal-width compact tabs that shrink as more open, the selected one raised, new tabs sliding in at the end.
final class SafariTabBar: NSView {
    static let maxTabWidth: CGFloat = 220
    static let minTabWidth: CGFloat = 76
    static let gap: CGFloat = 4
    static let tabHeight: CGFloat = 32

    var onSelect: (Int) -> Void = { _ in }
    var onClose: (Int) -> Void = { _ in }
    private var buttons: [ObjectIdentifier: SafariTabButton] = [:]
    private(set) var order: [ObjectIdentifier] = []

    /// Every tab gets the same width: as wide as fits, never wider than Safari's, never narrower than the minimum.
    static func tabWidth(count: Int, available: CGFloat) -> CGFloat {
        guard count > 0 else { return maxTabWidth }
        let fit = (available - gap * CGFloat(count - 1)) / CGFloat(count)
        return max(minTabWidth, min(maxTabWidth, fit.rounded(.down)))
    }

    /// The frame of tab `index` in a bar of this size.
    static func tabFrame(index: Int, count: Int, bounds: NSRect) -> NSRect {
        let w = tabWidth(count: count, available: bounds.width)
        return NSRect(x: CGFloat(index) * (w + gap), y: (bounds.height - tabHeight) / 2, width: w, height: tabHeight)
    }

    var tabButtons: [SafariTabButton] { order.compactMap { buttons[$0] } }

    private var animates: Bool { window != nil && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

    private var lastItems: [SafariTabItem] = []
    private var lastSize = NSSize.zero

    func update(_ items: [SafariTabItem]) {
        // Called on every navigation event: nothing changed, nothing moves.
        if items == lastItems, bounds.size == lastSize { return }
        lastItems = items; lastSize = bounds.size
        let ids = Set(items.map(\.id))
        for (id, button) in buttons where !ids.contains(id) {
            buttons[id] = nil
            // A tab on its way out takes no clicks: its tag is stale the moment the others renumber.
            button.isEnabled = false; button.target = nil; button.action = nil; button.onClose = {}
            button.closeButton.isEnabled = false; button.closeButton.target = nil
            button.setAccessibilityElement(false)
            if animates {
                NSAnimationContext.runAnimationGroup({ ctx in ctx.duration = 0.15; button.animator().alphaValue = 0 }, completionHandler: { button.removeFromSuperview() })
            } else { button.removeFromSuperview() }
        }
        var added: Set<ObjectIdentifier> = []
        for (index, item) in items.enumerated() {
            if let button = buttons[item.id] { button.apply(item); button.tag = index; button.closeButton.tag = index; continue }
            let button = SafariTabButton(item: item)
            button.target = self; button.action = #selector(tabPressed(_:))
            button.onClose = { [weak self, weak button] in if let button { self?.onClose(button.tag) } }
            button.tag = index; button.closeButton.tag = index
            buttons[item.id] = button
            addSubview(button)
            added.insert(item.id)
        }
        order = items.map(\.id)
        for (index, item) in items.enumerated() {
            guard let button = buttons[item.id] else { continue }
            let target = Self.tabFrame(index: index, count: items.count, bounds: bounds)
            if added.contains(item.id), animates {
                // Safari: a new tab grows out of the end of the row.
                button.frame = NSRect(x: target.minX, y: target.minY, width: 0, height: target.height)
                button.alphaValue = 0
                NSAnimationContext.runAnimationGroup { ctx in
                    ctx.duration = 0.18
                    button.animator().frame = target; button.animator().alphaValue = 1
                }
            } else if animates {
                // Only a tab that really moves or resizes animates.
                if button.frame != target {
                    NSAnimationContext.runAnimationGroup { ctx in ctx.duration = 0.18; button.animator().frame = target }
                }
            } else {
                button.frame = target; button.alphaValue = 1
            }
        }
    }

    override func layout() {
        super.layout()
        for (index, id) in order.enumerated() { buttons[id]?.frame = Self.tabFrame(index: index, count: order.count, bounds: bounds) }
    }

    @objc private func tabPressed(_ sender: SafariTabButton) { onSelect(sender.tag) }
}

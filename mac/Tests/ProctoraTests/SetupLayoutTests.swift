import Testing
import AppKit
@testable import Proctora

@MainActor @Suite struct SetupLayoutTests {
    @Test func pillVersionLabelAndColumnsNeverOverlap() throws {
        for size in [NSSize(width: 900, height: 600), NSSize(width: 1280, height: 820), NSSize(width: 1728, height: 1080)] {
            let container = NSView(frame: NSRect(origin: .zero, size: size))
            let controller = ProtectedBrowserController(busy: { false }, didClose: {})
            controller.show(in: container)
            let root = try #require(container.subviews.first)
            root.layoutSubtreeIfNeeded()
            let pill = try #require(root.subviews.first { $0 is DeviceTestPill })
            let label = try #require(root.subviews.first { $0.accessibilityIdentifier() == "fairplay.version" })
            let columns = try #require(root.subviews.first { $0 is NSStackView })
            #expect(!pill.frame.intersects(columns.frame), "pill overlaps content at \(size)")
            #expect(!pill.frame.intersects(label.frame), "pill overlaps version at \(size)")
            #expect(!label.frame.intersects(columns.frame), "version overlaps content at \(size)")
            #expect(root.bounds.contains(pill.frame) && root.bounds.contains(label.frame) && root.bounds.contains(columns.frame))
        }
    }
}

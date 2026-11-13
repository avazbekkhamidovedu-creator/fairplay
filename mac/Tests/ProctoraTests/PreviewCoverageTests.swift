import Testing
import AppKit
@testable import Proctora

@Suite struct PreviewCoverageTests {
    private let opts: NSApplication.PresentationOptions = [.hideDock, .hideMenuBar, .disableAppleMenu]
    private func snap(_ w: CGRect, _ screens: [CGRect], opts: NSApplication.PresentationOptions? = nil,
                      visible: Bool = true, key: Bool = true) -> PreviewCoverage.Snapshot {
        .init(window: w, screens: screens, presentation: opts ?? self.opts, isVisible: visible, isKey: key)
    }
    private let main = CGRect(x: 0, y: 0, width: 1512, height: 982)

    @Test func singleScreenCovered() {
        #expect(PreviewCoverage.evaluate(snap(main, [main])) == nil)
    }
    @Test func unionOfTwoDisplaysWithNegativeOrigin() {
        let left = CGRect(x: -1920, y: -100, width: 1920, height: 1080)
        let screens = [main, left]
        let union = CGRect(x: -1920, y: -100, width: 3432, height: 1082)
        #expect(PreviewCoverage.target(screens: screens) == union)
        #expect(PreviewCoverage.evaluate(snap(union, screens)) == nil)
        #expect(PreviewCoverage.evaluate(snap(main, screens)) == .windowShort)
    }
    @Test func stackedDisplayAbove() {
        let above = CGRect(x: 200, y: 982, width: 1920, height: 1080)
        let union = main.union(above)
        #expect(PreviewCoverage.evaluate(snap(union, [main, above])) == nil)
        #expect(PreviewCoverage.evaluate(snap(main, [main, above])) == .windowShort)
    }
    @Test func menuBarOffsetFails() {
        // A window limited to visibleFrame (menu bar 37 pt) leaves the top strip uncovered.
        let visible = CGRect(x: 0, y: 0, width: 1512, height: 945)
        #expect(PreviewCoverage.evaluate(snap(visible, [main])) == .windowShort)
        // Within the 2 pt tolerance (rounding on scaled displays) is fine.
        #expect(PreviewCoverage.evaluate(snap(CGRect(x: 0.5, y: 0.5, width: 1511, height: 981.5), [main])) == nil)
    }
    @Test func scaledDisplayFractionalFrames() {
        let scaled = CGRect(x: 0, y: 0, width: 1710.5, height: 1107.25)
        #expect(PreviewCoverage.evaluate(snap(scaled.insetBy(dx: -1, dy: -1), [scaled])) == nil)
        #expect(PreviewCoverage.evaluate(snap(scaled.insetBy(dx: 5, dy: 5), [scaled])) == .windowShort)
    }
    @Test func presentationVisibleKeyChecks() {
        #expect(PreviewCoverage.evaluate(snap(main, [main], opts: [.hideDock])) == .menuBarOrDockShown)
        #expect(PreviewCoverage.evaluate(snap(main, [main], opts: [])) == .menuBarOrDockShown)
        #expect(PreviewCoverage.evaluate(snap(main, [main], visible: false)) == .notVisible)
        #expect(PreviewCoverage.evaluate(snap(main, [main], key: false)) == nil)
    }
    @Test func noScreensAndFallback() {
        #expect(PreviewCoverage.evaluate(snap(main, [])) == .noScreens)
        #expect(PreviewCoverage.evaluate(snap(main, [.zero, .null])) == .noScreens)
        #expect(PreviewCoverage.target(screens: [], fallback: main) == main)
        #expect(!PreviewCoverage.covers(.zero, main))
    }
    @Test func logLineHasKeyFields() {
        let s = snap(main, [main])
        let line = PreviewCoverage.line(why: "start", failure: nil, snapshot: s, scales: [2, 1])
        #expect(line.contains("covered"))
        #expect(line.contains("scale=2,1"))
        #expect(line.contains("screens=0,0 1512x982"))
        #expect(PreviewCoverage.line(why: "x", failure: .windowShort, snapshot: s, scales: [2]).contains("NOT covered (windowShort)"))
    }
}

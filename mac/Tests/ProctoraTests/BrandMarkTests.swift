// The FairPlay logo drawn natively (permission screen, website-exam strip, own pages).
import Testing
import AppKit
@testable import Proctora

@Suite struct BrandMarkTests {
    private func rgb(_ img: NSImage, _ x: Int, _ y: Int) -> (Int, Int, Int) {
        let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
        let c = rep.colorAt(x: x, y: y)!.usingColorSpace(.sRGB)!
        return (Int(c.redComponent * 255 + 0.5), Int(c.greenComponent * 255 + 0.5), Int(c.blueComponent * 255 + 0.5))
    }

    @Test func drawsWhiteHalvesSplitByABlueGap() {
        let img = BrandMark.image(size: 64)
        let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
        let k = rep.pixelsWide / 64   // 1 on a normal screen, 2 on Retina
        // Colour management shifts exact values a little; check blue vs white.
        func isBlue(_ c: (Int, Int, Int)) -> Bool { c.2 > 180 && c.0 < 120 && c.2 - c.0 > 100 }
        func isWhite(_ c: (Int, Int, Int)) -> Bool { min(c.0, c.1, c.2) > 235 }
        #expect(isBlue(rgb(img, 4 * k, 32 * k)))      // square
        #expect(isWhite(rgb(img, 22 * k, 32 * k)))    // left half
        #expect(isWhite(rgb(img, 42 * k, 32 * k)))    // right half
        #expect(isBlue(rgb(img, 32 * k, 32 * k)))     // gap in the middle
    }

    @Test func htmlMarkMatchesTheWebLogo() {
        let s = BrandMark.svg(size: 44)
        #expect(s.contains("width=\"44\""))
        #expect(s.contains("M29.7 13.64A18.5 18.5 0 0 0 29.7 50.36Z"))
        #expect(s.contains("M34.3 13.64A18.5 18.5 0 0 1 34.3 50.36Z"))
        #expect(s.contains("fill=\"#2F4BD8\""))
    }
}

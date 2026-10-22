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
        #expect(rgb(img, 4 * k, 32 * k) == (0x2F, 0x4B, 0xD8))      // square
        #expect(rgb(img, 22 * k, 32 * k) == (255, 255, 255))         // left half
        #expect(rgb(img, 42 * k, 32 * k) == (255, 255, 255))         // right half
        #expect(rgb(img, 32 * k, 32 * k) == (0x2F, 0x4B, 0xD8))     // gap in the middle
    }

    @Test func htmlMarkMatchesTheWebLogo() {
        let s = BrandMark.svg(size: 44)
        #expect(s.contains("width=\"44\""))
        #expect(s.contains("M29.7 13.64A18.5 18.5 0 0 0 29.7 50.36Z"))
        #expect(s.contains("M34.3 13.64A18.5 18.5 0 0 1 34.3 50.36Z"))
        #expect(s.contains("fill=\"#2F4BD8\""))
    }
}

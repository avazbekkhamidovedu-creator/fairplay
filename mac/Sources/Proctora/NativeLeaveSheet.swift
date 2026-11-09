import AppKit

/// The Leave button in a native-locked exam never asks for a code: the instructor releases the
/// Mac from DKU OJ, a hand-in unlocks it, or the deadline failsafe does. The exit-code check stays for
/// the tests and is not reachable from this sheet.
enum NativeLeaveText {
    static let notice = "Your instructor can release this Mac from DKU OJ. It unlocks by itself, usually within about 25 seconds. If you finished, use the hand-in on the exam page."
}

enum NativeLeaveSheet {
    static func present(in window: NSWindow?) {
        guard let window, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "Leave"
        alert.informativeText = NativeLeaveText.notice
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window, completionHandler: nil)
    }
}

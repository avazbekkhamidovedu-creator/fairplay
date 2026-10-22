import Foundation

/// What the iPad app measured in one check round (IntegrityChecks.swift collects it).
public struct DeviceState: Equatable {
    /// Apple Assessment Mode (AEAssessmentSession) is active.
    public var assessment = false
    /// Single App Mode that FairPlay itself turned on (supervised iPads). Guided Access the
    /// student started doesn't count (review H1); it shows as `guidedAccess` only.
    public var singleApp = false
    /// Guided Access / Single App Mode of any kind is on (informational).
    public var guidedAccess = false
    /// The capture shield found its secure layer (review M4).
    public var shield = true
    /// Teacher-allowed apps in this Assessment Mode session (e.g. Calculator).
    public var participants = 0
    /// Dev builds without Assessment Mode (simulator, no entitlement): watched, not locked.
    public var softLock = false
    public var screens = 1
    /// Screen recording, AirPlay / cable mirroring or QuickTime capture of this screen.
    public var captured = false
    /// Split View, Slide Over or a Stage Manager window smaller than the screen.
    public var multitasking = false
    public var foreground = true
    /// Times the app left the foreground during this lock.
    public var leaves = 0
    public var screenshots = 0
    public var jailbreak: [String] = []
    public var debugger = false
    public var simulator = false
    public var hardwareKeyboard = false
    /// Dev builds with -AllowCapture YES: recording / mirroring doesn't put up the red
    /// screen (still reported as cap=1). Never set in release or test builds.
    public var captureAllowed = false
    public var vpn = false
    public var proxy = false
    /// "1" attested this launch, "0" not supported, "?" not tried / failed.
    public var appAttest = "?"
    /// S5: answers sealed on this iPad still wait for upload.
    public var sealedPending = false
    public var batteryPercent: Int?
    public var charging = false

    public init() {}

    /// The exam is on this iPad's own screen and nobody else's (the server treats
    /// console=0 as "not locked" and flags off_console). Review M5: while Assessment Mode
    /// holds and the teacher allowed apps, being in the background can only mean one of
    /// those apps (iPadOS allows nothing else), which is not leaving the exam.
    public var onConsole: Bool {
        (foreground || (assessment && participants > 0)) && !captured && screens <= 1
    }

    /// Short summary for the signed beat, e.g. "plat=ipad,aac=1,sam=0,disp=1,...".
    public var summary: String {
        func b(_ v: Bool) -> String { v ? "1" : "0" }
        return [
            "plat=ipad", "aac=\(b(assessment))", "sam=\(b(singleApp))", "ga=\(b(guidedAccess))",
            "soft=\(b(softLock))", "shield=\(b(shield))", "apps=\(participants)",
            "disp=\(screens)", "cap=\(b(captured))", "mw=\(b(multitasking))", "fg=\(b(foreground))",
            "left=\(leaves)", "shot=\(screenshots)", "jb=\(b(!jailbreak.isEmpty))", "dbg=\(b(debugger))",
            "sim=\(b(simulator))", "kbd=\(b(hardwareKeyboard))", "vpn=\(b(vpn))", "proxy=\(b(proxy))",
            "att=\(appAttest)", "sep=\(b(assessment || singleApp))", "sealed=\(b(sealedPending))",
            "console=\(b(onConsole))", "secinput=0",
        ].joined(separator: ",")
    }
}

public enum Integrity {

    /// Problems that put the red screen over the exam until fixed (key -> text).
    /// `release`: release builds also block on a jailbreak, a debugger and a lock that is
    /// neither Assessment Mode nor Single App Mode.
    public static func problems(_ s: DeviceState, release: Bool) -> [String: String] {
        var out: [String: String] = [:]
        if s.screens > 1 || (s.captured && !s.captureAllowed) {
            out["display"] = "This screen is being recorded, mirrored or shown on another display.\n"
                + "Stop screen recording, turn off AirPlay / Screen Mirroring and unplug any cable."
        }
        if s.multitasking {
            out["multitask"] = "FairPlay must fill the whole screen.\n"
                + "Close Split View / Slide Over and make the FairPlay window full screen."
        }
        if release && !s.jailbreak.isEmpty {
            out["jailbreak"] = "This iPad has been modified (jailbroken).\nExams can't be taken on it. Use another iPad."
        }
        if release && s.debugger {
            out["debugger"] = "A debugger is attached to FairPlay.\nExams can't be taken like this."
        }
        if release && !s.shield && !s.assessment {
            out["shield"] = "FairPlay could not protect this screen from recording.\n"
                + "Ask the invigilator. (Assessment Mode must be on for this iPad.)"
        }
        // A watched test build (soft=1, Config.isWatchedBuild) has no Assessment Mode by
        // design: the server already counts it as not locked; a red screen would only stop
        // the test. Release builds without WatchedLock never have softLock set.
        if release && !s.assessment && !s.singleApp && !s.softLock {
            out["assessment"] = "Assessment Mode was turned off.\nTap \"Resume locked mode\" or ask the invigilator."
        }
        return out
    }

    /// Before locking (audit M-11 on the Mac): what must be fixed while the student can
    /// still leave the app. Plain sentences; [] = ready.
    public static func preflight(_ s: DeviceState, release: Bool, osMajor: Int) -> [String] {
        preflightProblems(s, release: release, osMajor: osMajor).map(\.message)
    }

    /// The same problems with an id each, for the S1 device-check rows.
    public static func preflightProblems(_ s: DeviceState, release: Bool, osMajor: Int) -> [(id: String, message: String)] {
        var out: [(id: String, message: String)] = []
        if s.screens > 1 || (s.captured && !s.captureAllowed) {
            out.append(("display", "Stop screen recording and turn off Screen Mirroring / AirPlay. Unplug any external display."))
        }
        if s.multitasking {
            out.append(("multitask", "Make FairPlay full screen (no Split View, Slide Over or small Stage Manager window)."))
        }
        if release && !s.jailbreak.isEmpty {
            out.append(("jailbreak", "This iPad has been modified (jailbroken). Use another iPad for the exam."))
        }
        if release && s.debugger {
            out.append(("debugger", "Quit the debugger attached to FairPlay."))
        }
        if let pct = s.batteryPercent, pct < 20, !s.charging {
            out.append(("battery_low", "Battery is at \(pct)%. Plug in the charger before you start."))
        }
        if osMajor < 17 {
            out.append(("ipados_version", "Update iPadOS to version 17.5 or later (Settings > General > Software Update)."))
        }
        return out
    }

    /// Folders the system loads its own code from (dyld shared cache paths included).
    static let systemImagePrefixes = ["/system/", "/usr/lib/", "/developer/", "/private/preboot/cryptexes/"]

    /// Jailbreak / hooking code by its install location: always suspicious wherever it is.
    static let hookPathMarkers = ["/library/mobilesubstrate/", "/usr/lib/tweakinject", "/var/jb/", "/.jbroot",
                                  "/private/var/jb/", "/procursus/"]
    /// Hooking frameworks and tweaks by name: suspicious outside the system's folders.
    static let hookNames = ["substrate", "substitute", "libhooker", "ellekit", "frida", "cycript",
                            "sslkillswitch", "tweakinject", "libblackjack", "shadow.dylib", "flexdylib", "flexing"]

    /// Name of the hooking library this loaded image looks like, or nil. Before 0.5.1 any
    /// path containing "shadow" or "flex" counted, which can match Apple's own libraries
    /// and block a normal iPad in a release build.
    public static func hookLibrary(imagePath: String) -> String? {
        let p = imagePath.lowercased()
        if let m = hookPathMarkers.first(where: { p.contains($0) }) { return m.trimmingCharacters(in: CharacterSet(charactersIn: "/.")) }
        if systemImagePrefixes.contains(where: { p.hasPrefix($0) }) { return nil }
        let file = p.split(separator: "/").last.map(String.init) ?? p
        return hookNames.first { file.contains($0) }
    }

    /// Integrity event to report once per lock for each problem key.
    public static func event(forProblem key: String, state s: DeviceState) -> (String, String)? {
        switch key {
        case "display": return s.captured ? ("screen_captured", "recording or mirroring, displays=\(s.screens)")
                                          : ("extra_display", "count=\(s.screens)")
        case "multitask": return ("multitasking", "window smaller than the screen")
        case "jailbreak": return ("jailbreak_detected", s.jailbreak.prefix(4).joined(separator: ", "))
        case "debugger": return ("debugger_attached", "")
        case "assessment": return ("assessment_interrupted", "Assessment Mode not active")
        case "shield": return ("screen_captured", "capture shield unavailable without Assessment Mode")
        default: return nil
        }
    }
}

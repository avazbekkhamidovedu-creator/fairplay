// swift-tools-version:5.9
import PackageDescription

// Pure logic of the iPad app (no UIKit), so `swift test` checks it on the Mac against
// the server's own values. The app target (ios/FairPlay.xcodeproj) links this package.
let package = Package(
    name: "FairPlayCore",
    platforms: [.iOS("17.5"), .macOS(.v14)],
    products: [.library(name: "FairPlayCore", targets: ["FairPlayCore"])],
    targets: [
        .target(name: "FairPlayCore", path: "Sources/FairPlayCore"),
        .testTarget(name: "FairPlayCoreTests", dependencies: ["FairPlayCore"], path: "Tests/FairPlayCoreTests")
    ]
)

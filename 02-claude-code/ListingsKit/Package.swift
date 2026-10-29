// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ListingsKit",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(name: "ListingsKit", targets: ["ListingsKit"]),
        .library(name: "ListingsKitLive", targets: ["ListingsKitLive"])
    ],
    targets: [
        .target(name: "ListingsKit"),
        .target(
            name: "ListingsKitLive",
            dependencies: ["ListingsKit"]
        ),
        .testTarget(
            name: "ListingsKitTests",
            dependencies: ["ListingsKit"]
        ),
        .testTarget(
            name: "ListingsKitLiveTests",
            dependencies: ["ListingsKitLive"],
            resources: [.process("Resources")]
        )
    ]
)

// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SwitchboardMenu",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "SwitchboardCore", targets: ["SwitchboardCore"]),
        .executable(name: "SwitchboardMenu", targets: ["SwitchboardMenu"]),
    ],
    targets: [
        .target(name: "SwitchboardCore"),
        .executableTarget(name: "SwitchboardMenu", dependencies: ["SwitchboardCore"]),
        .testTarget(name: "SwitchboardCoreTests", dependencies: ["SwitchboardCore"]),
    ]
)

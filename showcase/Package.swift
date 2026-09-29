// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "NVXShowcase",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "NVXShowcase",
            path: "Sources/NVXShowcase"
        )
    ]
)

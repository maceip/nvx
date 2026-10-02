// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "NVXShowcase",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "NVXShowcase", targets: ["NVXShowcase"]),
        .executable(name: "nvx-mcp", targets: ["nvx-mcp"]),
        .library(name: "NVXCore", targets: ["NVXCore"]),
    ],
    targets: [
        .target(
            name: "NVXCore",
            path: "Sources/NVXCore"
        ),
        .executableTarget(
            name: "NVXShowcase",
            dependencies: ["NVXCore"],
            path: "Sources/NVXShowcase"
        ),
        .executableTarget(
            name: "nvx-mcp",
            dependencies: ["NVXCore"],
            path: "Sources/NVXMCP"
        ),
        .testTarget(
            name: "NVXShowcaseTests",
            dependencies: ["NVXShowcase", "NVXCore"]
        )
    ]
)

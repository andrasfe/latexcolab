// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "LaTeXColab",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "LaTeXColab", targets: ["LaTeXColab"]),
        .library(name: "LaTeXColabCore", targets: ["LaTeXColabCore"]),
    ],
    targets: [
        .target(name: "LaTeXColabCore"),
        .executableTarget(
            name: "LaTeXColab",
            dependencies: ["LaTeXColabCore"],
            linkerSettings: [
                .linkedFramework("PDFKit"),
                .linkedFramework("AppKit"),
            ]
        ),
        .testTarget(name: "LaTeXColabCoreTests", dependencies: ["LaTeXColabCore"]),
    ]
)

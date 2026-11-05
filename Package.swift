// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VLCKit",
    platforms: [
        .iOS(.v17)
    ],
    products: [
        .library(name: "VLCKit", targets: ["VLCKit"])
    ],
    targets: [
        .binaryTarget(
            name: "VLCKit",
            url: "https://github.com/HuangQiang11/vlckit/releases/download/4.0.0-vido.1/VLCKit-iOS-arm64.zip",
            checksum: "13ab9ca0a8e546e6bbb2f35b39cb581f1c5dcbd9bd276f9c6c94192f6b3f0d1d"
        )
    ]
)

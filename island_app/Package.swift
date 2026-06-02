// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "TablePetIsland",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "TablePetIsland",
            path: "Sources/TablePetIsland"
        ),
    ]
)

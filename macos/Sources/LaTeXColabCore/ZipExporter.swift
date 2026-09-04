import Foundation

public struct ZipError: Error, LocalizedError {
    public let message: String
    public var errorDescription: String? { message }
}

/// Zips the project folder (as a top-level directory in the archive), skipping
/// `.git`, build artifacts and editor junk — same filter as the file tree.
public enum ZipExporter {
    public static func export(project: URL, to destination: URL) throws {
        let parent = project.deletingLastPathComponent()
        let name = project.lastPathComponent
        let fm = FileManager.default
        if fm.fileExists(atPath: destination.path) {
            try fm.removeItem(at: destination)
        }
        var args = ["-r", "-q", "-X", destination.path, name, "-x"]
        for d in FileTree.excludedDirectories.sorted() {
            args.append("\(name)/\(d)/*")
            args.append("*/\(d)/*")
        }
        for s in FileTree.excludedSuffixes { args.append("*\(s)") }
        for f in FileTree.excludedFiles.sorted() { args.append("*/\(f)") }
        let r = ProcessRunner.run("/usr/bin/zip", args, cwd: parent, timeout: 180)
        guard r.ok else {
            throw ZipError(message: "zip failed (\(r.status)):\n\(r.output)")
        }
    }
}

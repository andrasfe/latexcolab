import Foundation

public struct ProcessResult {
    public let status: Int32
    public let output: String
    public let timedOut: Bool
    public var ok: Bool { status == 0 && !timedOut }
}

/// Synchronous subprocess helper. Call from a background thread.
public enum ProcessRunner {
    public static func run(_ executable: String, _ arguments: [String], cwd: URL? = nil,
                           environment: [String: String]? = nil, timeout: TimeInterval = 120) -> ProcessResult {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: executable)
        p.arguments = arguments
        if let cwd { p.currentDirectoryURL = cwd }
        if let environment { p.environment = environment }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        p.standardInput = FileHandle.nullDevice

        do {
            try p.run()
        } catch {
            return ProcessResult(status: -1, output: "failed to launch \(executable): \(error.localizedDescription)", timedOut: false)
        }

        final class Flag { var value = false }
        let flag = Flag()
        let killer = DispatchWorkItem {
            if p.isRunning {
                flag.value = true
                p.terminate()
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        killer.cancel()
        var out = String(decoding: data, as: UTF8.self)
        if flag.value { out += "\n[timed out after \(Int(timeout))s]" }
        return ProcessResult(status: p.terminationStatus, output: out, timedOut: flag.value)
    }

    /// The directories a GUI app should search in addition to its (minimal) PATH.
    public static var extraSearchPaths: [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return [
            "/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin",
            home + "/.local/bin", home + "/bin", "/opt/conda/bin",
            home + "/miniconda3/bin", home + "/anaconda3/bin",
        ]
    }

    public static func which(_ name: String, extraPaths: [String] = []) -> String? {
        if name.contains("/") {
            return FileManager.default.isExecutableFile(atPath: name) ? name : nil
        }
        let envPath = ProcessInfo.processInfo.environment["PATH"] ?? "/usr/bin:/bin"
        let dirs = envPath.split(separator: ":").map(String.init) + extraPaths + extraSearchPaths
        for d in dirs {
            let candidate = (d as NSString).appendingPathComponent(name)
            if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
        }
        return nil
    }

    public static func glob(_ pattern: String) -> [String] {
        var g = glob_t()
        defer { globfree(&g) }
        guard Darwin.glob(pattern, 0, nil, &g) == 0 else { return [] }
        var out: [String] = []
        for i in 0..<Int(g.gl_matchc) {
            if let c = g.gl_pathv[i] { out.append(String(cString: c)) }
        }
        return out
    }
}

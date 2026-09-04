import Foundation

/// One entry from `git status --porcelain=v2`.
public struct GitChange: Identifiable, Equatable, Hashable {
    public let path: String
    public let originalPath: String?
    /// Two-letter XY code (index, worktree); "??" for untracked.
    public let code: String

    public init(path: String, originalPath: String? = nil, code: String) {
        self.path = path
        self.originalPath = originalPath
        self.code = code
    }

    public var id: String { path }
    public var isUntracked: Bool { code == "??" }
    public var isConflict: Bool { code.contains("U") || code == "AA" || code == "DD" }

    public var summary: String {
        if isUntracked { return "untracked" }
        if isConflict { return "conflict" }
        let x = code.first ?? ".", y = code.dropFirst().first ?? "."
        func word(_ c: Character) -> String? {
            switch c {
            case "M": return "modified"
            case "A": return "added"
            case "D": return "deleted"
            case "R": return "renamed"
            case "C": return "copied"
            case "T": return "type changed"
            default: return nil
            }
        }
        var parts: [String] = []
        if let w = word(x) { parts.append(w + " (staged)") }
        if let w = word(y) { parts.append(w) }
        return parts.isEmpty ? code : parts.joined(separator: ", ")
    }
}

public struct GitStatus: Equatable {
    public var isRepo: Bool
    public var branch: String?
    public var upstream: String?
    public var ahead: Int
    public var behind: Int
    public var changes: [GitChange]
    public var remoteURL: String?

    public init(isRepo: Bool = false, branch: String? = nil, upstream: String? = nil, ahead: Int = 0,
                behind: Int = 0, changes: [GitChange] = [], remoteURL: String? = nil) {
        self.isRepo = isRepo
        self.branch = branch
        self.upstream = upstream
        self.ahead = ahead
        self.behind = behind
        self.changes = changes
        self.remoteURL = remoteURL
    }

    public var isDirty: Bool { !changes.isEmpty }
    public var hasRemote: Bool { remoteURL != nil }
    public var hasUpstream: Bool { upstream != nil }

    /// Parse the output of `git status --porcelain=v2 --branch`.
    public static func parse(porcelain: String) -> GitStatus {
        var s = GitStatus(isRepo: true)
        for line in porcelain.split(separator: "\n", omittingEmptySubsequences: true) {
            if line.hasPrefix("# branch.head ") {
                let v = String(line.dropFirst("# branch.head ".count))
                s.branch = v == "(detached)" ? nil : v
            } else if line.hasPrefix("# branch.upstream ") {
                s.upstream = String(line.dropFirst("# branch.upstream ".count))
            } else if line.hasPrefix("# branch.ab ") {
                let parts = line.dropFirst("# branch.ab ".count).split(separator: " ")
                if parts.count == 2 {
                    s.ahead = Int(parts[0].dropFirst()) ?? 0
                    s.behind = Int(parts[1].dropFirst()) ?? 0
                }
            } else if line.hasPrefix("1 ") {
                let f = line.split(separator: " ", maxSplits: 8, omittingEmptySubsequences: true)
                guard f.count >= 9 else { continue }
                s.changes.append(GitChange(path: String(f[8]), code: String(f[1])))
            } else if line.hasPrefix("2 ") {
                let f = line.split(separator: " ", maxSplits: 9, omittingEmptySubsequences: true)
                guard f.count >= 10 else { continue }
                let paths = f[9].split(separator: "\t", maxSplits: 1)
                s.changes.append(GitChange(path: String(paths[0]),
                                           originalPath: paths.count > 1 ? String(paths[1]) : nil,
                                           code: String(f[1])))
            } else if line.hasPrefix("u ") {
                let f = line.split(separator: " ", maxSplits: 10, omittingEmptySubsequences: true)
                guard f.count >= 11 else { continue }
                s.changes.append(GitChange(path: String(f[10]), code: String(f[1])))
            } else if line.hasPrefix("? ") {
                s.changes.append(GitChange(path: String(line.dropFirst(2)), code: "??"))
            }
        }
        return s
    }
}

public enum GitPullOutcome {
    case success
    case diverged
    case failed
}

/// Thin wrapper over the `git` CLI, run inside the project folder. All calls
/// are blocking — use from a background task. `GIT_TERMINAL_PROMPT=0` keeps
/// git from hanging on a password prompt; use SSH keys or a credential helper.
public final class GitClient {
    public let projectURL: URL
    public let gitPath: String?

    public init(projectURL: URL) {
        self.projectURL = projectURL
        self.gitPath = ProcessRunner.which("git") ?? (FileManager.default.isExecutableFile(atPath: "/usr/bin/git") ? "/usr/bin/git" : nil)
    }

    @discardableResult
    public func run(_ args: [String], timeout: TimeInterval = 120) -> ProcessResult {
        guard let git = gitPath else {
            return ProcessResult(status: -1, output: "git not found. Install the Xcode Command Line Tools: xcode-select --install", timedOut: false)
        }
        var env = ProcessInfo.processInfo.environment
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = "cat"
        env["LC_ALL"] = "en_US.UTF-8"
        let basePath = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = ([basePath] + ProcessRunner.extraSearchPaths).joined(separator: ":")
        return ProcessRunner.run(git, args, cwd: projectURL, environment: env, timeout: timeout)
    }

    public var isRepository: Bool {
        run(["rev-parse", "--is-inside-work-tree"], timeout: 15).output.trimmingCharacters(in: .whitespacesAndNewlines) == "true"
    }

    public func status() -> GitStatus {
        guard gitPath != nil, isRepository else { return GitStatus(isRepo: false) }
        let r = run(["status", "--porcelain=v2", "--branch", "--untracked-files=all"], timeout: 30)
        guard r.ok else { return GitStatus(isRepo: true) }
        var s = GitStatus.parse(porcelain: r.output)
        let remote = run(["remote", "get-url", "origin"], timeout: 15)
        if remote.ok {
            let url = remote.output.trimmingCharacters(in: .whitespacesAndNewlines)
            s.remoteURL = url.isEmpty ? nil : url
        }
        return s
    }

    public func branches() -> [String] {
        let r = run(["branch", "--format=%(refname:short)"], timeout: 15)
        guard r.ok else { return [] }
        return r.output.split(separator: "\n").map { String($0).trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    public func initRepository() -> ProcessResult {
        run(["init", "-q"], timeout: 30)
    }

    public func setRemote(url: String) -> ProcessResult {
        let existing = run(["remote", "get-url", "origin"], timeout: 15)
        if existing.ok {
            return run(["remote", "set-url", "origin", url], timeout: 15)
        }
        return run(["remote", "add", "origin", url], timeout: 15)
    }

    public func fetch() -> ProcessResult {
        run(["fetch", "--prune"], timeout: 180)
    }

    /// Fast-forward pull; reports `.diverged` when the branches need a merge/rebase.
    public func pull(rebase: Bool) -> (GitPullOutcome, ProcessResult) {
        let r = run(rebase ? ["pull", "--rebase"] : ["pull", "--ff-only"], timeout: 300)
        if r.ok { return (.success, r) }
        let out = r.output.lowercased()
        if !rebase && (out.contains("not possible to fast-forward") || out.contains("diverg") || out.contains("fatal: need to specify how to reconcile")) {
            return (.diverged, r)
        }
        return (.failed, r)
    }

    /// Stage the given paths (all changes when nil) and commit.
    public func commit(message: String, paths: [String]?) -> ProcessResult {
        let add: ProcessResult
        if let paths, !paths.isEmpty {
            add = run(["add", "-A", "--"] + paths, timeout: 60)
        } else if paths == nil {
            add = run(["add", "-A"], timeout: 60)
        } else {
            return ProcessResult(status: 1, output: "Nothing selected to commit.", timedOut: false)
        }
        guard add.ok else { return add }
        let r = run(["commit", "-q", "-m", message], timeout: 60)
        if r.ok {
            let show = run(["log", "-1", "--format=%h %s"], timeout: 15)
            return ProcessResult(status: 0, output: add.output + r.output + "committed " + show.output, timedOut: false)
        }
        return ProcessResult(status: r.status, output: add.output + r.output, timedOut: r.timedOut)
    }

    /// Push, setting the upstream on first push.
    public func push(branch: String?, hasUpstream: Bool) -> ProcessResult {
        if hasUpstream {
            return run(["push"], timeout: 300)
        }
        guard let branch else {
            return ProcessResult(status: 1, output: "Detached HEAD — check out a branch before pushing.", timedOut: false)
        }
        return run(["push", "-u", "origin", branch], timeout: 300)
    }

    public func checkout(branch: String, create: Bool) -> ProcessResult {
        run(create ? ["checkout", "-b", branch] : ["checkout", branch], timeout: 60)
    }

    public func log(limit: Int = 20) -> ProcessResult {
        run(["log", "--oneline", "--decorate", "-n", String(limit)], timeout: 30)
    }
}

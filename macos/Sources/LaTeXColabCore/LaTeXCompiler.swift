import Foundation

public struct CompileResult {
    public let ok: Bool
    public let log: String
    public let pdfURL: URL?
    public let engine: String?
}

/// Finds a LaTeX engine the same way the web app does: `$LATEX_ENGINE`, then
/// `latexmk`/`pdflatex`/`tectonic` on PATH, then common no-sudo installs.
public enum EngineFinder {
    static let homeGlobs = [
        "Library/TinyTeX/bin/*", ".TinyTeX/bin/*",
    ]
    static let dirGlobs = [
        "/usr/local/texlive/*/bin/*", "/Library/TeX/texbin",
        "/opt/homebrew/bin", "/usr/local/bin", "/opt/conda/bin",
        "~/miniconda3/bin", "~/anaconda3/bin",
    ]
    public static let preference = ["latexmk", "pdflatex", "tectonic"]

    /// Every directory worth searching: PATH, the usual GUI-invisible install
    /// locations, then TinyTeX / TeX Live globs.
    public static func searchDirectories() -> [String] {
        let envPath = ProcessInfo.processInfo.environment["PATH"] ?? "/usr/bin:/bin"
        var dirs = envPath.split(separator: ":").map(String.init) + ProcessRunner.extraSearchPaths
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for g in homeGlobs { dirs += ProcessRunner.glob(home + "/" + g) }
        for g in dirGlobs { dirs += ProcessRunner.glob((g as NSString).expandingTildeInPath) }
        var seen = Set<String>()
        return dirs.filter { seen.insert($0).inserted }
    }

    /// Pick by engine preference across *all* locations, so a TinyTeX latexmk
    /// beats a Homebrew tectonic even though only the latter is on a GUI app's PATH.
    public static func pick(from dirs: [String], preference: [String] = preference,
                            exists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }) -> String? {
        for name in preference {
            for d in dirs {
                let p = (d as NSString).appendingPathComponent(name)
                if exists(p) { return p }
            }
        }
        return nil
    }

    /// `override` (Settings) or `$LATEX_ENGINE` may be a bare name or a path.
    public static func find(override: String? = nil) -> String? {
        let dirs = searchDirectories()
        for candidate in [override, ProcessInfo.processInfo.environment["LATEX_ENGINE"]] {
            guard let c = candidate?.trimmingCharacters(in: .whitespacesAndNewlines), !c.isEmpty else { continue }
            let expanded = (c as NSString).expandingTildeInPath
            if expanded.contains("/") {
                if FileManager.default.isExecutableFile(atPath: expanded) { return expanded }
            } else if let p = pick(from: dirs, preference: [expanded]) {
                return p
            }
        }
        return pick(from: dirs)
    }

    public static let installHelp = """
    No LaTeX engine found.

    Tried $LATEX_ENGINE, then latexmk / pdflatex / tectonic on PATH and in the
    usual install locations (TinyTeX, MacTeX, Homebrew, conda).

    Install options:
      • brew install --cask basictex          (~100 MB, needs admin)
      • TinyTeX (no admin, ~/Library/TinyTeX):
          curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh
      • Tectonic: https://tectonic-typesetting.github.io
    Relaunch the app after installing.
    """
}

public final class LaTeXCompiler {
    public let projectURL: URL
    public let mainFile: String
    public let engineOverride: String?

    public init(projectURL: URL, mainFile: String, engineOverride: String? = nil) {
        self.projectURL = projectURL
        self.mainFile = mainFile
        self.engineOverride = engineOverride
    }

    public var pdfURL: URL {
        projectURL.appendingPathComponent((mainFile as NSString).deletingPathExtension + ".pdf")
    }

    /// Blocking. Run on a background thread. Tries the preferred engine; if it
    /// fails because of its own environment (missing packages that could not be
    /// installed, unknown primitives) rather than a LaTeX error in the source,
    /// the other installed engines are tried in preference order.
    public func compile(progress: ((String) -> Void)? = nil) -> CompileResult {
        guard FileManager.default.fileExists(atPath: projectURL.appendingPathComponent(mainFile).path) else {
            return CompileResult(ok: false, log: "\(mainFile) not found in \(projectURL.path)", pdfURL: nil, engine: nil)
        }
        guard let engine = EngineFinder.find(override: engineOverride) else {
            return CompileResult(ok: false, log: EngineFinder.installHelp, pdfURL: nil, engine: nil)
        }
        var result = compile(with: engine, progress: progress)
        if result.ok || (engineOverride?.isEmpty == false) { return result }
        guard LaTeXCompiler.isEnvironmentFailure(result.log) else { return result }
        let dirs = EngineFinder.searchDirectories()
        let alternatives = EngineFinder.preference
            .compactMap { EngineFinder.pick(from: dirs, preference: [$0]) }
            .filter { $0 != engine }
        for alt in alternatives {
            let name = (alt as NSString).lastPathComponent
            progress?("\((engine as NSString).lastPathComponent) could not build this document — trying \(name)…")
            let next = compile(with: alt, progress: progress)
            let log = result.log + "\n\n=== \((engine as NSString).lastPathComponent) failed for environment reasons; retrying with \(name) ===\n" + next.log
            result = CompileResult(ok: next.ok, log: log, pdfURL: next.pdfURL, engine: next.engine)
            if next.ok || !LaTeXCompiler.isEnvironmentFailure(next.log) { break }
        }
        return result
    }

    static let environmentFailure = try! NSRegularExpression(
        pattern: #"File `[^']+' not found|not loadable: Metric \(TFM\)|I can't find file|Undefined control sequence[\s\S]{0,120}\\(?:pdf|Xe|Lua)|fontspec[^\n]*(?:XeTeX|LuaTeX|XeLaTeX|LuaLaTeX)|requires (?:either )?(?:XeTeX|LuaTeX|XeLaTeX|LuaLaTeX)|tlmgr install [^\n]*\n[\s\S]*?(?:error|failed|not found)|failed to launch"#)

    /// Failures caused by the engine's installation rather than the document.
    static func isEnvironmentFailure(_ log: String) -> Bool {
        environmentFailure.firstMatch(in: log, range: NSRange(log.startIndex..., in: log)) != nil
    }

    private func compile(with engine: String, progress: ((String) -> Void)?) -> CompileResult {
        let engineDir = (engine as NSString).deletingLastPathComponent
        var env = ProcessInfo.processInfo.environment
        let basePath = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = ([engineDir, basePath] + ProcessRunner.extraSearchPaths).joined(separator: ":")
        env["max_print_line"] = "10000"

        let name = (engine as NSString).lastPathComponent
        let stem = (mainFile as NSString).deletingPathExtension
        let cmd: [String]
        switch name {
        case "latexmk":
            // -g forces a rebuild even when latexmk's cache still records a failed run.
            cmd = ["-pdf", "-g", "-synctex=1", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", mainFile]
        case "tectonic":
            cmd = ["--keep-logs", "--synctex", mainFile]
        default:
            cmd = ["-synctex=1", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", mainFile]
        }

        var log = "$ \(engine) \(cmd.joined(separator: " "))\n"
        progress?("Running \(name)…")

        func runEngine() -> (Int32, String) {
            let r = ProcessRunner.run(engine, cmd, cwd: projectURL, environment: env, timeout: 240)
            return (r.timedOut ? -1 : r.status, r.output)
        }

        var (rc, out) = runEngine()
        log += out

        // TinyTeX is minimal — install missing packages as the paper asks for them.
        let tlmgr = ProcessRunner.which("tlmgr", extraPaths: [engineDir])
        var tried: Set<String> = []
        for _ in 0..<12 {
            if rc == 0 && FileManager.default.fileExists(atPath: pdfURL.path) { break }
            guard let tlmgr else { break }
            let files = MissingFileDetector.missingFiles(in: log)
            if files.isEmpty { break }
            let (pkgs, searchLog) = MissingFileDetector.resolvePackages(tlmgr: tlmgr, files: files, env: env)
            let todo = pkgs.filter { !tried.contains($0) }
            log += "\n--- resolving missing files via tlmgr search ---\n" + searchLog
            if todo.isEmpty { break }
            log += "--- auto-installing: \(todo.joined(separator: ", ")) ---\n"
            progress?("Installing \(todo.joined(separator: ", "))…")
            var installedAny = false
            var selfUpdated = false
            for pkg in todo {
                tried.insert(pkg)
                var r = ProcessRunner.run(tlmgr, ["install", pkg], environment: env, timeout: 300)
                log += "$ tlmgr install \(pkg)\n\(r.output)\n"
                // An outdated tlmgr refuses to install until it updates itself.
                if !selfUpdated && MissingFileDetector.tlmgrNeedsSelfUpdate(r.output) {
                    selfUpdated = true
                    progress?("Updating tlmgr…")
                    let u = ProcessRunner.run(tlmgr, ["update", "--self"], environment: env, timeout: 600)
                    log += "$ tlmgr update --self\n\(u.output)\n"
                    r = ProcessRunner.run(tlmgr, ["install", pkg], environment: env, timeout: 300)
                    log += "$ tlmgr install \(pkg)\n\(r.output)\n"
                }
                if r.ok && !MissingFileDetector.tlmgrFailed(r.output) { installedAny = true }
            }
            if !installedAny { break }
            log += "--- retrying compile ---\n"
            progress?("Retrying \(name)…")
            (rc, out) = runEngine()
            log += out
        }

        // Plain pdflatex needs extra passes for references and bibliographies.
        if name != "latexmk" && name != "tectonic" && rc == 0 {
            let auxURL = projectURL.appendingPathComponent(stem + ".aux")
            if let aux = try? String(contentsOf: auxURL, encoding: .utf8),
               aux.contains("\\bibdata"),
               let bibtex = ProcessRunner.which("bibtex", extraPaths: [engineDir]) {
                progress?("Running bibtex…")
                let r = ProcessRunner.run(bibtex, [stem], cwd: projectURL, environment: env, timeout: 120)
                log += "\n$ bibtex \(stem)\n" + r.output
                (rc, out) = runEngine()
                log += out
            }
            var passes = 0
            while rc == 0 && passes < 2 && MissingFileDetector.needsRerun(log: out) {
                passes += 1
                progress?("Re-running \(name) for cross-references…")
                (rc, out) = runEngine()
                log += "\n--- rerun \(passes) ---\n" + out
            }
        }

        let ok = rc == 0 && FileManager.default.fileExists(atPath: pdfURL.path)
        if log.count > 60_000 { log = "…\n" + log.suffix(60_000) }
        return CompileResult(ok: ok, log: log, pdfURL: ok ? pdfURL : nil, engine: name)
    }
}

public enum MissingFileDetector {
    static let missingFile = try! NSRegularExpression(pattern: #"! LaTeX Error: File `([^']+)' not found"#)
    // "! Font OT1/pcr/m/n/10=pcrr7t at 10.0pt not loadable: Metric (TFM) file not found."
    static let missingFont = try! NSRegularExpression(
        pattern: #"! Font [^=\n]+=([^\s/]+)[^\n]*?(?:not loadable|Metric \(TFM\) file not found)"#)
    static let kpMissing = try! NSRegularExpression(pattern: #"! I can't find file `([^']+)'"#)
    static let pdftexMissing = try! NSRegularExpression(pattern: #"pdfTeX (?:error|warning)[^:]*:\s+[^()]*\(file ([^)]+)\):"#)
    static let rerun = try! NSRegularExpression(pattern: #"Rerun to get|There were undefined references|Label\(s\) may have changed"#)

    static let fileLineError = try! NSRegularExpression(pattern: #"(?m)^(\S+\.(?:tex|sty|cls|bib|bbl):\d+: .+)$"#)
    static let bangError = try! NSRegularExpression(pattern: #"(?m)^! (.+)$"#)
    static let tectonicError = try! NSRegularExpression(pattern: #"(?m)^error: (.+)$"#)

    /// tlmgr's "remote repository is newer than local … update-tlmgr" refusal.
    public static func tlmgrNeedsSelfUpdate(_ output: String) -> Bool {
        let o = output.lowercased()
        return o.contains("update-tlmgr") || o.contains("tlmgr update --self") || o.contains("repository is newer")
    }

    /// tlmgr exits 0 in some failure modes; look at the text as well.
    public static func tlmgrFailed(_ output: String) -> Bool {
        let o = output.lowercased()
        return o.contains("terminating") || o.contains("not present in repository") || o.contains("cannot find package")
    }

    /// The error that stopped the *last* run in a (possibly multi-pass) log,
    /// for a one-line explanation. Earlier passes may have failed on packages
    /// that were installed afterwards.
    public static func firstError(in log: String) -> String? {
        let ns = log as NSString
        let all = NSRange(location: 0, length: ns.length)
        var best: (Int, String)? = nil
        for re in [fileLineError, bangError, tectonicError] {
            if let m = re.matches(in: log, range: all).last {
                let text = ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces)
                if best == nil || m.range.location > best!.0 { best = (m.range.location, text) }
            }
        }
        return best?.1
    }

    public static func needsRerun(log: String) -> Bool {
        rerun.firstMatch(in: log, range: NSRange(log.startIndex..., in: log)) != nil
    }

    public static func missingFiles(in log: String) -> [String] {
        var seen: Set<String> = []
        var out: [String] = []
        func add(_ name: String, _ ext: String) {
            var n = name
            if n.isEmpty { return }
            if !n.contains(".") { n += ext }
            if seen.insert(n).inserted { out.append(n) }
        }
        let ns = log as NSString
        let all = NSRange(location: 0, length: ns.length)
        for m in missingFile.matches(in: log, range: all) { add(ns.substring(with: m.range(at: 1)), ".sty") }
        for m in missingFont.matches(in: log, range: all) { add(ns.substring(with: m.range(at: 1)), ".tfm") }
        for m in kpMissing.matches(in: log, range: all) { add(ns.substring(with: m.range(at: 1)), ".tfm") }
        for m in pdftexMissing.matches(in: log, range: all) { add(ns.substring(with: m.range(at: 1)), "") }
        return out
    }

    /// `tlmgr search --global --file /name` → package names (falls back to the stem).
    public static func resolvePackages(tlmgr: String, files: [String], env: [String: String]) -> ([String], String) {
        var pkgs: [String] = []
        var seen: Set<String> = []
        var searchLog = ""
        for f in files {
            let r = ProcessRunner.run(tlmgr, ["search", "--global", "--file", "/" + f], environment: env, timeout: 90)
            searchLog += "$ tlmgr search --global --file /\(f)\n\(r.output)"
            var found = false
            for line in r.output.split(separator: "\n") {
                if line.hasSuffix(":") && !line.hasPrefix(" ") {
                    let name = String(line.dropLast()).trimmingCharacters(in: .whitespaces)
                    if !name.isEmpty && seen.insert(name).inserted { pkgs.append(name); found = true }
                }
            }
            if !found {
                let stem = f.replacingOccurrences(of: #"\.(sty|cls|ldf|def|tex|cfg|fd|clo|tfm)$"#, with: "", options: .regularExpression)
                if !stem.isEmpty && seen.insert(stem).inserted { pkgs.append(stem) }
            }
        }
        return (pkgs, searchLog)
    }
}

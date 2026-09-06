import Foundation

/// Settings persisted in `~/.latexcolab/config.json` (shared with the web app,
/// which stores `last_project` there). Unknown keys are preserved.
public struct AppConfig {
    public static let fileURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".latexcolab").appendingPathComponent("config.json")

    public var raw: [String: Any]

    public init(raw: [String: Any] = [:]) { self.raw = raw }

    public static func load() -> AppConfig {
        guard let data = try? Data(contentsOf: fileURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return AppConfig() }
        return AppConfig(raw: obj)
    }

    public func save() throws {
        let dir = AppConfig.fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let data = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: AppConfig.fileURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: AppConfig.fileURL.path)
    }

    // MARK: typed accessors

    public var lastProject: String? {
        get { raw["last_project"] as? String }
        set { raw["last_project"] = newValue }
    }
    public var lmStudioURL: String {
        get { (raw["lmstudio_url"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? LMStudioService.defaultBaseURL }
        set { raw["lmstudio_url"] = newValue }
    }
    /// Empty means "use whatever LM Studio has loaded".
    public var model: String {
        get { raw["lmstudio_model"] as? String ?? "" }
        set { raw["lmstudio_model"] = newValue }
    }
    public var temperature: Double {
        get { raw["temperature"] as? Double ?? 0.2 }
        set { raw["temperature"] = newValue }
    }
    /// Empty = automatic (latexmk, then pdflatex, then tectonic, anywhere on disk).
    public var latexEngine: String {
        get { raw["latex_engine"] as? String ?? "" }
        set { raw["latex_engine"] = newValue }
    }
    public var closeWindowAfterApply: Bool {
        get { raw["close_window_after_apply"] as? Bool ?? true }
        set { raw["close_window_after_apply"] = newValue }
    }
    public var autoRegenerateAfterApply: Bool {
        get { raw["auto_regenerate_after_apply"] as? Bool ?? true }
        set { raw["auto_regenerate_after_apply"] = newValue }
    }
    public var defaultMaxWords: Int {
        get { raw["default_max_words"] as? Int ?? 20 }
        set { raw["default_max_words"] = newValue }
    }
    /// Main .tex file per project path.
    public func mainFile(for project: URL) -> String? {
        (raw["main_files"] as? [String: String])?[project.path]
    }
    public mutating func setMainFile(_ name: String, for project: URL) {
        var map = raw["main_files"] as? [String: String] ?? [:]
        map[project.path] = name
        raw["main_files"] = map
    }
}

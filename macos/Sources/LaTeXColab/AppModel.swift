import AppKit
import Combine
import PDFKit
import SwiftUI
import UniformTypeIdentifiers
import LaTeXColabCore

enum ViewMode: String, CaseIterable, Identifiable {
    case pdf = "PDF"
    case editor = "Editor"
    var id: String { rawValue }
}

enum StatusKind {
    case neutral, ok, error, busy
}

/// State of the paragraph currently open in the two-pane editor window.
@MainActor
final class ParagraphSession: ObservableObject, Identifiable {
    let id = UUID()
    let file: String
    @Published var range: ParagraphRange
    @Published var original: String
    @Published var draft: String {
        didSet { if draft != oldValue { onChanged?() } }
    }
    @Published var maxWords: Int {
        didSet { if maxWords != oldValue { onChanged?() } }
    }
    @Published var instructions: String {
        didSet { if instructions != oldValue { onChanged?() } }
    }
    @Published var applied: Bool
    @Published var isBusy = false
    @Published var message = ""
    @Published var messageIsError = false
    @Published var savedAt: Date?
    var editID: UUID?
    var onChanged: (() -> Void)?

    init(file: String, range: ParagraphRange, original: String, draft: String,
         maxWords: Int, instructions: String, editID: UUID?, applied: Bool) {
        self.file = file
        self.range = range
        self.original = original
        self.draft = draft
        self.maxWords = maxWords
        self.instructions = instructions
        self.editID = editID
        self.applied = applied
    }

    var isInSync: Bool {
        EditsStore.normalize(original) == EditsStore.normalize(draft)
    }

    func note(_ text: String, error: Bool = false) {
        message = text
        messageIsError = error
    }
}

@MainActor
final class AppModel: ObservableObject {
    // Project
    @Published var projectURL: URL?
    @Published var tree: [FileNode] = []
    @Published var selectedPath: String?
    @Published var mainFile = "main.tex"
    @Published var engineName: String?

    // Layout / status
    @Published var viewMode: ViewMode = .pdf
    @Published var showLog = false
    @Published var log = ""
    @Published var status = "Open a project folder to begin (⌘O)"
    @Published var statusKind: StatusKind = .neutral
    @Published var isCompiling = false

    // Editor
    @Published var editorPath: String?
    @Published var editorText = ""
    @Published var editorIsBinary = false
    @Published var editorDirty = false

    // PDF preview
    @Published var previewDocument: PDFDocument?
    @Published var previewVersion = 0
    @Published var previewIsMain = true
    @Published var pdfStale = false
    /// Transient message shown over the PDF (click results, mapping errors).
    @Published var pdfNotice: String?
    @Published var pdfNoticeIsError = false
    private var pdfNoticeWork: DispatchWorkItem?

    // Paragraph editing
    @Published var paragraphSession: ParagraphSession?
    @Published var paragraphWindowRequest = 0
    @Published var pendingDraftCount = 0

    @Published var config: AppConfig
    @Published var lmModels: [LMModel] = []

    // Git
    enum GitPrompt: Identifiable {
        case remote, newBranch
        var id: Self { self }
    }
    @Published var git = GitStatus()
    @Published var gitBranches: [String] = []
    @Published var gitBusy = false
    @Published var showCommitSheet = false
    @Published var gitPrompt: GitPrompt?
    private var pushAfterRemote = false
    private var gitRefreshWork: DispatchWorkItem?
    @Published var lmModelsError: String?

    private(set) var editsStore: EditsStore?
    private var autosaveWork: DispatchWorkItem?
    private var draftSaveWork: DispatchWorkItem?
    private var syncScanner: SyncTeXScanner?
    private var syncScannerStamp: Date?

    init() {
        config = AppConfig.load()
        engineName = EngineFinder.find().map { ($0 as NSString).lastPathComponent }
        NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refreshGitStatus() }
        }
        if let last = config.lastProject {
            let url = URL(fileURLWithPath: last)
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue {
                openProject(url)
            }
        }
    }

    // MARK: - Helpers

    var mainStem: String { (mainFile as NSString).deletingPathExtension }
    var mainPDFURL: URL? { projectURL?.appendingPathComponent(mainStem + ".pdf") }
    var editsFileName: String { EditsStore.fileName }

    func setStatus(_ text: String, _ kind: StatusKind = .neutral) {
        status = text
        statusKind = kind
    }

    func saveConfig() {
        do { try config.save() } catch { setStatus("Could not save settings: \(error.localizedDescription)", .error) }
    }

    func notice(_ text: String, error: Bool = false, seconds: Double = 4) {
        pdfNotice = text
        pdfNoticeIsError = error
        pdfNoticeWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.pdfNotice = nil }
        }
        pdfNoticeWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    func toggleViewMode() {
        viewMode = viewMode == .pdf ? .editor : .pdf
    }

    func openSettings() {
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }

    // MARK: - Project

    func chooseProjectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.prompt = "Open"
        panel.message = "Choose a LaTeX project folder"
        panel.directoryURL = projectURL ?? FileManager.default.homeDirectoryForCurrentUser
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in self?.openProject(url) }
        }
    }

    func openProject(_ url: URL) {
        flushEditor()
        if let s = paragraphSession { persistDraft(s) }
        projectURL = url.standardizedFileURL
        selectedPath = nil
        editorPath = nil
        editorText = ""
        editorIsBinary = false
        editorDirty = false
        previewDocument = nil
        previewIsMain = true
        pdfStale = false
        syncScanner = nil
        paragraphSession = nil
        log = ""

        mainFile = config.mainFile(for: projectURL!) ?? "main.tex"
        reloadTree()
        detectMainFile()

        let store = EditsStore(projectURL: projectURL!)
        do { try store.load() } catch {
            setStatus("Could not read \(EditsStore.fileName): \(error.localizedDescription)", .error)
        }
        editsStore = store
        refreshPendingCount()

        config.lastProject = projectURL!.path
        saveConfig()

        if let pdf = mainPDFURL, FileManager.default.fileExists(atPath: pdf.path) {
            loadPDF()
            viewMode = .pdf
        } else {
            viewMode = .editor
        }
        openFile(mainFile, switchToEditor: false)
        refreshGitStatus()
        setStatus("Opened \(url.lastPathComponent)", .ok)
    }

    func reloadTree() {
        guard let root = projectURL else { tree = []; return }
        tree = FileTree.build(root: root)
    }

    private func detectMainFile() {
        guard let root = projectURL else { return }
        let fm = FileManager.default
        if fm.fileExists(atPath: root.appendingPathComponent(mainFile).path) { return }
        if fm.fileExists(atPath: root.appendingPathComponent("main.tex").path) { mainFile = "main.tex"; return }
        for node in FileTree.flattenFiles(tree) where node.fileExtension == "tex" {
            if let h = FileHandle(forReadingAtPath: node.url.path) {
                let head = String(decoding: h.readData(ofLength: 8192), as: UTF8.self)
                try? h.close()
                if head.contains("\\documentclass") { mainFile = node.id; return }
            }
        }
    }

    func setMainFile(_ path: String) {
        guard let root = projectURL else { return }
        mainFile = path
        config.setMainFile(path, for: root)
        saveConfig()
        syncScanner = nil
        if let pdf = mainPDFURL, FileManager.default.fileExists(atPath: pdf.path) { loadPDF() } else { previewDocument = nil }
        setStatus("Main file: \(path)", .ok)
    }

    func selectFromTree(_ path: String) {
        guard let node = FileTree.find(path, in: tree), !node.isDirectory else { return }
        openFile(path)
    }

    func revealInFinder(_ path: String) {
        guard let root = projectURL else { return }
        NSWorkspace.shared.activateFileViewerSelecting([root.appendingPathComponent(path)])
    }

    // MARK: - Editor

    func openFile(_ rel: String, switchToEditor: Bool = true) {
        guard let root = projectURL else { return }
        let url = root.appendingPathComponent(rel)
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), !isDir.boolValue else { return }

        if rel.lowercased().hasSuffix(".pdf") {
            if let doc = PDFDocument(url: url) {
                previewDocument = doc
                previewIsMain = (url.standardizedFileURL == mainPDFURL?.standardizedFileURL)
                previewVersion += 1
                viewMode = .pdf
                setStatus(previewIsMain ? "Showing \(rel)" : "Showing \(rel) (paragraph editing only works on the main PDF)", .neutral)
            }
            return
        }
        if rel == editorPath {
            if switchToEditor { viewMode = .editor }
            return
        }
        flushEditor()
        if FileTree.isTextFile(named: url.lastPathComponent),
           let text = try? String(contentsOf: url, encoding: .utf8) {
            editorText = text.replacingOccurrences(of: "\r\n", with: "\n")
            editorIsBinary = false
        } else {
            editorText = ""
            editorIsBinary = true
        }
        editorPath = rel
        editorDirty = false
        if switchToEditor { viewMode = .editor }
        if selectedPath != rel { selectedPath = rel }
    }

    /// Called by the editor view on every keystroke (the binding already holds the text).
    func editorTextChanged(_ text: String) {
        guard !editorIsBinary, editorPath != nil else { return }
        editorDirty = true
        if statusKind != .busy { setStatus("Editing…") }
        autosaveWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.saveEditor() }
        }
        autosaveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8, execute: work)
    }

    func flushEditor() {
        autosaveWork?.cancel()
        autosaveWork = nil
        if editorDirty { saveEditor() }
    }

    func saveEditor() {
        guard let root = projectURL, let rel = editorPath, !editorIsBinary, editorDirty else { return }
        autosaveWork?.cancel()
        let url = root.appendingPathComponent(rel)
        do {
            try editorText.write(to: url, atomically: true, encoding: .utf8)
            editorDirty = false
            if rel.lowercased().hasSuffix(".tex") || rel.lowercased().hasSuffix(".bib") { pdfStale = previewDocument != nil }
            if statusKind != .busy { setStatus("Saved \(rel)", .ok) }
            scheduleGitRefresh()
        } catch {
            setStatus("Save failed: \(error.localizedDescription)", .error)
        }
    }

    // MARK: - Compile

    func regenerate() {
        guard let root = projectURL, !isCompiling else { return }
        flushEditor()
        isCompiling = true
        setStatus("Compiling \(mainFile)…", .busy)
        let compiler = LaTeXCompiler(projectURL: root, mainFile: mainFile)
        let model = self
        Task.detached(priority: .userInitiated) {
            let result = compiler.compile { msg in
                Task { @MainActor in model.setStatus(msg, .busy) }
            }
            await MainActor.run { model.finishCompile(result) }
        }
    }

    private func finishCompile(_ result: CompileResult) {
        isCompiling = false
        log = result.log
        if let e = result.engine { engineName = e }
        if result.ok {
            syncScanner = nil
            loadPDF()
            pdfStale = false
            viewMode = .pdf
            setStatus("PDF generated with \(result.engine ?? "LaTeX")", .ok)
        } else {
            showLog = true
            setStatus("Compile failed — see the build log", .error)
        }
        reloadTree()
        scheduleGitRefresh()
    }

    func loadPDF() {
        guard let url = mainPDFURL, let doc = PDFDocument(url: url) else { return }
        previewDocument = doc
        previewIsMain = true
        previewVersion += 1
    }

    // MARK: - Zip

    func exportZip() {
        guard let root = projectURL else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = root.lastPathComponent + ".zip"
        panel.allowedContentTypes = [.zip]
        panel.canCreateDirectories = true
        panel.title = "Export project as zip"
        panel.begin { [weak self] response in
            guard response == .OK, let dest = panel.url else { return }
            Task { @MainActor in
                guard let self else { return }
                self.flushEditor()
                self.setStatus("Zipping \(root.lastPathComponent)…", .busy)
                let outcome: Result<Void, Error> = await Task.detached {
                    Result { try ZipExporter.export(project: root, to: dest) }
                }.value
                switch outcome {
                case .success:
                    self.setStatus("Exported \(dest.lastPathComponent)", .ok)
                    NSWorkspace.shared.activateFileViewerSelecting([dest])
                case .failure(let err):
                    self.setStatus("Zip failed: \(err.localizedDescription)", .error)
                    self.log += "\n" + err.localizedDescription
                    self.showLog = true
                }
            }
        }
    }

    // MARK: - PDF click → paragraph

    private func loadSyncScanner() -> SyncTeXScanner? {
        guard let root = projectURL, let pdf = mainPDFURL,
              let file = SyncTeXScanner.locateFile(forPDF: pdf) else { return nil }
        let stamp = (try? FileManager.default.attributesOfItem(atPath: file.path)[.modificationDate] as? Date) ?? Date()
        if let s = syncScanner, syncScannerStamp == stamp { return s }
        do {
            let s = try SyncTeXScanner(fileURL: file, baseURL: root)
            syncScanner = s
            syncScannerStamp = stamp
            return s
        } catch {
            log += "\nSyncTeX read failed: \(error.localizedDescription)"
            return nil
        }
    }

    func handlePDFClick(pageIndex: Int, pagePoint: CGPoint, pageBounds: CGRect, nearbyText: String?) {
        guard let root = projectURL else { return }
        guard previewIsMain else {
            notice("Paragraph editing works on the main PDF (\(mainStem).pdf) only", error: true)
            return
        }
        guard !isCompiling else {
            notice("Wait for the compile to finish", error: true)
            return
        }
        flushEditor()
        NSLog("PDF click: page %d at (%.1f, %.1f)", pageIndex + 1, pagePoint.x, pagePoint.y)

        let x = Double(pagePoint.x - pageBounds.minX)
        let yFromTop = Double(pageBounds.maxY - pagePoint.y)

        var target: (file: String, range: ParagraphRange)? = nil
        var how = "SyncTeX"
        if let scanner = loadSyncScanner(),
           let loc = scanner.editQuery(page: pageIndex + 1, x: x, y: yFromTop),
           loc.file.path.hasPrefix(root.path + "/"),
           let source = try? String(contentsOf: loc.file, encoding: .utf8),
           let para = ParagraphLocator.paragraph(in: source, containingLine: loc.line) {
            target = (FileTree.relativePath(of: loc.file, root: root), para)
        }
        if target == nil, let text = nearbyText, !text.isEmpty {
            let sources = FileTree.flattenFiles(tree)
                .filter { $0.fileExtension == "tex" }
                .compactMap { n in (try? String(contentsOf: n.url, encoding: .utf8)).map { (path: n.id, source: $0) } }
            if let m = TextMatcher.bestMatch(pdfText: text, sources: sources) {
                target = (m.file, m.paragraph)
                how = "text match"
            }
        }
        guard let t = target else {
            let why = syncScanner == nil ? "no SyncTeX data — press Regenerate to rebuild the PDF with it" : "nothing in the sources matched that spot"
            notice("Couldn't map that click to a paragraph (\(why))", error: true, seconds: 6)
            setStatus("Click did not map to a paragraph", .error)
            return
        }
        openParagraphSession(file: t.file, range: t.range)
        let where_ = "\(t.file) lines \(t.range.startLine)–\(t.range.endLine)"
        notice("Opened \(where_) via \(how)" + (pdfStale ? " · PDF is out of date, regenerate for exact positions" : ""))
        setStatus(where_, .ok)
    }

    // MARK: - Paragraph sessions

    func openParagraphSession(file: String, range: ParagraphRange) {
        if let s = paragraphSession, s.file == file,
           EditsStore.normalize(s.original) == EditsStore.normalize(range.text) {
            s.range = range
            paragraphWindowRequest += 1
            return
        }
        if let s = paragraphSession { persistDraft(s) }

        let existing = editsStore?.find(file: file, original: range.text)
        let session = ParagraphSession(
            file: file, range: range, original: range.text,
            draft: existing?.draft ?? range.text,
            maxWords: existing?.maxWords ?? config.defaultMaxWords,
            instructions: existing?.instructions ?? "",
            editID: existing?.id,
            applied: existing?.applied ?? false)
        session.onChanged = { [weak self, weak session] in
            guard let self, let session else { return }
            self.scheduleDraftSave(session)
        }
        if let e = existing {
            if session.isInSync {
                session.note(e.applied ? "In sync — this rewrite was applied \(Self.relative(e.updatedAt))." : "In sync with the document.")
            } else {
                session.note("Draft from \(Self.relative(e.updatedAt)) differs from the document — not applied yet.")
            }
        } else {
            session.note("Edit the right side, use AI Fix, then Apply to write it into \(file).")
        }
        paragraphSession = session
        paragraphWindowRequest += 1
    }

    private static func relative(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f.localizedString(for: date, relativeTo: Date())
    }

    private func scheduleDraftSave(_ session: ParagraphSession) {
        draftSaveWork?.cancel()
        let work = DispatchWorkItem { [weak self, weak session] in
            Task { @MainActor in
                guard let self, let session else { return }
                self.persistDraft(session)
            }
        }
        draftSaveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6, execute: work)
    }

    /// Write the session into latexcolab-edits.json. A record is only created
    /// once the draft differs from the document (or on Apply, with `force`).
    @discardableResult
    func persistDraft(_ session: ParagraphSession, force: Bool = false) -> Bool {
        guard let store = editsStore else { return false }
        let now = Date()
        if let id = session.editID, var e = store.find(id: id) {
            e.original = session.original
            e.draft = session.draft
            e.maxWords = session.maxWords
            e.instructions = session.instructions
            e.lineHint = session.range.startLine
            e.applied = session.applied
            e.updatedAt = now
            store.upsert(e)
        } else {
            guard force || !session.isInSync else { return false }
            let e = ParagraphEdit(file: session.file, original: session.original, draft: session.draft,
                                  applied: session.applied, lineHint: session.range.startLine,
                                  maxWords: session.maxWords, instructions: session.instructions,
                                  createdAt: now, updatedAt: now)
            store.upsert(e)
            session.editID = e.id
        }
        do {
            try store.save()
            session.savedAt = now
        } catch {
            session.note("Could not write \(EditsStore.fileName): \(error.localizedDescription)", error: true)
            return false
        }
        refreshPendingCount()
        return true
    }

    func saveDraftNow(_ session: ParagraphSession) {
        draftSaveWork?.cancel()
        if persistDraft(session, force: true) {
            session.note("Draft saved to \(EditsStore.fileName) (not applied to the document).")
        }
    }

    func discardDraft(_ session: ParagraphSession) {
        draftSaveWork?.cancel()
        if let id = session.editID, let store = editsStore {
            store.remove(id: id)
            try? store.save()
            session.editID = nil
        }
        session.applied = false
        session.draft = session.original
        session.note("Draft discarded — right side reset to the document text.")
        refreshPendingCount()
    }

    func applyParagraph(_ session: ParagraphSession) {
        guard let root = projectURL else { return }
        draftSaveWork?.cancel()
        if editorPath == session.file { flushEditor() }
        let url = root.appendingPathComponent(session.file)
        guard let source = try? String(contentsOf: url, encoding: .utf8) else {
            session.note("Could not read \(session.file).", error: true)
            return
        }
        guard let range = ParagraphLocator.locate(expected: session.original, near: session.range, in: source) else {
            session.note("The paragraph no longer exists in \(session.file) as it was when opened. Close this window and click it again.", error: true)
            return
        }
        let newText = session.draft.replacingOccurrences(of: "\r\n", with: "\n").trimmingCharacters(in: .newlines)
        let updated = ParagraphLocator.replacing(range, in: source, with: newText)
        do {
            try updated.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            session.note("Could not write \(session.file): \(error.localizedDescription)", error: true)
            return
        }
        let lineCount = newText.components(separatedBy: "\n").count
        session.range = ParagraphRange(startLine: range.startLine, endLine: range.startLine + lineCount - 1, text: newText)
        session.original = newText
        session.draft = newText
        session.applied = true
        persistDraft(session, force: true)
        if editorPath == session.file {
            editorText = updated
            editorDirty = false
        }
        pdfStale = true
        scheduleGitRefresh()
        session.note("Applied to \(session.file) lines \(session.range.startLine)–\(session.range.endLine)." + (config.autoRegenerateAfterApply ? " Regenerating PDF…" : ""))
        setStatus("Applied paragraph edit to \(session.file)", .ok)
        if config.autoRegenerateAfterApply { regenerate() }
    }

    func refreshLMModels() async {
        let service = LMStudioService(baseURL: config.lmStudioURL)
        do {
            lmModels = try await service.listModels()
            lmModelsError = nil
        } catch {
            lmModels = []
            lmModelsError = error.localizedDescription
        }
    }

    func aiFix(_ session: ParagraphSession) {
        guard !session.isBusy else { return }
        session.isBusy = true
        let cfg = config
        let service = LMStudioService(baseURL: cfg.lmStudioURL)
        Task { [weak self, weak session] in
            guard let session else { return }
            var model = cfg.model
            if model.isEmpty {
                session.note("Asking LM Studio which model is loaded…")
                if let models = try? await service.listModels() {
                    self?.lmModels = models
                    model = LMStudioService.pickModel(from: models) ?? ""
                }
            }
            guard !model.isEmpty else {
                session.isBusy = false
                session.note(LMStudioError.noModel.localizedDescription, error: true)
                self?.openSettings()
                return
            }
            session.note("Rewriting with \(model) via LM Studio (max \(session.maxWords) words)…")
            let request = CleanupRequest(paragraph: session.draft, maxWords: session.maxWords,
                                         instructions: session.instructions, model: model,
                                         temperature: cfg.temperature)
            do {
                let result = try await service.cleanUp(request)
                session.draft = result.text
                let changed = WordDiff.changedWordCount(WordDiff.diff(old: session.original, new: result.text))
                let shown = result.model.isEmpty ? model : result.model
                session.note("AI fix done: \(changed) word(s) now differ from the document (limit \(session.maxWords)) · \(shown) · \(result.promptTokens)→\(result.completionTokens) tokens")
            } catch {
                session.note(error.localizedDescription, error: true)
            }
            session.isBusy = false
        }
    }


    // MARK: - Git

    private var gitClient: GitClient? {
        projectURL.map { GitClient(projectURL: $0) }
    }

    func appendLog(_ text: String) {
        log += (log.isEmpty ? "" : "\n") + text.trimmingCharacters(in: .newlines)
    }

    func scheduleGitRefresh() {
        gitRefreshWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.refreshGitStatus() }
        }
        gitRefreshWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: work)
    }

    func refreshGitStatus() {
        guard let client = gitClient else {
            git = GitStatus()
            gitBranches = []
            return
        }
        let model = self
        Task.detached(priority: .utility) {
            let status = client.status()
            let branches = status.isRepo ? client.branches() : []
            await MainActor.run {
                model.git = status
                model.gitBranches = branches
            }
        }
    }

    /// Re-read the open editor file when git changed it on disk (pull, checkout).
    private func reloadEditorIfChanged() {
        guard let root = projectURL, let rel = editorPath, !editorIsBinary, !editorDirty else { return }
        let url = root.appendingPathComponent(rel)
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return }
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
        if normalized != editorText { editorText = normalized }
    }

    private func afterGitChange() {
        reloadTree()
        reloadEditorIfChanged()
        if let pdf = mainPDFURL, FileManager.default.fileExists(atPath: pdf.path) { pdfStale = true }
        refreshGitStatus()
    }

    /// Runs one git operation off the main thread, logs it, and refreshes state.
    private func runGit(_ label: String, command: String, _ op: @escaping (GitClient) -> ProcessResult,
                        completion: ((ProcessResult) -> Void)? = nil) {
        guard let client = gitClient, !gitBusy else { return }
        flushEditor()
        gitBusy = true
        setStatus("\(label)…", .busy)
        let model = self
        Task.detached {
            let r = op(client)
            await MainActor.run {
                model.gitBusy = false
                model.appendLog("$ git \(command)\n\(r.output)")
                if r.ok {
                    model.setStatus("\(label) done", .ok)
                } else {
                    model.setStatus("\(label) failed — see the log", .error)
                    model.showLog = true
                }
                model.afterGitChange()
                completion?(r)
            }
        }
    }

    func gitInit() {
        runGit("Initialize repository", command: "init") { $0.initRepository() }
    }

    func gitFetch() {
        runGit("Fetch", command: "fetch --prune") { $0.fetch() }
    }

    func gitPull(rebase: Bool = false) {
        guard let client = gitClient, !gitBusy else { return }
        flushEditor()
        gitBusy = true
        setStatus(rebase ? "Pulling with rebase…" : "Pulling…", .busy)
        let model = self
        Task.detached {
            let (outcome, r) = client.pull(rebase: rebase)
            await MainActor.run {
                model.gitBusy = false
                model.appendLog("$ git pull \(rebase ? "--rebase" : "--ff-only")\n\(r.output)")
                model.afterGitChange()
                switch outcome {
                case .success:
                    model.setStatus("Pulled \(model.git.upstream ?? "from remote")", .ok)
                case .diverged:
                    model.setStatus("Local and remote branches have diverged", .error)
                    model.askToRebase()
                case .failed:
                    model.setStatus("Pull failed — see the log", .error)
                    model.showLog = true
                }
            }
        }
    }

    private func askToRebase() {
        let alert = NSAlert()
        alert.messageText = "Local and remote branches have diverged"
        alert.informativeText = "A fast-forward pull is not possible because both sides have new commits. Rebase your local commits on top of the remote branch?"
        alert.addButton(withTitle: "Pull with Rebase")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            gitPull(rebase: true)
        }
    }

    func openCommitSheet() {
        guard projectURL != nil else { return }
        guard git.isRepo else {
            let alert = NSAlert()
            alert.messageText = "This folder is not a git repository"
            alert.informativeText = "Initialize one here so you can commit and push?"
            alert.addButton(withTitle: "Initialize Repository")
            alert.addButton(withTitle: "Cancel")
            if alert.runModal() == .alertFirstButtonReturn { gitInit() }
            return
        }
        flushEditor()
        refreshGitStatus()
        showCommitSheet = true
    }

    func gitCommit(message: String, paths: [String]?, thenPush: Bool) {
        let msg = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !msg.isEmpty else { return }
        runGit("Commit", command: "add … && git commit -m \"\(msg)\"", { $0.commit(message: msg, paths: paths) }) { r in
            if r.ok, thenPush { self.gitPush() }
        }
    }

    func gitPush() {
        guard git.hasRemote else {
            pushAfterRemote = true
            gitPrompt = .remote
            return
        }
        let branch = git.branch
        let hasUpstream = git.hasUpstream
        runGit("Push", command: hasUpstream ? "push" : "push -u origin \(branch ?? "HEAD")") {
            $0.push(branch: branch, hasUpstream: hasUpstream)
        }
    }

    func gitSetRemote(url: String) {
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let push = pushAfterRemote
        pushAfterRemote = false
        runGit("Set remote", command: "remote add/set-url origin \(trimmed)", { $0.setRemote(url: trimmed) }) { r in
            if r.ok, push { self.gitPush() }
        }
    }

    func gitCheckout(branch: String, create: Bool) {
        let name = branch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        runGit(create ? "Create branch \(name)" : "Switch to \(name)",
               command: create ? "checkout -b \(name)" : "checkout \(name)") {
            $0.checkout(branch: name, create: create)
        }
    }

    func gitLog() {
        runGit("Recent commits", command: "log --oneline -n 20") { $0.log(limit: 20) } completion: { _ in
            self.showLog = true
        }
    }

    func refreshPendingCount() {
        pendingDraftCount = editsStore?.pendingCount ?? 0
    }
}

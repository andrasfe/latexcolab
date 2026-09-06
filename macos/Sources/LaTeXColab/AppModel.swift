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

/// What a click on the PDF should open.
enum PDFEditMode {
    case paragraph
    case sentence(word: String?)
    case selection(text: String, endPageIndex: Int, endPoint: CGPoint)
}

/// Granularity of the text in the paragraph window.
enum EditScope: Equatable {
    case paragraph, sentence, selection
    var label: String {
        switch self {
        case .paragraph: return "Paragraph"
        case .sentence: return "Sentence"
        case .selection: return "Selection"
        }
    }
}

/// A region of the PDF to scroll to and flash, in bp with a top-left origin.
struct PDFFocus: Equatable {
    let id = UUID()
    let pageIndex: Int
    let rect: CGRect
}

/// State of the paragraph currently open in the two-pane editor window.
@MainActor
final class ParagraphSession: ObservableObject, Identifiable {
    let id = UUID()
    let file: String
    let scope: EditScope
    /// The whole paragraph (or line range) that contains the edited text.
    @Published var container: String
    /// UTF-16 offset of `original` inside `container` (0 for whole paragraphs).
    var spanOffset: Int
    @Published var range: ParagraphRange
    @Published var original: String
    /// Which parts of `draft` the AI wrote; re-aligned on every change.
    @Published var provenance = Provenance()
    /// Set by the model just before assigning an AI-produced draft.
    var nextDraftChangeIsAI = false
    @Published var draft: String {
        didSet {
            if draft != oldValue {
                provenance = provenance.remapped(from: oldValue, to: draft, insertedByAI: nextDraftChangeIsAI)
                nextDraftChangeIsAI = false
                onChanged?()
            }
        }
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

    // Earlier versions of this paragraph (oldest first) and the sheet that shows them
    @Published var history: [ParagraphVersion] = []
    @Published var showHistory = false

    // AI-fix outcome: rewordings the budget did not allow (author may apply by hand)
    @Published var droppedSuggestions: [EditSuggestion] = []
    @Published var lastFixSummary: String?

    // Semantic comparison of the two sides
    @Published var comparison: SemanticComparison?
    @Published var integrityIssues: [SemanticChecks.Issue] = []
    @Published var isComparing = false
    @Published var showComparison = false
    var comparedOriginal: String?
    var comparedDraft: String?

    /// True when either side changed after the last comparison ran.
    var comparisonIsStale: Bool {
        guard showComparison, comparedDraft != nil else { return false }
        return comparedDraft != draft || comparedOriginal != original
    }

    init(file: String, range: ParagraphRange, original: String, draft: String,
         maxWords: Int, instructions: String, editID: UUID?, applied: Bool,
         scope: EditScope = .paragraph, container: String? = nil, spanOffset: Int = 0) {
        self.file = file
        self.scope = scope
        self.container = container ?? original
        self.spanOffset = spanOffset
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

    var isPartial: Bool { scope != .paragraph }

    /// Up to `n` characters of the paragraph before/after the edited span.
    func context(chars n: Int = 70) -> (before: String, after: String) {
        let ns = container as NSString
        let start = max(0, min(spanOffset, ns.length))
        let end = min(ns.length, start + (original as NSString).length)
        let before = ns.substring(with: NSRange(location: max(0, start - n), length: start - max(0, start - n)))
        let after = ns.substring(with: NSRange(location: end, length: min(n, ns.length - end)))
        func squash(_ t: String) -> String { t.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "  ", with: " ") }
        return ((start > n ? "…" : "") + squash(before), squash(after) + (ns.length - end > n ? "…" : ""))
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
    /// First error line of the last failed compile; shown as a persistent banner.
    @Published var compileError: String?
    /// Paragraph to scroll to and flash once the next successful compile lands.
    private var pendingFocus: (file: String, range: ParagraphRange)?
    /// Where the PDF view should scroll/highlight (set after Apply + regenerate).
    @Published var pdfFocus: PDFFocus?
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
        refreshEngine()
        NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refreshGitStatus() }
        }
        // LATEXCOLAB_PROJECT=/path opens that folder without touching the remembered one.
        if let forced = ProcessInfo.processInfo.environment["LATEXCOLAB_PROJECT"], !forced.isEmpty {
            let url = URL(fileURLWithPath: (forced as NSString).expandingTildeInPath)
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue {
                rememberProject = false
                openProject(url)
                // LATEXCOLAB_OPEN_PARAGRAPH=sections/intro.tex:12[:sentence] opens that paragraph (or the
                // sentence starting on that line) — for scripted testing.
                if let spec = ProcessInfo.processInfo.environment["LATEXCOLAB_OPEN_PARAGRAPH"] {
                    var parts = spec.split(separator: ":").map(String.init)
                    let scope: EditScope = parts.last == "sentence" ? .sentence : .paragraph
                    if scope == .sentence { parts.removeLast() }
                    if parts.count >= 2, let line = Int(parts.last!) {
                        let file = parts.dropLast().joined(separator: ":")
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                            Task { @MainActor in self?.openParagraph(file: file, line: line, scope: scope) }
                        }
                    }
                }
                return
            }
        }
        if let last = config.lastProject {
            let url = URL(fileURLWithPath: last)
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue {
                openProject(url)
            }
        }
    }

    private var rememberProject = true

    // MARK: - Helpers

    var mainStem: String { (mainFile as NSString).deletingPathExtension }
    var mainPDFURL: URL? { projectURL?.appendingPathComponent(mainStem + ".pdf") }
    var editsFileName: String { EditsStore.fileName }

    func setStatus(_ text: String, _ kind: StatusKind = .neutral) {
        status = text
        statusKind = kind
    }

    /// Full path of the engine that will be used, or nil.
    @Published var enginePath: String?

    func refreshEngine() {
        enginePath = EngineFinder.find(override: config.latexEngine)
        engineName = enginePath.map { ($0 as NSString).lastPathComponent }
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

        if rememberProject {
            config.lastProject = projectURL!.path
            saveConfig()
        }
        rememberProject = true

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
        let compiler = LaTeXCompiler(projectURL: root, mainFile: mainFile, engineOverride: config.latexEngine)
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
            compileError = nil
            loadPDF()
            pdfStale = false
            viewMode = .pdf
            setStatus("PDF generated with \(result.engine ?? "LaTeX")", .ok)
            focusPendingParagraph()
        } else {
            let applied = pendingFocus
            pendingFocus = nil
            showLog = true
            let first = MissingFileDetector.firstError(in: result.log) ?? "see the build log for details"
            compileError = (applied.map { "Compile failed after applying \($0.file) lines \($0.range.startLine)–\($0.range.endLine): " } ?? "Compile failed: ") + first
            viewMode = .pdf
            setStatus("Compile failed — the PDF still shows the previous build", .error)
        }
        reloadTree()
        scheduleGitRefresh()
    }

    /// SyncTeX forward search for the paragraph that was just applied.
    private func focusPendingParagraph() {
        guard let target = pendingFocus else { return }
        pendingFocus = nil
        guard let root = projectURL, let scanner = loadSyncScanner() else { return }
        let url = root.appendingPathComponent(target.file)
        if let hit = scanner.displayQuery(file: url, lines: target.range.startLine...target.range.endLine) {
            pdfFocus = PDFFocus(pageIndex: hit.page - 1, rect: hit.rect)
            notice("Updated \(target.file) lines \(target.range.startLine)–\(target.range.endLine) — showing the new PDF")
        }
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

    /// SyncTeX/text-match resolution of one PDF point to (file, paragraph).
    private func resolveParagraph(pageIndex: Int, pagePoint: CGPoint, pageBounds: CGRect, nearbyText: String?) -> (file: String, range: ParagraphRange, line: Int?, how: String)? {
        guard let root = projectURL else { return nil }
        let x = Double(pagePoint.x - pageBounds.minX)
        let yFromTop = Double(pageBounds.maxY - pagePoint.y)
        if let scanner = loadSyncScanner(),
           let loc = scanner.editQuery(page: pageIndex + 1, x: x, y: yFromTop),
           loc.file.path.hasPrefix(root.path + "/"),
           let source = try? String(contentsOf: loc.file, encoding: .utf8),
           let para = ParagraphLocator.paragraph(in: source, containingLine: loc.line) {
            return (FileTree.relativePath(of: loc.file, root: root), para, loc.line, "SyncTeX")
        }
        if let text = nearbyText, !text.isEmpty {
            let sources = FileTree.flattenFiles(tree)
                .filter { $0.fileExtension == "tex" }
                .compactMap { n in (try? String(contentsOf: n.url, encoding: .utf8)).map { (path: n.id, source: $0) } }
            if let m = TextMatcher.bestMatch(pdfText: text, sources: sources) {
                return (m.file, m.paragraph, nil, "text match")
            }
        }
        return nil
    }

    func handlePDFClick(pageIndex: Int, pagePoint: CGPoint, pageBounds: CGRect, nearbyText: String?, mode: PDFEditMode = .paragraph) {
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

        guard let t = resolveParagraph(pageIndex: pageIndex, pagePoint: pagePoint, pageBounds: pageBounds, nearbyText: nearbyText) else {
            let why = syncScanner == nil ? "no SyncTeX data — press Regenerate to rebuild the PDF with it" : "nothing in the sources matched that spot"
            notice("Couldn't map that click to a paragraph (\(why))", error: true, seconds: 6)
            setStatus("Click did not map to a paragraph", .error)
            return
        }
        var range = t.range
        var span: NSRange? = nil
        var scope: EditScope = .paragraph
        var fallbackNote = ""

        switch mode {
        case .paragraph:
            break
        case .sentence(let word):
            let lineOffset = SelectionMapper.offset(ofLine: max(1, (t.line ?? range.startLine) - range.startLine + 1), in: range.text)
            if let w = word, let r = SelectionMapper.sentenceRange(in: range.text, containingPDFWord: w, nearOffset: lineOffset) {
                span = r
            } else {
                span = SelectionMapper.sentenceRange(in: range.text, containing: lineOffset)
            }
            scope = .sentence
        case .selection(let text, let endPageIndex, let endPoint):
            // The selection may run into a later paragraph of the same file.
            if let e = resolveParagraph(pageIndex: endPageIndex, pagePoint: endPoint, pageBounds: pageBounds, nearbyText: nil),
               e.file == t.file, e.range.endLine > range.endLine,
               let source = try? String(contentsOf: root.appendingPathComponent(t.file), encoding: .utf8) {
                let lines = ParagraphLocator.lines(of: source)
                if e.range.endLine <= lines.count {
                    range = ParagraphRange(startLine: range.startLine, endLine: e.range.endLine,
                                           text: lines[(range.startLine - 1)...(e.range.endLine - 1)].joined(separator: "\n"))
                }
            }
            if let r = SelectionMapper.sourceRange(forPDFText: text, in: range.text) {
                span = r
                scope = .selection
            } else {
                fallbackNote = " · couldn't match the selected text exactly, opened the whole paragraph"
            }
        }
        if let r = span, r.length > 0, r.length < (range.text as NSString).length {
            openParagraphSession(file: t.file, range: range, span: r, scope: scope)
        } else {
            openParagraphSession(file: t.file, range: range)
            scope = .paragraph
        }
        let where_ = "\(scope.label.lowercased()) in \(t.file) lines \(range.startLine)–\(range.endLine)"
        notice("Opened \(where_) via \(t.how)" + fallbackNote + (pdfStale ? " · PDF is out of date, regenerate for exact positions" : ""))
        setStatus(where_, .ok)
    }

    // MARK: - Paragraph sessions

    /// Open the paragraph containing `line` of `file` (project-relative), or
    /// the sentence that starts on that line.
    func openParagraph(file: String, line: Int, scope: EditScope = .paragraph) {
        guard let root = projectURL,
              let source = try? String(contentsOf: root.appendingPathComponent(file), encoding: .utf8),
              let para = ParagraphLocator.paragraph(in: source, containingLine: line) else {
            setStatus("No paragraph at \(file):\(line)", .error)
            return
        }
        if scope == .sentence {
            let offset = SelectionMapper.offset(ofLine: line - para.startLine + 1, in: para.text)
            let r = SelectionMapper.sentenceRange(in: para.text, containing: offset)
            if r.length > 0 && r.length < (para.text as NSString).length {
                openParagraphSession(file: file, range: para, span: r, scope: .sentence)
                return
            }
        }
        openParagraphSession(file: file, range: para)
    }

    func openParagraphSession(file: String, range: ParagraphRange, span: NSRange? = nil, scope: EditScope = .paragraph) {
        let text = span.map { (range.text as NSString).substring(with: $0) } ?? range.text
        if let s = paragraphSession, s.file == file, s.scope == scope,
           EditsStore.normalize(s.original) == EditsStore.normalize(text) {
            s.range = range
            s.container = range.text
            s.spanOffset = span?.location ?? 0
            paragraphWindowRequest += 1
            return
        }
        if let s = paragraphSession { persistDraft(s) }

        var existing: ParagraphEdit? = nil
        var adopted = false
        if let store = editsStore, let m = store.match(file: file, original: text, nearLine: range.startLine, partial: scope != .paragraph) {
            var e = m.edit
            if !m.exact {
                // The document text changed outside the app (hand edit or manual
                // revert): keep the record and its history, follow the new text.
                let wasInSync = e.isInSync
                e.original = text
                if wasInSync { e.draft = text }
                e.lineHint = range.startLine
                e.updatedAt = Date()
                store.upsert(e)
                try? store.save()
                adopted = true
            }
            existing = e
        }
        let session = ParagraphSession(
            file: file, range: range, original: text,
            draft: existing?.draft ?? text,
            maxWords: existing?.maxWords ?? config.defaultMaxWords,
            instructions: existing?.instructions ?? "",
            editID: existing?.id,
            applied: existing?.applied ?? false,
            scope: scope, container: range.text, spanOffset: span?.location ?? 0)
        session.history = existing?.history ?? []
        if let e = existing, let prov = e.provenance {
            // Stored marks describe the record's draft text; re-align if the
            // session's draft differs (e.g. after adoption).
            session.provenance = e.draft == session.draft ? prov : prov.remapped(from: e.draft, to: session.draft, insertedByAI: false)
        }
        session.onChanged = { [weak self, weak session] in
            guard let self, let session else { return }
            self.scheduleDraftSave(session)
        }
        if let e = existing {
            let versions = e.history.isEmpty ? "" : " · \(e.history.count) earlier version\(e.history.count == 1 ? "" : "s") in History"
            if adopted {
                session.note("The document text changed outside the app; picked up the new text.\(versions)")
            } else if session.isInSync {
                session.note((e.applied ? "In sync — this rewrite was applied \(Self.relative(e.appliedAt ?? e.updatedAt))." : "In sync with the document.") + versions)
            } else {
                session.note("Draft from \(Self.relative(e.updatedAt)) differs from the document — not applied yet.\(versions)")
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
            e.history = session.history
            e.partial = session.isPartial
            e.provenance = session.provenance.isEmpty ? nil : session.provenance
            store.upsert(e)
        } else {
            guard force || !session.isInSync else { return false }
            let e = ParagraphEdit(file: session.file, original: session.original, draft: session.draft,
                                  applied: session.applied, lineHint: session.range.startLine,
                                  maxWords: session.maxWords, instructions: session.instructions,
                                  createdAt: now, updatedAt: now, history: session.history, partial: session.isPartial,
                                  provenance: session.provenance.isEmpty ? nil : session.provenance)
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

    /// Write the rewrite into the .tex file. Returns true on success. The
    /// replaced text is kept in the paragraph's history; the PDF is regenerated
    /// and scrolled to the paragraph.
    @discardableResult
    func applyParagraph(_ session: ParagraphSession, note historyNote: String? = nil) -> Bool {
        guard let root = projectURL else { return false }
        draftSaveWork?.cancel()
        if editorPath == session.file { flushEditor() }
        let url = root.appendingPathComponent(session.file)
        guard let source = try? String(contentsOf: url, encoding: .utf8) else {
            session.note("Could not read \(session.file).", error: true)
            return false
        }
        guard let range = ParagraphLocator.locate(expected: session.container, near: session.range, in: source) else {
            session.note("The paragraph no longer exists in \(session.file) as it was when opened. Close this window and click it again.", error: true)
            return false
        }
        let newText = session.draft.replacingOccurrences(of: "\r\n", with: "\n").trimmingCharacters(in: .newlines)

        // For a sentence/selection, find the span inside the (re-read) paragraph.
        let container = range.text as NSString
        var spanRange = NSRange(location: 0, length: container.length)
        let replacedText: String
        if session.isPartial {
            var occurrences: [NSRange] = []
            var search = NSRange(location: 0, length: container.length)
            while search.location < container.length {
                let r = container.range(of: session.original, options: [], range: search)
                if r.location == NSNotFound { break }
                occurrences.append(r)
                search = NSRange(location: NSMaxRange(r), length: container.length - NSMaxRange(r))
            }
            guard let found = occurrences.min(by: { abs($0.location - session.spanOffset) < abs($1.location - session.spanOffset) }) else {
                session.note("The \(session.scope.label.lowercased()) no longer exists in that paragraph as it was when opened. Close this window and click it again.", error: true)
                return false
            }
            spanRange = found
            replacedText = session.original
        } else {
            replacedText = range.text
        }
        guard EditsStore.normalize(newText) != EditsStore.normalize(replacedText) else {
            session.note("The rewrite is identical to the document; nothing to apply.")
            return false
        }
        let structural = LaTeXStructure.differences(original: replacedText, edited: newText)
        if !structural.isEmpty && !confirmStructuralChange(structural, file: session.file) {
            session.note("Not applied — the rewrite \(structural.joined(separator: "; ")). Fix the right side, or apply anyway from the alert.", error: true)
            return false
        }
        let newContainer = session.isPartial ? container.replacingCharacters(in: spanRange, with: newText) : newText
        let updated = ParagraphLocator.replacing(range, in: source, with: newContainer)
        do {
            try updated.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            session.note("Could not write \(session.file): \(error.localizedDescription)", error: true)
            return false
        }
        let now = Date()
        session.history.append(ParagraphVersion(text: replacedText, replacedAt: now, note: historyNote))
        let lineCount = newContainer.components(separatedBy: "\n").count
        session.range = ParagraphRange(startLine: range.startLine, endLine: range.startLine + lineCount - 1, text: newContainer)
        session.container = newContainer
        session.spanOffset = spanRange.location
        session.original = newText
        session.draft = newText
        session.applied = true
        session.droppedSuggestions = []
        session.showComparison = false
        persistDraft(session, force: true)
        if let id = session.editID, let store = editsStore, var e = store.find(id: id) {
            e.appliedAt = now
            store.upsert(e)
            try? store.save()
        }
        if editorPath == session.file {
            editorText = updated
            editorDirty = false
        }
        pdfStale = true
        scheduleGitRefresh()
        pendingFocus = (session.file, session.range)
        session.note("Applied \(session.scope.label.lowercased()) to \(session.file) lines \(session.range.startLine)–\(session.range.endLine)." + (config.autoRegenerateAfterApply ? " Regenerating PDF…" : ""))
        setStatus("Applied paragraph edit to \(session.file)" + (config.autoRegenerateAfterApply ? " — regenerating PDF…" : ""), .ok)
        if config.autoRegenerateAfterApply {
            regenerate()
        } else {
            notice("Applied to \(session.file). Press Regenerate (⌘R) to refresh the PDF.")
        }
        return true
    }

    /// Replaceable so tests never open a modal alert.
    var structuralChangeConfirmer: (([String], String) -> Bool)?

    /// Warn before writing a rewrite that adds or removes LaTeX structure —
    /// the usual way a paragraph edit breaks the whole compile.
    private func confirmStructuralChange(_ differences: [String], file: String) -> Bool {
        if let custom = structuralChangeConfirmer { return custom(differences, file) }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "This rewrite changes LaTeX structure"
        alert.informativeText = "Compared with the paragraph in \(file), the rewrite " + differences.joined(separator: "; ")
            + ".\n\nThat will most likely break the compile (for example a second \\end{abstract}). Apply it anyway?"
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Apply Anyway")
        return alert.runModal() == .alertSecondButtonReturn
    }

    /// Put an earlier version back into the document (recorded as a new
    /// history entry, so the revert itself can be undone).
    @discardableResult
    func revertParagraph(_ session: ParagraphSession, to version: ParagraphVersion) -> Bool {
        session.draft = version.text
        let stamp = version.replacedAt.formatted(date: .abbreviated, time: .shortened)
        return applyParagraph(session, note: "reverted to the version from before \(stamp)")
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

    /// The configured model, or whatever LM Studio currently has loaded.
    private func resolveLMModel(_ service: LMStudioService, session: ParagraphSession) async -> String? {
        var model = config.model
        if model.isEmpty {
            session.note("Asking LM Studio which model is loaded…")
            if let models = try? await service.listModels() {
                lmModels = models
                model = LMStudioService.pickModel(from: models) ?? ""
            }
        }
        if model.isEmpty {
            session.note(LMStudioError.noModel.localizedDescription, error: true)
            openSettings()
            return nil
        }
        return model
    }

    /// Compare the two sides: instant deterministic checks, then the model's
    /// semantic verdict on whether the edit still says the same thing.
    func compareParagraph(_ session: ParagraphSession) {
        guard !session.isComparing else { return }
        session.integrityIssues = SemanticChecks.integrity(original: session.original, edited: session.draft)
        session.comparedOriginal = session.original
        session.comparedDraft = session.draft
        session.showComparison = true
        if session.isInSync {
            session.comparison = .identical
            return
        }
        session.comparison = nil
        session.isComparing = true
        let cfg = config
        let service = LMStudioService(baseURL: cfg.lmStudioURL)
        let request = CompareRequest(original: session.original, edited: session.draft, model: "", temperature: min(cfg.temperature, 0.2))
        Task { [weak self, weak session] in
            guard let self, let session else { return }
            guard let model = await self.resolveLMModel(service, session: session) else {
                session.isComparing = false
                return
            }
            session.note("Comparing the two sides with \(model)…")
            var req = request
            req.model = model
            do {
                let result = try await service.compare(req)
                session.comparison = result
                session.note("Comparison done: \(result.headline.lowercased()) · \(result.model.isEmpty ? model : result.model)")
            } catch {
                session.note(error.localizedDescription, error: true)
            }
            session.isComparing = false
        }
    }

    func aiFix(_ session: ParagraphSession) {
        guard !session.isBusy else { return }
        session.isBusy = true
        let cfg = config
        let service = LMStudioService(baseURL: cfg.lmStudioURL)
        Task { [weak self, weak session] in
            guard let self, let session else { return }
            guard let model = await self.resolveLMModel(service, session: session) else {
                session.isBusy = false
                return
            }
            session.note("Rewriting with \(model) via LM Studio (max \(session.maxWords) words)…")
            let request = CleanupRequest(paragraph: session.draft, maxWords: session.maxWords,
                                         instructions: session.instructions, model: model,
                                         temperature: cfg.temperature)
            do {
                let result = try await service.cleanUp(request)
                // Enforce the budget: keep syntax fixes, keep rewordings only up to the limit.
                let budget = EditBudget.constrain(original: request.paragraph, rewrite: result.text, maxWords: session.maxWords)
                session.nextDraftChangeIsAI = true
                session.draft = budget.text
                session.droppedSuggestions = budget.dropped
                let shown = result.model.isEmpty ? model : result.model
                var parts: [String] = []
                parts.append("\(budget.freeFixes) syntax/punctuation fix\(budget.freeFixes == 1 ? "" : "es")")
                parts.append("\(budget.wordsUsed) of \(session.maxWords) allowed word change\(session.maxWords == 1 ? "" : "s") used")
                if !budget.dropped.isEmpty {
                    let words = budget.dropped.reduce(0) { $0 + $1.cost }
                    parts.append("\(budget.dropped.count) rewording\(budget.dropped.count == 1 ? "" : "s") (\(words) words) held back — see below")
                }
                if budget.applied.isEmpty && budget.dropped.isEmpty { parts = ["nothing to fix"] }
                let summary = parts.joined(separator: " · ")
                session.lastFixSummary = summary
                session.note("AI fix: \(summary) · \(shown)")
            } catch {
                session.note(error.localizedDescription, error: true)
            }
            session.isBusy = false
        }
    }

    /// Apply one held-back rewording the author explicitly wants.
    func applySuggestion(_ suggestion: EditSuggestion, to session: ParagraphSession) {
        guard let updated = EditBudget.apply(suggestion, to: session.draft) else {
            session.note("Couldn't find “\(suggestion.from.trimmingCharacters(in: .whitespacesAndNewlines))” in the rewrite any more.", error: true)
            session.droppedSuggestions.removeAll { $0.id == suggestion.id }
            return
        }
        session.nextDraftChangeIsAI = true
        session.draft = updated
        session.droppedSuggestions.removeAll { $0.id == suggestion.id }
        session.note("Applied “\(suggestion.label)”")
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

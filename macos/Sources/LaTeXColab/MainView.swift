import SwiftUI
import LaTeXColabCore

struct MainView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            HSplitView {
                FileTreeView()
                    .frame(minWidth: 190, idealWidth: 250, maxWidth: 520)
                RightPane()
                    .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            StatusBar()
        }
        .toolbar { toolbar }
        .navigationTitle(model.projectURL?.lastPathComponent ?? "LaTeX Colab")
        .navigationSubtitle(model.viewMode == .editor ? (model.editorPath ?? "") : (model.previewDocument != nil ? "\(model.mainStem).pdf" : ""))
        .onChange(of: model.paragraphWindowRequest) { _, _ in
            openWindow(id: ParagraphEditorWindow.id)
        }
        .sheet(isPresented: $model.showCommitSheet) {
            GitCommitSheet().environmentObject(model)
        }
        .sheet(item: $model.gitPrompt) { prompt in
            GitPromptSheet(prompt: prompt).environmentObject(model)
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            Button { model.chooseProjectFolder() } label: {
                Label("Open Folder", systemImage: "folder")
            }
            .help("Open a project folder (⌘O)")
        }
        ToolbarItem(placement: .principal) {
            Picker("View", selection: $model.viewMode) {
                ForEach(ViewMode.allCases) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 180)
            .help("Switch between the PDF preview and the LaTeX editor (⌘E)")
        }
        ToolbarItemGroup(placement: .primaryAction) {
            Button { model.regenerate() } label: {
                if model.isCompiling {
                    ProgressView().controlSize(.small)
                } else {
                    Label("Regenerate", systemImage: model.pdfStale ? "arrow.clockwise.circle.fill" : "arrow.clockwise")
                }
            }
            .disabled(model.projectURL == nil || model.isCompiling)
            .help(model.pdfStale ? "Sources changed — regenerate the PDF (⌘R)" : "Compile the main file to PDF (⌘R)")
            Button { model.exportZip() } label: {
                Label("Zip", systemImage: "archivebox")
            }
            .disabled(model.projectURL == nil)
            .help("Export the project as a zip archive (⇧⌘E)")
            Menu {
                GitMenuItems(withShortcuts: false).environmentObject(model)
            } label: {
                Label("Git", systemImage: "arrow.triangle.branch")
            }
            .disabled(model.projectURL == nil)
            .help("Pull, commit, push")
            Toggle(isOn: $model.showLog) {
                Label("Log", systemImage: "terminal")
            }
            .help("Show the build log (⌘L)")
        }
    }
}

struct RightPane: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VSplitView {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if model.showLog {
                LogPanelView()
                    .frame(minHeight: 90, idealHeight: 200)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch model.viewMode {
        case .pdf: PDFPane()
        case .editor: EditorPane()
        }
    }
}

struct PDFPane: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ZStack(alignment: .top) {
            if let doc = model.previewDocument {
                PDFPreviewView(document: doc, version: model.previewVersion) { page, point, bounds, text in
                    model.handlePDFClick(pageIndex: page, pagePoint: point, pageBounds: bounds, nearbyText: text)
                }
                if let n = model.pdfNotice {
                    banner(n, color: model.pdfNoticeIsError ? .red : .green)
                } else if model.pdfStale {
                    banner("Sources changed since this PDF was built — Regenerate (⌘R) to refresh.", color: .orange)
                } else if model.previewIsMain {
                    banner("Click any paragraph to open it in the paragraph editor.", color: .secondary)
                        .opacity(0.9)
                }
            } else if model.projectURL == nil {
                ContentUnavailableView {
                    Label("LaTeX Colab", systemImage: "doc.richtext")
                } description: {
                    Text("Open a project folder to preview and edit its PDF.")
                } actions: {
                    Button("Open Folder…") { model.chooseProjectFolder() }
                        .buttonStyle(.borderedProminent)
                }
            } else {
                ContentUnavailableView {
                    Label("No PDF yet", systemImage: "doc.badge.gearshape")
                } description: {
                    Text("Compile \(model.mainFile) to see the preview here.")
                } actions: {
                    Button(model.isCompiling ? "Compiling…" : "Regenerate") { model.regenerate() }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isCompiling)
                }
            }
        }
    }

    private func banner(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(.regularMaterial, in: Capsule())
            .overlay(Capsule().strokeBorder(color.opacity(0.5)))
            .padding(.top, 8)
            .allowsHitTesting(false)
    }
}

struct EditorPane: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "doc.text").foregroundStyle(.secondary)
                Text(model.editorPath ?? "No file open")
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(1)
                if model.editorDirty {
                    Text("• unsaved").font(.caption).foregroundStyle(.orange)
                }
                Spacer()
                if let p = model.editorPath, p != model.mainFile, p.hasSuffix(".tex") {
                    Button("Set as main") { model.setMainFile(p) }
                        .buttonStyle(.borderless).font(.caption)
                }
                Button("Save") { model.saveEditor() }
                    .buttonStyle(.borderless).font(.caption)
                    .disabled(!model.editorDirty)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            Divider()
            if model.editorPath == nil {
                ContentUnavailableView("No file open", systemImage: "doc.text",
                                       description: Text("Pick a file in the tree on the left."))
            } else if model.editorIsBinary {
                ContentUnavailableView("Binary file", systemImage: "doc.zipper",
                                       description: Text("This file can't be edited as text."))
            } else {
                CodeTextView(text: $model.editorText, isEditable: true, showLineNumbers: true,
                             onChange: { model.editorTextChanged($0) })
                    .id(model.editorPath)
            }
        }
    }
}

struct StatusBar: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        HStack(spacing: 14) {
            if model.statusKind == .busy {
                ProgressView().controlSize(.mini)
            } else {
                Circle().fill(dotColor).frame(width: 7, height: 7)
            }
            Text(model.status)
                .lineLimit(1)
                .truncationMode(.middle)
                .foregroundStyle(model.statusKind == .error ? Color.red : Color.primary)
            Spacer()
            GitStatusItem()
            if model.projectURL != nil {
                Label(model.mainFile, systemImage: "doc.text").help("Main file (right-click a .tex file to change)")
                Label(model.engineName ?? "no engine", systemImage: "gearshape")
                    .foregroundStyle(model.engineName == nil ? .red : .secondary)
                    .help("LaTeX engine")
                Button {
                    model.paragraphWindowRequest += 1
                } label: {
                    Label("\(model.pendingDraftCount) draft\(model.pendingDraftCount == 1 ? "" : "s")", systemImage: "pencil.line")
                }
                .buttonStyle(.borderless)
                .help("Rewrites saved in \(model.editsFileName) that are not applied yet")
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .padding(.vertical, 5)
        .background(.bar)
    }

    private var dotColor: Color {
        switch model.statusKind {
        case .ok: return .green
        case .error: return .red
        case .busy: return .orange
        case .neutral: return .gray
        }
    }
}

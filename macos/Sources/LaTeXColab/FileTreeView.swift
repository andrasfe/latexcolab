import SwiftUI
import LaTeXColabCore

struct FileTreeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if model.projectURL == nil {
                ContentUnavailableView {
                    Label("No project", systemImage: "folder.badge.questionmark")
                } description: {
                    Text("Open a folder that contains your .tex files.")
                } actions: {
                    Button("Open Folder…") { model.chooseProjectFolder() }
                }
            } else {
                List(model.tree, children: \.children, selection: $model.selectedPath) { node in
                    row(node)
                        .tag(node.id)
                        .contextMenu { contextMenu(node) }
                }
                .listStyle(.sidebar)
                .onChange(of: model.selectedPath) { _, newValue in
                    if let p = newValue { model.selectFromTree(p) }
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "folder.fill").foregroundStyle(.secondary)
            Text(model.projectURL?.lastPathComponent ?? "Files")
                .font(.headline)
                .lineLimit(1)
                .help(model.projectURL?.path ?? "")
            Spacer()
            Button { model.chooseProjectFolder() } label: { Image(systemName: "folder") }
                .buttonStyle(.borderless)
                .help("Open folder… (⌘O)")
            Button { model.reloadTree() } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.borderless)
                .help("Refresh file tree (⇧⌘R)")
                .disabled(model.projectURL == nil)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private func row(_ node: FileNode) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon(for: node))
                .foregroundStyle(node.isDirectory ? Color.accentColor : Color.secondary)
            Text(node.name)
                .lineLimit(1)
                .truncationMode(.middle)
            if node.id == model.mainFile {
                Text("main")
                    .font(.caption2)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(Color.accentColor.opacity(0.18), in: Capsule())
            }
            if node.id == model.editorPath && model.editorDirty {
                Circle().fill(.orange).frame(width: 6, height: 6)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private func contextMenu(_ node: FileNode) -> some View {
        if !node.isDirectory {
            Button("Open in Editor") { model.openFile(node.id) }
            if node.fileExtension == "tex", node.id != model.mainFile {
                Button("Set as Main File") { model.setMainFile(node.id) }
            }
        }
        Button("Reveal in Finder") { model.revealInFinder(node.id) }
    }

    private func icon(for node: FileNode) -> String {
        if node.isDirectory { return "folder.fill" }
        switch node.fileExtension {
        case "tex": return "doc.text"
        case "pdf": return "doc.richtext"
        case "bib": return "books.vertical"
        case "png", "jpg", "jpeg", "gif", "eps", "svg": return "photo"
        case "json": return "curlybraces"
        case "cls", "sty": return "gearshape"
        case "md", "txt": return "doc.plaintext"
        default: return "doc"
        }
    }
}

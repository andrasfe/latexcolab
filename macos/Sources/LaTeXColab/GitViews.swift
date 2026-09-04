import SwiftUI
import LaTeXColabCore

/// Git actions, shared by the menu bar (with shortcuts) and the toolbar menu.
struct GitMenuItems: View {
    @EnvironmentObject var model: AppModel
    var withShortcuts = false

    var body: some View {
        if model.projectURL == nil {
            Text("Open a project folder first")
        } else if !model.git.isRepo {
            Button("Initialize Git Repository") { model.gitInit() }
        } else {
            Group {
                shortcut(Button("Pull") { model.gitPull() }, "l")
                shortcut(Button("Commit…") { model.openCommitSheet() }, "c")
                shortcut(Button("Push") { model.gitPush() }, "u")
                Divider()
                Button("Fetch") { model.gitFetch() }
                Menu("Switch Branch") {
                    ForEach(model.gitBranches, id: \.self) { b in
                        Button(b) { model.gitCheckout(branch: b, create: false) }
                            .disabled(b == model.git.branch)
                    }
                }
                .disabled(model.gitBranches.count < 2)
                Button("New Branch…") { model.gitPrompt = .newBranch }
                Button(model.git.hasRemote ? "Change Remote URL…" : "Set Remote URL…") { model.gitPrompt = .remote }
                Divider()
                Button("Show Recent Commits") { model.gitLog() }
            }
            .disabled(model.gitBusy)
        }
    }

    @ViewBuilder
    private func shortcut(_ button: Button<Text>, _ key: Character) -> some View {
        if withShortcuts {
            button.keyboardShortcut(KeyEquivalent(key), modifiers: [.command, .shift])
        } else {
            button
        }
    }
}

/// Stage-and-commit sheet: pick files, write a message, commit or commit-and-push.
struct GitCommitSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var message = ""
    @State private var selected: Set<String> = []
    @State private var seen: Set<String> = []

    private var changes: [GitChange] { model.git.changes }
    private var canCommit: Bool {
        !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !selected.isEmpty && !model.gitBusy
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Commit to \(model.git.branch ?? "detached HEAD")", systemImage: "arrow.triangle.branch")
                    .font(.title3.bold())
                Spacer()
                if let up = model.git.upstream {
                    Text("↑\(model.git.ahead) ↓\(model.git.behind) vs \(up)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Button { model.refreshGitStatus() } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless)
                    .help("Refresh status")
            }

            if changes.isEmpty {
                ContentUnavailableView("Nothing to commit", systemImage: "checkmark.circle",
                                       description: Text("The working tree is clean."))
                    .frame(height: 150)
            } else {
                HStack {
                    Toggle("All files", isOn: Binding(
                        get: { selected.count == changes.count },
                        set: { on in selected = on ? Set(changes.map(\.id)) : [] }))
                        .toggleStyle(.checkbox)
                    Spacer()
                    Text("\(selected.count) of \(changes.count) selected")
                        .font(.caption).foregroundStyle(.secondary)
                }
                List(changes) { change in
                    Toggle(isOn: Binding(
                        get: { selected.contains(change.id) },
                        set: { on in if on { selected.insert(change.id) } else { selected.remove(change.id) } })) {
                        HStack {
                            Text(change.path).font(.system(.body, design: .monospaced)).lineLimit(1).truncationMode(.middle)
                            if let o = change.originalPath {
                                Text("← \(o)").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(change.summary)
                                .font(.caption)
                                .foregroundStyle(change.isConflict ? Color.red : Color.secondary)
                        }
                    }
                    .toggleStyle(.checkbox)
                }
                .frame(minHeight: 150, maxHeight: 280)
                .border(Color.secondary.opacity(0.25))
            }

            Text("Commit message").font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $message)
                .font(.body)
                .frame(minHeight: 64, maxHeight: 110)
                .border(Color.secondary.opacity(0.25))

            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                if model.gitBusy { ProgressView().controlSize(.small) }
                Button("Commit") { commit(push: false) }
                    .disabled(!canCommit)
                Button("Commit & Push") { commit(push: true) }
                    .keyboardShortcut(.return, modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(!canCommit)
                    .help("Commit, then push to the remote (⌘↩)")
            }
        }
        .padding(16)
        .frame(width: 640)
        .onAppear { syncSelection(with: changes) }
        .onChange(of: changes) { _, new in syncSelection(with: new) }
    }

    /// Keep the user's choices, select files that just appeared, drop ones that vanished.
    private func syncSelection(with new: [GitChange]) {
        let ids = Set(new.map(\.id))
        let fresh = ids.subtracting(seen)
        selected = selected.intersection(ids).union(fresh)
        seen.formUnion(ids)
    }

    private func commit(push: Bool) {
        let paths = selected.count == changes.count ? nil : Array(selected).sorted()
        model.gitCommit(message: message, paths: paths, thenPush: push)
        dismiss()
    }
}

/// Small text prompt used for "Set Remote URL…" and "New Branch…".
struct GitPromptSheet: View {
    let prompt: AppModel.GitPrompt
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var value = ""

    private var title: String {
        switch prompt {
        case .remote: return model.git.hasRemote ? "Change remote URL" : "Set remote URL"
        case .newBranch: return "New branch"
        }
    }
    private var detail: String {
        switch prompt {
        case .remote: return "The URL of the \"origin\" remote, e.g. git@github.com:you/paper.git. SSH keys or a credential helper must already be set up; the app never prompts for passwords."
        case .newBranch: return "Creates the branch from the current commit and switches to it."
        }
    }
    private var placeholder: String {
        switch prompt {
        case .remote: return "git@github.com:you/your-paper.git"
        case .newBranch: return "my-changes"
        }
    }
    private var buttonTitle: String {
        switch prompt {
        case .remote: return "Save"
        case .newBranch: return "Create"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.title3.bold())
            Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField(placeholder, text: $value)
                .textFieldStyle(.roundedBorder)
                .onSubmit(submit)
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(buttonTitle, action: submit)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(value.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(16)
        .frame(width: 500)
        .onAppear { if prompt == .remote { value = model.git.remoteURL ?? "" } }
    }

    private func submit() {
        let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !v.isEmpty else { return }
        switch prompt {
        case .remote: model.gitSetRemote(url: v)
        case .newBranch: model.gitCheckout(branch: v, create: true)
        }
        dismiss()
    }
}

/// Status-bar summary: branch, ahead/behind, dirty count. Click to commit.
struct GitStatusItem: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        if model.git.isRepo {
            Button { model.openCommitSheet() } label: {
                HStack(spacing: 4) {
                    if model.gitBusy {
                        ProgressView().controlSize(.mini)
                    } else {
                        Image(systemName: "arrow.triangle.branch")
                    }
                    Text(model.git.branch ?? "detached")
                    if model.git.ahead > 0 { Text("↑\(model.git.ahead)") }
                    if model.git.behind > 0 { Text("↓\(model.git.behind)") }
                    if model.git.isDirty {
                        Text("· \(model.git.changes.count) changed").foregroundStyle(.orange)
                    }
                }
            }
            .buttonStyle(.borderless)
            .help(gitHelp)
        } else if model.projectURL != nil {
            Button { model.gitInit() } label: {
                Label("not a git repo", systemImage: "arrow.triangle.branch")
            }
            .buttonStyle(.borderless)
            .help("Click to initialize a git repository here")
        }
    }

    private var gitHelp: String {
        var parts = ["Branch \(model.git.branch ?? "(detached)")"]
        if let up = model.git.upstream { parts.append("tracking \(up)") }
        if let r = model.git.remoteURL { parts.append("origin \(r)") } else { parts.append("no remote") }
        parts.append("click to commit (⇧⌘C)")
        return parts.joined(separator: " · ")
    }
}

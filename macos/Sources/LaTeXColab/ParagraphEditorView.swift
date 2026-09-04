import AppKit
import SwiftUI
import LaTeXColabCore

struct ParagraphEditorWindow: View {
    static let id = "paragraph-editor"
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            if let session = model.paragraphSession {
                ParagraphEditorView(session: session)
                    .environmentObject(model)
                    .id(session.id)
            } else {
                ContentUnavailableView("No paragraph selected", systemImage: "text.cursor",
                                       description: Text("Click a paragraph in the PDF preview to edit it here."))
            }
        }
        .frame(minWidth: 820, minHeight: 480)
    }
}

struct ParagraphEditorView: View {
    @ObservedObject var session: ParagraphSession
    @EnvironmentObject var model: AppModel

    private static let deletedColor = NSColor.systemRed.withAlphaComponent(0.22)
    private static let insertedColor = NSColor.systemGreen.withAlphaComponent(0.26)

    var body: some View {
        let segments = WordDiff.diff(old: session.original, new: session.draft)
        let ranges = WordDiff.highlightRanges(segments)
        let changed = WordDiff.changedWordCount(segments)

        VStack(spacing: 0) {
            header
            Divider()
            HSplitView {
                pane(title: "In document",
                     subtitle: "\(session.file) · lines \(session.range.startLine)–\(session.range.endLine)",
                     text: .constant(session.original), editable: false,
                     highlights: ranges.old.map { TextHighlight(range: $0, color: Self.deletedColor) })
                    .frame(minWidth: 280)
                pane(title: "Rewrite",
                     subtitle: changed == 0 ? "identical to the document" : "\(changed) word\(changed == 1 ? "" : "s") changed" + (changed > session.maxWords ? " · over the \(session.maxWords)-word limit" : ""),
                     text: $session.draft, editable: !session.isBusy,
                     highlights: ranges.new.map { TextHighlight(range: $0, color: Self.insertedColor) })
                    .frame(minWidth: 280)
            }
            Divider()
            controls(changed: changed)
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "text.quote").foregroundStyle(.secondary)
            Text(session.file).font(.headline)
            Text("lines \(session.range.startLine)–\(session.range.endLine)")
                .foregroundStyle(.secondary)
            Spacer()
            if session.isInSync {
                Label(session.applied ? "Applied · in sync" : "In sync with document", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Label("Draft differs · not applied", systemImage: "pencil.circle.fill")
                    .foregroundStyle(.orange)
            }
            if let saved = session.savedAt {
                Text("saved \(saved.formatted(date: .omitted, time: .shortened))")
                    .font(.caption).foregroundStyle(.secondary)
                    .help("Drafts are stored in \(model.editsFileName) inside the project")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func pane(title: String, subtitle: String, text: Binding<String>, editable: Bool,
                      highlights: [TextHighlight]) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text(title).font(.caption.bold())
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(.bar)
            Divider()
            CodeTextView(text: text, isEditable: editable, showLineNumbers: false, fontSize: 13, highlights: highlights)
        }
    }

    private func controls(changed: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("Max words the AI may change")
                Slider(value: Binding(get: { Double(session.maxWords) },
                                      set: { session.maxWords = Int($0.rounded()) }),
                       in: 0...100, step: 1)
                Text("\(session.maxWords)")
                    .monospacedDigit()
                    .frame(width: 30, alignment: .trailing)
            }
            HStack(alignment: .top, spacing: 10) {
                Text("Additional instructions")
                    .padding(.top, 4)
                TextField("e.g. use simpler terms, remove em dashes, make it sound less AI-like",
                          text: $session.instructions, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .disabled(session.isBusy)
            }
            HStack(spacing: 10) {
                Button { model.aiFix(session) } label: {
                    Label(session.isBusy ? "Fixing…" : "AI Fix", systemImage: "sparkles")
                }
                .disabled(session.isBusy)
                .help("Ask the LM Studio model to clean up the right side: punctuation, LaTeX syntax, and at most the chosen number of word changes")
                if session.isBusy { ProgressView().controlSize(.small) }
                Text(session.message)
                    .font(.caption)
                    .foregroundStyle(session.messageIsError ? Color.red : Color.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)
                Spacer()
                Button("Discard Draft") { model.discardDraft(session) }
                    .disabled(session.isBusy || (session.isInSync && session.editID == nil))
                    .help("Delete the saved draft and reset the right side to the document text")
                Button("Revert") { session.draft = session.original }
                    .disabled(session.isBusy || session.isInSync)
                    .help("Reset the right side to the document text (keeps the saved record)")
                Button("Save Draft") { model.saveDraftNow(session) }
                    .keyboardShortcut("s", modifiers: [.command, .shift])
                    .disabled(session.isBusy)
                    .help("Save the rewrite to \(model.editsFileName) without touching the document (⇧⌘S)")
                Button("Apply") { model.applyParagraph(session) }
                    .keyboardShortcut(.return, modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(session.isBusy || session.isInSync)
                    .help("Write the rewrite into \(session.file) (⌘↩)")
            }
        }
        .padding(12)
    }
}

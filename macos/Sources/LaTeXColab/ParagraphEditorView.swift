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
    @Environment(\.dismissWindow) private var dismissWindow

    /// Apply (or revert) and, if configured, close this window so the
    /// refreshed PDF in the main window is what the author sees next.
    private func applyAndClose(_ action: () -> Bool) {
        if action() && model.config.closeWindowAfterApply {
            // A sheet may still be animating out; closing the window at the same
            // instant is ignored by AppKit, so give it a moment.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                dismissWindow(id: ParagraphEditorWindow.id)
            }
        }
    }

    static let deletedColor = NSColor.systemRed.withAlphaComponent(0.22)
    static let insertedColor = NSColor.systemGreen.withAlphaComponent(0.26)
    static let aiColor = NSColor.systemPurple.withAlphaComponent(0.32)

    var body: some View {
        let marks = session.provenance.highlights(original: session.original, draft: session.draft)
        let measure = EditBudget.measure(original: session.original, new: session.draft)
        let changed = measure.wordChanges

        VStack(spacing: 0) {
            header
            Divider()
            if session.isPartial {
                let ctx = session.context()
                HStack(spacing: 4) {
                    Text("Editing a \(session.scope.label.lowercased()) inside the paragraph:").font(.caption.bold()).foregroundStyle(.secondary)
                    Text(ctx.before).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.head)
                    Text("⟨\(session.scope.label.lowercased())⟩").font(.caption.bold()).foregroundStyle(Color.accentColor)
                    Text(ctx.after).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail)
                    Spacer()
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 4)
                .background(.bar)
                Divider()
            }
            HSplitView {
                pane(title: session.isPartial ? "In document (\(session.scope.label.lowercased()))" : "In document",
                     subtitle: "\(session.file) · lines \(session.range.startLine)–\(session.range.endLine)",
                     text: .constant(session.original), editable: false,
                     highlights: marks.userInOriginal.map { TextHighlight(range: $0, color: Self.deletedColor) }
                        + marks.aiInOriginal.map { TextHighlight(range: $0, color: Self.aiColor) })
                    .frame(minWidth: 280)
                pane(title: "Rewrite",
                     subtitle: paneSubtitle(measure, marks),
                     text: $session.draft, editable: !session.isBusy,
                     highlights: marks.userInDraft.map { TextHighlight(range: $0, color: Self.insertedColor) }
                        + marks.aiInDraft.map { TextHighlight(range: $0, color: Self.aiColor) })
                    .frame(minWidth: 280)
            }
            legend(marks)
            if session.showComparison {
                Divider()
                ComparisonPanel(session: session)
                    .environmentObject(model)
            }
            Divider()
            controls(changed: changed)
        }
    }

    private func paneSubtitle(_ m: EditMeasure, _ marks: Provenance.Highlights) -> String {
        if m.wordChanges == 0 && m.freeFixes == 0 && marks.aiWords == 0 && marks.aiPunctuation == 0 { return "identical to the document" }
        var parts: [String] = []
        if m.freeFixes > 0 { parts.append("\(m.freeFixes) syntax fix\(m.freeFixes == 1 ? "" : "es")") }
        if m.wordChanges > 0 { parts.append("\(m.wordChanges) word\(m.wordChanges == 1 ? "" : "s") reworded in \(m.rewordings) place\(m.rewordings == 1 ? "" : "s")") }
        if m.wordChanges > session.maxWords { parts.append("over the \(session.maxWords)-word limit") }
        return parts.joined(separator: " · ")
    }

    /// Colour key under the two panes, with AI / your-edit counts.
    private func legend(_ marks: Provenance.Highlights) -> some View {
        HStack(spacing: 14) {
            swatch(Self.aiColor, "AI-made: \(marks.aiWords) word\(marks.aiWords == 1 ? "" : "s"), \(marks.aiPunctuation) punctuation mark\(marks.aiPunctuation == 1 ? "" : "s")")
            swatch(Self.insertedColor, "your additions: \(marks.userWords) word\(marks.userWords == 1 ? "" : "s")")
            swatch(Self.deletedColor, "removed or replaced by you")
            Spacer()
            Text("AI marks follow the text as you edit and are kept with the draft and after Apply.")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .font(.caption)
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
        .background(.bar)
    }

    private func swatch(_ color: NSColor, _ label: String) -> some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 2).fill(Color(nsColor: color)).frame(width: 12, height: 12)
            Text(label).foregroundStyle(.secondary)
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: session.isPartial ? "text.cursor" : "text.quote").foregroundStyle(.secondary)
            Text(session.file).font(.headline)
            Text("\(session.scope.label.lowercased()) · lines \(session.range.startLine)–\(session.range.endLine)")
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
            Button {
                session.showHistory = true
            } label: {
                Label(session.history.isEmpty ? "History" : "History (\(session.history.count))", systemImage: "clock.arrow.circlepath")
            }
            .help("Earlier versions of this paragraph that Apply replaced — view or revert")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .sheet(isPresented: $session.showHistory) {
            ParagraphHistorySheet(session: session, onRevert: { version in
                applyAndClose { model.revertParagraph(session, to: version) }
            })
            .environmentObject(model)
        }
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
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 10) {
                    Text("Max words the AI may add, delete or replace")
                    Slider(value: Binding(get: { Double(session.maxWords) },
                                          set: { session.maxWords = Int($0.rounded()) }),
                           in: 0...100, step: 1)
                    Text("\(session.maxWords)")
                        .monospacedDigit()
                        .frame(width: 30, alignment: .trailing)
                }
                Text(session.maxWords == 0
                     ? "0 = proofread only: spelling, punctuation and LaTeX syntax are fixed, every word you wrote stays."
                     : "Spelling, punctuation and LaTeX fixes are always applied and don't count. Rewordings beyond \(session.maxWords) words are held back for you to accept one by one.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
                .help("Proofread the right side with the LM Studio model: spelling, punctuation and LaTeX syntax, plus at most the chosen number of word changes. Anything beyond the limit is reverted to your words.")
                if session.isBusy { ProgressView().controlSize(.small) }
                Button { model.compareParagraph(session) } label: {
                    Label(session.isComparing ? "Comparing…" : "Compare", systemImage: "arrow.left.arrow.right.square")
                }
                .disabled(session.isComparing || session.isBusy)
                .help("Compare the two sides semantically: what changed, and does the edit still say the same thing?")
                if session.isComparing { ProgressView().controlSize(.small) }
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
                Button("Apply") { applyAndClose { model.applyParagraph(session) } }
                    .keyboardShortcut(.return, modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(session.isBusy || session.isInSync)
                    .help("Write the rewrite into \(session.file), regenerate the PDF and close this window (⌘↩)")
            }
            if !session.droppedSuggestions.isEmpty {
                heldBack
            }
        }
        .padding(12)
    }

    /// Rewordings the model proposed but the budget did not allow.
    private var heldBack: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(session.droppedSuggestions) { s in
                    HStack(alignment: .top, spacing: 8) {
                        Button("Apply") { model.applySuggestion(s, to: session) }
                            .controlSize(.small)
                        Text(s.label)
                            .font(.callout)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(s.cost) word\(s.cost == 1 ? "" : "s")")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                    }
                }
                Button("Dismiss all") { session.droppedSuggestions = [] }
                    .buttonStyle(.borderless)
                    .font(.caption)
            }
            .padding(.top, 4)
        } label: {
            let words = session.droppedSuggestions.reduce(0) { $0 + $1.cost }
            Label("\(session.droppedSuggestions.count) rewording\(session.droppedSuggestions.count == 1 ? "" : "s") held back (\(words) words over the limit) — apply any you actually want",
                  systemImage: "hand.raised")
                .font(.callout)
        }
    }
}


/// Result of "Compare": deterministic integrity checks plus the model's verdict.
struct ComparisonPanel: View {
    @ObservedObject var session: ParagraphSession
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if let c = session.comparison {
                        if !c.summary.isEmpty {
                            Text(c.summary)
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                        section("Meaning differences", c.meaningDifferences, color: .red, icon: "exclamationmark.triangle.fill")
                        section("What changed on the edited side", c.changes, color: .secondary, icon: "pencil")
                        section("LaTeX issues the model noticed", c.latexIssues, color: .orange, icon: "chevron.left.forwardslash.chevron.right")
                        if c.verdict == .unknown, !c.raw.isEmpty, c.raw != c.summary {
                            DisclosureGroup("Raw model output") {
                                Text(c.raw).font(.caption).textSelection(.enabled)
                            }
                            .font(.caption)
                        }
                    } else if session.isComparing {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Waiting for the model's verdict…").foregroundStyle(.secondary)
                        }
                    } else if !session.messageIsError {
                        Text("No verdict from the model.").foregroundStyle(.secondary)
                    }
                    checks
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.bottom, 4)
            }
            .frame(maxHeight: 220)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private var header: some View {
        HStack(spacing: 10) {
            if let c = session.comparison {
                Label(c.headline, systemImage: icon(for: c.verdict))
                    .font(.headline)
                    .foregroundStyle(color(for: c.verdict))
            } else {
                Label("Semantic comparison", systemImage: "arrow.left.arrow.right.square")
                    .font(.headline)
            }
            if session.comparisonIsStale {
                Text("text changed since this comparison")
                    .font(.caption)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.orange.opacity(0.2), in: Capsule())
            }
            Spacer()
            if let c = session.comparison, c.model != "none", !c.model.isEmpty {
                Text(c.model).font(.caption).foregroundStyle(.secondary)
            }
            Button("Re-run") { model.compareParagraph(session) }
                .buttonStyle(.borderless)
                .disabled(session.isComparing)
            Button { session.showComparison = false } label: { Image(systemName: "xmark") }
                .buttonStyle(.borderless)
                .help("Hide the comparison")
        }
    }

    @ViewBuilder
    private var checks: some View {
        let issues = session.integrityIssues
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text("Automatic checks").font(.caption.bold()).foregroundStyle(.secondary)
                Text(issues.isEmpty ? "citations, references, labels, math, numbers, negation and hedging all match" : "\(issues.count) finding\(issues.count == 1 ? "" : "s")")
                    .font(.caption).foregroundStyle(issues.isEmpty ? Color.green : Color.secondary)
            }
            ForEach(issues) { issue in
                Label {
                    Text(issue.text).font(.caption).textSelection(.enabled)
                } icon: {
                    Image(systemName: issue.severity == .error ? "xmark.octagon.fill" : issue.severity == .warning ? "exclamationmark.triangle.fill" : "info.circle")
                        .foregroundStyle(issue.severity == .error ? Color.red : issue.severity == .warning ? Color.orange : Color.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func section(_ title: String, _ items: [String], color: Color, icon: String) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.caption.bold()).foregroundStyle(.secondary)
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    Label {
                        Text(item).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    } icon: {
                        Image(systemName: icon).foregroundStyle(color)
                    }
                    .font(.callout)
                }
            }
        }
    }

    private func icon(for v: SemanticComparison.Verdict) -> String {
        switch v {
        case .same: return "checkmark.seal.fill"
        case .minor: return "equal.circle.fill"
        case .changed: return "exclamationmark.triangle.fill"
        case .unknown: return "questionmark.circle"
        }
    }

    private func color(for v: SemanticComparison.Verdict) -> Color {
        switch v {
        case .same: return .green
        case .minor: return .orange
        case .changed: return .red
        case .unknown: return .secondary
        }
    }
}


/// Earlier versions of the paragraph. Select one to see it side by side with
/// the current text; load it into the rewrite pane or put it back into the
/// document.
struct ParagraphHistorySheet: View {
    @ObservedObject var session: ParagraphSession
    var onRevert: (ParagraphVersion) -> Void
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var selectedID: UUID?

    private var versions: [ParagraphVersion] { session.history.reversed() }  // newest first
    private var selected: ParagraphVersion? { versions.first { $0.id == selectedID } }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("History of \(session.file) lines \(session.range.startLine)–\(session.range.endLine)", systemImage: "clock.arrow.circlepath")
                    .font(.title3.bold())
                Spacer()
                Text("\(session.history.count) earlier version\(session.history.count == 1 ? "" : "s")")
                    .foregroundStyle(.secondary)
            }
            if versions.isEmpty {
                ContentUnavailableView("No earlier versions yet", systemImage: "clock",
                                       description: Text("Every Apply keeps the text it replaced here, so it can be reviewed or restored."))
                    .frame(height: 200)
            } else {
                HSplitView {
                    List(versions, selection: $selectedID) { v in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Until \(v.replacedAt.formatted(date: .abbreviated, time: .shortened))")
                                .font(.callout.bold())
                            if let n = v.note { Text(n).font(.caption).foregroundStyle(.secondary) }
                            Text(v.text.replacingOccurrences(of: "\n", with: " "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        .padding(.vertical, 2)
                        .tag(v.id)
                    }
                    .frame(minWidth: 220, idealWidth: 280, maxWidth: 360)
                    VStack(spacing: 0) {
                        if let v = selected {
                            let segments = WordDiff.diff(old: v.text, new: session.original)
                            let ranges = WordDiff.highlightRanges(segments)
                            let measure = EditBudget.measure(original: v.text, new: session.original)
                            HStack {
                                Text("Selected version").font(.caption.bold())
                                Text("vs current: \(measure.wordChanges) word\(measure.wordChanges == 1 ? "" : "s") differ, \(measure.freeFixes) punctuation/LaTeX differences")
                                    .font(.caption).foregroundStyle(.secondary)
                                Spacer()
                            }
                            .padding(.horizontal, 10).padding(.vertical, 5)
                            .background(.bar)
                            Divider()
                            CodeTextView(text: .constant(v.text), isEditable: false,
                                         highlights: ranges.old.map { TextHighlight(range: $0, color: NSColor.systemRed.withAlphaComponent(0.22)) })
                            Divider()
                            HStack {
                                Text("Current (in document)").font(.caption.bold())
                                Spacer()
                            }
                            .padding(.horizontal, 10).padding(.vertical, 5)
                            .background(.bar)
                            Divider()
                            CodeTextView(text: .constant(session.original), isEditable: false,
                                         highlights: ranges.new.map { TextHighlight(range: $0, color: NSColor.systemGreen.withAlphaComponent(0.26)) })
                        } else {
                            ContentUnavailableView("Select a version", systemImage: "arrow.left",
                                                   description: Text("Pick an entry on the left to compare it with the current text."))
                        }
                    }
                    .frame(minWidth: 420)
                }
                .frame(minHeight: 320)
            }
            HStack {
                Button("Close") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Load into Rewrite") {
                    if let v = selected {
                        session.draft = v.text
                        session.note("Loaded the version from before \(v.replacedAt.formatted(date: .abbreviated, time: .shortened)) into the rewrite pane — press Apply to put it back.")
                        dismiss()
                    }
                }
                .disabled(selected == nil)
                .help("Copy this version to the right pane without touching the document")
                Button("Revert Document to This Version") {
                    if let v = selected {
                        onRevert(v)
                        dismiss()
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(selected == nil || model.isCompiling)
                .help("Write this version back into the .tex file (the current text is kept in history)")
            }
        }
        .padding(16)
        .frame(minWidth: 820, minHeight: 460)
        .onAppear { selectedID = versions.first?.id }
    }
}

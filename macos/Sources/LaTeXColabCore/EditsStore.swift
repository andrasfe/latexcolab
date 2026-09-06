import Foundation

/// A paragraph text that was in the document until `replacedAt`.
public struct ParagraphVersion: Codable, Identifiable, Equatable {
    public var id: UUID
    public var text: String
    public var replacedAt: Date
    public var note: String?

    public init(id: UUID = UUID(), text: String, replacedAt: Date = Date(), note: String? = nil) {
        self.id = id
        self.text = text
        self.replacedAt = replacedAt
        self.note = note
    }
}

/// One paragraph rewrite. `original` is the paragraph as it stands in the
/// document (updated on Apply); `draft` is the rewrite. When they are equal
/// the paragraph is "in sync". `history` holds every earlier text that Apply
/// replaced, oldest first, so any version can be restored.
public struct ParagraphEdit: Codable, Identifiable, Equatable {
    public var id: UUID
    public var file: String
    public var original: String
    public var draft: String
    public var applied: Bool
    public var lineHint: Int
    public var maxWords: Int
    public var instructions: String
    public var createdAt: Date
    public var updatedAt: Date
    public var appliedAt: Date?
    public var history: [ParagraphVersion]
    /// True when `original` is a sentence/selection inside a paragraph rather than a whole paragraph.
    public var partial: Bool
    /// Which words/punctuation of `draft` the AI produced.
    public var provenance: Provenance?

    public init(id: UUID = UUID(), file: String, original: String, draft: String, applied: Bool = false,
                lineHint: Int, maxWords: Int, instructions: String = "",
                createdAt: Date = Date(), updatedAt: Date = Date(), appliedAt: Date? = nil,
                history: [ParagraphVersion] = [], partial: Bool = false, provenance: Provenance? = nil) {
        self.id = id
        self.file = file
        self.original = original
        self.draft = draft
        self.applied = applied
        self.lineHint = lineHint
        self.maxWords = maxWords
        self.instructions = instructions
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.appliedAt = appliedAt
        self.history = history
        self.partial = partial
        self.provenance = provenance
    }

    enum CodingKeys: String, CodingKey {
        case id, file, original, draft, applied, lineHint, maxWords, instructions, createdAt, updatedAt, appliedAt, history, partial, provenance
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(UUID.self, forKey: .id)
        file = try c.decode(String.self, forKey: .file)
        original = try c.decode(String.self, forKey: .original)
        draft = try c.decode(String.self, forKey: .draft)
        applied = try c.decodeIfPresent(Bool.self, forKey: .applied) ?? false
        lineHint = try c.decodeIfPresent(Int.self, forKey: .lineHint) ?? 0
        maxWords = try c.decodeIfPresent(Int.self, forKey: .maxWords) ?? 20
        instructions = try c.decodeIfPresent(String.self, forKey: .instructions) ?? ""
        createdAt = try c.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt) ?? createdAt
        appliedAt = try c.decodeIfPresent(Date.self, forKey: .appliedAt)
        history = try c.decodeIfPresent([ParagraphVersion].self, forKey: .history) ?? []
        partial = try c.decodeIfPresent(Bool.self, forKey: .partial) ?? false
        provenance = try c.decodeIfPresent(Provenance.self, forKey: .provenance)
    }

    public var isInSync: Bool { EditsStore.normalize(original) == EditsStore.normalize(draft) }

    /// Record that `draft` replaced `original` in the document.
    public mutating func markApplied(at date: Date = Date(), note: String? = nil) {
        history.append(ParagraphVersion(text: original, replacedAt: date, note: note))
        original = draft
        applied = true
        appliedAt = date
        updatedAt = date
    }
}

public struct EditsDocument: Codable {
    public var version: Int
    public var edits: [ParagraphEdit]
    public init(version: Int = 1, edits: [ParagraphEdit] = []) {
        self.version = version
        self.edits = edits
    }
}

/// JSON-backed store living next to the LaTeX sources
/// (`<project>/latexcolab-edits.json`).
public final class EditsStore {
    public static let fileName = "latexcolab-edits.json"

    public let fileURL: URL
    public private(set) var edits: [ParagraphEdit] = []

    public init(projectURL: URL) {
        fileURL = projectURL.appendingPathComponent(EditsStore.fileName)
    }

    public static func normalize(_ s: String) -> String {
        s.replacingOccurrences(of: "\r\n", with: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func load() throws {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            edits = []
            return
        }
        let data = try Data(contentsOf: fileURL)
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        edits = try dec.decode(EditsDocument.self, from: data).edits
    }

    public func save() throws {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        enc.dateEncodingStrategy = .iso8601
        let data = try enc.encode(EditsDocument(edits: edits))
        try data.write(to: fileURL, options: .atomic)
    }

    /// The record whose `original` matches the paragraph currently in the document.
    public func find(file: String, original: String) -> ParagraphEdit? {
        let key = EditsStore.normalize(original)
        return edits.first { $0.file == file && EditsStore.normalize($0.original) == key }
    }

    public func find(id: UUID) -> ParagraphEdit? {
        edits.first { $0.id == id }
    }

    public struct Match: Equatable {
        public let edit: ParagraphEdit
        /// False when the document paragraph no longer equals the record's
        /// `original` (edited by hand or reverted outside the app).
        public let exact: Bool
    }

    /// Exact match on the current text, then a match on any historical
    /// version, then a nearby record (same file, close line) whose text still
    /// overlaps strongly — so history survives hand edits in the editor.
    public func match(file: String, original: String, nearLine line: Int, partial: Bool = false) -> Match? {
        if let e = find(file: file, original: original), e.partial == partial { return Match(edit: e, exact: true) }
        let key = EditsStore.normalize(original)
        if let e = edits.first(where: { $0.file == file && $0.partial == partial && $0.history.contains { EditsStore.normalize($0.text) == key } }) {
            return Match(edit: e, exact: false)
        }
        let tokens = TextMatcher.tokens(latex: original)
        guard tokens.count >= 3 else { return nil }
        let nearby = edits.filter { $0.file == file && $0.partial == partial && abs($0.lineHint - line) <= 3 }
        var best: (ParagraphEdit, Double)? = nil
        for e in nearby {
            let candidate = TextMatcher.tokens(latex: e.original)
            // A sentence must not adopt a whole-paragraph record (or vice versa).
            let longer = max(tokens.count, candidate.count), shorter = min(tokens.count, candidate.count)
            guard longer > 0, Double(shorter) / Double(longer) >= 0.5 else { continue }
            let score = TextMatcher.score(query: tokens, candidate: candidate)
            if score >= 0.6 && score > (best?.1 ?? 0) { best = (e, score) }
        }
        return best.map { Match(edit: $0.0, exact: false) }
    }

    public func upsert(_ edit: ParagraphEdit) {
        if let i = edits.firstIndex(where: { $0.id == edit.id }) {
            edits[i] = edit
        } else {
            edits.append(edit)
        }
    }

    public func remove(id: UUID) {
        edits.removeAll { $0.id == id }
    }

    /// Drafts that differ from the document and have not been applied yet.
    public var pendingCount: Int {
        edits.filter { !$0.isInSync }.count
    }
}

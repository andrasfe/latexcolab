import Foundation

/// One paragraph rewrite. `original` is the paragraph as it stands in the
/// document (updated on Apply); `draft` is the rewrite. When they are equal
/// the paragraph is "in sync".
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

    public init(id: UUID = UUID(), file: String, original: String, draft: String, applied: Bool = false,
                lineHint: Int, maxWords: Int, instructions: String = "",
                createdAt: Date = Date(), updatedAt: Date = Date()) {
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
    }

    public var isInSync: Bool { EditsStore.normalize(original) == EditsStore.normalize(draft) }
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

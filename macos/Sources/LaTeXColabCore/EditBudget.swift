import Foundation

/// One contiguous change between the author's text and the AI's reply.
public struct EditSuggestion: Identifiable, Equatable {
    public let id: Int
    public let from: String
    public let to: String
    /// Words this change costs against the budget (0 = free fix).
    public let cost: Int
    /// Adds or removes LaTeX structure (environments, sectioning, citations…);
    /// never applied automatically.
    public let isStructural: Bool

    public init(id: Int, from: String, to: String, cost: Int, isStructural: Bool = false) {
        self.id = id
        self.from = from
        self.to = to
        self.cost = cost
        self.isStructural = isStructural
    }

    public var isFree: Bool { cost == 0 && !isStructural }
    public var label: String {
        (isStructural ? "structural change: " : "") + plainLabel
    }
    public var plainLabel: String {
        let f = from.trimmingCharacters(in: .whitespacesAndNewlines)
        let t = to.trimmingCharacters(in: .whitespacesAndNewlines)
        if f.isEmpty && t.isEmpty {
            return from.count > to.count ? "remove extra space" : "insert space"
        }
        if f.isEmpty { return "insert “\(t)”" }
        if t.isEmpty { return "delete “\(f)”" }
        return "“\(f)” → “\(t)”"
    }
}

public struct EditBudgetResult: Equatable {
    public let text: String
    public let applied: [EditSuggestion]
    public let dropped: [EditSuggestion]
    public var wordsUsed: Int { applied.reduce(0) { $0 + $1.cost } }
    public var freeFixes: Int { applied.filter(\.isFree).count }
    public var wordChangesApplied: Int { applied.filter { !$0.isFree }.count }
}

public struct EditMeasure: Equatable {
    public let wordChanges: Int
    public let freeFixes: Int
    public let rewordings: Int
}

/// Enforces "correct the syntax, but change at most N words". The AI's reply
/// is diffed against the author's text; punctuation, LaTeX and typo-level
/// fixes are always kept, rewordings are kept smallest-first until the word
/// budget is spent, and everything else is reverted to the author's words.
public enum EditBudget {
    /// Commands whose addition or removal changes document structure or
    /// content rather than fixing syntax.
    static let structuralCommands: Set<String> = [
        "\\begin", "\\end", "\\part", "\\chapter", "\\section", "\\subsection", "\\subsubsection",
        "\\paragraph", "\\subparagraph", "\\item", "\\label", "\\input", "\\include", "\\caption",
        "\\documentclass", "\\usepackage", "\\newcommand", "\\renewcommand", "\\bibliography",
        "\\bibliographystyle", "\\maketitle", "\\appendix", "\\newpage", "\\clearpage", "\\footnote",
        "\\cite", "\\citep", "\\citet", "\\ref", "\\eqref", "\\cref", "\\Cref", "\\autoref",
    ]

    struct Hunk {
        let range: Range<Int>
        let deletedWords: [String]
        let insertedWords: [String]
        let deletedCommands: [String]
        let insertedCommands: [String]
        let from: String
        let to: String
        var isStructural: Bool {
            deletedCommands.contains { EditBudget.structuralCommands.contains($0) }
                || insertedCommands.contains { EditBudget.structuralCommands.contains($0) }
        }
        var cost: Int {
            if deletedWords.isEmpty && insertedWords.isEmpty { return 0 }
            if deletedWords.count == insertedWords.count,
               zip(deletedWords, insertedWords).allSatisfy({ EditBudget.isMinorVariant($0, $1) }) {
                return 0
            }
            return max(deletedWords.count, insertedWords.count)
        }
    }

    /// Words whose "typo-level" variants flip meaning; never free.
    static let guarded: Set<String> = ["not", "no", "nor", "now", "never", "none", "non", "un"]

    /// Typo, capitalization or inflection fix (show/shows, recieve/receive, a/an).
    static func isMinorVariant(_ a: String, _ b: String) -> Bool {
        let x = a.lowercased(), y = b.lowercased()
        if x == y { return true }
        if guarded.contains(x) || guarded.contains(y) { return false }
        let d = levenshtein(x, y)
        if d <= 1 { return true }
        return d == 2 && min(x.count, y.count) >= 5
    }

    static func levenshtein(_ a: String, _ b: String) -> Int {
        let s = Array(a), t = Array(b)
        if s.isEmpty { return t.count }
        if t.isEmpty { return s.count }
        var prev = Array(0...t.count)
        var cur = [Int](repeating: 0, count: t.count + 1)
        for i in 1...s.count {
            cur[0] = i
            for j in 1...t.count {
                let sub = prev[j - 1] + (s[i - 1] == t[j - 1] ? 0 : 1)
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, sub)
            }
            swap(&prev, &cur)
        }
        return prev[t.count]
    }

    static func isWhitespace(_ seg: DiffSegment) -> Bool {
        seg.text.allSatisfy { $0.isWhitespace }
    }

    /// Runs of changed tokens. Runs separated only by whitespace are merged so
    /// a phrase replacement ("very big" → "huge") is accepted or rejected whole.
    static func hunks(_ segs: [DiffSegment]) -> [Hunk] {
        var out: [Hunk] = []
        var i = 0
        while i < segs.count {
            guard segs[i].kind != .equal else { i += 1; continue }
            var j = i
            while j < segs.count {
                if segs[j].kind != .equal { j += 1; continue }
                // Look past equal whitespace to see if the change continues.
                var k = j
                while k < segs.count && segs[k].kind == .equal && isWhitespace(segs[k]) { k += 1 }
                if k < segs.count && segs[k].kind != .equal && k > j { j = k } else { break }
            }
            let slice = segs[i..<j]
            func isCommand(_ t: String) -> Bool { t.count > 1 && t.hasPrefix("\\") && t.dropFirst().allSatisfy(\.isLetter) }
            out.append(Hunk(
                range: i..<j,
                deletedWords: slice.filter { $0.kind == .deleted && $0.isWord }.map(\.text),
                insertedWords: slice.filter { $0.kind == .inserted && $0.isWord }.map(\.text),
                deletedCommands: slice.filter { $0.kind == .deleted && isCommand($0.text) }.map(\.text),
                insertedCommands: slice.filter { $0.kind == .inserted && isCommand($0.text) }.map(\.text),
                from: slice.filter { $0.kind != .inserted }.map(\.originalText).joined(),
                to: slice.filter { $0.kind != .deleted }.map(\.text).joined()))
            i = j
        }
        return out
    }

    /// How far `new` departs from `original` under the budget's accounting.
    public static func measure(original: String, new: String) -> EditMeasure {
        let hs = hunks(WordDiff.diff(old: original, new: new))
        let paid = hs.filter { $0.cost > 0 || $0.isStructural }
        return EditMeasure(wordChanges: paid.reduce(0) { $0 + max($1.cost, $1.isStructural ? 1 : 0) },
                           freeFixes: hs.count - paid.count,
                           rewordings: paid.count)
    }

    public static func constrain(original: String, rewrite: String, maxWords: Int) -> EditBudgetResult {
        let segs = WordDiff.diff(old: original, new: rewrite)
        let hs = hunks(segs)
        var accepted = Set<Int>()
        var used = 0
        // Structural changes are never applied automatically, whatever the budget.
        for (i, h) in hs.enumerated() where h.cost == 0 && !h.isStructural { accepted.insert(i) }
        let paid = hs.enumerated().filter { $0.element.cost > 0 && !$0.element.isStructural }
            .sorted { ($0.element.cost, $0.offset) < ($1.element.cost, $1.offset) }
        for (i, h) in paid where used + h.cost <= maxWords {
            accepted.insert(i)
            used += h.cost
        }

        var out = ""
        var hunkIndex = 0
        var segIndex = 0
        while segIndex < segs.count {
            if hunkIndex < hs.count, hs[hunkIndex].range.lowerBound == segIndex {
                let h = hs[hunkIndex]
                let keep = accepted.contains(hunkIndex)
                for k in h.range {
                    let seg = segs[k]
                    switch seg.kind {
                    case .equal: out += keep ? seg.text : seg.originalText
                    case .inserted: if keep { out += seg.text }
                    case .deleted: if !keep { out += seg.text }
                    }
                }
                segIndex = h.range.upperBound
                hunkIndex += 1
            } else {
                out += segs[segIndex].originalText
                segIndex += 1
            }
        }

        func suggestion(_ i: Int, _ h: Hunk) -> EditSuggestion {
            EditSuggestion(id: i, from: h.from, to: h.to, cost: max(h.cost, h.isStructural ? 1 : 0), isStructural: h.isStructural)
        }
        let applied = hs.enumerated().filter { accepted.contains($0.offset) }.map { suggestion($0.offset, $0.element) }
        let dropped = hs.enumerated().filter { !accepted.contains($0.offset) }.map { suggestion($0.offset, $0.element) }
        return EditBudgetResult(text: out, applied: applied, dropped: dropped)
    }

    /// Apply one previously dropped suggestion by hand (first exact occurrence).
    public static func apply(_ s: EditSuggestion, to text: String) -> String? {
        if s.from.isEmpty { return nil }
        guard let r = text.range(of: s.from) else { return nil }
        return text.replacingCharacters(in: r, with: s.to)
    }
}

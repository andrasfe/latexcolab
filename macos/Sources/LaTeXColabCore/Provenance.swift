import Foundation

/// Which parts of a rewrite were produced by the AI. Ranges are UTF-16 ranges
/// in the current draft text, aligned to WordDiff tokens (words and punctuation;
/// whitespace is never marked). They are carried through later edits by
/// re-aligning old and new text token by token.
public struct Provenance: Codable, Equatable {
    public struct RangeRecord: Codable, Equatable {
        public var location: Int
        public var length: Int
        public init(_ r: NSRange) { location = r.location; length = r.length }
        public var nsRange: NSRange { NSRange(location: location, length: length) }
    }

    public var aiRanges: [RangeRecord]
    /// Text the AI deleted outright (no replacement), used to mark those words
    /// in the document pane.
    public var aiDeletions: [String]

    public init(aiRanges: [NSRange] = [], aiDeletions: [String] = []) {
        self.aiRanges = aiRanges.map(RangeRecord.init)
        self.aiDeletions = aiDeletions
    }

    public var ranges: [NSRange] { aiRanges.map(\.nsRange) }
    public var isEmpty: Bool { aiRanges.isEmpty && aiDeletions.isEmpty }

    static func covers(_ ranges: [NSRange], _ r: NSRange) -> Bool {
        guard r.length > 0 else { return false }
        return ranges.contains { NSIntersectionRange($0, r).length > 0 }
    }

    static func isMarkable(_ seg: DiffSegment) -> Bool {
        !seg.text.allSatisfy { $0.isWhitespace }
    }

    /// Coalesce adjacent/overlapping ranges.
    public static func merge(_ ranges: [NSRange]) -> [NSRange] {
        let sorted = ranges.filter { $0.length > 0 }.sorted { $0.location < $1.location }
        var out: [NSRange] = []
        for r in sorted {
            if let last = out.last, NSMaxRange(last) >= r.location {
                out[out.count - 1] = NSUnionRange(last, r)
            } else {
                out.append(r)
            }
        }
        return out
    }

    /// Carry AI marks from `old` to `new`: tokens that survive keep their mark,
    /// tokens inserted in `new` are marked when `insertedByAI` is true.
    /// Returns the new provenance; `aiDeletions` grows with words the AI removed.
    public func remapped(from old: String, to new: String, insertedByAI: Bool) -> Provenance {
        let oldRanges = ranges
        var newRanges: [NSRange] = []
        var deletions = aiDeletions
        let segs = WordDiff.diff(old: old, new: new)
        var oldPos = 0, newPos = 0
        var i = 0
        var previousEqualPunctuation: NSRange? = nil   // new-text range of the last equal punctuation token
        func isPunctuation(_ seg: DiffSegment) -> Bool {
            Provenance.isMarkable(seg) && !seg.isWord && !seg.text.hasPrefix("\\")
        }
        while i < segs.count {
            let seg = segs[i]
            switch seg.kind {
            case .equal:
                let oldLen = seg.originalText.utf16.count, newLen = seg.text.utf16.count
                let r = NSRange(location: newPos, length: newLen)
                if Provenance.isMarkable(seg), Provenance.covers(oldRanges, NSRange(location: oldPos, length: oldLen)) {
                    newRanges.append(r)
                }
                previousEqualPunctuation = isPunctuation(seg) ? r : (Provenance.isMarkable(seg) ? nil : previousEqualPunctuation)
                oldPos += oldLen; newPos += newLen
                i += 1
            default:
                // A hunk: consecutive non-equal segments.
                var j = i
                var deletedText = ""
                var insertedAny = false
                var insertedText = ""
                while j < segs.count && segs[j].kind != .equal {
                    let s = segs[j]
                    if s.kind == .deleted {
                        deletedText += s.text
                        oldPos += s.text.utf16.count
                    } else {
                        let len = s.text.utf16.count
                        if insertedByAI && Provenance.isMarkable(s) { newRanges.append(NSRange(location: newPos, length: len)) }
                        if Provenance.isMarkable(s) { insertedAny = true }
                        insertedText += s.text
                        newPos += len
                    }
                    j += 1
                }
                let trimmed = deletedText.trimmingCharacters(in: .whitespacesAndNewlines)
                if insertedByAI && !insertedAny && !trimmed.isEmpty { deletions.append(trimmed) }
                // A whitespace-only fix ("method , works" → "method, works") is
                // really about the punctuation next to it: mark that mark.
                if insertedByAI && !insertedAny && trimmed.isEmpty && (!deletedText.isEmpty || !insertedText.isEmpty) {
                    if j < segs.count, isPunctuation(segs[j]) {
                        newRanges.append(NSRange(location: newPos, length: segs[j].text.utf16.count))
                    } else if let prev = previousEqualPunctuation {
                        newRanges.append(prev)
                    }
                }
                i = j
            }
        }
        var result = Provenance()
        result.aiRanges = Provenance.merge(newRanges).map(RangeRecord.init)
        result.aiDeletions = deletions
        return result
    }

    /// Highlight sets for the two panes.
    public struct Highlights: Equatable {
        public var aiInDraft: [NSRange] = []
        public var userInDraft: [NSRange] = []
        public var aiInOriginal: [NSRange] = []
        public var userInOriginal: [NSRange] = []
        public var aiWords = 0
        public var userWords = 0
        public var aiPunctuation = 0
    }

    /// Partition the differences between the document text and the draft into
    /// AI-made and user-made, and mark AI-made words that both sides share
    /// (after Apply the document itself contains AI words).
    public func highlights(original: String, draft: String) -> Highlights {
        var h = Highlights()
        let ai = ranges
        var pendingDeletions = aiDeletions
        let segs = WordDiff.diff(old: original, new: draft)
        var oldPos = 0, newPos = 0
        var i = 0
        while i < segs.count {
            let seg = segs[i]
            if seg.kind == .equal {
                let oldLen = seg.originalText.utf16.count, newLen = seg.text.utf16.count
                let r = NSRange(location: newPos, length: newLen)
                if Provenance.isMarkable(seg), Provenance.covers(ai, r) {
                    h.aiInDraft.append(r)
                    h.aiInOriginal.append(NSRange(location: oldPos, length: oldLen))
                    if seg.isWord { h.aiWords += 1 } else { h.aiPunctuation += 1 }
                }
                oldPos += oldLen; newPos += newLen
                i += 1
                continue
            }
            var j = i
            var inserted: [(NSRange, DiffSegment)] = []
            var deleted: [(NSRange, DiffSegment)] = []
            while j < segs.count && segs[j].kind != .equal {
                let s = segs[j]
                let len = s.text.utf16.count
                if s.kind == .deleted {
                    deleted.append((NSRange(location: oldPos, length: len), s)); oldPos += len
                } else {
                    inserted.append((NSRange(location: newPos, length: len), s)); newPos += len
                }
                j += 1
            }
            let hunkIsAI: Bool
            if inserted.contains(where: { Provenance.isMarkable($0.1) }) {
                hunkIsAI = inserted.contains { Provenance.isMarkable($0.1) && Provenance.covers(ai, $0.0) }
            } else {
                let text = deleted.map(\.1.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
                if let k = pendingDeletions.firstIndex(of: text) {
                    pendingDeletions.remove(at: k)
                    hunkIsAI = true
                } else {
                    hunkIsAI = false
                }
            }
            for (r, s) in inserted where Provenance.isMarkable(s) {
                let isAI = Provenance.covers(ai, r)
                if isAI { h.aiInDraft.append(r); if s.isWord { h.aiWords += 1 } else { h.aiPunctuation += 1 } }
                else { h.userInDraft.append(r); if s.isWord { h.userWords += 1 } }
            }
            for (r, s) in deleted where Provenance.isMarkable(s) {
                if hunkIsAI { h.aiInOriginal.append(r) } else { h.userInOriginal.append(r) }
            }
            i = j
        }
        h.aiInDraft = Provenance.merge(h.aiInDraft)
        h.userInDraft = Provenance.merge(h.userInDraft)
        h.aiInOriginal = Provenance.merge(h.aiInOriginal)
        h.userInOriginal = Provenance.merge(h.userInOriginal)
        return h
    }
}

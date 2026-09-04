import Foundation

public enum DiffKind: Equatable {
    case equal, inserted, deleted
}

public struct DiffSegment: Equatable {
    public let text: String
    public let kind: DiffKind
    public let isWord: Bool
}

struct DiffToken: Equatable {
    let text: String
    let key: String
    let isWord: Bool
}

/// Word-level diff (LCS) used to highlight a rewrite and to count how many
/// words changed.
public enum WordDiff {
    static func tokenize(_ s: String) -> [DiffToken] {
        var out: [DiffToken] = []
        let scalars = Array(s.unicodeScalars)
        var i = 0
        func isWordScalar(_ u: Unicode.Scalar) -> Bool {
            CharacterSet.alphanumerics.contains(u) || u == "'" || u == "\u{2019}"
        }
        while i < scalars.count {
            let u = scalars[i]
            if isWordScalar(u) {
                var j = i
                while j < scalars.count && (isWordScalar(scalars[j]) || (scalars[j] == "-" && j + 1 < scalars.count && isWordScalar(scalars[j + 1]))) { j += 1 }
                let t = String(String.UnicodeScalarView(scalars[i..<j]))
                out.append(DiffToken(text: t, key: t, isWord: true))
                i = j
            } else if u == "\\" {
                var j = i + 1
                while j < scalars.count && CharacterSet.letters.contains(scalars[j]) { j += 1 }
                if j == i + 1 && j < scalars.count { j += 1 } // \% \, \\ etc.
                let t = String(String.UnicodeScalarView(scalars[i..<j]))
                out.append(DiffToken(text: t, key: t, isWord: false))
                i = j
            } else if CharacterSet.whitespacesAndNewlines.contains(u) {
                var j = i
                while j < scalars.count && CharacterSet.whitespacesAndNewlines.contains(scalars[j]) { j += 1 }
                let t = String(String.UnicodeScalarView(scalars[i..<j]))
                out.append(DiffToken(text: t, key: " ", isWord: false))
                i = j
            } else {
                let t = String(u)
                out.append(DiffToken(text: t, key: t, isWord: false))
                i += 1
            }
        }
        return out
    }

    public static func diff(old: String, new: String) -> [DiffSegment] {
        let a = tokenize(old)
        let b = tokenize(new)
        let n = a.count, m = b.count
        if n == 0 { return b.map { DiffSegment(text: $0.text, kind: .inserted, isWord: $0.isWord) } }
        if m == 0 { return a.map { DiffSegment(text: $0.text, kind: .deleted, isWord: $0.isWord) } }

        // Trim the common prefix/suffix first: keeps the DP tiny for small edits.
        var start = 0
        while start < n && start < m && a[start].key == b[start].key { start += 1 }
        var endA = n, endB = m
        while endA > start && endB > start && a[endA - 1].key == b[endB - 1].key { endA -= 1; endB -= 1 }

        var segs: [DiffSegment] = []
        for t in a[0..<start] { segs.append(DiffSegment(text: t.text, kind: .equal, isWord: t.isWord)) }

        let ma = Array(a[start..<endA]), mb = Array(b[start..<endB])
        let na = ma.count, nb = mb.count
        if na > 0 || nb > 0 {
            if na * nb > 6_000_000 {
                for t in ma { segs.append(DiffSegment(text: t.text, kind: .deleted, isWord: t.isWord)) }
                for t in mb { segs.append(DiffSegment(text: t.text, kind: .inserted, isWord: t.isWord)) }
            } else {
                // LCS table, then backtrack.
                let w = nb + 1
                var table = [Int32](repeating: 0, count: (na + 1) * w)
                for i in stride(from: na - 1, through: 0, by: -1) {
                    for j in stride(from: nb - 1, through: 0, by: -1) {
                        if ma[i].key == mb[j].key {
                            table[i * w + j] = table[(i + 1) * w + j + 1] + 1
                        } else {
                            table[i * w + j] = max(table[(i + 1) * w + j], table[i * w + j + 1])
                        }
                    }
                }
                var i = 0, j = 0
                while i < na && j < nb {
                    if ma[i].key == mb[j].key {
                        segs.append(DiffSegment(text: mb[j].text, kind: .equal, isWord: mb[j].isWord))
                        i += 1; j += 1
                    } else if table[(i + 1) * w + j] >= table[i * w + j + 1] {
                        segs.append(DiffSegment(text: ma[i].text, kind: .deleted, isWord: ma[i].isWord))
                        i += 1
                    } else {
                        segs.append(DiffSegment(text: mb[j].text, kind: .inserted, isWord: mb[j].isWord))
                        j += 1
                    }
                }
                while i < na { segs.append(DiffSegment(text: ma[i].text, kind: .deleted, isWord: ma[i].isWord)); i += 1 }
                while j < nb { segs.append(DiffSegment(text: mb[j].text, kind: .inserted, isWord: mb[j].isWord)); j += 1 }
            }
        }
        for t in b[endB..<m] { segs.append(DiffSegment(text: t.text, kind: .equal, isWord: t.isWord)) }
        return segs
    }

    /// Words changed = max(inserted words, deleted words) — a substitution counts once.
    public static func changedWordCount(_ segments: [DiffSegment]) -> Int {
        let ins = segments.filter { $0.kind == .inserted && $0.isWord }.count
        let del = segments.filter { $0.kind == .deleted && $0.isWord }.count
        return max(ins, del)
    }

    /// UTF-16 ranges of changed tokens on each side, for highlighting.
    public static func highlightRanges(_ segments: [DiffSegment]) -> (old: [NSRange], new: [NSRange]) {
        var oldRanges: [NSRange] = []
        var newRanges: [NSRange] = []
        var oldPos = 0, newPos = 0
        for s in segments {
            let len = s.text.utf16.count
            switch s.kind {
            case .equal:
                oldPos += len; newPos += len
            case .deleted:
                oldRanges.append(NSRange(location: oldPos, length: len)); oldPos += len
            case .inserted:
                newRanges.append(NSRange(location: newPos, length: len)); newPos += len
            }
        }
        return (oldRanges, newRanges)
    }
}

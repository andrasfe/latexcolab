import Foundation

/// A blank-line delimited block of LaTeX source. Lines are 1-based and inclusive.
public struct ParagraphRange: Equatable, Hashable {
    public let startLine: Int
    public let endLine: Int
    public let text: String

    public init(startLine: Int, endLine: Int, text: String) {
        self.startLine = startLine
        self.endLine = endLine
        self.text = text
    }

    public var lineCount: Int { endLine - startLine + 1 }
}

public enum ParagraphLocator {
    public static func lines(of source: String) -> [String] {
        source.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
    }

    static func isBlank(_ s: String) -> Bool {
        s.allSatisfy { $0.isWhitespace }
    }

    /// The paragraph that contains `line`. When `line` is blank (LaTeX reports a
    /// paragraph's boxes on the blank line that *ends* it) the paragraph above is
    /// preferred, then the one below.
    public static func paragraph(in source: String, containingLine line: Int) -> ParagraphRange? {
        let ls = lines(of: source)
        guard !ls.isEmpty else { return nil }
        var idx = min(max(line, 1), ls.count) - 1
        if isBlank(ls[idx]) {
            var up = idx - 1
            while up >= 0 && isBlank(ls[up]) { up -= 1 }
            if up >= 0 {
                idx = up
            } else {
                var down = idx + 1
                while down < ls.count && isBlank(ls[down]) { down += 1 }
                guard down < ls.count else { return nil }
                idx = down
            }
        }
        var start = idx
        while start > 0 && !isBlank(ls[start - 1]) { start -= 1 }
        var end = idx
        while end + 1 < ls.count && !isBlank(ls[end + 1]) { end += 1 }
        return ParagraphRange(startLine: start + 1, endLine: end + 1,
                              text: ls[start...end].joined(separator: "\n"))
    }

    /// Every paragraph in the source, in order.
    public static func paragraphs(in source: String) -> [ParagraphRange] {
        let ls = lines(of: source)
        var out: [ParagraphRange] = []
        var i = 0
        while i < ls.count {
            if isBlank(ls[i]) { i += 1; continue }
            var end = i
            while end + 1 < ls.count && !isBlank(ls[end + 1]) { end += 1 }
            out.append(ParagraphRange(startLine: i + 1, endLine: end + 1,
                                      text: ls[i...end].joined(separator: "\n")))
            i = end + 1
        }
        return out
    }

    /// Replace the given line range with `newText` (which may have a different line count).
    public static func replacing(_ range: ParagraphRange, in source: String, with newText: String) -> String {
        var ls = lines(of: source)
        guard range.startLine >= 1, range.endLine <= ls.count, range.startLine <= range.endLine else {
            return source
        }
        let newLines = newText.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        ls.replaceSubrange((range.startLine - 1)...(range.endLine - 1), with: newLines)
        return ls.joined(separator: "\n")
    }

    /// Re-find `expected` in a (possibly changed) source. Checks the remembered
    /// range first, then falls back to a unique (or nearest) exact text match.
    public static func locate(expected: String, near range: ParagraphRange, in source: String) -> ParagraphRange? {
        let want = expected.trimmingCharacters(in: .whitespacesAndNewlines)
        let ls = lines(of: source)
        if range.startLine >= 1, range.endLine <= ls.count, range.startLine <= range.endLine {
            let here = ls[(range.startLine - 1)...(range.endLine - 1)].joined(separator: "\n")
            if here.trimmingCharacters(in: .whitespacesAndNewlines) == want {
                return ParagraphRange(startLine: range.startLine, endLine: range.endLine, text: here)
            }
        }
        let matches = paragraphs(in: source).filter {
            $0.text.trimmingCharacters(in: .whitespacesAndNewlines) == want
        }
        if matches.count == 1 { return matches[0] }
        return matches.min { abs($0.startLine - range.startLine) < abs($1.startLine - range.startLine) }
    }
}

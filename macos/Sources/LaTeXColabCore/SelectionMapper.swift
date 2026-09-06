import Foundation

/// Maps text as it appears in the PDF (a mouse selection, or the sentence under
/// a click) onto the exact substring of the LaTeX source that produced it.
public enum SelectionMapper {
    /// A prose word in the LaTeX source with its UTF-16 range.
    struct SourceWord {
        let key: String
        let range: NSRange
    }

    // Regions of LaTeX that produce no prose words (or words that appear elsewhere).
    static let skipRE = try! NSRegularExpression(pattern: [
        #"(?<!\\)%[^\n]*"#,
        #"\$\$[\s\S]*?\$\$"#, #"\$[^$\n]*\$"#, #"\\\[[\s\S]*?\\\]"#, #"\\\([\s\S]*?\\\)"#,
        #"\\(?:cite[a-zA-Z]*|ref|eqref|cref|Cref|autoref|pageref|vref|label|includegraphics|url|input|include|begin|end|vspace|hspace|footnote|footnotemark|documentclass|usepackage|bibliography|bibliographystyle|newcommand|renewcommand|setlength|caption|centering)\*?(?:\[[^\]]*\]){0,2}(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})?"#,
        #"\\[a-zA-Z@]+\*?"#,   // remaining command names (their brace contents stay visible)
        #"\\[^a-zA-Z]"#,        // \% \& \_ \, \\ …
        #"[{}]"#,
    ].joined(separator: "|"))

    static let wordRE = try! NSRegularExpression(pattern: #"[\p{L}\p{N}]+(?:['’][\p{L}]+)?"#)

    static let ligatures: [String: String] = ["ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"]

    public static func normalize(word: String) -> String {
        var w = word
        for (k, v) in ligatures { w = w.replacingOccurrences(of: k, with: v) }
        w = w.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: nil)
        return String(w.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) })
    }

    /// Ranges of the container that carry no prose (comments, math, commands).
    static func skipRanges(in s: String) -> [NSRange] {
        skipRE.matches(in: s, range: NSRange(location: 0, length: (s as NSString).length)).map(\.range)
    }

    static func sourceWords(in s: String) -> [SourceWord] {
        let ns = s as NSString
        var out: [SourceWord] = []
        var cursor = 0
        func scan(_ r: NSRange) {
            for m in wordRE.matches(in: s, range: r) {
                let key = normalize(word: ns.substring(with: m.range))
                if !key.isEmpty { out.append(SourceWord(key: key, range: m.range)) }
            }
        }
        for skip in skipRanges(in: s) {
            if skip.location > cursor { scan(NSRange(location: cursor, length: skip.location - cursor)) }
            cursor = max(cursor, NSMaxRange(skip))
        }
        if cursor < ns.length { scan(NSRange(location: cursor, length: ns.length - cursor)) }
        return out
    }

    /// Words of text copied out of a PDF: re-join hyphenation, expand ligatures.
    public static func pdfWords(_ text: String) -> [String] {
        let joined = text.replacingOccurrences(of: "-\n", with: "")
            .replacingOccurrences(of: "\u{AD}", with: "")
            .replacingOccurrences(of: "\u{2010}\n", with: "")
        let ns = joined as NSString
        return wordRE.matches(in: joined, range: NSRange(location: 0, length: ns.length))
            .map { normalize(word: ns.substring(with: $0.range)) }
            .filter { !$0.isEmpty }
    }

    /// Longest common subsequence of PDF words against source words; returns
    /// the matched source indices in order.
    static func align(_ p: [String], _ s: [SourceWord]) -> [Int] {
        let n = p.count, m = s.count
        guard n > 0, m > 0, n * m <= 4_000_000 else { return [] }
        let w = m + 1
        var table = [Int32](repeating: 0, count: (n + 1) * w)
        for i in stride(from: n - 1, through: 0, by: -1) {
            for j in stride(from: m - 1, through: 0, by: -1) {
                table[i * w + j] = p[i] == s[j].key
                    ? table[(i + 1) * w + j + 1] + 1
                    : max(table[(i + 1) * w + j], table[i * w + j + 1])
            }
        }
        var i = 0, j = 0
        var matched: [Int] = []
        while i < n && j < m {
            if p[i] == s[j].key { matched.append(j); i += 1; j += 1 }
            else if table[(i + 1) * w + j] >= table[i * w + j + 1] { i += 1 }
            else { j += 1 }
        }
        return matched
    }

    /// Grow `range` so that braces are balanced (opening `\cmd{` before it,
    /// closing `}` after it) and trailing punctuation that the PDF text also
    /// ends with is included.
    static func tidy(_ range: NSRange, in s: String, pdfText: String?) -> NSRange {
        let ns = s as NSString
        var start = range.location
        var end = NSMaxRange(range)

        // Trailing punctuation the selection visibly ends with.
        if let last = pdfText?.trimmingCharacters(in: .whitespacesAndNewlines).last, ".,;:!?".contains(last) {
            var k = end
            while k < ns.length, "}')\u{201D}".contains(Character(UnicodeScalar(ns.character(at: k))!)) { k += 1 }
            if k < ns.length, Character(UnicodeScalar(ns.character(at: k))!) == last { end = k + 1 }
        }

        func balance() -> Int {
            var bal = 0
            var i = start
            while i < end {
                let c = ns.character(at: i)
                let prev: unichar = i > 0 ? ns.character(at: i - 1) : 32
                if c == 123 && prev != 92 { bal += 1 }      // {
                if c == 125 && prev != 92 { bal -= 1 }      // }
                i += 1
            }
            return bal
        }
        var guardCount = 0
        while balance() < 0 && start > 0 && guardCount < 20 {
            // Extend backwards to the opening brace and its command.
            var i = start - 1
            var depth = 0
            while i >= 0 {
                let c = ns.character(at: i)
                if c == 125 { depth += 1 }
                if c == 123 { if depth == 0 { break }; depth -= 1 }
                i -= 1
            }
            if i < 0 { break }
            var j = i - 1
            while j >= 0, let sc = UnicodeScalar(ns.character(at: j)), CharacterSet.letters.contains(sc) || sc == "*" { j -= 1 }
            if j >= 0 && ns.character(at: j) == 92 { start = j } else { start = i }
            guardCount += 1
        }
        guardCount = 0
        while balance() > 0 && end < ns.length && guardCount < 20 {
            var i = end
            var depth = 0
            while i < ns.length {
                let c = ns.character(at: i)
                if c == 123 { depth += 1 }
                if c == 125 { if depth == 0 { break }; depth -= 1 }
                i += 1
            }
            if i >= ns.length { break }
            end = i + 1
            guardCount += 1
        }
        return NSRange(location: start, length: end - start)
    }

    /// The substring of `container` that produced `pdfText`. Needs at least
    /// 60 % of the PDF words to be found, in order.
    public static func sourceRange(forPDFText pdfText: String, in container: String) -> NSRange? {
        let p = pdfWords(pdfText)
        let s = sourceWords(in: container)
        guard !p.isEmpty, !s.isEmpty else { return nil }
        let matched = align(p, s)
        guard Double(matched.count) >= 0.6 * Double(p.count), let first = matched.first, let last = matched.last else { return nil }
        let raw = NSRange(location: s[first].range.location, length: NSMaxRange(s[last].range) - s[first].range.location)
        return tidy(raw, in: container, pdfText: pdfText)
    }

    // MARK: - Sentences

    static let structuralLineRE = try! NSRegularExpression(
        pattern: #"^\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph|begin|end|item|label|caption|centering|vspace|hspace|includegraphics|input|include|maketitle|title|author|date|newpage|clearpage)\b"#)

    static let abbreviations: Set<String> = [
        "e.g", "i.e", "etc", "vs", "cf", "fig", "figs", "eq", "eqs", "sec", "secs", "al", "no", "nos",
        "dr", "prof", "mr", "mrs", "ms", "st", "approx", "ref", "refs", "resp", "viz", "ca", "ch", "tab", "alg", "vol", "pp", "p",
    ]

    /// Sentence terminators (outside comments/math/commands) as UTF-16 offsets of the terminator character.
    static func terminators(in s: String) -> [Int] {
        let ns = s as NSString
        let skips = skipRanges(in: s)
        func skipped(_ i: Int) -> Bool { skips.contains { NSLocationInRange(i, $0) } }
        var out: [Int] = []
        var i = 0
        while i < ns.length {
            let c = ns.character(at: i)
            if (c == 46 || c == 33 || c == 63) && !skipped(i) {   // . ! ?
                // Must be followed by whitespace/end, possibly after closers.
                var k = i + 1
                while k < ns.length, "}')\u{201D}\"".utf16.contains(ns.character(at: k)) { k += 1 }
                let followedByBreak = k >= ns.length || CharacterSet.whitespacesAndNewlines.contains(UnicodeScalar(ns.character(at: k))!)
                var ok = followedByBreak
                if ok && c == 46 {
                    // Abbreviations, initials, decimals ("3.5" has no break so already excluded).
                    var j = i - 1
                    while j >= 0, let sc = UnicodeScalar(ns.character(at: j)), CharacterSet.letters.contains(sc) || sc == "." { j -= 1 }
                    let word = ns.substring(with: NSRange(location: j + 1, length: i - j - 1)).lowercased()
                    let bare = word.hasSuffix(".") ? String(word.dropLast()) : word
                    if abbreviations.contains(bare) || abbreviations.contains(word) { ok = false }
                    if word.count == 1, let f = word.first, f.isLetter, j >= 0, ns.character(at: j) == 32 { ok = false } // "A. Smith"
                    // Next sentence should start with an uppercase letter, digit, \ or ( — lowercase suggests abbreviation.
                    var n = k
                    while n < ns.length, CharacterSet.whitespacesAndNewlines.contains(UnicodeScalar(ns.character(at: n))!) { n += 1 }
                    if n < ns.length, let sc = UnicodeScalar(ns.character(at: n)), CharacterSet.lowercaseLetters.contains(sc) { ok = false }
                }
                if ok { out.append(k - 1) }   // include closers in the sentence
            }
            i += 1
        }
        return out
    }

    /// The sentence (UTF-16 range) of `container` containing `offset`.
    /// Sentences also break at blank lines.
    public static func sentenceRange(in container: String, containing offset: Int) -> NSRange {
        let ns = container as NSString
        let ends = terminators(in: container)
        var start = 0
        for e in ends where e < offset { start = e + 1 }
        var end = ns.length
        for e in ends where e >= offset { end = e + 1; break }

        // Blank lines, lines without any prose and lines that start with a
        // structural command (\section{…}, \begin{…}, \item, \label{…}) bound
        // sentences too.
        let words = sourceWords(in: container)
        var lineStart = 0
        while lineStart <= ns.length {
            let lineRange = ns.lineRange(for: NSRange(location: lineStart, length: 0))
            let content = ns.substring(with: lineRange).trimmingCharacters(in: .whitespacesAndNewlines)
            let hasProse = words.contains { NSLocationInRange($0.range.location, lineRange) }
            let structural = structuralLineRE.firstMatch(in: content, range: NSRange(location: 0, length: (content as NSString).length)) != nil
            if content.isEmpty || !hasProse || structural {
                if NSMaxRange(lineRange) <= offset { start = max(start, NSMaxRange(lineRange)) }
                else if lineRange.location > offset { end = min(end, lineRange.location) }
                else if !content.isEmpty && NSLocationInRange(offset, lineRange) {
                    // Offset is on a heading line itself: the "sentence" is that line.
                    start = lineRange.location
                    end = NSMaxRange(lineRange)
                }
            }
            if NSMaxRange(lineRange) >= ns.length || lineRange.length == 0 { break }
            lineStart = NSMaxRange(lineRange)
        }
        while start < end, CharacterSet.whitespacesAndNewlines.contains(UnicodeScalar(ns.character(at: start))!) { start += 1 }
        while end > start, CharacterSet.whitespacesAndNewlines.contains(UnicodeScalar(ns.character(at: end - 1))!) { end -= 1 }
        return tidy(NSRange(location: start, length: end - start), in: container, pdfText: nil)
    }

    /// Sentence containing a word seen in the PDF; when the word occurs more
    /// than once, the occurrence nearest to `nearOffset` wins.
    public static func sentenceRange(in container: String, containingPDFWord word: String, nearOffset: Int) -> NSRange? {
        let key = normalize(word: word)
        guard !key.isEmpty else { return nil }
        let candidates = sourceWords(in: container).filter { $0.key == key }
        guard let best = candidates.min(by: { abs($0.range.location - nearOffset) < abs($1.range.location - nearOffset) }) else { return nil }
        return sentenceRange(in: container, containing: best.range.location)
    }

    /// UTF-16 offset of the start of `line` (1-based) within `container`.
    public static func offset(ofLine line: Int, in container: String) -> Int {
        let ns = container as NSString
        var current = 1
        var i = 0
        while i < ns.length && current < line {
            if ns.character(at: i) == 10 { current += 1 }
            i += 1
        }
        return i
    }
}

import Foundation

/// Fallback PDF→source mapping when no SyncTeX data is available: match the
/// text near the click against a de-TeXed version of every paragraph.
public enum TextMatcher {
    public struct Match: Equatable {
        public let file: String
        public let paragraph: ParagraphRange
        public let score: Double
    }

    static let patterns: [String] = [
        #"(?<!\\)%[^\n]*"#,                                        // comments
        #"\\(?:label|cite[a-zA-Z]*|ref|eqref|autoref|cref|Cref|includegraphics|input|include|bibliography|bibliographystyle|usepackage|documentclass)\*?(?:\[[^\]]*\])?\{[^}]*\}"#,
        #"\\(?:begin|end)\{[^}]*\}"#,
        #"\$\$[\s\S]*?\$\$"#,
        #"\\\[[\s\S]*?\\\]"#,
        #"\\\([\s\S]*?\\\)"#,
        #"\$[^$\n]*\$"#,
        #"\\[a-zA-Z@]+\*?"#,                                      // remaining commands
    ]
    static let compiled: [NSRegularExpression] = patterns.compactMap { pattern in
        try? NSRegularExpression(pattern: pattern, options: [])
    }

    /// Lower-cased word tokens with LaTeX markup stripped.
    public static func tokens(latex: String) -> [String] {
        var s = latex
        for re in compiled {
            s = re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: " ")
        }
        return tokens(plain: s)
    }

    /// Tokens for text extracted from a PDF (re-joins hyphenated line breaks).
    public static func tokens(pdfText: String) -> [String] {
        let joined = pdfText
            .replacingOccurrences(of: "-\n", with: "")
            .replacingOccurrences(of: "\u{AD}", with: "")
        return tokens(plain: joined)
    }

    static func tokens(plain: String) -> [String] {
        plain.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count >= 2 }
    }

    static func bigrams(_ t: [String]) -> Set<String> {
        guard t.count >= 2 else { return [] }
        var out = Set<String>()
        for i in 0..<(t.count - 1) {
            let pair: String = t[i] + " " + t[i + 1]
            out.insert(pair)
        }
        return out
    }

    /// 0…1: how much of `query` is covered by `candidate`.
    public static func score(query: [String], candidate: [String]) -> Double {
        guard !query.isEmpty, !candidate.isEmpty else { return 0 }
        let cset = Set(candidate)
        let uni = Double(query.filter { cset.contains($0) }.count) / Double(query.count)
        let qb = bigrams(query)
        let cb = bigrams(candidate)
        let bi = qb.isEmpty ? uni : Double(qb.intersection(cb).count) / Double(qb.count)
        return 0.4 * uni + 0.6 * bi
    }

    /// Best paragraph across `sources` (`path` → LaTeX text) for the PDF text.
    public static func bestMatch(pdfText: String, sources: [(path: String, source: String)], threshold: Double = 0.45) -> Match? {
        let q = tokens(pdfText: pdfText)
        guard q.count >= 3 else { return nil }
        var best: Match? = nil
        for (path, source) in sources {
            for para in ParagraphLocator.paragraphs(in: source) {
                let c = tokens(latex: para.text)
                guard c.count >= 3 else { continue }
                let s = score(query: q, candidate: c)
                if s > (best?.score ?? threshold) || (best == nil && s >= threshold) {
                    best = Match(file: path, paragraph: para, score: s)
                }
            }
        }
        return best
    }
}

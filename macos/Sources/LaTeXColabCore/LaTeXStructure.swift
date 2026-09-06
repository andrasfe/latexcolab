import Foundation

/// Structural comparison of two LaTeX snippets: environments opened/closed,
/// brace and math-delimiter balance, sectioning commands. A proofread should
/// never change any of these.
public enum LaTeXStructure {
    static let beginRE = try! NSRegularExpression(pattern: #"\\begin\{([^}]*)\}"#)
    static let endRE = try! NSRegularExpression(pattern: #"\\end\{([^}]*)\}"#)
    static let sectioningRE = try! NSRegularExpression(pattern: #"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{"#)
    static let commentRE = try! NSRegularExpression(pattern: #"(?<!\\)%[^\n]*"#)

    static func stripComments(_ s: String) -> String {
        commentRE.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: "")
    }

    static func counts(_ re: NSRegularExpression, in s: String) -> [String: Int] {
        var out: [String: Int] = [:]
        let ns = s as NSString
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            let key = m.numberOfRanges > 1 ? ns.substring(with: m.range(at: 1)) : "*"
            out[key, default: 0] += 1
        }
        return out
    }

    static func braceBalance(_ s: String) -> Int {
        var bal = 0
        var prev: Character = " "
        for c in s {
            if c == "{" && prev != "\\" { bal += 1 }
            if c == "}" && prev != "\\" { bal -= 1 }
            prev = (prev == "\\" && c == "\\") ? " " : c
        }
        return bal
    }

    static func dollarCount(_ s: String) -> Int {
        var n = 0
        var prev: Character = " "
        for c in s {
            if c == "$" && prev != "\\" { n += 1 }
            prev = (prev == "\\" && c == "\\") ? " " : c
        }
        return n
    }

    /// Human-readable list of structural differences (empty when the edit is
    /// structurally identical to the original).
    public static func differences(original: String, edited: String) -> [String] {
        let a = stripComments(original), b = stripComments(edited)
        var out: [String] = []

        func compare(_ label: String, _ ca: [String: Int], _ cb: [String: Int]) {
            for key in Set(ca.keys).union(cb.keys).sorted() {
                let x = ca[key, default: 0], y = cb[key, default: 0]
                if y > x { out.append("adds \(label){\(key)}" + (y - x > 1 ? " ×\(y - x)" : "")) }
                if y < x { out.append("removes \(label){\(key)}" + (x - y > 1 ? " ×\(x - y)" : "")) }
            }
        }
        compare("\\begin", counts(beginRE, in: a), counts(beginRE, in: b))
        compare("\\end", counts(endRE, in: a), counts(endRE, in: b))

        let sa = counts(sectioningRE, in: a)["*", default: 0], sb = counts(sectioningRE, in: b)["*", default: 0]
        if sa != sb { out.append(sb > sa ? "adds a sectioning command" : "removes a sectioning command") }

        let ba = braceBalance(a), bb = braceBalance(b)
        if ba != bb {
            let d = bb - ba
            out.append(d > 0 ? "leaves \(d) more { than } unclosed" : "has \(-d) more } than {")
        }
        if dollarCount(a) % 2 != dollarCount(b) % 2 {
            out.append("changes the number of $ math delimiters to an odd count")
        }
        return out
    }
}

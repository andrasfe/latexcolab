import Foundation

/// The model's judgement of how an edited paragraph relates to the original.
public struct SemanticComparison: Equatable {
    public enum Verdict: String, Equatable {
        case same, minor, changed, unknown
    }

    public var verdict: Verdict
    public var summary: String
    public var changes: [String]
    public var meaningDifferences: [String]
    public var latexIssues: [String]
    public var raw: String
    public var model: String

    public init(verdict: Verdict, summary: String, changes: [String] = [], meaningDifferences: [String] = [],
                latexIssues: [String] = [], raw: String = "", model: String = "") {
        self.verdict = verdict
        self.summary = summary
        self.changes = changes
        self.meaningDifferences = meaningDifferences
        self.latexIssues = latexIssues
        self.raw = raw
        self.model = model
    }

    /// Result for two textually identical sides — no model call needed.
    public static let identical = SemanticComparison(
        verdict: .same, summary: "Both sides are identical; there is nothing to compare.", model: "none")

    public var headline: String {
        switch verdict {
        case .same: return "Says the same thing"
        case .minor: return "Same facts, slight shift in tone or emphasis"
        case .changed: return "Meaning changed"
        case .unknown: return "Could not interpret the model's answer"
        }
    }
}

public struct CompareRequest {
    public var original: String
    public var edited: String
    public var model: String
    public var temperature: Double

    public init(original: String, edited: String, model: String, temperature: Double = 0.1) {
        self.original = original
        self.edited = edited
        self.model = model
        self.temperature = temperature
    }
}

extension LMStudioService {
    public static let compareSystemPrompt = """
    You are a careful technical editor comparing two versions of one LaTeX paragraph from an academic paper: ORIGINAL and EDITED. Judge meaning, not style. Ignore whitespace and line-break differences.

    Reply with one JSON object and nothing else — no prose before or after, no code fences — using exactly these keys:
    {
      "verdict": "same" | "minor" | "changed",
      "summary": "one sentence answering whether the edited version says the same thing as the original",
      "changes": ["each concrete edit in plain language, quoting the words that changed"],
      "meaning_differences": ["claims, numbers, conditions, causal links, hedges, citations or references that were added, removed, strengthened or weakened; empty list if none"],
      "latex_issues": ["LaTeX commands, math, citations or references that were broken, dropped or altered; empty list if none"]
    }

    Verdict rules:
    - "same": every claim, quantity, condition, hedge and reference is preserved; only wording, grammar or punctuation changed.
    - "minor": emphasis, tone or hedging shifted slightly, but no fact was added or removed.
    - "changed": a claim, number, condition, causal link, citation or reference was added, removed or altered.
    """

    public static func compareUserPrompt(for req: CompareRequest) -> String {
        """
        ORIGINAL:
        <original>
        \(req.original)
        </original>

        EDITED:
        <edited>
        \(req.edited)
        </edited>

        Compare them and reply with the JSON object only.
        """
    }

    public func compare(_ req: CompareRequest) async throws -> SemanticComparison {
        guard !req.model.isEmpty else { throw LMStudioError.noModel }
        let body: [String: Any] = [
            "model": req.model,
            "messages": [
                ["role": "system", "content": LMStudioService.compareSystemPrompt],
                ["role": "user", "content": LMStudioService.compareUserPrompt(for: req)],
            ],
            "temperature": req.temperature,
            "max_tokens": 8192,
            "stream": false,
        ]
        let obj = try await sendChat(body)
        let choices = obj["choices"] as? [[String: Any]] ?? []
        let message = choices.first?["message"] as? [String: Any]
        var content = ""
        if let s = message?["content"] as? String {
            content = s
        } else if let parts = message?["content"] as? [[String: Any]] {
            content = parts.compactMap { $0["text"] as? String }.joined()
        }
        if content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if let reasoning = message?["reasoning_content"] as? String, !reasoning.isEmpty {
                throw LMStudioError.exhaustedThinking
            }
            throw LMStudioError.emptyResponse
        }
        return LMStudioService.parseComparison(content, model: obj["model"] as? String ?? req.model)
    }

    /// Lenient parser: strips reasoning/fences, takes the outermost {...} block,
    /// accepts strings where lists were expected. Falls back to the raw text.
    public static func parseComparison(_ raw: String, model: String) -> SemanticComparison {
        var text = raw
        if let re = try? NSRegularExpression(pattern: #"<think>[\s\S]*?</think>"#) {
            text = re.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "")
        }
        text = text.replacingOccurrences(of: "```json", with: "```")
            .replacingOccurrences(of: "```", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        func list(_ value: Any?) -> [String] {
            if let arr = value as? [Any] {
                return arr.compactMap { item -> String? in
                    if let s = item as? String { return s.trimmingCharacters(in: .whitespacesAndNewlines) }
                    if let d = item as? [String: Any] {
                        return d.values.compactMap { $0 as? String }.joined(separator: " — ")
                    }
                    return nil
                }.filter { !$0.isEmpty }
            }
            if let s = value as? String {
                let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
                return t.isEmpty || t.lowercased() == "none" ? [] : [t]
            }
            return []
        }

        if let open = text.firstIndex(of: "{"), let close = text.lastIndex(of: "}"), open < close {
            let json = String(text[open...close])
            if let data = json.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let v = (obj["verdict"] as? String ?? "").lowercased()
                let verdict: SemanticComparison.Verdict
                if v.contains("same") || v.contains("identical") || v.contains("preserv") { verdict = .same }
                else if v.contains("minor") || v.contains("slight") { verdict = .minor }
                else if v.contains("chang") || v.contains("differ") { verdict = .changed }
                else { verdict = .unknown }
                let summary = (obj["summary"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                return SemanticComparison(
                    verdict: verdict,
                    summary: summary.isEmpty ? (verdict == .unknown ? "The model did not give a verdict." : "") : summary,
                    changes: list(obj["changes"]),
                    meaningDifferences: list(obj["meaning_differences"] ?? obj["meaningDifferences"]),
                    latexIssues: list(obj["latex_issues"] ?? obj["latexIssues"]),
                    raw: raw, model: model)
            }
        }
        let short = text.count > 600 ? String(text.prefix(600)) + "…" : text
        return SemanticComparison(verdict: .unknown, summary: short, raw: raw, model: model)
    }
}

/// Deterministic checks that catch the most common ways an edit silently
/// changes meaning: dropped citations/references, altered numbers, lost math,
/// changed negation or hedging. Instant, no model involved.
public enum SemanticChecks {
    public struct Issue: Equatable, Identifiable {
        public enum Severity: Equatable { case error, warning, info }
        public let text: String
        public let severity: Severity
        public var id: String { text }
        public init(_ text: String, _ severity: Severity) {
            self.text = text
            self.severity = severity
        }
    }

    static let citeRE = try! NSRegularExpression(pattern: #"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}"#)
    static let refRE = try! NSRegularExpression(pattern: #"\\(?:ref|eqref|cref|Cref|autoref|pageref|vref|nameref)\*?\{([^}]*)\}"#)
    static let labelRE = try! NSRegularExpression(pattern: #"\\label\{([^}]*)\}"#)
    static let mathRE = try! NSRegularExpression(pattern: #"\$\$[\s\S]+?\$\$|\$[^$\n]+\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]"#)
    static let numberRE = try! NSRegularExpression(pattern: #"(?<![\w\\{])[-+]?\d[\d,]*(?:\.\d+)?%?"#)
    static let envRE = try! NSRegularExpression(pattern: #"\\begin\{([^}]*)\}"#)
    static let wordRE = try! NSRegularExpression(pattern: #"[A-Za-z']+"#)

    static let negations: Set<String> = ["not", "no", "never", "cannot", "can't", "won't", "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "without", "neither", "nor", "none", "nothing"]
    static let hedges: Set<String> = ["may", "might", "could", "likely", "unlikely", "possibly", "probably", "suggests", "suggest", "appears", "appear", "seems", "seem", "approximately", "roughly", "about", "typically", "often", "sometimes", "generally", "usually", "potentially", "arguably"]
    static let intensifiers: Set<String> = ["always", "all", "every", "never", "guarantees", "guarantee", "proves", "prove", "significantly", "substantially", "clearly", "certainly", "definitely", "must", "will", "only"]

    static func matches(_ re: NSRegularExpression, in s: String, group: Int = 1) -> [String] {
        let ns = s as NSString
        return re.matches(in: s, range: NSRange(location: 0, length: ns.length)).map { m in
            let r = group <= m.numberOfRanges - 1 && m.range(at: group).location != NSNotFound ? m.range(at: group) : m.range
            return ns.substring(with: r).trimmingCharacters(in: .whitespaces)
        }
    }

    static func keys(_ re: NSRegularExpression, in s: String) -> [String] {
        matches(re, in: s).flatMap { $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) } }.filter { !$0.isEmpty }
    }

    static func counted(_ items: [String]) -> [String: Int] {
        var out: [String: Int] = [:]
        for i in items { out[i, default: 0] += 1 }
        return out
    }

    static func words(_ s: String) -> [String] {
        matches(wordRE, in: s, group: 0).map { $0.lowercased() }
    }

    public static func integrity(original: String, edited: String) -> [Issue] {
        var issues: [Issue] = []

        func diffSets(_ label: String, _ a: [String], _ b: [String], severity: Issue.Severity) {
            let sa = Set(a), sb = Set(b)
            for k in sa.subtracting(sb).sorted() { issues.append(Issue("\(label) `\(k)` was removed", severity)) }
            for k in sb.subtracting(sa).sorted() { issues.append(Issue("\(label) `\(k)` was added", severity)) }
        }
        diffSets("Citation", keys(citeRE, in: original), keys(citeRE, in: edited), severity: .error)
        diffSets("Reference", keys(refRE, in: original), keys(refRE, in: edited), severity: .error)
        diffSets("Label", keys(labelRE, in: original), keys(labelRE, in: edited), severity: .error)
        diffSets("Environment", matches(envRE, in: original), matches(envRE, in: edited), severity: .error)

        // Math and numbers are compared as multisets so a repeated value counts.
        let mathA = counted(matches(mathRE, in: original, group: 0).map { $0.replacingOccurrences(of: " ", with: "") })
        let mathB = counted(matches(mathRE, in: edited, group: 0).map { $0.replacingOccurrences(of: " ", with: "") })
        for (m, n) in mathA where mathB[m, default: 0] < n { issues.append(Issue("Math \(m) was removed or altered", .error)) }
        for (m, n) in mathB where mathA[m, default: 0] < n { issues.append(Issue("Math \(m) was added or altered", .error)) }

        // Numbers inside math and command arguments are covered by the checks above.
        func prose(_ s: String) -> String {
            var t = s
            for re in [mathRE, citeRE, refRE, labelRE] {
                t = re.stringByReplacingMatches(in: t, range: NSRange(t.startIndex..., in: t), withTemplate: " ")
            }
            return t
        }
        let numA = counted(matches(numberRE, in: prose(original), group: 0))
        let numB = counted(matches(numberRE, in: prose(edited), group: 0))
        let numsRemoved = numA.filter { numB[$0.key, default: 0] < $0.value }.keys.sorted()
        let numsAdded = numB.filter { numA[$0.key, default: 0] < $0.value }.keys.sorted()
        if numsRemoved.count == 1 && numsAdded.count == 1 {
            issues.append(Issue("Number \(numsRemoved[0]) became \(numsAdded[0])", .error))
        } else {
            for n in numsRemoved { issues.append(Issue("Number \(n) is missing from the edit", .error)) }
            for n in numsAdded { issues.append(Issue("Number \(n) appears only in the edit", .error)) }
        }

        let wa = words(original), wb = words(edited)
        func countWords(_ set: Set<String>, _ ws: [String]) -> Int { ws.filter { set.contains($0) }.count }
        let negA = countWords(negations, wa), negB = countWords(negations, wb)
        if negA != negB { issues.append(Issue("Negation words changed: \(negA) → \(negB) (check the claim was not inverted)", .warning)) }
        let hedgeA = countWords(hedges, wa), hedgeB = countWords(hedges, wb)
        if hedgeA != hedgeB { issues.append(Issue("Hedging words changed: \(hedgeA) → \(hedgeB) (\(hedgeB < hedgeA ? "claims sound more certain" : "claims sound more tentative"))", .warning)) }
        let intA = countWords(intensifiers, wa), intB = countWords(intensifiers, wb)
        if intA != intB { issues.append(Issue("Absolute/intensifying words changed: \(intA) → \(intB)", .warning)) }

        let sentA = sentenceCount(original), sentB = sentenceCount(edited)
        if sentA != sentB { issues.append(Issue("Sentence count changed: \(sentA) → \(sentB)", .info)) }
        let lenA = wa.count, lenB = wb.count
        if lenA > 0 {
            let ratio = Double(lenB) / Double(lenA)
            if ratio < 0.7 { issues.append(Issue("Edit is \(Int((1 - ratio) * 100))% shorter — content may have been dropped", .warning)) }
            if ratio > 1.4 { issues.append(Issue("Edit is \(Int((ratio - 1) * 100))% longer — content may have been added", .warning)) }
        }
        return issues
    }

    static func sentenceCount(_ s: String) -> Int {
        // Strip LaTeX commands and math first so "Fig.~\ref{x}" or "3.5" don't count.
        var t = s
        for re in [mathRE, citeRE, refRE, labelRE] {
            t = re.stringByReplacingMatches(in: t, range: NSRange(t.startIndex..., in: t), withTemplate: " X ")
        }
        t = t.replacingOccurrences(of: #"\\[a-zA-Z@]+\*?"#, with: " ", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\b(?:e\.g|i\.e|etc|vs|cf|Fig|Eq|Sec|al)\."#, with: "X", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\d\.\d"#, with: "X", options: .regularExpression)
        let ends = t.components(separatedBy: CharacterSet(charactersIn: ".!?")).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return max(ends.count - (t.trimmingCharacters(in: .whitespacesAndNewlines).last.map { ".!?".contains($0) } == true ? 0 : 1), 0) + (t.trimmingCharacters(in: .whitespacesAndNewlines).last.map { ".!?".contains($0) } == true ? 0 : 1)
    }
}

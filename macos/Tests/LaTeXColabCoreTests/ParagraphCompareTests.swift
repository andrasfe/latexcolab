import XCTest
@testable import LaTeXColabCore

final class ParagraphCompareTests: XCTestCase {
    func testParseWellFormedJSON() {
        let raw = """
        {"verdict": "minor", "summary": "Mostly the same.", "changes": ["'shows' → 'show'"],
         "meaning_differences": [], "latex_issues": ["\\\\cite{x} dropped"]}
        """
        let c = LMStudioService.parseComparison(raw, model: "m")
        XCTAssertEqual(c.verdict, .minor)
        XCTAssertEqual(c.summary, "Mostly the same.")
        XCTAssertEqual(c.changes, ["'shows' → 'show'"])
        XCTAssertEqual(c.meaningDifferences, [])
        XCTAssertEqual(c.latexIssues, ["\\cite{x} dropped"])
        XCTAssertEqual(c.model, "m")
    }

    func testParseToleratesThinkingFencesAndStrings() {
        let raw = """
        <think>let me look</think>
        Here you go:
        ```json
        {"verdict": "Same meaning", "summary": "Identical claims.", "changes": "Only punctuation.", "meaning_differences": "none"}
        ```
        """
        let c = LMStudioService.parseComparison(raw, model: "m")
        XCTAssertEqual(c.verdict, .same)
        XCTAssertEqual(c.changes, ["Only punctuation."])
        XCTAssertEqual(c.meaningDifferences, [])
        XCTAssertEqual(c.headline, "Says the same thing")
    }

    func testParseFallsBackToRawText() {
        let c = LMStudioService.parseComparison("I think the second one is stronger.", model: "m")
        XCTAssertEqual(c.verdict, .unknown)
        XCTAssertEqual(c.summary, "I think the second one is stronger.")
    }

    func testPromptContainsBothSides() {
        let p = LMStudioService.compareUserPrompt(for: CompareRequest(original: "A", edited: "B", model: "m"))
        XCTAssertTrue(p.contains("<original>\nA\n</original>"))
        XCTAssertTrue(p.contains("<edited>\nB\n</edited>"))
        XCTAssertTrue(LMStudioService.compareSystemPrompt.contains("\"verdict\""))
    }

    func testIntegrityCatchesDroppedCitationRefNumberAndMath() {
        let original = "We show a 34\\% gain over the baseline~\\cite{smith2020,jones21} (see \\cref{sec:method}, $x^2$). It may help."
        let edited = "We show a 43\\% gain over the baseline~\\cite{smith2020} (see \\cref{sec:method}). It helps."
        let issues = SemanticChecks.integrity(original: original, edited: edited)
        let texts = issues.map(\.text)
        XCTAssertTrue(texts.contains("Citation `jones21` was removed"), "\(texts)")
        XCTAssertTrue(texts.contains("Number 34 became 43"), "\(texts)")
        XCTAssertTrue(texts.contains { $0.hasPrefix("Math $x^2$ was removed") }, "\(texts)")
        XCTAssertTrue(texts.contains { $0.hasPrefix("Hedging words changed: 1 → 0") }, "\(texts)")
        XCTAssertFalse(texts.contains { $0.hasPrefix("Reference") }, "\(texts)")
    }

    func testIntegrityQuietForCosmeticEdit() {
        let original = "In this paper we shows that the method , described in \\cref{sec:m} , outperform the baseline."
        let edited = "In this paper we show that the method, described in \\cref{sec:m}, outperforms the baseline."
        XCTAssertEqual(SemanticChecks.integrity(original: original, edited: edited), [])
    }

    func testIntegrityFlagsNegationAndLength() {
        let issues = SemanticChecks.integrity(original: "The method does not fail on large inputs and was tested extensively on three datasets.", edited: "The method fails.")
        let texts = issues.map(\.text)
        XCTAssertTrue(texts.contains { $0.hasPrefix("Negation words changed: 1 → 0") }, "\(texts)")
        XCTAssertTrue(texts.contains { $0.contains("shorter") }, "\(texts)")
    }
}

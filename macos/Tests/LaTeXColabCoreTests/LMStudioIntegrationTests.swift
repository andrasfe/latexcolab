import XCTest
@testable import LaTeXColabCore

/// Talks to a running LM Studio server. Skipped when the server is down or no
/// chat model is loaded (loading one on demand can take minutes).
final class LMStudioIntegrationTests: XCTestCase {
    func testRealRewrite() async throws {
        let service = LMStudioService(baseURL: LMStudioService.defaultBaseURL)
        let models: [LMModel]
        do { models = try await service.listModels() } catch { throw XCTSkip("LM Studio not reachable: \(error)") }
        guard let model = models.first(where: { $0.isChatModel && $0.isLoaded })?.id else {
            throw XCTSkip("no chat model loaded in LM Studio")
        }
        let paragraph = "In this paper we shows that the proposed method , which is described in \\cref{sec:method} , outperform the baseline by a large margin (see \\cite{smith2020})."
        let req = CleanupRequest(paragraph: paragraph, maxWords: 5, instructions: "Keep it formal.", model: model, temperature: 0.1)
        let result = try await service.cleanUp(req)
        print("LM Studio (\(model)) →", result.text)
        XCTAssertFalse(result.text.isEmpty)
        XCTAssertTrue(result.text.contains("\\cref{sec:method}"), "citation/reference must survive: \(result.text)")
        XCTAssertTrue(result.text.contains("\\cite{smith2020}"), "citation must survive: \(result.text)")
        XCTAssertFalse(result.text.contains("```"))
        XCTAssertFalse(result.text.contains("<think>"))
        let raw = EditBudget.measure(original: paragraph, new: result.text)
        let budget = EditBudget.constrain(original: paragraph, rewrite: result.text, maxWords: req.maxWords)
        print("model reworded \(raw.wordChanges) words, \(raw.freeFixes) free fixes → kept \(budget.wordsUsed), held back \(budget.dropped.map(\.label))")
        XCTAssertLessThanOrEqual(budget.wordsUsed, req.maxWords)
        XCTAssertTrue(budget.text.contains("\\cref{sec:method}"))
        XCTAssertTrue(budget.text.contains("\\cite{smith2020}"))

        // Semantic comparison on a deliberately altered edit.
        let altered = "In this paper we show that the proposed method, described in \\cref{sec:method}, matches the baseline (see \\cite{smith2020})."
        let cmp = try await service.compare(CompareRequest(original: paragraph, edited: altered, model: model))
        print("compare →", cmp.verdict, cmp.summary, cmp.changes, cmp.meaningDifferences)
        XCTAssertNotEqual(cmp.verdict, .unknown, cmp.raw)
        XCTAssertNotEqual(cmp.verdict, .same, "'outperform by a large margin' → 'matches' should not be judged identical: \(cmp.raw)")
        XCTAssertFalse(cmp.summary.isEmpty)
    }
}

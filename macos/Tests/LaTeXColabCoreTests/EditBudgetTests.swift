import XCTest
@testable import LaTeXColabCore

final class EditBudgetTests: XCTestCase {
    let original = "In this paper we shows that the proposed method , which is described in \\cref{sec:method} , outperform the baseline by a large margin ( see \\cite{smith2020} ) ."

    func testFreeFixesAlwaysKept() {
        let rewrite = "In this paper we show that the proposed method, which is described in \\cref{sec:method}, outperforms the baseline by a large margin (see \\cite{smith2020})."
        let r = EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 0)
        XCTAssertEqual(r.text, rewrite)
        XCTAssertEqual(r.dropped, [])
        XCTAssertEqual(r.wordsUsed, 0)
        XCTAssertGreaterThan(r.freeFixes, 0)
    }

    func testRewordingsBeyondBudgetAreReverted() {
        let rewrite = "In this paper we demonstrate that the proposed approach, which is described in \\cref{sec:method}, outperforms the baseline by a large margin (see \\cite{smith2020})."
        // "shows"→"demonstrate" (1 word), "method"→"approach" (1 word), plus free fixes.
        let one = EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 1)
        XCTAssertEqual(one.wordsUsed, 1)
        XCTAssertEqual(one.dropped.count, 1)
        XCTAssertTrue(one.text.contains("described in \\cref{sec:method}, outperforms"), one.text)
        XCTAssertTrue(one.text.contains("demonstrate") != one.text.contains("approach"), "exactly one rewording kept: \(one.text)")

        let zero = EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 0)
        XCTAssertEqual(zero.wordsUsed, 0)
        // "method ," → "approach," entangles the space fix with the rewording, so the
        // whole hunk is reverted; the independent punctuation fixes are still applied.
        XCTAssertTrue(zero.text.contains("we shows that the proposed method , which"), zero.text)
        XCTAssertTrue(zero.text.contains("\\cref{sec:method}, outperforms the baseline by a large margin (see \\cite{smith2020})."), zero.text)
        XCTAssertEqual(zero.dropped.map(\.label), ["“shows” → “demonstrate”", "“method” → “approach”"])
        XCTAssertEqual(zero.dropped.map(\.cost), [1, 1])

        let all = EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 5)
        XCTAssertEqual(all.text, rewrite)
        XCTAssertEqual(all.wordsUsed, 2)
    }

    func testWholeSentenceInsertionCountsEveryWord() {
        let rewrite = original + " This is an entirely new sentence added by the model."
        let r = EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 5)
        XCTAssertEqual(r.text, original)
        XCTAssertEqual(r.dropped.count, 1)
        XCTAssertEqual(r.dropped[0].cost, 10)
        XCTAssertTrue(r.dropped[0].label.hasPrefix("insert “This is an entirely"))
        XCTAssertEqual(EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 10).text, rewrite)
    }

    func testSmallestRewordingsFirst() {
        let orig = "alpha beta gamma delta epsilon zeta"
        let rewrite = "one two gamma delta three zeta"
        // hunk A: "alpha beta"→"one two" (cost 2); hunk B: "epsilon"→"three" (cost 1)
        let r = EditBudget.constrain(original: orig, rewrite: rewrite, maxWords: 2)
        XCTAssertEqual(r.text, "alpha beta gamma delta three zeta")
        XCTAssertEqual(r.wordsUsed, 1)
    }

    func testNegationIsNeverAFreeFix() {
        let r = EditBudget.constrain(original: "It is not fast.", rewrite: "It is now fast.", maxWords: 0)
        XCTAssertEqual(r.text, "It is not fast.")
        XCTAssertEqual(r.dropped.count, 1)
    }

    func testTyposAndInflectionsAreFree() {
        let r = EditBudget.constrain(original: "we recieve the datas and it show", rewrite: "we receive the data and it shows", maxWords: 0)
        XCTAssertEqual(r.text, "we receive the data and it shows")
        XCTAssertEqual(r.wordsUsed, 0)
    }

    func testAuthorsLineBreaksSurviveReflow() {
        let orig = "first line of text\nsecond line of text"
        let rewrite = "first line of text second line of text"
        let r = EditBudget.constrain(original: orig, rewrite: rewrite, maxWords: 10)
        XCTAssertEqual(r.text, orig)
    }

    func testMeasureAndManualApply() {
        let m = EditBudget.measure(original: original, new: "In this paper we demonstrate that the proposed method, which is described in \\cref{sec:method}, outperform the baseline by a large margin (see \\cite{smith2020}).")
        XCTAssertEqual(m.wordChanges, 1)
        XCTAssertEqual(m.rewordings, 1)
        XCTAssertGreaterThan(m.freeFixes, 0)
        let s = EditSuggestion(id: 0, from: "shows", to: "demonstrate", cost: 1)
        XCTAssertEqual(EditBudget.apply(s, to: "we shows it"), "we demonstrate it")
        XCTAssertNil(EditBudget.apply(s, to: "nothing here"))
    }
}

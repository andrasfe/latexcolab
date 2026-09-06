import XCTest
@testable import LaTeXColabCore

final class ProvenanceTests: XCTestCase {
    func sub(_ s: String, _ ranges: [NSRange]) -> [String] { ranges.map { (s as NSString).substring(with: $0) } }

    func testAIInsertionsAreMarkedAndSurviveTyping() {
        let original = "We shows that the method , works ."
        let ai = "We show that the method, works."
        var p = Provenance().remapped(from: original, to: ai, insertedByAI: true)
        XCTAssertEqual(sub(ai, p.ranges), ["show", ",", "."])

        // The user then types a word: the AI marks move with the text.
        let typed = "We show that the new method, works."
        p = p.remapped(from: ai, to: typed, insertedByAI: false)
        XCTAssertEqual(sub(typed, p.ranges), ["show", ",", "."])

        let h = p.highlights(original: original, draft: typed)
        XCTAssertEqual(sub(typed, h.aiInDraft), ["show", ",", "."])
        XCTAssertEqual(sub(typed, h.userInDraft), ["new"])
        XCTAssertEqual(sub(original, h.aiInOriginal), ["shows", ",", "."])
        XCTAssertEqual(h.userInOriginal, [])
        XCTAssertEqual(h.aiWords, 1)
        XCTAssertEqual(h.aiPunctuation, 2)
        XCTAssertEqual(h.userWords, 1)
    }

    func testPureAIDeletionMarksDocumentWords() {
        let original = "This is very very important."
        let ai = "This is very important."
        let p = Provenance().remapped(from: original, to: ai, insertedByAI: true)
        XCTAssertEqual(p.aiDeletions, ["very"])
        let h = p.highlights(original: original, draft: ai)
        XCTAssertEqual(sub(original, h.aiInOriginal), ["very"])
        XCTAssertEqual(h.aiInDraft, [])
    }

    func testUserEditsStayUserAndRevertClearsMarks() {
        let original = "alpha beta gamma"
        let userDraft = "alpha delta gamma"
        var p = Provenance().remapped(from: original, to: userDraft, insertedByAI: false)
        XCTAssertTrue(p.isEmpty)
        let h = p.highlights(original: original, draft: userDraft)
        XCTAssertEqual(sub(userDraft, h.userInDraft), ["delta"])
        XCTAssertEqual(sub(original, h.userInOriginal), ["beta"])
        // AI then changes gamma → omega; user reverts everything.
        let ai = "alpha delta omega"
        p = p.remapped(from: userDraft, to: ai, insertedByAI: true)
        XCTAssertEqual(sub(ai, p.ranges), ["omega"])
        p = p.remapped(from: ai, to: original, insertedByAI: false)
        XCTAssertEqual(p.ranges, [])
    }

    func testAfterApplySharedAIWordsShowInBothPanes() {
        let original = "We shows results."
        let ai = "We show results."
        let p = Provenance().remapped(from: original, to: ai, insertedByAI: true)
        // After Apply the document equals the draft.
        let h = p.highlights(original: ai, draft: ai)
        XCTAssertEqual(sub(ai, h.aiInDraft), ["show"])
        XCTAssertEqual(sub(ai, h.aiInOriginal), ["show"])
    }

    func testCodableRoundTrip() throws {
        let p = Provenance(aiRanges: [NSRange(location: 3, length: 4)], aiDeletions: ["very"])
        let data = try JSONEncoder().encode(p)
        let back = try JSONDecoder().decode(Provenance.self, from: data)
        XCTAssertEqual(back, p)
        XCTAssertEqual(back.ranges, [NSRange(location: 3, length: 4)])
    }
}

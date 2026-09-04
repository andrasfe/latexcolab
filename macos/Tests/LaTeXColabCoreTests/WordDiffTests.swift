import XCTest
@testable import LaTeXColabCore

final class WordDiffTests: XCTestCase {
    func testIdentical() {
        let segs = WordDiff.diff(old: "The cat sat.", new: "The cat sat.")
        XCTAssertTrue(segs.allSatisfy { $0.kind == .equal })
        XCTAssertEqual(WordDiff.changedWordCount(segs), 0)
    }

    func testSubstitutionCountsOnce() {
        let segs = WordDiff.diff(old: "The cat sat on the mat.", new: "The dog sat on the rug.")
        XCTAssertEqual(WordDiff.changedWordCount(segs), 2)
        let ranges = WordDiff.highlightRanges(segs)
        XCTAssertEqual(ranges.new.count, 2)
        XCTAssertEqual(ranges.old.count, 2)
        let new = "The dog sat on the rug." as NSString
        XCTAssertEqual(new.substring(with: ranges.new[0]), "dog")
        XCTAssertEqual(new.substring(with: ranges.new[1]), "rug")
    }

    func testWhitespaceReflowIsNotAChange() {
        let segs = WordDiff.diff(old: "one two\nthree four", new: "one two three\nfour")
        XCTAssertEqual(WordDiff.changedWordCount(segs), 0)
    }

    func testPunctuationAndCommandsAreNotWords() {
        let segs = WordDiff.diff(old: "Hello world", new: "Hello, \\emph{world}!")
        XCTAssertEqual(WordDiff.changedWordCount(segs), 0)
        XCTAssertTrue(segs.contains { $0.kind == .inserted && $0.text == "\\emph" })
    }

    func testInsertionAndDeletion() {
        XCTAssertEqual(WordDiff.changedWordCount(WordDiff.diff(old: "a b c", new: "a b c d e")), 2)
        XCTAssertEqual(WordDiff.changedWordCount(WordDiff.diff(old: "a b c d e", new: "a e")), 3)
        XCTAssertEqual(WordDiff.changedWordCount(WordDiff.diff(old: "", new: "x y")), 2)
    }
}

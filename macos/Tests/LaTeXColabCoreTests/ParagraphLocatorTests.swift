import XCTest
@testable import LaTeXColabCore

final class ParagraphLocatorTests: XCTestCase {
    let source = """
    \\documentclass{article}
    \\begin{document}

    \\section{Intro}
    First paragraph line one
    line two.

    Second paragraph.

    \\end{document}
    """

    func testParagraphContainingLine() {
        let p = ParagraphLocator.paragraph(in: source, containingLine: 5)
        XCTAssertEqual(p?.startLine, 4)
        XCTAssertEqual(p?.endLine, 6)
        XCTAssertEqual(p?.text, "\\section{Intro}\nFirst paragraph line one\nline two.")
    }

    func testBlankLinePrefersParagraphAbove() {
        // SyncTeX reports a paragraph's boxes on the blank line that ends it.
        let p = ParagraphLocator.paragraph(in: source, containingLine: 7)
        XCTAssertEqual(p?.startLine, 4)
        XCTAssertEqual(p?.endLine, 6)
    }

    func testLeadingBlankLineFallsForward() {
        let p = ParagraphLocator.paragraph(in: "\n\nHello\nworld", containingLine: 1)
        XCTAssertEqual(p?.startLine, 3)
        XCTAssertEqual(p?.text, "Hello\nworld")
    }

    func testAllParagraphs() {
        let ps = ParagraphLocator.paragraphs(in: source)
        XCTAssertEqual(ps.map(\.startLine), [1, 4, 8, 10])
        XCTAssertEqual(ps[2].text, "Second paragraph.")
    }

    func testReplacingChangesLineCount() {
        let p = ParagraphLocator.paragraph(in: source, containingLine: 8)!
        let out = ParagraphLocator.replacing(p, in: source, with: "Second\nparagraph\nrewritten.")
        XCTAssertTrue(out.contains("Second\nparagraph\nrewritten.\n\n\\end{document}"))
        XCTAssertEqual(ParagraphLocator.lines(of: out).count, ParagraphLocator.lines(of: source).count + 2)
    }

    func testLocateAfterShift() {
        let p = ParagraphLocator.paragraph(in: source, containingLine: 8)!
        let shifted = "% new comment\n\n" + source
        let found = ParagraphLocator.locate(expected: p.text, near: p, in: shifted)
        XCTAssertEqual(found?.startLine, 10)
        XCTAssertNil(ParagraphLocator.locate(expected: "not there", near: p, in: shifted))
    }
}

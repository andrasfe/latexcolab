import XCTest
@testable import LaTeXColab
@testable import LaTeXColabCore

/// Drives AppModel directly on a scratch project: sentence and selection
/// sessions, Apply of partial spans, history, and re-opening.
@MainActor
final class PartialApplyTests: XCTestCase {
    var tmp: URL!

    override func setUp() async throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("partial-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp.appendingPathComponent("sections"), withIntermediateDirectories: true)
        let tex = """
        \\documentclass{article}
        \\begin{document}

        \\section{Introduction}
        This is the first paragraph of the introduction. It spans
        multiple source lines so that we can check how \\emph{SyncTeX reports}
        line numbers for words in the middle of a paragraph.

        This is the second paragraph. It is short.

        \\end{document}
        """
        try tex.write(to: tmp.appendingPathComponent("main.tex"), atomically: true, encoding: .utf8)
        setenv("LATEXCOLAB_PROJECT", tmp.path, 1)
    }

    override func tearDown() async throws {
        unsetenv("LATEXCOLAB_PROJECT")
        try? FileManager.default.removeItem(at: tmp)
    }

    private func source() throws -> String {
        try String(contentsOf: tmp.appendingPathComponent("main.tex"), encoding: .utf8)
    }

    func testSentenceSessionApplyAndHistory() throws {
        let model = AppModel()
        model.config.autoRegenerateAfterApply = false
        model.structuralChangeConfirmer = { _, _ in false }
        XCTAssertEqual(model.projectURL?.path, tmp.path)

        model.openParagraph(file: "main.tex", line: 5, scope: .sentence)
        let s = try XCTUnwrap(model.paragraphSession)
        XCTAssertEqual(s.scope, .sentence)
        XCTAssertEqual(s.original, "This is the first paragraph of the introduction.")
        XCTAssertTrue(s.container.hasPrefix("\\section{Introduction}"))
        XCTAssertEqual(s.context().after.prefix(9), " It spans")

        s.draft = "This is the FIRST paragraph of the introduction."
        XCTAssertTrue(model.applyParagraph(s))
        let src = try source()
        XCTAssertTrue(src.contains("\\section{Introduction}\nThis is the FIRST paragraph of the introduction. It spans\nmultiple"), src)
        XCTAssertEqual(s.history.map(\.text), ["This is the first paragraph of the introduction."])
        XCTAssertTrue(s.isInSync)
        XCTAssertEqual(s.range.startLine, 4)
        XCTAssertEqual(s.range.endLine, 7)

        // Re-opening the same sentence finds the record (partial) with its history.
        model.paragraphSession = nil
        model.openParagraph(file: "main.tex", line: 5, scope: .sentence)
        let again = try XCTUnwrap(model.paragraphSession)
        XCTAssertEqual(again.original, "This is the FIRST paragraph of the introduction.")
        XCTAssertEqual(again.history.count, 1)
        XCTAssertTrue(again.applied)

        // The whole-paragraph session is separate and unaffected by the partial record.
        model.paragraphSession = nil
        model.openParagraph(file: "main.tex", line: 5)
        let whole = try XCTUnwrap(model.paragraphSession)
        XCTAssertEqual(whole.scope, .paragraph)
        XCTAssertEqual(whole.history.count, 0)
        XCTAssertTrue(whole.original.hasPrefix("\\section{Introduction}\nThis is the FIRST"))
    }

    func testSelectionSessionApplyKeepsBracesAndRest() throws {
        let model = AppModel()
        model.config.autoRegenerateAfterApply = false
        model.structuralChangeConfirmer = { _, _ in false }
        let src0 = try source()
        let para = try XCTUnwrap(ParagraphLocator.paragraph(in: src0, containingLine: 6))
        // What a PDF selection of "SyncTeX reports line numbers" maps to: brace-balanced span.
        let span = try XCTUnwrap(SelectionMapper.sourceRange(forPDFText: "SyncTeX reports line numbers", in: para.text))
        XCTAssertEqual((para.text as NSString).substring(with: span), "\\emph{SyncTeX reports}\nline numbers")
        model.openParagraphSession(file: "main.tex", range: para, span: span, scope: .selection)
        let s = try XCTUnwrap(model.paragraphSession)
        XCTAssertEqual(s.scope, .selection)
        s.draft = "\\emph{SyncTeX records}\nline numbers"
        XCTAssertTrue(model.applyParagraph(s))
        let src = try source()
        XCTAssertTrue(src.contains("how \\emph{SyncTeX records}\nline numbers for words"), src)
        XCTAssertTrue(src.contains("This is the second paragraph. It is short."))
        XCTAssertEqual(ParagraphLocator.lines(of: src).count, ParagraphLocator.lines(of: src0).count)

        // The structure guard that Apply consults for a partial span.
        XCTAssertEqual(LaTeXStructure.differences(original: s.original, edited: "SyncTeX records line numbers"), [])
        XCTAssertEqual(LaTeXStructure.differences(original: s.original, edited: "\\emph{SyncTeX records line numbers"), ["leaves 1 more { than } unclosed"])
    }
}

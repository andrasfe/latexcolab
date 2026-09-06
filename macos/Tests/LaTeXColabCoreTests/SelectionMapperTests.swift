import XCTest
@testable import LaTeXColabCore

final class SelectionMapperTests: XCTestCase {
    let para = """
    Every witness is source-certiﬁed and peer-matched against generated Java under
    the declared projection~\\cite{smith2020}, including \\emph{byte-exact} final
    output files (213/213). Post-study local search covers 2,860/3,806 source
    branch outcomes (75.1\\%); see Fig.~\\ref{fig:x} for details. The study
    demonstrates feasible evidence.
    """

    func sub(_ r: NSRange?) -> String? { r.map { (para as NSString).substring(with: $0) } }

    func testMapsPDFSelectionWithLigatureHyphenationAndCitation() {
        let pdf = "source-certified and peer-matched against gen-\nerated Java under the declared projection [3], including byte-exact final output files (213/213)."
        let r = SelectionMapper.sourceRange(forPDFText: pdf, in: para)
        XCTAssertEqual(sub(r), "source-certiﬁed and peer-matched against generated Java under\nthe declared projection~\\cite{smith2020}, including \\emph{byte-exact} final\noutput files (213/213).")
    }

    func testSelectionStartingInsideEmphIsBraceBalanced() {
        let r = SelectionMapper.sourceRange(forPDFText: "byte-exact final output", in: para)
        XCTAssertEqual(sub(r), "\\emph{byte-exact} final\noutput")
    }

    func testRejectsUnrelatedText() {
        XCTAssertNil(SelectionMapper.sourceRange(forPDFText: "completely different words about cats", in: para))
        XCTAssertNil(SelectionMapper.sourceRange(forPDFText: "", in: para))
    }

    func testSentenceBoundariesRespectAbbreviationsMathAndBlankLines() {
        let text = "First sentence, e.g. with an abbreviation and $x = 3.5$ inside. Second one here! Third\nstarts here.\n\nNew paragraph sentence."
        let ns = text as NSString
        func sentence(at needle: String) -> String {
            ns.substring(with: SelectionMapper.sentenceRange(in: text, containing: ns.range(of: needle).location))
        }
        XCTAssertEqual(sentence(at: "abbreviation"), "First sentence, e.g. with an abbreviation and $x = 3.5$ inside.")
        XCTAssertEqual(sentence(at: "Second"), "Second one here!")
        XCTAssertEqual(sentence(at: "starts"), "Third\nstarts here.")
        XCTAssertEqual(sentence(at: "New"), "New paragraph sentence.")
    }

    func testHeadingLinesBoundSentences() {
        let text = "\\section{Introduction}\nThis is the first paragraph. It spans lines.\n\\label{sec:intro}\nAnother sentence"
        let ns = text as NSString
        XCTAssertEqual(ns.substring(with: SelectionMapper.sentenceRange(in: text, containing: ns.range(of: "first").location)), "This is the first paragraph.")
        XCTAssertEqual(ns.substring(with: SelectionMapper.sentenceRange(in: text, containing: ns.range(of: "spans").location)), "It spans lines.")
        XCTAssertEqual(ns.substring(with: SelectionMapper.sentenceRange(in: text, containing: ns.range(of: "Another").location)), "Another sentence")
        XCTAssertEqual(ns.substring(with: SelectionMapper.sentenceRange(in: text, containing: 3)), "\\section{Introduction}")
    }

    func testSentenceFromPDFWordAndLineOffset() {
        let ns = para as NSString
        let offsetLine4 = SelectionMapper.offset(ofLine: 4, in: para)
        let r = SelectionMapper.sentenceRange(in: para, containingPDFWord: "outcomes", nearOffset: offsetLine4)
        XCTAssertEqual(r.map { ns.substring(with: $0) }, "Post-study local search covers 2,860/3,806 source\nbranch outcomes (75.1\\%); see Fig.~\\ref{fig:x} for details.")
        let last = SelectionMapper.sentenceRange(in: para, containingPDFWord: "demonstrates", nearOffset: ns.length)
        XCTAssertEqual(last.map { ns.substring(with: $0) }, "The study\ndemonstrates feasible evidence.")
        XCTAssertNil(SelectionMapper.sentenceRange(in: para, containingPDFWord: "zzz", nearOffset: 0))
    }

    func testTrailingPunctuationFollowsPDFText() {
        let r = SelectionMapper.sourceRange(forPDFText: "feasible evidence.", in: para)
        XCTAssertEqual(sub(r), "feasible evidence.")
        let noDot = SelectionMapper.sourceRange(forPDFText: "feasible evidence", in: para)
        XCTAssertEqual(sub(noDot), "feasible evidence")
    }
}

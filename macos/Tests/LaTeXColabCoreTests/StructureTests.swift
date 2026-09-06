import XCTest
@testable import LaTeXColabCore

final class StructureTests: XCTestCase {
    func testDifferencesDetectAddedEnd() {
        let original = "Producing synthetic data is a challenge. Our prior paper \\emph{X} did Y."
        let edited = original + "\n\\end{abstract}"
        XCTAssertEqual(LaTeXStructure.differences(original: original, edited: edited), ["adds \\end{abstract}"])
        XCTAssertEqual(LaTeXStructure.differences(original: original, edited: original.replacingOccurrences(of: "challenge", with: "problem")), [])
    }

    func testDifferencesBracesSectioningMath() {
        XCTAssertEqual(LaTeXStructure.differences(original: "a \\textbf{b} c", edited: "a \\textbf{b c"), ["leaves 1 more { than } unclosed"])
        XCTAssertEqual(LaTeXStructure.differences(original: "a b", edited: "\\section{New} a b"), ["adds a sectioning command"])
        XCTAssertEqual(LaTeXStructure.differences(original: "$x$ and $y$", edited: "$x$ and y$"), ["changes the number of $ math delimiters to an odd count"])
        XCTAssertEqual(LaTeXStructure.differences(original: "\\begin{itemize}\\item a\\end{itemize}", edited: "\\begin{itemize}\\item a"), ["removes \\end{itemize}"])
        // Comments and escaped braces are ignored.
        XCTAssertEqual(LaTeXStructure.differences(original: "50\\% done % \\end{x}", edited: "50\\% done"), [])
    }

    func testBudgetNeverAppliesStructuralChanges() {
        let original = "Producing synthetic data is a challenge that migrations face today."
        let rewrite = "Producing synthetic data is a challenge that migrations face today.\n\\end{abstract}"
        let r = EditBudget.constrain(original: original, rewrite: rewrite, maxWords: 50)
        XCTAssertEqual(r.text, original)
        XCTAssertEqual(r.dropped.count, 1)
        XCTAssertTrue(r.dropped[0].isStructural)
        XCTAssertTrue(r.dropped[0].label.hasPrefix("structural change: insert"), r.dropped[0].label)
        // A dropped citation is structural too, even though it costs no words.
        let cite = EditBudget.constrain(original: "as shown~\\cite{smith}.", rewrite: "as shown.", maxWords: 50)
        XCTAssertEqual(cite.text, "as shown~\\cite{smith}.")
        XCTAssertTrue(cite.dropped.first?.isStructural ?? false)
        // Harmless command fixes stay free.
        let free = EditBudget.constrain(original: "10 \\% of cases", rewrite: "10\\,\\% of cases", maxWords: 0)
        XCTAssertEqual(free.text, "10\\,\\% of cases")
    }

    func testEnginePreferenceAcrossDirectories() {
        let dirs = ["/opt/homebrew/bin", "/Users/me/Library/TinyTeX/bin/universal-darwin"]
        let existing: Set<String> = ["/opt/homebrew/bin/tectonic", "/Users/me/Library/TinyTeX/bin/universal-darwin/latexmk",
                                     "/Users/me/Library/TinyTeX/bin/universal-darwin/pdflatex"]
        let pick = EngineFinder.pick(from: dirs, exists: { existing.contains($0) })
        XCTAssertEqual(pick, "/Users/me/Library/TinyTeX/bin/universal-darwin/latexmk")
        XCTAssertEqual(EngineFinder.pick(from: dirs, preference: ["tectonic"], exists: { existing.contains($0) }), "/opt/homebrew/bin/tectonic")
        XCTAssertNil(EngineFinder.pick(from: dirs, preference: ["lualatex"], exists: { existing.contains($0) }))
    }

    func testFirstErrorExtraction() {
        XCTAssertEqual(MissingFileDetector.firstError(in: "blah\nmain.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}.\nmore"),
                       "main.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}.")
        XCTAssertEqual(MissingFileDetector.firstError(in: "! Undefined control sequence.\nl.5 \\foo"), "Undefined control sequence.")
        XCTAssertEqual(MissingFileDetector.firstError(in: "error: main.tex:61: LaTeX Error: x"), "main.tex:61: LaTeX Error: x")
        XCTAssertNil(MissingFileDetector.firstError(in: "Output written on main.pdf"))
        // Multi-pass log: the error of the last run wins.
        let twoPasses = "! LaTeX Error: File `microtype.sty' not found.\n--- retrying compile ---\nmain.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}.\n"
        XCTAssertEqual(MissingFileDetector.firstError(in: twoPasses), "main.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}.")
    }

    func testTlmgrAndEnvironmentFailureDetection() {
        let refusal = """
        tlmgr: Local TeX Live (2025) is older than remote repository (2026).
        Cross release updates are only supported with
          update-tlmgr-latest(.sh/.exe) --update
        tlmgr: Terminating; please see warning above!
        """
        XCTAssertTrue(MissingFileDetector.tlmgrNeedsSelfUpdate(refusal))
        XCTAssertTrue(MissingFileDetector.tlmgrFailed(refusal))
        XCTAssertFalse(MissingFileDetector.tlmgrNeedsSelfUpdate("tlmgr: package repository https://x\n[1/1] install: microtype [50k]\nrunning mktexlsr ...\ndone."))
        XCTAssertTrue(LaTeXCompiler.isEnvironmentFailure("! LaTeX Error: File `microtype.sty' not found."))
        XCTAssertTrue(LaTeXCompiler.isEnvironmentFailure("! Fatal Package fontspec Error: The fontspec package requires either XeTeX or LuaTeX."))
        XCTAssertFalse(LaTeXCompiler.isEnvironmentFailure("main.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}."))
        XCTAssertFalse(LaTeXCompiler.isEnvironmentFailure("! Missing $ inserted."))
    }
}

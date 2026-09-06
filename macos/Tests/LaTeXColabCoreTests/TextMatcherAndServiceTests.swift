import XCTest
@testable import LaTeXColabCore

final class TextMatcherTests: XCTestCase {
    func testStripsLatex() {
        let t = TextMatcher.tokens(latex: "We show \\emph{strong} results~\\cite{foo} in $x^2$. % comment")
        XCTAssertEqual(t, ["we", "show", "strong", "results", "in"])
    }

    func testBestMatchFindsParagraph() {
        let main = """
        \\section{Intro}
        Deep networks are hard to train without normalisation layers.

        We propose a simple trick that removes the need for warm-up entirely.
        """
        let other = "Unrelated text about cats and dogs.\n\nMore about cats."
        let m = TextMatcher.bestMatch(pdfText: "We propose a simple trick that re-\nmoves the need for warm-up", sources: [("main.tex", main), ("o.tex", other)])
        XCTAssertEqual(m?.file, "main.tex")
        XCTAssertEqual(m?.paragraph.startLine, 4)
        XCTAssertNil(TextMatcher.bestMatch(pdfText: "completely different words here", sources: [("main.tex", main)]))
    }
}

final class LMStudioServiceTests: XCTestCase {
    func testPromptMentionsLimitAndInstructions() {
        let p = LMStudioService.userPrompt(for: CleanupRequest(paragraph: "Hi", maxWords: 7, instructions: "no em dashes", model: "m"))
        XCTAssertTrue(p.contains("at most 7 words"))
        XCTAssertTrue(p.contains("no em dashes"))
        XCTAssertTrue(p.contains("<paragraph>\nHi\n</paragraph>"))
        let zero = LMStudioService.userPrompt(for: CleanupRequest(paragraph: "Hi", maxWords: 0, instructions: "", model: "m"))
        XCTAssertTrue(zero.contains("Do not add, delete or replace any word"))
        XCTAssertFalse(zero.contains("Additional instructions"))
        XCTAssertTrue(LMStudioService.systemPrompt.contains("proofreader, not a rewriter"))
    }

    func testExtractParagraph() {
        XCTAssertEqual(LMStudioService.extractParagraph(from: "```latex\nA \\emph{b}.\n```"), "A \\emph{b}.")
        XCTAssertEqual(LMStudioService.extractParagraph(from: "<paragraph>\nA b.\n</paragraph>\n"), "A b.")
        XCTAssertEqual(LMStudioService.extractParagraph(from: "<think>\nhmm\n</think>\n\nA b."), "A b.")
        XCTAssertEqual(LMStudioService.extractParagraph(from: "Here is the revised paragraph:\n\nA b."), "A b.")
        XCTAssertEqual(LMStudioService.extractParagraph(from: "Note:\n\nsee \\ref{x}"), "see \\ref{x}")
    }

    func testParseResponse() throws {
        let obj: [String: Any] = [
            "model": "qwen",
            "choices": [["message": ["role": "assistant", "content": "Fixed text."]]],
            "usage": ["prompt_tokens": 10, "completion_tokens": 3],
        ]
        let r = try LMStudioService.parseResponse(obj)
        XCTAssertEqual(r.text, "Fixed text.")
        XCTAssertEqual(r.promptTokens, 10)
        XCTAssertEqual(r.completionTokens, 3)
        XCTAssertThrowsError(try LMStudioService.parseResponse(["choices": []]))
    }

    func testPickModelPrefersLoadedChatModel() {
        let models = [
            LMModel(id: "text-embedding-x", type: "embeddings", state: "loaded"),
            LMModel(id: "a", type: "llm", state: "not-loaded"),
            LMModel(id: "b", type: "vlm", state: "loaded"),
        ]
        XCTAssertEqual(LMStudioService.pickModel(from: models), "b")
        XCTAssertEqual(LMStudioService.pickModel(from: Array(models.prefix(2))), "a")
        XCTAssertNil(LMStudioService.pickModel(from: Array(models.prefix(1))))
    }

    func testBaseURLNormalisation() {
        XCTAssertEqual(LMStudioService(baseURL: "127.0.0.1:1234/v1/").baseURL.absoluteString, "http://127.0.0.1:1234")
        XCTAssertEqual(LMStudioService(baseURL: "").baseURL.absoluteString, "http://127.0.0.1:1234")
    }
}

final class MissingFileDetectorTests: XCTestCase {
    func testDetectsPackagesAndFonts() {
        let log = """
        ! LaTeX Error: File `algorithm.sty' not found.
        ! Font OT1/pcr/m/n/10=pcrr7t at 10.0pt not loadable: Metric (TFM) file not found.
        ! I can't find file `pcrr7t'.
        """
        XCTAssertEqual(MissingFileDetector.missingFiles(in: log), ["algorithm.sty", "pcrr7t.tfm"])
        XCTAssertTrue(MissingFileDetector.needsRerun(log: "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right."))
        XCTAssertFalse(MissingFileDetector.needsRerun(log: "Output written on main.pdf"))
    }
}

final class FileTreeTests: XCTestCase {
    func testBuildExcludesArtifacts() throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("tree-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp.appendingPathComponent(".git"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: tmp.appendingPathComponent("sections"), withIntermediateDirectories: true)
        for f in ["main.tex", "main.aux", "main.synctex.gz", "main.pdf", ".DS_Store", "sections/intro.tex"] {
            try "x".write(to: tmp.appendingPathComponent(f), atomically: true, encoding: .utf8)
        }
        defer { try? FileManager.default.removeItem(at: tmp) }
        let tree = FileTree.build(root: tmp)
        XCTAssertEqual(tree.map(\.name), ["sections", "main.pdf", "main.tex"])
        XCTAssertEqual(FileTree.flattenFiles(tree).map(\.id), ["sections/intro.tex", "main.pdf", "main.tex"])
        XCTAssertEqual(FileTree.find("sections/intro.tex", in: tree)?.name, "intro.tex")
    }
}

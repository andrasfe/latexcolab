import PDFKit
import XCTest
@testable import LaTeXColabCore

final class SyncTeXTests: XCTestCase {
    var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("synctex-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        try "line1\nline2\n".write(to: tmp.appendingPathComponent("main.tex"), atomically: true, encoding: .utf8)
        try FileManager.default.createDirectory(at: tmp.appendingPathComponent("sections"), withIntermediateDirectories: true)
        try "body\n".write(to: tmp.appendingPathComponent("sections/body.tex"), atomically: true, encoding: .utf8)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmp)
    }

    /// Two line boxes on page 1: one from main.tex line 11 (with a glue record
    /// on line 9), one from sections/body.tex line 3.
    var sample: String {
        """
        SyncTeX Version:1
        Input:1:./main.tex
        Input:2:\(tmp.path)/./sections/body
        Output:pdf
        Magnification:1000
        Unit:1
        X Offset:0
        Y Offset:0
        Content:
        !631
        {1
        [1,16:4736286,46220574:26673152,41484288,0
        (1,11:8799518,19318411:22609920,455111,127431
        g1,9:9000000,19318411
        x1,10:20000000,19318411
        )
        (2,3:8799518,23844943:22609920,655359,183500
        g2,3:8799518,23844943
        )
        ]
        }1
        Postamble:
        Count:3
        """
    }

    func testParsesInputsAndBoxes() {
        let s = SyncTeXScanner(text: sample, baseURL: tmp)
        XCTAssertEqual(s.inputs[1], "./main.tex")
        XCTAssertEqual(s.boxes.count, 3)
        XCTAssertEqual(s.points.count, 3)
        // 8799518 sp → bp
        XCTAssertEqual(s.boxes[1].x, 8799518 / 65781.76, accuracy: 0.001)
        XCTAssertEqual(s.boxes[1].nesting, 1)
        XCTAssertEqual(s.fileURL(forTag: 1)?.lastPathComponent, "main.tex")
        XCTAssertEqual(s.fileURL(forTag: 2)?.lastPathComponent, "body.tex")
    }

    func testEditQueryPicksNearestPointRecord() {
        let s = SyncTeXScanner(text: sample, baseURL: tmp)
        let y = 19318411 / 65781.76
        let near9 = s.editQuery(page: 1, x: 9_100_000 / 65781.76, y: y)
        XCTAssertEqual(near9?.line, 9)
        XCTAssertEqual(near9?.file.lastPathComponent, "main.tex")
        let near10 = s.editQuery(page: 1, x: 19_000_000 / 65781.76, y: y)
        XCTAssertEqual(near10?.line, 10)
    }

    func testEditQueryOtherFileAndTolerance() {
        let s = SyncTeXScanner(text: sample, baseURL: tmp)
        let y = 23844943 / 65781.76
        let hit = s.editQuery(page: 1, x: 200, y: y + 5)
        XCTAssertEqual(hit?.line, 3)
        XCTAssertEqual(hit?.file.lastPathComponent, "body.tex")
        XCTAssertNil(s.editQuery(page: 1, x: 200, y: y + 200))
        XCTAssertNil(s.editQuery(page: 2, x: 200, y: y))
    }

    func testGzipRoundTrip() throws {
        let plain = tmp.appendingPathComponent("sample.synctex")
        try sample.write(to: plain, atomically: true, encoding: .utf8)
        let gz = ProcessRunner.run("/usr/bin/gzip", ["-k", "-f", plain.path], timeout: 20)
        XCTAssertTrue(gz.ok, gz.output)
        let text = try Gzip.readTextFile(at: tmp.appendingPathComponent("sample.synctex.gz"))
        XCTAssertEqual(text, sample)
        XCTAssertEqual(SyncTeXScanner.locateFile(forPDF: tmp.appendingPathComponent("sample.pdf"))?.lastPathComponent, "sample.synctex.gz")
    }

    /// End-to-end with a real engine when one is installed (skipped otherwise).
    func testRealCompileAndQuery() throws {
        guard let engine = EngineFinder.find() else { throw XCTSkip("no LaTeX engine installed") }
        let tex = """
        \\documentclass{article}
        \\begin{document}

        \\section{Introduction}
        This is the first paragraph of the introduction. It spans
        multiple source lines so that we can check how SyncTeX reports
        line numbers for words in the middle of a paragraph.

        This is the second paragraph. It is short.

        \\end{document}
        """
        try tex.write(to: tmp.appendingPathComponent("main.tex"), atomically: true, encoding: .utf8)
        let result = LaTeXCompiler(projectURL: tmp, mainFile: "main.tex").compile()
        XCTAssertTrue(result.ok, "engine \(engine)\n" + result.log.suffix(2000))
        let syncFile = try XCTUnwrap(SyncTeXScanner.locateFile(forPDF: tmp.appendingPathComponent("main.pdf")))
        let scanner = try SyncTeXScanner(fileURL: syncFile, baseURL: tmp)
        let doc = try XCTUnwrap(PDFDocument(url: tmp.appendingPathComponent("main.pdf")))

        // Locate words with PDFKit, convert to SyncTeX's top-left page space
        // exactly like the app does, and expect the right source paragraph.
        func paragraph(forText needle: String) throws -> String {
            let sel = try XCTUnwrap(doc.findString(needle, withOptions: []).first, "'\(needle)' not in PDF")
            let page = try XCTUnwrap(sel.pages.first)
            let b = sel.bounds(for: page)
            let pageBounds = page.bounds(for: .mediaBox)
            let x = Double(b.midX - pageBounds.minX)
            let y = Double(pageBounds.maxY - b.midY)
            let loc = try XCTUnwrap(scanner.editQuery(page: doc.index(for: page) + 1, x: x, y: y), "no SyncTeX hit for '\(needle)'")
            XCTAssertEqual(loc.file.lastPathComponent, "main.tex")
            let source = try String(contentsOf: loc.file, encoding: .utf8)
            return try XCTUnwrap(ParagraphLocator.paragraph(in: source, containingLine: loc.line)).text
        }
        XCTAssertEqual(try paragraph(forText: "second paragraph"), "This is the second paragraph. It is short.")
        XCTAssertTrue(try paragraph(forText: "line numbers").hasSuffix("line numbers for words in the middle of a paragraph."))
        XCTAssertTrue(try paragraph(forText: "first paragraph").hasPrefix("\\section{Introduction}"))
    }
}

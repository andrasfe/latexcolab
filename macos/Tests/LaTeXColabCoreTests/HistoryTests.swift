import XCTest
@testable import LaTeXColabCore

final class HistoryTests: XCTestCase {
    var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("hist-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmp)
    }

    func testMarkAppliedBuildsHistoryAndRoundTrips() throws {
        let store = EditsStore(projectURL: tmp)
        var e = ParagraphEdit(file: "main.tex", original: "v1 text here", draft: "v2 text here", lineHint: 10, maxWords: 5)
        e.markApplied(note: "first apply")
        XCTAssertEqual(e.original, "v2 text here")
        XCTAssertTrue(e.applied)
        XCTAssertEqual(e.history.map(\.text), ["v1 text here"])
        e.draft = "v3 text here"
        e.markApplied()
        XCTAssertEqual(e.history.map(\.text), ["v1 text here", "v2 text here"])
        store.upsert(e)
        try store.save()

        let again = EditsStore(projectURL: tmp)
        try again.load()
        let loaded = try XCTUnwrap(again.find(id: e.id))
        XCTAssertEqual(loaded.history.map(\.text), ["v1 text here", "v2 text here"])
        XCTAssertEqual(loaded.history[0].note, "first apply")
        XCTAssertNotNil(loaded.appliedAt)
    }

    func testOldJSONWithoutHistoryStillLoads() throws {
        let json = """
        {"version": 1, "edits": [{"id": "6F9619FF-8B86-D011-B42D-00C04FC964FF", "file": "a.tex",
          "original": "x", "draft": "y", "applied": false, "lineHint": 3, "maxWords": 7,
          "instructions": "", "createdAt": "2026-09-04T10:00:00Z", "updatedAt": "2026-09-04T10:00:00Z"}]}
        """
        try json.write(to: tmp.appendingPathComponent(EditsStore.fileName), atomically: true, encoding: .utf8)
        let store = EditsStore(projectURL: tmp)
        try store.load()
        XCTAssertEqual(store.edits.count, 1)
        XCTAssertEqual(store.edits[0].history, [])
        XCTAssertNil(store.edits[0].appliedAt)
    }

    func testMatchFallsBackToHistoryAndNearbyText() {
        let store = EditsStore(projectURL: tmp)
        var e = ParagraphEdit(file: "main.tex", original: "The quick brown fox jumps over the lazy dog near the river bank.",
                              draft: "The quick brown fox leaps over the lazy dog near the river bank.", lineHint: 12, maxWords: 5)
        e.markApplied()
        store.upsert(e)
        // Exact current text
        XCTAssertEqual(store.match(file: "main.tex", original: e.original, nearLine: 12)?.exact, true)
        // Reverted by hand to the old text → found via history
        let viaHistory = store.match(file: "main.tex", original: "The quick brown fox jumps over the lazy dog near the river bank.", nearLine: 40)
        XCTAssertEqual(viaHistory?.edit.id, e.id)
        XCTAssertEqual(viaHistory?.exact, false)
        // Hand-edited nearby paragraph with strong overlap
        let fuzzy = store.match(file: "main.tex", original: "The quick brown fox leaps over the lazy dog near the river bank today.", nearLine: 13)
        XCTAssertEqual(fuzzy?.edit.id, e.id)
        XCTAssertEqual(fuzzy?.exact, false)
        // Different file / far away / unrelated
        XCTAssertNil(store.match(file: "other.tex", original: e.original, nearLine: 12))
        XCTAssertNil(store.match(file: "main.tex", original: "Completely unrelated words about cats and hats.", nearLine: 12))
    }

    func testPartialRecordsDoNotAdoptParagraphRecords() throws {
        let store = EditsStore(projectURL: tmp)
        let paragraph = "The quick brown fox jumps over the lazy dog near the river bank. It was a sunny day and the fox was happy."
        store.upsert(ParagraphEdit(file: "main.tex", original: paragraph, draft: paragraph, lineHint: 12, maxWords: 5))
        let sentence = "The quick brown fox jumps over the lazy dog near the river bank."
        XCTAssertNil(store.match(file: "main.tex", original: sentence, nearLine: 12, partial: true))
        store.upsert(ParagraphEdit(file: "main.tex", original: sentence, draft: sentence + " Really.", lineHint: 12, maxWords: 5, partial: true))
        XCTAssertEqual(store.match(file: "main.tex", original: sentence, nearLine: 12, partial: true)?.exact, true)
        XCTAssertEqual(store.match(file: "main.tex", original: paragraph, nearLine: 12)?.edit.partial, false)
        try store.save()
        let again = EditsStore(projectURL: tmp)
        try again.load()
        XCTAssertEqual(again.edits.filter(\.partial).count, 1)
    }
}

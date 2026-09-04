import XCTest
@testable import LaTeXColabCore

final class EditsStoreTests: XCTestCase {
    var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("edits-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmp)
    }

    func testRoundTripAndLookup() throws {
        let store = EditsStore(projectURL: tmp)
        try store.load()
        XCTAssertEqual(store.edits.count, 0)
        let e = ParagraphEdit(file: "main.tex", original: "Hello world.\n", draft: "Hello, world.", lineHint: 12, maxWords: 5, instructions: "be nice")
        store.upsert(e)
        try store.save()
        XCTAssertTrue(FileManager.default.fileExists(atPath: tmp.appendingPathComponent(EditsStore.fileName).path))

        let again = EditsStore(projectURL: tmp)
        try again.load()
        XCTAssertEqual(again.edits.count, 1)
        XCTAssertEqual(again.find(file: "main.tex", original: "  Hello world.  ")?.id, e.id)
        XCTAssertNil(again.find(file: "other.tex", original: "Hello world."))
        XCTAssertEqual(again.pendingCount, 1)

        var applied = again.edits[0]
        applied.original = applied.draft
        applied.applied = true
        again.upsert(applied)
        XCTAssertEqual(again.pendingCount, 0)
        XCTAssertEqual(again.edits.count, 1)
        again.remove(id: e.id)
        XCTAssertEqual(again.edits.count, 0)
    }

    func testJSONShape() throws {
        let store = EditsStore(projectURL: tmp)
        store.upsert(ParagraphEdit(file: "a.tex", original: "x", draft: "y", lineHint: 1, maxWords: 0))
        try store.save()
        let json = try String(contentsOf: store.fileURL, encoding: .utf8)
        XCTAssertTrue(json.contains("\"version\" : 1"))
        XCTAssertTrue(json.contains("\"draft\" : \"y\""))
        XCTAssertTrue(json.contains("\"applied\" : false"))
    }
}

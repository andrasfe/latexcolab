import XCTest
@testable import LaTeXColabCore

final class GitClientTests: XCTestCase {
    func testParsePorcelainV2() {
        let out = """
        # branch.oid 1234
        # branch.head feature
        # branch.upstream origin/feature
        # branch.ab +2 -1
        1 .M N... 100644 100644 100644 abc def main.tex
        1 A. N... 000000 100644 100644 000 111 sections/new.tex
        2 R. N... 100644 100644 100644 aaa bbb R100 new-name.tex\told-name.tex
        u UU N... 100644 100644 100644 100644 aaa bbb ccc conflict.tex
        ? notes.txt
        """
        let s = GitStatus.parse(porcelain: out)
        XCTAssertTrue(s.isRepo)
        XCTAssertEqual(s.branch, "feature")
        XCTAssertEqual(s.upstream, "origin/feature")
        XCTAssertEqual(s.ahead, 2)
        XCTAssertEqual(s.behind, 1)
        XCTAssertEqual(s.changes.map(\.path), ["main.tex", "sections/new.tex", "new-name.tex", "conflict.tex", "notes.txt"])
        XCTAssertEqual(s.changes[0].summary, "modified")
        XCTAssertEqual(s.changes[1].summary, "added (staged)")
        XCTAssertEqual(s.changes[2].originalPath, "old-name.tex")
        XCTAssertTrue(s.changes[3].isConflict)
        XCTAssertTrue(s.changes[4].isUntracked)
        XCTAssertNil(GitStatus.parse(porcelain: "# branch.head (detached)\n").branch)
    }

    func testRealRepoRoundTrip() throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("git-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let git = GitClient(projectURL: tmp)
        guard git.gitPath != nil else { throw XCTSkip("git not installed") }
        XCTAssertFalse(git.status().isRepo)
        XCTAssertTrue(git.initRepository().ok)
        git.run(["config", "user.email", "t@example.com"])
        git.run(["config", "user.name", "Test"])
        try "hello".write(to: tmp.appendingPathComponent("main.tex"), atomically: true, encoding: .utf8)
        var s = git.status()
        XCTAssertTrue(s.isRepo)
        XCTAssertEqual(s.changes.map(\.path), ["main.tex"])
        XCTAssertTrue(s.changes[0].isUntracked)
        XCTAssertFalse(s.hasRemote)

        let c = git.commit(message: "first", paths: ["main.tex"])
        XCTAssertTrue(c.ok, c.output)
        XCTAssertTrue(c.output.contains("committed"))
        s = git.status()
        XCTAssertTrue(s.changes.isEmpty)
        XCTAssertNotNil(s.branch)

        // Push to a bare "remote" and check upstream tracking gets set.
        let bare = tmp.appendingPathComponent("remote.git")
        XCTAssertTrue(GitClient(projectURL: tmp).run(["init", "--bare", "-q", bare.path]).ok)
        XCTAssertTrue(git.setRemote(url: bare.path).ok)
        s = git.status()
        XCTAssertEqual(s.remoteURL, bare.path)
        XCTAssertFalse(s.hasUpstream)
        let p = git.push(branch: s.branch, hasUpstream: false)
        XCTAssertTrue(p.ok, p.output)
        s = git.status()
        XCTAssertTrue(s.hasUpstream)
        XCTAssertEqual(s.ahead, 0)
        let (outcome, _) = git.pull(rebase: false)
        XCTAssertEqual(outcome, .success)
        XCTAssertEqual(git.branches().count, 1)
    }
}

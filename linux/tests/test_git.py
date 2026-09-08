"""Port of GitClientTests.swift."""

import os
import tempfile
import unittest

from latexcolab.core.git import PULL_SUCCESS, GitClient, GitStatus


class GitStatusTests(unittest.TestCase):
    def test_parse_porcelain_v2(self):
        out = """# branch.oid 1234
# branch.head feature
# branch.upstream origin/feature
# branch.ab +2 -1
1 .M N... 100644 100644 100644 abc def main.tex
1 A. N... 000000 100644 100644 000 111 sections/new.tex
2 R. N... 100644 100644 100644 aaa bbb R100 new-name.tex\told-name.tex
u UU N... 100644 100644 100644 100644 aaa bbb ccc conflict.tex
? notes.txt"""
        s = GitStatus.parse(out)
        self.assertTrue(s.is_repo)
        self.assertEqual(s.branch, "feature")
        self.assertEqual(s.upstream, "origin/feature")
        self.assertEqual(s.ahead, 2)
        self.assertEqual(s.behind, 1)
        self.assertEqual([c.path for c in s.changes],
                         ["main.tex", "sections/new.tex", "new-name.tex",
                          "conflict.tex", "notes.txt"])
        self.assertEqual(s.changes[0].summary, "modified")
        self.assertEqual(s.changes[1].summary, "added (staged)")
        self.assertEqual(s.changes[2].original_path, "old-name.tex")
        self.assertTrue(s.changes[3].is_conflict)
        self.assertTrue(s.changes[4].is_untracked)
        self.assertIsNone(GitStatus.parse("# branch.head (detached)\n").branch)


class RealRepoTests(unittest.TestCase):
    def test_real_repo_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = GitClient(tmp)
            if not git.git_path:
                self.skipTest("git not installed")
            self.assertFalse(git.status().is_repo)
            self.assertTrue(git.init_repository().ok)
            git.run(["config", "user.email", "t@example.com"])
            git.run(["config", "user.name", "Test"])
            with open(os.path.join(tmp, "main.tex"), "w", encoding="utf-8") as fh:
                fh.write("hello")
            s = git.status()
            self.assertTrue(s.is_repo)
            self.assertEqual([c.path for c in s.changes], ["main.tex"])
            self.assertTrue(s.changes[0].is_untracked)
            self.assertFalse(s.has_remote)

            c = git.commit("first", ["main.tex"])
            self.assertTrue(c.ok, c.output)
            self.assertIn("committed", c.output)
            s = git.status()
            self.assertEqual(s.changes, [])
            self.assertIsNotNone(s.branch)

            # Push to a bare "remote" and check upstream tracking gets set.
            bare = os.path.join(tmp, "remote.git")
            self.assertTrue(git.run(["init", "--bare", "-q", bare]).ok)
            self.assertTrue(git.set_remote(bare).ok)
            s = git.status()
            self.assertEqual(s.remote_url, bare)
            self.assertFalse(s.has_upstream)
            p = git.push(s.branch, False)
            self.assertTrue(p.ok, p.output)
            s = git.status()
            self.assertTrue(s.has_upstream)
            self.assertEqual(s.ahead, 0)
            outcome, _ = git.pull(False)
            self.assertEqual(outcome, PULL_SUCCESS)
            self.assertEqual(len(git.branches()), 1)


if __name__ == "__main__":
    unittest.main()

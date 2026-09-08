"""Thin wrapper over the ``git`` CLI (port of GitClient.swift).

All calls are blocking — use from a worker thread. ``GIT_TERMINAL_PROMPT=0``
keeps git from hanging on a password prompt; use SSH keys or a credential helper.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import process
from .process import ProcessResult

PULL_SUCCESS = "success"
PULL_DIVERGED = "diverged"
PULL_FAILED = "failed"


@dataclass(frozen=True)
class GitChange:
    """One entry from ``git status --porcelain=v2``."""
    path: str
    code: str          # two-letter XY code (index, worktree); "??" for untracked
    original_path: str | None = None

    @property
    def id(self) -> str:
        return self.path

    @property
    def is_untracked(self) -> bool:
        return self.code == "??"

    @property
    def is_conflict(self) -> bool:
        return "U" in self.code or self.code in ("AA", "DD")

    @property
    def summary(self) -> str:
        if self.is_untracked:
            return "untracked"
        if self.is_conflict:
            return "conflict"
        x = self.code[0] if self.code else "."
        y = self.code[1] if len(self.code) > 1 else "."

        def word(c: str) -> str | None:
            return {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
                    "C": "copied", "T": "type changed"}.get(c)

        parts = []
        w = word(x)
        if w:
            parts.append(w + " (staged)")
        w = word(y)
        if w:
            parts.append(w)
        return ", ".join(parts) if parts else self.code


@dataclass
class GitStatus:
    is_repo: bool = False
    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    changes: list[GitChange] = field(default_factory=list)
    remote_url: str | None = None

    @property
    def is_dirty(self) -> bool:
        return bool(self.changes)

    @property
    def has_remote(self) -> bool:
        return self.remote_url is not None

    @property
    def has_upstream(self) -> bool:
        return self.upstream is not None

    @staticmethod
    def parse(porcelain: str) -> "GitStatus":
        """Parse the output of ``git status --porcelain=v2 --branch``."""
        s = GitStatus(is_repo=True)
        for line in porcelain.split("\n"):
            if not line:
                continue
            if line.startswith("# branch.head "):
                v = line[len("# branch.head "):]
                s.branch = None if v == "(detached)" else v
            elif line.startswith("# branch.upstream "):
                s.upstream = line[len("# branch.upstream "):]
            elif line.startswith("# branch.ab "):
                parts = line[len("# branch.ab "):].split()
                if len(parts) == 2:
                    s.ahead = _to_int(parts[0][1:])
                    s.behind = _to_int(parts[1][1:])
            elif line.startswith("1 "):
                f = line.split(" ", 8)
                if len(f) >= 9:
                    s.changes.append(GitChange(f[8], f[1]))
            elif line.startswith("2 "):
                f = line.split(" ", 9)
                if len(f) >= 10:
                    paths = f[9].split("\t", 1)
                    s.changes.append(GitChange(paths[0], f[1],
                                               paths[1] if len(paths) > 1 else None))
            elif line.startswith("u "):
                f = line.split(" ", 10)
                if len(f) >= 11:
                    s.changes.append(GitChange(f[10], f[1]))
            elif line.startswith("? "):
                s.changes.append(GitChange(line[2:], "??"))
        return s


def _to_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


class GitClient:
    def __init__(self, project_path: str | os.PathLike):
        self.project_path = str(project_path)
        self.git_path = process.which("git") or (
            "/usr/bin/git" if os.access("/usr/bin/git", os.X_OK) else None)

    def run(self, args: list[str], timeout: float = 120) -> ProcessResult:
        if not self.git_path:
            return ProcessResult(
                -1, "git not found. Install it with your package manager "
                    "(e.g. sudo apt install git).", False)
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = "cat"
        env.setdefault("LC_ALL", "C.UTF-8")
        base_path = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        env["PATH"] = ":".join([base_path] + process.extra_search_paths())
        return process.run(self.git_path, args, cwd=self.project_path,
                           environment=env, timeout=timeout)

    @property
    def is_repository(self) -> bool:
        return self.run(["rev-parse", "--is-inside-work-tree"], 15).output.strip() == "true"

    def status(self) -> GitStatus:
        if not self.git_path or not self.is_repository:
            return GitStatus(is_repo=False)
        r = self.run(["status", "--porcelain=v2", "--branch", "--untracked-files=all"], 30)
        if not r.ok:
            return GitStatus(is_repo=True)
        s = GitStatus.parse(r.output)
        remote = self.run(["remote", "get-url", "origin"], 15)
        if remote.ok:
            url = remote.output.strip()
            s.remote_url = url or None
        return s

    def branches(self) -> list[str]:
        r = self.run(["branch", "--format=%(refname:short)"], 15)
        if not r.ok:
            return []
        return [line.strip() for line in r.output.split("\n") if line.strip()]

    def init_repository(self) -> ProcessResult:
        return self.run(["init", "-q"], 30)

    def set_remote(self, url: str) -> ProcessResult:
        existing = self.run(["remote", "get-url", "origin"], 15)
        if existing.ok:
            return self.run(["remote", "set-url", "origin", url], 15)
        return self.run(["remote", "add", "origin", url], 15)

    def fetch(self) -> ProcessResult:
        return self.run(["fetch", "--prune"], 180)

    def pull(self, rebase: bool) -> tuple[str, ProcessResult]:
        """Fast-forward pull; reports ``diverged`` when the branches need a merge/rebase."""
        r = self.run(["pull", "--rebase"] if rebase else ["pull", "--ff-only"], 300)
        if r.ok:
            return PULL_SUCCESS, r
        out = r.output.lower()
        if not rebase and ("not possible to fast-forward" in out or "diverg" in out
                           or "fatal: need to specify how to reconcile" in out):
            return PULL_DIVERGED, r
        return PULL_FAILED, r

    def commit(self, message: str, paths: list[str] | None) -> ProcessResult:
        """Stage the given paths (all changes when ``paths`` is None) and commit."""
        if paths is None:
            add = self.run(["add", "-A"], 60)
        elif paths:
            add = self.run(["add", "-A", "--", *paths], 60)
        else:
            return ProcessResult(1, "Nothing selected to commit.", False)
        if not add.ok:
            return add
        r = self.run(["commit", "-q", "-m", message], 60)
        if r.ok:
            show = self.run(["log", "-1", "--format=%h %s"], 15)
            return ProcessResult(0, add.output + r.output + "committed " + show.output, False)
        return ProcessResult(r.status, add.output + r.output, r.timed_out)

    def push(self, branch: str | None, has_upstream: bool) -> ProcessResult:
        """Push, setting the upstream on first push."""
        if has_upstream:
            return self.run(["push"], 300)
        if not branch:
            return ProcessResult(1, "Detached HEAD — check out a branch before pushing.", False)
        return self.run(["push", "-u", "origin", branch], 300)

    def checkout(self, branch: str, create: bool) -> ProcessResult:
        return self.run(["checkout", "-b", branch] if create else ["checkout", branch], 60)

    def log(self, limit: int = 20) -> ProcessResult:
        return self.run(["log", "--oneline", "--decorate", "-n", str(limit)], 30)

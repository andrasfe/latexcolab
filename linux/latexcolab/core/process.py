"""Subprocess helper and executable lookup (port of ProcessRunner.swift)."""

from __future__ import annotations

import glob as _glob
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    status: int
    output: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 0 and not self.timed_out


def run(executable: str, arguments: list[str], cwd: str | os.PathLike | None = None,
        environment: dict[str, str] | None = None, timeout: float = 120) -> ProcessResult:
    """Blocking subprocess call. stdout and stderr are merged, stdin is /dev/null."""
    try:
        proc = subprocess.Popen(
            [str(executable), *[str(a) for a in arguments]],
            cwd=str(cwd) if cwd else None,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return ProcessResult(-1, f"failed to launch {executable}: {exc}", False)

    timed_out = False
    try:
        out = proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        timed_out = True
        # The engine may have spawned children (latexmk → pdflatex); kill the group.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            out = proc.communicate()[0]

    text = (out or b"").decode("utf-8", "replace")
    if timed_out:
        text += f"\n[timed out after {int(timeout)}s]"
    return ProcessResult(proc.returncode if proc.returncode is not None else -1, text, timed_out)


def extra_search_paths() -> list[str]:
    """Directories a desktop-launched app should search besides its (minimal) PATH."""
    home = str(Path.home())
    return [
        "/usr/local/bin", "/usr/bin", "/bin",
        home + "/.local/bin", home + "/bin",
        "/snap/bin", "/var/lib/flatpak/exports/bin",
        home + "/.local/share/flatpak/exports/bin",
        "/opt/conda/bin", home + "/miniconda3/bin", home + "/anaconda3/bin",
        home + "/.cargo/bin",
    ]


def which(name: str, extra_paths: list[str] | None = None) -> str | None:
    if "/" in name:
        return name if os.access(name, os.X_OK) and os.path.isfile(name) else None
    env_path = os.environ.get("PATH", "/usr/bin:/bin")
    dirs = env_path.split(":") + list(extra_paths or []) + extra_search_paths()
    for d in dirs:
        if not d:
            continue
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def glob(pattern: str) -> list[str]:
    return sorted(_glob.glob(pattern))


def have(name: str) -> bool:
    return which(name) is not None or shutil.which(name) is not None

"""Zip the project folder (port of ZipExporter.swift).

The project appears as a top-level directory in the archive; ``.git``, build
artifacts and editor junk are skipped — same filter as the file tree. Uses the
``zip`` binary when present and falls back to Python's ``zipfile`` otherwise,
so the feature works on a bare install.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

from . import filetree, process


class ZipError(Exception):
    pass


def export(project: str | os.PathLike, destination: str | os.PathLike) -> None:
    project = os.path.normpath(str(project))
    destination = str(destination)
    parent = os.path.dirname(project)
    name = os.path.basename(project)

    if os.path.exists(destination):
        os.remove(destination)

    zip_bin = process.which("zip")
    if zip_bin:
        args = ["-r", "-q", "-X", destination, name, "-x"]
        for d in sorted(filetree.EXCLUDED_DIRECTORIES):
            args.append(f"{name}/{d}/*")
            args.append(f"*/{d}/*")
        for s in filetree.EXCLUDED_SUFFIXES:
            args.append(f"*{s}")
        for f in sorted(filetree.EXCLUDED_FILES):
            args.append(f"*/{f}")
        r = process.run(zip_bin, args, cwd=parent, timeout=180)
        if not r.ok:
            raise ZipError(f"zip failed ({r.status}):\n{r.output}")
        return

    _export_with_zipfile(project, destination, name)


def _export_with_zipfile(project: str, destination: str, name: str) -> None:
    try:
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(project):
                dirs[:] = [d for d in dirs if d not in filetree.EXCLUDED_DIRECTORIES]
                for f in files:
                    if filetree.is_excluded(f):
                        continue
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, project)
                    zf.write(full, os.path.join(name, rel))
    except OSError as exc:
        raise ZipError(f"zip failed: {exc}") from exc


def default_destination(project: str | os.PathLike) -> str:
    return str(Path.home() / (os.path.basename(os.path.normpath(str(project))) + ".zip"))

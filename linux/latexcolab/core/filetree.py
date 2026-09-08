"""Project file tree (port of FileTree.swift)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Directories that never show up in the tree or the zip.
EXCLUDED_DIRECTORIES = {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode"}

#: LaTeX build artifacts hidden from the tree and left out of the zip.
EXCLUDED_SUFFIXES = [
    ".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk",
    ".synctex.gz", ".synctex", ".bbl", ".blg", ".nav", ".snm", ".vrb",
]

EXCLUDED_FILES = {".DS_Store"}

TEXT_EXTENSIONS = {
    "tex", "bib", "cls", "sty", "md", "txt", "json", "yaml", "yml",
    "toml", "cfg", "ini", "gitignore", "sh", "py", "js", "ts", "html",
    "css", "csv", "tsv", "bst", "def", "clo", "ltx", "dtx",
}

TEXT_FILE_NAMES = {".gitignore", ".gitattributes", "Makefile", "latexmkrc", ".latexmkrc"}


@dataclass(frozen=True)
class FileNode:
    id: str            # project-relative path
    name: str
    path: str          # absolute path
    is_directory: bool
    children: tuple["FileNode", ...] | None = None

    @property
    def relative_path(self) -> str:
        return self.id

    @property
    def file_extension(self) -> str:
        return os.path.splitext(self.name)[1].lstrip(".").lower()


def is_excluded(file_name: str) -> bool:
    if file_name in EXCLUDED_FILES:
        return True
    return any(file_name.endswith(s) for s in EXCLUDED_SUFFIXES)


def is_text_file(name: str) -> bool:
    if name in TEXT_FILE_NAMES:
        return True
    return os.path.splitext(name)[1].lstrip(".").lower() in TEXT_EXTENSIONS


def relative_path(path: str | os.PathLike, root: str | os.PathLike) -> str:
    root_path = os.path.normpath(str(root))
    p = os.path.normpath(str(path))
    if p == root_path:
        return ""
    if p.startswith(root_path + os.sep):
        return p[len(root_path) + 1:]
    return os.path.basename(p)


def build(root: str | os.PathLike) -> list[FileNode]:
    return _build_children(str(root), str(root))


def _build_children(directory: str, root: str) -> list[FileNode]:
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []

    nodes: list[FileNode] = []
    for entry in entries:
        name = entry.name
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        rel = relative_path(entry.path, root)
        if is_dir:
            if name in EXCLUDED_DIRECTORIES:
                continue
            nodes.append(FileNode(rel, name, entry.path, True,
                                  tuple(_build_children(entry.path, root))))
        else:
            if is_excluded(name):
                continue
            nodes.append(FileNode(rel, name, entry.path, False, None))

    nodes.sort(key=lambda n: (not n.is_directory, n.name.lower(), n.name))
    return nodes


def flatten_files(nodes: list[FileNode] | tuple[FileNode, ...]) -> list[FileNode]:
    """Depth-first list of every file node (directories excluded)."""
    out: list[FileNode] = []
    for n in nodes:
        if n.is_directory:
            out.extend(flatten_files(n.children or ()))
        else:
            out.append(n)
    return out


def find(path: str, nodes: list[FileNode] | tuple[FileNode, ...]) -> FileNode | None:
    for n in nodes:
        if n.id == path:
            return n
        if n.children:
            hit = find(path, n.children)
            if hit is not None:
                return hit
    return None


def read_text(path: str | os.PathLike) -> str | None:
    """Read a UTF-8 text file, normalising CRLF. None when it is not readable text."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None

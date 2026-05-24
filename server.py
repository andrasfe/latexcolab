"""LaTeX Colab — local web app for editing LaTeX, previewing PDFs, and running git.

Run:
    pip install -r requirements.txt
    python server.py
Then open http://localhost:8000
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

ROOT = Path(__file__).parent.resolve()
PROJECT_DIR = (ROOT / "project").resolve()
MAIN_TEX = "main.tex"
PDF_NAME = "main.pdf"

# Whitelist of git subcommands the bottom CLI is allowed to run.
# Where to look for a LaTeX engine when nothing is on PATH.
# Globs are expanded against $HOME (so TeX Live's per-year directory is found
# without needing to bake in 2024/2025/...). Order = preference.
ENGINE_SEARCH_GLOBS = [
    "Library/TinyTeX/bin/*",                          # TinyTeX (macOS, no sudo)
    ".TinyTeX/bin/*",                                 # TinyTeX (Linux)
    "Library/TinyTeX/bin/universal-darwin",
    "Library/TinyTeX/bin/x86_64-darwin",
]
ENGINE_SEARCH_DIRS = [
    "/usr/local/texlive/*/bin/*",                     # MacTeX / TeX Live
    "/Library/TeX/texbin",                            # MacTeX symlink
    "/opt/homebrew/bin",                              # brew (Apple silicon)
    "/usr/local/bin",                                 # brew (Intel)
    "/opt/conda/bin",
    str(Path.home() / "miniconda3/bin"),
    str(Path.home() / "anaconda3/bin"),
]
ENGINE_PREFERENCE = ["latexmk", "pdflatex", "tectonic"]


def _find_engine_in_common_dirs() -> str | None:
    import glob

    candidates: list[str] = []
    home = str(Path.home())
    for g in ENGINE_SEARCH_GLOBS:
        candidates.extend(glob.glob(str(Path(home) / g)))
    for d in ENGINE_SEARCH_DIRS:
        candidates.extend(glob.glob(d))
    for name in ENGINE_PREFERENCE:
        for d in candidates:
            p = Path(d) / name
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


GIT_ALLOWED = {
    "status", "log", "diff", "pull", "push", "fetch", "add",
    "commit", "branch", "checkout", "remote", "stash", "reset",
    "show", "config",
}

app = FastAPI()


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Force browsers to revalidate static assets. Avoids stale JS/CSS after edits."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") and (
            path == "/" or path.endswith((".html", ".js", ".css"))
        ):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


app.add_middleware(NoCacheStaticMiddleware)


class FileWrite(BaseModel):
    path: str
    content: str


class GitCmd(BaseModel):
    args: str  # raw arg string after "git "


class ProjectPath(BaseModel):
    path: str


# File-tree exclusions (directories) and file-extension/suffix filters.
TREE_EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode"}
TREE_EXCLUDE_SUFFIXES = {
    ".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk",
    ".synctex.gz", ".bbl", ".blg", ".nav", ".snm", ".vrb",
}
TREE_EXCLUDE_FILES = {".DS_Store"}

# Files we'll send as text. Anything else returns a "binary" marker.
TEXT_SUFFIXES = {
    ".tex", ".bib", ".cls", ".sty", ".md", ".txt", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".gitignore", ".sh", ".py", ".js", ".ts", ".html",
    ".css", ".csv", ".tsv",
}


def _safe_resolve(rel: str) -> Path:
    """Resolve a project-relative path, refusing anything outside PROJECT_DIR."""
    rel = (rel or "").lstrip("/")
    p = (PROJECT_DIR / rel).resolve()
    if p != PROJECT_DIR and PROJECT_DIR not in p.parents:
        raise HTTPException(400, f"path outside project: {rel}")
    return p


def _is_text(p: Path) -> bool:
    if p.name in {".gitignore", ".gitattributes", "Makefile"}:
        return True
    return p.suffix.lower() in TEXT_SUFFIXES


def _build_tree(root: Path) -> list[dict]:
    out: list[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except FileNotFoundError:
        return out
    for entry in entries:
        if entry.is_dir():
            if entry.name in TREE_EXCLUDE_DIRS:
                continue
            rel = entry.relative_to(PROJECT_DIR).as_posix()
            out.append({
                "name": entry.name,
                "type": "dir",
                "path": rel,
                "children": _build_tree(entry),
            })
        else:
            if entry.name in TREE_EXCLUDE_FILES:
                continue
            if any(entry.name.endswith(s) for s in TREE_EXCLUDE_SUFFIXES):
                continue
            rel = entry.relative_to(PROJECT_DIR).as_posix()
            out.append({"name": entry.name, "type": "file", "path": rel})
    return out


@app.get("/api/tree")
def tree() -> dict:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    return {"root": PROJECT_DIR.name, "children": _build_tree(PROJECT_DIR)}


@app.get("/api/project")
def get_project() -> dict:
    return {"path": str(PROJECT_DIR), "name": PROJECT_DIR.name}


@app.get("/api/browse")
def browse(path: str = "") -> dict:
    """List immediate subdirectories of `path` (defaults to $HOME)."""
    if not path:
        target = Path.home()
    else:
        target = Path(path).expanduser()
    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(400, f"resolve failed: {e}")
    if not target.exists():
        raise HTTPException(404, f"not found: {target}")
    if not target.is_dir():
        raise HTTPException(400, f"not a directory: {target}")
    dirs: list[dict] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, f"permission denied: {target}")
    parent = str(target.parent) if target.parent != target else None
    return {"path": str(target), "parent": parent, "dirs": dirs}


@app.post("/api/project")
def set_project(body: ProjectPath) -> dict:
    """Point the app at a different local folder (no auto-creation of files)."""
    global PROJECT_DIR
    p = Path(body.path).expanduser().resolve()
    if not p.exists():
        raise HTTPException(400, f"does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {p}")
    PROJECT_DIR = p
    return {"ok": True, "path": str(PROJECT_DIR), "name": PROJECT_DIR.name}


@app.get("/api/zip")
def download_zip() -> Response:
    """Zip the project (skipping .git, build artifacts) and stream it."""
    import io
    import zipfile

    if not PROJECT_DIR.exists():
        raise HTTPException(404, "project folder missing")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_DIR):
            dirs[:] = [d for d in dirs if d not in TREE_EXCLUDE_DIRS]
            for f in files:
                if f in TREE_EXCLUDE_FILES:
                    continue
                if any(f.endswith(s) for s in TREE_EXCLUDE_SUFFIXES):
                    continue
                full = Path(root) / f
                arc = full.relative_to(PROJECT_DIR.parent)
                zf.write(full, str(arc))
    data = buf.getvalue()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{PROJECT_DIR.name}.zip"',
            "Content-Length": str(len(data)),
        },
    )


@app.get("/api/file")
def read_file(path: str = MAIN_TEX) -> dict:
    p = _safe_resolve(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"not found: {path}")
    if not _is_text(p):
        return {"path": path, "binary": True, "content": ""}
    try:
        return {"path": path, "binary": False, "content": p.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        return {"path": path, "binary": True, "content": ""}


@app.put("/api/file")
def write_file(body: FileWrite) -> dict:
    p = _safe_resolve(body.path)
    if p.exists() and p.is_dir():
        raise HTTPException(400, f"is a directory: {body.path}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    return {"ok": True, "bytes": len(body.content), "path": body.path}


@app.post("/api/compile")
def compile_tex() -> JSONResponse:
    path = _safe_resolve(MAIN_TEX)
    if not path.exists():
        raise HTTPException(404, f"{MAIN_TEX} not found")

    # Resolution order:
    #   1. LATEX_ENGINE env var (full path or bare name)
    #   2. latexmk / pdflatex on PATH
    #   3. Common no-sudo install locations (TinyTeX, MacTeX, conda, Tectonic)
    override = os.environ.get("LATEX_ENGINE")
    engine = shutil.which(override) if override else None
    if engine is None and override and Path(override).is_file():
        engine = override
    if engine is None:
        engine = shutil.which("latexmk") or shutil.which("pdflatex")
    if engine is None:
        engine = _find_engine_in_common_dirs()
    if engine is None:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "log": (
                    "No LaTeX engine found.\n\n"
                    "Tried: $LATEX_ENGINE, then `latexmk` / `pdflatex` on PATH.\n\n"
                    "No-sudo install options:\n"
                    "  • TinyTeX (recommended, ~100MB, installs to ~/Library/TinyTeX):\n"
                    "      curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh\n"
                    "      export PATH=\"$HOME/Library/TinyTeX/bin/universal-darwin:$PATH\"\n"
                    "  • Tectonic (single binary, ~30MB):\n"
                    "      curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh\n"
                    "      then set LATEX_ENGINE=/path/to/tectonic\n"
                    "  • conda: `conda install -c conda-forge texlive-core`\n\n"
                    "If pdflatex is already installed but not on PATH, point at it:\n"
                    "  LATEX_ENGINE=/full/path/to/pdflatex python server.py"
                ),
            },
        )

    name = Path(engine).name
    if name == "latexmk":
        # -g forces a full rebuild even if latexmk's cache says "nothing to do".
        # Critical when a prior run failed (e.g. PATH issue) and the .fdb_latexmk
        # state still records that failure — without -g latexmk refuses to retry.
        cmd = [engine, "-pdf", "-g", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", MAIN_TEX]
    elif name == "tectonic":
        cmd = [engine, "--keep-logs", MAIN_TEX]
    else:
        cmd = [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", MAIN_TEX]

    # Prepend the engine's directory to PATH so that latexmk's child processes
    # (pdflatex, bibtex, ...) are found even when the parent shell's PATH
    # doesn't include the TeX install.
    env = {**os.environ}
    engine_dir = str(Path(engine).parent)
    env["PATH"] = engine_dir + os.pathsep + env.get("PATH", "")

    def run_engine() -> tuple[int, str]:
        try:
            p = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired:
            return -1, "Compilation timed out after 120s."
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    rc, log = run_engine()

    # TinyTeX is minimal — install missing packages as papers ask for them.
    # Loop because installing one package often surfaces the next missing one
    # (e.g. \usepackage{algorithm} needs both `algorithm` AND `algorithmicx`).
    pdf_path = PROJECT_DIR / PDF_NAME
    tlmgr = shutil.which("tlmgr", path=env["PATH"])
    tried_installs: set[str] = set()
    for _ in range(5):
        if rc == 0 and pdf_path.exists():
            break
        if not tlmgr:
            break
        files = _missing_files_from_log(log)
        if not files:
            break
        pkgs, search_log = _resolve_packages(tlmgr, files, env)
        pkgs = [p for p in pkgs if p not in tried_installs]
        log += "\n--- resolving missing files via tlmgr search ---\n" + search_log
        if not pkgs:
            break
        log += f"--- auto-installing: {', '.join(pkgs)} ---\n"
        installed_any = False
        for pkg in pkgs:
            tried_installs.add(pkg)
            p = subprocess.run(
                [tlmgr, "install", pkg],
                capture_output=True, text=True, timeout=180, env=env,
            )
            log += f"$ tlmgr install {pkg}\n{p.stdout}{p.stderr}\n"
            if p.returncode == 0:
                installed_any = True
        if not installed_any:
            break
        log += "--- retrying compile ---\n"
        rc, log2 = run_engine()
        log += log2

    ok = rc == 0 and pdf_path.exists()
    return JSONResponse(
        status_code=200 if ok else 500,
        content={"ok": ok, "returncode": rc, "log": log[-20000:]},
    )


_MISSING_FILE_RE = re.compile(r"! LaTeX Error: File `([^']+)' not found")


def _missing_files_from_log(log: str) -> list[str]:
    """Pull the bare filenames LaTeX complains about (e.g. 'algorithmic.sty')."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _MISSING_FILE_RE.finditer(log):
        name = m.group(1)
        # If no extension, LaTeX is hinting it's a .sty.
        if "." not in name:
            name = name + ".sty"
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _resolve_packages(tlmgr: str, files: list[str], env: dict) -> tuple[list[str], str]:
    """Map missing filenames to tlmgr package names via `tlmgr search --file`.

    Returns (package_names, search_log). Falls back to the file's stem when the
    search yields nothing — many packages share a name with their main file.
    """
    pkgs: list[str] = []
    seen: set[str] = set()
    search_log = ""
    for fname in files:
        try:
            p = subprocess.run(
                [tlmgr, "search", "--global", "--file", "/" + fname],
                capture_output=True, text=True, timeout=60, env=env,
            )
        except subprocess.TimeoutExpired:
            search_log += f"$ tlmgr search --file /{fname}  [timed out]\n"
            continue
        search_log += f"$ tlmgr search --file /{fname}\n{p.stdout}"
        # Output format: lines like "<filename> - <pkg>:" for files, then
        # the package name follows on a line ending with ':' — but the pattern
        # most common is groups like "  <path>" indented under "package_name:".
        found = False
        for line in p.stdout.splitlines():
            if line.endswith(":") and not line.startswith(" "):
                name = line[:-1].strip()
                if name and name not in seen:
                    seen.add(name)
                    pkgs.append(name)
                    found = True
        if not found:
            # Last resort: try the filename's stem as a package name.
            stem = re.sub(r"\.(sty|cls|ldf|def|tex|cfg|fd|clo)$", "", fname)
            if stem and stem not in seen:
                seen.add(stem)
                pkgs.append(stem)
    return pkgs, search_log


@app.get("/api/pdf")
def get_pdf() -> Response:
    pdf_path = PROJECT_DIR / PDF_NAME
    if not pdf_path.exists():
        raise HTTPException(404, "PDF not yet generated")
    # Disable caching so the iframe always picks up the latest build.
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.post("/api/git")
def run_git(body: GitCmd) -> dict:
    try:
        argv = shlex.split(body.args)
    except ValueError as e:
        raise HTTPException(400, f"parse error: {e}")
    if not argv:
        raise HTTPException(400, "empty command")
    sub = argv[0]
    if sub not in GIT_ALLOWED:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"git subcommand '{sub}' not allowed. Allowed: {', '.join(sorted(GIT_ALLOWED))}",
        }
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "git command timed out"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


# Static frontend last so /api/* wins.
app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True), name="static")


def _bootstrap_project() -> None:
    """Create a starter project folder + sample .tex + git repo if missing."""
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    tex = PROJECT_DIR / MAIN_TEX
    if not tex.exists():
        tex.write_text(
            "\\documentclass{article}\n"
            "\\title{LaTeX Colab}\n"
            "\\author{You}\n"
            "\\begin{document}\n"
            "\\maketitle\n"
            "Hello, world. Edit me on the left, hit \\textbf{Generate}, and watch the\n"
            "PDF appear on the right.\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
    gi = PROJECT_DIR / ".gitignore"
    if not gi.exists():
        gi.write_text("*.aux\n*.log\n*.out\n*.toc\n*.fls\n*.fdb_latexmk\n*.synctex.gz\n", encoding="utf-8")
    if not (PROJECT_DIR / ".git").exists() and shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=PROJECT_DIR, check=False)


if __name__ == "__main__":
    import uvicorn

    _bootstrap_project()
    uvicorn.run(app, host="127.0.0.1", port=8000)

"""LaTeX engine discovery and compilation (port of LaTeXCompiler.swift).

Search locations are the Linux equivalents of the macOS ones: PATH, the usual
no-sudo installs (TinyTeX, TeX Live, conda, cargo), and distribution paths.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import process
from .process import ProcessResult

Progress = Callable[[str], None] | None


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    log: str
    pdf_path: str | None
    engine: str | None


# --------------------------------------------------------------------------
# Engine discovery
# --------------------------------------------------------------------------

HOME_GLOBS = [
    ".TinyTeX/bin/*",
    "TinyTeX/bin/*",
    ".local/share/TinyTeX/bin/*",
    "texlive/*/bin/*",
]

DIR_GLOBS = [
    "/usr/local/texlive/*/bin/*",
    "/opt/texlive/*/bin/*",
    "/usr/share/texlive/bin/*",
    "/usr/local/bin",
    "/opt/conda/bin",
    "~/miniconda3/bin",
    "~/anaconda3/bin",
    "/snap/bin",
]

PREFERENCE = ["latexmk", "pdflatex", "tectonic"]

INSTALL_HELP = """\
No LaTeX engine found.

Tried $LATEX_ENGINE, then latexmk / pdflatex / tectonic on PATH and in the
usual install locations (TinyTeX, TeX Live, conda, /snap/bin).

Install options:
  • Debian/Ubuntu:  sudo apt install texlive-latex-recommended texlive-latex-extra latexmk
  • Fedora:         sudo dnf install texlive-scheme-medium latexmk
  • Arch:           sudo pacman -S texlive-basic texlive-latexextra
  • TinyTeX (no root, ~/.TinyTeX):
      curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh
  • Tectonic: https://tectonic-typesetting.github.io
Relaunch the app after installing.
"""


def search_directories() -> list[str]:
    """Every directory worth searching: PATH, GUI-invisible installs, TeX Live globs."""
    env_path = os.environ.get("PATH", "/usr/bin:/bin")
    dirs = env_path.split(":") + process.extra_search_paths()
    home = str(Path.home())
    for g in HOME_GLOBS:
        dirs += process.glob(os.path.join(home, g))
    for g in DIR_GLOBS:
        dirs += process.glob(os.path.expanduser(g))
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _default_exists(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def pick(dirs: list[str], preference: list[str] | None = None,
         exists: Callable[[str], bool] = _default_exists) -> str | None:
    """Pick by engine preference across *all* locations, so a TinyTeX latexmk
    beats a distro tectonic even though only the latter is on a desktop app's PATH."""
    for name in (preference if preference is not None else PREFERENCE):
        for d in dirs:
            p = os.path.join(d, name)
            if exists(p):
                return p
    return None


def find_engine(override: str | None = None) -> str | None:
    """``override`` (Settings) or ``$LATEX_ENGINE`` may be a bare name or a path."""
    dirs = search_directories()
    for candidate in (override, os.environ.get("LATEX_ENGINE")):
        c = (candidate or "").strip()
        if not c:
            continue
        expanded = os.path.expanduser(c)
        if "/" in expanded:
            if _default_exists(expanded):
                return expanded
        else:
            p = pick(dirs, [expanded])
            if p:
                return p
    return pick(dirs)


# --------------------------------------------------------------------------
# Log analysis
# --------------------------------------------------------------------------

class MissingFileDetector:
    _missing_file = re.compile(r"! LaTeX Error: File `([^']+)' not found")
    # "! Font OT1/pcr/m/n/10=pcrr7t at 10.0pt not loadable: Metric (TFM) file not found."
    _missing_font = re.compile(
        r"! Font [^=\n]+=([^\s/]+)[^\n]*?(?:not loadable|Metric \(TFM\) file not found)")
    _kp_missing = re.compile(r"! I can't find file `([^']+)'")
    _pdftex_missing = re.compile(r"pdfTeX (?:error|warning)[^:]*:\s+[^()]*\(file ([^)]+)\):")
    _rerun = re.compile(r"Rerun to get|There were undefined references|Label\(s\) may have changed")

    _file_line_error = re.compile(r"^(\S+\.(?:tex|sty|cls|bib|bbl):\d+: .+)$", re.M)
    _bang_error = re.compile(r"^! (.+)$", re.M)
    _tectonic_error = re.compile(r"^error: (.+)$", re.M)

    @staticmethod
    def tlmgr_needs_self_update(output: str) -> bool:
        """tlmgr's "remote repository is newer than local … update-tlmgr" refusal."""
        o = output.lower()
        return "update-tlmgr" in o or "tlmgr update --self" in o or "repository is newer" in o

    @staticmethod
    def tlmgr_failed(output: str) -> bool:
        """tlmgr exits 0 in some failure modes; look at the text as well."""
        o = output.lower()
        return ("terminating" in o or "not present in repository" in o
                or "cannot find package" in o)

    @classmethod
    def first_error(cls, log: str) -> str | None:
        """The error that stopped the *last* run in a (possibly multi-pass) log."""
        best: tuple[int, str] | None = None
        for regex in (cls._file_line_error, cls._bang_error, cls._tectonic_error):
            matches = list(regex.finditer(log))
            if not matches:
                continue
            m = matches[-1]
            text = m.group(1).strip()
            if best is None or m.start() > best[0]:
                best = (m.start(), text)
        return best[1] if best else None

    @classmethod
    def needs_rerun(cls, log: str) -> bool:
        return cls._rerun.search(log) is not None

    @classmethod
    def missing_files(cls, log: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []

        def add(name: str, ext: str) -> None:
            n = name
            if not n:
                return
            if "." not in n:
                n += ext
            if n not in seen:
                seen.add(n)
                out.append(n)

        for m in cls._missing_file.finditer(log):
            add(m.group(1), ".sty")
        for m in cls._missing_font.finditer(log):
            add(m.group(1), ".tfm")
        for m in cls._kp_missing.finditer(log):
            add(m.group(1), ".tfm")
        for m in cls._pdftex_missing.finditer(log):
            add(m.group(1), "")
        return out

    @staticmethod
    def resolve_packages(tlmgr: str, files: list[str], env: dict[str, str]) -> tuple[list[str], str]:
        """``tlmgr search --global --file /name`` → package names (falls back to the stem)."""
        pkgs: list[str] = []
        seen: set[str] = set()
        search_log = ""
        for f in files:
            r = process.run(tlmgr, ["search", "--global", "--file", "/" + f],
                            environment=env, timeout=90)
            search_log += f"$ tlmgr search --global --file /{f}\n{r.output}"
            found = False
            for line in r.output.split("\n"):
                if line.endswith(":") and not line.startswith(" "):
                    name = line[:-1].strip()
                    if name and name not in seen:
                        seen.add(name)
                        pkgs.append(name)
                        found = True
            if not found:
                stem = re.sub(r"\.(sty|cls|ldf|def|tex|cfg|fd|clo|tfm)$", "", f)
                if stem and stem not in seen:
                    seen.add(stem)
                    pkgs.append(stem)
        return pkgs, search_log


_ENVIRONMENT_FAILURE = re.compile(
    r"File `[^']+' not found"
    r"|not loadable: Metric \(TFM\)"
    r"|I can't find file"
    r"|Undefined control sequence[\s\S]{0,120}\\(?:pdf|Xe|Lua)"
    r"|fontspec[^\n]*(?:XeTeX|LuaTeX|XeLaTeX|LuaLaTeX)"
    r"|requires (?:either )?(?:XeTeX|LuaTeX|XeLaTeX|LuaLaTeX)"
    r"|tlmgr install [^\n]*\n[\s\S]*?(?:error|failed|not found)"
    r"|failed to launch")


def is_environment_failure(log: str) -> bool:
    """Failures caused by the engine's installation rather than the document."""
    return _ENVIRONMENT_FAILURE.search(log) is not None


# --------------------------------------------------------------------------
# Compiler
# --------------------------------------------------------------------------

class LaTeXCompiler:
    def __init__(self, project_path: str | os.PathLike, main_file: str,
                 engine_override: str | None = None):
        self.project_path = str(project_path)
        self.main_file = main_file
        self.engine_override = engine_override

    @property
    def pdf_path(self) -> str:
        stem = os.path.splitext(self.main_file)[0]
        return os.path.join(self.project_path, stem + ".pdf")

    def compile(self, progress: Progress = None) -> CompileResult:
        """Blocking; run on a worker thread.

        Tries the preferred engine; if it fails because of its own environment
        (missing packages that could not be installed, unknown primitives) rather
        than a LaTeX error in the source, the other installed engines are tried
        in preference order.
        """
        if not os.path.exists(os.path.join(self.project_path, self.main_file)):
            return CompileResult(False, f"{self.main_file} not found in {self.project_path}", None, None)
        engine = find_engine(self.engine_override)
        if not engine:
            return CompileResult(False, INSTALL_HELP, None, None)

        result = self._compile_with(engine, progress)
        if result.ok or (self.engine_override or "").strip():
            return result
        if not is_environment_failure(result.log):
            return result

        dirs = search_directories()
        alternatives = []
        for name in PREFERENCE:
            p = pick(dirs, [name])
            if p and p != engine:
                alternatives.append(p)
        for alt in alternatives:
            name = os.path.basename(alt)
            if progress:
                progress(f"{os.path.basename(engine)} could not build this document — trying {name}…")
            nxt = self._compile_with(alt, progress)
            log = (result.log
                   + f"\n\n=== {os.path.basename(engine)} failed for environment reasons; "
                     f"retrying with {name} ===\n" + nxt.log)
            result = CompileResult(nxt.ok, log, nxt.pdf_path, nxt.engine)
            if nxt.ok or not is_environment_failure(nxt.log):
                break
        return result

    def _compile_with(self, engine: str, progress: Progress) -> CompileResult:
        engine_dir = os.path.dirname(engine)
        env = dict(os.environ)
        base_path = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        env["PATH"] = ":".join([engine_dir, base_path] + process.extra_search_paths())
        env["max_print_line"] = "10000"

        name = os.path.basename(engine)
        stem = os.path.splitext(self.main_file)[0]
        if name == "latexmk":
            # -g forces a rebuild even when latexmk's cache still records a failed run.
            cmd = ["-pdf", "-g", "-synctex=1", "-interaction=nonstopmode",
                   "-halt-on-error", "-file-line-error", self.main_file]
        elif name == "tectonic":
            cmd = ["--keep-logs", "--synctex", self.main_file]
        else:
            cmd = ["-synctex=1", "-interaction=nonstopmode", "-halt-on-error",
                   "-file-line-error", self.main_file]

        log = "$ {} {}\n".format(engine, " ".join(cmd))
        if progress:
            progress(f"Running {name}…")

        def run_engine() -> tuple[int, str]:
            r = process.run(engine, cmd, cwd=self.project_path, environment=env, timeout=240)
            return (-1 if r.timed_out else r.status), r.output

        rc, out = run_engine()
        log += out

        # TinyTeX and minimal TeX Live installs: fetch packages as the paper asks.
        tlmgr = process.which("tlmgr", [engine_dir])
        tried: set[str] = set()
        for _ in range(12):
            if rc == 0 and os.path.exists(self.pdf_path):
                break
            if not tlmgr:
                break
            files = MissingFileDetector.missing_files(log)
            if not files:
                break
            pkgs, search_log = MissingFileDetector.resolve_packages(tlmgr, files, env)
            todo = [p for p in pkgs if p not in tried]
            log += "\n--- resolving missing files via tlmgr search ---\n" + search_log
            if not todo:
                break
            log += "--- auto-installing: {} ---\n".format(", ".join(todo))
            if progress:
                progress("Installing {}…".format(", ".join(todo)))
            installed_any = False
            self_updated = False
            for pkg in todo:
                tried.add(pkg)
                r = process.run(tlmgr, ["install", pkg], environment=env, timeout=300)
                log += f"$ tlmgr install {pkg}\n{r.output}\n"
                # An outdated tlmgr refuses to install until it updates itself.
                if not self_updated and MissingFileDetector.tlmgr_needs_self_update(r.output):
                    self_updated = True
                    if progress:
                        progress("Updating tlmgr…")
                    u = process.run(tlmgr, ["update", "--self"], environment=env, timeout=600)
                    log += f"$ tlmgr update --self\n{u.output}\n"
                    r = process.run(tlmgr, ["install", pkg], environment=env, timeout=300)
                    log += f"$ tlmgr install {pkg}\n{r.output}\n"
                if r.ok and not MissingFileDetector.tlmgr_failed(r.output):
                    installed_any = True
            if not installed_any:
                break
            log += "--- retrying compile ---\n"
            if progress:
                progress(f"Retrying {name}…")
            rc, out = run_engine()
            log += out

        # Plain pdflatex needs extra passes for references and bibliographies.
        if name not in ("latexmk", "tectonic") and rc == 0:
            aux_path = os.path.join(self.project_path, stem + ".aux")
            aux = ""
            try:
                aux = Path(aux_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            bibtex = process.which("bibtex", [engine_dir])
            if "\\bibdata" in aux and bibtex:
                if progress:
                    progress("Running bibtex…")
                r = process.run(bibtex, [stem], cwd=self.project_path, environment=env, timeout=120)
                log += f"\n$ bibtex {stem}\n" + r.output
                rc, out = run_engine()
                log += out
            passes = 0
            while rc == 0 and passes < 2 and MissingFileDetector.needs_rerun(out):
                passes += 1
                if progress:
                    progress(f"Re-running {name} for cross-references…")
                rc, out = run_engine()
                log += f"\n--- rerun {passes} ---\n" + out

        ok = rc == 0 and os.path.exists(self.pdf_path)
        if len(log) > 60_000:
            log = "…\n" + log[-60_000:]
        return CompileResult(ok, log, self.pdf_path if ok else None, name)


__all__ = [
    "CompileResult", "LaTeXCompiler", "MissingFileDetector", "ProcessResult",
    "find_engine", "is_environment_failure", "pick", "search_directories",
    "INSTALL_HELP", "PREFERENCE",
]

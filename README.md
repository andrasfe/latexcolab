# LaTeX Colab

A local web app: LaTeX editor on the left, live PDF preview on the right, git
command line at the bottom.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You also need a LaTeX engine. The server tries, in order:

1. `$LATEX_ENGINE` (full path or bare name) — set this if your binary is not on `PATH`
2. `latexmk` on `PATH`
3. `pdflatex` on `PATH`

**With sudo:** `brew install --cask basictex` (~100 MB) or `mactex-no-gui` (full).

**Without sudo** (e.g. shared machine), any of:

```bash
# TinyTeX — installs to ~/Library/TinyTeX, no admin rights
curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh
export PATH="$HOME/Library/TinyTeX/bin/universal-darwin:$PATH"

# Tectonic — single self-contained binary
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh
LATEX_ENGINE=$(pwd)/tectonic python server.py

# conda
conda install -c conda-forge texlive-core
```

If `pdflatex` is already installed but not on the Python process's `PATH`:

```bash
LATEX_ENGINE=/full/path/to/pdflatex python server.py
```

## Run

```bash
python server.py
```

Open <http://localhost:8000>. On first run, `./project/` is created with a
sample `main.tex` and initialized as a git repo.

## Layout

- **Editor (left)** — CodeMirror with the `stex` mode. Autosaves 800ms after
  you stop typing. `Cmd/Ctrl+S` saves now, `Cmd/Ctrl+Enter` generates.
- **PDF (right)** — Native browser PDF viewer in an iframe. Refreshes on each
  generate.
- **CLI (bottom)** — Prefixed with `git`. Type the args only, e.g.
  `status`, `pull`, `add -A`, `commit -m 'msg'`, `push`. Supports `&&` to chain
  sequentially. Subcommand whitelist in `server.py:GIT_ALLOWED`. Arrow keys
  navigate history.

To push/pull from GitHub, configure the remote once via the CLI:

```
remote add origin git@github.com:you/your-repo.git
push -u origin main
```

(SSH keys or a credential helper must already be set up — `GIT_TERMINAL_PROMPT`
is disabled so the server never blocks waiting for a password.)

# LaTeX Colab

A local web app: LaTeX editor on the left, live PDF preview on the right, git
command line at the bottom.

## Quick start — work on a paper from GitHub

This walks through the end-to-end loop: install LaTeX, clone a paper repo,
open it in the app, render it, edit, commit, and push.

### 1. Install pdflatex (Homebrew)

```bash
brew install --cask basictex        # ~100 MB, recommended
# or
brew install --cask mactex-no-gui   # full TeX Live, ~4 GB
```

BasicTeX puts binaries under `/Library/TeX/texbin`. Open a new terminal (or
`eval "$(/usr/libexec/path_helper)"`) so `pdflatex` is on `PATH`. The server
will auto-discover it from common install locations either way.

Don't have admin? Use TinyTeX — see the [Setup](#setup) section below.

### 2. Clone the paper repo

The in-app git CLI is whitelisted and doesn't include `clone` for safety, so do
this once in a terminal:

```bash
cd ~/papers                  # or anywhere you want it
git clone git@github.com:you/your-paper.git
```

### 3. Start the app and open the paper

```bash
cd ~/path/to/latexcolab
source .venv/bin/activate
python server.py
```

Open <http://localhost:8000>. Click the **📂 Open folder** button at the top of
the file tree, browse to `~/papers/your-paper`, and click **Open**. The app
remembers this choice — next launch opens the same folder automatically.

### 4. Generate the PDF

Click **Generate** (or `Cmd/Ctrl+Enter` in the editor). On first run any missing
TeX packages are auto-installed via `tlmgr` and the compile retries until the
PDF builds. The rendered PDF appears in the right pane.

### 5. Edit, commit, push to a branch

Edit in the center pane — autosave kicks in 800 ms after you stop typing
(or `Cmd/Ctrl+S` to force).

In the **git** panel at the bottom-right (the prompt is already prefixed with
`git`), run, in order:

```
checkout -b my-changes              # create + switch to a new branch
status                              # sanity check what changed
add -A
commit -m 'Update introduction'
push -u origin my-changes           # publishes the branch
```

`&&` chains commands sequentially, so you can also do:

```
add -A && commit -m 'Update introduction' && push -u origin my-changes
```

To pull collaborators' changes later: `pull`. Arrow keys cycle history.

> SSH keys (or a credential helper) must already be set up for `push`/`pull` —
> the server sets `GIT_TERMINAL_PROMPT=0` so it never hangs waiting on a
> password prompt.

---

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

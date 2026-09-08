# LaTeX Colab for Linux

Native GTK4 / libadwaita version of the LaTeX Colab app, feature-for-feature
with the [macOS build](../macos/README.md). Two panes: the project file tree on
the left, the PDF preview (or the LaTeX editor) on the right. Click any
paragraph in the PDF to open it in a two-pane rewrite window with an AI clean-up
powered by a local LM Studio model.

## Build

```bash
cd linux
./build-app.sh              # builds ./build/latexcolab — a single executable
./build-app.sh --run        # build, then launch
./build-app.sh --install    # + icon and menu entry in ~/.local
./build-app.sh --check      # unit tests
```

`build/latexcolab` is one self-contained executable file (a Python zipapp) that
runs against the system GTK 4 stack, the way GNOME's own Python apps ship. Copy
it anywhere on your `PATH`. `--install` also drops the icon and a `.desktop`
entry into `~/.local`, so the app shows up in the applications menu; set
`PREFIX=/usr/local` to install system-wide, and `./build-app.sh --uninstall`
removes everything it placed.

For a build that bundles the Python runtime too, `./build-app.sh --pyinstaller`
(needs `pip install pyinstaller`).

### Dependencies

Runtime, all from your distribution:

```bash
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 poppler-utils git
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita poppler-utils git
# Arch
sudo pacman -S python-gobject gtk4 libadwaita poppler git
```

`build-app.sh` checks for these and tells you what is missing. You also need a
LaTeX engine — see [Build settings](#build-settings) below.

### Running without building

```bash
python3 -m latexcolab                  # from this directory
python3 -m latexcolab --project ~/papers/my-paper
```

`LATEXCOLAB_PROJECT=/path/to/paper latexcolab` opens a specific folder without
changing the remembered project; add
`LATEXCOLAB_OPEN_PARAGRAPH=sections/intro.tex:12` to open the paragraph at that
line straight away (scripted testing).

## What it does

| Feature | Where |
|---|---|
| Open a project folder (remembered across launches) | Ctrl+O, header bar, file-tree header |
| Regenerate the PDF (auto-installs missing packages with `tlmgr`) | Ctrl+R, header bar |
| Export the project as a zip (`.git` and build artifacts skipped) | Ctrl+Shift+E, main menu |
| Switch the right pane between PDF and editor | Ctrl+E / Ctrl+1 / Ctrl+2, toggle buttons |
| Zoom the PDF | Ctrl+`+` / Ctrl+`-` / Ctrl+0, Ctrl+scroll |
| Editor autosaves 0.8 s after you stop typing | Ctrl+S saves now |
| Build log | Ctrl+L |
| Paragraph editor | click a paragraph in the PDF, or Ctrl+Shift+P |
| Git: pull / commit / push | Ctrl+Shift+L / Ctrl+Shift+C / Ctrl+Shift+U, Git menu, status bar |
| Settings | Ctrl+, |

The LaTeX engine is chosen by preference — `latexmk`, then `pdflatex`, then
`tectonic` — across every known location at once (`PATH`, `/usr/local/texlive`,
`/opt/texlive`, TinyTeX, conda, `/snap/bin`), so a TinyTeX `latexmk` wins over a
distro `tectonic` even though a desktop-launched app's `PATH` only contains the
latter. Override it in Settings → Build (a name or a full path) or with
`$LATEX_ENGINE`. Every compile runs with `-synctex=1`.

The main file defaults to `main.tex`; otherwise the first `.tex` containing
`\documentclass` is used. Right-click any `.tex` file to make it the main file.

## Paragraph editing

Clicking the PDF maps the click to a source paragraph with SyncTeX (parsed
natively, no `synctex` binary needed). If SyncTeX data is missing, the text
under the click is fuzzy-matched against the paragraphs of every `.tex` file.
A paragraph is a blank-line-delimited block, so what you see is exactly the
LaTeX that will be replaced.

Three granularities:

- **Click** a paragraph to edit the whole paragraph.
- **Alt-click** a word to edit just the sentence it belongs to. Sentence
  boundaries skip abbreviations (e.g., i.e., Fig., et al.), decimals, math
  and citations.
- **Select text** by dragging, then **Alt-click inside the selection** (or
  right-click → *Edit Selection…*) to edit exactly those words. The PDF text
  is aligned word-by-word onto the LaTeX source (ligatures, end-of-line
  hyphenation and rendered citations are handled), and the span is widened so
  that `\emph{…}`-style braces stay balanced. Selections may run into the next
  paragraph of the same file.

The right-click menu also offers *Edit Sentence…* and *Edit Paragraph…*.
Sentence and selection edits keep their own drafts and history, and Apply
replaces only that span inside the paragraph; the window shows the surrounding
words for context.

The paragraph window has:

- **Left** – the paragraph as it is in the document (read-only).
- **Right** – an editable copy. Changed words are highlighted on both sides and
  counted.
- **Max words the AI may add, delete or replace** – slider 0–100. This is a
  hard cap, not a hint: the model's reply is diffed against your text, and only
  that many words of rewording are accepted.
- **Additional instructions** – free text passed to the model (still within
  the word allowance).
- **AI Fix** – proofreads the right side with LM Studio. The model is told to
  act as a proofreader, not a rewriter: fix spelling, grammar, punctuation and
  LaTeX syntax and keep your wording. Whatever comes back is then filtered:
  punctuation, LaTeX and typo/inflection fixes are always kept (they cost
  nothing), rewordings are kept smallest-first until the cap is reached, and
  the rest is reverted to your words and listed as "held back" suggestions
  with an Apply button each, so you decide. At 0 every word you wrote stays.
  The summary line reports how many fixes and how many of the allowed words
  were used. Changes that add or remove LaTeX structure (`\begin`/`\end`,
  sectioning, `\item`, `\label`, citations, references, `\input`…) are never
  applied automatically; they appear as "structural change" suggestions.
- **AI provenance highlighting** – every word and punctuation mark the AI
  produced is shown in violet in *both* panes: in the rewrite, and in the
  document pane for the words it replaced or removed. Your own edits stay
  green (additions) and red (removals). The marks are re-aligned as you keep
  typing, saved with the draft, and kept after Apply, so re-opening a
  paragraph later still shows which of its words came from the AI. A legend
  under the panes counts AI-made words and punctuation.
- **Compare** – asks the model whether the edit still says the same thing.
  You get a verdict (same / minor shift / meaning changed), a plain-language
  list of what changed on the edited side, any meaning differences (claims,
  numbers, hedges, citations added, removed or altered), plus instant
  automatic checks that need no model: dropped or added citations,
  references, labels, environments, math and numbers, and changes in
  negation, hedging or absolute wording. The panel flags itself as stale when
  either side changes afterwards.
- **Save Draft** (Ctrl+Shift+S) – stores the rewrite without touching the
  document. Drafts are also autosaved as you type.
- **Apply** (Ctrl+Return) – writes the rewrite into the `.tex` file, closes the
  window, regenerates the PDF and scrolls the preview to the paragraph
  (SyncTeX forward search), flashing it briefly. Both behaviours can be turned
  off in Settings. Before writing, the rewrite is checked against the
  paragraph's LaTeX structure (environments opened/closed, sectioning, brace
  and `$` balance); a difference such as an extra `\end{abstract}` brings up a
  warning with "Apply Anyway". If the compile then fails, a red banner over the
  PDF shows the first error line with Show Log and Retry buttons, and stays
  until a compile succeeds — the preview keeps showing the previous build until
  then.
- **History** – every Apply keeps the text it replaced. The History window
  lists the earlier versions newest first, shows the selected one next to the
  current text with a word diff, and offers **Load into Rewrite** (copy it to
  the right pane) or **Revert Document to This Version** (write it back into
  the `.tex` file; the current text goes into history, so a revert can itself
  be reverted). History is stored per paragraph in `latexcolab-edits.json` and
  survives hand edits: if the paragraph text no longer matches exactly, the
  record is found through an earlier version or by position and overlap.
- **Revert** / **Discard Draft** – reset the right side.

Drafts live in `latexcolab-edits.json` in the project folder, so they travel
with the project (and are included in the zip) — the file format is identical
to the macOS app's, so a project can move between the two. Reopening a
paragraph shows the two sides in sync if the draft was applied, or the pending
draft if you started editing earlier. Apply verifies the paragraph is still
where it was before writing, so edits made in the editor in between are safe.

## Git

The Git menu (in the header bar, and by clicking the branch in the status bar)
covers the collaboration loop without leaving the app:

- **Pull** (Ctrl+Shift+L) runs `git pull --ff-only`; when the branches have
  diverged it offers `git pull --rebase`. The tree and the open editor file
  reload afterwards.
- **Commit…** (Ctrl+Shift+C) opens a sheet listing changed files with
  checkboxes (all selected by default), a message box, and **Commit** /
  **Commit & Push**. Selected files are staged with `git add -A -- <paths>`.
- **Push** (Ctrl+Shift+U) runs `git push`, or `git push -u origin <branch>` the
  first time. If there is no remote yet it asks for the URL.
- Fetch, switch branch, new branch, set/change remote URL, recent commits, and
  "Initialize Git Repository" for folders that are not repos yet.

The status bar shows the branch, ahead/behind counts and the number of changed
files. Every command's output lands in the build log (Ctrl+L). Git runs with
`GIT_TERMINAL_PROMPT=0`, so SSH keys or a credential helper must be configured;
the app never prompts for passwords.

## AI backend: LM Studio

The app talks to LM Studio's local OpenAI-compatible server, by default
`http://127.0.0.1:1234`. Start the server in LM Studio (Developer tab) and load
a chat model; the app uses whatever is loaded unless you pick a model in
Settings (Ctrl+,), which lists the models LM Studio reports. Temperature and the
default word limit are also in Settings. No API key is involved and nothing
leaves your machine.

Settings are stored in `~/.latexcolab/config.json` — shared with the web app and
the macOS app, so all three remember the same last project.

## Build settings

Install a LaTeX engine with your package manager, or without root:

```bash
# Debian/Ubuntu, a usable subset
sudo apt install texlive-latex-recommended texlive-latex-extra latexmk

# TinyTeX — no root, installs to ~/.TinyTeX, packages fetched on demand
curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh

# Tectonic — single self-contained binary
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh
```

With TinyTeX (or any TeX Live with `tlmgr`), a compile that fails on a missing
`.sty` resolves the package with `tlmgr search --global --file`, installs it and
retries, up to twelve rounds — so a paper usually builds on the first click.

## Layout of this package

- `latexcolab/core/` – engine discovery and compilation, SyncTeX parser,
  paragraph location, edit store, word diff, edit budget, provenance, LM Studio
  client, semantic comparison, git wrapper, zip export, PDF geometry. Pure
  Python, no GTK, fully unit-tested.
- `latexcolab/model.py`, `latexcolab/session.py` – application state and
  behaviour, also GTK-free, so it can be driven headlessly in tests.
- `latexcolab/ui/` – the GTK4 app: main window, file tree, PDF preview,
  `GtkTextView`-based editor with LaTeX highlighting and line numbers,
  paragraph window, settings, git dialogs.
- `tests/` – the macOS test suite ported one-to-one, plus the app-level
  integration tests. `python3 -m unittest discover -s tests -t .`

## How this differs from the macOS build

Same features, different platform libraries:

| macOS | Linux |
|---|---|
| SwiftUI + AppKit | GTK 4 + libadwaita |
| PDFKit (rendering, text selection) | poppler (`pdftocairo`/`pdftoppm` to render, `pdftotext -bbox-layout` for word boxes) drawn through `GskSnapshot` |
| `NSTextView` + `NSRulerView` | `GtkTextView` with a line-number gutter |
| Compression.framework for `.synctex.gz` | Python's `gzip` |
| ⌘-based shortcuts | Ctrl-based shortcuts |
| `.app` bundle, ad-hoc signed | single-file executable + `.desktop` entry |

Everything else — the SyncTeX parser, paragraph location, the word diff, the
edit budget, provenance tracking, the LM Studio prompts, the edits file format —
is a direct port, and the macOS unit tests were ported with it to keep the two
honest.

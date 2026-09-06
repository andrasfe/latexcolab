# LaTeX Colab for macOS

Native SwiftUI/AppKit version of the LaTeX Colab web app. Two panes: the
project file tree on the left, the PDF preview (or the LaTeX editor) on the
right. Click any paragraph in the PDF to open it in a two-pane rewrite window
with an AI clean-up powered by a local LM Studio model.

## Build

Requires Xcode 15+ (macOS 14 SDK). No third-party dependencies.

```bash
cd macos
./build-app.sh --run          # builds build/LaTeX Colab.app and launches it
swift test                    # unit tests (uses your pdflatex if installed)
swift run                     # run without bundling
```

Copy `build/LaTeX Colab.app` to `/Applications` if you like. The bundle is
ad-hoc signed, so it runs locally without a developer certificate.

`LATEXCOLAB_PROJECT=/path/to/paper open -a "LaTeX Colab"` opens a specific
folder without changing the remembered project; add
`LATEXCOLAB_OPEN_PARAGRAPH=sections/intro.tex:12` to open the paragraph at
that line straight away (scripted testing); `LATEXCOLAB_DEBUG=1` logs PDF
click handling to stderr.

## What it does

| Feature | Where |
|---|---|
| Open a project folder (remembered across launches) | ⌘O, toolbar, file-tree header |
| Regenerate the PDF (auto-installs missing packages with `tlmgr`) | ⌘R, toolbar |
| Export the project as a zip (`.git` and build artifacts skipped) | ⇧⌘E, toolbar |
| Switch the right pane between PDF and editor | ⌘E / ⌘1 / ⌘2, segmented control |
| Editor autosaves 0.8 s after you stop typing | ⌘S saves now |
| Build log | ⌘L |
| Paragraph editor | click a paragraph in the PDF, or ⇧⌘P |
| Git: pull / commit / push | ⇧⌘L / ⇧⌘C / ⇧⌘U, Git menu, toolbar, status bar |

The LaTeX engine is chosen by preference — `latexmk`, then `pdflatex`, then
`tectonic` — across every known location at once (`PATH`, `/Library/TeX/texbin`,
Homebrew, TinyTeX, TeX Live, conda), so a TinyTeX `latexmk` wins over a
Homebrew `tectonic` even though a GUI app's `PATH` only contains the latter.
Override it in Settings → Build (a name or a full path) or with
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
- **⌥-click** a word to edit just the sentence it belongs to. Sentence
  boundaries skip abbreviations (e.g., i.e., Fig., et al.), decimals, math
  and citations.
- **Select text** with the mouse, then **⌥-click inside the selection** (or
  right-click → *Edit Selection…*) to edit exactly those words. The PDF text
  is aligned word-by-word onto the LaTeX source (ligatures, end-of-line
  hyphenation and rendered citations are handled), and the span is widened
  so that `\emph{…}`-style braces stay balanced. Selections may run into the
  next paragraph of the same file.

The right-click menu also offers *Edit Sentence…* and *Edit Paragraph…*.
Sentence and selection edits keep their own drafts and history, and Apply
replaces only that span inside the paragraph; the window shows the
surrounding words for context.

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
- **Save Draft** (⇧⌘S) – stores the rewrite without touching the document.
  Drafts are also autosaved as you type.
- **Apply** (⌘↩) – writes the rewrite into the `.tex` file, closes the
  window, regenerates the PDF and scrolls the preview to the paragraph
  (SyncTeX forward search), flashing it briefly. Both behaviours can be turned
  off in Settings. Before writing, the rewrite is checked against the
  paragraph's LaTeX structure (environments opened/closed, sectioning, brace
  and `$` balance); a difference such as an extra `\end{abstract}` brings up
  a warning with "Apply Anyway". If the compile then fails, a red banner over
  the PDF shows the first error line with Show Log and Retry buttons, and
  stays until a compile succeeds — the preview keeps showing the previous
  build until then.
- **History** – every Apply keeps the text it replaced. The History sheet
  lists the earlier versions newest first, shows the selected one next to the
  current text with a word diff, and offers **Load into Rewrite** (copy it to
  the right pane) or **Revert Document to This Version** (write it back into
  the `.tex` file; the current text goes into history, so a revert can itself
  be reverted). History is stored per paragraph in `latexcolab-edits.json`
  and survives hand edits: if the paragraph text no longer matches exactly,
  the record is found through an earlier version or by position and overlap.
- **Revert** / **Discard Draft** – reset the right side.

Drafts live in `latexcolab-edits.json` in the project folder, so they travel
with the project (and are included in the zip). Reopening a paragraph shows the
two sides in sync if the draft was applied, or the pending draft if you started
editing earlier. Apply verifies the paragraph is still where it was before
writing, so edits made in the editor in between are safe.

## Git

The Git menu (also in the toolbar and by clicking the branch in the status bar)
covers the collaboration loop without leaving the app:

- **Pull** (⇧⌘L) runs `git pull --ff-only`; when the branches have diverged it
  offers `git pull --rebase`. The tree and the open editor file reload afterwards.
- **Commit…** (⇧⌘C) opens a sheet listing changed files with checkboxes (all
  selected by default), a message box, and **Commit** / **Commit & Push**.
  Selected files are staged with `git add -A -- <paths>`.
- **Push** (⇧⌘U) runs `git push`, or `git push -u origin <branch>` the first
  time. If there is no remote yet it asks for the URL.
- Fetch, switch branch, new branch, set/change remote URL, recent commits, and
  "Initialize Git Repository" for folders that are not repos yet.

The status bar shows the branch, ahead/behind counts and the number of changed
files. Every command's output lands in the build log (⌘L). Git runs with
`GIT_TERMINAL_PROMPT=0`, so SSH keys or a credential helper must be configured;
the app never prompts for passwords.

## AI backend: LM Studio

The app talks to LM Studio's local OpenAI-compatible server, by default
`http://127.0.0.1:1234`. Start the server in LM Studio (Developer tab) and load
a chat model; the app uses whatever is loaded unless you pick a model in
Settings (⌘,), which lists the models LM Studio reports. Temperature and the
default word limit are also in Settings. No API key is involved and nothing
leaves your machine.

Settings are stored in `~/.latexcolab/config.json` (shared with the web app,
so both remember the same last project).

## Layout of this package

- `Sources/LaTeXColabCore` – engine discovery and compilation, SyncTeX parser,
  paragraph location, edit store, word diff, LM Studio client, zip export.
  Pure Foundation, fully unit-tested.
- `Sources/LaTeXColab` – the SwiftUI app: model, file tree, PDFKit preview,
  NSTextView-based editor with LaTeX highlighting and line numbers, paragraph
  window, settings.
- `Tests/LaTeXColabCoreTests` – unit tests; `testRealCompileAndQuery` compiles
  a document with your installed engine and checks the PDF→source mapping.

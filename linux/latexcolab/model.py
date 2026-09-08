"""Application state and behaviour — port of AppModel.swift, free of GTK.

Views subscribe to change keys with :meth:`AppModel.subscribe`; everything the
UI shows is a plain attribute here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable

from .core import budget as budget_mod
from .core import compare as compare_mod
from .core import filetree, pdfdoc, selection, structure, textmatcher, zipexport
from .core.compiler import (CompileResult, LaTeXCompiler, MissingFileDetector,
                            find_engine)
from .core.config import AppConfig
from .core.edits import (FILE_NAME as EDITS_FILE_NAME, EditsStore, ParagraphEdit,
                         ParagraphVersion, normalize)
from .core.git import PULL_DIVERGED, PULL_SUCCESS, GitClient, GitStatus
from .core.lmstudio import (CleanupRequest, LMStudioError, LMStudioService, NoModel,
                            pick_model)
from .core.paragraphs import ParagraphRange, lines_of, locate, paragraph_containing_line
from .core.paragraphs import replacing as replace_paragraph
from .core.synctex import SyncTeXScanner, locate_file_for_pdf
from .session import (SCOPE_PARAGRAPH, SCOPE_SELECTION, SCOPE_SENTENCE, ParagraphSession)

# Status kinds
NEUTRAL, OK, ERROR, BUSY = "neutral", "ok", "error", "busy"

# View modes
VIEW_PDF, VIEW_EDITOR = "pdf", "editor"

# apply_paragraph outcomes
APPLY_OK = "ok"
APPLY_FAILED = "failed"
APPLY_NEEDS_CONFIRMATION = "structural"


class PDFFocus:
    """A region of the PDF to scroll to and flash, in points with a top-left origin."""

    _counter = 0

    def __init__(self, page_index: int, x: float, y: float, width: float, height: float):
        PDFFocus._counter += 1
        self.id = PDFFocus._counter
        self.page_index = page_index
        self.x = x
        self.y = y
        self.width = width
        self.height = height


def relative_time(date: datetime) -> str:
    """Short "3m ago" style stamp, like RelativeDateTimeFormatter(.short)."""
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - date).total_seconds()
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        n = int(seconds // 60)
        return f"{n} min ago"
    if seconds < 86400:
        n = int(seconds // 3600)
        return f"{n} hr ago"
    if seconds < 2592000:
        n = int(seconds // 86400)
        return f"{n} day{'s' if n != 1 else ''} ago"
    return date.astimezone().strftime("%d %b %Y")


class AppModel:
    def __init__(self, scheduler, config: AppConfig | None = None):
        self.scheduler = scheduler
        self._subscribers: dict[str, list[Callable[[], None]]] = {}

        # Project
        self.project_path: str | None = None
        self.tree: list[filetree.FileNode] = []
        self.selected_path: str | None = None
        self.main_file = "main.tex"
        self.engine_path: str | None = None
        self.engine_name: str | None = None

        # Layout / status
        self.view_mode = VIEW_PDF
        self.show_log = False
        self.log = ""
        self.status = "Open a project folder to begin (Ctrl+O)"
        self.status_kind = NEUTRAL
        self.is_compiling = False

        # Editor
        self.editor_path: str | None = None
        self.editor_text = ""
        self.editor_is_binary = False
        self.editor_dirty = False

        # PDF preview
        self.preview_document: pdfdoc.PDFDocument | None = None
        self.preview_version = 0
        self.preview_is_main = True
        self.pdf_stale = False
        #: First error line of the last failed compile; shown as a persistent banner.
        self.compile_error: str | None = None
        self.pdf_focus: PDFFocus | None = None
        self.pdf_notice: str | None = None
        self.pdf_notice_is_error = False
        self._pdf_notice_handle: int | None = None
        self._pending_focus: tuple[str, ParagraphRange] | None = None

        # Paragraph editing
        self.paragraph_session: ParagraphSession | None = None
        self.paragraph_window_request = 0
        self.pending_draft_count = 0

        self.config = config if config is not None else AppConfig.load()
        self.lm_models: list = []
        self.lm_models_error: str | None = None

        # Git
        self.git = GitStatus()
        self.git_branches: list[str] = []
        self.git_busy = False
        self._push_after_remote = False
        self._git_refresh_handle: int | None = None

        self.edits_store: EditsStore | None = None
        self._autosave_handle: int | None = None
        self._draft_save_handle: int | None = None
        self._sync_scanner: SyncTeXScanner | None = None
        self._sync_scanner_stamp: float | None = None
        self._remember_project = True

        #: Hooks the UI installs.
        self.on_open_settings: Callable[[], None] | None = None
        self.on_ask_rebase: Callable[[], None] | None = None
        self.on_ask_remote: Callable[[], None] | None = None

        self.refresh_engine()

    # -- observation -------------------------------------------------------

    def subscribe(self, key: str, callback: Callable[[], None]) -> None:
        self._subscribers.setdefault(key, []).append(callback)

    def notify(self, *keys: str) -> None:
        for key in keys:
            for callback in list(self._subscribers.get(key, ())):
                callback()

    # -- helpers -----------------------------------------------------------

    @property
    def main_stem(self) -> str:
        return os.path.splitext(self.main_file)[0]

    @property
    def main_pdf_path(self) -> str | None:
        if not self.project_path:
            return None
        return os.path.join(self.project_path, self.main_stem + ".pdf")

    @property
    def edits_file_name(self) -> str:
        return EDITS_FILE_NAME

    def set_status(self, text: str, kind: str = NEUTRAL) -> None:
        self.status = text
        self.status_kind = kind
        self.notify("status")

    def refresh_engine(self) -> None:
        self.engine_path = find_engine(self.config.latex_engine)
        self.engine_name = os.path.basename(self.engine_path) if self.engine_path else None
        self.notify("status")

    def save_config(self) -> None:
        try:
            self.config.save()
        except OSError as exc:
            self.set_status(f"Could not save settings: {exc}", ERROR)
        self.notify("config")

    def notice(self, text: str, error: bool = False, seconds: float = 4) -> None:
        self.pdf_notice = text
        self.pdf_notice_is_error = error
        self.scheduler.cancel(self._pdf_notice_handle)

        def clear() -> None:
            self.pdf_notice = None
            self._pdf_notice_handle = None
            self.notify("pdf")

        self._pdf_notice_handle = self.scheduler.after(seconds, clear)
        self.notify("pdf")

    def append_log(self, text: str) -> None:
        self.log += ("" if not self.log else "\n") + text.strip("\n")
        self.notify("log")

    def toggle_view_mode(self) -> None:
        self.set_view_mode(VIEW_EDITOR if self.view_mode == VIEW_PDF else VIEW_PDF)

    def set_view_mode(self, mode: str) -> None:
        if mode != self.view_mode:
            self.view_mode = mode
            self.notify("view")

    def set_show_log(self, value: bool) -> None:
        if value != self.show_log:
            self.show_log = value
            self.notify("log")

    # -- project -----------------------------------------------------------

    def open_project(self, path: str, remember: bool | None = None) -> None:
        self.flush_editor()
        if self.paragraph_session:
            self.persist_draft(self.paragraph_session)
        if remember is not None:
            self._remember_project = remember
        self.project_path = os.path.normpath(os.path.abspath(path))
        self.selected_path = None
        self.editor_path = None
        self.editor_text = ""
        self.editor_is_binary = False
        self.editor_dirty = False
        self.preview_document = None
        self.preview_is_main = True
        self.pdf_stale = False
        self.compile_error = None
        self._sync_scanner = None
        self.paragraph_session = None
        self.log = ""

        self.main_file = self.config.main_file(self.project_path) or "main.tex"
        self.reload_tree()
        self._detect_main_file()

        store = EditsStore(self.project_path)
        try:
            store.load()
        except (OSError, ValueError) as exc:
            self.set_status(f"Could not read {EDITS_FILE_NAME}: {exc}", ERROR)
        self.edits_store = store
        self.refresh_pending_count()

        if self._remember_project:
            self.config.last_project = self.project_path
            self.save_config()
        self._remember_project = True

        pdf = self.main_pdf_path
        if pdf and os.path.exists(pdf):
            self.load_pdf()
            self.view_mode = VIEW_PDF
        else:
            self.view_mode = VIEW_EDITOR
        self.open_file(self.main_file, switch_to_editor=False)
        self.notify("project", "tree", "editor", "pdf", "view", "paragraph", "log")
        self.refresh_git_status()
        self.set_status(f"Opened {os.path.basename(self.project_path)}", OK)

    def reload_tree(self) -> None:
        self.tree = filetree.build(self.project_path) if self.project_path else []
        self.notify("tree")

    def _detect_main_file(self) -> None:
        if not self.project_path:
            return
        if os.path.exists(os.path.join(self.project_path, self.main_file)):
            return
        if os.path.exists(os.path.join(self.project_path, "main.tex")):
            self.main_file = "main.tex"
            return
        for node in filetree.flatten_files(self.tree):
            if node.file_extension != "tex":
                continue
            try:
                with open(node.path, "rb") as fh:
                    head = fh.read(8192).decode("utf-8", "replace")
            except OSError:
                continue
            if "\\documentclass" in head:
                self.main_file = node.id
                return

    def set_main_file(self, path: str) -> None:
        if not self.project_path:
            return
        self.main_file = path
        self.config.set_main_file(path, self.project_path)
        self.save_config()
        self._sync_scanner = None
        pdf = self.main_pdf_path
        if pdf and os.path.exists(pdf):
            self.load_pdf()
        else:
            self.preview_document = None
            self.notify("pdf")
        self.set_status(f"Main file: {path}", OK)
        self.notify("tree", "project")

    def select_from_tree(self, path: str) -> None:
        node = filetree.find(path, self.tree)
        if node and not node.is_directory:
            self.open_file(path)

    def reveal_in_file_manager(self, path: str) -> None:
        if not self.project_path:
            return
        target = os.path.join(self.project_path, path)
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        from .core import process
        opener = process.which("xdg-open")
        if opener:
            process.run(opener, [folder], timeout=10)

    # -- editor ------------------------------------------------------------

    def open_file(self, rel: str, switch_to_editor: bool = True) -> None:
        if not self.project_path:
            return
        path = os.path.join(self.project_path, rel)
        if not os.path.isfile(path):
            return

        if rel.lower().endswith(".pdf"):
            try:
                doc = pdfdoc.PDFDocument(path)
            except pdfdoc.PDFError as exc:
                self.set_status(f"Could not open {rel}: {exc}", ERROR)
                return
            self.preview_document = doc
            main = self.main_pdf_path
            self.preview_is_main = bool(main and os.path.normpath(path) == os.path.normpath(main))
            self.preview_version += 1
            self.set_view_mode(VIEW_PDF)
            self.notify("pdf")
            self.set_status(
                f"Showing {rel}" if self.preview_is_main
                else f"Showing {rel} (paragraph editing only works on the main PDF)", NEUTRAL)
            return

        if rel == self.editor_path:
            if switch_to_editor:
                self.set_view_mode(VIEW_EDITOR)
            return

        self.flush_editor()
        text = filetree.read_text(path) if filetree.is_text_file(os.path.basename(path)) else None
        if text is not None:
            self.editor_text = text
            self.editor_is_binary = False
        else:
            self.editor_text = ""
            self.editor_is_binary = True
        self.editor_path = rel
        self.editor_dirty = False
        if switch_to_editor:
            self.set_view_mode(VIEW_EDITOR)
        if self.selected_path != rel:
            self.selected_path = rel
        self.notify("editor")

    def editor_text_changed(self, text: str) -> None:
        """Called by the editor view on every keystroke."""
        if self.editor_is_binary or self.editor_path is None:
            return
        self.editor_text = text
        self.editor_dirty = True
        if self.status_kind != BUSY:
            self.set_status("Editing…")
        self.scheduler.cancel(self._autosave_handle)
        self._autosave_handle = self.scheduler.after(0.8, self.save_editor)
        self.notify("editor-dirty")

    def flush_editor(self) -> None:
        self.scheduler.cancel(self._autosave_handle)
        self._autosave_handle = None
        if self.editor_dirty:
            self.save_editor()

    def save_editor(self) -> None:
        if not (self.project_path and self.editor_path
                and not self.editor_is_binary and self.editor_dirty):
            return
        self.scheduler.cancel(self._autosave_handle)
        self._autosave_handle = None
        path = os.path.join(self.project_path, self.editor_path)
        try:
            _write_atomically(path, self.editor_text)
        except OSError as exc:
            self.set_status(f"Save failed: {exc}", ERROR)
            return
        self.editor_dirty = False
        low = self.editor_path.lower()
        if low.endswith(".tex") or low.endswith(".bib"):
            self.pdf_stale = self.preview_document is not None
            self.notify("pdf")
        if self.status_kind != BUSY:
            self.set_status(f"Saved {self.editor_path}", OK)
        self.notify("editor-dirty")
        self.schedule_git_refresh()

    # -- compile -----------------------------------------------------------

    def regenerate(self) -> None:
        if not self.project_path or self.is_compiling:
            return
        self.flush_editor()
        self.is_compiling = True
        self.set_status(f"Compiling {self.main_file}…", BUSY)
        compiler = LaTeXCompiler(self.project_path, self.main_file, self.config.latex_engine)

        def work() -> CompileResult:
            return compiler.compile(
                lambda msg: self.scheduler.on_main(lambda: self.set_status(msg, BUSY)))

        def done(result: CompileResult | None, error: BaseException | None) -> None:
            if error is not None or result is None:
                self.is_compiling = False
                self.set_status(f"Compile failed: {error}", ERROR)
                return
            self._finish_compile(result)

        self.scheduler.run_background(work, done)

    def _finish_compile(self, result: CompileResult) -> None:
        self.is_compiling = False
        self.log = result.log
        self.notify("log")
        if result.engine:
            self.engine_name = result.engine
        if result.ok:
            self._sync_scanner = None
            self.compile_error = None
            self.load_pdf()
            self.pdf_stale = False
            self.set_view_mode(VIEW_PDF)
            self.set_status(f"PDF generated with {result.engine or 'LaTeX'}", OK)
            self._focus_pending_paragraph()
        else:
            applied = self._pending_focus
            self._pending_focus = None
            self.set_show_log(True)
            first = MissingFileDetector.first_error(result.log) or "see the build log for details"
            prefix = ("Compile failed: " if applied is None else
                      f"Compile failed after applying {applied[0]} lines "
                      f"{applied[1].start_line}–{applied[1].end_line}: ")
            self.compile_error = prefix + first
            self.set_view_mode(VIEW_PDF)
            self.set_status("Compile failed — the PDF still shows the previous build", ERROR)
        self.notify("pdf")
        self.reload_tree()
        self.schedule_git_refresh()

    def _focus_pending_paragraph(self) -> None:
        """SyncTeX forward search for the paragraph that was just applied."""
        target = self._pending_focus
        self._pending_focus = None
        if not target or not self.project_path:
            return
        scanner = self.load_sync_scanner()
        if not scanner:
            return
        path = os.path.join(self.project_path, target[0])
        hit = scanner.display_query(path, target[1].start_line, target[1].end_line)
        if not hit:
            return
        page, rect = hit
        self.pdf_focus = PDFFocus(page - 1, rect.min_x, rect.min_y, rect.width, rect.height)
        self.notice(f"Updated {target[0]} lines {target[1].start_line}–{target[1].end_line}"
                    " — showing the new PDF")

    def load_pdf(self) -> None:
        path = self.main_pdf_path
        if not path or not os.path.exists(path):
            return
        try:
            self.preview_document = pdfdoc.PDFDocument(path)
        except pdfdoc.PDFError as exc:
            self.append_log(f"Could not read the PDF: {exc}")
            return
        self.preview_is_main = True
        self.preview_version += 1
        self.notify("pdf")

    # -- zip ---------------------------------------------------------------

    def export_zip(self, destination: str) -> None:
        if not self.project_path:
            return
        project = self.project_path
        self.flush_editor()
        self.set_status(f"Zipping {os.path.basename(project)}…", BUSY)

        def work() -> None:
            zipexport.export(project, destination)

        def done(_result, error: BaseException | None) -> None:
            if error is None:
                self.set_status(f"Exported {os.path.basename(destination)}", OK)
            else:
                self.set_status(f"Zip failed: {error}", ERROR)
                self.append_log(str(error))
                self.set_show_log(True)

        self.scheduler.run_background(work, done)

    # -- PDF click → paragraph ---------------------------------------------

    def load_sync_scanner(self) -> SyncTeXScanner | None:
        if not self.project_path:
            return None
        pdf = self.main_pdf_path
        if not pdf:
            return None
        path = locate_file_for_pdf(pdf)
        if not path:
            return None
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            stamp = 0.0
        if self._sync_scanner is not None and self._sync_scanner_stamp == stamp:
            return self._sync_scanner
        try:
            scanner = SyncTeXScanner.from_file(path, self.project_path)
        except (OSError, ValueError) as exc:
            self.append_log(f"SyncTeX read failed: {exc}")
            return None
        self._sync_scanner = scanner
        self._sync_scanner_stamp = stamp
        return scanner

    def resolve_paragraph(self, page_index: int, x: float, y: float,
                          nearby_text: str | None):
        """One PDF point → ``(file, ParagraphRange, line|None, how)``.

        ``x``/``y`` are PDF points from the page's top-left corner.
        """
        if not self.project_path:
            return None
        scanner = self.load_sync_scanner()
        if scanner:
            loc = scanner.edit_query(page_index + 1, x, y)
            if loc and loc.path.startswith(self.project_path + os.sep):
                source = filetree.read_text(loc.path)
                if source is not None:
                    para = paragraph_containing_line(source, loc.line)
                    if para:
                        return (filetree.relative_path(loc.path, self.project_path),
                                para, loc.line, "SyncTeX")
        if nearby_text:
            sources = []
            for node in filetree.flatten_files(self.tree):
                if node.file_extension != "tex":
                    continue
                text = filetree.read_text(node.path)
                if text is not None:
                    sources.append((node.id, text))
            m = textmatcher.best_match(nearby_text, sources)
            if m:
                return (m.file, m.paragraph, None, "text match")
        return None

    def handle_pdf_click(self, page_index: int, x: float, y: float,
                         nearby_text: str | None, mode: tuple) -> None:
        """``mode`` is ``("paragraph",)``, ``("sentence", word)`` or
        ``("selection", text, end_page_index, end_x, end_y)``."""
        if not self.project_path:
            return
        if not self.preview_is_main:
            self.notice(f"Paragraph editing works on the main PDF "
                        f"({self.main_stem}.pdf) only", error=True)
            return
        if self.is_compiling:
            self.notice("Wait for the compile to finish", error=True)
            return
        self.flush_editor()

        target = self.resolve_paragraph(page_index, x, y, nearby_text)
        if target is None:
            why = ("no SyncTeX data — press Regenerate to rebuild the PDF with it"
                   if self._sync_scanner is None
                   else "nothing in the sources matched that spot")
            self.notice(f"Couldn't map that click to a paragraph ({why})",
                        error=True, seconds=6)
            self.set_status("Click did not map to a paragraph", ERROR)
            return

        file, rng, line, how = target
        span = None
        scope = SCOPE_PARAGRAPH
        fallback_note = ""

        kind = mode[0]
        if kind == SCOPE_SENTENCE:
            word = mode[1] if len(mode) > 1 else None
            line_offset = selection.offset_of_line(
                max(1, (line or rng.start_line) - rng.start_line + 1), rng.text)
            span = None
            if word:
                span = selection.sentence_range_for_pdf_word(rng.text, word, line_offset)
            if span is None:
                span = selection.sentence_range(rng.text, line_offset)
            scope = SCOPE_SENTENCE
        elif kind == SCOPE_SELECTION:
            text, end_page, end_x, end_y = mode[1], mode[2], mode[3], mode[4]
            # The selection may run into a later paragraph of the same file.
            end = self.resolve_paragraph(end_page, end_x, end_y, None)
            if end and end[0] == file and end[1].end_line > rng.end_line:
                source = filetree.read_text(os.path.join(self.project_path, file))
                if source is not None:
                    ls = lines_of(source)
                    if end[1].end_line <= len(ls):
                        rng = ParagraphRange(
                            rng.start_line, end[1].end_line,
                            "\n".join(ls[rng.start_line - 1:end[1].end_line]))
            span = selection.source_range_for_pdf_text(text, rng.text)
            if span is not None:
                scope = SCOPE_SELECTION
            else:
                fallback_note = (" · couldn't match the selected text exactly, "
                                 "opened the whole paragraph")

        if span is not None and 0 < span[1] < len(rng.text):
            self.open_paragraph_session(file, rng, span, scope)
        else:
            self.open_paragraph_session(file, rng)
            scope = SCOPE_PARAGRAPH

        label = {SCOPE_PARAGRAPH: "paragraph", SCOPE_SENTENCE: "sentence",
                 SCOPE_SELECTION: "selection"}[scope]
        where = f"{label} in {file} lines {rng.start_line}–{rng.end_line}"
        self.notice(f"Opened {where} via {how}" + fallback_note
                    + (" · PDF is out of date, regenerate for exact positions"
                       if self.pdf_stale else ""))
        self.set_status(where, OK)

    # -- paragraph sessions ------------------------------------------------

    def open_paragraph(self, file: str, line: int, scope: str = SCOPE_PARAGRAPH) -> None:
        """Open the paragraph containing ``line`` of ``file``, or the sentence
        that starts on that line."""
        if not self.project_path:
            return
        source = filetree.read_text(os.path.join(self.project_path, file))
        para = paragraph_containing_line(source, line) if source is not None else None
        if para is None:
            self.set_status(f"No paragraph at {file}:{line}", ERROR)
            return
        if scope == SCOPE_SENTENCE:
            offset = selection.offset_of_line(line - para.start_line + 1, para.text)
            r = selection.sentence_range(para.text, offset)
            if 0 < r[1] < len(para.text):
                self.open_paragraph_session(file, para, r, SCOPE_SENTENCE)
                return
        self.open_paragraph_session(file, para)

    def open_paragraph_session(self, file: str, rng: ParagraphRange,
                               span: tuple[int, int] | None = None,
                               scope: str = SCOPE_PARAGRAPH) -> None:
        text = rng.text[span[0]:span[0] + span[1]] if span else rng.text
        current = self.paragraph_session
        if (current and current.file == file and current.scope == scope
                and normalize(current.original) == normalize(text)):
            current.range = rng
            current.container = rng.text
            current.span_offset = span[0] if span else 0
            self.paragraph_window_request += 1
            self.notify("paragraph-window")
            return
        if current:
            self.persist_draft(current)

        existing: ParagraphEdit | None = None
        adopted = False
        store = self.edits_store
        if store:
            m = store.match(file, text, rng.start_line, partial=scope != SCOPE_PARAGRAPH)
            if m:
                e = m.edit
                if not m.exact:
                    # The document text changed outside the app (hand edit or manual
                    # revert): keep the record and its history, follow the new text.
                    was_in_sync = e.is_in_sync
                    e.original = text
                    if was_in_sync:
                        e.draft = text
                    e.line_hint = rng.start_line
                    e.updated_at = datetime.now(timezone.utc)
                    store.upsert(e)
                    _try(store.save)
                    adopted = True
                existing = e

        session = ParagraphSession(
            file=file, rng=rng, original=text,
            draft=existing.draft if existing else text,
            max_words=existing.max_words if existing else self.config.default_max_words,
            instructions=existing.instructions if existing else "",
            edit_id=existing.id if existing else None,
            applied=existing.applied if existing else False,
            scope=scope, container=rng.text, span_offset=span[0] if span else 0)
        session.history = list(existing.history) if existing else []
        if existing and existing.provenance:
            # Stored marks describe the record's draft text; re-align if the
            # session's draft differs (e.g. after adoption).
            session.provenance = (existing.provenance
                                  if existing.draft == session.draft
                                  else existing.provenance.remapped(
                                      existing.draft, session.draft, False))
        session.on_changed = lambda: self.schedule_draft_save(session)

        if existing:
            n = len(existing.history)
            versions = "" if not n else f" · {n} earlier version{'' if n == 1 else 's'} in History"
            if adopted:
                session.note("The document text changed outside the app; "
                             f"picked up the new text.{versions}")
            elif session.is_in_sync:
                head = (f"In sync — this rewrite was applied "
                        f"{relative_time(existing.applied_at or existing.updated_at)}."
                        if existing.applied else "In sync with the document.")
                session.note(head + versions)
            else:
                session.note(f"Draft from {relative_time(existing.updated_at)} differs from "
                             f"the document — not applied yet.{versions}")
        else:
            session.note(f"Edit the right side, use AI Fix, then Apply to write it into {file}.")

        self.paragraph_session = session
        self.paragraph_window_request += 1
        self.notify("paragraph", "paragraph-window")

    def schedule_draft_save(self, session: ParagraphSession) -> None:
        self.scheduler.cancel(self._draft_save_handle)
        self._draft_save_handle = self.scheduler.after(
            0.6, lambda: self.persist_draft(session))

    def persist_draft(self, session: ParagraphSession, force: bool = False) -> bool:
        """Write the session into latexcolab-edits.json.

        A record is only created once the draft differs from the document (or on
        Apply, with ``force``).
        """
        store = self.edits_store
        if not store:
            return False
        stamp = datetime.now(timezone.utc)
        existing = store.find_id(session.edit_id) if session.edit_id else None
        if existing is not None:
            existing.original = session.original
            existing.draft = session.draft
            existing.max_words = session.max_words
            existing.instructions = session.instructions
            existing.line_hint = session.range.start_line
            existing.applied = session.applied
            existing.updated_at = stamp
            existing.history = list(session.history)
            existing.partial = session.is_partial
            existing.provenance = None if session.provenance.is_empty else session.provenance
            store.upsert(existing)
        else:
            if not force and session.is_in_sync:
                return False
            e = ParagraphEdit(
                file=session.file, original=session.original, draft=session.draft,
                applied=session.applied, line_hint=session.range.start_line,
                max_words=session.max_words, instructions=session.instructions,
                created_at=stamp, updated_at=stamp, history=list(session.history),
                partial=session.is_partial,
                provenance=None if session.provenance.is_empty else session.provenance)
            store.upsert(e)
            session.edit_id = e.id
        try:
            store.save()
        except OSError as exc:
            session.note(f"Could not write {EDITS_FILE_NAME}: {exc}", error=True)
            return False
        session.saved_at = stamp
        session.touch()
        self.refresh_pending_count()
        return True

    def save_draft_now(self, session: ParagraphSession) -> None:
        self.scheduler.cancel(self._draft_save_handle)
        if self.persist_draft(session, force=True):
            session.note(f"Draft saved to {EDITS_FILE_NAME} (not applied to the document).")

    def discard_draft(self, session: ParagraphSession) -> None:
        self.scheduler.cancel(self._draft_save_handle)
        if session.edit_id and self.edits_store:
            self.edits_store.remove(session.edit_id)
            _try(self.edits_store.save)
            session.edit_id = None
        session.applied = False
        session.draft = session.original
        session.note("Draft discarded — right side reset to the document text.")
        self.refresh_pending_count()

    def apply_paragraph(self, session: ParagraphSession, note: str | None = None,
                        allow_structural: bool = False) -> str:
        """Write the rewrite into the .tex file.

        Returns :data:`APPLY_OK`, :data:`APPLY_FAILED`, or
        :data:`APPLY_NEEDS_CONFIRMATION` (with ``session.pending_structural`` set)
        when the rewrite changes LaTeX structure and the caller has not confirmed.
        """
        if not self.project_path:
            return APPLY_FAILED
        self.scheduler.cancel(self._draft_save_handle)
        if self.editor_path == session.file:
            self.flush_editor()
        path = os.path.join(self.project_path, session.file)
        source = filetree.read_text(path)
        if source is None:
            session.note(f"Could not read {session.file}.", error=True)
            return APPLY_FAILED
        rng = locate(session.container, session.range, source)
        if rng is None:
            session.note(f"The paragraph no longer exists in {session.file} as it was when "
                         "opened. Close this window and click it again.", error=True)
            return APPLY_FAILED

        new_text = session.draft.replace("\r\n", "\n").strip("\n")

        # For a sentence/selection, find the span inside the (re-read) paragraph.
        container = rng.text
        span_start, span_length = 0, len(container)
        if session.is_partial:
            occurrences = []
            search = 0
            while search < len(container):
                found = container.find(session.original, search)
                if found < 0:
                    break
                occurrences.append(found)
                search = found + max(1, len(session.original))
            if not occurrences:
                session.note(f"The {session.scope_label.lower()} no longer exists in that "
                             "paragraph as it was when opened. Close this window and click "
                             "it again.", error=True)
                return APPLY_FAILED
            span_start = min(occurrences, key=lambda o: abs(o - session.span_offset))
            span_length = len(session.original)
            replaced_text = session.original
        else:
            replaced_text = rng.text

        if normalize(new_text) == normalize(replaced_text):
            session.note("The rewrite is identical to the document; nothing to apply.")
            return APPLY_FAILED

        differences = structure.differences(replaced_text, new_text)
        if differences and not allow_structural:
            session.pending_structural = differences
            session.note("Not applied — the rewrite " + "; ".join(differences)
                         + ". Fix the right side, or apply anyway from the alert.",
                         error=True)
            return APPLY_NEEDS_CONFIRMATION
        session.pending_structural = []

        if session.is_partial:
            new_container = (container[:span_start] + new_text
                             + container[span_start + span_length:])
        else:
            new_container = new_text
        updated = replace_paragraph(rng, source, new_container)
        try:
            _write_atomically(path, updated)
        except OSError as exc:
            session.note(f"Could not write {session.file}: {exc}", error=True)
            return APPLY_FAILED

        stamp = datetime.now(timezone.utc)
        session.history.append(ParagraphVersion(replaced_text, stamp, note))
        line_count = len(new_container.split("\n"))
        session.range = ParagraphRange(rng.start_line, rng.start_line + line_count - 1,
                                       new_container)
        session.container = new_container
        session.span_offset = span_start
        session.original = new_text
        session.set_draft_silently(new_text)
        session.applied = True
        session.dropped_suggestions = []
        session.show_comparison = False
        self.persist_draft(session, force=True)
        if session.edit_id and self.edits_store:
            e = self.edits_store.find_id(session.edit_id)
            if e:
                e.applied_at = stamp
                self.edits_store.upsert(e)
                _try(self.edits_store.save)
        if self.editor_path == session.file:
            self.editor_text = updated
            self.editor_dirty = False
            self.notify("editor")
        self.pdf_stale = True
        self.notify("pdf")
        self.schedule_git_refresh()
        self._pending_focus = (session.file, session.range)
        auto = self.config.auto_regenerate_after_apply
        session.note(f"Applied {session.scope_label.lower()} to {session.file} lines "
                     f"{session.range.start_line}–{session.range.end_line}."
                     + (" Regenerating PDF…" if auto else ""))
        self.set_status(f"Applied paragraph edit to {session.file}"
                        + (" — regenerating PDF…" if auto else ""), OK)
        if auto:
            self.regenerate()
        else:
            self.notice(f"Applied to {session.file}. Press Regenerate (Ctrl+R) "
                        "to refresh the PDF.")
        return APPLY_OK

    def revert_paragraph(self, session: ParagraphSession, version: ParagraphVersion,
                         allow_structural: bool = False) -> str:
        """Put an earlier version back into the document (recorded as a new
        history entry, so the revert itself can be undone)."""
        session.draft = version.text
        stamp = version.replaced_at.astimezone().strftime("%d %b %Y at %H:%M")
        return self.apply_paragraph(session, note=f"reverted to the version from before {stamp}",
                                    allow_structural=allow_structural)

    # -- LM Studio ---------------------------------------------------------

    def refresh_lm_models(self, done: Callable[[], None] | None = None) -> None:
        service = LMStudioService(self.config.lmstudio_url)

        def work():
            return service.list_models()

        def finished(models, error: BaseException | None) -> None:
            if error is None:
                self.lm_models = models or []
                self.lm_models_error = None
            else:
                self.lm_models = []
                self.lm_models_error = str(error)
            self.notify("lmmodels")
            if done:
                done()

        self.scheduler.run_background(work, finished)

    def _resolve_lm_model(self, service: LMStudioService,
                          session: ParagraphSession) -> str | None:
        """The configured model, or whatever LM Studio currently has loaded.

        Runs on a worker thread.
        """
        model = self.config.model
        if not model:
            self.scheduler.on_main(
                lambda: session.note("Asking LM Studio which model is loaded…"))
            try:
                models = service.list_models()
            except LMStudioError:
                models = []
            if models:
                self.lm_models = models
                self.scheduler.on_main(lambda: self.notify("lmmodels"))
                model = pick_model(models) or ""
        if not model:
            def fail() -> None:
                session.note(str(NoModel()), error=True)
                if self.on_open_settings:
                    self.on_open_settings()
            self.scheduler.on_main(fail)
            return None
        return model

    def compare_paragraph(self, session: ParagraphSession) -> None:
        """Instant deterministic checks, then the model's semantic verdict."""
        if session.is_comparing:
            return
        session.integrity_issues = compare_mod.integrity(session.original, session.draft)
        session.compared_original = session.original
        session.compared_draft = session.draft
        session.show_comparison = True
        if session.is_in_sync:
            session.comparison = compare_mod.identical_comparison()
            session.touch()
            return
        session.comparison = None
        session.is_comparing = True
        session.touch()

        cfg = self.config
        service = LMStudioService(cfg.lmstudio_url)
        original, draft = session.original, session.draft
        temperature = min(cfg.temperature, 0.2)

        def work():
            model = self._resolve_lm_model(service, session)
            if not model:
                return None
            self.scheduler.on_main(
                lambda: session.note(f"Comparing the two sides with {model}…"))
            req = compare_mod.CompareRequest(original, draft, model, temperature)
            return model, compare_mod.compare(service, req)

        def done(result, error: BaseException | None) -> None:
            session.is_comparing = False
            if error is not None:
                session.note(str(error), error=True)
            elif result is not None:
                model, comparison = result
                session.comparison = comparison
                shown = comparison.model or model
                session.note(f"Comparison done: {comparison.headline.lower()} · {shown}")
            session.touch()

        self.scheduler.run_background(work, done)

    def ai_fix(self, session: ParagraphSession) -> None:
        if session.is_busy:
            return
        session.is_busy = True
        session.touch()
        cfg = self.config
        service = LMStudioService(cfg.lmstudio_url)
        paragraph = session.draft
        max_words = session.max_words
        instructions = session.instructions
        temperature = cfg.temperature

        def work():
            model = self._resolve_lm_model(service, session)
            if not model:
                return None
            self.scheduler.on_main(lambda: session.note(
                f"Rewriting with {model} via LM Studio (max {max_words} words)…"))
            req = CleanupRequest(paragraph, max_words, instructions, model, temperature)
            return model, service.clean_up(req)

        def done(result, error: BaseException | None) -> None:
            session.is_busy = False
            if error is not None:
                session.note(str(error), error=True)
                session.touch()
                return
            if result is None:
                session.touch()
                return
            model, cleanup = result
            # Enforce the budget: keep syntax fixes, keep rewordings only up to the limit.
            budget = budget_mod.constrain(paragraph, cleanup.text, max_words)
            session.next_draft_change_is_ai = True
            session.draft = budget.text
            session.dropped_suggestions = list(budget.dropped)
            shown = cleanup.model or model
            parts = [
                f"{budget.free_fixes} syntax/punctuation fix"
                f"{'' if budget.free_fixes == 1 else 'es'}",
                f"{budget.words_used} of {max_words} allowed word change"
                f"{'' if max_words == 1 else 's'} used",
            ]
            if budget.dropped:
                words = sum(s.cost for s in budget.dropped)
                parts.append(f"{len(budget.dropped)} rewording"
                             f"{'' if len(budget.dropped) == 1 else 's'} ({words} words) "
                             "held back — see below")
            if not budget.applied and not budget.dropped:
                parts = ["nothing to fix"]
            summary = " · ".join(parts)
            session.last_fix_summary = summary
            session.note(f"AI fix: {summary} · {shown}")
            session.touch()

        self.scheduler.run_background(work, done)

    def apply_suggestion(self, suggestion, session: ParagraphSession) -> None:
        """Apply one held-back rewording the author explicitly wants."""
        updated = budget_mod.apply_suggestion(suggestion, session.draft)
        if updated is None:
            session.note(f"Couldn't find “{suggestion.from_text.strip()}” in the rewrite "
                         "any more.", error=True)
            session.dropped_suggestions = [s for s in session.dropped_suggestions
                                           if s.id != suggestion.id]
            session.touch()
            return
        session.next_draft_change_is_ai = True
        session.draft = updated
        session.dropped_suggestions = [s for s in session.dropped_suggestions
                                       if s.id != suggestion.id]
        session.note(f"Applied “{suggestion.label}”")
        session.touch()

    # -- git ---------------------------------------------------------------

    @property
    def git_client(self) -> GitClient | None:
        return GitClient(self.project_path) if self.project_path else None

    def schedule_git_refresh(self) -> None:
        self.scheduler.cancel(self._git_refresh_handle)
        self._git_refresh_handle = self.scheduler.after(1.0, self.refresh_git_status)

    def refresh_git_status(self) -> None:
        client = self.git_client
        if not client:
            self.git = GitStatus()
            self.git_branches = []
            self.notify("git")
            return

        def work():
            status = client.status()
            branches = client.branches() if status.is_repo else []
            return status, branches

        def done(result, error: BaseException | None) -> None:
            if error is None and result is not None:
                self.git, self.git_branches = result
                self.notify("git")

        self.scheduler.run_background(work, done)

    def _reload_editor_if_changed(self) -> None:
        """Re-read the open editor file when git changed it on disk (pull, checkout)."""
        if not (self.project_path and self.editor_path
                and not self.editor_is_binary and not self.editor_dirty):
            return
        text = filetree.read_text(os.path.join(self.project_path, self.editor_path))
        if text is not None and text != self.editor_text:
            self.editor_text = text
            self.notify("editor")

    def _after_git_change(self) -> None:
        self.reload_tree()
        self._reload_editor_if_changed()
        pdf = self.main_pdf_path
        if pdf and os.path.exists(pdf):
            self.pdf_stale = True
            self.notify("pdf")
        self.refresh_git_status()

    def _run_git(self, label: str, command: str, op, completion=None) -> None:
        """Run one git operation off the main thread, log it, and refresh state."""
        client = self.git_client
        if not client or self.git_busy:
            return
        self.flush_editor()
        self.git_busy = True
        self.set_status(f"{label}…", BUSY)
        self.notify("git")

        def done(result, error: BaseException | None) -> None:
            self.git_busy = False
            if error is not None:
                self.set_status(f"{label} failed: {error}", ERROR)
                self.notify("git")
                return
            self.append_log(f"$ git {command}\n{result.output}")
            if result.ok:
                self.set_status(f"{label} done", OK)
            else:
                self.set_status(f"{label} failed — see the log", ERROR)
                self.set_show_log(True)
            self._after_git_change()
            if completion:
                completion(result)

        self.scheduler.run_background(lambda: op(client), done)

    def git_init(self) -> None:
        self._run_git("Initialize repository", "init", lambda c: c.init_repository())

    def git_fetch(self) -> None:
        self._run_git("Fetch", "fetch --prune", lambda c: c.fetch())

    def git_pull(self, rebase: bool = False) -> None:
        client = self.git_client
        if not client or self.git_busy:
            return
        self.flush_editor()
        self.git_busy = True
        self.set_status("Pulling with rebase…" if rebase else "Pulling…", BUSY)
        self.notify("git")

        def done(result, error: BaseException | None) -> None:
            self.git_busy = False
            if error is not None or result is None:
                self.set_status(f"Pull failed: {error}", ERROR)
                self.notify("git")
                return
            outcome, r = result
            self.append_log(f"$ git pull {'--rebase' if rebase else '--ff-only'}\n{r.output}")
            self._after_git_change()
            if outcome == PULL_SUCCESS:
                self.set_status(f"Pulled {self.git.upstream or 'from remote'}", OK)
            elif outcome == PULL_DIVERGED:
                self.set_status("Local and remote branches have diverged", ERROR)
                if self.on_ask_rebase:
                    self.on_ask_rebase()
            else:
                self.set_status("Pull failed — see the log", ERROR)
                self.set_show_log(True)

        self.scheduler.run_background(lambda: client.pull(rebase), done)

    def git_commit(self, message: str, paths: list[str] | None, then_push: bool) -> None:
        msg = message.strip()
        if not msg:
            return

        def after(result) -> None:
            if result.ok and then_push:
                self.git_push()

        self._run_git("Commit", f'add … && git commit -m "{msg}"',
                      lambda c: c.commit(msg, paths), after)

    def git_push(self) -> None:
        if not self.git.has_remote:
            self._push_after_remote = True
            if self.on_ask_remote:
                self.on_ask_remote()
            return
        branch = self.git.branch
        has_upstream = self.git.has_upstream
        self._run_git("Push", "push" if has_upstream else f"push -u origin {branch or 'HEAD'}",
                      lambda c: c.push(branch, has_upstream))

    def git_set_remote(self, url: str) -> None:
        trimmed = url.strip()
        if not trimmed:
            return
        push = self._push_after_remote
        self._push_after_remote = False

        def after(result) -> None:
            if result.ok and push:
                self.git_push()

        self._run_git("Set remote", f"remote add/set-url origin {trimmed}",
                      lambda c: c.set_remote(trimmed), after)

    def git_checkout(self, branch: str, create: bool) -> None:
        name = branch.strip()
        if not name:
            return
        self._run_git(f"Create branch {name}" if create else f"Switch to {name}",
                      f"checkout -b {name}" if create else f"checkout {name}",
                      lambda c: c.checkout(name, create))

    def git_log(self) -> None:
        self._run_git("Recent commits", "log --oneline -n 20", lambda c: c.log(20),
                      lambda _r: self.set_show_log(True))

    # -- misc --------------------------------------------------------------

    def refresh_pending_count(self) -> None:
        self.pending_draft_count = self.edits_store.pending_count if self.edits_store else 0
        self.notify("pending")

    def restore_last_project(self) -> None:
        """Open ``$LATEXCOLAB_PROJECT`` if set, else the remembered folder."""
        forced = os.environ.get("LATEXCOLAB_PROJECT")
        if forced:
            path = os.path.expanduser(forced)
            if os.path.isdir(path):
                self.open_project(path, remember=False)
                spec = os.environ.get("LATEXCOLAB_OPEN_PARAGRAPH")
                if spec:
                    self._open_paragraph_spec(spec)
                return
        last = self.config.last_project
        if last and os.path.isdir(last):
            self.open_project(last)

    def _open_paragraph_spec(self, spec: str) -> None:
        """``sections/intro.tex:12[:sentence]`` — for scripted testing."""
        parts = spec.split(":")
        scope = SCOPE_PARAGRAPH
        if parts and parts[-1] == "sentence":
            scope = SCOPE_SENTENCE
            parts = parts[:-1]
        if len(parts) < 2:
            return
        try:
            line = int(parts[-1])
        except ValueError:
            return
        file = ":".join(parts[:-1])
        self.scheduler.after(1.0, lambda: self.open_paragraph(file, line, scope))


def _write_atomically(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _try(fn) -> None:
    try:
        fn()
    except (OSError, ValueError):
        pass


__all__ = ["AppModel", "PDFFocus", "APPLY_OK", "APPLY_FAILED", "APPLY_NEEDS_CONFIRMATION",
           "VIEW_PDF", "VIEW_EDITOR", "NEUTRAL", "OK", "ERROR", "BUSY", "relative_time"]

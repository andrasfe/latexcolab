"""Main window: file tree | PDF-or-editor, build log, status bar.

Port of MainView.swift.
"""

from __future__ import annotations

import os

from gi.repository import Adw, Gio, GLib, Gtk

from ..core.compiler import INSTALL_HELP
from ..model import BUSY, ERROR, OK, VIEW_EDITOR, VIEW_PDF, AppModel
from . import gitui
from .codeview import CodeView
from .filetreeview import FileTreeView
from .pdfview import PdfView

_HINT = ("Click a paragraph · Alt-click a sentence · select text, then Alt-click it "
         "(or right-click) to edit just that.")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, model: AppModel):
        super().__init__(application=application, title="LaTeX Colab",
                         default_width=1360, default_height=880)
        self.model = model
        self.set_size_request(900, 560)

        self.toasts = Adw.ToastOverlay()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts.set_child(root)
        self.set_content(self.toasts)

        root.append(self._build_header())

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_vexpand(True)
        panes.set_wide_handle(True)
        self.file_tree = FileTreeView(model, self.choose_project_folder)
        self.file_tree.set_size_request(200, -1)
        panes.set_start_child(self.file_tree)
        panes.set_resize_start_child(False)
        panes.set_shrink_start_child(False)
        panes.set_position(260)

        self.right_split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.right_split.set_wide_handle(True)
        self.right_split.set_start_child(self._build_right_pane())
        self.right_split.set_end_child(self._build_log_pane())
        self.right_split.set_resize_start_child(True)
        self.right_split.set_shrink_start_child(False)
        panes.set_end_child(self.right_split)
        panes.set_resize_end_child(True)
        root.append(panes)

        root.append(Gtk.Separator())
        root.append(self._build_status_bar())

        model.subscribe("status", self._refresh_status)
        model.subscribe("project", self._refresh_titles)
        model.subscribe("project", self._refresh_git)
        model.subscribe("view", self._refresh_view)
        model.subscribe("editor", self._refresh_editor)
        model.subscribe("editor-dirty", self._refresh_editor_header)
        model.subscribe("log", self._refresh_log)
        model.subscribe("pdf", self._refresh_pdf)
        model.subscribe("git", self._refresh_git)
        model.subscribe("pending", self._refresh_status)

        # The macOS app refreshes git when the app becomes active; the
        # equivalent here is the window regaining focus.
        self.connect("notify::is-active", self._on_active)

        self._pdf_version = -1
        self._focus_id = -1
        self._refresh_all()

    def _on_active(self, *_args) -> None:
        if self.is_active() and self.model.project_path:
            self.model.refresh_git_status()

    # -- construction ------------------------------------------------------

    def _build_header(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="LaTeX Colab", subtitle="")
        header.set_title_widget(self.window_title)

        open_button = Gtk.Button(icon_name="folder-open-symbolic")
        open_button.set_tooltip_text("Open a project folder (Ctrl+O)")
        open_button.connect("clicked", lambda *_: self.choose_project_folder())
        header.pack_start(open_button)

        toggle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        toggle_box.add_css_class("linked")
        self.pdf_toggle = Gtk.ToggleButton(label="PDF", active=True)
        self.pdf_toggle.set_tooltip_text("Show the PDF preview (Ctrl+1)")
        self.editor_toggle = Gtk.ToggleButton(label="Editor")
        self.editor_toggle.set_tooltip_text("Show the LaTeX editor (Ctrl+2)")
        self.editor_toggle.set_group(self.pdf_toggle)
        self.pdf_toggle.connect("toggled", self._on_view_toggled)
        toggle_box.append(self.pdf_toggle)
        toggle_box.append(self.editor_toggle)
        header.pack_start(toggle_box)

        self.regenerate_button = Gtk.Button()
        self.regenerate_content = Adw.ButtonContent(
            icon_name="view-refresh-symbolic", label="Regenerate")
        self.regenerate_button.set_child(self.regenerate_content)
        self.regenerate_button.set_tooltip_text("Compile the main file to PDF (Ctrl+R)")
        self.regenerate_button.connect("clicked", lambda *_: self.model.regenerate())
        header.pack_end(self._menu_button())
        header.pack_end(self.log_toggle_button())
        header.pack_end(self.git_button())
        header.pack_end(self.regenerate_button)
        return header

    def log_toggle_button(self) -> Gtk.Widget:
        self.log_toggle = Gtk.ToggleButton(icon_name="utilities-terminal-symbolic")
        self.log_toggle.set_tooltip_text("Show the build log (Ctrl+L)")
        self.log_toggle.connect("toggled",
                                lambda b: self.model.set_show_log(b.get_active()))
        return self.log_toggle

    def git_button(self) -> Gtk.Widget:
        self.git_menu_button = Gtk.MenuButton(icon_name="media-playlist-shuffle-symbolic")
        self.git_menu_button.set_tooltip_text("Pull, commit, push")
        return self.git_menu_button

    def _menu_button(self) -> Gtk.Widget:
        menu = Gio.Menu()
        project = Gio.Menu()
        project.append("Open Folder…", "app.open-folder")
        project.append("Export Zip…", "app.export-zip")
        menu.append_section(None, project)
        build = Gio.Menu()
        build.append("Regenerate PDF", "app.regenerate")
        build.append("Refresh File Tree", "app.refresh-tree")
        build.append("Paragraph Editor", "app.paragraph-editor")
        menu.append_section(None, build)
        view = Gio.Menu()
        view.append("Zoom In", "app.zoom-in")
        view.append("Zoom Out", "app.zoom-out")
        view.append("Fit Width", "app.zoom-fit")
        menu.append_section(None, view)
        app_section = Gio.Menu()
        app_section.append("Settings", "app.settings")
        app_section.append("About LaTeX Colab", "app.about")
        app_section.append("Quit", "app.quit")
        menu.append_section(None, app_section)
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        button.set_tooltip_text("Main menu")
        return button

    def _build_right_pane(self) -> Gtk.Widget:
        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)

        # PDF pane, with the banner overlay.
        self.pdf_view = PdfView(self.model.handle_pdf_click)
        overlay = Gtk.Overlay()
        overlay.set_child(self.pdf_view)

        self.banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.banner.set_halign(Gtk.Align.CENTER)
        self.banner.set_valign(Gtk.Align.START)
        self.banner.set_margin_top(8)
        self.banner.add_css_class("osd")
        self.banner.add_css_class("toolbar")
        self.banner_label = Gtk.Label(wrap=True, selectable=True, xalign=0.0)
        self.banner_label.set_max_width_chars(90)
        self.banner.append(self.banner_label)
        self.banner_log = Gtk.Button(label="Show Log")
        self.banner_log.connect("clicked", lambda *_: self.model.set_show_log(True))
        self.banner.append(self.banner_log)
        self.banner_retry = Gtk.Button(label="Retry")
        self.banner_retry.connect("clicked", lambda *_: self.model.regenerate())
        self.banner.append(self.banner_retry)
        overlay.add_overlay(self.banner)
        self.content_stack.add_named(overlay, "pdf")

        self.no_pdf = Adw.StatusPage(
            title="No PDF yet", icon_name="x-office-document-symbolic",
            description="Compile the main file to see the preview here.")
        button = Gtk.Button(label="Regenerate")
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: self.model.regenerate())
        self.no_pdf.set_child(button)
        self.content_stack.add_named(self.no_pdf, "no-pdf")

        welcome = Adw.StatusPage(
            title="LaTeX Colab", icon_name="x-office-document-symbolic",
            description="Open a project folder to preview and edit its PDF.")
        open_button = Gtk.Button(label="Open Folder…")
        open_button.add_css_class("suggested-action")
        open_button.set_halign(Gtk.Align.CENTER)
        open_button.connect("clicked", lambda *_: self.choose_project_folder())
        welcome.set_child(open_button)
        self.content_stack.add_named(welcome, "welcome")

        self.content_stack.add_named(self._build_editor_pane(), "editor")
        return self.content_stack

    def _build_editor_pane(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.set_margin_start(10)
        head.set_margin_end(10)
        head.set_margin_top(5)
        head.set_margin_bottom(5)
        icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        head.append(icon)
        self.editor_label = Gtk.Label(xalign=0.0, ellipsize=3)
        self.editor_label.add_css_class("monospace")
        head.append(self.editor_label)
        self.editor_dirty_label = Gtk.Label(label="• unsaved")
        self.editor_dirty_label.add_css_class("warning")
        self.editor_dirty_label.add_css_class("caption")
        head.append(self.editor_dirty_label)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)
        self.set_main_button = Gtk.Button(label="Set as main")
        self.set_main_button.add_css_class("flat")
        self.set_main_button.connect(
            "clicked", lambda *_: self.model.set_main_file(self.model.editor_path))
        head.append(self.set_main_button)
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("flat")
        self.save_button.connect("clicked", lambda *_: self.model.save_editor())
        head.append(self.save_button)
        box.append(head)
        box.append(Gtk.Separator())

        self.editor_stack = Gtk.Stack()
        self.editor_stack.set_vexpand(True)
        self.editor = CodeView(editable=True, show_line_numbers=True, font_size=11,
                               on_change=self.model.editor_text_changed)
        self.editor_stack.add_named(self.editor, "text")
        self.editor_stack.add_named(Adw.StatusPage(
            title="No file open", icon_name="text-x-generic-symbolic",
            description="Pick a file in the tree on the left."), "none")
        self.editor_stack.add_named(Adw.StatusPage(
            title="Binary file", icon_name="package-x-generic-symbolic",
            description="This file can't be edited as text."), "binary")
        box.append(self.editor_stack)
        return box

    def _build_log_pane(self) -> Gtk.Widget:
        self.log_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.set_margin_start(10)
        head.set_margin_end(6)
        head.set_margin_top(4)
        head.set_margin_bottom(4)
        title = Gtk.Label(label="Build log", xalign=0.0)
        title.add_css_class("caption-heading")
        head.append(title)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)
        clear = Gtk.Button(label="Clear")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self._clear_log())
        head.append(clear)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text("Hide log (Ctrl+L)")
        close.connect("clicked", lambda *_: self.model.set_show_log(False))
        head.append(close)
        self.log_pane.append(head)
        self.log_pane.append(Gtk.Separator())

        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.set_left_margin(8)
        self.log_view.set_top_margin(6)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.log_view)
        scroller.set_vexpand(True)
        self.log_scroller = scroller
        self.log_pane.append(scroller)
        self.log_pane.set_size_request(-1, 90)
        self.log_pane.set_visible(False)
        return self.log_pane

    def _build_status_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        bar.set_margin_start(12)
        bar.set_margin_end(12)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        bar.add_css_class("toolbar")

        self.status_spinner = Gtk.Spinner()
        bar.append(self.status_spinner)
        self.status_dot = Gtk.Label(label="●")
        bar.append(self.status_dot)
        self.status_label = Gtk.Label(xalign=0.0, ellipsize=2)
        self.status_label.add_css_class("caption")
        self.status_label.set_hexpand(True)
        bar.append(self.status_label)

        self.git_status_button = Gtk.Button()
        self.git_status_button.add_css_class("flat")
        self.git_status_content = Gtk.Label()
        self.git_status_content.add_css_class("caption")
        self.git_status_button.set_child(self.git_status_content)
        self.git_status_button.connect("clicked", lambda *_: self.open_commit_dialog())
        bar.append(self.git_status_button)

        self.main_file_label = Gtk.Label()
        self.main_file_label.add_css_class("caption")
        self.main_file_label.add_css_class("dim-label")
        self.main_file_label.set_tooltip_text(
            "Main file (right-click a .tex file to change)")
        bar.append(self.main_file_label)

        self.engine_label = Gtk.Label()
        self.engine_label.add_css_class("caption")
        self.engine_label.set_tooltip_text("LaTeX engine")
        bar.append(self.engine_label)

        self.drafts_button = Gtk.Button()
        self.drafts_button.add_css_class("flat")
        self.drafts_label = Gtk.Label()
        self.drafts_label.add_css_class("caption")
        self.drafts_button.set_child(self.drafts_label)
        self.drafts_button.connect("clicked", lambda *_: self._open_paragraph_editor())
        bar.append(self.drafts_button)
        return bar

    # -- actions -----------------------------------------------------------

    def _on_view_toggled(self, *_args) -> None:
        self.model.set_view_mode(VIEW_PDF if self.pdf_toggle.get_active() else VIEW_EDITOR)

    def _clear_log(self) -> None:
        self.model.log = ""
        self.model.notify("log")

    def _open_paragraph_editor(self) -> None:
        self.model.paragraph_window_request += 1
        self.model.notify("paragraph-window")

    def choose_project_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a LaTeX project folder")
        if self.model.project_path:
            dialog.set_initial_folder(Gio.File.new_for_path(self.model.project_path))

        def done(source, result) -> None:
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.model.open_project(folder.get_path())

        dialog.select_folder(self, None, done)

    def choose_zip_destination(self) -> None:
        if not self.model.project_path:
            return
        dialog = Gtk.FileDialog(title="Export project as zip")
        dialog.set_initial_name(os.path.basename(self.model.project_path) + ".zip")

        def done(source, result) -> None:
            try:
                target = source.save_finish(result)
            except GLib.Error:
                return
            if target and target.get_path():
                self.model.export_zip(target.get_path())

        dialog.save(self, None, done)

    def open_commit_dialog(self) -> None:
        model = self.model
        if not model.project_path:
            return
        if not model.git.is_repo:
            gitui.confirm(self, "This folder is not a git repository",
                          "Initialize one here so you can commit and push?",
                          "Initialize Repository", model.git_init)
            return
        model.flush_editor()
        model.refresh_git_status()
        GLib.timeout_add(120, lambda: (gitui.GitCommitDialog(self, model).present(), False)[1])

    def ask_rebase(self) -> None:
        gitui.confirm(
            self, "Local and remote branches have diverged",
            "A fast-forward pull is not possible because both sides have new commits. "
            "Rebase your local commits on top of the remote branch?",
            "Pull with Rebase", lambda: self.model.git_pull(rebase=True))

    def ask_remote(self) -> None:
        gitui.prompt(
            self,
            "Change remote URL" if self.model.git.has_remote else "Set remote URL",
            "The URL of the \"origin\" remote, e.g. git@github.com:you/paper.git. "
            "SSH keys or a credential helper must already be set up; the app never "
            "prompts for passwords.",
            "git@github.com:you/your-paper.git", "Save",
            self.model.git.remote_url or "", self.model.git_set_remote)

    def ask_new_branch(self) -> None:
        gitui.prompt(self, "New branch",
                     "Creates the branch from the current commit and switches to it.",
                     "my-changes", "Create", "",
                     lambda name: self.model.git_checkout(name, create=True))

    # -- refresh -----------------------------------------------------------

    def _refresh_all(self) -> None:
        self._refresh_titles()
        self._refresh_status()
        self._refresh_view()
        self._refresh_editor()
        self._refresh_log()
        self._refresh_pdf()
        self._refresh_git()

    def _refresh_titles(self) -> None:
        model = self.model
        self.window_title.set_title(
            os.path.basename(model.project_path) if model.project_path else "LaTeX Colab")
        self.window_title.set_subtitle(
            (model.editor_path or "") if model.view_mode == VIEW_EDITOR
            else (f"{model.main_stem}.pdf" if model.preview_document else ""))

    def _refresh_status(self) -> None:
        model = self.model
        self.status_label.set_text(model.status)
        busy = model.status_kind == BUSY
        self.status_dot.set_visible(not busy)
        self.status_spinner.set_visible(busy)
        if busy:
            self.status_spinner.start()
        else:
            self.status_spinner.stop()
        for css in ("success", "error", "warning", "dim-label"):
            self.status_dot.remove_css_class(css)
            self.status_label.remove_css_class(css)
        if model.status_kind == OK:
            self.status_dot.add_css_class("success")
        elif model.status_kind == ERROR:
            self.status_dot.add_css_class("error")
            self.status_label.add_css_class("error")
        else:
            self.status_dot.add_css_class("dim-label")

        has_project = model.project_path is not None
        self.main_file_label.set_visible(has_project)
        self.engine_label.set_visible(has_project)
        self.drafts_button.set_visible(has_project)
        self.main_file_label.set_text(model.main_file)
        self.engine_label.set_text(model.engine_name or "no engine")
        if model.engine_name:
            self.engine_label.remove_css_class("error")
            self.engine_label.add_css_class("dim-label")
        else:
            self.engine_label.add_css_class("error")
            self.engine_label.set_tooltip_text(INSTALL_HELP)
        n = model.pending_draft_count
        self.drafts_label.set_text(f"{n} draft{'' if n == 1 else 's'}")
        self.drafts_button.set_tooltip_text(
            f"Rewrites saved in {model.edits_file_name} that are not applied yet")
        self.regenerate_button.set_sensitive(has_project and not model.is_compiling)
        self.regenerate_content.set_label("Compiling…" if model.is_compiling
                                          else "Regenerate")
        self._refresh_titles()

    def _refresh_view(self) -> None:
        model = self.model
        if model.view_mode == VIEW_EDITOR:
            self.content_stack.set_visible_child_name("editor")
            if not self.editor_toggle.get_active():
                self.editor_toggle.set_active(True)
        else:
            if model.project_path is None:
                self.content_stack.set_visible_child_name("welcome")
            elif model.preview_document is None:
                self.content_stack.set_visible_child_name("no-pdf")
            else:
                self.content_stack.set_visible_child_name("pdf")
            if not self.pdf_toggle.get_active():
                self.pdf_toggle.set_active(True)
        self._refresh_titles()

    def _refresh_editor(self) -> None:
        model = self.model
        if model.editor_path is None:
            self.editor_stack.set_visible_child_name("none")
        elif model.editor_is_binary:
            self.editor_stack.set_visible_child_name("binary")
        else:
            self.editor_stack.set_visible_child_name("text")
            if self.editor.text != model.editor_text:
                self.editor.set_text(model.editor_text)
        self._refresh_editor_header()

    def _refresh_editor_header(self) -> None:
        model = self.model
        self.editor_label.set_text(model.editor_path or "No file open")
        self.editor_dirty_label.set_visible(model.editor_dirty)
        self.save_button.set_sensitive(model.editor_dirty)
        path = model.editor_path
        self.set_main_button.set_visible(
            bool(path and path != model.main_file and path.endswith(".tex")))
        self.file_tree.list_view.queue_draw()

    def _refresh_log(self) -> None:
        model = self.model
        self.log_view.get_buffer().set_text(model.log or "No output yet.")
        self.log_pane.set_visible(model.show_log)
        if self.log_toggle.get_active() != model.show_log:
            self.log_toggle.set_active(model.show_log)
        if model.show_log:
            def scroll() -> bool:
                adj = self.log_scroller.get_vadjustment()
                adj.set_value(max(0.0, adj.get_upper() - adj.get_page_size()))
                return False
            GLib.idle_add(scroll)

    def _refresh_pdf(self) -> None:
        model = self.model
        if model.preview_document is not None and model.preview_version != self._pdf_version:
            self._pdf_version = model.preview_version
            self.pdf_view.set_document(model.preview_document)
        elif model.preview_document is None:
            self.pdf_view.set_document(None)

        if model.pdf_focus is not None and model.pdf_focus.id != self._focus_id:
            self._focus_id = model.pdf_focus.id
            f = model.pdf_focus
            self.pdf_view.show_focus(f.page_index, f.x, f.y, f.width, f.height)

        for css in ("error", "warning", "success"):
            self.banner.remove_css_class(css)
        if model.compile_error:
            self.banner_label.set_text(model.compile_error)
            self.banner.add_css_class("error")
            self.banner_log.set_visible(True)
            self.banner_retry.set_visible(True)
            self.banner_retry.set_sensitive(not model.is_compiling)
            self.banner.set_visible(True)
        elif model.pdf_notice:
            self.banner_label.set_text(model.pdf_notice)
            self.banner.add_css_class("error" if model.pdf_notice_is_error else "success")
            self.banner_log.set_visible(False)
            self.banner_retry.set_visible(False)
            self.banner.set_visible(True)
        elif model.pdf_stale:
            self.banner_label.set_text(
                "Sources changed since this PDF was built — Regenerate (Ctrl+R) to refresh.")
            self.banner.add_css_class("warning")
            self.banner_log.set_visible(False)
            self.banner_retry.set_visible(False)
            self.banner.set_visible(True)
        elif model.preview_is_main and model.preview_document is not None:
            self.banner_label.set_text(_HINT)
            self.banner_log.set_visible(False)
            self.banner_retry.set_visible(False)
            self.banner.set_visible(True)
        else:
            self.banner.set_visible(False)
        self._refresh_view()

    def _refresh_git(self) -> None:
        model = self.model
        self.git_menu_button.set_menu_model(gitui.build_git_menu(model))
        self.git_menu_button.set_sensitive(model.project_path is not None)
        if model.git.is_repo:
            parts = [model.git.branch or "detached"]
            if model.git.ahead:
                parts.append(f"↑{model.git.ahead}")
            if model.git.behind:
                parts.append(f"↓{model.git.behind}")
            if model.git.is_dirty:
                parts.append(f"· {len(model.git.changes)} changed")
            self.git_status_content.set_text(" ".join(parts))
            tooltip = [f"Branch {model.git.branch or '(detached)'}"]
            if model.git.upstream:
                tooltip.append(f"tracking {model.git.upstream}")
            tooltip.append(f"origin {model.git.remote_url}" if model.git.remote_url
                           else "no remote")
            tooltip.append("click to commit (Ctrl+Shift+C)")
            self.git_status_button.set_tooltip_text(" · ".join(tooltip))
            self.git_status_button.set_visible(True)
        elif model.project_path:
            self.git_status_content.set_text("not a git repo")
            self.git_status_button.set_tooltip_text(
                "Click to initialize a git repository here")
            self.git_status_button.set_visible(True)
        else:
            self.git_status_button.set_visible(False)

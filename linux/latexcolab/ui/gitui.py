"""Git menu, commit sheet and prompts (port of GitViews.swift)."""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from ..model import AppModel


def build_git_menu(model: AppModel) -> Gio.Menu:
    """The Git menu, shared by the header-bar button and the main menu."""
    menu = Gio.Menu()
    if model.project_path is None:
        menu.append("Open a project folder first", "app.open-folder")
        return menu
    if not model.git.is_repo:
        menu.append("Initialize Git Repository", "app.git-init")
        return menu

    top = Gio.Menu()
    top.append("Pull", "app.git-pull")
    top.append("Commit…", "app.git-commit")
    top.append("Push", "app.git-push")
    menu.append_section(None, top)

    middle = Gio.Menu()
    middle.append("Fetch", "app.git-fetch")
    branches = Gio.Menu()
    for b in model.git_branches:
        if b != model.git.branch:
            branches.append(b, f"app.git-checkout::{b}")
    if branches.get_n_items():
        middle.append_submenu("Switch Branch", branches)
    middle.append("New Branch…", "app.git-new-branch")
    middle.append("Change Remote URL…" if model.git.has_remote else "Set Remote URL…",
                  "app.git-remote")
    menu.append_section(None, middle)

    bottom = Gio.Menu()
    bottom.append("Show Recent Commits", "app.git-log")
    menu.append_section(None, bottom)
    return menu


class GitCommitDialog(Adw.Window):
    """Stage-and-commit sheet: pick files, write a message, commit or commit-and-push."""

    def __init__(self, parent: Gtk.Window, model: AppModel):
        super().__init__(transient_for=parent, modal=True,
                         default_width=660, default_height=560, title="Commit")
        self.model = model
        self._checks: dict[str, Gtk.CheckButton] = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title=f"Commit to {model.git.branch or 'detached HEAD'}",
            subtitle=(f"↑{model.git.ahead} ↓{model.git.behind} vs {model.git.upstream}"
                      if model.git.upstream else "no upstream yet")))
        outer.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(14)
        body.set_margin_end(14)
        body.set_vexpand(True)

        changes = model.git.changes
        if not changes:
            status = Adw.StatusPage(title="Nothing to commit",
                                    description="The working tree is clean.",
                                    icon_name="object-select-symbolic")
            status.set_vexpand(True)
            body.append(status)
        else:
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.all_check = Gtk.CheckButton(label="All files", active=True)
            self.all_check.connect("toggled", self._toggle_all)
            top.append(self.all_check)
            self.count_label = Gtk.Label(xalign=1.0)
            self.count_label.set_hexpand(True)
            self.count_label.add_css_class("dim-label")
            top.append(self.count_label)
            body.append(top)

            listbox = Gtk.ListBox()
            listbox.add_css_class("boxed-list")
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            for change in changes:
                row = Adw.ActionRow(title=change.path, subtitle=change.summary)
                row.set_title_lines(1)
                check = Gtk.CheckButton(active=True)
                check.connect("toggled", lambda *_: self._update_count())
                row.add_prefix(check)
                if change.original_path:
                    row.set_subtitle(f"{change.summary} ← {change.original_path}")
                if change.is_conflict:
                    row.add_css_class("error")
                listbox.append(row)
                self._checks[change.path] = check
            scroller = Gtk.ScrolledWindow()
            scroller.set_child(listbox)
            scroller.set_vexpand(True)
            scroller.set_min_content_height(160)
            body.append(scroller)

        label = Gtk.Label(label="Commit message", xalign=0.0)
        label.add_css_class("dim-label")
        body.append(label)
        self.message = Gtk.TextView()
        self.message.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.message.set_top_margin(6)
        self.message.set_bottom_margin(6)
        self.message.set_left_margin(6)
        self.message.set_right_margin(6)
        self.message.get_buffer().connect("changed", lambda *_: self._update_count())
        frame = Gtk.Frame()
        frame.set_child(self.message)
        frame.set_size_request(-1, 90)
        body.append(frame)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        buttons.append(cancel)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        self.commit_button = Gtk.Button(label="Commit")
        self.commit_button.connect("clicked", lambda *_: self._commit(False))
        buttons.append(self.commit_button)
        self.commit_push_button = Gtk.Button(label="Commit & Push")
        self.commit_push_button.add_css_class("suggested-action")
        self.commit_push_button.connect("clicked", lambda *_: self._commit(True))
        buttons.append(self.commit_push_button)
        body.append(buttons)

        outer.append(body)
        self.set_content(outer)
        self._update_count()

    def _toggle_all(self, button: Gtk.CheckButton) -> None:
        for check in self._checks.values():
            check.set_active(button.get_active())

    def _selected(self) -> list[str]:
        return sorted(p for p, c in self._checks.items() if c.get_active())

    def _update_count(self) -> None:
        selected = self._selected()
        if self._checks:
            self.count_label.set_text(f"{len(selected)} of {len(self._checks)} selected")
        buffer = self.message.get_buffer()
        start, end = buffer.get_bounds()
        has_message = bool(buffer.get_text(start, end, False).strip())
        enabled = has_message and bool(selected) and not self.model.git_busy
        self.commit_button.set_sensitive(enabled)
        self.commit_push_button.set_sensitive(enabled)

    def _commit(self, push: bool) -> None:
        buffer = self.message.get_buffer()
        start, end = buffer.get_bounds()
        message = buffer.get_text(start, end, False)
        selected = self._selected()
        paths = None if len(selected) == len(self._checks) else selected
        self.model.git_commit(message, paths, push)
        self.close()


def prompt(parent: Gtk.Window, title: str, detail: str, placeholder: str,
           button: str, initial: str, on_submit) -> None:
    """Small text prompt used for "Set Remote URL…" and "New Branch…"."""
    dialog = Adw.MessageDialog(transient_for=parent, modal=True,
                               heading=title, body=detail)
    entry = Gtk.Entry(placeholder_text=placeholder, text=initial,
                      activates_default=True)
    entry.set_margin_top(8)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", button)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def responded(_dialog, response: str) -> None:
        if response == "ok":
            value = entry.get_text().strip()
            if value:
                on_submit(value)

    dialog.connect("response", responded)
    dialog.present()


def confirm(parent: Gtk.Window, heading: str, body: str, confirm_label: str,
            on_confirm, destructive: bool = False) -> None:
    dialog = Adw.MessageDialog(transient_for=parent, modal=True,
                               heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", confirm_label)
    dialog.set_response_appearance(
        "ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_confirm() if r == "ok" else None)
    dialog.present()

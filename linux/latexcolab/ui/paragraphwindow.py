"""The two-pane paragraph rewrite window (port of ParagraphEditorView.swift).

Left: the paragraph as it stands in the document. Right: an editable copy.
Changed words are highlighted on both sides, AI-made words in violet, with the
word budget, AI Fix, Compare, History, Save Draft and Apply below.
"""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..core import budget as budget_mod
from ..core import compare as compare_mod
from ..model import APPLY_NEEDS_CONFIRMATION, APPLY_OK, AppModel
from ..session import ParagraphSession
from .codeview import AI_COLOR, DELETED_COLOR, INSERTED_COLOR, CodeView, Highlight

_VERDICT_CSS = {
    compare_mod.VERDICT_SAME: "success",
    compare_mod.VERDICT_MINOR: "warning",
    compare_mod.VERDICT_CHANGED: "error",
    compare_mod.VERDICT_UNKNOWN: "dim-label",
}

_VERDICT_ICON = {
    compare_mod.VERDICT_SAME: "object-select-symbolic",
    compare_mod.VERDICT_MINOR: "dialog-information-symbolic",
    compare_mod.VERDICT_CHANGED: "dialog-warning-symbolic",
    compare_mod.VERDICT_UNKNOWN: "dialog-question-symbolic",
}

_SEVERITY_ICON = {
    compare_mod.SEVERITY_ERROR: "dialog-error-symbolic",
    compare_mod.SEVERITY_WARNING: "dialog-warning-symbolic",
    compare_mod.SEVERITY_INFO: "dialog-information-symbolic",
}


def _swatch(color, label: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    area = Gtk.DrawingArea()
    area.set_size_request(12, 12)
    area.set_valign(Gtk.Align.CENTER)
    css = Gtk.CssProvider()
    r, g, b, a = color
    css.load_from_data(
        f"* {{ background-color: rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},{a});"
        " border-radius: 2px; }".encode("utf-8"))
    area.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    box.append(area)
    text = Gtk.Label(label=label, xalign=0.0)
    text.add_css_class("caption")
    text.add_css_class("dim-label")
    box.append(text)
    return box


class ParagraphWindow(Adw.Window):
    def __init__(self, parent: Gtk.Window, model: AppModel):
        super().__init__(transient_for=parent, modal=False,
                         default_width=1120, default_height=720,
                         title="Paragraph Editor")
        self.model = model
        self.session: ParagraphSession | None = None
        self._syncing = False

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        self.header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Paragraph Editor", subtitle="")
        self.header.set_title_widget(self.window_title)

        self.sync_badge = Gtk.Label()
        self.sync_badge.add_css_class("caption")
        self.header.pack_start(self.sync_badge)

        self.history_button = Gtk.Button(label="History")
        self.history_button.set_tooltip_text(
            "Earlier versions of this paragraph that Apply replaced — view or revert")
        self.history_button.connect("clicked", lambda *_: self._show_history())
        self.header.pack_end(self.history_button)

        self.saved_label = Gtk.Label()
        self.saved_label.add_css_class("caption")
        self.saved_label.add_css_class("dim-label")
        self.header.pack_end(self.saved_label)
        root.append(self.header)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        root.append(self.stack)

        empty = Adw.StatusPage(
            title="No paragraph selected", icon_name="text-editor-symbolic",
            description="Click a paragraph in the PDF preview to edit it here.")
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self._build_body(), "body")
        self.stack.set_visible_child_name("empty")

        model.subscribe("paragraph", self.attach_session)
        model.subscribe("paragraph-window", self._present_request)

        self._install_shortcuts()
        self.connect("close-request", self._on_close)

    # -- construction ------------------------------------------------------

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.context_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.context_bar.set_margin_start(12)
        self.context_bar.set_margin_end(12)
        self.context_bar.set_margin_top(4)
        self.context_bar.set_margin_bottom(4)
        self.context_intro = Gtk.Label(xalign=0.0)
        self.context_intro.add_css_class("caption-heading")
        self.context_before = Gtk.Label(xalign=1.0, ellipsize=1)
        self.context_before.add_css_class("caption")
        self.context_before.add_css_class("dim-label")
        self.context_before.set_hexpand(True)
        self.context_span = Gtk.Label()
        self.context_span.add_css_class("caption-heading")
        self.context_span.add_css_class("accent")
        self.context_after = Gtk.Label(xalign=0.0, ellipsize=3)
        self.context_after.add_css_class("caption")
        self.context_after.add_css_class("dim-label")
        self.context_after.set_hexpand(True)
        for w in (self.context_intro, self.context_before, self.context_span,
                  self.context_after):
            self.context_bar.append(w)
        body.append(self.context_bar)

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_vexpand(True)
        panes.set_wide_handle(True)

        self.left_title = Gtk.Label(xalign=0.0)
        self.left_title.add_css_class("caption-heading")
        self.left_subtitle = Gtk.Label(xalign=0.0, ellipsize=3)
        self.left_subtitle.add_css_class("caption")
        self.left_subtitle.add_css_class("dim-label")
        self.left_view = CodeView(editable=False, font_size=11)
        panes.set_start_child(self._pane(self.left_title, self.left_subtitle, self.left_view))

        self.right_title = Gtk.Label(xalign=0.0, label="Rewrite")
        self.right_title.add_css_class("caption-heading")
        self.right_subtitle = Gtk.Label(xalign=0.0, ellipsize=3)
        self.right_subtitle.add_css_class("caption")
        self.right_subtitle.add_css_class("dim-label")
        self.right_view = CodeView(editable=True, font_size=11,
                                   on_change=self._on_draft_changed)
        panes.set_end_child(self._pane(self.right_title, self.right_subtitle, self.right_view))
        panes.set_resize_start_child(True)
        panes.set_resize_end_child(True)
        self.panes = panes
        body.append(panes)

        # Colour key with AI / your-edit counts.
        self.legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.legend.set_margin_start(12)
        self.legend.set_margin_end(12)
        self.legend.set_margin_top(4)
        self.legend.set_margin_bottom(4)
        self.legend_ai = _swatch(AI_COLOR, "")
        self.legend_ins = _swatch(INSERTED_COLOR, "")
        self.legend_del = _swatch(DELETED_COLOR, "removed or replaced by you")
        self.legend.append(self.legend_ai)
        self.legend.append(self.legend_ins)
        self.legend.append(self.legend_del)
        hint = Gtk.Label(
            label="AI marks follow the text as you edit and are kept with the draft "
                  "and after Apply.", xalign=1.0, ellipsize=3)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_hexpand(True)
        self.legend.append(hint)
        body.append(self.legend)

        self.comparison_panel = ComparisonPanel(self)
        body.append(self.comparison_panel)

        body.append(Gtk.Separator())
        body.append(self._build_controls())
        return body

    def _pane(self, title: Gtk.Label, subtitle: Gtk.Label, view: CodeView) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.set_margin_start(10)
        head.set_margin_end(10)
        head.set_margin_top(5)
        head.set_margin_bottom(5)
        head.append(title)
        subtitle.set_hexpand(True)
        head.append(subtitle)
        box.append(head)
        box.append(Gtk.Separator())
        view.set_vexpand(True)
        box.append(view)
        box.set_size_request(300, -1)
        return box

    def _build_controls(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)

        slider_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        slider_row.append(Gtk.Label(label="Max words the AI may add, delete or replace",
                                    xalign=0.0))
        self.max_words = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.max_words.set_hexpand(True)
        self.max_words.set_draw_value(False)
        self.max_words.connect("value-changed", self._on_max_words)
        slider_row.append(self.max_words)
        self.max_words_label = Gtk.Label(xalign=1.0)
        self.max_words_label.set_size_request(32, -1)
        slider_row.append(self.max_words_label)
        box.append(slider_row)

        self.budget_hint = Gtk.Label(xalign=0.0, wrap=True)
        self.budget_hint.add_css_class("caption")
        self.budget_hint.add_css_class("dim-label")
        box.append(self.budget_hint)

        instructions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        instructions_row.append(Gtk.Label(label="Additional instructions", xalign=0.0,
                                          valign=Gtk.Align.START))
        self.instructions = Gtk.Entry(
            placeholder_text="e.g. use simpler terms, remove em dashes, "
                             "make it sound less AI-like")
        self.instructions.set_hexpand(True)
        self.instructions.connect("changed", self._on_instructions)
        instructions_row.append(self.instructions)
        box.append(instructions_row)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.fix_button = Gtk.Button(label="AI Fix")
        self.fix_button.set_tooltip_text(
            "Proofread the right side with the LM Studio model: spelling, punctuation and "
            "LaTeX syntax, plus at most the chosen number of word changes. Anything beyond "
            "the limit is reverted to your words.")
        self.fix_button.connect("clicked", lambda *_: self.model.ai_fix(self.session))
        actions.append(self.fix_button)

        self.compare_button = Gtk.Button(label="Compare")
        self.compare_button.set_tooltip_text(
            "Compare the two sides semantically: what changed, and does the edit still "
            "say the same thing?")
        self.compare_button.connect("clicked",
                                    lambda *_: self.model.compare_paragraph(self.session))
        actions.append(self.compare_button)

        self.spinner = Gtk.Spinner()
        actions.append(self.spinner)

        self.message = Gtk.Label(xalign=0.0, wrap=True, selectable=True)
        self.message.add_css_class("caption")
        self.message.set_hexpand(True)
        actions.append(self.message)

        self.discard_button = Gtk.Button(label="Discard Draft")
        self.discard_button.set_tooltip_text(
            "Delete the saved draft and reset the right side to the document text")
        self.discard_button.connect("clicked",
                                    lambda *_: self.model.discard_draft(self.session))
        actions.append(self.discard_button)

        self.revert_button = Gtk.Button(label="Revert")
        self.revert_button.set_tooltip_text(
            "Reset the right side to the document text (keeps the saved record)")
        self.revert_button.connect("clicked", lambda *_: self._revert())
        actions.append(self.revert_button)

        self.save_button = Gtk.Button(label="Save Draft")
        self.save_button.set_tooltip_text(
            "Save the rewrite without touching the document (Ctrl+Shift+S)")
        self.save_button.connect("clicked",
                                 lambda *_: self.model.save_draft_now(self.session))
        actions.append(self.save_button)

        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.set_tooltip_text(
            "Write the rewrite into the .tex file, regenerate the PDF and close "
            "this window (Ctrl+Return)")
        self.apply_button.connect("clicked", lambda *_: self.apply())
        actions.append(self.apply_button)
        box.append(actions)

        self.held_back = Gtk.Expander()
        self.held_back_label = Gtk.Label(xalign=0.0, wrap=True)
        self.held_back.set_label_widget(self.held_back_label)
        self.held_back_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.held_back_list.set_margin_top(6)
        self.held_back_list.set_margin_start(12)
        self.held_back.set_child(self.held_back_list)
        box.append(self.held_back)
        return box

    def _install_shortcuts(self) -> None:
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)
        for accel, callback in (
            ("<Control>Return", lambda: self.apply()),
            ("<Control><Shift>s", lambda: self.model.save_draft_now(self.session)),
            ("<Control>h", lambda: self._show_history()),
            ("Escape", lambda: self.close()),
        ):
            controller.add_shortcut(Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string(accel),
                action=Gtk.CallbackAction.new(lambda *_a, cb=callback: (cb(), True)[1])))
        self.add_controller(controller)

    # -- session -----------------------------------------------------------

    def _present_request(self) -> None:
        self.attach_session()
        self.present()

    def attach_session(self) -> None:
        session = self.model.paragraph_session
        if session is None:
            self.stack.set_visible_child_name("empty")
            self.session = None
            return
        if session is not self.session:
            self.session = session
            session.on_updated = self.refresh
            self._syncing = True
            self.left_view.set_text(session.original)
            self.right_view.set_text(session.draft)
            self.max_words.set_value(session.max_words)
            self.instructions.set_text(session.instructions)
            self._syncing = False
        self.stack.set_visible_child_name("body")
        self.refresh()

    def _on_close(self, *_args) -> bool:
        if self.session:
            self.model.persist_draft(self.session)
        self.set_visible(False)
        return True

    # -- edits -------------------------------------------------------------

    def _on_draft_changed(self, text: str) -> None:
        if self._syncing or self.session is None:
            return
        self.session.draft = text
        self.refresh(sync_text=False)

    def _on_max_words(self, scale: Gtk.Scale) -> None:
        if self._syncing or self.session is None:
            return
        value = int(round(scale.get_value()))
        if value != self.session.max_words:
            self.session.max_words = value
            if self.session.on_changed:
                self.session.on_changed()
        self.refresh(sync_text=False)

    def _on_instructions(self, entry: Gtk.Entry) -> None:
        if self._syncing or self.session is None:
            return
        value = entry.get_text()
        if value != self.session.instructions:
            self.session.instructions = value
            if self.session.on_changed:
                self.session.on_changed()

    def _revert(self) -> None:
        if self.session:
            self.session.draft = self.session.original
            self.refresh()

    def apply(self) -> None:
        session = self.session
        if session is None:
            return
        outcome = self.model.apply_paragraph(session)
        if outcome == APPLY_NEEDS_CONFIRMATION:
            self._confirm_structural(session)
            return
        if outcome == APPLY_OK:
            self._after_apply()
        self.refresh()

    def _confirm_structural(self, session: ParagraphSession) -> None:
        """Warn before writing a rewrite that adds or removes LaTeX structure —
        the usual way a paragraph edit breaks the whole compile."""
        body = ("Compared with the paragraph in " + session.file + ", the rewrite "
                + "; ".join(session.pending_structural)
                + ".\n\nThat will most likely break the compile (for example a second "
                  "\\end{abstract}). Apply it anyway?")
        dialog = Adw.MessageDialog(transient_for=self, modal=True,
                                   heading="This rewrite changes LaTeX structure",
                                   body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply Anyway")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def responded(_d, response: str) -> None:
            if response == "apply":
                if self.model.apply_paragraph(session, allow_structural=True) == APPLY_OK:
                    self._after_apply()
            self.refresh()

        dialog.connect("response", responded)
        dialog.present()

    def _after_apply(self) -> None:
        self._syncing = True
        self.left_view.set_text(self.session.original)
        self.right_view.set_text(self.session.draft)
        self._syncing = False
        if self.model.config.close_window_after_apply:
            # A dialog may still be animating out; closing at the same instant
            # is ignored, so give it a moment.
            GLib.timeout_add(400, lambda: (self.set_visible(False), False)[1])

    # -- rendering ---------------------------------------------------------

    def refresh(self, sync_text: bool = True) -> None:
        session = self.session
        if session is None:
            return
        marks = session.provenance.highlights(session.original, session.draft)
        measure = budget_mod.measure(session.original, session.draft)

        self._syncing = True
        if sync_text:
            if self.left_view.text != session.original:
                self.left_view.set_text(session.original)
            if self.right_view.text != session.draft:
                self.right_view.set_text(session.draft)
            if int(self.max_words.get_value()) != session.max_words:
                self.max_words.set_value(session.max_words)
            if self.instructions.get_text() != session.instructions:
                self.instructions.set_text(session.instructions)
        else:
            if self.left_view.text != session.original:
                self.left_view.set_text(session.original)
        self._syncing = False

        self.left_view.set_highlights(
            [Highlight(loc, length, DELETED_COLOR) for loc, length in marks.user_in_original]
            + [Highlight(loc, length, AI_COLOR) for loc, length in marks.ai_in_original])
        self.right_view.set_highlights(
            [Highlight(loc, length, INSERTED_COLOR) for loc, length in marks.user_in_draft]
            + [Highlight(loc, length, AI_COLOR) for loc, length in marks.ai_in_draft])

        scope = session.scope_label.lower()
        self.window_title.set_title(session.file)
        self.window_title.set_subtitle(
            f"{scope} · lines {session.range.start_line}–{session.range.end_line}")

        if session.is_in_sync:
            self.sync_badge.set_text(
                "✓ Applied · in sync" if session.applied else "✓ In sync with document")
            self.sync_badge.remove_css_class("warning")
            self.sync_badge.add_css_class("success")
        else:
            self.sync_badge.set_text("● Draft differs · not applied")
            self.sync_badge.remove_css_class("success")
            self.sync_badge.add_css_class("warning")

        self.saved_label.set_text(
            f"saved {session.saved_at.astimezone().strftime('%H:%M')}"
            if session.saved_at else "")
        self.history_button.set_label(
            "History" if not session.history else f"History ({len(session.history)})")

        self.context_bar.set_visible(session.is_partial)
        if session.is_partial:
            before, after = session.context()
            self.context_intro.set_text(f"Editing a {scope} inside the paragraph:")
            self.context_before.set_text(before)
            self.context_span.set_text(f"⟨{scope}⟩")
            self.context_after.set_text(after)

        self.left_title.set_text(
            f"In document ({scope})" if session.is_partial else "In document")
        self.left_subtitle.set_text(
            f"{session.file} · lines {session.range.start_line}–{session.range.end_line}")
        self.right_subtitle.set_text(self._pane_subtitle(measure, marks))

        self.max_words_label.set_text(str(session.max_words))
        self.budget_hint.set_text(
            "0 = proofread only: spelling, punctuation and LaTeX syntax are fixed, "
            "every word you wrote stays."
            if session.max_words == 0 else
            "Spelling, punctuation and LaTeX fixes are always applied and don't count. "
            f"Rewordings beyond {session.max_words} words are held back for you to "
            "accept one by one.")

        self.legend_ai.get_last_child().set_text(
            f"AI-made: {marks.ai_words} word{'' if marks.ai_words == 1 else 's'}, "
            f"{marks.ai_punctuation} punctuation "
            f"mark{'' if marks.ai_punctuation == 1 else 's'}")
        self.legend_ins.get_last_child().set_text(
            f"your additions: {marks.user_words} word"
            f"{'' if marks.user_words == 1 else 's'}")

        self.message.set_text(session.message)
        if session.message_is_error:
            self.message.add_css_class("error")
            self.message.remove_css_class("dim-label")
        else:
            self.message.remove_css_class("error")
            self.message.add_css_class("dim-label")

        busy = session.is_busy
        self.fix_button.set_label("Fixing…" if busy else "AI Fix")
        self.fix_button.set_sensitive(not busy)
        self.compare_button.set_label("Comparing…" if session.is_comparing else "Compare")
        self.compare_button.set_sensitive(not session.is_comparing and not busy)
        if busy or session.is_comparing:
            self.spinner.start()
            self.spinner.set_visible(True)
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)
        self.right_view.set_editable(not busy)
        self.instructions.set_sensitive(not busy)
        self.discard_button.set_sensitive(
            not busy and not (session.is_in_sync and session.edit_id is None))
        self.revert_button.set_sensitive(not busy and not session.is_in_sync)
        self.save_button.set_sensitive(not busy)
        self.apply_button.set_sensitive(not busy and not session.is_in_sync)

        self._refresh_held_back()
        self.comparison_panel.refresh()

    def _pane_subtitle(self, measure, marks) -> str:
        session = self.session
        if (measure.word_changes == 0 and measure.free_fixes == 0
                and marks.ai_words == 0 and marks.ai_punctuation == 0):
            return "identical to the document"
        parts = []
        if measure.free_fixes:
            parts.append(f"{measure.free_fixes} syntax "
                         f"fix{'' if measure.free_fixes == 1 else 'es'}")
        if measure.word_changes:
            parts.append(f"{measure.word_changes} "
                         f"word{'' if measure.word_changes == 1 else 's'} reworded in "
                         f"{measure.rewordings} "
                         f"place{'' if measure.rewordings == 1 else 's'}")
        if measure.word_changes > session.max_words:
            parts.append(f"over the {session.max_words}-word limit")
        return " · ".join(parts)

    def _refresh_held_back(self) -> None:
        session = self.session
        dropped = session.dropped_suggestions if session else []
        self.held_back.set_visible(bool(dropped))
        child = self.held_back_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.held_back_list.remove(child)
            child = nxt
        if not dropped:
            return
        words = sum(s.cost for s in dropped)
        self.held_back_label.set_text(
            f"{len(dropped)} rewording{'' if len(dropped) == 1 else 's'} held back "
            f"({words} words over the limit) — apply any you actually want")
        for suggestion in dropped:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            button = Gtk.Button(label="Apply")
            button.connect("clicked",
                           lambda _b, s=suggestion: self.model.apply_suggestion(s, session))
            row.append(button)
            label = Gtk.Label(label=suggestion.label, xalign=0.0, wrap=True,
                              selectable=True, hexpand=True)
            row.append(label)
            cost = Gtk.Label(label=f"{suggestion.cost} "
                                   f"word{'' if suggestion.cost == 1 else 's'}")
            cost.add_css_class("caption")
            cost.add_css_class("dim-label")
            row.append(cost)
            self.held_back_list.append(row)
        dismiss = Gtk.Button(label="Dismiss all")
        dismiss.add_css_class("flat")
        dismiss.set_halign(Gtk.Align.START)
        dismiss.connect("clicked", lambda *_: self._dismiss_all())
        self.held_back_list.append(dismiss)

    def _dismiss_all(self) -> None:
        if self.session:
            self.session.dropped_suggestions = []
            self.refresh(sync_text=False)

    def _show_history(self) -> None:
        if self.session:
            HistoryDialog(self, self.model, self.session).present()


class ComparisonPanel(Gtk.Box):
    """Result of "Compare": deterministic integrity checks plus the model's verdict."""

    def __init__(self, window: ParagraphWindow):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window = window
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.add_css_class("card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.set_margin_start(10)
        header.set_margin_end(6)
        header.set_margin_top(8)
        self.icon = Gtk.Image()
        header.append(self.icon)
        self.headline = Gtk.Label(xalign=0.0)
        self.headline.add_css_class("heading")
        header.append(self.headline)
        self.stale = Gtk.Label(label="text changed since this comparison")
        self.stale.add_css_class("caption")
        self.stale.add_css_class("warning")
        header.append(self.stale)
        self.model_label = Gtk.Label(xalign=1.0)
        self.model_label.add_css_class("caption")
        self.model_label.add_css_class("dim-label")
        self.model_label.set_hexpand(True)
        header.append(self.model_label)
        rerun = Gtk.Button(label="Re-run")
        rerun.add_css_class("flat")
        rerun.connect("clicked", lambda *_: window.model.compare_paragraph(window.session))
        header.append(rerun)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text("Hide the comparison")
        close.connect("clicked", lambda *_: self._hide())
        header.append(close)
        self.append(header)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.body.set_margin_start(10)
        self.body.set_margin_end(10)
        self.body.set_margin_bottom(10)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.body)
        scroller.set_max_content_height(240)
        scroller.set_propagate_natural_height(True)
        self.append(scroller)
        self.set_visible(False)

    def _hide(self) -> None:
        if self.window.session:
            self.window.session.show_comparison = False
        self.set_visible(False)

    def refresh(self) -> None:
        session = self.window.session
        if session is None or not session.show_comparison:
            self.set_visible(False)
            return
        self.set_visible(True)
        self.stale.set_visible(session.comparison_is_stale)

        child = self.body.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt

        c = session.comparison
        if c is not None:
            self.icon.set_from_icon_name(_VERDICT_ICON[c.verdict])
            self.headline.set_text(c.headline)
            for css in _VERDICT_CSS.values():
                self.headline.remove_css_class(css)
            self.headline.add_css_class(_VERDICT_CSS[c.verdict])
            self.model_label.set_text("" if c.model in ("", "none") else c.model)
            if c.summary:
                label = Gtk.Label(label=c.summary, xalign=0.0, wrap=True, selectable=True)
                self.body.append(label)
            self._section("Meaning differences", c.meaning_differences, "error")
            self._section("What changed on the edited side", c.changes, "dim-label")
            self._section("LaTeX issues the model noticed", c.latex_issues, "warning")
            if c.verdict == compare_mod.VERDICT_UNKNOWN and c.raw and c.raw != c.summary:
                expander = Gtk.Expander(label="Raw model output")
                raw = Gtk.Label(label=c.raw, xalign=0.0, wrap=True, selectable=True)
                raw.add_css_class("caption")
                expander.set_child(raw)
                self.body.append(expander)
        elif session.is_comparing:
            self.icon.set_from_icon_name("emblem-synchronizing-symbolic")
            self.headline.set_text("Semantic comparison")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            spinner = Gtk.Spinner()
            spinner.start()
            row.append(spinner)
            waiting = Gtk.Label(label="Waiting for the model's verdict…", xalign=0.0)
            waiting.add_css_class("dim-label")
            row.append(waiting)
            self.body.append(row)
        elif not session.message_is_error:
            self.headline.set_text("Semantic comparison")
            none = Gtk.Label(label="No verdict from the model.", xalign=0.0)
            none.add_css_class("dim-label")
            self.body.append(none)

        issues = session.integrity_issues
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="Automatic checks", xalign=0.0)
        title.add_css_class("caption-heading")
        head.append(title)
        summary = Gtk.Label(
            label=("citations, references, labels, math, numbers, negation and hedging "
                   "all match") if not issues
            else f"{len(issues)} finding{'' if len(issues) == 1 else 's'}", xalign=0.0)
        summary.add_css_class("caption")
        summary.add_css_class("success" if not issues else "dim-label")
        head.append(summary)
        self.body.append(head)
        for issue in issues:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            icon = Gtk.Image.new_from_icon_name(_SEVERITY_ICON[issue.severity])
            icon.set_valign(Gtk.Align.START)
            row.append(icon)
            label = Gtk.Label(label=issue.text, xalign=0.0, wrap=True, selectable=True)
            label.add_css_class("caption")
            row.append(label)
            self.body.append(row)

    def _section(self, title: str, items: list[str], css: str) -> None:
        if not items:
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        head = Gtk.Label(label=title, xalign=0.0)
        head.add_css_class("caption-heading")
        head.add_css_class("dim-label")
        box.append(head)
        for item in items:
            label = Gtk.Label(label="• " + item, xalign=0.0, wrap=True, selectable=True)
            label.add_css_class(css)
            box.append(label)
        self.body.append(box)


class HistoryDialog(Adw.Window):
    """Earlier versions of the paragraph, side by side with the current text."""

    def __init__(self, parent: ParagraphWindow, model: AppModel, session: ParagraphSession):
        super().__init__(transient_for=parent, modal=True,
                         default_width=940, default_height=620, title="History")
        self.parent_window = parent
        self.model = model
        self.session = session
        self.versions = list(reversed(session.history))   # newest first
        self.selected = self.versions[0] if self.versions else None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title=f"History of {session.file}",
            subtitle=f"lines {session.range.start_line}–{session.range.end_line} · "
                     f"{len(session.history)} earlier "
                     f"version{'' if len(session.history) == 1 else 's'}"))
        root.append(header)

        if not self.versions:
            status = Adw.StatusPage(
                title="No earlier versions yet", icon_name="document-open-recent-symbolic",
                description="Every Apply keeps the text it replaced here, so it can be "
                            "reviewed or restored.")
            status.set_vexpand(True)
            root.append(status)
            self.set_content(root)
            return

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_vexpand(True)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("navigation-sidebar")
        for v in self.versions:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(8)
            box.set_margin_end(8)
            when = Gtk.Label(
                label="Until " + v.replaced_at.astimezone().strftime("%d %b %Y, %H:%M"),
                xalign=0.0)
            when.add_css_class("heading")
            box.append(when)
            if v.note:
                note = Gtk.Label(label=v.note, xalign=0.0, wrap=True)
                note.add_css_class("caption")
                note.add_css_class("dim-label")
                box.append(note)
            preview = Gtk.Label(label=v.text.replace("\n", " "), xalign=0.0,
                                ellipsize=3, lines=2)
            preview.add_css_class("caption")
            preview.add_css_class("dim-label")
            box.append(preview)
            row.set_child(box)
            row.version = v
            self.listbox.append(row)
        self.listbox.connect("row-selected", self._on_selected)
        sidebar = Gtk.ScrolledWindow()
        sidebar.set_child(self.listbox)
        sidebar.set_size_request(280, -1)
        panes.set_start_child(sidebar)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.diff_summary = Gtk.Label(xalign=0.0)
        self.diff_summary.add_css_class("caption")
        self.diff_summary.add_css_class("dim-label")
        self.diff_summary.set_margin_start(10)
        self.diff_summary.set_margin_top(6)
        self.diff_summary.set_margin_bottom(4)
        right.append(self.diff_summary)
        self.old_view = CodeView(editable=False, font_size=10)
        self.old_view.set_vexpand(True)
        right.append(self.old_view)
        current_label = Gtk.Label(label="Current (in document)", xalign=0.0)
        current_label.add_css_class("caption-heading")
        current_label.set_margin_start(10)
        current_label.set_margin_top(6)
        current_label.set_margin_bottom(4)
        right.append(current_label)
        self.new_view = CodeView(editable=False, font_size=10)
        self.new_view.set_vexpand(True)
        right.append(self.new_view)
        panes.set_end_child(right)
        panes.set_resize_end_child(True)
        root.append(panes)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_margin_top(10)
        buttons.set_margin_bottom(10)
        buttons.set_margin_start(12)
        buttons.set_margin_end(12)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.append(close)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        load = Gtk.Button(label="Load into Rewrite")
        load.set_tooltip_text("Copy this version to the right pane without touching "
                              "the document")
        load.connect("clicked", lambda *_: self._load())
        buttons.append(load)
        revert = Gtk.Button(label="Revert Document to This Version")
        revert.add_css_class("suggested-action")
        revert.set_tooltip_text("Write this version back into the .tex file "
                                "(the current text is kept in history)")
        revert.connect("clicked", lambda *_: self._revert())
        buttons.append(revert)
        root.append(buttons)

        self.set_content(root)
        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)

    def _on_selected(self, _box, row) -> None:
        if row is None:
            return
        from ..core.worddiff import diff, highlight_ranges

        self.selected = row.version
        segments = diff(row.version.text, self.session.original)
        old, new = highlight_ranges(segments)
        measure = budget_mod.measure(row.version.text, self.session.original)
        self.diff_summary.set_text(
            f"Selected version vs current: {measure.word_changes} "
            f"word{'' if measure.word_changes == 1 else 's'} differ, "
            f"{measure.free_fixes} punctuation/LaTeX differences")
        self.old_view.set_text(row.version.text)
        self.old_view.set_highlights([Highlight(loc, length, DELETED_COLOR)
                                      for loc, length in old])
        self.new_view.set_text(self.session.original)
        self.new_view.set_highlights([Highlight(loc, length, INSERTED_COLOR)
                                      for loc, length in new])

    def _load(self) -> None:
        if not self.selected:
            return
        self.session.draft = self.selected.text
        stamp = self.selected.replaced_at.astimezone().strftime("%d %b %Y, %H:%M")
        self.session.note(f"Loaded the version from before {stamp} into the rewrite "
                          "pane — press Apply to put it back.")
        self.parent_window.refresh()
        self.close()

    def _revert(self) -> None:
        if not self.selected:
            return
        version = self.selected
        self.close()
        outcome = self.model.revert_paragraph(self.session, version)
        if outcome == APPLY_NEEDS_CONFIRMATION:
            self.parent_window._confirm_structural(self.session)
        elif outcome == APPLY_OK:
            self.parent_window._after_apply()
        self.parent_window.refresh()

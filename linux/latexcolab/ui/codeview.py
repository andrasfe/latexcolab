"""Monospaced LaTeX editor with syntax colouring, line numbers and diff
backgrounds — the GTK counterpart of CodeTextView.swift.
"""

from __future__ import annotations

import re
from typing import Callable

from gi.repository import Adw, Gdk, GLib, Gtk, Pango, Graphene

# Diff / provenance backgrounds, matching the macOS palette.
DELETED_COLOR = (0.90, 0.22, 0.21, 0.22)
INSERTED_COLOR = (0.18, 0.76, 0.49, 0.26)
AI_COLOR = (0.68, 0.38, 0.85, 0.32)

_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
_COMMAND_RE = re.compile(r"\\(?:[a-zA-Z@]+\*?|[^a-zA-Z\s])")
_MATH_RE = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)")
_ENVIRONMENT_RE = re.compile(r"\\(?:begin|end)\{([^}]*)\}")
_BRACE_RE = re.compile(r"[{}\[\]]")

_LIGHT = {
    "math": "#8b2fb5",
    "brace": "#8a8a8a",
    "command": "#1a5fb4",
    "environment": "#1a7f45",
    "comment": "#767676",
}
_DARK = {
    "math": "#dc8add",
    "brace": "#9a9996",
    "command": "#78aeed",
    "environment": "#8ff0a4",
    "comment": "#9a9996",
}


def rgba(spec) -> Gdk.RGBA:
    c = Gdk.RGBA()
    if isinstance(spec, str):
        c.parse(spec)
    else:
        c.red, c.green, c.blue, c.alpha = spec
    return c


class Highlight:
    """A background span: ``location``/``length`` are character offsets."""

    __slots__ = ("location", "length", "color")

    def __init__(self, location: int, length: int, color):
        self.location = location
        self.length = length
        self.color = color

    def key(self):
        return (self.location, self.length, tuple(self.color))


class LineNumbers(Gtk.Widget):
    """Gutter drawing line numbers next to a Gtk.TextView."""

    def __init__(self, view: Gtk.TextView):
        super().__init__()
        self._view = view
        self.set_size_request(46, -1)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:  # noqa: N802
        view = self._view
        buffer = view.get_buffer()
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return

        color = self.get_style_context().get_color()
        color.alpha *= 0.55

        _, top = view.window_to_buffer_coords(Gtk.TextWindowType.LEFT, 0, 0)
        it = view.get_line_at_y(top)[0]
        context = self.get_pango_context()
        while True:
            y, line_height = view.get_line_yrange(it)
            _, window_y = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            if window_y > height:
                break
            layout = Pango.Layout(context)
            layout.set_font_description(Pango.FontDescription("Monospace 9"))
            layout.set_text(str(it.get_line() + 1), -1)
            text_width = layout.get_pixel_size()[0]
            snapshot.save()
            snapshot.translate(Graphene.Point().init(width - text_width - 7, window_y + 1))
            snapshot.append_layout(layout, color)
            snapshot.restore()
            if not it.forward_line():
                break

    def refresh(self) -> None:
        self.queue_draw()


class CodeView(Gtk.ScrolledWindow):
    def __init__(self, editable: bool = True, show_line_numbers: bool = False,
                 font_size: int = 11, on_change: Callable[[str], None] | None = None):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.on_change = on_change
        self._highlights: list[Highlight] = []
        self._applied_key: tuple | None = None
        self._pending: int | None = None
        self._suppress = False

        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.set_monospace(True)
        self.view.set_editable(editable)
        self.view.set_cursor_visible(editable)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(8)
        self.view.set_right_margin(8)
        self.view.set_top_margin(6)
        self.view.set_bottom_margin(6)
        self.view.add_css_class("latexcolab-code")
        self.set_child(self.view)

        css = Gtk.CssProvider()
        css.load_from_data(
            (".latexcolab-code { font-family: monospace; font-size: %dpt; }" % font_size)
            .encode("utf-8"))
        self.view.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._tags: dict[str, Gtk.TextTag] = {}
        self._make_tags()

        self._gutter: LineNumbers | None = None
        if show_line_numbers:
            self._gutter = LineNumbers(self.view)
            self.view.set_gutter(Gtk.TextWindowType.LEFT, self._gutter)
            self.buffer.connect("changed", lambda *_: self._gutter.refresh())
            self.get_vadjustment().connect("value-changed", lambda *_: self._gutter.refresh())

        self.buffer.connect("changed", self._on_changed)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._retheme())

    # -- tags --------------------------------------------------------------

    def _palette(self) -> dict[str, str]:
        return _DARK if Adw.StyleManager.get_default().get_dark() else _LIGHT

    def _make_tags(self) -> None:
        table = self.buffer.get_tag_table()
        palette = self._palette()
        for name, color in palette.items():
            tag = self._tags.get(name)
            if tag is None:
                tag = Gtk.TextTag(name=name)
                table.add(tag)
                self._tags[name] = tag
            tag.set_property("foreground-rgba", rgba(color))
        for name, color in (("bg-deleted", DELETED_COLOR), ("bg-inserted", INSERTED_COLOR),
                            ("bg-ai", AI_COLOR)):
            if name not in self._tags:
                tag = Gtk.TextTag(name=name)
                tag.set_property("background-rgba", rgba(color))
                table.add(tag)
                self._tags[name] = tag

    def _retheme(self) -> None:
        self._make_tags()
        self.rehighlight(immediate=True)

    # -- text --------------------------------------------------------------

    @property
    def text(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def set_text(self, value: str) -> None:
        if value == self.text:
            return
        self._suppress = True
        offset = self.buffer.get_property("cursor-position")
        self.buffer.set_text(value)
        self._suppress = False
        it = self.buffer.get_iter_at_offset(min(offset, self.buffer.get_char_count()))
        self.buffer.place_cursor(it)
        self.rehighlight(immediate=True)

    def set_editable(self, editable: bool) -> None:
        self.view.set_editable(editable)
        self.view.set_cursor_visible(editable)

    def set_highlights(self, highlights: list[Highlight]) -> None:
        key = tuple(h.key() for h in highlights)
        if key == self._applied_key:
            return
        self._highlights = list(highlights)
        self.rehighlight(immediate=True)

    def _on_changed(self, *_args) -> None:
        if self._suppress:
            return
        if self.on_change:
            self.on_change(self.text)
        self.rehighlight()

    # -- highlighting ------------------------------------------------------

    def rehighlight(self, immediate: bool = False) -> None:
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = None
        if immediate:
            self._apply_highlighting()
        else:
            self._pending = GLib.timeout_add(120, self._apply_timeout)

    def _apply_timeout(self) -> bool:
        self._pending = None
        self._apply_highlighting()
        return False

    def _apply_highlighting(self) -> None:
        buffer = self.buffer
        text = self.text
        start, end = buffer.get_bounds()
        buffer.remove_all_tags(start, end)

        def tag(name: str, a: int, b: int) -> None:
            buffer.apply_tag(self._tags[name],
                             buffer.get_iter_at_offset(a), buffer.get_iter_at_offset(b))

        for m in _MATH_RE.finditer(text):
            tag("math", m.start(), m.end())
        for m in _BRACE_RE.finditer(text):
            tag("brace", m.start(), m.end())
        for m in _COMMAND_RE.finditer(text):
            tag("command", m.start(), m.end())
        for m in _ENVIRONMENT_RE.finditer(text):
            tag("environment", m.start(1), m.end(1))
        for m in _COMMENT_RE.finditer(text):
            tag("comment", m.start(), m.end())

        limit = len(text)
        for h in self._highlights:
            a = max(0, min(h.location, limit))
            b = max(a, min(h.location + h.length, limit))
            if b > a:
                name = ("bg-ai" if tuple(h.color) == AI_COLOR else
                        "bg-inserted" if tuple(h.color) == INSERTED_COLOR else "bg-deleted")
                tag(name, a, b)
        self._applied_key = tuple(h.key() for h in self._highlights)

    def scroll_to_offset(self, offset: int) -> None:
        it = self.buffer.get_iter_at_offset(min(offset, self.buffer.get_char_count()))
        self.view.scroll_to_iter(it, 0.2, False, 0.0, 0.0)

"""Continuous PDF preview with click-to-source mapping.

The GTK counterpart of PDFPreviewView.swift. Pages are rasterised with
``pdftoppm`` on a worker thread and cached as textures; word boxes come from
:mod:`latexcolab.core.pdfdoc`, which is what makes clicks, ⌥-clicks and drag
selections map back to LaTeX exactly as PDFKit's selections did.
"""

from __future__ import annotations

import threading
from typing import Callable

from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk

from ..core import pdfdoc

PAGE_GAP = 14
MARGIN = 10

SELECTION_RGBA = (0.20, 0.51, 0.89, 0.30)
FLASH_RGBA = (0.98, 0.75, 0.18, 0.45)

# What a click should open.
MODE_PARAGRAPH = "paragraph"
MODE_SENTENCE = "sentence"
MODE_SELECTION = "selection"


def _rgba(spec) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = spec
    return c


class PdfCanvas(Gtk.Widget):
    """Draws the pages; owns the page ↔ widget coordinate mapping."""

    def __init__(self, view: "PdfView"):
        super().__init__()
        self._view = view
        self.document: pdfdoc.PDFDocument | None = None
        self.scale = 1.0
        self._textures: dict[int, tuple[float, Gdk.Texture]] = {}
        self._rendering: set[int] = set()
        self._lock = threading.Lock()
        #: ``(page_index, Box, rgba)`` overlays drawn above the page bitmap.
        self.selection_boxes: list[tuple[int, pdfdoc.Box]] = []
        self.flash_boxes: list[tuple[int, pdfdoc.Box]] = []
        self.set_hexpand(True)
        self.set_vexpand(True)

    # -- layout ------------------------------------------------------------

    @property
    def pages(self) -> list[pdfdoc.Page]:
        return self.document.pages if self.document else []

    def set_document(self, document: pdfdoc.PDFDocument | None) -> None:
        self.document = document
        with self._lock:
            self._textures.clear()
            self._rendering.clear()
        self.selection_boxes = []
        self.flash_boxes = []
        self.queue_resize()

    def content_width(self) -> float:
        if not self.pages:
            return 200.0
        return max(p.width for p in self.pages) * self.scale + 2 * MARGIN

    def content_height(self) -> float:
        if not self.pages:
            return 200.0
        total = sum(p.height * self.scale for p in self.pages)
        return total + PAGE_GAP * (len(self.pages) - 1) + 2 * MARGIN

    def page_origin(self, index: int) -> tuple[float, float]:
        """Top-left of a page in widget coordinates."""
        y = MARGIN
        for i, p in enumerate(self.pages):
            if i == index:
                break
            y += p.height * self.scale + PAGE_GAP
        page = self.pages[index]
        width = self.get_width() or self.content_width()
        x = max(MARGIN, (width - page.width * self.scale) / 2)
        return x, y

    def point_to_page(self, wx: float, wy: float) -> tuple[int, float, float] | None:
        """Widget point → ``(page index, x, y)`` in PDF points from the page's top-left."""
        for i, page in enumerate(self.pages):
            ox, oy = self.page_origin(i)
            w, h = page.width * self.scale, page.height * self.scale
            if oy <= wy <= oy + h:
                return i, (wx - ox) / self.scale, (wy - oy) / self.scale
        return None

    def page_to_widget(self, index: int, x: float, y: float) -> tuple[float, float]:
        ox, oy = self.page_origin(index)
        return ox + x * self.scale, oy + y * self.scale

    def do_measure(self, orientation, for_size):  # noqa: N802
        if orientation == Gtk.Orientation.HORIZONTAL:
            size = int(self.content_width())
        else:
            size = int(self.content_height())
        return size, size, -1, -1

    # -- rendering ---------------------------------------------------------

    def _visible_range(self) -> tuple[float, float]:
        adj = self._view.get_vadjustment()
        if adj is None:
            return 0.0, float(self.get_height())
        return adj.get_value(), adj.get_value() + adj.get_page_size()

    def _request_render(self, index: int, dpi: float) -> None:
        with self._lock:
            if index in self._rendering:
                return
            cached = self._textures.get(index)
            if cached and abs(cached[0] - dpi) < 0.5:
                return
            self._rendering.add(index)
        path = self.document.path if self.document else None
        if not path:
            return

        def work() -> None:
            texture = None
            try:
                data = pdfdoc.render_png(path, index, dpi)
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except (pdfdoc.PDFError, GLib.Error):
                texture = None

            def finish() -> bool:
                with self._lock:
                    self._rendering.discard(index)
                    if texture is not None:
                        self._textures[index] = (dpi, texture)
                self.queue_draw()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:  # noqa: N802
        if not self.pages:
            return
        top, bottom = self._visible_range()
        dpi = 72.0 * self.scale * self.get_scale_factor()
        white = _rgba((1.0, 1.0, 1.0, 1.0))
        border = _rgba((0.0, 0.0, 0.0, 0.18))

        for i, page in enumerate(self.pages):
            ox, oy = self.page_origin(i)
            w, h = page.width * self.scale, page.height * self.scale
            if oy + h < top - 400 or oy > bottom + 400:
                continue
            rect = Graphene.Rect().init(ox, oy, w, h)
            snapshot.append_color(border, Graphene.Rect().init(ox - 1, oy - 1, w + 2, h + 2))
            snapshot.append_color(white, rect)
            with self._lock:
                cached = self._textures.get(i)
            if cached is not None:
                snapshot.append_scaled_texture(cached[1], Gsk.ScalingFilter.TRILINEAR, rect)
            self._request_render(i, dpi)

        for boxes, color in ((self.selection_boxes, SELECTION_RGBA),
                             (self.flash_boxes, FLASH_RGBA)):
            paint = _rgba(color)
            for index, box in boxes:
                if index >= len(self.pages):
                    continue
                x, y = self.page_to_widget(index, box.x0, box.y0)
                snapshot.append_color(paint, Graphene.Rect().init(
                    x, y, max(1.0, box.width * self.scale),
                    max(1.0, box.height * self.scale)))


class PdfView(Gtk.ScrolledWindow):
    """Scrollable preview. ``on_click`` receives
    ``(page_index, x, y, nearby_text, mode)`` with PDF-point coordinates."""

    def __init__(self, on_click: Callable[..., None]):
        super().__init__()
        self.on_click = on_click
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.canvas = PdfCanvas(self)
        self.set_child(self.canvas)

        self.fit_width = True
        self._zoom = 1.0
        self._flash_handle: int | None = None
        self._press: tuple[float, float] | None = None
        self._press_modifiers = 0
        #: The current drag selection: ``(text, start_page, start_pt, end_page, end_pt)``.
        self.selection: tuple[str, int, tuple[float, float], int, tuple[float, float]] | None = None
        self._menu_point: tuple[float, float] | None = None

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.canvas.add_controller(click)

        secondary = Gtk.GestureClick()
        secondary.set_button(3)
        secondary.connect("pressed", self._on_secondary)
        self.canvas.add_controller(secondary)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.canvas.add_controller(drag)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        self.menu = Gtk.PopoverMenu()
        self.menu.set_parent(self.canvas)
        self.menu.set_has_arrow(False)

        self.canvas.connect("notify::scale-factor", lambda *_: self.canvas.queue_draw())
        # The viewport's horizontal page size *is* the visible width, so this
        # fires exactly when a fit-width rescale is needed.
        self.get_hadjustment().connect("notify::page-size", lambda *_: self._on_width_changed())
        self._last_width = 0.0

        actions = Gio.SimpleActionGroup()
        for name, kind in (("edit-selection", MODE_SELECTION),
                           ("edit-sentence", MODE_SENTENCE),
                           ("edit-paragraph", MODE_PARAGRAPH)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, k=kind: self.context_edit(k))
            actions.add_action(action)
        self.insert_action_group("pdf", actions)

    # -- zoom --------------------------------------------------------------

    def _on_width_changed(self) -> None:
        width = self.get_hadjustment().get_page_size()
        if width and abs(width - self._last_width) > 1:
            self._last_width = width
            if self.fit_width:
                self._apply_fit()

    def _apply_fit(self) -> None:
        pages = self.canvas.pages
        if not pages:
            return
        visible = self.get_hadjustment().get_page_size() or self.get_width()
        available = max(200.0, visible - 2 * MARGIN - 4)
        widest = max(p.width for p in pages)
        scale = available / widest if widest else 1.0
        self._set_scale(max(0.15, min(scale, 6.0)))

    def _set_scale(self, scale: float) -> None:
        if abs(scale - self.canvas.scale) < 0.001:
            return
        self.canvas.scale = scale
        self.canvas.queue_resize()
        self.canvas.queue_draw()

    def zoom_in(self) -> None:
        self.fit_width = False
        self._set_scale(min(6.0, self.canvas.scale * 1.25))

    def zoom_out(self) -> None:
        self.fit_width = False
        self._set_scale(max(0.15, self.canvas.scale / 1.25))

    def zoom_fit(self) -> None:
        self.fit_width = True
        self._apply_fit()

    def _on_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_out() if dy > 0 else self.zoom_in()
            return True
        return False

    # -- document ----------------------------------------------------------

    def set_document(self, document: pdfdoc.PDFDocument | None, keep_position: bool = True) -> None:
        adj = self.get_vadjustment()
        value = adj.get_value() if adj and keep_position else 0.0
        self.selection = None
        self.canvas.set_document(document)
        if adj:
            def restore() -> bool:
                adj.set_value(min(value, max(0.0, adj.get_upper() - adj.get_page_size())))
                return False
            GLib.idle_add(restore)
        if self.fit_width:
            GLib.idle_add(lambda: (self._apply_fit(), False)[1])

    # -- focus / flashing --------------------------------------------------

    def show_focus(self, page_index: int, x: float, y: float,
                   width: float, height: float) -> None:
        """Scroll so the paragraph is near the top of the view and flash it."""
        if page_index >= len(self.canvas.pages):
            return
        box = pdfdoc.Box(x, y, x + width, y + height)
        self.flash(page_index, [box], seconds=2.5)
        _, wy = self.canvas.page_to_widget(page_index, x, y)
        adj = self.get_vadjustment()
        if adj:
            def scroll() -> bool:
                adj.set_value(max(0.0, min(wy - 60, adj.get_upper() - adj.get_page_size())))
                return False
            GLib.idle_add(scroll)

    def flash(self, page_index: int, boxes: list[pdfdoc.Box], seconds: float = 1.0) -> None:
        self.canvas.flash_boxes = [(page_index, b) for b in boxes]
        self.canvas.queue_draw()
        if self._flash_handle:
            GLib.source_remove(self._flash_handle)

        def clear() -> bool:
            self.canvas.flash_boxes = []
            self._flash_handle = None
            self.canvas.queue_draw()
            return False

        self._flash_handle = GLib.timeout_add(int(seconds * 1000), clear)

    def clear_selection(self) -> None:
        self.selection = None
        self.canvas.selection_boxes = []
        self.canvas.queue_draw()

    # -- gestures ----------------------------------------------------------

    def _on_pressed(self, gesture, n_press, x, y):
        self._press = (x, y)
        self._press_modifiers = gesture.get_current_event_state()

    def _on_released(self, gesture, n_press, x, y):
        press = self._press
        self._press = None
        if press is None or n_press > 1:
            return
        if abs(x - press[0]) > 4 or abs(y - press[1]) > 4:
            return
        state = self._press_modifiers
        # ⌥ (Alt) = sentence / selection; Ctrl and Shift are left alone.
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            return
        option = bool(state & Gdk.ModifierType.ALT_MASK)
        self._handle_click(x, y, option)

    def _on_secondary(self, gesture, n_press, x, y):
        self._menu_point = (x, y)
        menu = _build_context_menu(self.selection is not None)
        self.menu.set_menu_model(menu)
        self.menu.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        self.menu.popup()

    def context_edit(self, kind: str) -> None:
        """``kind`` is one of the MODE_* constants; called from the context menu."""
        if self._menu_point is None:
            return
        x, y = self._menu_point
        if kind == MODE_SELECTION:
            self._handle_click(x, y, True, force_selection=True)
        else:
            self._handle_click(x, y, kind == MODE_SENTENCE, ignore_selection=True)

    def _on_drag_begin(self, gesture, x, y):
        self.clear_selection()

    def _on_drag_update(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        self._update_selection(sx, sy, sx + dx, sy + dy)

    def _on_drag_end(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        if abs(dx) < 4 and abs(dy) < 4:
            return
        self._update_selection(sx, sy, sx + dx, sy + dy)

    def _update_selection(self, x0, y0, x1, y1) -> None:
        start = self.canvas.point_to_page(x0, y0)
        end = self.canvas.point_to_page(x1, y1)
        if start is None or end is None:
            return
        if (end[0], end[2]) < (start[0], start[2]):
            start, end = end, start
        boxes: list[tuple[int, pdfdoc.Box]] = []
        texts: list[str] = []
        for index in range(start[0], end[0] + 1):
            page = self.canvas.document.page(index) if self.canvas.document else None
            if page is None:
                continue
            first = (start[1], start[2]) if index == start[0] else (0.0, 0.0)
            last = (end[1], end[2]) if index == end[0] else (page.width, page.height)
            words = page.words_between(first, last)
            for w in words:
                boxes.append((index, w.box))
                texts.append(w.text)
        self.canvas.selection_boxes = boxes
        self.canvas.queue_draw()
        text = " ".join(texts).strip()
        if text:
            self.selection = (text, start[0], (start[1], start[2]),
                              end[0], (end[1], end[2]))
        else:
            self.selection = None

    # -- click → model -----------------------------------------------------

    def _handle_click(self, wx: float, wy: float, option: bool,
                      force_selection: bool = False,
                      ignore_selection: bool = False) -> None:
        hit = self.canvas.point_to_page(wx, wy)
        if hit is None or self.canvas.document is None:
            return
        index, px, py = hit
        page = self.canvas.document.page(index)
        if page is None:
            return

        nearby = page.text_in_band(py) or None

        if (option or force_selection) and not ignore_selection and self.selection:
            text, start_page, start_pt, end_page, end_pt = self.selection
            inside = any(b.inset(-6, -6).contains(px, py)
                         for i, b in self.canvas.selection_boxes if i == index)
            if force_selection or inside:
                self.on_click(start_page, start_pt[0], start_pt[1], nearby,
                              (MODE_SELECTION, text, end_page, end_pt[0], end_pt[1]))
                return

        # Flash the clicked line so the user sees what was picked.
        line = page.line_at(px, py)
        if line is not None:
            self.flash(index, [w.box for w in page.words_of_line(line)], seconds=1.0)

        if option:
            word = page.word_at(px, py)
            self.on_click(index, px, py, nearby, (MODE_SENTENCE, word.text if word else None))
        else:
            self.on_click(index, px, py, nearby, (MODE_PARAGRAPH,))


def _build_context_menu(has_selection: bool) -> Gio.Menu:
    menu = Gio.Menu()
    if has_selection:
        menu.append("Edit Selection…", "pdf.edit-selection")
    menu.append("Edit Sentence…", "pdf.edit-sentence")
    menu.append("Edit Paragraph…", "pdf.edit-paragraph")
    return menu

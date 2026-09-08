"""Project file tree sidebar (port of FileTreeView.swift)."""

from __future__ import annotations

from gi.repository import Gio, GObject, Gtk

from ..core import filetree
from ..model import AppModel

_ICONS = {
    "tex": "text-x-generic-symbolic",
    "pdf": "x-office-document-symbolic",
    "bib": "view-list-symbolic",
    "png": "image-x-generic-symbolic",
    "jpg": "image-x-generic-symbolic",
    "jpeg": "image-x-generic-symbolic",
    "gif": "image-x-generic-symbolic",
    "eps": "image-x-generic-symbolic",
    "svg": "image-x-generic-symbolic",
    "json": "text-x-generic-symbolic",
    "cls": "emblem-system-symbolic",
    "sty": "emblem-system-symbolic",
    "md": "text-x-generic-symbolic",
    "txt": "text-x-generic-symbolic",
}


class NodeItem(GObject.Object):
    """GObject wrapper so FileNode can live in a Gio.ListModel."""

    def __init__(self, node: filetree.FileNode):
        super().__init__()
        self.node = node


def _model_for(children) -> Gio.ListStore:
    store = Gio.ListStore(item_type=NodeItem)
    for child in children:
        store.append(NodeItem(child))
    return store


class FileTreeView(Gtk.Box):
    def __init__(self, model: AppModel, on_open_folder):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.model = model
        self.on_open_folder = on_open_folder
        self._selecting = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_top(6)
        header.set_margin_bottom(6)
        header.set_margin_start(10)
        header.set_margin_end(6)
        self.title = Gtk.Label(label="Files", xalign=0.0, ellipsize=3)
        self.title.add_css_class("heading")
        self.title.set_hexpand(True)
        header.append(self.title)

        open_button = Gtk.Button(icon_name="folder-open-symbolic")
        open_button.add_css_class("flat")
        open_button.set_tooltip_text("Open folder… (Ctrl+O)")
        open_button.connect("clicked", lambda *_: self.on_open_folder())
        header.append(open_button)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Refresh file tree (Ctrl+Shift+R)")
        refresh.connect("clicked", lambda *_: self.model.reload_tree())
        header.append(refresh)
        self.append(header)
        self.append(Gtk.Separator())

        self.root_store = Gio.ListStore(item_type=NodeItem)
        self.tree_model = Gtk.TreeListModel.new(self.root_store, False, False,
                                                self._create_children)
        self.selection = Gtk.SingleSelection(model=self.tree_model)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.connect("selection-changed", self._on_selection_changed)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.add_css_class("navigation-sidebar")
        self.list_view.connect("activate", self._on_activate)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.list_view)

        self.placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        self.placeholder.set_margin_start(16)
        self.placeholder.set_margin_end(16)
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        self.placeholder.append(icon)
        label = Gtk.Label(label="Open a folder that contains your .tex files.",
                          wrap=True, justify=Gtk.Justification.CENTER)
        label.add_css_class("dim-label")
        self.placeholder.append(label)
        button = Gtk.Button(label="Open Folder…")
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: self.on_open_folder())
        self.placeholder.append(button)

        self.stack = Gtk.Stack()
        self.stack.add_named(self.placeholder, "empty")
        self.stack.add_named(scroller, "tree")
        self.stack.set_vexpand(True)
        self.append(self.stack)

        model.subscribe("tree", self.refresh)
        model.subscribe("project", self.refresh)
        model.subscribe("editor-dirty", lambda: self.list_view.queue_draw())
        self.refresh()

    # -- model glue --------------------------------------------------------

    def _create_children(self, item: NodeItem):
        node = item.node
        if not node.is_directory or not node.children:
            return None
        return _model_for(node.children)

    def refresh(self) -> None:
        self.root_store.remove_all()
        for node in self.model.tree:
            self.root_store.append(NodeItem(node))
        if self.model.project_path:
            import os
            self.title.set_text(os.path.basename(self.model.project_path))
            self.title.set_tooltip_text(self.model.project_path)
            self.stack.set_visible_child_name("tree")
        else:
            self.title.set_text("Files")
            self.stack.set_visible_child_name("empty")

    # -- rows --------------------------------------------------------------

    def _setup_row(self, _factory, list_item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image()
        label = Gtk.Label(xalign=0.0, ellipsize=3)
        badge = Gtk.Label(label="main")
        badge.add_css_class("caption")
        badge.add_css_class("accent")
        dirty = Gtk.Label(label="●")
        dirty.add_css_class("warning")
        box.append(icon)
        box.append(label)
        box.append(badge)
        box.append(dirty)
        expander.set_child(box)
        list_item.set_child(expander)
        list_item.icon, list_item.label = icon, label
        list_item.badge, list_item.dirty = badge, dirty

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_right_click, list_item)
        expander.add_controller(gesture)

    def _bind_row(self, _factory, list_item: Gtk.ListItem) -> None:
        row = list_item.get_item()
        node = row.get_item().node
        expander = list_item.get_child()
        expander.set_list_row(row)
        list_item.icon.set_from_icon_name(
            "folder-symbolic" if node.is_directory
            else _ICONS.get(node.file_extension, "text-x-generic-symbolic"))
        list_item.label.set_text(node.name)
        list_item.badge.set_visible(node.id == self.model.main_file)
        list_item.dirty.set_visible(node.id == self.model.editor_path
                                    and self.model.editor_dirty)

    def _on_right_click(self, gesture, _n, x, y, list_item: Gtk.ListItem) -> None:
        row = list_item.get_item()
        if row is None:
            return
        node = row.get_item().node
        menu = Gio.Menu()
        if not node.is_directory:
            menu.append("Open in Editor", f"tree.open::{node.id}")
            if node.file_extension == "tex" and node.id != self.model.main_file:
                menu.append("Set as Main File", f"tree.main::{node.id}")
        menu.append("Show in Files", f"tree.reveal::{node.id}")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(list_item.get_child())
        popover.set_has_arrow(False)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _on_selection_changed(self, *_args) -> None:
        if self._selecting:
            return
        row = self.selection.get_selected_item()
        if row is None:
            return
        node = row.get_item().node
        if not node.is_directory:
            self.model.select_from_tree(node.id)

    def _on_activate(self, _view, position: int) -> None:
        row = self.tree_model.get_item(position)
        if row is None:
            return
        node = row.get_item().node
        if node.is_directory:
            row.set_expanded(not row.get_expanded())
        else:
            self.model.open_file(node.id)

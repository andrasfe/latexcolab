"""Application shell: actions, keyboard shortcuts, windows.

Port of LaTeXColabApp.swift. Shortcuts use the Linux conventions (Ctrl where
macOS used ⌘), keeping every command the macOS app had.
"""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .. import __version__
from ..model import VIEW_EDITOR, VIEW_PDF, AppModel
from ..scheduling import Scheduler
from .mainwindow import MainWindow
from .paragraphwindow import ParagraphWindow
from .settings import SettingsWindow

APP_ID = "dev.latexcolab.LaTeXColab"

#: ``action name → (callback attribute, accelerators)`` — the whole command set.
_ACCELS = {
    "open-folder": ["<Control>o"],
    "export-zip": ["<Control><Shift>e"],
    "save": ["<Control>s"],
    "regenerate": ["<Control>r"],
    "refresh-tree": ["<Control><Shift>r"],
    "toggle-view": ["<Control>e"],
    "show-pdf": ["<Control>1"],
    "show-editor": ["<Control>2"],
    "toggle-log": ["<Control>l"],
    "paragraph-editor": ["<Control><Shift>p"],
    "settings": ["<Control>comma"],
    "quit": ["<Control>q"],
    "git-pull": ["<Control><Shift>l"],
    "git-commit": ["<Control><Shift>c"],
    "git-push": ["<Control><Shift>u"],
    "zoom-in": ["<Control>plus", "<Control>equal"],
    "zoom-out": ["<Control>minus"],
    "zoom-fit": ["<Control>0"],
}


class LaTeXColabApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.model: AppModel | None = None
        self.window: MainWindow | None = None
        self.paragraph_window: ParagraphWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self._project_argument: str | None = None
        self.add_main_option("project", ord("p"), GLib.OptionFlags.NONE,
                             GLib.OptionArg.FILENAME, "Project folder to open", "DIR")

    # -- lifecycle ---------------------------------------------------------

    def do_command_line(self, command_line) -> int:  # noqa: N802
        options = command_line.get_options_dict().end().unpack()
        argv = command_line.get_arguments()
        path = options.get("project")
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        if not path and len(argv) > 1:
            path = argv[1]
        self._project_argument = path
        self.activate()
        return 0

    def do_activate(self) -> None:  # noqa: N802
        if self.window is not None:
            self.window.present()
            return

        self.model = AppModel(Scheduler())
        self.window = MainWindow(self, self.model)
        self.paragraph_window = ParagraphWindow(self.window, self.model)

        self.model.on_open_settings = self.show_settings
        self.model.on_ask_rebase = self.window.ask_rebase
        self.model.on_ask_remote = self.window.ask_remote

        self._install_actions()
        self.window.present()

        if self._project_argument:
            import os
            path = os.path.abspath(os.path.expanduser(self._project_argument))
            if os.path.isdir(path):
                self.model.open_project(path)
            else:
                self.model.set_status(f"{path} is not a folder", "error")
        else:
            self.model.restore_last_project()

    # -- actions -----------------------------------------------------------

    def _install_actions(self) -> None:
        model = self.model
        window = self.window
        simple = {
            "open-folder": window.choose_project_folder,
            "export-zip": window.choose_zip_destination,
            "save": model.save_editor,
            "regenerate": model.regenerate,
            "refresh-tree": model.reload_tree,
            "toggle-view": model.toggle_view_mode,
            "show-pdf": lambda: model.set_view_mode(VIEW_PDF),
            "show-editor": lambda: model.set_view_mode(VIEW_EDITOR),
            "toggle-log": lambda: model.set_show_log(not model.show_log),
            "paragraph-editor": window._open_paragraph_editor,
            "settings": self.show_settings,
            "about": self.show_about,
            "quit": self.quit,
            "git-init": model.git_init,
            "git-fetch": model.git_fetch,
            "git-pull": model.git_pull,
            "git-commit": window.open_commit_dialog,
            "git-push": model.git_push,
            "git-log": model.git_log,
            "git-remote": window.ask_remote,
            "git-new-branch": window.ask_new_branch,
            "zoom-in": window.pdf_view.zoom_in,
            "zoom-out": window.pdf_view.zoom_out,
            "zoom-fit": window.pdf_view.zoom_fit,
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

        for name, target in (("git-checkout", lambda v: model.git_checkout(v, False)),
                             ("tree-open", model.open_file),
                             ("tree-main", model.set_main_file),
                             ("tree-reveal", model.reveal_in_file_manager)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, p, cb=target: cb(p.get_string()))
            self.add_action(action)

        for name, accels in _ACCELS.items():
            self.set_accels_for_action(f"app.{name}", accels)

        # The file-tree context menu lives in a "tree" group on the window.
        tree_actions = Gio.SimpleActionGroup()
        for name, target in (("open", model.open_file), ("main", model.set_main_file),
                             ("reveal", model.reveal_in_file_manager)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, p, cb=target: cb(p.get_string()))
            tree_actions.add_action(action)
        window.insert_action_group("tree", tree_actions)

    # -- windows -----------------------------------------------------------

    def show_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.window, self.model)
            self.settings_window.connect(
                "close-request", lambda *_: (self.settings_window.set_visible(False), True)[1])
        self.settings_window.present()

    def show_about(self) -> None:
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name="LaTeX Colab",
            application_icon="x-office-document",
            version=__version__,
            comments=("LaTeX editor with a live PDF preview, click-to-edit paragraphs "
                      "with a local LM Studio model, and git — the native Linux build."),
            license_type=Gtk.License.MIT_X11,
            developer_name="LaTeX Colab")
        about.present()


def main(argv: list[str] | None = None) -> int:
    import sys

    app = LaTeXColabApplication()
    return app.run(argv if argv is not None else sys.argv)

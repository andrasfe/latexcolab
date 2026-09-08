"""Preferences window (port of SettingsView.swift)."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ..core.config import CONFIG_PATH
from ..core.lmstudio import DEFAULT_BASE_URL
from ..model import AppModel


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, parent: Gtk.Window, model: AppModel):
        super().__init__(transient_for=parent, modal=False, title="Settings")
        self.model = model
        self._loading = True
        self.set_default_size(620, 640)

        page = Adw.PreferencesPage()
        self.add(page)

        # -- LM Studio -----------------------------------------------------
        lm = Adw.PreferencesGroup(title="LM Studio")
        lm.set_description(
            f"Requests go to {model.config.lmstudio_url}/v1/chat/completions. "
            "No API key is needed and nothing leaves your machine.")
        page.add(lm)

        self.url_row = Adw.EntryRow(title="Server URL")
        self.url_row.set_text(model.config.lmstudio_url)
        self.url_row.connect("apply", lambda *_: self._save_and_refresh())
        self.url_row.connect("changed", lambda *_: self._save())
        lm.add(self.url_row)

        self.model_row = Adw.ComboRow(title="Model",
                                      subtitle="Whatever is loaded, unless you pick one")
        self.model_list = Gtk.StringList()
        self.model_row.set_model(self.model_list)
        self.model_row.connect("notify::selected", lambda *_: self._save())
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Reload the model list from LM Studio")
        refresh.connect("clicked", lambda *_: self._save_and_refresh())
        self.model_row.add_suffix(refresh)
        lm.add(self.model_row)

        self.models_status = Adw.ActionRow(title="Models")
        self.models_status.add_css_class("dim-label")
        lm.add(self.models_status)

        self.temperature = Adw.SpinRow.new_with_range(0.0, 1.0, 0.05)
        self.temperature.set_title("Temperature")
        self.temperature.set_value(model.config.temperature)
        self.temperature.set_digits(2)
        self.temperature.connect("notify::value", lambda *_: self._save())
        lm.add(self.temperature)

        # -- Paragraph editing ---------------------------------------------
        editing = Adw.PreferencesGroup(
            title="Paragraph editing",
            description=(f"Drafts live in {model.edits_file_name} inside the project "
                         "folder. Apply writes them into the .tex file."))
        page.add(editing)

        self.max_words = Adw.SpinRow.new_with_range(0, 100, 1)
        self.max_words.set_title("Default max words to change")
        self.max_words.set_value(model.config.default_max_words)
        self.max_words.connect("notify::value", lambda *_: self._save())
        editing.add(self.max_words)

        self.auto_regen = Adw.SwitchRow(title="Regenerate the PDF after Apply")
        self.auto_regen.set_active(model.config.auto_regenerate_after_apply)
        self.auto_regen.connect("notify::active", lambda *_: self._save())
        editing.add(self.auto_regen)

        self.close_after = Adw.SwitchRow(title="Close the paragraph window after Apply")
        self.close_after.set_active(model.config.close_window_after_apply)
        self.close_after.connect("notify::active", lambda *_: self._save())
        editing.add(self.close_after)

        # -- Build ---------------------------------------------------------
        build = Adw.PreferencesGroup(
            title="Build",
            description=("A name (latexmk, pdflatex, tectonic, xelatex) or a full path. "
                         "Searched on PATH and in TeX Live, TinyTeX, conda and "
                         "/snap/bin locations."))
        page.add(build)

        self.engine_row = Adw.EntryRow(title="LaTeX engine")
        self.engine_row.set_text(model.config.latex_engine)
        self.engine_row.connect("changed", lambda *_: self._save())
        build.add(self.engine_row)

        self.resolved = Adw.ActionRow(title="Resolved to")
        build.add(self.resolved)
        self.main_row = Adw.ActionRow(title="Main file")
        build.add(self.main_row)
        settings_row = Adw.ActionRow(title="Settings file", subtitle=str(CONFIG_PATH))
        settings_row.set_subtitle_selectable(True)
        build.add(settings_row)

        model.subscribe("lmmodels", self._refresh_models)
        model.subscribe("project", self._refresh_build)
        self._loading = False
        self._refresh_models()
        self._refresh_build()
        self.model.refresh_lm_models()

    # -- helpers -----------------------------------------------------------

    def _chat_models(self) -> list:
        return [m for m in self.model.lm_models if m.is_chat_model]

    def _refresh_models(self) -> None:
        self._loading = True
        configured = self.model.config.model
        entries = ["Whatever is loaded (automatic)"]
        ids = [""]
        chat = self._chat_models()
        if configured and all(m.id != configured for m in chat):
            entries.append(configured)
            ids.append(configured)
        for m in chat:
            entries.append(f"{m.id} · loaded" if m.is_loaded else m.id)
            ids.append(m.id)
        self.model_list.splice(0, self.model_list.get_n_items(), entries)
        self._model_ids = ids
        try:
            self.model_row.set_selected(ids.index(configured))
        except ValueError:
            self.model_row.set_selected(0)

        if self.model.lm_models_error:
            self.models_status.set_title("LM Studio not reachable")
            self.models_status.set_subtitle(self.model.lm_models_error)
            self.models_status.add_css_class("error")
        else:
            loaded = sum(1 for m in self.model.lm_models if m.is_loaded)
            self.models_status.remove_css_class("error")
            self.models_status.set_title(
                f"{len(chat)} chat model{'' if len(chat) == 1 else 's'} · {loaded} loaded")
            self.models_status.set_subtitle("")
        self._loading = False

    def _refresh_build(self) -> None:
        self.resolved.set_subtitle(self.model.engine_path or "no engine found")
        self.main_row.set_subtitle(self.model.main_file)

    def _save(self) -> None:
        if self._loading:
            return
        cfg = self.model.config
        cfg.lmstudio_url = self.url_row.get_text().strip() or DEFAULT_BASE_URL
        index = self.model_row.get_selected()
        ids = getattr(self, "_model_ids", [""])
        cfg.model = ids[index] if 0 <= index < len(ids) else ""
        cfg.temperature = self.temperature.get_value()
        cfg.default_max_words = int(self.max_words.get_value())
        cfg.auto_regenerate_after_apply = self.auto_regen.get_active()
        cfg.close_window_after_apply = self.close_after.get_active()
        cfg.latex_engine = self.engine_row.get_text().strip()
        self.model.save_config()
        self.model.refresh_engine()
        self._refresh_build()

    def _save_and_refresh(self) -> None:
        self._save()
        self.model.refresh_lm_models()

"""Settings in ``~/.latexcolab/config.json`` (port of AppConfig.swift).

The file is shared with the web app and the macOS app, so unknown keys are
preserved on save and ``last_project`` means the same thing everywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_LMSTUDIO_URL = "http://127.0.0.1:1234"

CONFIG_PATH = Path.home() / ".latexcolab" / "config.json"


class AppConfig:
    def __init__(self, raw: dict[str, Any] | None = None, path: Path | None = None):
        self.raw: dict[str, Any] = dict(raw or {})
        self.path = Path(path) if path else CONFIG_PATH

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        p = Path(path) if path else CONFIG_PATH
        try:
            with open(p, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if not isinstance(obj, dict):
                obj = {}
        except (OSError, ValueError):
            obj = {}
        return cls(obj, p)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.raw, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    # -- typed accessors ---------------------------------------------------

    @property
    def last_project(self) -> str | None:
        v = self.raw.get("last_project")
        return v if isinstance(v, str) and v else None

    @last_project.setter
    def last_project(self, value: str | None) -> None:
        self.raw["last_project"] = value

    @property
    def lmstudio_url(self) -> str:
        v = self.raw.get("lmstudio_url")
        return v if isinstance(v, str) and v else DEFAULT_LMSTUDIO_URL

    @lmstudio_url.setter
    def lmstudio_url(self, value: str) -> None:
        self.raw["lmstudio_url"] = value

    @property
    def model(self) -> str:
        """Empty means "use whatever LM Studio has loaded"."""
        v = self.raw.get("lmstudio_model")
        return v if isinstance(v, str) else ""

    @model.setter
    def model(self, value: str) -> None:
        self.raw["lmstudio_model"] = value

    @property
    def temperature(self) -> float:
        v = self.raw.get("temperature")
        return float(v) if isinstance(v, (int, float)) else 0.2

    @temperature.setter
    def temperature(self, value: float) -> None:
        self.raw["temperature"] = float(value)

    @property
    def latex_engine(self) -> str:
        """Empty = automatic (latexmk, then pdflatex, then tectonic, anywhere on disk)."""
        v = self.raw.get("latex_engine")
        return v if isinstance(v, str) else ""

    @latex_engine.setter
    def latex_engine(self, value: str) -> None:
        self.raw["latex_engine"] = value

    @property
    def close_window_after_apply(self) -> bool:
        v = self.raw.get("close_window_after_apply")
        return bool(v) if isinstance(v, bool) else True

    @close_window_after_apply.setter
    def close_window_after_apply(self, value: bool) -> None:
        self.raw["close_window_after_apply"] = bool(value)

    @property
    def auto_regenerate_after_apply(self) -> bool:
        v = self.raw.get("auto_regenerate_after_apply")
        return bool(v) if isinstance(v, bool) else True

    @auto_regenerate_after_apply.setter
    def auto_regenerate_after_apply(self, value: bool) -> None:
        self.raw["auto_regenerate_after_apply"] = bool(value)

    @property
    def default_max_words(self) -> int:
        v = self.raw.get("default_max_words")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 20

    @default_max_words.setter
    def default_max_words(self, value: int) -> None:
        self.raw["default_max_words"] = int(value)

    def main_file(self, project: str | os.PathLike) -> str | None:
        table = self.raw.get("main_files")
        if isinstance(table, dict):
            v = table.get(str(project))
            if isinstance(v, str) and v:
                return v
        return None

    def set_main_file(self, name: str, project: str | os.PathLike) -> None:
        table = self.raw.get("main_files")
        if not isinstance(table, dict):
            table = {}
        table[str(project)] = name
        self.raw["main_files"] = table

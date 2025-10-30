from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
from .paths import app_data_dir


class SettingsManager:
    DEFAULTS: Dict[str, Any] = {
        "user": {"name": ""},
        "ui": {
            "theme": "dark",
            "font_delta": 0,           # global text size offset (pt)
            "base_point_size": None,   # captured once by UI on first run (stored as int)
            "base_font_family": "",    # captured once by UI on first run (stored as str)
            "rows_per_page": 500
        },
        "register": {
            "sheet_name": "MI Documents",
            "doc_id_col": "B", "doc_type_col": "C",
            "file_type_col": "D", "description_col": "E", "status_col": "F",
            "start_row": 10, "rev_start_col": "G", "rev_end_col": "BZ"
        },
        "mapping": {
            "ask_for_search_roots": False,
            "search_roots": [],
            "extensions": [".pdf", ".dwg", ".docx", ".xlsx"],
            "prefer_revision_suffix": True
        },
        "export": {"output_root_override": ""},
        "projects": {"recent": []}
    }

    def __init__(self) -> None:
        self._path: Path = app_data_dir() / "settings.json"
        self._data: Dict[str, Any] = {}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    # ---- internal helpers -------------------------------------------------

    def _deep_merge_defaults(self, data: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
        """
        Merge `defaults` into `data` in-place without overwriting existing user values.
        Returns True if any changes were made.
        """
        changed = False
        for k, v in defaults.items():
            if isinstance(v, dict):
                if not isinstance(data.get(k), dict):
                    data[k] = {}
                    changed = True
                if self._deep_merge_defaults(data[k], v):
                    changed = True
            else:
                if k not in data:
                    data[k] = v
                    changed = True
        return changed

    # ---- public api --------------------------------------------------------

    def load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._data = dict(self.DEFAULTS)
                self.save()
        else:
            self._data = dict(self.DEFAULTS)
            self.save()

        # Back-fill any new defaults into existing settings without clobbering user values
        if self._deep_merge_defaults(self._data, self.DEFAULTS):
            self.save()

    def save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._data
        parts = dotted.split(".")
        for i, part in enumerate(parts):
            if not isinstance(cur, dict):
                return default
            cur = cur.get(part, default if i == len(parts) - 1 else {})
        return cur

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self._data
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
        self.save()

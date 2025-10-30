# project_store.py
from __future__ import annotations
from pathlib import Path
import json, os, time
from typing import Any, Dict, Optional, Union

from ..core.paths import app_data_dir  # <-- use your helper

DEBUG_PS = True

def _ts(): return time.strftime("%H:%M:%S")
def _dbg(*a):
    if DEBUG_PS:
        print("[ProjectStore", _ts(), "]", *a)

def _canon(p: Optional[Union[str, Path]]) -> str:
    if not p:
        return ""
    try:
        return str(Path(p).resolve())
    except Exception:
        return os.path.normcase(os.path.normpath(str(p)))

class ProjectStore:
    def __init__(self, store_path: Optional[Union[str, Path]] = None):
        # Default location: C:\Users\...\AppData\Local\DocumentTransmittal\project_store.json (on Windows)
        default_path = app_data_dir() / "project_store.json"
        self.store_path = Path(store_path) if store_path else default_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {"by_register": {}, "by_root": {}}
        _dbg("INIT store_path =", str(self.store_path))
        self._load()

    # ---- Drop-in replacements / additions in ProjectStore ----
    def __init__(self):
        # ensure a real path and a dict in memory
        self.store_path = Path(app_data_dir()) / "project_store.json"
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = None
        self._load()

    def _load(self) -> Dict[str, Any]:
        """
        Always return a dict. Ensure required top-level keys exist.
        """
        try:
            if not self.store_path.exists():
                self._data = {"by_register": {}, "by_root": {}, "row_options": {}}
                self._save()
                _dbg("LOAD: ok (new file); by_register keys:", list(self._data["by_register"].keys()))
                return self._data

            raw = self.store_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            _dbg("LOAD: FAILED", e)
            data = {}

        if not isinstance(data, dict):
            data = {}

        # Guarantee required sections
        data.setdefault("by_register", {})
        data.setdefault("by_root", {})
        data.setdefault("row_options", {})

        self._data = data
        _dbg("LOAD: ok; by_register keys:", list(self._data["by_register"].keys()))
        return self._data

    def _save(self):
        """
        Write the current dict to disk safely.
        """
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            if not isinstance(self._data, dict):
                self._data = {"by_register": {}, "by_root": {}, "row_options": {}}
            self.store_path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            _dbg("SAVE: ok to", str(self.store_path))
        except Exception as e:
            _dbg("SAVE: FAILED", e)

    # --- Per-project row-attribute options (used by the Row Attributes dialog) ---

    def get_row_options(self, project_code: str) -> dict:
        data = self._load()  # always dict
        ra = data.get("row_options", {})
        return dict(ra.get(project_code, {}))

    def set_row_options(self, project_code: str, options: dict) -> None:
        data = self._load()  # always dict
        ra = data.setdefault("row_options", {})
        ra[project_code] = {
            "doc_types": list(options.get("doc_types", [])),
            "file_types": list(options.get("file_types", [])),
            "statuses": list(options.get("statuses", [])),
        }
        self._save()

    def get_for_register(self, register_path: Optional[Union[str, Path]]):
        key = _canon(register_path)
        rec = self._data.get("by_register", {}).get(key)
        _dbg("GET register:", key, "->", "HIT" if rec else "MISS")
        return rec

    def get_for_root(self, project_root: Optional[Union[str, Path]]):
        key = _canon(project_root)
        rec = self._data.get("by_root", {}).get(key)
        _dbg("GET root    :", key, "->", "HIT" if rec else "MISS")
        return rec

    def upsert(self, *, register_path: Optional[Union[str, Path]], project_root: Optional[Union[str, Path]], meta: Dict[str, Any]) -> None:
        rkey = _canon(register_path)
        pkey = _canon(project_root)
        meta = dict(meta or {})
        meta.setdefault("extra", {})
        self._data.setdefault("by_register", {})
        self._data.setdefault("by_root", {})
        if rkey: self._data["by_register"][rkey] = meta
        if pkey: self._data["by_root"][pkey] = meta
        _dbg("UPSERT:", "register_key:", rkey, "| root_key:", pkey,
             "| meta.job_number:", meta.get("job_number"),
             "| meta.project_name:", meta.get("project_name"),
             "| extra_keys:", list(meta.get("extra", {}).keys()))
        self._save()

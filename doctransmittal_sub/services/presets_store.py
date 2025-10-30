# services/presets_store.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json, os

from doctransmittal_sub.core.paths import project_state_dir

def _canon(p: Optional[os.PathLike] | Optional[str]) -> str:
    if not p:
        return ""
    try:
        return str(Path(p).resolve())
    except Exception:
        return os.path.normcase(os.path.normpath(str(p)))

class PresetsStore:
    """
    Stores presets per *project* (preferred) and also per *register* as a fallback key.
    File location: project_state_dir(project_root)/presets.json

    JSON shape:
    {
      "by_register": {
        "<abs register path>": {
          "My Preset A": ["DOC-001", "DOC-002"]
        }
      },
      "by_root": {
        "<abs project root>": {
          "Team Preset": ["DOC-010", "DOC-099"]
        }
      }
    }
    """
    def __init__(self) -> None:
        pass

    def _store_path(self, register_path: Optional[Path], project_root: Optional[Path]) -> Path:
        root = Path(project_root) if project_root else (Path(register_path).parent if register_path else None)
        if root is None:
            # very last fallback: put something in user-local app state for "unknown" projects
            # but prefer to always call with a real project root.
            root = Path(os.path.expanduser("~"))
        return project_state_dir(root) / "presets.json"

    def _load_json(self, p: Path) -> Dict:
        if not p.exists():
            return {"by_register": {}, "by_root": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {"by_register": {}, "by_root": {}}
        except Exception:
            return {"by_register": {}, "by_root": {}}

    def _save_json(self, p: Path, data: Dict) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --------- Public API ---------

    def list_presets(self, register_path: Optional[Path], project_root: Optional[Path]) -> List[str]:
        sp = self._store_path(register_path, project_root)
        data = self._load_json(sp)
        rk, pk = _canon(register_path), _canon(project_root)
        names = set()
        # register-specific take precedence in UIs (but union is helpful)
        names.update(list((data.get("by_register", {}).get(rk, {}) or {}).keys()))
        names.update(list((data.get("by_root", {}).get(pk, {}) or {}).keys()))
        return sorted(names)

    def get_preset(self, name: str, register_path: Optional[Path], project_root: Optional[Path]) -> List[str]:
        sp = self._store_path(register_path, project_root)
        data = self._load_json(sp)
        rk, pk = _canon(register_path), _canon(project_root)

        # Prefer register key, then project root
        hit = (data.get("by_register", {}).get(rk, {}) or {}).get(name)
        if isinstance(hit, list):
            return hit

        hit = (data.get("by_root", {}).get(pk, {}) or {}).get(name)
        if isinstance(hit, list):
            return hit

        return []

    def save_preset(self, name: str, doc_ids: List[str], register_path: Optional[Path],
                    project_root: Optional[Path], scope: str = "register") -> bool:
        """
        scope: "register" (default) or "root".
        Returns True on success.
        """
        sp = self._store_path(register_path, project_root)
        data = self._load_json(sp)
        data.setdefault("by_register", {})
        data.setdefault("by_root", {})

        rk, pk = _canon(register_path), _canon(project_root)
        target = data["by_register"] if scope == "register" else data["by_root"]
        key = rk if scope == "register" else pk
        target.setdefault(key, {})
        target[key][name] = list(sorted(set(doc_ids)))
        self._save_json(sp, data)
        return True

    def delete_preset(self, name: str, register_path: Optional[Path], project_root: Optional[Path]) -> bool:
        sp = self._store_path(register_path, project_root)
        data = self._load_json(sp)
        rk, pk = _canon(register_path), _canon(project_root)

        changed = False
        br, broot = data.get("by_register", {}), data.get("by_root", {})
        if rk in br and name in br[rk]:
            del br[rk][name]; changed = True
        if pk in broot and name in broot[pk]:
            del broot[pk][name]; changed = True

        if changed:
            self._save_json(sp, data)
        return changed

    def rename_preset(self, old_name: str, new_name: str, register_path: Optional[Path], project_root: Optional[Path]) -> bool:
        if old_name == new_name:
            return False
        sp = self._store_path(register_path, project_root)
        data = self._load_json(sp)
        rk, pk = _canon(register_path), _canon(project_root)
        changed = False

        for bucket, key in (("by_register", rk), ("by_root", pk)):
            d = data.setdefault(bucket, {}).get(key, {})
            if old_name in d:
                d[new_name] = d.pop(old_name)
                changed = True

        if changed:
            self._save_json(sp, data)
        return changed

    def load_preset(self, name: str, register_path: Optional[Path] = None, project_root: Optional[Path] = None) -> list:
        """
        Return the list of Doc IDs for a preset name. Prefers a preset saved
        against the specific register/db path; falls back to the project_root bucket.
        Always returns a list (possibly empty).
        """
        sp = self._store_path(register_path, project_root)
        data = self._load_json(sp)
        rk, pk = _canon(register_path), _canon(project_root)

        # Prefer register/db-scoped preset, then fall back to project-root
        for bucket, key in (("by_register", rk), ("by_root", pk)):
            d = data.get(bucket, {}).get(key, {})
            if not isinstance(d, dict):
                continue
            if name in d:
                v = d[name]
                # Support either plain list or a legacy {"ids":[...]} shape
                if isinstance(v, list):
                    return [str(x) for x in v]
                if isinstance(v, dict) and "ids" in v and isinstance(v["ids"], list):
                    return [str(x) for x in v["ids"]]
                # Unknown shape; be defensive
                return []

        return []

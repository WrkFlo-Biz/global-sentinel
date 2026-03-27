"""Local/file-first artifact helper for quantum research artifacts.

Extend later with Azure Blob SDK methods if needed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class StorageArtifactIO:

    def __init__(self, base_dir: str = "artifacts"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def read_json(self, rel_path: str) -> Dict[str, Any]:
        path = self.base_dir / rel_path
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, rel_path: str, payload: Dict[str, Any]) -> Path:
        path = self.base_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def list_json(self, rel_dir: str) -> List[Path]:
        path = self.base_dir / rel_dir
        if not path.exists():
            return []
        return sorted(path.glob("*.json"))

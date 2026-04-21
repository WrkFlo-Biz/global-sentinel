#!/usr/bin/env python3
"""Registry of all feature groups with metadata and versioning."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class FeatureGroupRegistry:
    """Registry of feature groups with version tracking."""

    def __init__(self):
        self._groups: Dict[str, List[Dict[str, Any]]] = {}

    def register_group(
        self,
        name: str,
        version: str,
        schema: Dict[str, Any],
        source_bridges: List[str],
        description: str = "",
    ) -> Dict[str, Any]:
        entry = {
            "name": name,
            "version": version,
            "schema": schema,
            "source_bridges": source_bridges,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "deprecated_at": None,
        }
        self._groups.setdefault(name, []).append(entry)
        return entry

    def get_group(self, name: str, version: Optional[str] = None) -> Optional[Dict[str, Any]]:
        versions = self._groups.get(name, [])
        if not versions:
            return None
        if version:
            for v in versions:
                if v["version"] == version:
                    return v
            return None
        # Return latest non-deprecated
        active = [v for v in versions if v["deprecated_at"] is None]
        return active[-1] if active else versions[-1]

    def list_groups(self) -> List[Dict[str, Any]]:
        result = []
        for name, versions in self._groups.items():
            latest = self.get_group(name)
            if latest:
                result.append(latest)
        return result

    def deprecate_group(self, name: str, version: str) -> bool:
        versions = self._groups.get(name, [])
        for v in versions:
            if v["version"] == version:
                v["deprecated_at"] = datetime.now(timezone.utc).isoformat()
                return True
        return False

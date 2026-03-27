#!/usr/bin/env python3
"""Encoder version management with save, rollback, and listing.

Stores encoder state snapshots to local storage (artifacts/encoder_versions/)
with versioned metadata for safe promotion and rollback.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CURRENT_POINTER = "_current.json"


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


class EncoderVersionManager:
    """Manage encoder state versions with save/rollback/list operations."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self._dir = storage_dir or Path("artifacts/encoder_versions")
        self._dir.mkdir(parents=True, exist_ok=True)

    def save_version(
        self,
        encoder_state: Dict[str, Any],
        version_tag: str,
        metadata: Dict[str, Any] | None = None,
    ) -> str:
        """Save encoder state with a version tag. Returns the version tag."""
        metadata = metadata or {}
        ts = datetime.now(timezone.utc).isoformat()
        content = json.dumps(encoder_state, indent=2, default=str)
        content_hash = _sha256(content)

        envelope = {
            "schema_version": "encoder_version.v1",
            "version_tag": version_tag,
            "timestamp_utc": ts,
            "content_hash": content_hash,
            "encoder_state": encoder_state,
            "metadata": metadata,
        }

        path = self._dir / f"{version_tag}.json"
        path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
        logger.info("Saved encoder version %s -> %s", version_tag, path)

        # Update current pointer
        self._set_current(version_tag)

        return version_tag

    def rollback_to(self, version_tag: str) -> Dict[str, Any]:
        """Load a previous encoder version and set it as current.

        Returns the encoder_state dict.
        Raises FileNotFoundError if version does not exist.
        """
        path = self._dir / f"{version_tag}.json"
        if not path.exists():
            raise FileNotFoundError(f"Encoder version not found: {version_tag}")

        envelope = json.loads(path.read_text(encoding="utf-8"))
        self._set_current(version_tag)
        logger.info("Rolled back encoder to version %s", version_tag)
        return envelope["encoder_state"]

    def list_versions(self) -> List[Dict[str, Any]]:
        """List all saved versions with metadata, newest first."""
        versions = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            if path.name == _CURRENT_POINTER:
                continue
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                versions.append({
                    "version_tag": envelope.get("version_tag", path.stem),
                    "timestamp_utc": envelope.get("timestamp_utc"),
                    "content_hash": envelope.get("content_hash"),
                    "metadata": envelope.get("metadata", {}),
                })
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Skipping malformed version file %s: %s", path.name, e)
        return versions

    def current_version(self) -> Optional[str]:
        """Return the current active version tag, or None if not set."""
        pointer_path = self._dir / _CURRENT_POINTER
        if not pointer_path.exists():
            return None
        try:
            data = json.loads(pointer_path.read_text(encoding="utf-8"))
            return data.get("current_version_tag")
        except (json.JSONDecodeError, KeyError):
            return None

    def _set_current(self, version_tag: str) -> None:
        pointer_path = self._dir / _CURRENT_POINTER
        pointer_path.write_text(
            json.dumps({
                "current_version_tag": version_tag,
                "updated_utc": datetime.now(timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )

#!/usr/bin/env python3
"""Rollback telemetry helpers for canary readiness reviews.

Provides read-only visibility into encoder-version rollback artifacts and
learning-state rollback envelopes so readiness reviews can assess whether an
immediate rollback path is actually present and evidenced.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.research.rollback_encoder_version import EncoderVersionManager


@dataclass
class RollbackTelemetry:
    """Structured rollback telemetry summary."""

    encoder_version_count: int = 0
    current_encoder_version: str = ""
    encoder_versions_available: List[str] = field(default_factory=list)
    learning_state_version_count: int = 0
    latest_learning_state_version: str = ""
    learning_state_versions_available: List[str] = field(default_factory=list)
    recent_encoder_rollbacks: List[Dict[str, Any]] = field(default_factory=list)
    recent_learning_state_rollbacks: List[Dict[str, Any]] = field(default_factory=list)
    rollback_path_present: bool = False
    rollback_path_proven: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RollbackTelemetryCollector:
    """Collect rollback evidence from local artifact stores."""

    def __init__(
        self,
        repo_root: Path,
        *,
        encoder_dir: Optional[Path] = None,
        learning_state_dir: Optional[Path] = None,
    ):
        self.repo_root = repo_root
        self.encoder_dir = encoder_dir or repo_root / "artifacts" / "encoder_versions"
        self.learning_state_dir = learning_state_dir or repo_root / "reports" / "research" / "state" / "versions"

    def collect(self, limit: int = 20) -> RollbackTelemetry:
        """Collect rollback evidence and availability from artifact stores."""
        mgr = EncoderVersionManager(storage_dir=self.encoder_dir)
        encoder_versions = mgr.list_versions()
        encoder_rollbacks = self._encoder_rollback_events(encoder_versions)[:limit]

        learning_state_versions = self._list_learning_state_versions(limit=limit)
        learning_state_rollbacks = [
            item
            for item in learning_state_versions
            if item.get("metadata", {}).get("rollback_from")
        ][:limit]

        rollback_path_present = bool(encoder_versions or learning_state_versions)
        rollback_path_proven = bool(encoder_rollbacks or learning_state_rollbacks)

        return RollbackTelemetry(
            encoder_version_count=len(encoder_versions),
            current_encoder_version=mgr.current_version() or "",
            encoder_versions_available=[str(item.get("version_tag", "")) for item in encoder_versions[:limit]],
            learning_state_version_count=len(learning_state_versions),
            latest_learning_state_version=(
                str(learning_state_versions[0].get("version_id", "")) if learning_state_versions else ""
            ),
            learning_state_versions_available=[
                str(item.get("version_id", "")) for item in learning_state_versions[:limit]
            ],
            recent_encoder_rollbacks=encoder_rollbacks,
            recent_learning_state_rollbacks=learning_state_rollbacks,
            rollback_path_present=rollback_path_present,
            rollback_path_proven=rollback_path_proven,
        )

    def _encoder_rollback_events(self, versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for item in versions:
            metadata = item.get("metadata", {}) or {}
            if metadata.get("rollback_from"):
                events.append({
                    "version_tag": item.get("version_tag", ""),
                    "timestamp_utc": item.get("timestamp_utc", ""),
                    "rollback_from": metadata.get("rollback_from", ""),
                    "metadata": metadata,
                })
        return events

    def _list_learning_state_versions(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.learning_state_dir.exists():
            return []

        versions: List[Dict[str, Any]] = []
        for path in sorted(self.learning_state_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            versions.append({
                "version_id": path.stem,
                "timestamp": envelope.get("timestamp", ""),
                "content_hash": envelope.get("content_hash", ""),
                "metadata": envelope.get("metadata", {}) or {},
            })
        return versions

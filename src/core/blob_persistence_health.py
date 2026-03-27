#!/usr/bin/env python3
"""Health checks for Blob-backed learning state persistence.

Provides a read-only health surface around ``LearningStatePersistence`` so
operators can see whether Blob is active, whether local fallback is in use,
and what the most recent available versions are.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.structured_logger import get_logger
from src.research.learning_state_persistence import LearningStatePersistence


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BlobPersistenceHealth:
    """Structured health result for learning-state persistence."""

    status: str
    persistence_mode: str
    container_name: str
    blob_available: bool
    local_fallback_available: bool
    fallback_reason: str = ""
    local_version_count: int = 0
    blob_version_count: int = 0
    latest_local_version: str = ""
    latest_blob_version: str = ""
    checked_at: str = field(default_factory=_utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BlobPersistenceHealthChecker:
    """Read-only health probe for Blob-backed learning state persistence."""

    CONNECTION_STRING_ENV_KEYS = (
        "LEARNING_STATE_AZURE_CONNECTION_STRING",
        "AZURE_STORAGE_CONNECTION_STRING",
        "GS_AZURE_STORAGE_CONNECTION_STRING",
        "BLOB_CONNECTION_STRING",
    )
    CONTAINER_ENV_KEYS = (
        "LEARNING_STATE_CONTAINER",
        "LEARNING_STATE_CONTAINER_NAME",
        "AZURE_LEARNING_STATE_CONTAINER",
    )

    def __init__(self, repo_root: Path, local_dir: Optional[Path] = None):
        self.repo_root = repo_root
        self.local_dir = local_dir or repo_root / "reports" / "research" / "state" / "versions"
        self._logger = get_logger("blob_persistence_health")

    def _load_env_file(self) -> Dict[str, str]:
        env_path = self.repo_root / ".env"
        loaded: Dict[str, str] = {}
        if not env_path.exists():
            return loaded

        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            loaded[key.strip()] = value.strip()
        return loaded

    def _resolve_env(self, *keys: str, default: str = "") -> str:
        env_file = self._load_env_file()
        for key in keys:
            value = os.getenv(key) or env_file.get(key)
            if value:
                return str(value)
        return default

    def _connection_string(self) -> str:
        return self._resolve_env(*self.CONNECTION_STRING_ENV_KEYS)

    def _container_name(self) -> str:
        return self._resolve_env(*self.CONTAINER_ENV_KEYS, default="research-state")

    def _latest_local_version(self) -> str:
        if not self.local_dir.exists():
            return ""
        files = sorted(self.local_dir.glob("*.json"), reverse=True)
        return files[0].stem if files else ""

    def _local_version_count(self) -> int:
        if not self.local_dir.exists():
            return 0
        return len(list(self.local_dir.glob("*.json")))

    def check(self) -> BlobPersistenceHealth:
        """Return current persistence health without mutating state."""
        connection_string = self._connection_string()
        container_name = self._container_name()

        persistence = LearningStatePersistence(
            container_name=container_name,
            connection_string=connection_string or None,
            local_dir=self.local_dir,
        )

        blob_versions: List[str] = []
        blob_error = ""
        if persistence.available:
            try:
                blob_versions = persistence.list_versions(limit=5)
            except Exception as exc:  # pragma: no cover - defensive
                blob_error = str(exc)
        else:
            if connection_string:
                blob_error = "blob_client_unavailable_after_init"
            else:
                blob_error = "missing_connection_string"

        local_version_count = self._local_version_count()
        latest_local_version = self._latest_local_version()
        latest_blob_version = blob_versions[0] if blob_versions else ""
        local_fallback_available = local_version_count > 0

        if persistence.available and not blob_error:
            status = "healthy"
            persistence_mode = "blob_primary"
            fallback_reason = ""
        else:
            status = "degraded" if local_fallback_available else "critical"
            persistence_mode = "local_fallback"
            fallback_reason = blob_error or "blob_unavailable"

        health = BlobPersistenceHealth(
            status=status,
            persistence_mode=persistence_mode,
            container_name=container_name,
            blob_available=persistence.available,
            local_fallback_available=local_fallback_available,
            fallback_reason=fallback_reason,
            local_version_count=local_version_count,
            blob_version_count=len(blob_versions),
            latest_local_version=latest_local_version,
            latest_blob_version=latest_blob_version,
            metadata={
                "local_dir": str(self.local_dir),
                "connection_string_present": bool(connection_string),
                "connection_string_source": next(
                    (
                        key
                        for key in self.CONNECTION_STRING_ENV_KEYS
                        if self._resolve_env(key)
                    ),
                    "",
                ),
                "container_env_key": next(
                    (
                        key
                        for key in self.CONTAINER_ENV_KEYS
                        if self._resolve_env(key)
                    ),
                    "",
                ),
            },
        )

        self._logger.info(
            "blob_persistence_health_checked",
            status=health.status,
            persistence_mode=health.persistence_mode,
            blob_available=health.blob_available,
            fallback_reason=health.fallback_reason,
            blob_version_count=health.blob_version_count,
            local_version_count=health.local_version_count,
        )
        return health

    def persist_report(self, output_path: Optional[Path] = None) -> Path:
        """Write a JSON health report and return its path."""
        report_path = output_path or (
            self.repo_root / "reports" / "operational" / "blob_persistence_health.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(self.check().to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        return report_path

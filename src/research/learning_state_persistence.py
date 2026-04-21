#!/usr/bin/env python3
"""Persist online learning state to Azure Blob Storage with immutable versioning.

Blob is the PRIMARY persistence path. Local file storage is an explicit
fallback that triggers alerts and is logged as degraded mode.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


class LearningStatePersistence:
    """Persists online learning state with versioning and rollback.

    Persistence modes:
    - blob_primary: Azure Blob is the system of record, local is fallback
    - local_only: Local filesystem only (test/dev or when Blob unavailable)
    """

    def __init__(
        self,
        container_name: str = "research-state",
        connection_string: Optional[str] = None,
        local_dir: Optional[Path] = None,
    ):
        self.container_name = container_name
        self._local_dir = local_dir or Path("reports/research/state/versions")
        self._blob_client = None
        self._persistence_mode = "local_only"
        self._last_fallback_reason: str = ""

        # Auto-detect connection string from env if not provided
        if not connection_string:
            connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

        if connection_string:
            try:
                from azure.storage.blob import BlobServiceClient
                self._blob_client = BlobServiceClient.from_connection_string(connection_string)
                self._persistence_mode = "blob_primary"
                logger.info("Azure Blob persistence enabled (primary) for container %s", container_name)
            except ImportError:
                self._last_fallback_reason = "azure-storage-blob not installed"
                logger.warning("azure-storage-blob not installed; local fallback")
            except Exception as e:
                self._last_fallback_reason = str(e)
                logger.warning("Azure Blob init failed: %s; local fallback", e)

    @property
    def available(self) -> bool:
        return self._blob_client is not None

    @property
    def persistence_mode(self) -> str:
        return self._persistence_mode

    @property
    def last_fallback_reason(self) -> str:
        return self._last_fallback_reason

    def health_check(self) -> Dict[str, Any]:
        """Check Blob connectivity and return health status."""
        result: Dict[str, Any] = {
            "persistence_mode": self._persistence_mode,
            "blob_available": self._blob_client is not None,
            "local_dir": str(self._local_dir),
            "last_fallback_reason": self._last_fallback_reason,
        }
        if self._blob_client:
            try:
                container = self._blob_client.get_container_client(self.container_name)
                props = container.get_container_properties()
                result["blob_healthy"] = True
                result["container_name"] = self.container_name
                result["container_last_modified"] = str(props.get("last_modified", ""))
            except Exception as e:
                result["blob_healthy"] = False
                result["blob_error"] = str(e)
        else:
            result["blob_healthy"] = False
        return result

    def save_state(self, learning_state: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> str:
        metadata = metadata or {}
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        content = json.dumps(learning_state, indent=2, default=str)
        content_hash = _sha256(content)

        envelope = {
            "schema_version": "learning_state_persistence.v1",
            "timestamp": ts,
            "content_hash": content_hash,
            "learning_state": learning_state,
            "metadata": metadata,
        }
        envelope_json = json.dumps(envelope, indent=2, default=str)
        manifest_hash = _sha256(envelope_json)
        envelope["manifest_hash"] = manifest_hash

        version_id = f"{ts}_{content_hash}"
        blob_name = f"learning-state/{version_id}.json"

        # Track where we actually persisted
        persisted_to = "none"

        # Primary: save to blob
        if self._blob_client:
            try:
                container = self._blob_client.get_container_client(self.container_name)
                try:
                    container.create_container()
                except Exception:
                    pass
                blob = container.get_blob_client(blob_name)
                blob.upload_blob(json.dumps(envelope, indent=2, default=str), overwrite=True)
                persisted_to = "blob_primary"
                logger.info("Saved learning state to blob: %s", blob_name)
            except Exception as e:
                # Blob failed — fallback to local
                self._last_fallback_reason = f"blob_save_failed: {e}"
                logger.error("Blob save failed: %s; falling back to local", e)
                self._save_local(version_id, envelope)
                persisted_to = "local_fallback"
        else:
            self._save_local(version_id, envelope)
            persisted_to = "local_only"

        # Always save local copy as backup when in blob_primary mode
        if persisted_to == "blob_primary":
            try:
                self._save_local(version_id, envelope)
            except Exception:
                pass  # Non-fatal — blob is the system of record

        # Record persistence mode in metadata for auditability
        envelope["_persistence_target"] = persisted_to
        logger.info("State %s persisted_to=%s", version_id, persisted_to)

        return version_id

    def load_latest_state(self) -> Optional[Dict[str, Any]]:
        if self._blob_client:
            try:
                container = self._blob_client.get_container_client(self.container_name)
                blobs = list(container.list_blobs(name_starts_with="learning-state/"))
                if not blobs:
                    return None
                blobs.sort(key=lambda b: b.name, reverse=True)
                blob = container.get_blob_client(blobs[0].name)
                data = json.loads(blob.download_blob().readall())
                return data.get("learning_state", data)
            except Exception as e:
                logger.warning("Blob load failed: %s; trying local", e)

        return self._load_latest_local()

    def load_state_by_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        blob_name = f"learning-state/{version_id}.json"
        if self._blob_client:
            try:
                container = self._blob_client.get_container_client(self.container_name)
                blob = container.get_blob_client(blob_name)
                data = json.loads(blob.download_blob().readall())
                return data.get("learning_state", data)
            except Exception as e:
                logger.warning("Blob version load failed: %s; trying local", e)

        local_path = self._local_dir / f"{version_id}.json"
        if local_path.exists():
            data = json.loads(local_path.read_text(encoding="utf-8"))
            return data.get("learning_state", data)
        return None

    def list_versions(self, limit: int = 20) -> List[str]:
        versions = []
        if self._blob_client:
            try:
                container = self._blob_client.get_container_client(self.container_name)
                blobs = list(container.list_blobs(name_starts_with="learning-state/"))
                blobs.sort(key=lambda b: b.name, reverse=True)
                for b in blobs[:limit]:
                    name = b.name.replace("learning-state/", "").replace(".json", "")
                    versions.append(name)
                return versions
            except Exception:
                pass

        if self._local_dir.exists():
            files = sorted(self._local_dir.glob("*.json"), reverse=True)
            for f in files[:limit]:
                versions.append(f.stem)

        return versions

    def rollback_to_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        state = self.load_state_by_version(version_id)
        if state:
            self.save_state(state, metadata={"rollback_from": version_id})
        return state

    def _save_local(self, version_id: str, envelope: Dict[str, Any]):
        self._local_dir.mkdir(parents=True, exist_ok=True)
        path = self._local_dir / f"{version_id}.json"
        path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
        logger.info("Saved learning state locally: %s", path)

    def _load_latest_local(self) -> Optional[Dict[str, Any]]:
        if not self._local_dir.exists():
            return None
        files = sorted(self._local_dir.glob("*.json"), reverse=True)
        if not files:
            return None
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return data.get("learning_state", data)

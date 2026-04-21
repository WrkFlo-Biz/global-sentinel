#!/usr/bin/env python3
"""Immutable manifest for training/evaluation datasets."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class DatasetManifest:
    """Creates immutable manifests for datasets."""

    def create(
        self,
        rows: List[Dict[str, Any]],
        feature_group_versions: Dict[str, str],
        time_window: Optional[Dict[str, str]] = None,
        code_commit_sha: str = "unknown",
        environment: str = "dev",
        incident_mode: str = "NORMAL",
        regime_state: str = "unknown",
    ) -> Dict[str, Any]:
        # Compute content hash
        content = json.dumps(rows, sort_keys=True, default=str)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

        # Label distribution
        labels = {}
        for row in rows:
            label = row.get("alpha_label", "unlabeled")
            labels[label] = labels.get(label, 0) + 1

        manifest = {
            "schema_version": "dataset_manifest.v1",
            "manifest_id": content_hash,
            "row_count": len(rows),
            "feature_group_versions": feature_group_versions,
            "time_window": time_window or {},
            "label_distribution": labels,
            "creation_timestamp": datetime.now(timezone.utc).isoformat(),
            "code_commit_sha": code_commit_sha,
            "environment": environment,
            "incident_mode": incident_mode,
            "regime_state": regime_state,
        }

        # Manifest hash (hash of manifest itself)
        manifest_json = json.dumps(manifest, sort_keys=True, default=str)
        manifest["manifest_hash"] = hashlib.sha256(manifest_json.encode()).hexdigest()[:32]

        return manifest

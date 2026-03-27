#!/usr/bin/env python3
"""Global Sentinel V4 — Artifact Manifest Builder.

Builds immutable, typed manifests for any research artifact in the system.
Every major artifact (feature set, snapshot, research score, learning state,
encoder version, evaluation result) carries a manifest for lineage tracking.

Manifests enable:
- Full parent-child lineage resolution
- Reproducibility across Azure runs
- Immutable audit trail
- Incident-mode tracking
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ArtifactManifest:
    """Immutable manifest attached to any research artifact."""

    schema_version: str = "artifact_manifest.v1"
    artifact_id: str = ""
    artifact_type: str = ""
    parent_artifact_ids: List[str] = field(default_factory=list)
    source_packet_hashes: List[str] = field(default_factory=list)
    feature_version: str = ""
    model_or_weight_version: str = ""
    code_version: str = ""
    environment: str = ""
    runtime_flags: Dict[str, Any] = field(default_factory=dict)
    incident_mode: str = "NORMAL"
    time_window_state: str = ""
    created_at: str = ""
    content_hash: str = ""
    not_for_direct_execution: bool = True

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.artifact_id:
            self.artifact_id = self._generate_id()
        if not self.environment:
            self.environment = os.getenv("GS_ENVIRONMENT", "local")

    def _generate_id(self) -> str:
        raw = json.dumps({
            "type": self.artifact_type,
            "created": self.created_at,
            "parents": self.parent_artifact_ids,
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class ArtifactManifestBuilder:
    """Builder for creating artifact manifests with lineage tracking.

    Usage:
        builder = ArtifactManifestBuilder()
        manifest = (builder
            .set_type("research_score")
            .set_parents(["parent_abc123"])
            .set_source_packets(["pkt_hash_1", "pkt_hash_2"])
            .set_feature_version("encoder_v3")
            .set_code_version_from_git()
            .set_incident_mode("NORMAL")
            .build())
    """

    def __init__(self):
        self._manifest = ArtifactManifest()

    def set_type(self, artifact_type: str) -> ArtifactManifestBuilder:
        self._manifest.artifact_type = artifact_type
        return self

    def set_id(self, artifact_id: str) -> ArtifactManifestBuilder:
        self._manifest.artifact_id = artifact_id
        return self

    def set_parents(self, parent_ids: List[str]) -> ArtifactManifestBuilder:
        self._manifest.parent_artifact_ids = list(parent_ids)
        return self

    def add_parent(self, parent_id: str) -> ArtifactManifestBuilder:
        self._manifest.parent_artifact_ids.append(parent_id)
        return self

    def set_source_packets(self, hashes: List[str]) -> ArtifactManifestBuilder:
        self._manifest.source_packet_hashes = list(hashes)
        return self

    def set_feature_version(self, version: str) -> ArtifactManifestBuilder:
        self._manifest.feature_version = version
        return self

    def set_model_version(self, version: str) -> ArtifactManifestBuilder:
        self._manifest.model_or_weight_version = version
        return self

    def set_code_version(self, version: str) -> ArtifactManifestBuilder:
        self._manifest.code_version = version
        return self

    def set_code_version_from_git(self) -> ArtifactManifestBuilder:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self._manifest.code_version = result.stdout.strip()
        except Exception:
            self._manifest.code_version = "unknown"
        return self

    def set_environment(self, env: str) -> ArtifactManifestBuilder:
        self._manifest.environment = env
        return self

    def set_runtime_flags(self, flags: Dict[str, Any]) -> ArtifactManifestBuilder:
        self._manifest.runtime_flags = dict(flags)
        return self

    def set_incident_mode(self, mode: str) -> ArtifactManifestBuilder:
        self._manifest.incident_mode = mode
        return self

    def set_time_window(self, window: str) -> ArtifactManifestBuilder:
        self._manifest.time_window_state = window
        return self

    def set_content_hash(self, content: Any) -> ArtifactManifestBuilder:
        if isinstance(content, (dict, list)):
            raw = json.dumps(content, sort_keys=True, default=str)
        else:
            raw = str(content)
        self._manifest.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:32]
        return self

    def build(self) -> ArtifactManifest:
        """Build and return the manifest. Resets the builder."""
        manifest = self._manifest
        if not manifest.artifact_id:
            manifest.artifact_id = manifest._generate_id()
        self._manifest = ArtifactManifest()
        return manifest


class LineageResolver:
    """Resolve parent-child relationships across artifact manifests."""

    def __init__(self):
        self._manifests: Dict[str, ArtifactManifest] = {}

    def register(self, manifest: ArtifactManifest) -> None:
        self._manifests[manifest.artifact_id] = manifest

    def register_dict(self, manifest_dict: Dict[str, Any]) -> None:
        m = ArtifactManifest(**{k: v for k, v in manifest_dict.items()
                                if k in ArtifactManifest.__dataclass_fields__})
        self.register(m)

    def get(self, artifact_id: str) -> Optional[ArtifactManifest]:
        return self._manifests.get(artifact_id)

    def get_parents(self, artifact_id: str) -> List[ArtifactManifest]:
        manifest = self._manifests.get(artifact_id)
        if not manifest:
            return []
        return [self._manifests[pid] for pid in manifest.parent_artifact_ids
                if pid in self._manifests]

    def get_children(self, artifact_id: str) -> List[ArtifactManifest]:
        return [m for m in self._manifests.values()
                if artifact_id in m.parent_artifact_ids]

    def get_ancestry(self, artifact_id: str, max_depth: int = 10) -> List[ArtifactManifest]:
        """Get full ancestry chain up to max_depth."""
        result: List[ArtifactManifest] = []
        visited = set()
        queue = [artifact_id]
        depth = 0

        while queue and depth < max_depth:
            next_queue = []
            for aid in queue:
                if aid in visited:
                    continue
                visited.add(aid)
                manifest = self._manifests.get(aid)
                if manifest:
                    result.append(manifest)
                    next_queue.extend(manifest.parent_artifact_ids)
            queue = next_queue
            depth += 1

        return result

    def validate_lineage(self, artifact_id: str) -> Dict[str, Any]:
        """Validate that lineage is complete (no broken parent references)."""
        manifest = self._manifests.get(artifact_id)
        if not manifest:
            return {"valid": False, "reason": "artifact_not_found", "artifact_id": artifact_id}

        missing_parents = [pid for pid in manifest.parent_artifact_ids
                          if pid not in self._manifests]

        return {
            "valid": len(missing_parents) == 0,
            "artifact_id": artifact_id,
            "artifact_type": manifest.artifact_type,
            "parent_count": len(manifest.parent_artifact_ids),
            "missing_parents": missing_parents,
            "source_packet_count": len(manifest.source_packet_hashes),
        }

    @property
    def manifest_count(self) -> int:
        return len(self._manifests)

    def summary(self) -> Dict[str, Any]:
        types = {}
        for m in self._manifests.values():
            types[m.artifact_type] = types.get(m.artifact_type, 0) + 1
        return {
            "total_manifests": len(self._manifests),
            "by_type": types,
            "schema_version": "lineage_summary.v1",
        }

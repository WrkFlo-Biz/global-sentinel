#!/usr/bin/env python3
"""Track full lineage from raw packet to feature to recommendation to outcome."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _compute_hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


class FeatureLineageTracker:
    """Tracks full lineage of feature computations."""

    def __init__(self):
        self._records: List[Dict[str, Any]] = []

    def record_computation(
        self,
        input_packet_ids: List[str],
        feature_group_name: str,
        feature_group_version: str,
        output_features: Dict[str, Any],
        code_version: str = "unknown",
    ) -> str:
        output_hash = _compute_hash(output_features)
        record = {
            "input_packet_ids": input_packet_ids,
            "feature_group_name": feature_group_name,
            "feature_group_version": feature_group_version,
            "output_feature_hash": output_hash,
            "computation_timestamp": datetime.now(timezone.utc).isoformat(),
            "code_version": code_version,
        }
        self._records.append(record)
        return output_hash

    def get_lineage(self, feature_hash: str) -> Optional[Dict[str, Any]]:
        for r in self._records:
            if r["output_feature_hash"] == feature_hash:
                return r
        return None

    def get_lineage_chain(self, feature_hash: str) -> List[Dict[str, Any]]:
        chain = []
        visited = set()
        current = feature_hash
        while current and current not in visited:
            visited.add(current)
            record = self.get_lineage(current)
            if record:
                chain.append(record)
                # Could follow upstream if packet_ids are themselves feature hashes
                break  # For now, single-hop
            else:
                break
        return chain

    @property
    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)

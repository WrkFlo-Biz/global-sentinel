#!/usr/bin/env python3
"""Deterministic config fingerprint for governance YAML files.

Computes SHA-256 hashes across all governance configs so that
ArtifactManifests can embed a config fingerprint for replayability.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GOVERNANCE_CONFIGS = [
    "promotion_policy.yaml",
    "feature_registry.yaml",
    "feature_group_registry.yaml",
    "freshness_policy.yaml",
    "policy_engine_config.yaml",
    "data_trust_hierarchy.yaml",
    "pre_trade_controls.yaml",
]


def _hash_file(path: Path) -> str:
    """Compute SHA-256 of file contents. Returns empty string if file missing."""
    if not path.exists():
        return ""
    try:
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except Exception as e:
        logger.warning("Failed to hash %s: %s", path, e)
        return ""


def compute_config_fingerprint(
    config_dir: Optional[Path] = None,
    config_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compute deterministic fingerprint across governance configs.

    Returns:
        {
            "configs": {config_name: sha256_hash, ...},
            "combined_fingerprint": sha256_of_all_hashes,
            "config_count": int,
            "missing_configs": [names...],
        }
    """
    if config_dir is None:
        config_dir = Path("config")
    if config_names is None:
        config_names = GOVERNANCE_CONFIGS

    configs: Dict[str, str] = {}
    missing: List[str] = []

    for name in sorted(config_names):
        h = _hash_file(config_dir / name)
        if h:
            configs[name] = h
        else:
            missing.append(name)

    # Combined fingerprint: hash of sorted (name, hash) pairs
    combined_input = "|".join(f"{k}:{v}" for k, v in sorted(configs.items()))
    combined = hashlib.sha256(combined_input.encode()).hexdigest() if combined_input else ""

    return {
        "configs": configs,
        "combined_fingerprint": combined,
        "config_count": len(configs),
        "missing_configs": missing,
    }

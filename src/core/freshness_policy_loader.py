#!/usr/bin/env python3
"""Typed loader for config/freshness_policy.yaml.

Validates structure and provides typed access to per-source freshness
policies and quorum requirements.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SourceFreshnessPolicy:
    """Freshness policy for a single data source."""
    source_name: str
    ttl_minutes: int = 60
    backoff_strategy: str = "exponential"
    max_backoff_minutes: int = 480
    stale_action: str = "degrade_trust_weight"
    stale_trust_weight_override: float = 0.5


@dataclass(frozen=True)
class QuorumRequirement:
    """Quorum requirement for a specific decision type."""
    decision_type: str
    required_tier1_sources: int = 2
    required_tier2_sources: int = 1
    min_freshness_sources: int = 3
    required_confirming_sources: int = 2
    max_stale_sources: int = 1


@dataclass
class FreshnessConfig:
    """Fully typed freshness config loaded from YAML."""
    schema_version: str = "freshness_policy.v1"
    sources: Dict[str, SourceFreshnessPolicy] = field(default_factory=dict)
    quorum: Dict[str, QuorumRequirement] = field(default_factory=dict)

    def get_source_policy(self, source_name: str) -> Optional[SourceFreshnessPolicy]:
        return self.sources.get(source_name)

    def list_sources(self) -> List[str]:
        return list(self.sources.keys())

    def get_quorum_requirements(self, decision_type: str) -> Optional[QuorumRequirement]:
        return self.quorum.get(decision_type)


def load_freshness_policy(config_path: Optional[Path] = None) -> FreshnessConfig:
    """Load and validate freshness policy from YAML."""
    if config_path is None:
        config_path = Path("config/freshness_policy.yaml")

    if yaml is None:
        logger.warning("pyyaml not available, using default freshness policy")
        return FreshnessConfig()

    if not config_path.exists():
        logger.warning("Freshness policy not found at %s, using defaults", config_path)
        return FreshnessConfig()

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error("Failed to parse freshness policy: %s", e)
        return FreshnessConfig()

    # Parse sources
    sources: Dict[str, SourceFreshnessPolicy] = {}
    for name, src_raw in raw.get("sources", {}).items():
        if isinstance(src_raw, dict):
            sources[name] = SourceFreshnessPolicy(
                source_name=name,
                ttl_minutes=int(src_raw.get("freshness_ttl_minutes", 60)),
                backoff_strategy=str(src_raw.get("backoff_on_failure", "exponential")),
                max_backoff_minutes=int(src_raw.get("max_backoff_minutes", 480)),
                stale_action=str(src_raw.get("stale_action", "degrade_trust_weight")),
                stale_trust_weight_override=float(src_raw.get("stale_trust_weight_override", 0.5)),
            )

    # Parse quorum
    quorum: Dict[str, QuorumRequirement] = {}
    for name, q_raw in raw.get("quorum", {}).items():
        if isinstance(q_raw, dict):
            quorum[name] = QuorumRequirement(
                decision_type=name,
                required_tier1_sources=int(q_raw.get("required_tier1_sources", 2)),
                required_tier2_sources=int(q_raw.get("required_tier2_sources", 1)),
                min_freshness_sources=int(q_raw.get("min_freshness_sources", 3)),
                required_confirming_sources=int(q_raw.get("required_confirming_sources", 2)),
                max_stale_sources=int(q_raw.get("max_stale_sources", 1)),
            )

    return FreshnessConfig(
        schema_version=raw.get("schema_version", "freshness_policy.v1"),
        sources=sources,
        quorum=quorum,
    )

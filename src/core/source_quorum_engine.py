#!/usr/bin/env python3
"""Verify quorum of fresh sources before execution escalation or regime changes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.structured_logger import get_logger


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


class SourceQuorumEngine:
    """Evaluate per-source freshness against quorum policy."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path("config")
        policy = _load_yaml(self.config_dir / "freshness_policy.yaml")
        self._quorum_config = policy.get("quorum", {})
        self._source_policies = policy.get("sources", {})
        self._logger = get_logger("source_quorum_engine")

    def _policy_for(self, source: str) -> Dict[str, Any]:
        source = str(source or "")
        return self._source_policies.get(source) or self._source_policies.get(source.removesuffix("_bridge")) or {}

    def _is_fresh(self, source: str, ts_str: Any, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        ts = _parse_dt(ts_str)
        if ts is None:
            return False
        ttl = float(self._policy_for(source).get("freshness_ttl_minutes", 60))
        return (now - ts).total_seconds() / 60.0 <= ttl

    def check_execution_quorum(self, source_freshness: Dict[str, str], trust_hierarchy: Dict[str, Any]) -> Dict[str, Any]:
        config = self._quorum_config.get("execution_escalation", {})
        required_t1 = int(config.get("required_tier1_sources", 2))
        required_t2 = int(config.get("required_tier2_sources", 1))
        min_fresh = int(config.get("min_freshness_sources", 3))
        now = datetime.now(timezone.utc)

        tiers = trust_hierarchy.get("tiers", {})
        tier1_sources = {str(x) for x in (tiers.get("tier_1_official", {}) or {}).get("sources", [])}
        tier2_sources = {str(x) for x in (tiers.get("tier_2_operational", {}) or {}).get("sources", [])}
        fresh_t1 = 0
        fresh_t2 = 0
        fresh_total = 0
        stale_sources: List[str] = []

        for source, ts_str in source_freshness.items():
            fresh = self._is_fresh(source, ts_str, now=now)
            if fresh:
                fresh_total += 1
                base = str(source).removesuffix("_bridge")
                if source in tier1_sources or base in tier1_sources:
                    fresh_t1 += 1
                if source in tier2_sources or base in tier2_sources:
                    fresh_t2 += 1
            else:
                stale_sources.append(str(source))

        result = {
            "quorum_met": fresh_t1 >= required_t1 and fresh_t2 >= required_t2 and fresh_total >= min_fresh,
            "fresh_tier1": fresh_t1,
            "required_tier1": required_t1,
            "fresh_tier2": fresh_t2,
            "required_tier2": required_t2,
            "fresh_total": fresh_total,
            "required_total": min_fresh,
            "stale_sources": stale_sources,
        }
        self._logger.info("execution_quorum_evaluated", **result)
        return result

    def check_regime_transition_quorum(self, confirming_sources: List[str], source_freshness: Dict[str, str]) -> Dict[str, Any]:
        config = self._quorum_config.get("regime_transition", {})
        required = int(config.get("required_confirming_sources", 2))
        max_stale = int(config.get("max_stale_sources", 1))
        now = datetime.now(timezone.utc)

        fresh_confirming = 0
        stale_count = 0
        stale_sources: List[str] = []
        for source in confirming_sources:
            fresh = self._is_fresh(source, source_freshness.get(source), now=now)
            if fresh:
                fresh_confirming += 1
            else:
                stale_count += 1
                stale_sources.append(str(source))

        result = {
            "quorum_met": fresh_confirming >= required and stale_count <= max_stale,
            "fresh_confirming": fresh_confirming,
            "required_confirming": required,
            "stale_count": stale_count,
            "max_stale": max_stale,
            "stale_sources": stale_sources,
        }
        self._logger.info("regime_transition_quorum_evaluated", **result)
        return result

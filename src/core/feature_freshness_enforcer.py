#!/usr/bin/env python3
"""Runtime feature freshness enforcement.

Loads feature_registry.yaml and feature_group_registry.yaml, then checks
whether features and feature groups meet their freshness policies.

Produces structured freshness facts (not final decisions) that downstream
policy_engine can use for eligibility decisions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FeatureFreshnessResult:
    """Freshness check result for a single feature."""
    feature_name: str
    status: str  # "fresh", "stale", "expired", "unknown"
    ttl_minutes: int
    age_minutes: float
    confidence_penalty: float
    stale_reason: str


@dataclass(frozen=True)
class GroupFreshnessResult:
    """Freshness check result for a feature group."""
    group_name: str
    policy: str  # "all_fresh", "best_effort", "quorum"
    compliant: bool
    total_features: int
    fresh_count: int
    stale_count: int
    missing_count: int
    confidence_penalty: float
    stale_reasons: List[str]
    feature_results: List[FeatureFreshnessResult]
    degraded: bool


class FeatureFreshnessEnforcer:
    """Enforces feature freshness policies at runtime.

    Loads config/feature_registry.yaml and config/feature_group_registry.yaml
    to check individual feature and group-level freshness compliance.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path("config")
        self._features: Dict[str, Dict[str, Any]] = {}
        self._groups: Dict[str, Dict[str, Any]] = {}
        self._freshness_policies: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._load(config_dir)

    def _load(self, config_dir: Path) -> None:
        if yaml is None:
            logger.warning("pyyaml not available, freshness enforcement disabled")
            return

        # Load feature registry
        feat_path = config_dir / "feature_registry.yaml"
        if feat_path.exists():
            try:
                raw = yaml.safe_load(feat_path.read_text(encoding="utf-8")) or {}
                self._features = raw.get("features", {})
            except Exception as e:
                logger.error("Failed to load feature_registry.yaml: %s", e)

        # Load feature group registry
        group_path = config_dir / "feature_group_registry.yaml"
        if group_path.exists():
            try:
                raw = yaml.safe_load(group_path.read_text(encoding="utf-8")) or {}
                self._groups = raw.get("groups", {})
                self._freshness_policies = raw.get("freshness_policies", {})
            except Exception as e:
                logger.error("Failed to load feature_group_registry.yaml: %s", e)

        self._loaded = bool(self._features or self._groups)
        if self._loaded:
            logger.info(
                "Freshness enforcer loaded: %d features, %d groups",
                len(self._features), len(self._groups),
            )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def check_feature(
        self,
        feature_name: str,
        last_updated: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> FeatureFreshnessResult:
        """Check freshness of a single feature.

        Returns structured facts — does NOT make final policy decisions.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        feat_config = self._features.get(feature_name)
        if feat_config is None:
            return FeatureFreshnessResult(
                feature_name=feature_name,
                status="unknown",
                ttl_minutes=0,
                age_minutes=0.0,
                confidence_penalty=0.5,
                stale_reason=f"feature '{feature_name}' not in registry",
            )

        ttl = int(feat_config.get("freshness_ttl_minutes", 60))

        if last_updated is None:
            return FeatureFreshnessResult(
                feature_name=feature_name,
                status="expired",
                ttl_minutes=ttl,
                age_minutes=float("inf"),
                confidence_penalty=1.0,
                stale_reason="no_timestamp_provided",
            )

        # Ensure timezone-aware comparison
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)

        age_minutes = (now - last_updated).total_seconds() / 60.0

        if age_minutes <= ttl:
            return FeatureFreshnessResult(
                feature_name=feature_name,
                status="fresh",
                ttl_minutes=ttl,
                age_minutes=round(age_minutes, 2),
                confidence_penalty=0.0,
                stale_reason="",
            )
        elif age_minutes <= ttl * 2:
            # Stale but within 2x TTL — degraded with proportional penalty
            penalty = min(0.5, (age_minutes - ttl) / ttl * 0.5)
            return FeatureFreshnessResult(
                feature_name=feature_name,
                status="stale",
                ttl_minutes=ttl,
                age_minutes=round(age_minutes, 2),
                confidence_penalty=round(penalty, 3),
                stale_reason=f"age {age_minutes:.0f}min > ttl {ttl}min",
            )
        else:
            # Expired — beyond 2x TTL
            return FeatureFreshnessResult(
                feature_name=feature_name,
                status="expired",
                ttl_minutes=ttl,
                age_minutes=round(age_minutes, 2),
                confidence_penalty=1.0,
                stale_reason=f"expired: age {age_minutes:.0f}min >> ttl {ttl}min",
            )

    def check_group(
        self,
        group_name: str,
        feature_timestamps: Dict[str, Optional[datetime]],
        now: Optional[datetime] = None,
    ) -> GroupFreshnessResult:
        """Check freshness compliance for a feature group.

        Applies the group's freshness policy (all_fresh, best_effort, quorum).
        Returns structured facts for policy_engine to act on.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        group_config = self._groups.get(group_name)
        if group_config is None:
            return GroupFreshnessResult(
                group_name=group_name,
                policy="unknown",
                compliant=False,
                total_features=0,
                fresh_count=0,
                stale_count=0,
                missing_count=0,
                confidence_penalty=1.0,
                stale_reasons=[f"group '{group_name}' not in registry"],
                feature_results=[],
                degraded=True,
            )

        policy = group_config.get("freshness_policy", "all_fresh")
        group_features = group_config.get("features", [])
        min_required = group_config.get("min_features_required", len(group_features))

        # Check each feature in the group
        results: List[FeatureFreshnessResult] = []
        for feat_name in group_features:
            ts = feature_timestamps.get(feat_name)
            results.append(self.check_feature(feat_name, ts, now))

        fresh_count = sum(1 for r in results if r.status == "fresh")
        stale_count = sum(1 for r in results if r.status == "stale")
        missing_count = sum(1 for r in results if r.status in ("expired", "unknown"))
        stale_reasons = [r.stale_reason for r in results if r.stale_reason]

        # Apply freshness policy
        if policy == "all_fresh":
            compliant = fresh_count == len(group_features)
            degraded = not compliant
            penalty = max(r.confidence_penalty for r in results) if results else 0.0

        elif policy == "best_effort":
            # Always compliant, but with confidence penalty per stale feature
            policy_config = self._freshness_policies.get("best_effort", {})
            per_stale_penalty = float(policy_config.get("confidence_penalty_per_stale", 0.1))
            compliant = True
            degraded = stale_count + missing_count > 0
            penalty = min(1.0, (stale_count + missing_count) * per_stale_penalty)

        elif policy == "quorum":
            # Require minimum ratio of fresh features
            policy_config = self._freshness_policies.get("quorum", {})
            min_fresh_ratio = float(policy_config.get("min_fresh_ratio", 0.5))
            actual_ratio = fresh_count / len(group_features) if group_features else 0
            compliant = actual_ratio >= min_fresh_ratio and fresh_count >= min_required
            degraded = not compliant or stale_count > 0
            penalty = max(r.confidence_penalty for r in results) if results else 0.0

        else:
            compliant = False
            degraded = True
            penalty = 1.0
            stale_reasons.append(f"unknown_policy:{policy}")

        return GroupFreshnessResult(
            group_name=group_name,
            policy=policy,
            compliant=compliant,
            total_features=len(group_features),
            fresh_count=fresh_count,
            stale_count=stale_count,
            missing_count=missing_count,
            confidence_penalty=round(penalty, 3),
            stale_reasons=stale_reasons,
            feature_results=results,
            degraded=degraded,
        )

    def check_all_groups(
        self,
        feature_timestamps: Dict[str, Optional[datetime]],
        now: Optional[datetime] = None,
    ) -> Dict[str, GroupFreshnessResult]:
        """Check all registered groups. Returns dict of group_name → result."""
        return {
            name: self.check_group(name, feature_timestamps, now)
            for name in self._groups
        }

    def summary(
        self,
        feature_timestamps: Dict[str, Optional[datetime]],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Produce a summary suitable for logging or dashboard consumption."""
        group_results = self.check_all_groups(feature_timestamps, now)
        return {
            "total_groups": len(group_results),
            "compliant_groups": sum(1 for r in group_results.values() if r.compliant),
            "degraded_groups": sum(1 for r in group_results.values() if r.degraded),
            "max_confidence_penalty": max(
                (r.confidence_penalty for r in group_results.values()), default=0.0
            ),
            "groups": {
                name: {
                    "policy": r.policy,
                    "compliant": r.compliant,
                    "degraded": r.degraded,
                    "fresh": r.fresh_count,
                    "stale": r.stale_count,
                    "missing": r.missing_count,
                    "confidence_penalty": r.confidence_penalty,
                }
                for name, r in group_results.items()
            },
        }

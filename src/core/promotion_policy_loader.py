#!/usr/bin/env python3
"""Typed loader for config/promotion_policy.yaml.

Validates structure and provides typed access to promotion thresholds,
frozen modes, canary policy, and rollback policy.
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
class SignalThresholds:
    """Promotion thresholds for a specific signal type."""
    min_eval_days: int = 60
    min_trade_count: int = 100
    max_drawdown_delta_bps: float = 50.0
    min_slippage_adjusted_win_delta_bps: float = 10.0
    max_failure_rate: float = 0.05
    max_cumulative_drift_std: float = 2.0
    min_sharpe_ratio: float = 0.5
    max_drift_score: float = 0.15
    promotion_blocked: bool = False
    blocked_reason: str = ""


@dataclass(frozen=True)
class CanaryPolicy:
    min_improvement_pct: float = 0.0
    max_regression_pct: float = 2.0
    min_sample_size: int = 50
    confidence_level: float = 0.95


@dataclass(frozen=True)
class RollbackPolicy:
    max_versions_retained: int = 10
    auto_rollback_on_drift: bool = True
    drift_threshold_for_rollback: float = 0.20


@dataclass
class PromotionPolicy:
    """Fully typed promotion policy loaded from YAML."""
    schema_version: str = "promotion_policy.v1"
    frozen_modes: List[str] = field(default_factory=lambda: ["CRISIS", "MANUAL_REVIEW"])
    human_approval_required: bool = True
    dual_run_required: bool = True
    rollback_required: bool = True
    lineage_completeness_required: bool = True
    reproducibility_required: bool = True
    no_safety_regressions: bool = True
    signal_thresholds: Dict[str, SignalThresholds] = field(default_factory=dict)
    canary_policy: CanaryPolicy = field(default_factory=CanaryPolicy)
    rollback_policy: RollbackPolicy = field(default_factory=RollbackPolicy)

    def get_thresholds(self, signal_type: str) -> SignalThresholds:
        """Get thresholds for a signal type, falling back to default."""
        if signal_type in self.signal_thresholds:
            return self.signal_thresholds[signal_type]
        return self.signal_thresholds.get("default", SignalThresholds())

    def is_promotion_blocked(self, signal_type: str) -> bool:
        return self.get_thresholds(signal_type).promotion_blocked

    def is_mode_frozen(self, current_mode: str) -> bool:
        return current_mode in self.frozen_modes


def _parse_signal_thresholds(raw: Dict[str, Any]) -> SignalThresholds:
    return SignalThresholds(
        min_eval_days=int(raw.get("min_eval_days", 60)),
        min_trade_count=int(raw.get("min_trade_count", 100)),
        max_drawdown_delta_bps=float(raw.get("max_drawdown_delta_bps", 50.0)),
        min_slippage_adjusted_win_delta_bps=float(raw.get("min_slippage_adjusted_win_delta_bps", 10.0)),
        max_failure_rate=float(raw.get("max_failure_rate", 0.05)),
        max_cumulative_drift_std=float(raw.get("max_cumulative_drift_std", 2.0)),
        min_sharpe_ratio=float(raw.get("min_sharpe_ratio", 0.5)),
        max_drift_score=float(raw.get("max_drift_score", 0.15)),
        promotion_blocked=bool(raw.get("promotion_blocked", False)),
        blocked_reason=str(raw.get("reason", "")),
    )


def load_promotion_policy(config_path: Optional[Path] = None) -> PromotionPolicy:
    """Load and validate promotion policy from YAML.

    Falls back to defaults if file is missing or yaml is unavailable.
    """
    if config_path is None:
        config_path = Path("config/promotion_policy.yaml")

    if yaml is None:
        logger.warning("pyyaml not available, using default promotion policy")
        return PromotionPolicy(signal_thresholds={"default": SignalThresholds()})

    if not config_path.exists():
        logger.warning("Promotion policy not found at %s, using defaults", config_path)
        return PromotionPolicy(signal_thresholds={"default": SignalThresholds()})

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error("Failed to parse promotion policy: %s", e)
        return PromotionPolicy(signal_thresholds={"default": SignalThresholds()})

    # Validate schema version
    sv = raw.get("schema_version", "")
    if sv and not sv.startswith("promotion_policy"):
        logger.warning("Unexpected schema version: %s", sv)

    # Parse global rules
    global_rules = raw.get("global_rules", {})

    # Parse per-signal thresholds
    signal_map: Dict[str, SignalThresholds] = {}
    for name, thresh_raw in raw.get("signal_thresholds", {}).items():
        if isinstance(thresh_raw, dict):
            signal_map[name] = _parse_signal_thresholds(thresh_raw)

    if "default" not in signal_map:
        signal_map["default"] = SignalThresholds()

    # Parse canary policy
    cp_raw = raw.get("canary_policy", {})
    canary = CanaryPolicy(
        min_improvement_pct=float(cp_raw.get("min_improvement_pct", 0.0)),
        max_regression_pct=float(cp_raw.get("max_regression_pct", 2.0)),
        min_sample_size=int(cp_raw.get("min_sample_size", 50)),
        confidence_level=float(cp_raw.get("confidence_level", 0.95)),
    )

    # Parse rollback policy
    rp_raw = raw.get("rollback_policy", {})
    rollback = RollbackPolicy(
        max_versions_retained=int(rp_raw.get("max_versions_retained", 10)),
        auto_rollback_on_drift=bool(rp_raw.get("auto_rollback_on_drift", True)),
        drift_threshold_for_rollback=float(rp_raw.get("drift_threshold_for_rollback", 0.20)),
    )

    return PromotionPolicy(
        schema_version=sv or "promotion_policy.v1",
        frozen_modes=list(global_rules.get("frozen_modes", ["CRISIS", "MANUAL_REVIEW"])),
        human_approval_required=bool(global_rules.get("human_approval_required", True)),
        dual_run_required=bool(global_rules.get("dual_run_required", True)),
        rollback_required=bool(global_rules.get("rollback_required", True)),
        lineage_completeness_required=bool(global_rules.get("lineage_completeness_required", True)),
        reproducibility_required=bool(global_rules.get("reproducibility_required", True)),
        no_safety_regressions=bool(global_rules.get("no_safety_regressions", True)),
        signal_thresholds=signal_map,
        canary_policy=canary,
        rollback_policy=rollback,
    )

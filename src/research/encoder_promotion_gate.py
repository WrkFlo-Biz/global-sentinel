#!/usr/bin/env python3
"""Hard gate before learned weights enter the feature encoder.

Requires multiple independent checks to pass before promotion.
Loads thresholds from config/promotion_policy.yaml via promotion_policy_loader.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PromotionDecision:
    allowed: bool
    reason: str
    signal_type: str = ""
    gate_results: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EncoderPromotionGate:
    """Multi-gate promotion control for learned weights.

    Loads thresholds from promotion_policy.yaml. Falls back to defaults
    if the config is unavailable.
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        # Legacy kwargs for backward compatibility — overridden by YAML if loaded
        min_eval_days: int = 60,
        min_trade_count: int = 100,
        max_drawdown_delta_bps: float = 50.0,
        min_slippage_adjusted_win_delta_bps: float = 10.0,
        max_failure_rate: float = 0.05,
        max_cumulative_drift_std: float = 2.0,
    ):
        self._policy = None
        self._config_path = config_path

        # Try loading from YAML
        try:
            from src.core.promotion_policy_loader import load_promotion_policy
            self._policy = load_promotion_policy(config_path)
            logger.info("Loaded promotion policy from YAML (version=%s)", self._policy.schema_version)
        except Exception as e:
            logger.warning("Could not load promotion policy YAML: %s — using constructor defaults", e)

        # Store legacy defaults as fallback
        self._legacy_defaults = {
            "min_eval_days": min_eval_days,
            "min_trade_count": min_trade_count,
            "max_drawdown_delta_bps": max_drawdown_delta_bps,
            "min_slippage_adjusted_win_delta_bps": min_slippage_adjusted_win_delta_bps,
            "max_failure_rate": max_failure_rate,
            "max_cumulative_drift_std": max_cumulative_drift_std,
        }

    def evaluate(
        self,
        eval_metrics: Dict[str, Any],
        guardrail_result: Optional[Dict[str, Any]] = None,
        policy_decision: Optional[Dict[str, Any]] = None,
        signal_type: str = "default",
        current_mode: str = "NORMAL",
    ) -> PromotionDecision:
        gates: List[Dict[str, Any]] = []

        # Gate 0: Frozen mode check
        if self._policy and self._policy.is_mode_frozen(current_mode):
            gates.append({
                "gate": "frozen_mode",
                "passed": False,
                "value": current_mode,
                "threshold": self._policy.frozen_modes,
            })
            return PromotionDecision(
                allowed=False,
                reason=f"mode_frozen:{current_mode}",
                signal_type=signal_type,
                gate_results=gates,
            )

        # Gate 0b: Promotion blocked for this signal type
        if self._policy and self._policy.is_promotion_blocked(signal_type):
            thresholds = self._policy.get_thresholds(signal_type)
            gates.append({
                "gate": "promotion_blocked",
                "passed": False,
                "value": signal_type,
                "threshold": thresholds.blocked_reason or "blocked_by_policy",
            })
            return PromotionDecision(
                allowed=False,
                reason=f"promotion_blocked:{signal_type}:{thresholds.blocked_reason}",
                signal_type=signal_type,
                gate_results=gates,
            )

        # Resolve thresholds: YAML > legacy defaults
        if self._policy:
            t = self._policy.get_thresholds(signal_type)
            min_eval_days = t.min_eval_days
            min_trade_count = t.min_trade_count
            max_drawdown_delta_bps = t.max_drawdown_delta_bps
            min_slippage_win_delta = t.min_slippage_adjusted_win_delta_bps
            max_failure_rate = t.max_failure_rate
            max_cumulative_drift_std = t.max_cumulative_drift_std
        else:
            min_eval_days = self._legacy_defaults["min_eval_days"]
            min_trade_count = self._legacy_defaults["min_trade_count"]
            max_drawdown_delta_bps = self._legacy_defaults["max_drawdown_delta_bps"]
            min_slippage_win_delta = self._legacy_defaults["min_slippage_adjusted_win_delta_bps"]
            max_failure_rate = self._legacy_defaults["max_failure_rate"]
            max_cumulative_drift_std = self._legacy_defaults["max_cumulative_drift_std"]

        # Gate 1: Minimum eval days
        eval_days = eval_metrics.get("eval_days", 0)
        gates.append({
            "gate": "min_eval_days",
            "passed": eval_days >= min_eval_days,
            "value": eval_days,
            "threshold": min_eval_days,
        })

        # Gate 2: Minimum trade count
        trade_count = eval_metrics.get("trade_count", 0)
        gates.append({
            "gate": "min_trade_count",
            "passed": trade_count >= min_trade_count,
            "value": trade_count,
            "threshold": min_trade_count,
        })

        # Gate 3: Max drawdown delta
        drawdown_delta = eval_metrics.get("drawdown_delta_bps", 0)
        gates.append({
            "gate": "max_drawdown_delta",
            "passed": drawdown_delta <= max_drawdown_delta_bps,
            "value": drawdown_delta,
            "threshold": max_drawdown_delta_bps,
        })

        # Gate 4: Slippage-adjusted win delta
        win_delta = eval_metrics.get("slippage_adjusted_win_delta_bps", 0)
        gates.append({
            "gate": "min_win_delta",
            "passed": win_delta >= min_slippage_win_delta,
            "value": win_delta,
            "threshold": min_slippage_win_delta,
        })

        # Gate 5: Failure rate
        failure_rate = eval_metrics.get("failure_rate", 0)
        gates.append({
            "gate": "max_failure_rate",
            "passed": failure_rate <= max_failure_rate,
            "value": failure_rate,
            "threshold": max_failure_rate,
        })

        # Gate 6: Cumulative drift
        drift_std = eval_metrics.get("cumulative_drift_std", 0)
        gates.append({
            "gate": "max_cumulative_drift",
            "passed": drift_std <= max_cumulative_drift_std,
            "value": drift_std,
            "threshold": max_cumulative_drift_std,
        })

        # Gate 7: Guardrail result (if provided)
        if guardrail_result is not None:
            gr_passed = guardrail_result.get("passed", False)
            gates.append({
                "gate": "guardrail_check",
                "passed": gr_passed,
                "value": gr_passed,
                "threshold": True,
            })

        # Gate 8: Policy decision (if provided)
        if policy_decision is not None:
            pd_allowed = policy_decision.get("allowed", False)
            gates.append({
                "gate": "policy_check",
                "passed": pd_allowed,
                "value": pd_allowed,
                "threshold": True,
            })

        all_passed = all(g["passed"] for g in gates)
        failed = [g["gate"] for g in gates if not g["passed"]]
        reason = "all gates passed" if all_passed else f"failed: {', '.join(failed)}"

        return PromotionDecision(
            allowed=all_passed,
            reason=reason,
            signal_type=signal_type,
            gate_results=gates,
        )

    def evaluate_canary(
        self,
        eval_metrics: Dict[str, Any],
        baseline_metrics: Optional[Dict[str, Any]] = None,
        guardrail_result: Optional[Dict[str, Any]] = None,
        policy_decision: Optional[Dict[str, Any]] = None,
        signal_type: str = "default",
        current_mode: str = "NORMAL",
    ) -> Dict[str, Any]:
        """Run a canary evaluation — evidence generation only, never promotes.

        Evaluates the candidate against the full promotion gate, compares
        to baseline if provided, and produces a structured evidence report.
        The result is always marked canary_evidence_only=True.
        """
        # Run full gate evaluation
        decision = self.evaluate(
            eval_metrics=eval_metrics,
            guardrail_result=guardrail_result,
            policy_decision=policy_decision,
            signal_type=signal_type,
            current_mode=current_mode,
        )

        # Compute canary vs baseline divergence
        divergence: Dict[str, Any] = {}
        if baseline_metrics:
            for key in eval_metrics:
                if key in baseline_metrics:
                    try:
                        canary_val = float(eval_metrics[key])
                        baseline_val = float(baseline_metrics[key])
                        divergence[key] = {
                            "canary": canary_val,
                            "baseline": baseline_val,
                            "delta": canary_val - baseline_val,
                            "delta_pct": ((canary_val - baseline_val) / baseline_val * 100)
                                if baseline_val != 0 else 0,
                        }
                    except (TypeError, ValueError):
                        pass

        return {
            "canary_evidence_only": True,
            "promotion_allowed_if_not_canary": decision.allowed,
            "reason": decision.reason,
            "signal_type": signal_type,
            "current_mode": current_mode,
            "gate_results": decision.gate_results,
            "canary_vs_baseline_divergence": divergence,
            "timestamp": decision.timestamp,
            "rollback_recommended": not decision.allowed,
        }

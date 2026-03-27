#!/usr/bin/env python3
"""Centralized policy evaluation engine for Global Sentinel."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.structured_logger import get_logger
from src.core.telemetry import record_metric, start_span


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class PolicyDecision:
    """Decision envelope returned by every policy evaluation."""

    allowed: bool
    reason: str
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    timestamp: str = ""
    trace_id: str = ""
    trace: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.trace_id:
            self.trace_id = _trace_id()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PolicyEngine:
    """Single-point policy evaluation for execution and research decisions."""

    TIER_ORDER = {
        "tier_1_official": 1,
        "tier_2_operational": 2,
        "tier_3_research": 3,
        "tier_4_experimental": 4,
    }

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path("config")
        self._trust_hierarchy = _load_yaml(self.config_dir / "data_trust_hierarchy.yaml")
        self._quantum_policy = _load_yaml(self.config_dir / "quantum_lane_policy.yaml")
        self._execution_mode = _load_yaml(self.config_dir / "execution_mode.yaml")
        self._incident_policy = _load_yaml(self.config_dir / "incident_mode_policy.yaml")
        self._timing_guardrails = _load_yaml(self.config_dir / "intraday_timing_guardrails.yaml")
        self._options_rollout = _load_yaml(self.config_dir / "options_rollout.yaml")
        self._order_ttl = _load_yaml(self.config_dir / "order_ttl_policy.yaml")
        self._venue_policies = _load_yaml(self.config_dir / "venue_policies.yaml")
        self._sanctions = _load_yaml(self.config_dir / "sanctions_policies.yaml")
        self._graduation = _load_yaml(self.config_dir / "paper_trading_graduation.yaml")
        self._policy_config = _load_yaml(self.config_dir / "policy_engine_config.yaml")
        self._logger = get_logger("policy_engine", log_dir=self.config_dir.parent / "logs" / "policy")
        self._audit_log: List[Dict[str, Any]] = []

    def _source_tier(self, source: str) -> Optional[str]:
        normalized = str(source or "").strip()
        stripped = normalized.removesuffix("_bridge")
        for tier_name, tier_cfg in (self._trust_hierarchy.get("tiers") or {}).items():
            sources = {str(item) for item in tier_cfg.get("sources", [])}
            if normalized in sources or stripped in sources:
                return tier_name
        return None

    def _source_weight(self, source: str) -> float:
        tier = self._source_tier(source)
        if tier is None:
            return 0.0
        return _safe_float(((self._trust_hierarchy.get("tiers") or {}).get(tier) or {}).get("weight"), 0.0)

    def _current_mode(self) -> str:
        return str(self._execution_mode.get("current_mode", "NORMAL")).upper()

    def _quantum_stage(self) -> int:
        stages = self._quantum_policy.get("maturity_stages", {})
        for idx in range(3, 0, -1):
            stage = stages.get(f"stage_{idx}", {})
            if stage.get("active") is True:
                return idx
        return 1

    def _quantum_max_influence(self) -> float:
        caps = self._policy_config.get("quantum_influence_caps_by_stage", {})
        if caps:
            return _safe_float(caps.get(f"stage_{self._quantum_stage()}"), 0.0)
        stages = self._quantum_policy.get("maturity_stages", {})
        stage_cfg = stages.get(f"stage_{self._quantum_stage()}", {})
        return _safe_float(stage_cfg.get("max_influence_weight"), 0.0 if self._quantum_stage() < 3 else 0.15)

    def _record(self, eval_type: str, decision: PolicyDecision, context: Dict[str, Any]) -> None:
        entry = {
            "eval_type": eval_type,
            "decision": decision.to_dict(),
            "context_summary": {k: str(v)[:250] for k, v in context.items()},
        }
        self._audit_log.append(entry)
        record_metric("policy_evaluations_total", 1, eval_type=eval_type, allowed=decision.allowed)
        if decision.allowed:
            record_metric("policy_allowed_total", 1, eval_type=eval_type)
        else:
            record_metric("policy_blocked_total", 1, eval_type=eval_type)
        level = self._logger.info if decision.allowed else self._logger.warning
        level(
            f"policy_{eval_type}",
            trace_id=decision.trace_id,
            allowed=decision.allowed,
            reason=decision.reason,
            checks_passed=decision.checks_passed,
            checks_failed=decision.checks_failed,
            trace=decision.trace,
        )

    def _execution_floor_allows(self, tier: Optional[str]) -> Tuple[bool, str]:
        if tier is None:
            return True, "source_tier_unknown_allowed"

        floor = self._policy_config.get("trust_tier_execution_floor")
        if not floor:
            if tier == "tier_4_experimental" and (self._trust_hierarchy.get("rules") or {}).get("execution_block_tier_4", True):
                return False, "source_trust_tier_4_blocked"
            return True, "source_trust_tier_allowed_legacy"

        floor_rank = self.TIER_ORDER.get(str(floor), 99)
        tier_rank = self.TIER_ORDER.get(tier, 99)
        if tier_rank > floor_rank:
            return False, f"source_tier_{tier}_below_execution_floor_{floor}"
        return True, "source_tier_meets_execution_floor"

    def _timing_allows(self, trade_idea: Dict[str, Any]) -> Tuple[bool, str]:
        window_ctx = trade_idea.get("window_context") or {}
        if window_ctx.get("watchlist_only_window") is True:
            return False, "watchlist_only_window_active"
        if trade_idea.get("time_window_allowed") is False:
            return False, "time_window_disallowed"
        default = ((self._policy_config.get("timing_policy") or {}).get("default_allow_execution"))
        return bool(default is not False), "timing_window_allows_execution"

    def _venue_allows(self, trade_idea: Dict[str, Any]) -> Tuple[bool, str]:
        venue_policy = self._policy_config.get("venue_policy") or {}
        asset = str(trade_idea.get("asset_class", trade_idea.get("instrument_type", "equity"))).lower()
        venue = str(trade_idea.get("venue", "")).lower()
        if asset in {str(x).lower() for x in venue_policy.get("blocked_assets", [])}:
            return False, f"venue_policy_blocked_asset_{asset}"
        if venue and venue in {str(x).lower() for x in venue_policy.get("blocked_venues", [])}:
            return False, f"venue_policy_blocked_venue_{venue}"
        return True, "venue_policy_allows_trade"

    def _sanctions_allow(self, trade_idea: Dict[str, Any]) -> Tuple[bool, str]:
        blocked_symbols = {str(x).upper() for x in self._sanctions.get("blocked_symbols", [])}
        symbol = str(trade_idea.get("symbol", "")).upper()
        if symbol and symbol in blocked_symbols:
            return False, f"sanctions_blocked_{symbol}"
        blocked_entities = {str(x).lower() for x in self._sanctions.get("blocked_entities", [])}
        entity = str(trade_idea.get("issuer_name", "")).lower()
        if entity and entity in blocked_entities:
            return False, f"sanctions_blocked_entity_{entity}"
        return True, "sanctions_clear"

    def _ttl_allows(self, trade_idea: Dict[str, Any]) -> Tuple[bool, str]:
        ttl = trade_idea.get("order_ttl_minutes")
        if ttl is None:
            return True, "order_ttl_not_provided"
        min_ttl = _safe_float(self._policy_config.get("default_order_ttl_minutes_min"), 1.0)
        max_ttl = _safe_float(self._policy_config.get("default_order_ttl_minutes_max"), 1440.0)
        ttl_val = _safe_float(ttl, -1.0)
        if ttl_val < min_ttl or ttl_val > max_ttl:
            return False, f"order_ttl_{ttl_val}_outside_policy_bounds"
        return True, "order_ttl_within_bounds"

    def _lineage_allows(self, trade_idea: Dict[str, Any]) -> Tuple[bool, str]:
        blocked_terms = {str(x).lower() for x in self._policy_config.get("blocked_execution_lineage_terms", [])}
        blocked_sources = {str(x).lower() for x in self._policy_config.get("blocked_execution_sources", [])}
        lineage = [str(x).lower() for x in trade_idea.get("lineage_sources", [])]
        source = str(trade_idea.get("source", "")).lower()
        if source in blocked_sources:
            return False, f"blocked_source_{source}"
        for item in lineage:
            if item in blocked_sources:
                return False, f"blocked_lineage_source_{item}"
            if any(term in item for term in blocked_terms):
                return False, f"blocked_lineage_term_{item}"
        return True, "lineage_policy_clear"

    def evaluate_trade_idea(self, trade_idea: Dict[str, Any]) -> PolicyDecision:
        with start_span(
            "policy.evaluate.trade_idea",
            eval_type="trade_idea",
            source=str(trade_idea.get("source", "unknown")),
            symbol=str(trade_idea.get("symbol", "")),
        ):
            checks_passed: List[str] = []
            checks_failed: List[str] = []
            trace_id = _trace_id()

            source = trade_idea.get("source") or (trade_idea.get("strategy_context") or {}).get("source", "unknown")
            tier = self._source_tier(str(source))
            mode = self._current_mode()
            trace = {
                "source": source,
                "source_tier": tier,
                "source_weight": self._source_weight(str(source)),
                "mode": mode,
                "symbol": trade_idea.get("symbol"),
                "quantum_stage": self._quantum_stage(),
            }

            for allowed, label in [
                self._execution_floor_allows(tier),
                (mode not in {"CRISIS", "MANUAL_REVIEW"}, f"mode_{mode}_{'allows_shadow' if mode not in {'CRISIS', 'MANUAL_REVIEW'} else 'blocks_shadow_drafts'}"),
                (not bool(trade_idea.get("kill_switch_active")), "kill_switch_active" if trade_idea.get("kill_switch_active") else "kill_switch_clear"),
                (not bool(trade_idea.get("manual_veto_active")), "manual_veto_active" if trade_idea.get("manual_veto_active") else "manual_veto_clear"),
                self._timing_allows(trade_idea),
                self._venue_allows(trade_idea),
                self._sanctions_allow(trade_idea),
                self._ttl_allows(trade_idea),
                self._lineage_allows(trade_idea),
            ]:
                if allowed:
                    checks_passed.append(label)
                else:
                    checks_failed.append(label)

            asset_class = str(trade_idea.get("asset_class", trade_idea.get("instrument_type", ""))).lower()
            if "option" in asset_class:
                options_enabled = bool(((self._options_rollout.get("options") or self._options_rollout.get("options_rollout") or {}).get("enabled")))
                options_enabled = options_enabled or bool((self._policy_config.get("options_policy") or {}).get("default_options_enabled"))
                if options_enabled:
                    checks_passed.append("options_rollout_allows_trade")
                else:
                    checks_failed.append("options_rollout_disabled")

            quantum_weight = _safe_float(trade_idea.get("quantum_influence_weight"), 0.0)
            max_influence = self._quantum_max_influence()
            trace["quantum_influence_weight"] = quantum_weight
            trace["quantum_influence_cap"] = max_influence
            if quantum_weight > max_influence:
                checks_failed.append(f"quantum_influence_{quantum_weight:.2f}_exceeds_cap_{max_influence:.2f}")
            else:
                checks_passed.append("quantum_influence_within_cap")

            notional = _safe_float(trade_idea.get("notional"), 0.0)
            equity = _safe_float(trade_idea.get("portfolio_equity"), 100000.0)
            limit_pct = _safe_float(self._policy_config.get("max_single_order_notional_pct"), 0.12)
            trace["notional"] = notional
            trace["portfolio_equity"] = equity
            if equity > 0 and notional > equity * limit_pct:
                checks_failed.append(f"notional_{notional:.0f}_exceeds_{limit_pct * 100:.0f}pct_of_equity")
            else:
                checks_passed.append("notional_within_limits")

            decision = PolicyDecision(
                allowed=not checks_failed,
                reason="; ".join(checks_failed) if checks_failed else "all checks passed",
                checks_passed=checks_passed,
                checks_failed=checks_failed,
                trace_id=trace_id,
                trace=trace,
            )
            self._record("trade_idea", decision, trade_idea)
            return decision

    def evaluate_research_score_attachment(self, score: Dict[str, Any]) -> PolicyDecision:
        with start_span(
            "policy.evaluate.research_score_attachment",
            eval_type="research_score_attachment",
            request_id=str(score.get("request_id", "")),
        ):
            checks_passed: List[str] = []
            checks_failed: List[str] = []
            trace_id = _trace_id()
            trace = {"quantum_stage": self._quantum_stage(), "request_id": score.get("request_id")}

            rs = score.get("research_score")
            if rs is None or not (0.0 <= _safe_float(rs, -1.0) <= 1.0):
                checks_failed.append(f"research_score_{rs}_out_of_range_0_1")
            else:
                checks_passed.append("research_score_in_range")

            if bool(score.get("not_for_direct_execution")):
                checks_passed.append("not_for_direct_execution_set")
            else:
                checks_failed.append("missing_not_for_direct_execution_flag")

            bounded = score.get("bounded_secondary_signal_only", (score.get("guardrails") or {}).get("bounded_secondary_signal_only"))
            if bool(bounded):
                checks_passed.append("bounded_secondary_signal_only_set")
            else:
                checks_failed.append("missing_bounded_secondary_signal_only_flag")

            if score.get("quantum_sourced"):
                if bool(score.get("quantum_direct_execution_forbidden")):
                    checks_passed.append("quantum_direct_execution_forbidden_set")
                else:
                    checks_failed.append("missing_quantum_direct_execution_forbidden_flag")
            else:
                checks_passed.append("non_quantum_score")

            checks_passed.append("maturity_stage_ok" if self._quantum_stage() >= 1 else "maturity_stage_unknown")
            decision = PolicyDecision(
                allowed=not checks_failed,
                reason="; ".join(checks_failed) if checks_failed else "all checks passed",
                checks_passed=checks_passed,
                checks_failed=checks_failed,
                trace_id=trace_id,
                trace=trace,
            )
            self._record("research_score_attachment", decision, score)
            return decision

    def evaluate_weight_promotion(
        self,
        current_weights: Dict[str, float],
        proposed_weights: Dict[str, float],
        eval_metrics: Dict[str, Any],
    ) -> PolicyDecision:
        with start_span("policy.evaluate.weight_promotion", eval_type="weight_promotion"):
            checks_passed: List[str] = []
            checks_failed: List[str] = []
            trace_id = _trace_id()

            max_step = _safe_float(self._policy_config.get("max_abs_weight_step"), 0.05)
            min_eval_count = int(self._policy_config.get("min_eval_count_for_promotion", 50))
            max_drift = _safe_float(self._policy_config.get("max_cumulative_drift_std"), 2.0)
            mode = self._current_mode()
            trace = {
                "mode": mode,
                "max_abs_weight_step": max_step,
                "max_cumulative_drift_std": max_drift,
                "eval_metrics": dict(eval_metrics),
            }

            max_delta = 0.0
            for key in set(current_weights) | set(proposed_weights):
                delta = abs(_safe_float(proposed_weights.get(key)) - _safe_float(current_weights.get(key)))
                max_delta = max(max_delta, delta)
                if delta > max_step:
                    checks_failed.append(f"weight_{key}_delta_{delta:.4f}_exceeds_max_step_{max_step}")
            if not any(item.startswith("weight_") for item in checks_failed):
                checks_passed.append("all_weight_deltas_within_max_step")

            eval_count = int(eval_metrics.get("eval_count", 0))
            if eval_count < min_eval_count:
                checks_failed.append(f"eval_count_{eval_count}_below_min_{min_eval_count}")
            else:
                checks_passed.append("eval_count_sufficient")

            drift = _safe_float(eval_metrics.get("cumulative_drift_std"), 0.0)
            if drift > max_drift:
                checks_failed.append(f"cumulative_drift_{drift:.3f}_exceeds_{max_drift:.3f}")
            else:
                checks_passed.append("drift_within_limits")

            invalid_weight = False
            for key, value in proposed_weights.items():
                if value != value or abs(value) == float("inf"):
                    checks_failed.append(f"weight_{key}_is_nan_or_inf")
                    invalid_weight = True
            if not invalid_weight:
                checks_passed.append("no_nan_or_inf_weights")

            if mode in {"CRISIS", "MANUAL_REVIEW"}:
                checks_failed.append(f"mode_{mode}_blocks_weight_promotion")
            else:
                checks_passed.append("mode_allows_promotion")

            if bool(eval_metrics.get("safety_regression", False)):
                checks_failed.append("safety_regression_detected")
            else:
                checks_passed.append("no_safety_regressions")

            if self._policy_config.get("require_reproducibility_pass", False) and eval_metrics.get("reproducibility_pass") is False:
                checks_failed.append("reproducibility_check_failed")
            else:
                checks_passed.append("reproducibility_check_passed")

            trace["max_delta"] = max_delta
            decision = PolicyDecision(
                allowed=not checks_failed,
                reason="; ".join(checks_failed) if checks_failed else "all checks passed",
                checks_passed=checks_passed,
                checks_failed=checks_failed,
                trace_id=trace_id,
                trace=trace,
            )
            self._record("weight_promotion", decision, {"current_weights": current_weights, "proposed_weights": proposed_weights, "eval_metrics": eval_metrics})
            return decision

    def evaluate_mode_transition(self, current_mode: str, proposed_mode: str, trigger_data: Dict[str, Any]) -> PolicyDecision:
        with start_span(
            "policy.evaluate.mode_transition",
            eval_type="mode_transition",
            current_mode=str(current_mode),
            proposed_mode=str(proposed_mode),
        ):
            checks_passed: List[str] = []
            checks_failed: List[str] = []
            trace_id = _trace_id()

            current_mode = str(current_mode).upper()
            proposed_mode = str(proposed_mode).upper()
            valid_modes = {"NORMAL", "ELEVATED", "CRISIS", "MANUAL_REVIEW"}
            trace = {"current_mode": current_mode, "proposed_mode": proposed_mode, "trigger_data": dict(trigger_data)}

            if proposed_mode not in valid_modes:
                checks_failed.append(f"invalid_mode_{proposed_mode}")
            else:
                checks_passed.append("valid_mode")

            rules = self._policy_config.get("mode_transition_rules", {})
            allowed_targets = set((rules.get(current_mode) or {}).get("allowed_targets", []))
            if proposed_mode in allowed_targets or (not allowed_targets and proposed_mode == "MANUAL_REVIEW"):
                checks_passed.append("mode_transition_allowed")
            else:
                order = ["NORMAL", "ELEVATED", "CRISIS", "MANUAL_REVIEW"]
                try:
                    cur_idx = order.index(current_mode)
                    nxt_idx = order.index(proposed_mode)
                    if proposed_mode != "MANUAL_REVIEW" and abs(cur_idx - nxt_idx) > 1:
                        checks_failed.append(f"cannot_skip_from_{current_mode}_to_{proposed_mode}")
                    elif proposed_mode == "MANUAL_REVIEW":
                        checks_passed.append("manual_review_always_valid")
                    else:
                        checks_passed.append("valid_transition_step")
                except ValueError:
                    checks_failed.append("unknown_mode_transition")

            frozen_modes = {str(x).upper() for x in rules.get("freeze_config_in_modes", [])}
            if proposed_mode in frozen_modes and trigger_data.get("config_change_requested"):
                checks_failed.append(f"config_freeze_enforced_in_{proposed_mode}")
            else:
                checks_passed.append("config_freeze_clear")

            decision = PolicyDecision(
                allowed=not checks_failed,
                reason="; ".join(checks_failed) if checks_failed else "all checks passed",
                checks_passed=checks_passed,
                checks_failed=checks_failed,
                trace_id=trace_id,
                trace=trace,
            )
            self._record("mode_transition", decision, trace)
            return decision

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        return list(self._audit_log)

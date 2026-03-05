#!/usr/bin/env python3
"""
Global Sentinel V4.3 - Time Window Policy Engine
DST-aware ET time classification + strategy eligibility + window multipliers/guardrails

Reads:
- config/intraday_timing_guardrails.yaml

Outputs:
- current window classification
- overlap states
- event risk mode flags
- confidence/size multipliers
- risk budgets by window
- strategy eligibility and window blocks
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import json

try:
    import yaml
except ImportError:
    raise SystemExit("Please install pyyaml")


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def parse_hhmm(s: str) -> dt_time:
    hh, mm = s.split(":")
    return dt_time(hour=int(hh), minute=int(mm))


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class TimeWindowPolicyEngine:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "intraday_timing_guardrails.yaml")
        tz_name = self.cfg.get("timing_policy", {}).get("timezone", "America/New_York")
        self.et_tz = ZoneInfo(tz_name)

    # -----------------------------
    # Public API
    # -----------------------------
    def classify(
        self,
        timestamp_utc: Optional[datetime] = None,
        controls: Optional[Dict[str, bool]] = None,
        data_quality: Optional[Dict[str, Any]] = None,
        runtime_flags: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        controls:
          {manual_veto: bool, kill_switch: bool}
        data_quality:
          {quorum_pass: bool, fallback_mode: bool, luld_halt_detected: bool}
        runtime_flags:
          {major_release_day: bool, whipsaw_detected_after_open: bool, slippage_bps_estimate: float}
        """
        now_utc = timestamp_utc or datetime.now(timezone.utc)
        if isinstance(now_utc, str):
            now_utc = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_et = now_utc.astimezone(self.et_tz)

        controls = controls or {}
        data_quality = data_quality or {}
        runtime_flags = runtime_flags or {}

        policy = self.cfg.get("timing_policy", {})
        windows_cfg = policy.get("windows", {})
        strategy_policies = policy.get("strategy_policies", {})
        risk_budgets = policy.get("risk_budgets_by_window", {})
        universal = policy.get("universal_intraday_guardrails", {})

        current_window, window_policy = self._resolve_window(now_et, windows_cfg)
        overlaps = self._resolve_overlaps(now_et, policy.get("global_overlap_windows", {}))
        event_risk_mode = self._resolve_event_risk_mode(now_et, policy.get("economic_release_windows", {}), runtime_flags)

        # Base multipliers from window
        confidence_multiplier = float(window_policy.get("confidence_multiplier", 1.0))
        size_multiplier = float(window_policy.get("size_multiplier", 1.0))

        # Contingency adjustments (runtime)
        guardrail_blocks: List[str] = []
        if event_risk_mode:
            guardrail_blocks.append("event_risk_mode_active")
            confidence_multiplier *= 0.95
            size_multiplier *= 0.85

        if bool(runtime_flags.get("whipsaw_detected_after_open", False)):
            if current_window in {"opening_amateur_hour_cooldown", "opening_range_breakout_window"}:
                guardrail_blocks.append("opening_whipsaw_detected")
                confidence_multiplier *= 0.90
                size_multiplier *= 0.75

        slippage_est = runtime_flags.get("slippage_bps_estimate")
        max_slippage_bps = float(universal.get("max_slippage_bps_estimate", 25))
        if isinstance(slippage_est, (int, float)) and float(slippage_est) > max_slippage_bps:
            guardrail_blocks.append("slippage_estimate_above_threshold")
            size_multiplier *= 0.70

        # Universal control/data-quality blocks
        quorum_pass = data_quality.get("quorum_pass", True)
        fallback_mode = bool(data_quality.get("fallback_mode", False))
        luld = bool(data_quality.get("luld_halt_detected", False))

        if universal.get("block_new_setups_if_data_quorum_fails", True) and quorum_pass is False:
            guardrail_blocks.append("data_quorum_fail")
        if universal.get("block_new_setups_if_manual_veto", True) and controls.get("manual_veto", False):
            guardrail_blocks.append("manual_veto")
        if universal.get("block_new_setups_if_kill_switch", True) and controls.get("kill_switch", False):
            guardrail_blocks.append("kill_switch")
        if universal.get("block_new_setups_if_luld_halt_detected", True) and luld:
            guardrail_blocks.append("luld_halt_detected")

        # Window-level block
        shadow_execution_window_blocked = not bool(window_policy.get("allow_shadow_drafts", True))
        # Control/data-quality override block
        shadow_execution_window_blocked = shadow_execution_window_blocked or (len(guardrail_blocks) > 0)

        risk_budget = risk_budgets.get(current_window, {
            "max_gross_exposure_pct": None,
            "max_new_positions": None,
            "max_loss_budget_pct_of_daily": None
        })

        strategy_eligibility = self._build_strategy_eligibility(
            strategy_policies=strategy_policies,
            current_window=current_window,
            guardrail_blocks=guardrail_blocks,
            fallback_mode=fallback_mode,
            controls=controls,
        )

        confidence_multiplier = round(clamp(confidence_multiplier, 0.0, 2.0), 4)
        size_multiplier = round(clamp(size_multiplier, 0.0, 2.0), 4)

        out = {
            "timestamp_utc": now_utc.isoformat(),
            "timestamp_et": now_et.isoformat(),
            "timestamp_et_hhmm": now_et.strftime("%H:%M"),
            "timezone_et": str(self.et_tz),
            "current_window": current_window,
            "window_priority": window_policy.get("priority", "out_of_scope"),
            "window_policy": window_policy,
            "event_risk_mode": event_risk_mode,
            "overlap_states": overlaps,
            "confidence_multiplier": confidence_multiplier,
            "size_multiplier": size_multiplier,
            "risk_budget": risk_budget,
            "strategy_eligibility": strategy_eligibility,
            "window_guardrail_blocks": guardrail_blocks,
            "shadow_execution_window_blocked": shadow_execution_window_blocked,
            "preferred_setups": window_policy.get("preferred_setups", []),
            "restrictions": window_policy.get("restrictions", {}),
            "thresholds": window_policy.get("thresholds", {}),
            "use_for": window_policy.get("use_for", []),
        }
        return out

    # ------------------------------------------------------------------
    # Convenience: simple classify (backward compat with string timestamp)
    # ------------------------------------------------------------------
    def classify_str(self, timestamp_utc: str) -> Dict[str, Any]:
        """Classify from an ISO-8601 UTC string (backward compatible)."""
        return self.classify(timestamp_utc=timestamp_utc)

    # ------------------------------------------------------------------
    # Universal guardrails
    # ------------------------------------------------------------------
    def get_universal_guardrails(self) -> Dict[str, Any]:
        """Return the full universal_intraday_guardrails section from config."""
        policy = self.cfg.get("timing_policy", {})
        return dict(policy.get("universal_intraday_guardrails", {}))

    # ------------------------------------------------------------------
    # Contingency plan
    # ------------------------------------------------------------------
    def get_contingency_actions(self, trigger_name: str) -> Optional[List[str]]:
        """Look up the contingency action list for trigger_name."""
        policy = self.cfg.get("timing_policy", {})
        plan = policy.get("contingency_plan", {})
        entry = plan.get(trigger_name)
        if entry is None:
            return None
        return list(entry.get("action", []))

    # -----------------------------
    # Window resolution
    # -----------------------------
    def _resolve_window(self, now_et: datetime, windows_cfg: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        current_t = now_et.time()
        for name, cfg in windows_cfg.items():
            start = parse_hhmm(cfg["start"])
            end = parse_hhmm(cfg["end"])
            if self._time_in_range(current_t, start, end):
                return name, cfg
        return "out_of_policy_window", {
            "allow_shadow_drafts": False,
            "confidence_multiplier": 0.75,
            "size_multiplier": 0.0,
            "priority": "out_of_scope"
        }

    def _resolve_overlaps(self, now_et: datetime, overlaps_cfg: Dict[str, Any]) -> Dict[str, bool]:
        current_t = now_et.time()
        out: Dict[str, bool] = {}
        for name, cfg in overlaps_cfg.items():
            if not cfg.get("enabled", True):
                out[name] = False
                continue
            start = parse_hhmm(cfg["start"])
            end = parse_hhmm(cfg["end"])
            out[name] = self._time_in_range(current_t, start, end)
        return out

    def _resolve_event_risk_mode(self, now_et: datetime, econ_cfg: Dict[str, Any], runtime_flags: Dict[str, Any]) -> bool:
        if not bool(runtime_flags.get("major_release_day", False)):
            return bool(runtime_flags.get("force_event_risk_mode", False))

        release_cfg = econ_cfg.get("us_major_release_buffer", {})
        release_t = parse_hhmm(release_cfg.get("release_time_et", "08:30"))
        pre = int(release_cfg.get("pre_buffer_minutes", 10))
        post = int(release_cfg.get("post_buffer_minutes", 20))

        start_dt = now_et.replace(hour=release_t.hour, minute=release_t.minute, second=0, microsecond=0) - timedelta(minutes=pre)
        end_dt = now_et.replace(hour=release_t.hour, minute=release_t.minute, second=0, microsecond=0) + timedelta(minutes=post)
        return start_dt <= now_et <= end_dt

    @staticmethod
    def _time_in_range(t: dt_time, start: dt_time, end: dt_time) -> bool:
        if start <= end:
            return start <= t <= end
        return t >= start or t <= end

    # -----------------------------
    # Strategy policies
    # -----------------------------
    def _build_strategy_eligibility(
        self,
        strategy_policies: Dict[str, Any],
        current_window: str,
        guardrail_blocks: List[str],
        fallback_mode: bool,
        controls: Dict[str, bool],
    ) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        blocked_flags = set(guardrail_blocks)

        for strat_name, cfg in strategy_policies.items():
            allowed_windows = set(cfg.get("allowed_windows", []))
            allowed_here = current_window in allowed_windows if allowed_windows else True

            execution_allowed = cfg.get("execution_allowed", True)
            reasons = []

            if not allowed_here:
                reasons.append("window_not_allowed")

            for block_rule in cfg.get("block_if", []):
                if block_rule == "fallback_mode" and fallback_mode:
                    reasons.append("fallback_mode")
                elif block_rule == "manual_veto" and controls.get("manual_veto", False):
                    reasons.append("manual_veto")
                elif block_rule == "kill_switch" and controls.get("kill_switch", False):
                    reasons.append("kill_switch")
                elif block_rule == "trend_strength_too_high":
                    pass  # runtime-specific; left as advisory

            if "data_quorum_fail" in blocked_flags:
                reasons.append("data_quorum_fail")
            if "luld_halt_detected" in blocked_flags:
                reasons.append("luld_halt_detected")
            if "kill_switch" in blocked_flags and "kill_switch" not in reasons:
                reasons.append("kill_switch")
            if "manual_veto" in blocked_flags and "manual_veto" not in reasons:
                reasons.append("manual_veto")

            eligible = allowed_here and execution_allowed and (len(reasons) == 0)
            out[strat_name] = {
                "eligible": eligible,
                "allowed_window": allowed_here,
                "execution_allowed": execution_allowed,
                "requires": cfg.get("requires", []),
                "reasons_blocked": sorted(list(set(reasons))),
            }

        return out


# ---------------------------------------------------------------------------
# Backward compatibility: alias for code importing TimeWindowPolicy
# ---------------------------------------------------------------------------
TimeWindowPolicy = TimeWindowPolicyEngine


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    engine = TimeWindowPolicyEngine(repo_root)

    policy = engine.classify(
        controls={"manual_veto": False, "kill_switch": False},
        data_quality={"quorum_pass": True, "fallback_mode": False, "luld_halt_detected": False},
        runtime_flags={"major_release_day": False, "whipsaw_detected_after_open": False, "slippage_bps_estimate": 12}
    )

    print("=" * 72)
    print("Global Sentinel V4.3 - Time Window Policy Engine Demo")
    print("=" * 72)
    print(f"\nTimestamp ET: {policy['timestamp_et_hhmm']}")
    print(f"Current window: {policy['current_window']}")
    print(f"Window priority: {policy['window_priority']}")
    print(f"Confidence multiplier: {policy['confidence_multiplier']}")
    print(f"Size multiplier: {policy['size_multiplier']}")
    print(f"Shadow blocked: {policy['shadow_execution_window_blocked']}")
    print(f"Event risk mode: {policy['event_risk_mode']}")
    print(f"Overlaps: {policy['overlap_states']}")
    print(f"Guardrail blocks: {policy['window_guardrail_blocks']}")
    print("\nStrategy eligibility:")
    for sname, sinfo in policy.get("strategy_eligibility", {}).items():
        status = "ELIGIBLE" if sinfo["eligible"] else f"BLOCKED ({', '.join(sinfo['reasons_blocked'])})"
        print(f"  {sname}: {status}")
    print(f"\nRisk budget: {json.dumps(policy['risk_budget'], indent=2)}")
    print(f"\nUniversal guardrails: {json.dumps(engine.get_universal_guardrails(), indent=2)}")


if __name__ == "__main__":
    main()

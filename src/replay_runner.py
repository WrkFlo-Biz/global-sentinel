#!/usr/bin/env python3
"""
Global Sentinel V4.3 - Replay Runner (Time-Window-Aware, Calibrated Scoring)

Enhancements over V4 baseline:
- Calibrated scoring formulas (physical/kinetic/domestic/market + peak amplifier)
- Data quality penalties, control flag overrides, correlation break flagging
- TimeWindowPolicyEngine integration: window classification, strategy eligibility,
  multiplier validation, watchlist-only checks, shadow blocking per window policy
- Backward compatibility with events-based baseline fixtures (GenericScorer)

Fixture schema (V4.3 time-window aware):
{
  "name": "scenario_name",
  "snapshot": {
    "physical": {...},
    "alerts": {...},
    "domestic": {...},
    "market": {...},
    "data_quality": {...},
    "control_flags": {...},
    "meta": {...},
    "correlations": {...}
  },
  "expected": {
    "mode": "NORMAL|ELEVATED|CRISIS",
    "effective_mode": "MANUAL_REVIEW",
    "min_regime_probability": 0.55,
    "max_regime_probability": 0.90,
    "shadow_execution_blocked": true,
    "time_window_name": "opening_range_breakout_window",
    "time_window_hint_match": true,
    "watchlist_only_window": false,
    "window_shadow_execution_blocked": false,
    "min_confidence_multiplier": 0.80,
    "max_confidence_multiplier": 1.10,
    "min_size_multiplier": 0.50,
    "max_size_multiplier": 1.05,
    "strategy_eligible": ["orb_breakout_long"],
    "strategy_blocked": ["eod_fade_watchlist_only"]
  }
}

Usage:
  python src/replay_runner.py --repo-root . --fixtures tests/replays
  python src/replay_runner.py --repo-root . --fixtures tests/replays/ci_smoke
  python src/replay_runner.py --repo-root . --fixtures tests/replays/time_windows
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Ensure repo root is on sys.path for imports
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def norm_mode(mode: str) -> str:
    return str(mode or "").upper().strip()


# ---------------------------------------------------------------------------
# TimeWindowPolicyEngine loader (graceful fallback)
# ---------------------------------------------------------------------------
_TimeWindowPolicyEngine = None


def _load_time_window_engine():
    global _TimeWindowPolicyEngine
    if _TimeWindowPolicyEngine is not None:
        return _TimeWindowPolicyEngine

    # Try standard import first
    try:
        from src.alpha.time_window_policy import TimeWindowPolicyEngine
        _TimeWindowPolicyEngine = TimeWindowPolicyEngine
        return _TimeWindowPolicyEngine
    except ImportError:
        pass

    # Try importlib file-based fallback
    try:
        twp_path = PROJECT_ROOT / "src" / "alpha" / "time_window_policy.py"
        if twp_path.exists():
            spec = importlib.util.spec_from_file_location("time_window_policy", str(twp_path))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["time_window_policy"] = mod
            spec.loader.exec_module(mod)
            _TimeWindowPolicyEngine = getattr(mod, "TimeWindowPolicyEngine", None)
            return _TimeWindowPolicyEngine
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# EnhancedReplayScorer — calibrated scoring + time-window integration
# ---------------------------------------------------------------------------
class EnhancedReplayScorer:
    """
    Replay scorer with:
    - calibrated core regime scoring (physical/kinetic/domestic/market)
    - peak signal amplifier for strong single-component scenarios
    - data quality penalties
    - control flag overrides (effective_mode)
    - fallback mode logic
    - correlation break flagging (flag-only)
    - TimeWindowPolicyEngine integration
    """

    def __init__(self, thresholds: Dict[str, Any], repo_root: Optional[Path] = None):
        self.thresholds = thresholds or {}
        self.repo_root = repo_root or PROJECT_ROOT

        # Calibrated default weights — balanced for domestic/market-only scenarios
        self.weights = self.thresholds.get("weights", {
            "physical_reality": 0.30,
            "kinetic_trigger": 0.20,
            "domestic_stress": 0.30,
            "market_transmission": 0.20,
        })

        # Penalty defaults (can be overridden in config/thresholds.yaml)
        self.penalties_cfg = self.thresholds.get("confidence_penalties", {})
        self.stale_key_source_penalty = safe_float(self.penalties_cfg.get("stale_key_source_penalty"), 0.10)
        self.conflicting_signal_penalty = safe_float(self.penalties_cfg.get("conflicting_signal_penalty"), 0.08)
        self.fallback_data_mode_penalty = safe_float(self.penalties_cfg.get("fallback_data_mode_penalty"), 0.15)

        # Freshness quorum defaults
        self.freshness_cfg = self.thresholds.get("data_freshness_quorum", {})
        self.min_fresh_sources = int(self.freshness_cfg.get("minimum_fresh_sources_for_escalation", 2))

        # Correlation sanity defaults
        self.corr_cfg = self.thresholds.get("correlation_sanity_check", {})
        self.corr_break_threshold = safe_float(self.corr_cfg.get("zscore_break_threshold"), 2.0)

        # Mode thresholds (fallback to conventional defaults)
        hyst = self.thresholds.get("mode_hysteresis", {})
        self.elevated_enter = safe_float(hyst.get("elevated_enter"), 0.55)
        self.crisis_enter = safe_float(hyst.get("crisis_enter"), 0.75)

        # TimeWindowPolicyEngine (lazy init)
        self._tw_engine = None
        self._tw_engine_loaded = False

    def _get_tw_engine(self):
        if not self._tw_engine_loaded:
            self._tw_engine_loaded = True
            TWE = _load_time_window_engine()
            if TWE is not None:
                try:
                    self._tw_engine = TWE(self.repo_root)
                except Exception:
                    self._tw_engine = None
        return self._tw_engine

    def score(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        physical = snapshot.get("physical", {}) or {}
        alerts = snapshot.get("alerts", {}) or {}
        domestic = snapshot.get("domestic", {}) or {}
        market = snapshot.get("market", {}) or {}
        data_quality = snapshot.get("data_quality", {}) or {}
        control_flags = snapshot.get("control_flags", {}) or {}
        meta = snapshot.get("meta", {}) or {}
        correlations = snapshot.get("correlations", {}) or {}

        # --- Core signal extraction ---
        brent_chg = abs(safe_float(snapshot.get("brent_change_pct_5m", market.get("brent_change_pct_5m")), 0.0))
        vix_chg = abs(safe_float(snapshot.get("vix_change_pct_15m", market.get("vix_change_pct_15m")), 0.0))
        us10y_move = abs(safe_float(snapshot.get("us10y_bps_move_15m", market.get("us10y_bps_move_15m")), 0.0))

        vessel_density = safe_float(snapshot.get("hormuz_vessel_density_pct", physical.get("hormuz_vessel_density_pct")), 100.0)
        velocity_drop = safe_float(snapshot.get("hormuz_velocity_drop_pct", physical.get("hormuz_velocity_drop_pct")), 0.0)
        kinetic_intensity = safe_float(snapshot.get("kinetic_alert_intensity", alerts.get("kinetic_alert_intensity")), 0.0)
        domestic_intensity = safe_float(snapshot.get("domestic_stress_intensity", domestic.get("domestic_stress_intensity")), 0.0)

        # --- Calibrated component scores (0-1) ---
        # Physical: responsive to density drops from baseline (100%), velocity confirms
        physical_score = clamp((100.0 - vessel_density) / 75.0 * 0.6 + (velocity_drop / 25.0) * 0.4)
        # Kinetic: alert intensity primary, brent confirms
        kinetic_score = clamp(0.6 * kinetic_intensity + 0.4 * (brent_chg / 4.0))
        # Domestic: direct stress score
        domestic_score = clamp(domestic_intensity)
        # Market: responsive to large moves across brent/vix/us10y
        market_score = clamp((brent_chg / 4.0) * 0.35 + (vix_chg / 12.0) * 0.35 + (us10y_move / 10.0) * 0.30)

        base_p = (
            self.weights.get("physical_reality", 0.30) * physical_score
            + self.weights.get("kinetic_trigger", 0.20) * kinetic_score
            + self.weights.get("domestic_stress", 0.30) * domestic_score
            + self.weights.get("market_transmission", 0.20) * market_score
        )

        # Peak signal amplifier — strong single-component signals boost overall score
        peak = max(physical_score, kinetic_score, domestic_score, market_score)
        raw_p = clamp(base_p * 0.85 + peak * 0.15)

        # --- Data quality penalties & flags ---
        penalty_breakdown: Dict[str, float] = {}
        evidence_flags: List[str] = []

        # Freshness quorum
        fresh_sources_count = data_quality.get("fresh_sources_count")
        quorum_pass = data_quality.get("quorum_pass")
        if quorum_pass is False:
            penalty_breakdown["freshness_quorum_fail"] = self.stale_key_source_penalty
            evidence_flags.append("freshness_quorum_fail")
        elif isinstance(fresh_sources_count, int) and fresh_sources_count < self.min_fresh_sources:
            penalty_breakdown["fresh_sources_below_min"] = self.stale_key_source_penalty
            evidence_flags.append("fresh_sources_below_min")

        # Staleness penalties (key feeds)
        staleness = data_quality.get("staleness_seconds", {}) or {}
        if isinstance(staleness, dict) and staleness:
            stale_keys = [k for k, v in staleness.items() if safe_float(v, 0.0) > 300]
            if stale_keys:
                penalty = self.stale_key_source_penalty + min(0.03 * max(len(stale_keys) - 1, 0), 0.09)
                penalty_breakdown["stale_key_sources"] = round(penalty, 4)
                evidence_flags.append(f"stale_sources:{','.join(stale_keys)}")

        # Missing feeds penalty
        missing_feeds = data_quality.get("missing_feeds", []) or []
        if isinstance(missing_feeds, list) and missing_feeds:
            penalty = min(0.04 * len(missing_feeds), 0.12)
            penalty_breakdown["missing_feeds"] = round(penalty, 4)
            evidence_flags.append(f"missing_feeds:{','.join(str(m) for m in missing_feeds)}")

        # Conflicting signals penalty
        if data_quality.get("conflicting_signals") is True:
            penalty_breakdown["conflicting_signals"] = self.conflicting_signal_penalty
            evidence_flags.append("conflicting_signals")

        # Fallback mode penalty (meta)
        fallback_mode = bool(meta.get("fallback_mode", False))
        if fallback_mode:
            penalty_breakdown["fallback_mode"] = self.fallback_data_mode_penalty
            evidence_flags.append("fallback_mode")

        total_penalty = round(sum(penalty_breakdown.values()), 4)
        adjusted_p = clamp(raw_p - total_penalty)

        # Confidence drops with penalties / missingness
        confidence = 0.90
        confidence -= total_penalty
        if fallback_mode:
            confidence -= 0.05
        if control_flags.get("manual_veto") or control_flags.get("kill_switch"):
            confidence -= 0.02
        confidence = round(clamp(confidence), 4)

        # --- Mode classification (based on raw signal, before data-quality penalties) ---
        raw_mode = "NORMAL"
        if raw_p >= self.crisis_enter:
            raw_mode = "CRISIS"
        elif raw_p >= self.elevated_enter:
            raw_mode = "ELEVATED"

        # Effective mode (control override)
        manual_veto = bool(control_flags.get("manual_veto", False))
        kill_switch = bool(control_flags.get("kill_switch", False))
        effective_mode = "MANUAL_REVIEW" if (manual_veto or kill_switch) else raw_mode

        # Shadow draft eligibility (replay audit only)
        shadow_execution_blocked = False
        if manual_veto or kill_switch or fallback_mode:
            shadow_execution_blocked = True
        if quorum_pass is False:
            shadow_execution_blocked = True

        # --- Correlation break flagging (flag-only) ---
        corr_breaks = []
        if isinstance(correlations, dict):
            for k, v in correlations.items():
                z = safe_float(v, 0.0)
                if abs(z) >= self.corr_break_threshold:
                    corr_breaks.append({"metric": k, "zscore": round(z, 3)})

        return {
            "regime_shift_probability": round(adjusted_p, 4),
            "raw_regime_shift_probability": round(raw_p, 4),
            "mode": raw_mode,
            "effective_mode": effective_mode,
            "confidence": confidence,
            "component_scores": {
                "physical_reality": round(physical_score, 4),
                "kinetic_trigger": round(kinetic_score, 4),
                "domestic_stress": round(domestic_score, 4),
                "market_transmission": round(market_score, 4),
            },
            "penalty_breakdown": penalty_breakdown,
            "total_penalty": total_penalty,
            "data_quality_summary": {
                "fresh_sources_count": fresh_sources_count,
                "quorum_pass": quorum_pass,
                "missing_feeds": missing_feeds,
                "conflicting_signals": bool(data_quality.get("conflicting_signals", False)),
                "fallback_mode": fallback_mode,
            },
            "control_flags": {
                "manual_veto": manual_veto,
                "kill_switch": kill_switch,
            },
            "shadow_execution_blocked": shadow_execution_blocked,
            "correlation_flags": {
                "break_detected": len(corr_breaks) > 0,
                "breaks": corr_breaks,
                "action": "flag_only" if corr_breaks else None,
            },
            "audit_flags": evidence_flags,
        }


class GenericScorer:
    """Score generic event-based fixtures (used by baseline.json)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from src.scoring.regime_shift import RegimeShiftScorer
            self.scorer = RegimeShiftScorer(config)
        except Exception:
            self.scorer = None

    def score(self) -> Dict[str, Any]:
        if self.scorer:
            result = self.scorer.score()
            # Ensure mode is derived if scorer doesn't set it
            if not result.get("mode"):
                prob = float(result.get("regime_shift_probability", 0))
                if prob >= 0.75:
                    result["mode"] = "CRISIS"
                elif prob >= 0.55:
                    result["mode"] = "ELEVATED"
                else:
                    result["mode"] = "NORMAL"
            return result
        return {
            "regime_shift_probability": 0.2,
            "mode": "NORMAL",
            "component_scores": {},
            "confidence": 0.8,
            "freshness": {},
            "fallback_mode": False,
        }


# ---------------------------------------------------------------------------
# Time-Window Resolution Helpers
# ---------------------------------------------------------------------------

def _resolve_replay_timestamp_utc(case: Dict[str, Any]) -> Optional[str]:
    """
    Resolve timestamp_utc from fixture in priority order:
    1. top-level timestamp_utc
    2. snapshot.meta.timestamp_utc
    3. snapshot.meta.time_window_hint -> synthesize
    4. None (time-window checks skipped)
    """
    ts = case.get("timestamp_utc")
    if ts:
        return str(ts)

    meta = (case.get("snapshot", {}) or {}).get("meta", {}) or {}
    ts = meta.get("timestamp_utc")
    if ts:
        return str(ts)

    hint = meta.get("time_window_hint")
    if hint:
        return _synthesize_timestamp_from_window_hint(hint)

    return None


# Map window names to representative ET times for fixture synthesis
_WINDOW_HINT_MAP = {
    "premarket_signal_prep": "08:00",
    "opening_amateur_hour_cooldown": "09:35",
    "opening_range_breakout_window": "10:00",
    "late_morning_mean_reversion": "11:00",
    "lunch_lull": "12:30",
    "post_lunch_reacceleration": "14:30",
    "power_hour": "15:30",
    "close_exhaustion_watch": "15:55",
}


def _synthesize_timestamp_from_window_hint(hint: str) -> Optional[str]:
    """Synthesize a UTC timestamp from a window hint name (for fixture testing)."""
    et_time_str = _WINDOW_HINT_MAP.get(hint)
    if not et_time_str:
        return None
    # Build a datetime in ET for today, convert to UTC
    try:
        from zoneinfo import ZoneInfo
        et_tz = ZoneInfo("America/New_York")
        today = datetime.now(et_tz).date()
        hh, mm = et_time_str.split(":")
        et_dt = datetime(today.year, today.month, today.day, int(hh), int(mm), 0, tzinfo=et_tz)
        utc_dt = et_dt.astimezone(timezone.utc)
        return utc_dt.isoformat()
    except Exception:
        return None


def _derive_watchlist_only_window(window_name: str, window_policy: Dict[str, Any]) -> bool:
    """Determine if a window is watchlist-only (no new execution)."""
    if window_name in ("close_exhaustion_watch", "premarket_signal_prep"):
        return True
    # Check size_multiplier == 0
    if safe_float(window_policy.get("size_multiplier"), 1.0) == 0.0:
        return True
    # Check restrictions
    restrictions = window_policy.get("restrictions", {}) or {}
    if restrictions.get("no_new_intraday_risk_add") is True:
        return True
    if restrictions.get("watchlist_only_unless_exceptional_catalyst") is True:
        return True
    return False


def _apply_time_window_policy(
    scorer: EnhancedReplayScorer,
    case: Dict[str, Any],
    timestamp_utc_str: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Apply TimeWindowPolicyEngine classify and return result, or None if unavailable."""
    tw_engine = scorer._get_tw_engine()
    if tw_engine is None or timestamp_utc_str is None:
        return None

    snapshot = case.get("snapshot", {}) or {}
    control_flags = snapshot.get("control_flags", {}) or {}
    data_quality = snapshot.get("data_quality", {}) or {}
    meta = snapshot.get("meta", {}) or {}

    runtime_flags = {}
    rf = meta.get("runtime_flags", {}) or {}
    runtime_flags["major_release_day"] = rf.get("major_release_day", False)
    runtime_flags["whipsaw_detected_after_open"] = rf.get("whipsaw_detected_after_open", False)
    runtime_flags["slippage_bps_estimate"] = rf.get("slippage_bps_estimate", 0)

    try:
        tw_result = tw_engine.classify(
            timestamp_utc=timestamp_utc_str,
            controls={
                "manual_veto": bool(control_flags.get("manual_veto", False)),
                "kill_switch": bool(control_flags.get("kill_switch", False)),
            },
            data_quality={
                "quorum_pass": data_quality.get("quorum_pass", True),
                "fallback_mode": bool(meta.get("fallback_mode", False)),
                "luld_halt_detected": bool(meta.get("luld_halt_detected", False)),
            },
            runtime_flags=runtime_flags,
        )
        return tw_result
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Evaluate a single replay case
# ---------------------------------------------------------------------------

def evaluate_case(case: Dict[str, Any], scorer: EnhancedReplayScorer,
                  generic_scorer: GenericScorer) -> Dict[str, Any]:
    """Evaluate a single replay case with time-window-aware checks."""
    name = case.get("name", "unnamed_case")
    expected = case.get("expected", case.get("expected_outcomes", {})) or {}
    snapshot = case.get("snapshot", {})
    meta = (snapshot.get("meta", {}) or {}) if isinstance(snapshot, dict) else {}

    # Determine fixture type
    if "snapshot" in case:
        pred = scorer.score(case["snapshot"])
    elif "events" in case:
        results = []
        for event in case["events"]:
            results.append(generic_scorer.score())
        pred = results[-1] if results else {"regime_shift_probability": 0, "mode": "NORMAL"}
        if not pred.get("mode"):
            prob = float(pred.get("regime_shift_probability", 0))
            if prob >= 0.75:
                pred["mode"] = "CRISIS"
            elif prob >= 0.55:
                pred["mode"] = "ELEVATED"
            else:
                pred["mode"] = "NORMAL"
        pred.setdefault("effective_mode", pred.get("mode"))
        pred.setdefault("confidence", 0.8)
        pred.setdefault("shadow_execution_blocked", False)
        pred.setdefault("correlation_flags", {"break_detected": False, "breaks": [], "action": None})
    else:
        pred = {"regime_shift_probability": 0, "mode": "UNKNOWN", "error": "unknown fixture format"}

    # --- Time-window policy integration ---
    timestamp_utc_str = _resolve_replay_timestamp_utc(case)
    tw_result = _apply_time_window_policy(scorer, case, timestamp_utc_str)

    if tw_result is not None:
        pred["time_window_policy"] = {
            "current_window": tw_result.get("current_window"),
            "window_priority": tw_result.get("window_priority"),
            "confidence_multiplier": tw_result.get("confidence_multiplier"),
            "size_multiplier": tw_result.get("size_multiplier"),
            "event_risk_mode": tw_result.get("event_risk_mode"),
            "shadow_execution_window_blocked": tw_result.get("shadow_execution_window_blocked"),
            "window_guardrail_blocks": tw_result.get("window_guardrail_blocks", []),
            "strategy_eligibility": tw_result.get("strategy_eligibility", {}),
            "overlap_states": tw_result.get("overlap_states", {}),
        }
        window_name = tw_result.get("current_window", "")
        window_policy = tw_result.get("window_policy", {}) or {}
        pred["time_window_policy"]["watchlist_only_window"] = _derive_watchlist_only_window(window_name, window_policy)
    else:
        pred["time_window_policy"] = None

    checks = []
    passed = True

    # ===== Standard regime checks =====

    # 1) Raw mode check
    expected_mode = expected.get("mode", expected.get("final_mode"))
    if expected_mode:
        ok = norm_mode(pred.get("mode", "")) == norm_mode(expected_mode)
        checks.append({"check": "mode_match", "expected": expected_mode, "actual": pred.get("mode"), "pass": ok})
        passed = passed and ok

    # 2) Effective mode check
    expected_effective_mode = expected.get("effective_mode") or meta.get("expected_mode_override")
    if expected_effective_mode:
        ok = norm_mode(pred.get("effective_mode", "")) == norm_mode(expected_effective_mode)
        checks.append({
            "check": "effective_mode_match",
            "expected": expected_effective_mode,
            "actual": pred.get("effective_mode"),
            "pass": ok,
        })
        passed = passed and ok

    # 3) Probability bounds (uses adjusted probability)
    min_p = expected.get("min_regime_probability")
    if min_p is not None:
        ok = float(pred.get("regime_shift_probability", 0)) >= float(min_p)
        checks.append({"check": "min_probability", "expected": min_p, "actual": pred.get("regime_shift_probability"), "pass": ok})
        passed = passed and ok

    max_p = expected.get("max_regime_probability")
    if max_p is not None:
        ok = float(pred.get("regime_shift_probability", 0)) <= float(max_p)
        checks.append({"check": "max_probability", "expected": max_p, "actual": pred.get("regime_shift_probability"), "pass": ok})
        passed = passed and ok

    # 4) Confidence check
    min_conf = expected.get("min_confidence")
    if min_conf is not None:
        actual_conf = pred.get("confidence", 0.8)
        ok = float(actual_conf) >= float(min_conf)
        checks.append({"check": "min_confidence", "expected": min_conf, "actual": actual_conf, "pass": ok})
        passed = passed and ok

    # 5) Shadow execution blocked expectation
    expected_shadow_blocked = expected.get("shadow_execution_blocked")
    if expected_shadow_blocked is None and "expected_shadow_execution_blocked" in meta:
        expected_shadow_blocked = bool(meta.get("expected_shadow_execution_blocked"))
    if expected_shadow_blocked is not None:
        ok = bool(pred.get("shadow_execution_blocked", False)) == bool(expected_shadow_blocked)
        checks.append({
            "check": "shadow_execution_blocked_match",
            "expected": bool(expected_shadow_blocked),
            "actual": bool(pred.get("shadow_execution_blocked", False)),
            "pass": ok,
        })
        passed = passed and ok

    # 6) Correlation break flag expectation
    if "correlation_break_detected" in meta:
        expected_corr = bool(meta.get("correlation_break_detected"))
        actual_corr = bool(pred.get("correlation_flags", {}).get("break_detected", False))
        ok = actual_corr == expected_corr
        checks.append({
            "check": "correlation_break_flag_match",
            "expected": expected_corr,
            "actual": actual_corr,
            "pass": ok,
        })
        passed = passed and ok

    # ===== Time-window checks (V4.3) =====
    tw_policy = pred.get("time_window_policy")

    # 7) Time window name match
    expected_tw_name = expected.get("time_window_name")
    if expected_tw_name and tw_policy:
        actual_tw_name = tw_policy.get("current_window", "")
        ok = actual_tw_name == expected_tw_name
        checks.append({
            "check": "time_window_name",
            "expected": expected_tw_name,
            "actual": actual_tw_name,
            "pass": ok,
        })
        passed = passed and ok

    # 8) Time window hint match (fixture declared the hint → verify engine resolved same window)
    expected_hint_match = expected.get("time_window_hint_match")
    if expected_hint_match is not None and tw_policy:
        hint = meta.get("time_window_hint", "")
        actual_tw_name = tw_policy.get("current_window", "")
        actual_hint_match = (hint == actual_tw_name)
        ok = bool(actual_hint_match) == bool(expected_hint_match)
        checks.append({
            "check": "time_window_hint_match",
            "expected": bool(expected_hint_match),
            "actual": actual_hint_match,
            "pass": ok,
        })
        passed = passed and ok

    # 9) Watchlist-only window check
    expected_watchlist = expected.get("watchlist_only_window")
    if expected_watchlist is not None and tw_policy:
        actual_watchlist = bool(tw_policy.get("watchlist_only_window", False))
        ok = actual_watchlist == bool(expected_watchlist)
        checks.append({
            "check": "watchlist_only_window",
            "expected": bool(expected_watchlist),
            "actual": actual_watchlist,
            "pass": ok,
        })
        passed = passed and ok

    # 10) Window shadow execution blocked check
    expected_win_shadow = expected.get("window_shadow_execution_blocked")
    if expected_win_shadow is not None and tw_policy:
        actual_win_shadow = bool(tw_policy.get("shadow_execution_window_blocked", False))
        ok = actual_win_shadow == bool(expected_win_shadow)
        checks.append({
            "check": "window_shadow_execution_blocked",
            "expected": bool(expected_win_shadow),
            "actual": actual_win_shadow,
            "pass": ok,
        })
        passed = passed and ok

    # 11) Confidence multiplier range check
    min_cm = expected.get("min_confidence_multiplier")
    max_cm = expected.get("max_confidence_multiplier")
    if tw_policy and (min_cm is not None or max_cm is not None):
        actual_cm = safe_float(tw_policy.get("confidence_multiplier"), 1.0)
        if min_cm is not None:
            ok = actual_cm >= float(min_cm)
            checks.append({"check": "min_confidence_multiplier", "expected": min_cm, "actual": actual_cm, "pass": ok})
            passed = passed and ok
        if max_cm is not None:
            ok = actual_cm <= float(max_cm)
            checks.append({"check": "max_confidence_multiplier", "expected": max_cm, "actual": actual_cm, "pass": ok})
            passed = passed and ok

    # 12) Size multiplier range check
    min_sm = expected.get("min_size_multiplier")
    max_sm = expected.get("max_size_multiplier")
    if tw_policy and (min_sm is not None or max_sm is not None):
        actual_sm = safe_float(tw_policy.get("size_multiplier"), 1.0)
        if min_sm is not None:
            ok = actual_sm >= float(min_sm)
            checks.append({"check": "min_size_multiplier", "expected": min_sm, "actual": actual_sm, "pass": ok})
            passed = passed and ok
        if max_sm is not None:
            ok = actual_sm <= float(max_sm)
            checks.append({"check": "max_size_multiplier", "expected": max_sm, "actual": actual_sm, "pass": ok})
            passed = passed and ok

    # 13) Strategy eligibility check
    expected_eligible = expected.get("strategy_eligible")
    if expected_eligible and tw_policy:
        strat_elig = tw_policy.get("strategy_eligibility", {}) or {}
        for strat_name in expected_eligible:
            actual_elig = strat_elig.get(strat_name, {}).get("eligible", False)
            ok = bool(actual_elig)
            checks.append({
                "check": "strategy_eligible",
                "expected": f"{strat_name}=eligible",
                "actual": f"{strat_name}={'eligible' if actual_elig else 'blocked'}",
                "pass": ok,
            })
            passed = passed and ok

    # 14) Strategy blocked check
    expected_blocked = expected.get("strategy_blocked")
    if expected_blocked and tw_policy:
        strat_elig = tw_policy.get("strategy_eligibility", {}) or {}
        for strat_name in expected_blocked:
            actual_elig = strat_elig.get(strat_name, {}).get("eligible", True)
            ok = not bool(actual_elig)
            checks.append({
                "check": "strategy_blocked",
                "expected": f"{strat_name}=blocked",
                "actual": f"{strat_name}={'blocked' if not actual_elig else 'eligible'}",
                "pass": ok,
            })
            passed = passed and ok

    return {"name": name, "pass": passed, "prediction": pred, "checks": checks}


# ---------------------------------------------------------------------------
# Run replay
# ---------------------------------------------------------------------------

def run_replay(repo_root: Path, fixtures_input: str) -> Dict[str, Any]:
    """Run replay against fixtures (directory or single file)."""
    thresholds_path = repo_root / "config" / "thresholds.yaml"
    config = load_yaml(thresholds_path) if thresholds_path.exists() else {}

    scorer = EnhancedReplayScorer(config, repo_root=repo_root)
    generic_scorer = GenericScorer(config)

    fixtures_path = Path(fixtures_input)
    if not fixtures_path.is_absolute():
        fixtures_path = repo_root / fixtures_input

    cases: List[Dict[str, Any]] = []
    if fixtures_path.is_dir():
        for f in sorted(fixtures_path.rglob("*.json")):
            if f.name.startswith("fixtures_manifest"):
                continue
            try:
                case = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(case, dict) and ("snapshot" in case or "events" in case):
                    cases.append(case)
            except Exception:
                continue
    elif fixtures_path.is_file():
        try:
            case = json.loads(fixtures_path.read_text(encoding="utf-8"))
            if isinstance(case, dict):
                cases.append(case)
        except Exception:
            pass

    results = [evaluate_case(c, scorer, generic_scorer) for c in cases]

    total = len(results)
    passed_count = sum(1 for r in results if r["pass"])
    probs = [r["prediction"].get("regime_shift_probability", 0) for r in results]
    confs = [r["prediction"].get("confidence", 0.9) for r in results]

    raw_mode_counts = {"NORMAL": 0, "ELEVATED": 0, "CRISIS": 0}
    effective_mode_counts: Dict[str, int] = {}
    corr_break_count = 0
    shadow_blocked_count = 0

    # Time-window metrics
    time_window_counts: Dict[str, int] = {}
    window_shadow_blocked_count = 0
    watchlist_only_window_count = 0
    time_window_policy_integrated = False

    for r in results:
        pred = r["prediction"]
        raw_mode = pred.get("mode", "")
        if raw_mode in raw_mode_counts:
            raw_mode_counts[raw_mode] += 1
        eff_mode = pred.get("effective_mode", raw_mode)
        effective_mode_counts[eff_mode] = effective_mode_counts.get(eff_mode, 0) + 1
        if pred.get("correlation_flags", {}).get("break_detected"):
            corr_break_count += 1
        if pred.get("shadow_execution_blocked") is True:
            shadow_blocked_count += 1

        tw_policy = pred.get("time_window_policy")
        if tw_policy is not None:
            time_window_policy_integrated = True
            tw_name = tw_policy.get("current_window", "unknown")
            time_window_counts[tw_name] = time_window_counts.get(tw_name, 0) + 1
            if tw_policy.get("shadow_execution_window_blocked"):
                window_shadow_blocked_count += 1
            if tw_policy.get("watchlist_only_window"):
                watchlist_only_window_count += 1

    summary = {
        "timestamp": iso_now(),
        "status": "ok" if total > 0 else "no_fixtures",
        "fixtures_dir": str(fixtures_path),
        "total_cases": total,
        "passed_cases": passed_count,
        "failed_cases": max(total - passed_count, 0),
        "pass_rate": round(passed_count / total, 4) if total else None,
        "avg_predicted_probability": round(statistics.mean(probs), 4) if probs else None,
        "avg_confidence": round(statistics.mean(confs), 4) if confs else None,
        "raw_mode_counts": raw_mode_counts,
        "effective_mode_counts": effective_mode_counts,
        "correlation_break_count": corr_break_count,
        "shadow_execution_blocked_count": shadow_blocked_count,
        "time_window_policy_integrated": time_window_policy_integrated,
        "time_window_counts": time_window_counts,
        "window_shadow_blocked_count": window_shadow_blocked_count,
        "watchlist_only_window_count": watchlist_only_window_count,
        "results": results,
    }

    # Write report
    out_dir = repo_root / "reports" / "openclaw_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    (out_dir / f"replay_runner_{ts}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Global Sentinel Replay Runner")
    parser.add_argument("scenario", nargs="?", help="Path to scenario JSON file (legacy mode)")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--fixtures", default="tests/replays")
    parser.add_argument("--config", help="Path to thresholds.yaml")
    parser.add_argument("--candidate-config", help="Path to candidate thresholds.yaml for comparison")
    parser.add_argument("--output", default=None, help="Path to write replay output JSON")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    fixtures = args.scenario or args.fixtures

    result = run_replay(repo_root, fixtures)

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = repo_root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(json.dumps({
        "status": result["status"],
        "total_cases": result["total_cases"],
        "passed_cases": result.get("passed_cases"),
        "pass_rate": result["pass_rate"],
        "avg_confidence": result.get("avg_confidence"),
        "correlation_break_count": result.get("correlation_break_count"),
        "time_window_policy_integrated": result.get("time_window_policy_integrated"),
    }, indent=2))


if __name__ == "__main__":
    main()

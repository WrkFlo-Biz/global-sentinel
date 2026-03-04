#!/usr/bin/env python3
"""
Global Sentinel V4.5 - Fill Simulator (Shadow Realism Layer)

Purpose:
- Simulate execution realism for shadow candidates
- Estimate fill feasibility, slippage, partial fills, and routing risk
- Provide "do-not-route-even-in-shadow" flags for low-quality setups

Inputs:
- candidate row from idiosyncratic_package_builder
- packet/window/runtime context
- optional quote/liquidity snapshot (if available)

Outputs:
- execution realism assessment dict (no orders submitted)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Helpers
# -----------------------------
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def safe_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# -----------------------------
# Config model
# -----------------------------
@dataclass
class FillSimConfig:
    # Slippage baseline by time-window bucket (bps)
    slippage_bps_baseline_opening: float = 14.0
    slippage_bps_baseline_lunch: float = 8.0
    slippage_bps_baseline_power_hour: float = 16.0
    slippage_bps_baseline_other: float = 10.0

    # Option penalty multipliers (options are harder to fill cleanly)
    options_slippage_multiplier: float = 1.8
    options_partial_fill_penalty: float = 0.15
    options_completion_penalty: float = 0.10

    # Short-side penalties (locate/SSR/friction proxy)
    short_slippage_multiplier: float = 1.15
    short_reject_risk_add: float = 0.08

    # Time-window microstructure multipliers
    opening_whipsaw_slippage_multiplier: float = 1.6
    major_release_day_slippage_multiplier: float = 1.35
    lunch_lull_fill_completion_multiplier: float = 0.80
    close_exhaustion_watch_fill_block: bool = True

    # Spread / slippage thresholds
    hard_block_slippage_bps: float = 35.0
    soft_block_slippage_bps: float = 22.0
    max_runtime_estimated_slippage_bps_allowed: float = 30.0

    # Probability baselines
    base_fill_feasibility: float = 0.82
    base_partial_fill_probability: float = 0.18
    base_fill_completion_probability: float = 0.88
    base_reject_risk_probability: float = 0.04


# -----------------------------
# Fill Simulator
# -----------------------------
class FillSimulator:
    def __init__(self, config: Optional[FillSimConfig] = None):
        self.cfg = config or FillSimConfig()

    def assess_candidate(
        self,
        candidate: Dict[str, Any],
        packet: Dict[str, Any],
        quote_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        candidate: row from idiosyncratic_package_builder
        packet: crisis_monitor packet or package context
        quote_snapshot (optional):
          {
            "bid": ...,
            "ask": ...,
            "last": ...,
            "spread_bps": ...,
            "rvol": ...,
            "halted": bool,
            "quote_age_ms": ...
          }
        """
        quote_snapshot = quote_snapshot or {}

        # Context
        window_name = str(candidate.get("window_name") or packet.get("time_window_name") or "")
        runtime_flags = packet.get("runtime_flags") or packet.get("runtime_flags_snapshot") or {}
        execution_constraints = candidate.get("execution_constraints") or packet.get("execution_constraints") or {}

        direction = str(candidate.get("direction", ""))
        instrument_types = [str(x) for x in (candidate.get("instrument_types") or [])]
        confidence = safe_float(candidate.get("confidence_score"), safe_float(packet.get("confidence"), 0.5))
        size_mult = safe_float(candidate.get("size_multiplier_suggestion"), 1.0)

        # Optional quote/liquidity data
        spread_bps = safe_float(quote_snapshot.get("spread_bps"), 0.0)
        rvol = safe_float(quote_snapshot.get("rvol"), 0.0)
        halted = safe_bool(quote_snapshot.get("halted"), False)
        quote_age_ms = safe_float(quote_snapshot.get("quote_age_ms"), 0.0)

        # Runtime flags
        major_release_day = safe_bool(runtime_flags.get("major_release_day"), False)
        whipsaw_detected_after_open = safe_bool(runtime_flags.get("whipsaw_detected_after_open"), False)
        luld_halt_detected = safe_bool(runtime_flags.get("luld_halt_detected"), False)
        broker_status_degraded = safe_bool(runtime_flags.get("broker_status_degraded"), False)
        runtime_slippage_bps = safe_float(runtime_flags.get("slippage_bps_estimate"), 0.0)

        watchlist_only_window = safe_bool(packet.get("watchlist_only_window"), False)
        window_shadow_execution_blocked = safe_bool(packet.get("window_shadow_execution_blocked"), False)
        shadow_execution_blocked = safe_bool(packet.get("shadow_execution_blocked"), False)

        # Determine bucket
        bucket = self._bucket_for_window(window_name)

        # Expected slippage model (bps)
        expected_slippage_bps = self._estimate_slippage_bps(
            bucket=bucket,
            runtime_slippage_bps=runtime_slippage_bps,
            spread_bps=spread_bps,
            rvol=rvol,
            instrument_types=instrument_types,
            direction=direction,
            major_release_day=major_release_day,
            whipsaw_detected_after_open=whipsaw_detected_after_open,
        )

        # Fill probabilities
        fill_feasibility = self._estimate_fill_feasibility(
            confidence=confidence,
            expected_slippage_bps=expected_slippage_bps,
            spread_bps=spread_bps,
            quote_age_ms=quote_age_ms,
            halted=halted,
            luld_halt_detected=luld_halt_detected,
            broker_status_degraded=broker_status_degraded,
            watchlist_only_window=watchlist_only_window,
            window_shadow_execution_blocked=window_shadow_execution_blocked,
            shadow_execution_blocked=shadow_execution_blocked,
            bucket=bucket,
        )

        partial_fill_probability = self._estimate_partial_fill_probability(
            expected_slippage_bps=expected_slippage_bps,
            instrument_types=instrument_types,
            size_mult=size_mult,
            bucket=bucket,
            spread_bps=spread_bps,
        )

        fill_completion_probability = self._estimate_fill_completion_probability(
            fill_feasibility=fill_feasibility,
            partial_fill_probability=partial_fill_probability,
            instrument_types=instrument_types,
            bucket=bucket,
            major_release_day=major_release_day,
        )

        reject_risk_probability, reject_risk_flags = self._estimate_reject_risk(
            candidate=candidate,
            packet=packet,
            instrument_types=instrument_types,
            direction=direction,
            halted=halted,
            luld_halt_detected=luld_halt_detected,
            broker_status_degraded=broker_status_degraded,
            quote_age_ms=quote_age_ms,
            runtime_slippage_bps=runtime_slippage_bps,
            execution_constraints=execution_constraints,
        )

        do_not_route, do_not_route_reasons = self._do_not_route_decision(
            candidate=candidate,
            packet=packet,
            expected_slippage_bps=expected_slippage_bps,
            fill_feasibility=fill_feasibility,
            reject_risk_probability=reject_risk_probability,
            halted=halted,
            luld_halt_detected=luld_halt_detected,
            bucket=bucket,
            window_name=window_name,
        )

        # Expected fill price band representation (shadow heuristic)
        fill_price_band = self._expected_fill_price_band(direction, expected_slippage_bps)

        quality_class = self._execution_quality_class(
            expected_slippage_bps=expected_slippage_bps,
            fill_feasibility=fill_feasibility,
            reject_risk_probability=reject_risk_probability,
            do_not_route=do_not_route,
        )

        return {
            "schema_version": "fill_sim_assessment.v1",
            "timestamp_utc": iso_now(),
            "symbol": candidate.get("symbol"),
            "window_name": window_name,
            "bucket": bucket,

            "expected_slippage_bps": round(expected_slippage_bps, 2),
            "fill_feasibility_score": round(fill_feasibility, 4),
            "partial_fill_probability": round(partial_fill_probability, 4),
            "fill_completion_probability": round(fill_completion_probability, 4),
            "reject_risk_probability": round(reject_risk_probability, 4),

            "fill_price_band": fill_price_band,
            "execution_quality_class": quality_class,

            "do_not_route_even_in_shadow": do_not_route,
            "do_not_route_reasons": do_not_route_reasons,
            "reject_risk_flags": reject_risk_flags,

            "inputs_used": {
                "runtime_slippage_bps_estimate": runtime_slippage_bps,
                "spread_bps": spread_bps,
                "rvol": rvol,
                "quote_age_ms": quote_age_ms,
                "halted": halted,
                "luld_halt_detected": luld_halt_detected,
                "major_release_day": major_release_day,
                "whipsaw_detected_after_open": whipsaw_detected_after_open,
                "broker_status_degraded": broker_status_degraded,
                "watchlist_only_window": watchlist_only_window,
                "window_shadow_execution_blocked": window_shadow_execution_blocked,
                "shadow_execution_blocked": shadow_execution_blocked,
            }
        }

    # -------------------------
    # Estimation methods
    # -------------------------
    def _bucket_for_window(self, window_name: str) -> str:
        wn = (window_name or "").lower()
        if "lunch" in wn:
            return "lunch_lull"
        if "power_hour" in wn:
            return "power_hour"
        if "opening" in wn or "orb" in wn or "amateur_hour" in wn:
            return "opening_windows"
        if "close_exhaustion" in wn:
            return "close_exhaustion_watch"
        return "other"

    def _estimate_slippage_bps(
        self,
        bucket: str,
        runtime_slippage_bps: float,
        spread_bps: float,
        rvol: float,
        instrument_types: List[str],
        direction: str,
        major_release_day: bool,
        whipsaw_detected_after_open: bool,
    ) -> float:
        # Baseline by bucket
        if bucket == "opening_windows":
            s = self.cfg.slippage_bps_baseline_opening
        elif bucket == "lunch_lull":
            s = self.cfg.slippage_bps_baseline_lunch
        elif bucket == "power_hour":
            s = self.cfg.slippage_bps_baseline_power_hour
        elif bucket == "close_exhaustion_watch":
            s = self.cfg.slippage_bps_baseline_power_hour
        else:
            s = self.cfg.slippage_bps_baseline_other

        # Blend runtime estimate if present (trust runtime but smooth it)
        if runtime_slippage_bps > 0:
            s = 0.55 * s + 0.45 * runtime_slippage_bps

        # Spread contribution if present
        if spread_bps > 0:
            s += min(spread_bps * 0.35, 10.0)

        # Low RVOL can worsen actual fills (especially intraday)
        if rvol > 0 and rvol < 1.0:
            s += (1.0 - rvol) * 6.0

        # Options penalty
        if any("option" in t for t in instrument_types):
            s *= self.cfg.options_slippage_multiplier

        # Short-side penalty
        if "short" in direction:
            s *= self.cfg.short_slippage_multiplier

        # Macro/time-window stress multipliers
        if major_release_day:
            s *= self.cfg.major_release_day_slippage_multiplier
        if whipsaw_detected_after_open and bucket == "opening_windows":
            s *= self.cfg.opening_whipsaw_slippage_multiplier

        return max(0.0, s)

    def _estimate_fill_feasibility(
        self,
        confidence: float,
        expected_slippage_bps: float,
        spread_bps: float,
        quote_age_ms: float,
        halted: bool,
        luld_halt_detected: bool,
        broker_status_degraded: bool,
        watchlist_only_window: bool,
        window_shadow_execution_blocked: bool,
        shadow_execution_blocked: bool,
        bucket: str,
    ) -> float:
        f = self.cfg.base_fill_feasibility

        f += (confidence - 0.5) * 0.18
        f -= min(expected_slippage_bps / 100.0, 0.35)
        if spread_bps > 0:
            f -= min(spread_bps / 200.0, 0.20)
        if quote_age_ms > 1500:
            f -= 0.08
        if quote_age_ms > 5000:
            f -= 0.12

        if halted or luld_halt_detected:
            f -= 0.70
        if broker_status_degraded:
            f -= 0.25

        if bucket == "lunch_lull":
            f -= 0.06
        if bucket == "close_exhaustion_watch":
            f -= 0.15

        # Shadow blocks reduce feasibility to near-zero because this should not be considered routable
        if watchlist_only_window:
            f -= 0.45
        if window_shadow_execution_blocked:
            f -= 0.55
        if shadow_execution_blocked:
            f -= 0.65

        return clamp(f)

    def _estimate_partial_fill_probability(
        self,
        expected_slippage_bps: float,
        instrument_types: List[str],
        size_mult: float,
        bucket: str,
        spread_bps: float,
    ) -> float:
        p = self.cfg.base_partial_fill_probability

        p += min(expected_slippage_bps / 120.0, 0.30)
        p += min(max(size_mult - 0.5, 0.0) * 0.15, 0.15)

        if spread_bps > 0:
            p += min(spread_bps / 250.0, 0.15)

        if any("option" in t for t in instrument_types):
            p += self.cfg.options_partial_fill_penalty

        if bucket == "opening_windows":
            p += 0.08
        elif bucket == "power_hour":
            p += 0.10
        elif bucket == "lunch_lull":
            p += 0.05

        return clamp(p)

    def _estimate_fill_completion_probability(
        self,
        fill_feasibility: float,
        partial_fill_probability: float,
        instrument_types: List[str],
        bucket: str,
        major_release_day: bool,
    ) -> float:
        p = self.cfg.base_fill_completion_probability

        p = 0.45 * p + 0.55 * fill_feasibility
        p -= partial_fill_probability * 0.35

        if any("option" in t for t in instrument_types):
            p -= self.cfg.options_completion_penalty

        if bucket == "lunch_lull":
            p *= self.cfg.lunch_lull_fill_completion_multiplier
        if major_release_day and bucket == "opening_windows":
            p -= 0.08

        return clamp(p)

    def _estimate_reject_risk(
        self,
        candidate: Dict[str, Any],
        packet: Dict[str, Any],
        instrument_types: List[str],
        direction: str,
        halted: bool,
        luld_halt_detected: bool,
        broker_status_degraded: bool,
        quote_age_ms: float,
        runtime_slippage_bps: float,
        execution_constraints: Dict[str, Any],
    ) -> Tuple[float, list]:
        p = self.cfg.base_reject_risk_probability
        flags = []

        if halted or luld_halt_detected:
            p += 0.55
            flags.append("halt_or_luld")

        if broker_status_degraded:
            p += 0.25
            flags.append("broker_status_degraded")

        if quote_age_ms > 5000:
            p += 0.08
            flags.append("stale_quote_age")

        if runtime_slippage_bps > self.cfg.max_runtime_estimated_slippage_bps_allowed:
            p += 0.10
            flags.append("runtime_slippage_exceeds_allowed")

        if "short" in direction:
            p += self.cfg.short_reject_risk_add
            flags.append("short_side_friction")

        if any("option" in t for t in instrument_types):
            # Permissions / contract validation risk proxy
            p += 0.06
            flags.append("options_permissions_or_contract_validation")

        # If candidate already blocked by package-level reasons, reject risk is effectively high for routing purposes
        block_reasons = candidate.get("block_reasons") or []
        if block_reasons:
            p += 0.15
            flags.append("candidate_has_block_reasons")

        return clamp(p), flags

    def _do_not_route_decision(
        self,
        candidate: Dict[str, Any],
        packet: Dict[str, Any],
        expected_slippage_bps: float,
        fill_feasibility: float,
        reject_risk_probability: float,
        halted: bool,
        luld_halt_detected: bool,
        bucket: str,
        window_name: str,
    ) -> Tuple[bool, list]:
        reasons = []

        if halted or luld_halt_detected:
            reasons.append("halt_or_luld")

        if expected_slippage_bps >= self.cfg.hard_block_slippage_bps:
            reasons.append("hard_block_slippage_threshold")

        if fill_feasibility < 0.20:
            reasons.append("fill_feasibility_too_low")

        if reject_risk_probability > 0.50:
            reasons.append("reject_risk_too_high")

        if bucket == "close_exhaustion_watch" and self.cfg.close_exhaustion_watch_fill_block:
            reasons.append("close_exhaustion_watch_no_new_intraday_risk")

        # honor upstream window/package blocks
        if safe_bool(packet.get("watchlist_only_window"), False):
            reasons.append("watchlist_only_window")
        if safe_bool(packet.get("window_shadow_execution_blocked"), False):
            reasons.append("window_shadow_execution_blocked")
        if safe_bool(packet.get("shadow_execution_blocked"), False):
            reasons.append("shadow_execution_blocked")

        # candidate-level blocks
        if candidate.get("block_reasons"):
            reasons.append("candidate_blocked_upstream")

        return (len(reasons) > 0), list(dict.fromkeys(reasons))

    def _expected_fill_price_band(self, direction: str, expected_slippage_bps: float) -> Dict[str, Any]:
        """
        Price-band is expressed in bps from decision reference, since we may not have a live reference price here.
        """
        if "long" in direction:
            return {
                "reference": "decision_price",
                "expected_fill_band_bps_from_reference": [0, round(expected_slippage_bps, 2)],
                "note": "Longs likely fill at/above decision reference in volatile conditions"
            }
        elif "short" in direction or "bearish" in direction:
            return {
                "reference": "decision_price",
                "expected_fill_band_bps_from_reference": [0, round(expected_slippage_bps, 2)],
                "note": "Shorts/puts may incur adverse entry slippage vs signal snapshot"
            }
        else:
            return {
                "reference": "decision_price",
                "expected_fill_band_bps_from_reference": [0, round(expected_slippage_bps, 2)],
                "note": "Watchlist/non-routing candidate"
            }

    def _execution_quality_class(
        self,
        expected_slippage_bps: float,
        fill_feasibility: float,
        reject_risk_probability: float,
        do_not_route: bool,
    ) -> str:
        if do_not_route:
            return "blocked"
        if fill_feasibility >= 0.75 and expected_slippage_bps <= 15 and reject_risk_probability <= 0.10:
            return "good"
        if fill_feasibility >= 0.50 and expected_slippage_bps <= 25 and reject_risk_probability <= 0.25:
            return "acceptable_shadow_only"
        return "poor"


# -----------------------------
# CLI smoke test
# -----------------------------
def parse_args():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-json", default=None)
    p.add_argument("--packet-json", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    sim = FillSimulator()

    if args.candidate_json:
        candidate = json.loads(Path(args.candidate_json).read_text(encoding="utf-8"))
    else:
        candidate = {
            "symbol": "XOM",
            "window_name": "power_hour",
            "direction": "long",
            "instrument_types": ["equity"],
            "confidence_score": 0.78,
            "size_multiplier_suggestion": 0.95,
            "block_reasons": [],
            "execution_constraints": {"limit_only": True}
        }

    if args.packet_json:
        packet = json.loads(Path(args.packet_json).read_text(encoding="utf-8"))
    else:
        packet = {
            "time_window_name": "power_hour",
            "watchlist_only_window": False,
            "window_shadow_execution_blocked": False,
            "shadow_execution_blocked": False,
            "runtime_flags": {
                "major_release_day": False,
                "whipsaw_detected_after_open": False,
                "slippage_bps_estimate": 14,
                "broker_status_degraded": False,
                "luld_halt_detected": False
            }
        }

    quote_snapshot = {
        "spread_bps": 7.5,
        "rvol": 1.8,
        "quote_age_ms": 450,
        "halted": False
    }

    out = sim.assess_candidate(candidate, packet, quote_snapshot=quote_snapshot)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

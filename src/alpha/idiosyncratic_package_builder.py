#!/usr/bin/env python3
"""
Global Sentinel V4.2 - Idiosyncratic Intraday Package Builder (Shadow Mode Only)

Purpose:
- Generate a diversified intraday opportunity package (long/short/puts/calls/spreads)
- Bias toward idiosyncratic names (low ETF overlap / underweighted in thematic baskets)
- Use real-time regime score + catalysts + AI infra/geopolitical signals
- Never place live orders; outputs shadow recommendations only

This is a framework/stub:
- Replace mock inputs with your real MCP feeds / market snapshots / options chains / ETF holdings data.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml
except ImportError:
    raise SystemExit("Please install pyyaml")

try:
    from src.alpha.time_window_policy import TimeWindowPolicyEngine
except Exception:
    try:
        # Fallback: direct relative import when running as script
        import importlib.util as _ilu
        _twp_path = Path(__file__).resolve().parent / "time_window_policy.py"
        if _twp_path.exists():
            _spec = _ilu.spec_from_file_location("time_window_policy", _twp_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            TimeWindowPolicyEngine = _mod.TimeWindowPolicyEngine
        else:
            TimeWindowPolicyEngine = None  # type: ignore[assignment,misc]
    except Exception:
        TimeWindowPolicyEngine = None  # type: ignore[assignment,misc]


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class IdiosyncraticPackageBuilder:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "idiosyncratic_package.yaml")
        self.out_dir = repo_root / "reports" / "packages"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # --- Time window policy (additive timing layer) ---
        self.time_policy = None
        try:
            if TimeWindowPolicyEngine is not None:
                self.time_policy = TimeWindowPolicyEngine(repo_root=self.repo_root)
        except Exception:
            self.time_policy = None

    def load_latest_regime_packet(self) -> Dict[str, Any]:
        score_dir = self.repo_root / "logs" / "scorecards"
        if not score_dir.exists():
            return {}
        files = sorted(score_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return read_json(files[0], {}) if files else {}

    def load_control_flags(self) -> Dict[str, bool]:
        manual = read_json(self.repo_root / "control" / "manual_veto.json", {"manual_veto": False})
        kill = read_json(self.repo_root / "control" / "kill_switch.json", {"kill_switch": False})
        return {
            "manual_veto": bool(manual.get("manual_veto", False)),
            "kill_switch": bool(kill.get("kill_switch", False))
        }

    def get_candidate_universe(self) -> List[Dict[str, Any]]:
        """
        Replace this with:
        - ETF holdings overlap stats
        - liquidity
        - options chain quality
        - real-time catalyst tags
        - MCP-sourced signals (news, price, options, infra, geopolitics)
        """
        return [
            {
                "symbol": "DY", "name": "Dycom Industries", "sector": "Industrials",
                "themes": ["fiber_buildout", "engineering_construction_datacenter"],
                "etf_overlap_score": 0.22, "thematic_ai_etf_overlap_score": 0.10,
                "liquidity_quality": 0.78, "ai_infra_relevance": 0.88,
                "geopolitical_relevance": 0.32, "idiosyncratic_catalyst": 0.74,
                "real_time_dislocation": 0.63, "crowding_inverse": 0.72,
                "risk_asymmetry": 0.58, "options_liquidity": 0.55,
                "catalyst_tags": ["hyperscaler_capex_signal", "fiber_buildout"]
            },
            {
                "symbol": "ETN", "name": "Eaton", "sector": "Industrials",
                "themes": ["data_center_power_distribution", "switchgear"],
                "etf_overlap_score": 0.55, "thematic_ai_etf_overlap_score": 0.42,
                "liquidity_quality": 0.95, "ai_infra_relevance": 0.92,
                "geopolitical_relevance": 0.40, "idiosyncratic_catalyst": 0.68,
                "real_time_dislocation": 0.52, "crowding_inverse": 0.30,
                "risk_asymmetry": 0.60, "options_liquidity": 0.85,
                "catalyst_tags": ["data_center_capacity_signal", "power_constraint_signal"]
            },
            {
                "symbol": "LITE", "name": "Lumentum", "sector": "Technology",
                "themes": ["optical_interconnects", "networking_components"],
                "etf_overlap_score": 0.31, "thematic_ai_etf_overlap_score": 0.21,
                "liquidity_quality": 0.73, "ai_infra_relevance": 0.83,
                "geopolitical_relevance": 0.29, "idiosyncratic_catalyst": 0.66,
                "real_time_dislocation": 0.71, "crowding_inverse": 0.64,
                "risk_asymmetry": 0.67, "options_liquidity": 0.61,
                "catalyst_tags": ["optical_interconnects", "guidance_revision"]
            },
            {
                "symbol": "PWR", "name": "Quanta Services", "sector": "Industrials",
                "themes": ["grid_infrastructure", "power_distribution"],
                "etf_overlap_score": 0.36, "thematic_ai_etf_overlap_score": 0.18,
                "liquidity_quality": 0.86, "ai_infra_relevance": 0.84,
                "geopolitical_relevance": 0.48, "idiosyncratic_catalyst": 0.61,
                "real_time_dislocation": 0.57, "crowding_inverse": 0.58,
                "risk_asymmetry": 0.54, "options_liquidity": 0.70,
                "catalyst_tags": ["power_constraint_signal", "grid_infrastructure"]
            },
            {
                "symbol": "CARR", "name": "Carrier Global", "sector": "Industrials",
                "themes": ["thermal_management_cooling", "hvac_datacenter"],
                "etf_overlap_score": 0.43, "thematic_ai_etf_overlap_score": 0.25,
                "liquidity_quality": 0.88, "ai_infra_relevance": 0.79,
                "geopolitical_relevance": 0.37, "idiosyncratic_catalyst": 0.59,
                "real_time_dislocation": 0.49, "crowding_inverse": 0.51,
                "risk_asymmetry": 0.50, "options_liquidity": 0.72,
                "catalyst_tags": ["thermal_management_cooling"]
            },
            {
                "symbol": "AMKR", "name": "Amkor Technology", "sector": "Technology",
                "themes": ["semiconductor_packaging_test"],
                "etf_overlap_score": 0.29, "thematic_ai_etf_overlap_score": 0.16,
                "liquidity_quality": 0.76, "ai_infra_relevance": 0.72,
                "geopolitical_relevance": 0.55, "idiosyncratic_catalyst": 0.64,
                "real_time_dislocation": 0.62, "crowding_inverse": 0.69,
                "risk_asymmetry": 0.63, "options_liquidity": 0.58,
                "catalyst_tags": ["supply_chain_disruption", "semiconductor_packaging_test"]
            },
            {
                "symbol": "DLR", "name": "Digital Realty", "sector": "Real Estate",
                "themes": ["data_center_reit", "capacity_constraints"],
                "etf_overlap_score": 0.61, "thematic_ai_etf_overlap_score": 0.47,
                "liquidity_quality": 0.84, "ai_infra_relevance": 0.80,
                "geopolitical_relevance": 0.35, "idiosyncratic_catalyst": 0.56,
                "real_time_dislocation": 0.45, "crowding_inverse": 0.34,
                "risk_asymmetry": 0.44, "options_liquidity": 0.77,
                "catalyst_tags": ["data_center_capacity_signal"]
            },
            {
                "symbol": "KEYS", "name": "Keysight Technologies", "sector": "Technology",
                "themes": ["test_measurement", "network_validation"],
                "etf_overlap_score": 0.35, "thematic_ai_etf_overlap_score": 0.19,
                "liquidity_quality": 0.82, "ai_infra_relevance": 0.69,
                "geopolitical_relevance": 0.42, "idiosyncratic_catalyst": 0.60,
                "real_time_dislocation": 0.58, "crowding_inverse": 0.62,
                "risk_asymmetry": 0.57, "options_liquidity": 0.66,
                "catalyst_tags": ["networking_components", "guidance_revision"]
            }
        ]

    def score_candidate(self, c: Dict[str, Any], regime: Dict[str, Any]) -> Dict[str, Any]:
        weights = self.cfg["package_builder"]["signals"]["weights"]
        base = (
            weights["geopolitical_relevance"] * c.get("geopolitical_relevance", 0.0)
            + weights["ai_infra_relevance"] * c.get("ai_infra_relevance", 0.0)
            + weights["idiosyncratic_catalyst"] * c.get("idiosyncratic_catalyst", 0.0)
            + weights["real_time_dislocation"] * c.get("real_time_dislocation", 0.0)
            + weights["crowding_inverse"] * c.get("crowding_inverse", 0.0)
            + weights["liquidity_quality"] * c.get("liquidity_quality", 0.0)
            + weights["risk_asymmetry"] * c.get("risk_asymmetry", 0.0)
        )

        regime_p = float(regime.get("regime_shift_probability", 0.0) or 0.0)
        crisis_tilt = 0.0
        if regime_p >= 0.75:
            if any(t in c.get("themes", []) for t in ["grid_infrastructure", "power_distribution", "data_center_power_distribution"]):
                crisis_tilt += 0.04
            if c.get("geopolitical_relevance", 0.0) > 0.45:
                crisis_tilt += 0.03

        overlap_penalty = 0.15 * max(c.get("etf_overlap_score", 0.0) - 0.35, 0.0)
        thematic_overlap_penalty = 0.10 * max(c.get("thematic_ai_etf_overlap_score", 0.0) - 0.25, 0.0)

        score = clamp(base + crisis_tilt - overlap_penalty - thematic_overlap_penalty)
        c2 = dict(c)
        c2["package_score"] = round(score, 4)
        c2["score_components"] = {
            "base_score": round(base, 4),
            "crisis_tilt": round(crisis_tilt, 4),
            "overlap_penalty": round(overlap_penalty + thematic_overlap_penalty, 4)
        }
        return c2

    def choose_instrument(self, c: Dict[str, Any], regime: Dict[str, Any]) -> Tuple[str, str]:
        regime_p = float(regime.get("regime_shift_probability", 0.0) or 0.0)
        options_ok = c.get("options_liquidity", 0.0) >= 0.6
        score = c.get("package_score", 0.0)

        bullish = c.get("ai_infra_relevance", 0.0) + c.get("idiosyncratic_catalyst", 0.0) >= 1.35
        if regime_p >= 0.75 and c.get("geopolitical_relevance", 0.0) < 0.3 and c.get("real_time_dislocation", 0.0) > 0.7:
            bullish = False

        if bullish:
            if options_ok and c.get("risk_asymmetry", 0.0) > 0.6:
                return "long_call" if score > 0.72 else "call_spread", "bullish idiosyncratic catalyst + AI infra relevance"
            return "long_equity", "bullish idiosyncratic catalyst + liquidity"
        else:
            if options_ok:
                return "long_put" if score > 0.70 else "put_spread", "defensive/downside setup in risk-off or dislocation context"
            return "short_equity", "bearish/dislocation setup with limited options liquidity"

    def _strategy_type_for_instrument(self, instrument: str) -> str:
        mapping = {
            "long_equity": "orb_breakout_long",
            "short_equity": "orb_breakdown_short",
            "long_put": "gap_and_crap_puts",
            "long_call": "orb_breakout_long",
            "put_spread": "short_mean_reversion",
            "call_spread": "orb_breakout_long",
        }
        return mapping.get(instrument, "orb_breakout_long")

    def build_package(self) -> Dict[str, Any]:
        regime = self.load_latest_regime_packet()
        flags = self.load_control_flags()

        fallback_mode = bool(regime.get("fallback_mode_status", False))
        confidence = float(regime.get("confidence", 0.85) or 0.85)
        quorum_ok = bool(regime.get("data_freshness_status", {}).get("quorum_pass", True)) if isinstance(regime.get("data_freshness_status"), dict) else True

        shadow_blocked = flags["manual_veto"] or flags["kill_switch"] or fallback_mode or (not quorum_ok)

        # --- Time window classification ---
        timestamp_utc = iso_now()
        window_state = None
        window_guardrail_blocks: List[str] = []
        if self.time_policy is not None:
            try:
                window_state = self.time_policy.classify(
                    timestamp_utc=timestamp_utc,
                    controls={
                        "manual_veto": bool(flags.get("manual_veto", False)),
                        "kill_switch": bool(flags.get("kill_switch", False)),
                    },
                    data_quality={
                        "quorum_pass": quorum_ok,
                        "fallback_mode": fallback_mode,
                        "luld_halt_detected": False,
                    },
                    runtime_flags={
                        "major_release_day": False,
                    },
                )
            except Exception:
                window_state = None

        # Apply window-level confidence multiplier
        window_confidence_mult = 1.0
        window_size_mult = 1.0
        shadow_execution_window_blocked = False
        strategy_eligibility: Dict[str, Any] = {}
        if window_state is not None:
            window_confidence_mult = float(window_state.get("confidence_multiplier", 1.0))
            window_size_mult = float(window_state.get("size_multiplier", 1.0))
            strategy_eligibility = window_state.get("strategy_eligibility", {})
            # Merge engine-reported guardrail blocks
            for blk in window_state.get("window_guardrail_blocks", []):
                if blk not in window_guardrail_blocks:
                    window_guardrail_blocks.append(blk)
            if window_state.get("shadow_execution_window_blocked", False):
                shadow_execution_window_blocked = True
                shadow_blocked = True
                if "window_policy_block" not in window_guardrail_blocks:
                    window_guardrail_blocks.append("window_policy_block")
            if window_size_mult == 0.0:
                if "size_multiplier_zero_no_new_positions" not in window_guardrail_blocks:
                    window_guardrail_blocks.append("size_multiplier_zero_no_new_positions")
            if window_state.get("event_risk_window_active", False):
                if "event_risk_window_active" not in window_guardrail_blocks:
                    window_guardrail_blocks.append("event_risk_window_active")

        candidates = self.get_candidate_universe()
        cfg = self.cfg["package_builder"]

        filt = []
        for c in candidates:
            if c.get("etf_overlap_score", 1.0) > cfg["universe"]["max_etf_overlap_score"]:
                continue
            if c.get("thematic_ai_etf_overlap_score", 1.0) > cfg["universe"]["max_thematic_ai_etf_overlap_score"]:
                continue
            if c.get("liquidity_quality", 0.0) < cfg["universe"]["min_intraday_liquidity_score"]:
                continue
            filt.append(c)

        scored = [self.score_candidate(c, regime) for c in filt]
        scored.sort(key=lambda x: x["package_score"], reverse=True)

        positions = []
        sector_weights: Dict[str, float] = {}
        themes = set()
        target_positions = cfg["diversification"]["target_positions"]
        max_positions = cfg["diversification"]["max_positions"]
        max_sector = cfg["diversification"]["max_sector_weight_pct"] / 100.0
        max_single = cfg["diversification"]["max_single_name_weight_pct"] / 100.0

        # Pre-compute window-aware parameters for candidate loop
        current_window_name = window_state.get("current_window", "") if window_state else ""
        preferred_setups = window_state.get("preferred_setups", []) if window_state else []
        window_min_rvol = None
        if window_state and isinstance(window_state.get("thresholds"), dict):
            window_min_rvol = window_state["thresholds"].get("min_rvol")

        for c in scored:
            if len(positions) >= max_positions:
                break

            # Block new positions entirely if size_multiplier is 0
            if window_state is not None and window_size_mult == 0.0:
                break

            instrument, rationale_dir = self.choose_instrument(c, regime)
            raw_w = 0.10 + 0.12 * (c["package_score"] - 0.5)
            w = clamp(raw_w, 0.04, max_single)

            # Apply window size multiplier to position weight
            if window_state is not None and window_size_mult != 1.0:
                w = w * window_size_mult

            sector = c["sector"]
            if sector_weights.get(sector, 0.0) + w > max_sector:
                continue

            # --- Window-aware tagging (shadow-mode permissive: tag, don't hard-block) ---
            position_tags: List[str] = []

            # Strategy eligibility from TimeWindowPolicyEngine
            strategy_type = self._strategy_type_for_instrument(instrument)
            strat_elig = strategy_eligibility.get(strategy_type, {})
            if not strat_elig.get("eligible", True):
                position_tags.append(f"strategy_blocked:{','.join(strat_elig.get('reasons_blocked', []))}")

            # Strategy eligibility: tag preferred_setup mismatches
            if preferred_setups and c.get("setup_type"):
                if c["setup_type"] not in preferred_setups:
                    position_tags.append(f"setup_mismatch:{c['setup_type']}_not_in_window_preferred")

            # Lunch lull restrictions
            if current_window_name == "lunch_lull":
                has_exceptional_catalyst = bool(c.get("idiosyncratic_catalyst", 0.0) >= 0.80)
                if not has_exceptional_catalyst:
                    position_tags.append("watchlist_only")
                if window_min_rvol is not None:
                    position_tags.append(f"min_rvol_required:{window_min_rvol}")

            # Event risk mode tagging
            if window_state and window_state.get("event_risk_window_active", False):
                position_tags.append("event_risk_active")

            position = {
                "symbol": c["symbol"], "name": c["name"],
                "instrument": instrument,
                "weight_pct_gross": round(w * 100.0, 2),
                "package_score": c["package_score"],
                "sector": sector, "themes": c.get("themes", []),
                "catalyst_tags": c.get("catalyst_tags", []),
                "etf_overlap_score": c.get("etf_overlap_score"),
                "thematic_ai_etf_overlap_score": c.get("thematic_ai_etf_overlap_score"),
                "rationale": [
                    rationale_dir,
                    f"AI infra relevance={c.get('ai_infra_relevance')}",
                    f"Real-time dislocation={c.get('real_time_dislocation')}",
                    f"Crowding inverse={c.get('crowding_inverse')}",
                    f"ETF overlap penalty applied={c['score_components']['overlap_penalty']}"
                ],
                "score_components": c["score_components"],
                "window_tags": position_tags,
            }
            positions.append(position)
            sector_weights[sector] = sector_weights.get(sector, 0.0) + w
            for t in c.get("themes", []):
                themes.add(t)
            if len(positions) >= target_positions and len(themes) >= cfg["diversification"]["min_unique_themes"]:
                break

        # --- Watchlist-only logic for specific windows ---
        current_window = window_state.get("current_window", "unknown") if window_state else "unknown"
        if current_window == "close_exhaustion_watch":
            # No new intraday risk, watchlist only
            for p in positions:
                p["watchlist_only"] = True
                p.setdefault("window_tags", []).append("close_exhaustion_no_new_risk")
        elif current_window == "lunch_lull":
            # Tag lunch_lull unless there's an exceptional catalyst
            for p in positions:
                has_exceptional = any(
                    ct in ("earnings_surprise", "fda_approval", "activist_stake")
                    for ct in p.get("catalyst_tags", [])
                )
                if not has_exceptional:
                    p.setdefault("window_tags", []).append("lunch_lull_reduced_conviction")

        if cfg["diversification"]["require_at_least_one_hedge"]:
            has_hedge = any(p["instrument"] in {"long_put", "put_spread", "short_equity"} for p in positions)
            if not has_hedge and positions:
                worst = positions[-1]
                worst["instrument"] = "put_spread"
                worst["rationale"].append("forced hedge slot for package diversification/risk control")

        total_w = sum(p["weight_pct_gross"] for p in positions) or 1.0
        for p in positions:
            p["weight_pct_gross"] = round((p["weight_pct_gross"] / total_w) * 100.0, 2)

        confidence_adj = confidence
        if fallback_mode:
            confidence_adj -= cfg["intraday_logic"]["confidence_penalty_if_fallback_mode"]
        if isinstance(regime.get("data_freshness_status"), dict) and regime["data_freshness_status"].get("conflicting_signals"):
            confidence_adj -= cfg["intraday_logic"]["confidence_penalty_if_conflicting_signals"]
        # Apply time window confidence multiplier
        confidence_adj *= window_confidence_mult
        confidence_adj = round(clamp(confidence_adj), 4)

        package = {
            "timestamp_utc": timestamp_utc,
            "engine": "idiosyncratic_intraday_package_builder_v4_2",
            "shadow_only": True, "no_live_orders": True,
            "regime_context": {
                "mode": regime.get("mode"),
                "effective_mode": regime.get("effective_mode", regime.get("mode")),
                "regime_shift_probability": regime.get("regime_shift_probability"),
                "confidence": confidence, "adjusted_package_confidence": confidence_adj
            },
            "control_flags": flags,
            "data_quality": {"fallback_mode_status": fallback_mode, "quorum_ok": quorum_ok},
            "shadow_execution_blocked": shadow_blocked,
            "time_window_state": {
                "current_window": window_state.get("current_window", "unknown") if window_state else "unknown",
                "window_priority": window_state.get("window_priority", "unknown") if window_state else "unknown",
                "confidence_multiplier": window_state.get("confidence_multiplier", 1.0) if window_state else 1.0,
                "size_multiplier": window_state.get("size_multiplier", 1.0) if window_state else 1.0,
                "event_risk_active": window_state.get("event_risk_window_active", False) if window_state else False,
                "event_risk_mode": window_state.get("event_risk_mode") if window_state else None,
                "global_overlap": window_state.get("global_overlap_active") if window_state else None,
                "overlap_states": window_state.get("overlap_states") if window_state else None,
                "shadow_window_blocked": window_state.get("shadow_execution_window_blocked", False) if window_state else False,
                "preferred_setups": window_state.get("preferred_setups", []) if window_state else [],
                "restrictions": window_state.get("restrictions") if window_state else None,
                "thresholds": window_state.get("thresholds") if window_state else None,
            },
            "window_policy_applied": True if window_state else False,
            "window_risk_budget": window_state.get("risk_budget") if window_state else None,
            "window_guardrail_blocks": window_guardrail_blocks,
            "window_strategy_eligibility": strategy_eligibility if strategy_eligibility else None,
            "package_summary": {
                "position_count": len(positions),
                "unique_themes": sorted(list(themes)),
                "sector_weights_pct": {k: round(v * 100.0, 2) for k, v in sector_weights.items()},
                "underweighted_etf_bias": True,
                "notes": [
                    "This package is for shadow mode / research only.",
                    "Focus is idiosyncratic names with lower ETF overlap rather than broad ETF beta.",
                    "Instrument selection is a stub and must be validated against real options chain + borrow + liquidity data."
                ]
            },
            "position_candidates": positions,
            "risk_notes": [
                "No live execution; paper/sandbox only.",
                "Require risk gate + human approval before any shadow order routing export.",
                "Avoid concentrated exposure to a single subtheme even when scores cluster (e.g., power/cooling)."
            ]
        }
        return package

    def write_outputs(self, package: Dict[str, Any]) -> None:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out_json = self.out_dir / f"intraday_package_{ts}.json"
        out_json.write_text(json.dumps(package, indent=2), encoding="utf-8")

        tw = package.get("time_window_state", {})
        md = [
            "# Global Sentinel Intraday Package (Shadow Mode)",
            f"- Timestamp (UTC): {package['timestamp_utc']}",
            f"- Regime mode: {package['regime_context'].get('mode')}",
            f"- Effective mode: {package['regime_context'].get('effective_mode')}",
            f"- Regime shift probability: {package['regime_context'].get('regime_shift_probability')}",
            f"- Package confidence: {package['regime_context'].get('adjusted_package_confidence')}",
            f"- Shadow execution blocked: {package['shadow_execution_blocked']}",
            "",
            "## Time Window",
            f"- Current window: {tw.get('current_window', 'unknown')}",
            f"- Window priority: {tw.get('window_priority', 'unknown')}",
            f"- Confidence multiplier: {tw.get('confidence_multiplier', 1.0)}",
            f"- Size multiplier: {tw.get('size_multiplier', 1.0)}",
            f"- Event risk active: {tw.get('event_risk_active', False)}",
            f"- Global overlap: {tw.get('global_overlap', 'N/A')}",
            f"- Event risk mode: {tw.get('event_risk_mode', 'N/A')}",
            f"- Overlap states: {tw.get('overlap_states', 'N/A')}",
            f"- Shadow window blocked: {tw.get('shadow_window_blocked', False)}",
            f"- Preferred setups: {', '.join(tw.get('preferred_setups', [])) or 'None'}",
            f"- Window policy applied: {package.get('window_policy_applied', False)}",
            f"- Guardrail blocks: {', '.join(package.get('window_guardrail_blocks', [])) or 'None'}",
            "",
            "## Strategy Eligibility",
        ]
        strat_elig_data = package.get("window_strategy_eligibility") or {}
        if strat_elig_data:
            for strat_name, elig_info in strat_elig_data.items():
                eligible = elig_info.get("eligible", True)
                status = "ELIGIBLE" if eligible else "BLOCKED"
                reasons = ", ".join(elig_info.get("reasons_blocked", [])) if not eligible else "none"
                md.append(f"- **{strat_name}**: {status} (blocked: {reasons})")
        else:
            md.append("- No strategy eligibility data available")
        md += [
            "",
            "## Summary",
            f"- Positions: {package['package_summary']['position_count']}",
            f"- Unique themes: {', '.join(package['package_summary']['unique_themes']) if package['package_summary']['unique_themes'] else 'None'}",
            "",
            "## Candidates"
        ]
        for p in package["position_candidates"]:
            tags_str = f", tags={p['window_tags']}" if p.get("window_tags") else ""
            md.append(f"- **{p['symbol']}** ({p['instrument']}) — score={p['package_score']}, weight={p['weight_pct_gross']}%, themes={', '.join(p['themes'])}{tags_str}")
        (self.out_dir / f"intraday_package_{ts}.md").write_text("\n".join(md), encoding="utf-8")

    def run(self) -> Dict[str, Any]:
        pkg = self.build_package()
        self.write_outputs(pkg)
        return pkg


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    builder = IdiosyncraticPackageBuilder(repo_root)
    pkg = builder.run()
    print(json.dumps({
        "timestamp_utc": pkg["timestamp_utc"],
        "position_count": pkg["package_summary"]["position_count"],
        "shadow_execution_blocked": pkg["shadow_execution_blocked"],
        "package_confidence": pkg["regime_context"]["adjusted_package_confidence"]
    }, indent=2))


if __name__ == "__main__":
    main()

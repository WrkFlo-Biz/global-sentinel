#!/usr/bin/env python3
"""
Global Sentinel V4.5 - Idiosyncratic Package Builder (Shadow Mode)

Purpose:
- Build diversified, time-window-aware, guardrail-aware SHADOW trade packages
- Focus on idiosyncratic/single-name opportunities informed by macro + geopolitics + AI infra themes
- Produce auditable package objects, not orders

Inputs (flexible dicts):
- packet: crisis_monitor packet with macro summaries/router output/time-window state/risk flags
- watchlist config and optional candidate universe
- runtime flags

Outputs:
- package dict with candidate ideas + blocked ideas + execution constraints + reasons
"""

from __future__ import annotations

import hashlib
import json
import uuid
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


def load_yaml(path: Path) -> Dict[str, Any]:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# -----------------------------
# Package Builder
# -----------------------------
class IdiosyncraticPackageBuilder:
    """
    Produces shadow-only candidate packages.
    Does NOT submit orders.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.watchlist_cfg = self._load_watchlist()
        self.execution_reliability_cfg = self._load_execution_reliability_cfg()
        self.candidate_universe = self._build_default_candidate_universe()

        # Fill simulator integration (optional — degrades gracefully)
        try:
            from src.execution.fill_simulator import FillSimulator
        except Exception:
            try:
                from execution.fill_simulator import FillSimulator
            except Exception:
                FillSimulator = None
        self.fill_simulator = FillSimulator() if FillSimulator else None

        self.ticker_theme_map = {
            "LMT": ["defense", "geopolitical_conflict"],
            "RTX": ["defense", "geopolitical_conflict"],
            "NOC": ["defense", "geopolitical_conflict"],
            "XOM": ["energy", "oil_supply"],
            "CVX": ["energy", "oil_supply"],
            "SLB": ["energy_services", "oil_supply"],
            "HAL": ["energy_services", "oil_supply"],
            "CAT": ["industrials", "infrastructure", "margin_squeeze_risk"],
            "DE": ["industrials", "ag", "margin_squeeze_risk"],
            "NVDA": ["ai_infra", "hyperscaler_capex", "liquidity_beta"],
            "SMCI": ["ai_infra", "server_hardware", "data_center_capex"],
            "ANET": ["ai_infra", "networking", "data_center_capex"],
            "VRT": ["ai_infra", "power_cooling", "data_center_capex"],
            "ETN": ["electrical", "power_infra", "data_center_power"],
            "ABB": ["electrical", "power_infra"],
            "AMZN": ["hyperscaler", "cloud", "ai_capex"],
            "MSFT": ["hyperscaler", "cloud", "ai_capex"],
            "GOOGL": ["hyperscaler", "cloud", "ai_capex"],
            "META": ["ai_capex", "ad_beta", "risk_beta"],
            "TSLA": ["high_beta", "consumer_discretionary", "macro_sensitive"],
            "ASML": ["semicap", "ai_infra", "global_supply_chain"],
            "ARM": ["semis", "ai_beta"],
        }

        self.default_strategy_templates = {
            "long_breakout": {"style": "momentum_long", "requires": ["orb_breakout_long"], "direction": "long", "instrument_types": ["equity"]},
            "short_breakdown": {"style": "momentum_short", "requires": ["orb_breakdown_short"], "direction": "short", "instrument_types": ["equity"]},
            "gap_and_crap_puts": {"style": "gap_failure_puts", "requires": ["gap_and_crap_puts"], "direction": "bearish_defined_risk", "instrument_types": ["options_put"]},
            "late_morning_mean_reversion_short": {"style": "mean_reversion_short", "requires": ["short_mean_reversion"], "direction": "short", "instrument_types": ["equity", "options_put"]},
            "power_hour_continuation_long": {"style": "power_hour_momentum_long", "requires": ["orb_breakout_long"], "direction": "long", "instrument_types": ["equity"]},
            "watchlist_next_day_fade_only": {"style": "watchlist_only_next_day_setup", "requires": ["eod_fade_watchlist_only"], "direction": "watchlist_only", "instrument_types": ["none"]},
        }

    def build_package(self, packet: Dict[str, Any], candidate_universe_override: Optional[List[str]] = None) -> Dict[str, Any]:
        now = iso_now()
        time_window_state = packet.get("time_window_state") or {}
        strategy_eligibility = time_window_state.get("strategy_eligibility") or {}
        runtime_flags = packet.get("runtime_flags") or {}
        control_flags = packet.get("control_flags") or {}
        macro_summary = packet.get("macro_policy_summary") or {}
        macro_router_summary = (packet.get("macro_event_router") or {}).get("macro_event_router_summary", {})
        macro_top_events = (packet.get("macro_event_router") or {}).get("macro_events_priority_top", []) or packet.get("macro_events_priority_top", [])

        effective_mode = str(packet.get("effective_mode") or packet.get("mode") or "NORMAL")
        regime_p = safe_float(packet.get("regime_shift_probability"), 0.0)
        base_conf = safe_float(packet.get("confidence") or packet.get("time_window_adjusted_confidence"), 0.0)
        tw_conf_mult = safe_float(packet.get("window_confidence_multiplier"), 1.0)
        tw_size_mult = safe_float(packet.get("window_size_multiplier"), 1.0)
        tw_adj_conf = safe_float(packet.get("time_window_adjusted_confidence"), base_conf * tw_conf_mult)

        combined_shadow_blocked = safe_bool(packet.get("shadow_execution_blocked"), False)
        window_shadow_blocked = safe_bool(packet.get("window_shadow_execution_blocked"), False)
        watchlist_only_window = safe_bool(packet.get("watchlist_only_window"), False)
        policy_data_integrity_degraded = safe_bool(packet.get("policy_data_integrity_degraded"), False)
        slippage_bps_est = safe_float(runtime_flags.get("slippage_bps_estimate"), 0.0)
        luld_halt_detected = safe_bool(runtime_flags.get("luld_halt_detected"), False)
        broker_status_degraded = safe_bool(runtime_flags.get("broker_status_degraded"), False)
        major_release_day = safe_bool(runtime_flags.get("major_release_day"), False)
        whipsaw_after_open = safe_bool(runtime_flags.get("whipsaw_detected_after_open"), False)

        thematic_context = self._derive_thematic_context(packet, macro_summary, macro_top_events)
        candidates = candidate_universe_override or self.candidate_universe
        candidates = self._dedupe_preserve_order(candidates)

        package = {
            "schema_version": "idiosyncratic_trade_package.v1",
            "timestamp_utc": now, "shadow_mode_only": True,
            "package_type": "diversified_idiosyncratic_daytrade_candidates",
            "effective_mode": effective_mode,
            "regime_shift_probability": round(regime_p, 4),
            "base_confidence": round(base_conf, 4),
            "time_window_adjusted_confidence": round(tw_adj_conf, 4),
            "window_context": {"time_window_name": packet.get("time_window_name"), "watchlist_only_window": watchlist_only_window, "window_shadow_execution_blocked": window_shadow_blocked, "window_confidence_multiplier": round(tw_conf_mult, 4), "window_size_multiplier": round(tw_size_mult, 4)},
            "runtime_flags_snapshot": {"major_release_day": major_release_day, "whipsaw_detected_after_open": whipsaw_after_open, "slippage_bps_estimate": slippage_bps_est, "luld_halt_detected": luld_halt_detected, "broker_status_degraded": broker_status_degraded},
            "macro_context": {"policy_release_urgency_score_max": macro_summary.get("policy_release_urgency_score_max", 0.0), "official_policy_confirmation_count": macro_summary.get("official_policy_confirmation_count", 0), "rate_regime_shock_candidate_any": macro_summary.get("rate_regime_shock_candidate_any", False), "macro_event_quorum_pass": (packet.get("macro_event_quorum_status") or {}).get("quorum_pass"), "macro_event_router_summary": macro_router_summary, "top_macro_headlines_preview": [str(e.get("headline", "")) for e in macro_top_events[:5]]},
            "thematic_context": thematic_context,
            "global_blocks": [], "execution_constraints": {}, "required_confirmations": [],
            "candidates": [], "blocked_candidates": [], "diversification_summary": {}, "operator_notes": [],
        }

        global_blocks = self._compute_global_blocks(packet=packet, combined_shadow_blocked=combined_shadow_blocked, window_shadow_blocked=window_shadow_blocked, watchlist_only_window=watchlist_only_window, policy_data_integrity_degraded=policy_data_integrity_degraded, luld_halt_detected=luld_halt_detected, broker_status_degraded=broker_status_degraded)
        package["global_blocks"] = global_blocks
        package["execution_constraints"] = self._compute_execution_constraints(packet=packet, slippage_bps_est=slippage_bps_est, tw_size_mult=tw_size_mult, watchlist_only_window=watchlist_only_window, major_release_day=major_release_day, whipsaw_after_open=whipsaw_after_open)
        package["required_confirmations"] = self._required_confirmations(packet=packet, policy_data_integrity_degraded=policy_data_integrity_degraded, major_release_day=major_release_day)

        candidate_rows, blocked_rows = self._build_candidate_rows(packet=packet, candidates=candidates, strategy_eligibility=strategy_eligibility, package_global_blocks=global_blocks, thematic_context=thematic_context)
        package["candidates"] = candidate_rows
        package["blocked_candidates"] = blocked_rows
        package["diversification_summary"] = self._build_diversification_summary(candidate_rows, blocked_rows)
        package["operator_notes"] = self._operator_notes(packet, package)
        # Assign deterministic package_id for intent-binding traceability
        package["package_id"] = self._ensure_package_id(package)
        return package

    def _load_watchlist(self) -> Dict[str, Any]:
        path = self.repo_root / "config" / "assets_watchlist.yaml"
        if path.exists():
            return load_yaml(path)
        return {}

    def _load_execution_reliability_cfg(self) -> Dict[str, Any]:
        path = self.repo_root / "config" / "execution_reliability.yaml"
        if path.exists():
            return load_yaml(path)
        return {"execution_reliability_guardrails": {"price_controls": {"default_order_type_equity": "limit", "default_order_type_option": "limit", "max_estimated_slippage_bps": {"opening_windows": 20, "lunch_lull": 12, "power_hour": 22}}, "fill_controls": {"order_ttl_seconds": {"opening_windows": 20, "lunch_lull": 45, "power_hour": 20}}}}

    def _build_default_candidate_universe(self) -> List[str]:
        seeded = []
        wl = self.watchlist_cfg.get("watchlist") or self.watchlist_cfg.get("assets") or []
        for item in wl:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol", "")).strip()
            if sym and self._is_equity_like(sym):
                seeded.append(sym)
        seeded.extend(["RTX", "NOC", "CVX", "SLB", "HAL", "SMCI", "ANET", "VRT", "ETN", "ABB", "ASML", "ARM", "AVGO", "MU", "AMZN", "MSFT", "GOOGL", "META", "DE", "URI", "PWR", "EME"])
        return self._dedupe_preserve_order(seeded)

    def _is_equity_like(self, sym: str) -> bool:
        bad_fragments = ["USD", "XAU", "UST", "^", "=", "/"]
        return not any(f in sym for f in bad_fragments)

    def _dedupe_preserve_order(self, items: List[str]) -> List[str]:
        seen = set()
        out = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    def _derive_thematic_context(self, packet: Dict[str, Any], macro_summary: Dict[str, Any], macro_top_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        text_blob = " ".join([str(e.get("headline", "")) + " " + str(e.get("summary", "")) for e in (macro_top_events or [])]).lower()
        rate_regime_any = safe_bool(macro_summary.get("rate_regime_shock_candidate_any"), False)
        policy_urgency = safe_float(macro_summary.get("policy_release_urgency_score_max"), 0.0)
        energy_shock = any(k in text_blob for k in ["oil", "energy", "opec", "hormuz", "tanker", "eia"])
        sanctions_policy = any(k in text_blob for k in ["sanction", "ofac", "treasury"])
        fed_rates = any(k in text_blob for k in ["fomc", "federal reserve", "fed", "cpi", "inflation", "jobs", "employment", "pce"])
        trade_tariffs = any(k in text_blob for k in ["tariff", "trade policy"])
        immigration_labor = any(k in text_blob for k in ["immigration", "labor", "jobs", "manufacturing"])
        packet_text_flags = {"energy_shock_transmission_confirmed": safe_bool(packet.get("energy_shock_transmission_confirmed"), False), "ai_capex_positive_impulse": safe_bool(packet.get("ai_capex_positive_impulse"), False), "hyperscaler_capex_watch": safe_bool(packet.get("hyperscaler_capex_watch"), False)}
        ai_infra_bias = packet_text_flags["ai_capex_positive_impulse"] or packet_text_flags["hyperscaler_capex_watch"]
        return {"rate_regime_shock_candidate_any": rate_regime_any, "policy_release_urgency_score_max": policy_urgency, "energy_shock_theme": energy_shock, "sanctions_policy_theme": sanctions_policy, "fed_rates_theme": fed_rates, "trade_tariffs_theme": trade_tariffs, "immigration_labor_theme": immigration_labor, "ai_infra_theme": ai_infra_bias or any(k in text_blob for k in ["nvidia", "data center", "hyperscaler", "ai infrastructure"]), "energy_shock_transmission_confirmed": packet_text_flags["energy_shock_transmission_confirmed"]}

    def _compute_global_blocks(self, packet, combined_shadow_blocked, window_shadow_blocked, watchlist_only_window, policy_data_integrity_degraded, luld_halt_detected, broker_status_degraded) -> List[str]:
        blocks = []
        control_flags = packet.get("control_flags") or {}
        if safe_bool(control_flags.get("manual_veto"), False): blocks.append("manual_veto")
        if safe_bool(control_flags.get("kill_switch"), False): blocks.append("kill_switch")
        if combined_shadow_blocked: blocks.append("shadow_execution_blocked")
        if window_shadow_blocked: blocks.append("time_window_shadow_execution_blocked")
        if watchlist_only_window: blocks.append("watchlist_only_window")
        if policy_data_integrity_degraded: blocks.append("policy_data_integrity_degraded")
        if luld_halt_detected: blocks.append("luld_halt_detected")
        if broker_status_degraded: blocks.append("broker_status_degraded")
        macro_quorum = (packet.get("macro_event_quorum_status") or {}).get("quorum_pass")
        if macro_quorum is False and safe_float(packet.get("policy_release_urgency_score_max"), 0.0) >= 0.85:
            blocks.append("high_urgency_macro_without_quorum")
        return self._dedupe_preserve_order(blocks)

    def _compute_execution_constraints(self, packet, slippage_bps_est, tw_size_mult, watchlist_only_window, major_release_day, whipsaw_after_open) -> Dict[str, Any]:
        cfg = self.execution_reliability_cfg.get("execution_reliability_guardrails", {})
        price_cfg = cfg.get("price_controls", {})
        fill_cfg = cfg.get("fill_controls", {})
        tw_name = str(packet.get("time_window_name") or "")
        max_slippage_cfg = price_cfg.get("max_estimated_slippage_bps", {}) or {}
        ttl_cfg = fill_cfg.get("order_ttl_seconds", {}) or {}
        bucket = "opening_windows" if "opening" in tw_name else ("power_hour" if "power_hour" in tw_name else ("lunch_lull" if "lunch" in tw_name else "opening_windows"))
        max_slippage_bps = float(max_slippage_cfg.get(bucket, 20))
        ttl_seconds = int(ttl_cfg.get(bucket, 20))
        if major_release_day: max_slippage_bps = min(max_slippage_bps, 15)
        if whipsaw_after_open: max_slippage_bps = min(max_slippage_bps, 12)
        constraints = {"shadow_mode_only": True, "default_order_type_equity": price_cfg.get("default_order_type_equity", "limit"), "default_order_type_option": price_cfg.get("default_order_type_option", "limit"), "max_estimated_slippage_bps_allowed": round(max_slippage_bps, 2), "runtime_estimated_slippage_bps": round(slippage_bps_est, 2), "slippage_constraint_breached": bool(slippage_bps_est > max_slippage_bps), "order_ttl_seconds": ttl_seconds, "position_size_multiplier_cap": round(clamp(tw_size_mult, 0.0, 1.25), 4), "watchlist_only_window": watchlist_only_window}
        if constraints["slippage_constraint_breached"]: constraints["recommended_execution_posture"] = "watchlist_only_or_reduce_size"
        elif watchlist_only_window: constraints["recommended_execution_posture"] = "watchlist_only"
        else: constraints["recommended_execution_posture"] = "shadow_candidate_generation_ok"
        return constraints

    def _required_confirmations(self, packet, policy_data_integrity_degraded, major_release_day) -> List[str]:
        req = []
        macro_summary = packet.get("macro_policy_summary") or {}
        if safe_bool(macro_summary.get("rate_regime_shock_candidate_any"), False):
            req.append("rate_cross_asset_check_pass")
            req.append("official_policy_or_macro_confirmation")
        if policy_data_integrity_degraded: req.append("policy_data_integrity_recovery_or_manual_review")
        if major_release_day:
            req.append("release_window_volatility_confirmation")
            req.append("fresh_quote_and_signal_age_check")
        req += ["execution_reliability_checks_pass", "risk_gate_pass", "manual_operator_review_before_any_live_use"]
        return self._dedupe_preserve_order(req)

    def _build_candidate_rows(self, packet, candidates, strategy_eligibility, package_global_blocks, thematic_context) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        tw_name = str(packet.get("time_window_name") or "")
        base_conf = safe_float(packet.get("time_window_adjusted_confidence") or packet.get("confidence"), 0.0)
        regime_p = safe_float(packet.get("regime_shift_probability"), 0.0)
        slippage_breach = safe_bool((packet.get("execution_constraints") or {}).get("slippage_constraint_breached"), False)
        strat_elig = {}
        for k, v in (strategy_eligibility or {}).items():
            if isinstance(v, dict): strat_elig[k] = safe_bool(v.get("eligible"), False)
            else: strat_elig[k] = safe_bool(v, False)
        global_blocking = set(package_global_blocks)

        for sym in candidates:
            themes = self.ticker_theme_map.get(sym, ["other"])
            idea_templates = self._select_strategy_templates_for_symbol(sym, themes, tw_name, thematic_context, strat_elig)
            if not idea_templates:
                blocked.append({"symbol": sym, "reason": "no_eligible_strategy_templates_for_window", "themes": themes})
                continue
            for template_key in idea_templates:
                idea = self.default_strategy_templates.get(template_key)
                if not idea: continue
                req_flags = idea.get("requires", [])
                missing_reqs = [r for r in req_flags if not strat_elig.get(r, False)]
                block_reasons = []
                if missing_reqs: block_reasons.append(f"missing_strategy_eligibility:{','.join(missing_reqs)}")
                hard_blocks = {"manual_veto", "kill_switch", "luld_halt_detected", "broker_status_degraded"}
                if any(b in global_blocking for b in hard_blocks): block_reasons.append("global_hard_block")
                if "shadow_execution_blocked" in global_blocking and idea["direction"] != "watchlist_only": block_reasons.append("shadow_execution_blocked")
                if "time_window_shadow_execution_blocked" in global_blocking and idea["direction"] != "watchlist_only": block_reasons.append("time_window_shadow_execution_blocked")
                if "watchlist_only_window" in global_blocking and idea["direction"] != "watchlist_only": block_reasons.append("watchlist_only_window")
                if slippage_breach and idea["direction"] != "watchlist_only": block_reasons.append("slippage_constraint_breached")
                thematic_boost = self._thematic_fit_score(sym, themes, thematic_context)
                strategy_fit = self._strategy_fit_score(template_key, thematic_context, tw_name)
                confidence = clamp(0.45 * base_conf + 0.25 * regime_p + 0.15 * thematic_boost + 0.15 * strategy_fit)
                window_size_mult = safe_float(packet.get("window_size_multiplier"), 1.0)
                size_mult = clamp(window_size_mult * (0.6 + 0.4 * confidence), 0.0, 1.2)
                thesis = self._build_thesis(sym, themes, template_key, thematic_context, packet)
                row = {"symbol": sym, "themes": themes, "template_key": template_key, "strategy_style": idea["style"], "direction": idea["direction"], "instrument_types": idea["instrument_types"], "window_name": tw_name, "thesis": thesis, "catalyst_type": self._catalyst_type(themes, thematic_context), "confidence_score": round(confidence, 4), "size_multiplier_suggestion": round(size_mult, 4), "required_confirmations": self._candidate_required_confirmations(sym, themes, thematic_context), "execution_constraints": self._candidate_execution_constraints(packet, idea), "block_reasons": block_reasons, "status": "blocked" if block_reasons else "candidate"}
                row = self._apply_fill_simulator(row, packet)
                if row.get("status") == "blocked" or row.get("block_reasons"):
                    row["status"] = "blocked"
                    blocked.append(row)
                else: rows.append(row)
        rows = self._rank_and_diversify(rows)
        blocked = self._rank_blocked(blocked)
        # Assign deterministic IDs for intent-binding traceability
        for r in rows:
            if not r.get("candidate_id"):
                r["candidate_id"] = self._ensure_candidate_id(r)
        for b in blocked:
            if not b.get("candidate_id"):
                b["candidate_id"] = self._ensure_candidate_id(b)
        return rows, blocked

    def _select_strategy_templates_for_symbol(self, sym, themes, tw_name, thematic_context, strat_elig) -> List[str]:
        out: List[str] = []
        if "close_exhaustion_watch" in tw_name:
            out.append("watchlist_next_day_fade_only")
            return out
        if "late_morning_mean_reversion" in tw_name: out.append("late_morning_mean_reversion_short")
        if "power_hour" in tw_name:
            if any(t in themes for t in ["defense", "energy", "ai_infra", "hyperscaler", "electrical", "networking"]): out.append("power_hour_continuation_long")
            if "high_beta" in themes and thematic_context.get("rate_regime_shock_candidate_any"): out.append("short_breakdown")
        if "opening" in tw_name or "opening_range_breakout" in tw_name:
            if any(t in themes for t in ["defense", "energy", "ai_infra", "hyperscaler"]): out.append("long_breakout")
            if any(t in themes for t in ["high_beta", "consumer_discretionary"]) and thematic_context.get("rate_regime_shock_candidate_any"):
                out.append("gap_and_crap_puts")
                out.append("short_breakdown")
        if "lunch_lull" in tw_name:
            if strat_elig.get("short_mean_reversion", False): out.append("late_morning_mean_reversion_short")
        if not out:
            if any(t in themes for t in ["defense", "energy", "ai_infra"]): out.append("long_breakout")
            elif "high_beta" in themes: out.append("short_breakdown")
        return self._dedupe_preserve_order(out)

    def _thematic_fit_score(self, sym, themes, thematic_context) -> float:
        score = 0.4
        if thematic_context.get("energy_shock_theme"):
            if any(t in themes for t in ["energy", "energy_services", "oil_supply"]): score += 0.35
            if any(t in themes for t in ["consumer_discretionary", "high_beta"]): score += 0.05
        if thematic_context.get("sanctions_policy_theme"):
            if any(t in themes for t in ["defense", "energy", "geopolitical_conflict"]): score += 0.25
        if thematic_context.get("fed_rates_theme") or thematic_context.get("rate_regime_shock_candidate_any"):
            if any(t in themes for t in ["ai_infra", "hyperscaler", "high_beta", "consumer_discretionary", "margin_squeeze_risk"]): score += 0.20
        if thematic_context.get("ai_infra_theme"):
            if any(t in themes for t in ["ai_infra", "hyperscaler", "server_hardware", "networking", "power_cooling", "data_center_capex"]): score += 0.35
        return clamp(score)

    def _strategy_fit_score(self, template_key, thematic_context, tw_name) -> float:
        score = 0.5
        if template_key in {"long_breakout", "power_hour_continuation_long"} and ("power_hour" in tw_name or "opening" in tw_name): score += 0.2
        if template_key == "gap_and_crap_puts" and thematic_context.get("rate_regime_shock_candidate_any"): score += 0.2
        if template_key == "late_morning_mean_reversion_short" and "mean_reversion" in tw_name: score += 0.25
        if template_key == "watchlist_next_day_fade_only" and "close_exhaustion_watch" in tw_name: score += 0.35
        return clamp(score)

    def _build_thesis(self, sym, themes, template_key, thematic_context, packet) -> str:
        clauses = []
        if thematic_context.get("energy_shock_theme") and any(t in themes for t in ["energy", "energy_services", "oil_supply"]): clauses.append("energy supply shock narrative supports relative strength in energy-linked names")
        if thematic_context.get("sanctions_policy_theme") and any(t in themes for t in ["defense", "geopolitical_conflict"]): clauses.append("sanctions/geopolitical policy flow increases defense/geopolitical sensitivity relevance")
        if thematic_context.get("ai_infra_theme") and any(t in themes for t in ["ai_infra", "hyperscaler", "server_hardware", "networking", "power_cooling"]): clauses.append("AI infrastructure / data-center capex theme supports idiosyncratic upside in infra-linked names")
        if thematic_context.get("rate_regime_shock_candidate_any") and any(t in themes for t in ["high_beta", "consumer_discretionary", "margin_squeeze_risk"]): clauses.append("rate-regime shock conditions raise downside sensitivity / valuation compression risk")
        if not clauses: clauses.append("candidate selected for watchlist relevance and time-window strategy fit")
        return f"{sym}: " + "; ".join(clauses) + f"; strategy_template={template_key}"

    def _catalyst_type(self, themes, thematic_context) -> str:
        if thematic_context.get("energy_shock_theme") and any(t in themes for t in ["energy", "energy_services"]): return "macro_geopolitical_energy"
        if thematic_context.get("sanctions_policy_theme") and any(t in themes for t in ["defense", "geopolitical_conflict"]): return "macro_policy_geopolitical"
        if thematic_context.get("ai_infra_theme") and any(t in themes for t in ["ai_infra", "hyperscaler", "server_hardware", "networking", "power_cooling"]): return "ai_infrastructure_capex"
        if thematic_context.get("rate_regime_shock_candidate_any"): return "macro_rates_cross_asset"
        return "idiosyncratic_watchlist"

    def _candidate_required_confirmations(self, sym, themes, thematic_context) -> List[str]:
        req = ["fresh_quote_check", "spread_slippage_check", "execution_reliability_check"]
        if thematic_context.get("rate_regime_shock_candidate_any"): req.append("rate_cross_asset_check_pass")
        if any(t in themes for t in ["energy", "energy_services", "oil_supply"]): req.append("energy_transmission_confirmation_or_market_price_alignment")
        if any(t in themes for t in ["defense", "geopolitical_conflict"]): req.append("official_geopolitical_policy_confirmation")
        if any(t in themes for t in ["ai_infra", "hyperscaler", "server_hardware", "networking"]): req.append("headline_catalyst_quality_check_non_osint_only")
        return self._dedupe_preserve_order(req)

    def _candidate_execution_constraints(self, packet, idea) -> Dict[str, Any]:
        tw_name = str(packet.get("time_window_name") or "")
        runtime_flags = packet.get("runtime_flags") or {}
        slippage_bps_est = safe_float(runtime_flags.get("slippage_bps_estimate"), 0.0)
        if "power_hour" in tw_name or "opening" in tw_name: ttl = 20
        elif "lunch" in tw_name: ttl = 45
        else: ttl = 30
        instrument_types = idea.get("instrument_types", [])
        options_present = any("option" in str(x) for x in instrument_types)
        return {"shadow_only": True, "limit_only": True, "order_ttl_seconds": ttl, "estimated_slippage_bps_runtime": round(slippage_bps_est, 2), "defined_risk_preferred": bool(options_present or idea.get("direction") in {"bearish_defined_risk"}), "no_live_order_submission": True}

    def _apply_fill_simulator(self, candidate_row: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich candidate with execution realism assessment. Shadow-only. Never routes orders."""
        row = dict(candidate_row)

        if self.fill_simulator is None:
            row["fill_sim_assessment"] = {
                "schema_version": "fill_sim_assessment.v1",
                "execution_quality_class": "unknown",
                "do_not_route_even_in_shadow": True,
                "do_not_route_reasons": ["fill_simulator_unavailable"]
            }
            row.setdefault("block_reasons", []).append("fill_simulator_unavailable")
            row["status"] = "blocked"
            return row

        quote_snapshot = (packet.get("quote_snapshots") or {}).get(row.get("symbol"), {})
        assessment = self.fill_simulator.assess_candidate(
            candidate=row,
            packet=packet,
            quote_snapshot=quote_snapshot if isinstance(quote_snapshot, dict) else None
        )
        row["fill_sim_assessment"] = assessment

        if assessment.get("do_not_route_even_in_shadow"):
            row.setdefault("block_reasons", []).append("fill_sim_do_not_route")
            row["status"] = "blocked"

        quality = str(assessment.get("execution_quality_class", "unknown"))
        if quality == "poor" and row.get("status") != "blocked":
            row["confidence_score"] = round(max(0.0, float(row.get("confidence_score", 0.0)) * 0.75), 4)
            row["size_multiplier_suggestion"] = round(max(0.0, float(row.get("size_multiplier_suggestion", 0.0)) * 0.60), 4)
            row.setdefault("block_reasons", []).append("fill_quality_poor_reduce_or_watch")
        elif quality == "acceptable_shadow_only" and row.get("status") != "blocked":
            row["size_multiplier_suggestion"] = round(max(0.0, float(row.get("size_multiplier_suggestion", 0.0)) * 0.80), 4)

        row.setdefault("execution_constraints", {})
        row["execution_constraints"]["expected_slippage_bps_simulated"] = assessment.get("expected_slippage_bps")
        row["execution_constraints"]["fill_feasibility_score"] = assessment.get("fill_feasibility_score")
        row["execution_constraints"]["partial_fill_probability"] = assessment.get("partial_fill_probability")
        row["execution_constraints"]["reject_risk_probability"] = assessment.get("reject_risk_probability")
        row["execution_constraints"]["execution_quality_class"] = assessment.get("execution_quality_class")

        return row

    def _rank_and_diversify(self, rows) -> List[Dict[str, Any]]:
        for r in rows:
            fill_feas = safe_float((r.get("fill_sim_assessment") or {}).get("fill_feasibility_score"), 0.5)
            reject_risk = safe_float((r.get("fill_sim_assessment") or {}).get("reject_risk_probability"), 0.0)
            exec_penalty = 1.0 - min(reject_risk, 0.8) * 0.5
            r["_rank_score"] = round(safe_float(r.get("confidence_score")) * max(safe_float(r.get("size_multiplier_suggestion")), 0.01) * fill_feas * exec_penalty, 6)
        rows = sorted(rows, key=lambda r: r["_rank_score"], reverse=True)
        selected = []
        theme_counts: Dict[str, int] = {}
        for r in rows:
            themes = r.get("themes", [])
            primary = themes[0] if themes else "other"
            if theme_counts.get(primary, 0) >= 3: continue
            selected.append(r)
            theme_counts[primary] = theme_counts.get(primary, 0) + 1
        if len(selected) < 5:
            for r in rows:
                if r in selected: continue
                selected.append(r)
                if len(selected) >= min(len(rows), 8): break
        for r in selected: r.pop("_rank_score", None)
        return selected

    def _rank_blocked(self, blocked) -> List[Dict[str, Any]]:
        def keyfn(r):
            br = " ".join(r.get("block_reasons", [])) if isinstance(r.get("block_reasons"), list) else str(r.get("reason", ""))
            hard = 0 if "global_hard_block" in br else 1
            return (hard, str(r.get("symbol", "")))
        return sorted(blocked, key=keyfn)

    def _build_diversification_summary(self, candidates, blocked) -> Dict[str, Any]:
        theme_counts: Dict[str, int] = {}
        direction_counts: Dict[str, int] = {}
        instrument_counts: Dict[str, int] = {}
        for r in candidates:
            for t in r.get("themes", [])[:2]: theme_counts[t] = theme_counts.get(t, 0) + 1
            direction_counts[str(r.get("direction", "unknown"))] = direction_counts.get(str(r.get("direction", "unknown")), 0) + 1
            for inst in r.get("instrument_types", []): instrument_counts[str(inst)] = instrument_counts.get(str(inst), 0) + 1
        return {"candidate_count": len(candidates), "blocked_candidate_count": len(blocked), "theme_counts_top": dict(sorted(theme_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]), "direction_counts": direction_counts, "instrument_type_counts": instrument_counts}

    def _ensure_package_id(self, package: Dict[str, Any]) -> str:
        if package.get("package_id"):
            return str(package["package_id"])
        ts = str(package.get("timestamp_utc", ""))
        ptype = str(package.get("package_type", "pkg"))
        return f"pkg-{uuid.uuid5(uuid.NAMESPACE_URL, ts + '|' + ptype).hex[:12]}"

    def _ensure_candidate_id(self, candidate: Dict[str, Any]) -> str:
        if candidate.get("candidate_id"):
            return str(candidate["candidate_id"])
        sym = str(candidate.get("symbol", ""))
        tmpl = str(candidate.get("template_key", ""))
        direction = str(candidate.get("direction", ""))
        digest = hashlib.sha1(f"{sym}|{tmpl}|{direction}".encode()).hexdigest()[:12]
        return f"cand-{sym.lower()}-{digest}"

    def _operator_notes(self, packet, package) -> List[str]:
        notes = []
        if package["global_blocks"]: notes.append(f"Global blocks active: {', '.join(package['global_blocks'])}")
        if package["execution_constraints"].get("slippage_constraint_breached"): notes.append("Runtime slippage estimate exceeds allowed threshold; treat package as watchlist-only or reduce size.")
        if package["macro_context"].get("policy_release_urgency_score_max", 0.0) >= 0.85 and not package["macro_context"].get("macro_event_quorum_pass", False): notes.append("High-urgency macro context without quorum confirmation; degrade confidence and require manual review.")
        if not package["candidates"]: notes.append("No actionable shadow candidates after time-window and guardrail filtering; no-trade/watchlist outcome is valid.")
        notes.append("Shadow-mode package only. No live order routing from this builder.")
        return notes


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Global Sentinel Idiosyncratic Package Builder")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--packet-json", default=None, help="Path to input packet JSON")
    p.add_argument("--output", default=None, help="Output path for package JSON")
    return p.parse_args()


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    builder = IdiosyncraticPackageBuilder(repo_root)
    if args.packet_json:
        packet = json.loads(Path(args.packet_json).read_text(encoding="utf-8"))
    else:
        packet = {"mode": "ELEVATED", "effective_mode": "ELEVATED", "regime_shift_probability": 0.67, "confidence": 0.74, "time_window_adjusted_confidence": 0.78, "time_window_name": "power_hour", "watchlist_only_window": False, "window_shadow_execution_blocked": False, "shadow_execution_blocked": False, "window_confidence_multiplier": 1.05, "window_size_multiplier": 1.0, "runtime_flags": {"major_release_day": False, "whipsaw_detected_after_open": False, "slippage_bps_estimate": 12}, "macro_policy_summary": {"policy_release_urgency_score_max": 0.9, "official_policy_confirmation_count": 2, "rate_regime_shock_candidate_any": True}, "macro_event_quorum_status": {"quorum_pass": True}, "macro_event_router": {"macro_events_priority_top": [{"headline": "Treasury linked item: sanctions update on shipping entities", "summary": "official source", "event_type": "treasury_sanctions_or_regulatory_action"}, {"headline": "FRED series update: DGS10", "summary": "rate move", "event_type": "central_bank_statement"}], "macro_event_router_summary": {"top_event_count": 2}}, "time_window_state": {"window_policy": {"priority": "trend_acceleration", "allow_shadow_drafts": True}, "strategy_eligibility": {"orb_breakout_long": {"eligible": True}, "orb_breakdown_short": {"eligible": True}, "gap_and_crap_puts": {"eligible": False}, "short_mean_reversion": {"eligible": False}, "eod_fade_watchlist_only": {"eligible": False}}}, "control_flags": {}}
    package = builder.build_package(packet)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    else:
        print(json.dumps(package, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Global Sentinel V5.1 — Trade Idea Packager

Bridges the Trade Analysis Engine output to the Shadow Order Router input.
Converts trade ideas into candidate packages the router can process.

Safety: All packages are marked shadow_mode=True. No live orders.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# When shorting is blocked, use inverse ETFs instead
SHORT_TO_INVERSE = {
    "JETS": None,  # No inverse JETS ETF — skip
    "SPY": {"symbol": "SH", "side": "long", "note": "ProShares Short S&P500"},
    "QQQ": {"symbol": "PSQ", "side": "long", "note": "ProShares Short QQQ"},
    "EEM": None,  # EUM too illiquid, bleeds on hold — skip
    "HYG": {"symbol": "SJB", "side": "long", "note": "ProShares Short High Yield"},
    "FXI": {"symbol": "YANG", "side": "long", "note": "Direxion Daily China Bear 3x"},
    "IYT": None,  # No good inverse transport ETF — skip
    "XLV": None,  # No good inverse healthcare — skip
    "CCL": None,  # No good inverse, skip
    "DAL": None,  # No airline inverse ETF
    "GLD": {"symbol": "GLL", "side": "long", "note": "ProShares UltraShort Gold"},
    "GDX": {"symbol": "DUST", "side": "long", "note": "Direxion Daily Gold Miners Bear 2x"},
    "VLO": None,  # No inverse refiner — skip
    "ITA": None,  # No inverse defense ETF — skip
    "XLE": {"symbol": "ERY", "side": "long", "note": "Direxion Daily Energy Bear 2x"},
    "RTX": None,  # No single-stock inverse — skip
    "LMT": None,  # No single-stock inverse — skip
    "XOM": None,  # No single-stock inverse — skip
    "UVXY": {"symbol": "SVXY", "side": "long", "note": "ProShares Short VIX Short-Term"},
}


class TradeIdeaPackager:
    """Converts trade analysis ideas into shadow order router packages."""

    def build_package(
        self,
        trade_analysis: Dict[str, Any],
        scorecard: Dict[str, Any],
        microstructure: Optional[Dict[str, Any]] = None,
        max_ideas: int = 10,
        politician_alpha: Optional[Dict[str, Any]] = None,
        bridge_signals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build a router-compatible package from trade analysis output.

        Args:
            trade_analysis: Output from TradeAnalysisEngine.analyze()
            scorecard: Current scorecard for context
            microstructure: Market microstructure data for price hints
            max_ideas: Max number of trade ideas to include as candidates
            politician_alpha: Politician alpha data for confidence boosting
            bridge_signals: All bridge data (GSS, consciousness, narrative, etc.)
        """
        ideas = trade_analysis.get("trade_ideas", [])[:max_ideas]
        if not ideas:
            return {"candidates": [], "global_blocks": ["no_trade_ideas"]}

        mode = scorecard.get("mode", "NORMAL")
        regime_p = scorecard.get("regime_shift_probability", 0)
        confidence = scorecard.get("confidence", 0)
        time_window = scorecard.get("time_window", {})
        micro = microstructure or {}
        pol_scores = (politician_alpha or {}).get("political_alpha_scores", {})
        signals = bridge_signals or {}

        # --- Time Window Aware Trading ---
        current_window = time_window.get("current_window", "unknown")
        tw_confidence_mult = float(time_window.get("confidence_multiplier", 1.0))
        tw_size_mult = float(time_window.get("size_multiplier", 1.0))
        tw_risk_budget = time_window.get("risk_budget", {})
        tw_strategy_elig = time_window.get("strategy_eligibility", {})
        tw_preferred_setups = time_window.get("preferred_setups", [])
        tw_restrictions = time_window.get("restrictions", {})
        tw_blocked = time_window.get("shadow_execution_window_blocked", False)
        max_new_positions = tw_risk_budget.get("max_new_positions")

        # Compute signal-based confidence adjustments from ALL bridge data
        signal_boost = self._compute_signal_boost(signals, scorecard)

        # Apply learned adjustments from adaptive feedback loop
        learned = signals.get("_feedback_adjustments", {})
        if learned:
            for sig_name, adj in learned.items():
                if sig_name in signal_boost:
                    signal_boost[sig_name] = round(signal_boost[sig_name] + adj, 4)

        # Check if shadow execution is eligible
        if not scorecard.get("shadow_execution_eligible", False):
            return {
                "candidates": [],
                "global_blocks": ["shadow_execution_not_eligible"],
                "effective_mode": mode,
            }

        # Block all new positions if window says size=0 or blocked
        if tw_blocked and tw_size_mult <= 0:
            return {
                "candidates": [],
                "global_blocks": [f"window_blocked:{current_window}"],
                "effective_mode": mode,
                "window_context": {
                    "time_window_name": current_window,
                    "watchlist_only_window": True,
                },
            }

        candidates = []
        blocked_candidates = []
        for idea in ideas:
            # Check strategy eligibility for this window
            strategy_style = idea.get("strategy_style", "regime_playbook")
            strat_elig = tw_strategy_elig.get(strategy_style, {})
            if strat_elig and not strat_elig.get("eligible", True):
                blocked_candidates.append({
                    "symbol": idea.get("symbol"),
                    "reason": f"strategy '{strategy_style}' blocked in {current_window}",
                    "block_reasons": strat_elig.get("reasons_blocked", []),
                })
                continue

            cand = self._idea_to_candidate(
                idea, confidence, micro, pol_scores, signal_boost,
                tw_confidence_mult, tw_size_mult, current_window,
            )
            if cand:
                # Apply window-specific restrictions
                if tw_restrictions.get("watchlist_only_unless_exceptional_catalyst"):
                    if cand["confidence_score"] < 0.55:
                        blocked_candidates.append({
                            "symbol": cand["symbol"],
                            "reason": f"below catalyst threshold in {current_window} (lunch lull)",
                            "confidence": cand["confidence_score"],
                        })
                        continue

                candidates.append(cand)

            # Enforce max_new_positions from risk budget
            if max_new_positions is not None and len(candidates) >= int(max_new_positions):
                break

        # Prioritize preferred setups — sort candidates so preferred come first
        if tw_preferred_setups:
            preferred_set = set(tw_preferred_setups)
            candidates.sort(
                key=lambda c: (0 if c.get("strategy_style") in preferred_set else 1,
                               -c.get("confidence_score", 0))
            )

        package = {
            "schema_version": "trade_idea_package.v1",
            "package_id": f"tip-{uuid.uuid4().hex[:12]}",
            "package_type": "trade_analysis_ideas",
            "timestamp_utc": iso_now(),
            "effective_mode": mode,
            "candidates": candidates,
            "blocked_candidates": blocked_candidates,
            "global_blocks": [],
            "window_context": {
                "time_window_name": current_window,
                "watchlist_only_window": tw_blocked,
                "confidence_multiplier": tw_confidence_mult,
                "size_multiplier": tw_size_mult,
                "preferred_setups": tw_preferred_setups,
                "risk_budget": tw_risk_budget,
            },
            "macro_context": {
                "regime_shift_probability": regime_p,
                "confidence": confidence,
                "transition": trade_analysis.get("transition", ""),
                "macro_event_quorum_pass": not scorecard.get("fallback_mode_status", False),
            },
            "snapshot": {
                "market_microstructure": micro,
            },
        }

        return package

    def _compute_signal_boost(
        self,
        signals: Dict[str, Any],
        scorecard: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Compute per-signal confidence adjustments from ALL bridge data.

        Returns a dict of boost components that get summed and applied to each
        candidate's confidence score. Positive = boost, negative = penalty.

        Signals integrated:
        - GSS econophysics (gss_signal in scorecard)
        - GCP consciousness coherence
        - Narrative velocity & dominant narrative
        - Market microstructure (vol regime, liquidity)
        - Options greeks (put/call skew, gamma exposure)
        - Fed Board hawkish/dovish tilt
        - Treasury OFAC sanctions activity
        - White House policy signals
        - BLS labor/economic releases
        - AI/technology disruption signals
        """
        boost: Dict[str, float] = {}

        # --- 1. GSS Econophysics Signal ---
        gss = scorecard.get("gss_signal", {})
        if isinstance(gss, dict):
            gss_sig = gss.get("signal", "NEUTRAL")
            gss_conf = gss.get("confidence", 0)
            if gss_sig == "RISK_OFF":
                boost["gss"] = -0.15 * min(gss_conf, 1.0)
            elif gss_sig == "HEDGE":
                boost["gss"] = -0.10 * min(gss_conf, 1.0)
            elif gss_sig == "BLACK_SWAN_SHIELD":
                boost["gss"] = -0.20 * min(gss_conf, 1.0)
            elif gss_sig == "GAMMA_SQUEEZE":
                boost["gss"] = 0.12 * min(gss_conf, 1.0)
            elif gss_sig == "MOMENTUM_BURST":
                boost["gss"] = 0.10 * min(gss_conf, 1.0)
            elif gss_sig in ("NEUTRAL", "NO_DATA"):
                boost["gss"] = 0.0

        # --- 2. GCP Consciousness Coherence ---
        consciousness = signals.get("gcp_consciousness", {})
        coherence = consciousness.get("coherence_z", 0)
        if isinstance(coherence, (int, float)):
            if coherence >= 2.5:
                boost["consciousness"] = -0.08  # High coherence = potential black swan
            elif coherence >= 1.5:
                boost["consciousness"] = -0.04  # Elevated awareness
            elif coherence <= -1.5:
                boost["consciousness"] = 0.03   # Low coherence = calm markets

        # --- 3. Narrative Velocity ---
        narrative = signals.get("narrative_velocity", {})
        velocity = narrative.get("velocity_score", 0)
        infection = narrative.get("infection_rate", 0)
        dominant = narrative.get("dominant_narrative", "")
        if isinstance(velocity, (int, float)):
            if velocity >= 80:
                boost["narrative"] = -0.10  # Extreme narrative = likely priced in or panic
            elif velocity >= 50:
                boost["narrative"] = -0.04  # High velocity = caution
            elif velocity >= 20:
                boost["narrative"] = 0.02   # Moderate = opportunity window
            # AI/tech disruption narratives boost tech-adjacent trades
            if dominant and "ai_technology" in str(dominant).lower():
                boost["ai_narrative"] = 0.05

        # --- 4. Market Microstructure (aggregate vol regime) ---
        micro = signals.get("market_microstructure", {})
        if isinstance(micro, dict):
            # Check aggregate vol across symbols
            vol_scores = []
            for sym_data in micro.values():
                if isinstance(sym_data, dict):
                    vol = sym_data.get("sigma_daily_pct", 0)
                    if isinstance(vol, (int, float)):
                        vol_scores.append(vol)
            if vol_scores:
                avg_vol = sum(vol_scores) / len(vol_scores)
                if avg_vol >= 5.0:
                    boost["microstructure_vol"] = 0.08   # High vol = opportunity for intraday
                elif avg_vol >= 3.0:
                    boost["microstructure_vol"] = 0.04   # Elevated vol
                elif avg_vol < 1.0:
                    boost["microstructure_vol"] = -0.03  # Dead vol = no opportunity

        # --- 5. Options Greeks (put/call skew, gamma exposure) ---
        options = signals.get("options_greeks", {})
        if isinstance(options, dict):
            pc_ratio = options.get("put_call_ratio", 1.0)
            gamma_exp = options.get("aggregate_gamma_exposure", 0)
            if isinstance(pc_ratio, (int, float)):
                if pc_ratio >= 1.5:
                    boost["options_fear"] = -0.06  # Extreme put buying = fear
                elif pc_ratio <= 0.6:
                    boost["options_greed"] = 0.06  # Extreme call buying = momentum
            if isinstance(gamma_exp, (int, float)):
                if gamma_exp < -1e9:
                    boost["negative_gamma"] = 0.05  # Negative gamma = vol amplification

        # --- 5b. Per-symbol gamma exposure from options data ---
        if isinstance(options, dict):
            per_symbol = options.get("per_symbol", {})
            if isinstance(per_symbol, dict):
                for sym, sym_opts in per_symbol.items():
                    if isinstance(sym_opts, dict):
                        sym_gamma_risk = sym_opts.get("gamma_squeeze_risk", "normal")
                        if sym_gamma_risk in ("high", "extreme"):
                            boost["gamma_squeeze_active"] = 0.08

        # --- 6. Fed Board Signals ---
        fed = signals.get("fed_board", {})
        if isinstance(fed, dict):
            tone = fed.get("aggregate_tone", "neutral")
            if tone == "hawkish":
                boost["fed"] = -0.05  # Hawkish = headwind for equities
            elif tone == "dovish":
                boost["fed"] = 0.06   # Dovish = tailwind

        # --- 7. Treasury OFAC Sanctions ---
        ofac = signals.get("treasury_ofac", {})
        if isinstance(ofac, dict):
            new_sanctions = ofac.get("new_designations_count", 0)
            if isinstance(new_sanctions, (int, float)) and new_sanctions >= 5:
                boost["ofac_sanctions"] = -0.04  # Major sanctions wave

        # --- 8. White House Policy ---
        wh = signals.get("whitehouse_policy", {})
        if isinstance(wh, dict):
            policy_tone = wh.get("economic_tone", "neutral")
            if policy_tone == "stimulative":
                boost["wh_policy"] = 0.04
            elif policy_tone == "restrictive":
                boost["wh_policy"] = -0.04
            # AI executive orders or tech policy
            ai_policy = wh.get("ai_policy_signal")
            if ai_policy == "accelerate":
                boost["wh_ai_policy"] = 0.05
            elif ai_policy == "regulate":
                boost["wh_ai_policy"] = -0.03

        # --- 9. BLS Labor/Economic Releases ---
        bls = signals.get("bls_releases", {})
        if isinstance(bls, dict):
            unemployment_surprise = bls.get("unemployment_surprise", 0)
            if isinstance(unemployment_surprise, (int, float)):
                if unemployment_surprise >= 0.3:
                    boost["bls_labor"] = -0.06  # Worse than expected
                elif unemployment_surprise <= -0.3:
                    boost["bls_labor"] = 0.04   # Better than expected
            # AI-driven job displacement signals
            ai_displacement = bls.get("tech_sector_layoffs_spike", False)
            if ai_displacement:
                boost["ai_job_displacement"] = -0.03

        # --- 10. Velocity-Politician Correlation ---
        # When narrative velocity spikes, politician alpha signal carries more weight
        if isinstance(velocity, (int, float)) and velocity >= 50:
            pol_alpha_data = signals.get("politician_alpha", {})
            if isinstance(pol_alpha_data, dict):
                pol_sentiment = pol_alpha_data.get("aggregate_sentiment", "neutral")
                whale_count = len(pol_alpha_data.get("top_whale_trades", []))
                if pol_sentiment == "bullish" and whale_count >= 3:
                    boost["velocity_politician_sync"] = 0.08  # High velocity + bullish politicians
                elif pol_sentiment == "bearish" and whale_count >= 3:
                    boost["velocity_politician_sync"] = -0.08  # High velocity + bearish politicians

        # --- 11. Regime shift probability from scorecard ---
        regime_p = scorecard.get("regime_shift_probability", 0)
        if isinstance(regime_p, (int, float)):
            if regime_p >= 0.7:
                boost["regime_shift"] = -0.10  # High regime shift = reduce confidence
            elif regime_p >= 0.5:
                boost["regime_shift"] = -0.05

        # --- 12. EIA Energy Inventory Signals ---
        eia_packets = signals.get("eia", [])
        if isinstance(eia_packets, list):
            for pkt in eia_packets:
                if not isinstance(pkt, dict):
                    continue
                meta = pkt.get("parsing_meta", {})
                draw = meta.get("inventory_draw_mmbbl", 0)
                if isinstance(draw, (int, float)):
                    if draw > 5.0:
                        boost["eia_large_draw"] = 0.06  # Large crude draw = bullish energy
                    elif draw > 2.0:
                        boost["eia_draw"] = 0.03
                    elif draw < -5.0:
                        boost["eia_large_build"] = -0.04  # Large build = bearish energy

        # --- 13. GDELT Geopolitical Event Intensity ---
        gdelt_events = signals.get("gdelt_events", [])
        if isinstance(gdelt_events, list) and len(gdelt_events) > 0:
            neg_count = sum(1 for e in gdelt_events if isinstance(e, dict) and e.get("avg_tone", 0) < -5.0)
            if neg_count >= 10:
                boost["gdelt_crisis"] = -0.08  # Heavy negative geopolitical flow
            elif neg_count >= 5:
                boost["gdelt_tension"] = -0.04

        # --- 14. Aviation / Travel Disruption Signals ---
        disruptions = signals.get("aviation_disruptions", [])
        if isinstance(disruptions, list) and len(disruptions) > 0:
            high_severity = sum(1 for d in disruptions if isinstance(d, dict) and d.get("severity") == "high")
            if high_severity >= 3:
                boost["aviation_crisis"] = -0.08  # Major airspace/travel disruption
            elif high_severity >= 1:
                boost["aviation_disruption"] = -0.04
            elif len(disruptions) >= 5:
                boost["aviation_elevated"] = -0.03

        # --- 15. Finnhub News Headline Pressure ---
        finnhub_packets = signals.get("finnhub", [])
        if isinstance(finnhub_packets, list) and len(finnhub_packets) > 0:
            pressure_scores = []
            for pkt in finnhub_packets:
                if isinstance(pkt, dict):
                    p = (pkt.get("parsing_meta") or {}).get("headline_pressure_score", 0)
                    if isinstance(p, (int, float)) and p > 0:
                        pressure_scores.append(p)
            if pressure_scores:
                avg_pressure = sum(pressure_scores) / len(pressure_scores)
                if avg_pressure > 0.7:
                    boost["news_pressure_high"] = -0.06
                elif avg_pressure > 0.4:
                    boost["news_pressure_moderate"] = -0.03
                elif avg_pressure < 0.15:
                    boost["news_calm"] = 0.02  # Low news pressure = favorable

        # --- 16. Yield Curve & Bond Market Signals (FRED) ---
        fred_packets = signals.get("fred", [])
        if isinstance(fred_packets, list):
            for pkt in fred_packets:
                meta = (pkt.get("parsing_meta") or {}) if isinstance(pkt, dict) else {}
                sid = meta.get("series_id", "")
                delta = meta.get("delta")
                latest = meta.get("latest_value")
                if delta is None:
                    continue
                # Yield curve inversion (T10Y2Y < 0 = recession signal)
                if sid == "T10Y2Y" and isinstance(latest, (int, float)):
                    if latest < -0.5:
                        boost["yield_curve_inversion"] = -0.10  # Deep inversion
                    elif latest < 0:
                        boost["yield_curve_inversion"] = -0.05  # Mild inversion
                    elif latest > 0.5 and isinstance(delta, (int, float)) and delta > 0.1:
                        boost["yield_curve_steepening"] = 0.03  # Steepening = growth
                # 10Y yield spike = equity headwind
                if sid == "DGS10" and isinstance(delta, (int, float)):
                    if delta > 0.15:
                        boost["yield_spike_10y"] = -0.06  # Sharp rise = equity drag
                    elif delta < -0.15:
                        boost["yield_drop_10y"] = 0.04    # Sharp drop = equity support
                # Credit spreads widening (HY OAS)
                if sid == "BAMLH0A0HYM2" and isinstance(delta, (int, float)):
                    if delta > 0.50:
                        boost["credit_spread_blow"] = -0.08  # Spreads blowing out
                    elif delta > 0.20:
                        boost["credit_spread_widen"] = -0.04
                    elif delta < -0.30:
                        boost["credit_spread_tight"] = 0.04  # Spreads tightening
                # TED spread (interbank stress)
                if sid == "TEDRATE" and isinstance(delta, (int, float)):
                    if delta > 0.15:
                        boost["interbank_stress"] = -0.05

        # --- 13. Equity Index Momentum (SPY/QQQ from microstructure) ---
        micro_data = signals.get("market_microstructure", {})
        if isinstance(micro_data, dict):
            spy_data = micro_data.get("SPY", {})
            qqq_data = micro_data.get("QQQ", {})
            spy_vol = spy_data.get("sigma_daily_pct", 0) if isinstance(spy_data, dict) else 0
            qqq_vol = qqq_data.get("sigma_daily_pct", 0) if isinstance(qqq_data, dict) else 0
            avg_index_vol = (spy_vol + qqq_vol) / 2 if (spy_vol and qqq_vol) else spy_vol or qqq_vol
            if avg_index_vol > 3.0:
                boost["index_vol_crisis"] = -0.08  # Index vol >3% = crisis-level
            elif avg_index_vol > 2.0:
                boost["index_vol_elevated"] = -0.04  # Elevated broad market vol
            elif avg_index_vol < 0.8:
                boost["index_vol_calm"] = 0.03  # Low vol = favorable for entries

            # Credit ETF stress (HYG vol as real-time credit proxy)
            hyg_data = micro_data.get("HYG", {})
            hyg_vol = hyg_data.get("sigma_daily_pct", 0) if isinstance(hyg_data, dict) else 0
            if hyg_vol > 2.0:
                boost["hyg_credit_stress"] = -0.06  # Credit ETF vol spike

            # --- 14. Real-time Treasury Yield Curve (ETF vol as proxy) ---
            # SHY (2Y), IEF (10Y), TLT (20+Y) — rising vol = rate uncertainty
            shy_vol = (micro_data.get("SHY", {}) or {}).get("sigma_daily_pct", 0)
            ief_vol = (micro_data.get("IEF", {}) or {}).get("sigma_daily_pct", 0)
            tlt_vol = (micro_data.get("TLT", {}) or {}).get("sigma_daily_pct", 0)
            bond_vols = [v for v in [shy_vol, ief_vol, tlt_vol] if v > 0]
            if bond_vols:
                avg_bond_vol = sum(bond_vols) / len(bond_vols)
                if avg_bond_vol > 1.5:
                    boost["treasury_vol_crisis"] = -0.07  # Bond vol spike = rate shock
                elif avg_bond_vol > 1.0:
                    boost["treasury_vol_elevated"] = -0.03

            # --- 15. Futures vs Cash Spread (ES=F vs SPY divergence) ---
            es_data = micro_data.get("ES=F", {})
            if isinstance(es_data, dict) and isinstance(spy_data, dict):
                es_price = es_data.get("last_price", 0)
                spy_price = spy_data.get("last_price", 0)
                if es_price > 0 and spy_price > 0:
                    futures_premium_pct = ((es_price - spy_price) / spy_price) * 100
                    if futures_premium_pct < -0.3:
                        boost["futures_discount"] = -0.05  # Futures trading below cash = bearish
                    elif futures_premium_pct > 0.3:
                        boost["futures_premium"] = 0.03  # Futures above cash = bullish

            # --- 16. Small Cap vs Large Cap (IWM vs SPY relative vol) ---
            iwm_data = micro_data.get("IWM", {})
            iwm_vol = (iwm_data or {}).get("sigma_daily_pct", 0) if isinstance(iwm_data, dict) else 0
            if iwm_vol > 0 and spy_vol > 0:
                small_large_ratio = iwm_vol / max(spy_vol, 0.01)
                if small_large_ratio > 2.0:
                    boost["small_cap_stress"] = -0.04  # Small caps stressed = risk-off

        return boost

    def _idea_to_candidate(
        self,
        idea: Dict[str, Any],
        system_confidence: float,
        micro: Dict[str, Any],
        pol_scores: Optional[Dict[str, float]] = None,
        signal_boost: Optional[Dict[str, float]] = None,
        tw_confidence_mult: float = 1.0,
        tw_size_mult: float = 1.0,
        current_window: str = "unknown",
    ) -> Optional[Dict[str, Any]]:
        """Convert a single trade idea to a router candidate with time-window awareness."""
        symbol = idea.get("symbol")
        if not symbol:
            return None

        side = idea.get("side", "long")

        # Convert short ideas to inverse ETF longs (Alpaca paper blocks many shorts)
        if side == "short":
            inverse = SHORT_TO_INVERSE.get(symbol)
            if inverse is None:
                return None  # No inverse available, skip
            if inverse.get("note", "").startswith("No inverse"):
                return None  # Explicitly marked as unavailable
            symbol = inverse["symbol"]
            side = inverse["side"]
            idea = {**idea, "symbol": symbol, "side": side,
                    "reason": f"{idea.get('reason', '')} [via inverse ETF: {inverse.get('note', '')}]"}

        direction = "bullish" if side == "long" else "bearish"

        # Confidence score combines historical win rate with system confidence
        hist_wr = idea.get("historical_win_rate", 0.5)
        conf_score = round(hist_wr * min(system_confidence, 1.0), 3)

        # Boost confidence for symbols with politician alpha (whale trades)
        if pol_scores:
            pol_score = pol_scores.get(symbol, 0)
            if pol_score >= 7.0:
                conf_score = min(1.0, conf_score + 0.15)  # Strong whale signal
            elif pol_score >= 5.0:
                conf_score = min(1.0, conf_score + 0.08)  # Moderate whale signal
            elif pol_score >= 3.0:
                conf_score = min(1.0, conf_score + 0.03)  # Weak whale signal

        # Apply ALL bridge signal boosts/penalties
        boost_detail = {}
        if signal_boost:
            total_boost = sum(signal_boost.values())
            conf_score = round(min(1.0, max(0.05, conf_score + total_boost)), 3)
            boost_detail = signal_boost

        # --- Apply time window confidence multiplier ---
        # Opening: 0.90x (cautious), ORB: 1.05x (aggressive), Lunch: 0.80x (avoid),
        # Power hour: 1.05x (aggressive), Close: 0.85x (wind down)
        conf_score = round(min(1.0, max(0.05, conf_score * tw_confidence_mult)), 3)

        # Price hints from trade idea or microstructure
        sym_micro = micro.get(symbol, {})
        entry_price = idea.get("entry") or sym_micro.get("last_price")
        daily_vol = idea.get("daily_vol_pct") or sym_micro.get("sigma_daily_pct", 2.0)

        price_hints = {}
        if entry_price:
            price_hints["decision_price"] = entry_price
            price_hints["last_price"] = entry_price

        # Size multiplier based on confidence, then scaled by time window
        if conf_score >= 0.6:
            size_mult = 1.0
        elif conf_score >= 0.4:
            size_mult = 0.5
        else:
            size_mult = 0.25

        # Apply time window size multiplier
        # Opening: 0.60x, ORB: 1.0x, Lunch: 0.50x, Power hour: 1.0x, Close: 0.0x
        size_mult = round(size_mult * tw_size_mult, 3)

        # Determine holding period based on window
        holding_period = idea.get("holding_period", "day")
        if current_window in ("opening_amateur_hour_cooldown", "opening_range_breakout_window"):
            holding_period = "intraday_scalp"
        elif current_window == "power_hour":
            holding_period = "intraday_momentum"
        elif current_window == "lunch_lull":
            holding_period = "swing"  # Only high-conviction swing trades during lunch

        return {
            "candidate_id": f"ta-{symbol.lower()}-{uuid.uuid4().hex[:8]}",
            "symbol": symbol,
            "direction": direction,
            "strategy_style": idea.get("strategy_style", "regime_playbook"),
            "template_key": f"regime_{side}_{symbol.lower()}",
            "instrument_types": ["equity"],
            "confidence_score": conf_score,
            "size_multiplier_suggestion": size_mult,
            "status": "eligible",
            "block_reasons": [],
            "reason": idea.get("reason", ""),
            "price_hints": price_hints,
            "execution_constraints": {
                "limit_price_fallback": entry_price,
                "manual_review_required": False,
            },
            "fill_sim_assessment": {
                "fill_feasibility_score": 0.8 if entry_price else 0.5,
                "expected_slippage_bps": max(daily_vol * 2, 5),
                "reject_risk_probability": 0.05,
                "do_not_route_even_in_shadow": False,
            },
            "holding_period": holding_period,
            "metadata": {
                "source": "trade_analysis_engine",
                "historical_win_rate": hist_wr,
                "risk_reward": idea.get("risk_reward"),
                "target": idea.get("target"),
                "stop": idea.get("stop"),
                "signal_boost_detail": boost_detail,
                "signal_boost_total": round(sum(boost_detail.values()), 3) if boost_detail else 0,
                "time_window": current_window,
                "tw_confidence_mult": tw_confidence_mult,
                "tw_size_mult": tw_size_mult,
            },
        }

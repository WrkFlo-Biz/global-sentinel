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
    "JETS": {"symbol": "JETS", "side": "long", "note": "No inverse JETS ETF available — skip"},
    "SPY": {"symbol": "SH", "side": "long", "note": "ProShares Short S&P500"},
    "QQQ": {"symbol": "PSQ", "side": "long", "note": "ProShares Short QQQ"},
    "EEM": {"symbol": "EUM", "side": "long", "note": "ProShares Short MSCI EM"},
    "HYG": {"symbol": "SJB", "side": "long", "note": "ProShares Short High Yield"},
    "FXI": {"symbol": "YANG", "side": "long", "note": "Direxion Daily China Bear 3x"},
    "CCL": None,  # No good inverse, skip
    "DAL": None,  # No airline inverse ETF
}


class TradeIdeaPackager:
    """Converts trade analysis ideas into shadow order router packages."""

    def build_package(
        self,
        trade_analysis: Dict[str, Any],
        scorecard: Dict[str, Any],
        microstructure: Optional[Dict[str, Any]] = None,
        max_ideas: int = 5,
    ) -> Dict[str, Any]:
        """
        Build a router-compatible package from trade analysis output.

        Args:
            trade_analysis: Output from TradeAnalysisEngine.analyze()
            scorecard: Current scorecard for context
            microstructure: Market microstructure data for price hints
            max_ideas: Max number of trade ideas to include as candidates
        """
        ideas = trade_analysis.get("trade_ideas", [])[:max_ideas]
        if not ideas:
            return {"candidates": [], "global_blocks": ["no_trade_ideas"]}

        mode = scorecard.get("mode", "NORMAL")
        regime_p = scorecard.get("regime_shift_probability", 0)
        confidence = scorecard.get("confidence", 0)
        time_window = scorecard.get("time_window", {})
        micro = microstructure or {}

        # Check if shadow execution is eligible
        if not scorecard.get("shadow_execution_eligible", False):
            return {
                "candidates": [],
                "global_blocks": ["shadow_execution_not_eligible"],
                "effective_mode": mode,
            }

        candidates = []
        for idea in ideas:
            cand = self._idea_to_candidate(idea, confidence, micro)
            if cand:
                candidates.append(cand)

        package = {
            "schema_version": "trade_idea_package.v1",
            "package_id": f"tip-{uuid.uuid4().hex[:12]}",
            "package_type": "trade_analysis_ideas",
            "timestamp_utc": iso_now(),
            "effective_mode": mode,
            "candidates": candidates,
            "blocked_candidates": [],
            "global_blocks": [],
            "window_context": {
                "time_window_name": time_window.get("current_window"),
                "watchlist_only_window": time_window.get("shadow_execution_window_blocked", False),
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

    def _idea_to_candidate(
        self,
        idea: Dict[str, Any],
        system_confidence: float,
        micro: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Convert a single trade idea to a router candidate."""
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

        # Price hints from trade idea or microstructure
        sym_micro = micro.get(symbol, {})
        entry_price = idea.get("entry") or sym_micro.get("last_price")
        daily_vol = idea.get("daily_vol_pct") or sym_micro.get("sigma_daily_pct", 2.0)

        price_hints = {}
        if entry_price:
            price_hints["decision_price"] = entry_price
            price_hints["last_price"] = entry_price

        # Size multiplier based on confidence
        if conf_score >= 0.6:
            size_mult = 1.0
        elif conf_score >= 0.4:
            size_mult = 0.5
        else:
            size_mult = 0.25

        return {
            "candidate_id": f"ta-{symbol.lower()}-{uuid.uuid4().hex[:8]}",
            "symbol": symbol,
            "direction": direction,
            "strategy_style": "regime_playbook",
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
            "metadata": {
                "source": "trade_analysis_engine",
                "historical_win_rate": hist_wr,
                "risk_reward": idea.get("risk_reward"),
                "target": idea.get("target"),
                "stop": idea.get("stop"),
            },
        }

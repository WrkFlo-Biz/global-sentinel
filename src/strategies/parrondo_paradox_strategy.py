"""Parrondo's Paradox strategy — combine individually losing sub-strategies.

Inspired by Instagram reel from @Nipsey (Tab 11):
  "What if I told you that playing two losing games could actually make you
   rich? Discover the mind-bending math of Parrondo's Paradox"

The paradox shows that alternating between two individually losing strategies
can produce a net winning outcome when the strategies interact through shared
capital state.  This module implements a capital-dependent switching mechanism:

  Strategy A: Mean-reversion (slightly negative edge alone)
  Strategy B: Momentum breakout (slightly negative edge alone)
  Switch rule: Use A when recent P&L is positive (capital "high"),
               use B when recent P&L is negative (capital "low").

The interaction between capital-dependent switching and the two strategy
regimes creates a ratchet effect analogous to Parrondo's original games.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sub-strategy watchlists
MEAN_REVERSION_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT"]
MOMENTUM_SYMBOLS = ["NVDA", "TSLA", "AMD", "META", "COIN", "MARA"]

PARAMS: dict[str, Any] = {
    # Mean-reversion (Strategy A) params
    "mr_rsi_oversold": 32,
    "mr_rsi_overbought": 68,
    "mr_min_drop_pct": -1.5,
    # Momentum (Strategy B) params
    "mo_min_breakout_pct": 2.0,
    "mo_min_relative_volume": 1.8,
    # Parrondo switching
    "capital_lookback_days": 5,
    "capital_switch_threshold": 0.0,
    # General
    "min_confidence": 0.42,
    "base_notional_usd": 800.0,
    "max_candidates": 4,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _rsi(sym_data: dict[str, Any]) -> float:
    return _safe_float(sym_data.get("rsi") or sym_data.get("rsi_14"), 50.0)


def _pct_change(sym_data: dict[str, Any]) -> float:
    if "change_pct" in sym_data:
        return _safe_float(sym_data["change_pct"])
    price = _safe_float(sym_data.get("price"))
    prev = _safe_float(sym_data.get("prior_close"))
    if price > 0 and prev > 0:
        return ((price - prev) / prev) * 100.0
    return 0.0


def _relative_volume(sym_data: dict[str, Any]) -> float:
    vol = _safe_float(sym_data.get("volume"))
    avg = _safe_float(sym_data.get("avg_volume"))
    if vol > 0 and avg > 0:
        return vol / avg
    return _safe_float(sym_data.get("relative_volume"), 1.0)


class ParrondoParadoxStrategy:
    """Alternate mean-reversion and momentum based on capital state."""

    def __init__(self, params: dict[str, Any] | None = None):
        self._params = {**PARAMS, **(params or {})}
        self._recent_pnl: float = 0.0

    def set_recent_pnl(self, pnl: float) -> None:
        """Feed recent P&L so the switching logic knows capital state."""
        self._recent_pnl = pnl

    def _strategy_a_candidates(
        self, market_data: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Mean-reversion: buy oversold dips on liquid names."""
        ideas: list[dict[str, Any]] = []
        for symbol in MEAN_REVERSION_SYMBOLS:
            sd = market_data.get(symbol)
            if not sd:
                continue
            rsi = _rsi(sd)
            chg = _pct_change(sd)
            if rsi > self._params["mr_rsi_oversold"]:
                continue
            if chg > self._params["mr_min_drop_pct"]:
                continue
            confidence = min(0.85, 0.35 + (self._params["mr_rsi_oversold"] - rsi) * 0.012 + abs(chg) * 0.04)
            ideas.append({
                "strategy": "parrondo_paradox",
                "symbol": symbol,
                "direction": "long",
                "confidence_score": round(confidence, 3),
                "confidence": round(confidence, 3),
                "holding_period": "intraday_momentum",
                "entry_signal": f"Parrondo mean-reversion on {symbol}",
                "rationale": f"Parrondo A: RSI={rsi:.0f} oversold, day chg {chg:+.1f}%",
                "notional_usd": self._params["base_notional_usd"],
                "sub_strategy": "A_mean_reversion",
                "metadata": {
                    "source": "instagram_scientific_nipsey_parrondo",
                    "sub_strategy": "A_mean_reversion",
                    "rsi": round(rsi, 2),
                    "change_pct": round(chg, 2),
                },
            })
        return ideas

    def _strategy_b_candidates(
        self, market_data: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Momentum breakout: buy high-volume breakouts."""
        ideas: list[dict[str, Any]] = []
        for symbol in MOMENTUM_SYMBOLS:
            sd = market_data.get(symbol)
            if not sd:
                continue
            chg = _pct_change(sd)
            rvol = _relative_volume(sd)
            if chg < self._params["mo_min_breakout_pct"]:
                continue
            if rvol < self._params["mo_min_relative_volume"]:
                continue
            confidence = min(0.85, 0.32 + chg * 0.035 + (rvol - 1.0) * 0.08)
            ideas.append({
                "strategy": "parrondo_paradox",
                "symbol": symbol,
                "direction": "long",
                "confidence_score": round(confidence, 3),
                "confidence": round(confidence, 3),
                "holding_period": "day",
                "entry_signal": f"Parrondo momentum on {symbol}",
                "rationale": f"Parrondo B: breakout +{chg:.1f}%, rvol {rvol:.1f}x",
                "notional_usd": self._params["base_notional_usd"],
                "sub_strategy": "B_momentum",
                "metadata": {
                    "source": "instagram_scientific_nipsey_parrondo",
                    "sub_strategy": "B_momentum",
                    "change_pct": round(chg, 2),
                    "relative_volume": round(rvol, 2),
                },
            })
        return ideas

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not market_data:
            return []

        # Parrondo switch: pick strategy based on capital state
        threshold = self._params["capital_switch_threshold"]
        if self._recent_pnl >= threshold:
            # Capital is "high" → use mean-reversion (Strategy A)
            active = "A"
            candidates = self._strategy_a_candidates(market_data)
        else:
            # Capital is "low" → use momentum (Strategy B)
            active = "B"
            candidates = self._strategy_b_candidates(market_data)

        # Apply scorecard boost for geopolitical context
        if scorecard:
            geo = _safe_float(scorecard.get("component_scores", {}).get("geopolitical_tension"))
            vix = _safe_float(scorecard.get("component_scores", {}).get("volatility_regime"))
            for c in candidates:
                if geo > 0.4 and active == "B":
                    c["confidence"] = min(0.9, c["confidence"] + 0.06)
                if vix > 0.5 and active == "A":
                    c["confidence"] = min(0.9, c["confidence"] + 0.05)
                c["confidence"] = round(c["confidence"], 3)
                c["confidence_score"] = c["confidence"]

        # Filter and sort
        min_conf = self._params["min_confidence"]
        candidates = [c for c in candidates if c["confidence"] >= min_conf]
        candidates.sort(key=lambda x: x["confidence"], reverse=True)

        for c in candidates:
            c["parrondo_state"] = f"capital_{'high' if active == 'A' else 'low'}"
            c["active_sub"] = active

        logger.info(
            "Parrondo scan: state=%s, candidates=%d, recent_pnl=%.2f",
            active, len(candidates), self._recent_pnl,
        )
        return candidates[: self._params["max_candidates"]]


def evaluate_parrondo_paradox(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    recent_pnl: float | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Strategy-engine adapter for the Parrondo switching strategy."""
    params = dict((strat or {}).get("params", {}))
    strategy = ParrondoParadoxStrategy(params=params)
    if recent_pnl is None:
        recent_pnl = _safe_float(params.get("recent_pnl"))
    strategy.set_recent_pnl(recent_pnl or 0.0)
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

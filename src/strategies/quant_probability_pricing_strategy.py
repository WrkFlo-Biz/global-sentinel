"""Quant probability-based options pricing strategy.

Inspired by Instagram reels:
  - @advicefromtraders (Tab 1): "How Quants Use Probability to Crack Option
    Pricing (MIT Lecture)" — Source: MIT OpenCourseWare
  - @Raghee Horner (Tab 19): "This equation moves BILLIONS of dollars every
    single day" — Black-Scholes and implied volatility surface analysis
  - @julia (Tab 13): "How Traders Predict The Future Price of an Index"

This strategy detects mispriced options by comparing:
  1. Implied volatility (market's price) vs realized volatility (actual)
  2. Put-call parity violations
  3. IV skew anomalies across strikes
  4. Term structure inversions

When IV significantly exceeds realized vol, sell premium (short vol).
When realized vol exceeds IV, buy options (long vol).
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

VOL_SURFACE_WATCHLIST = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA",
    "GLD", "USO", "XLE", "META", "AMZN",
]

PARAMS: dict[str, Any] = {
    # IV vs RV thresholds
    "iv_rv_ratio_sell_threshold": 1.35,   # IV 35%+ above RV → sell premium
    "iv_rv_ratio_buy_threshold": 0.78,    # IV 22%+ below RV → buy options
    # Skew thresholds
    "skew_anomaly_threshold": 0.15,       # 15%+ skew deviation from median
    # Term structure
    "term_inversion_threshold": -0.05,    # Inverted term structure signal
    # Position sizing
    "base_notional_usd": 600.0,
    "min_confidence": 0.45,
    "max_candidates": 4,
    # Risk
    "max_iv_percentile": 95,
    "min_days_to_expiry": 3,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _iv_rv_ratio(sym_data: dict[str, Any]) -> float | None:
    """Compute implied vol / realized vol ratio."""
    iv = _safe_float(sym_data.get("implied_volatility") or sym_data.get("iv"))
    rv = _safe_float(sym_data.get("realized_volatility") or sym_data.get("hv_20")
                     or sym_data.get("historical_volatility"))
    if iv > 0 and rv > 0:
        return iv / rv
    return None


def _iv_percentile(sym_data: dict[str, Any]) -> float:
    """Get IV percentile (0-100)."""
    return _safe_float(sym_data.get("iv_percentile") or sym_data.get("iv_rank"), 50.0)


def _skew_score(sym_data: dict[str, Any]) -> float:
    """Measure put-call skew anomaly."""
    put_iv = _safe_float(sym_data.get("put_iv") or sym_data.get("put_implied_vol"))
    call_iv = _safe_float(sym_data.get("call_iv") or sym_data.get("call_implied_vol"))
    if put_iv > 0 and call_iv > 0:
        skew = (put_iv - call_iv) / call_iv
        return skew
    return 0.0


def _term_structure_slope(sym_data: dict[str, Any]) -> float:
    """Measure IV term structure slope (front vs back)."""
    front_iv = _safe_float(sym_data.get("front_month_iv") or sym_data.get("near_iv"))
    back_iv = _safe_float(sym_data.get("back_month_iv") or sym_data.get("far_iv"))
    if front_iv > 0 and back_iv > 0:
        return (back_iv - front_iv) / front_iv
    return 0.0


def _black_scholes_edge(sym_data: dict[str, Any]) -> float:
    """Simplified theoretical edge estimate.

    Uses the insight from the MIT lecture: when IV significantly diverges
    from statistical expectations, there's a probability edge in the
    options pricing.
    """
    iv = _safe_float(sym_data.get("implied_volatility"))
    rv = _safe_float(sym_data.get("realized_volatility"))
    if iv <= 0 or rv <= 0:
        return 0.0
    # Approximate edge as the vol risk premium
    edge = (iv - rv) / iv
    return edge


class QuantProbabilityPricingStrategy:
    """Exploit IV-RV divergence and vol surface anomalies."""

    def __init__(self, params: dict[str, Any] | None = None):
        self._params = {**PARAMS, **(params or {})}

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not market_data:
            return []

        ideas: list[dict[str, Any]] = []

        for symbol in VOL_SURFACE_WATCHLIST:
            sd = market_data.get(symbol)
            if not sd:
                continue

            ratio = _iv_rv_ratio(sd)
            if ratio is None:
                continue

            iv_pct = _iv_percentile(sd)
            skew = _skew_score(sd)
            term_slope = _term_structure_slope(sd)
            bs_edge = _black_scholes_edge(sd)

            # Decision: sell premium when IV >> RV
            if ratio >= self._params["iv_rv_ratio_sell_threshold"]:
                direction = "short_vol"
                confidence = 0.38
                confidence += min((ratio - 1.0) * 0.18, 0.25)
                confidence += min(iv_pct / 100 * 0.12, 0.10)
                if abs(skew) > self._params["skew_anomaly_threshold"]:
                    confidence += 0.06
                if term_slope < self._params["term_inversion_threshold"]:
                    confidence += 0.05
                rationale = (
                    f"Sell premium: IV/RV={ratio:.2f} (overpriced), "
                    f"IV pctl={iv_pct:.0f}%, edge={bs_edge:+.1%}"
                )
                holding = "swing"

            # Decision: buy options when RV >> IV
            elif ratio <= self._params["iv_rv_ratio_buy_threshold"]:
                direction = "long_vol"
                confidence = 0.36
                confidence += min((1.0 - ratio) * 0.22, 0.25)
                if term_slope > 0.10:
                    confidence += 0.05
                rationale = (
                    f"Buy vol: IV/RV={ratio:.2f} (underpriced), "
                    f"edge={bs_edge:+.1%}"
                )
                holding = "day"
            else:
                continue

            # Block excessively high IV (tail risk for short vol)
            if direction == "short_vol" and iv_pct > self._params["max_iv_percentile"]:
                logger.debug("Skipping %s: IV pctl %s too high for short vol", symbol, iv_pct)
                continue

            # Scorecard adjustments
            if scorecard:
                vix = _safe_float(scorecard.get("component_scores", {}).get("volatility_regime"))
                geo = _safe_float(scorecard.get("component_scores", {}).get("geopolitical_tension"))
                # Don't sell vol during geopolitical crisis
                if direction == "short_vol" and geo > 0.6:
                    confidence -= 0.12
                # Buy vol is better during elevated tension
                if direction == "long_vol" and geo > 0.4:
                    confidence += 0.06

            confidence = round(min(0.90, confidence), 3)
            if confidence < self._params["min_confidence"]:
                continue

            ideas.append({
                "strategy": "quant_probability_pricing",
                "symbol": symbol,
                "direction": direction,
                "confidence_score": confidence,
                "confidence": confidence,
                "holding_period": holding,
                "entry_signal": f"Quant probability {direction} on {symbol}",
                "rationale": rationale,
                "notional_usd": self._params["base_notional_usd"],
                "vol_metrics": {
                    "iv_rv_ratio": round(ratio, 3),
                    "iv_percentile": round(iv_pct, 1),
                    "skew": round(skew, 4),
                    "term_slope": round(term_slope, 4),
                    "bs_edge": round(bs_edge, 4),
                },
                "metadata": {
                    "source": "instagram_quant_probability_and_black_scholes",
                    "iv_rv_ratio": round(ratio, 3),
                    "iv_percentile": round(iv_pct, 1),
                    "skew": round(skew, 4),
                    "term_slope": round(term_slope, 4),
                    "bs_edge": round(bs_edge, 4),
                },
            })

        ideas.sort(key=lambda x: x["confidence"], reverse=True)
        logger.info("Quant probability scan: %d ideas", len(ideas))
        return ideas[: self._params["max_candidates"]]


def evaluate_quant_probability_pricing(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Strategy-engine adapter for the vol-pricing strategy."""
    params = dict((strat or {}).get("params", {}))
    strategy = QuantProbabilityPricingStrategy(params=params)
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

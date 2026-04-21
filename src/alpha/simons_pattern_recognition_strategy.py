"""Simons Pattern Recognition Strategy — Renaissance Technologies Inspired.

Based on @verbal.is.here's reel about Jim Simons' mathematical approach
to market pattern recognition, referencing topology, geometric signal
processing, and the Medallion Fund's data-driven methodology.

Core Philosophy (Jim Simons):
  "We don't start with models. We start with data. We don't have any
   preconceived notions. We look for things that can be replicated
   thousands of times."

Strategy Logic — Adapted for Global Sentinel:
  Renaissance's edge comes from 5 key principles:
  1. PATTERN ANOMALY DETECTION — Find non-random patterns in price data
     that repeat with statistical significance
  2. MULTI-SIGNAL FUSION — Combine unconventional data (weather, shipping,
     sentiment, geopolitical) with price data for richer signals
  3. MEAN REVERSION AT MICRO SCALE — Most patterns are mean-reverting
     at short timescales (hours to days)
  4. MOMENTUM AT MACRO SCALE — Trends persist at longer timescales
     (weeks to months)
  5. NEVER OVERRIDE THE COMPUTER — Systematic execution, no human bias

GS Implementation:
  - Combines ALL 25+ bridge signals into a composite anomaly score
  - Detects statistical anomalies in price behavior vs regime expectations
  - Mean-reversion on intraday dislocations (price moved too far vs signals)
  - Momentum continuation when signals confirm the trend
  - Fully systematic — no discretionary override

The key Simons insight for GS: Your geopolitical signals (GDELT, maritime,
chokepoint scoring) ARE the unconventional data that Renaissance uses.
Weather, shipping, political signals — GS already collects these. This
strategy fuses them into a single anomaly detection framework.

Reference:
  - Instagram: @verbal.is.here reel DTAIbagDMr7 (7,837 likes)
  - Jim Simons / Renaissance Technologies / Medallion Fund
  - Hidden Markov Models, Baum-Welch algorithm, topological pattern recognition
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Anomaly detection thresholds
PARAMS = {
    # Signal-price divergence thresholds
    "divergence_min": 0.3,       # Min divergence score to trigger
    "divergence_strong": 0.6,    # Strong divergence — high confidence
    # Mean reversion parameters (intraday)
    "mean_rev_threshold_pct": 2.0,  # Price moved 2%+ against signal → revert
    "mean_rev_max_pct": 5.0,        # Beyond 5% = real trend, don't fade
    # Momentum parameters (multi-day)
    "momentum_min_pct": 1.5,     # Minimum move for momentum signal
    "momentum_signal_confirm": 0.5,  # Min signal score to confirm momentum
    # Position sizing
    "base_notional": 170,        # 20% of $858
    "max_positions": 3,
    # Confidence scaling
    "base_confidence": 0.45,
    "max_confidence": 0.85,
    "signal_confidence_weight": 0.3,  # How much signals boost confidence
}

# Multi-signal fusion weights (Renaissance-style unconventional data fusion)
# These map GS bridge signals to pattern recognition weights
SIGNAL_WEIGHTS = {
    "geopolitical_tension": 0.20,      # GDELT conflict events
    "commodity_shock": 0.15,           # Oil/energy disruption
    "market_volatility": 0.15,         # VIX regime
    "chokepoint_risk": 0.12,           # Maritime chokepoint scoring
    "currency_stress": 0.08,           # FX dislocations
    "politician_alpha": 0.08,          # Congressional insider signals
    "policy_signals": 0.07,            # Fed/WH/OFAC
    "commodity_inventory": 0.05,       # EIA storage data
    "yield_curve": 0.05,              # Rate structure
    "credit_spread": 0.03,            # Credit stress
    "liquidity_stress": 0.02,         # Market microstructure
}

# Tickers and their signal sensitivity profiles
# Each ticker maps to which signals matter most for it
TICKER_SIGNAL_MAP = {
    # Energy — driven by geopolitical + commodity signals
    "USO": {"geopolitical_tension": 0.3, "commodity_shock": 0.4, "chokepoint_risk": 0.3},
    "XLE": {"geopolitical_tension": 0.3, "commodity_shock": 0.3, "market_volatility": 0.2, "chokepoint_risk": 0.2},
    "OXY": {"commodity_shock": 0.4, "geopolitical_tension": 0.3, "chokepoint_risk": 0.3},
    "XOP": {"commodity_shock": 0.4, "geopolitical_tension": 0.3, "chokepoint_risk": 0.3},
    # Defense — driven by geopolitical tension
    "LMT": {"geopolitical_tension": 0.5, "policy_signals": 0.3, "politician_alpha": 0.2},
    "RTX": {"geopolitical_tension": 0.5, "policy_signals": 0.3, "politician_alpha": 0.2},
    "NOC": {"geopolitical_tension": 0.5, "policy_signals": 0.3, "politician_alpha": 0.2},
    # Gold — driven by volatility + geo tension + currency
    "GLD": {"market_volatility": 0.3, "geopolitical_tension": 0.3, "currency_stress": 0.2, "yield_curve": 0.2},
    "GDX": {"market_volatility": 0.3, "geopolitical_tension": 0.3, "currency_stress": 0.2, "commodity_shock": 0.2},
    # Airlines — inverse of energy
    "UAL": {"commodity_shock": 0.4, "chokepoint_risk": 0.3, "geopolitical_tension": 0.3},
    "DAL": {"commodity_shock": 0.4, "chokepoint_risk": 0.3, "geopolitical_tension": 0.3},
    "JETS": {"commodity_shock": 0.4, "chokepoint_risk": 0.3, "geopolitical_tension": 0.3},
    # Broad market — driven by vol + policy + macro
    "SPY": {"market_volatility": 0.3, "policy_signals": 0.2, "yield_curve": 0.2, "credit_spread": 0.15, "geopolitical_tension": 0.15},
    "QQQ": {"market_volatility": 0.3, "policy_signals": 0.2, "yield_curve": 0.2, "liquidity_stress": 0.15, "geopolitical_tension": 0.15},
    # Tech momentum — driven by vol + liquidity
    "NVDA": {"market_volatility": 0.3, "liquidity_stress": 0.2, "policy_signals": 0.2, "geopolitical_tension": 0.15, "yield_curve": 0.15},
    "SOXL": {"market_volatility": 0.35, "liquidity_stress": 0.2, "geopolitical_tension": 0.2, "policy_signals": 0.15, "commodity_shock": 0.1},
    "TQQQ": {"market_volatility": 0.35, "policy_signals": 0.2, "yield_curve": 0.2, "geopolitical_tension": 0.15, "credit_spread": 0.1},
    # Shipping — driven by chokepoint + commodity
    "STNG": {"chokepoint_risk": 0.4, "commodity_shock": 0.3, "geopolitical_tension": 0.3},
    "ZIM": {"chokepoint_risk": 0.4, "commodity_shock": 0.3, "geopolitical_tension": 0.3},
    # EM — driven by currency + commodity + geo
    "EEM": {"currency_stress": 0.3, "commodity_shock": 0.3, "geopolitical_tension": 0.2, "policy_signals": 0.2},
    "FXI": {"currency_stress": 0.3, "commodity_shock": 0.3, "geopolitical_tension": 0.2, "policy_signals": 0.2},
}


def compute_composite_signal(scorecard: dict, ticker_weights: dict) -> float:
    """Compute weighted composite signal score for a ticker.

    Combines multiple GS bridge signals into a single score based on
    each ticker's sensitivity profile. This is the Renaissance-style
    multi-signal fusion.
    """
    component_scores = scorecard.get("component_scores", {})
    chokepoint = scorecard.get("chokepoint_risk", {})

    score = 0.0
    total_weight = 0.0

    for signal_name, weight in ticker_weights.items():
        if signal_name == "chokepoint_risk":
            # Average across chokepoints
            cp_values = list(chokepoint.values()) if chokepoint else [0]
            value = sum(cp_values) / len(cp_values) if cp_values else 0
        else:
            value = component_scores.get(signal_name, 0)

        score += value * weight
        total_weight += weight

    return score / max(total_weight, 0.01)


def detect_divergence(
    signal_score: float,
    price_change_pct: float,
    ticker_weights: dict,
) -> dict[str, Any] | None:
    """Detect signal-price divergence — the core Simons insight.

    When signals say one thing and price does another, there's an
    anomaly worth trading. Signals derived from unconventional data
    (geopolitical, shipping, weather) often lead price by hours/days.

    Returns divergence info if detected, None otherwise.
    """
    # Determine expected price direction from signals
    # High signal score = bearish environment for risk assets
    is_risk_ticker = any(
        k in ticker_weights for k in ("commodity_shock", "geopolitical_tension", "chokepoint_risk")
    )

    # For energy/defense: high geo tension = bullish
    # For airlines/broad market: high geo tension = bearish
    energy_defense = any(k in ("commodity_shock", "chokepoint_risk") for k in ticker_weights if ticker_weights[k] > 0.25)

    if energy_defense:
        # Energy: high signal = price should go UP
        signal_direction = 1.0 if signal_score > 0.5 else -1.0
    else:
        # Risk assets: high signal = price should go DOWN
        signal_direction = -1.0 if signal_score > 0.5 else 1.0

    price_direction = 1.0 if price_change_pct > 0 else -1.0

    # Divergence: signal and price disagree
    if signal_direction != price_direction and abs(price_change_pct) >= PARAMS["divergence_min"]:
        divergence_strength = abs(price_change_pct) * signal_score
        return {
            "type": "mean_reversion" if abs(price_change_pct) <= PARAMS["mean_rev_max_pct"] else "trend_break",
            "signal_direction": "bullish" if signal_direction > 0 else "bearish",
            "price_direction": "up" if price_change_pct > 0 else "down",
            "divergence_strength": round(divergence_strength, 3),
            "signal_score": round(signal_score, 3),
            "trade_direction": "long" if signal_direction > 0 else "short",
        }

    # Confirmation: signal and price agree AND signal is strong
    if signal_direction == price_direction and signal_score > PARAMS["momentum_signal_confirm"] and abs(price_change_pct) >= PARAMS["momentum_min_pct"]:
        return {
            "type": "momentum_confirmation",
            "signal_direction": "bullish" if signal_direction > 0 else "bearish",
            "price_direction": "up" if price_change_pct > 0 else "down",
            "divergence_strength": round(abs(price_change_pct) * signal_score, 3),
            "signal_score": round(signal_score, 3),
            "trade_direction": "long" if price_change_pct > 0 else "short",
        }

    return None


class SimonsPatternRecognitionStrategy:
    """Jim Simons-inspired pattern recognition using GS signal fusion.

    Combines multiple unconventional data sources (geopolitical, shipping,
    commodity, policy) into a composite signal, then detects anomalies
    where price behavior diverges from what signals predict.
    """

    def __init__(self, params: dict | None = None):
        self._params = {**PARAMS, **(params or {})}

    def scan_watchlist(
        self,
        market_data: dict | None = None,
        scorecard: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Scan all tickers for pattern anomalies."""
        if market_data is None or scorecard is None:
            return []

        ideas = []

        for symbol, ticker_weights in TICKER_SIGNAL_MAP.items():
            sym_data = market_data.get(symbol, {})
            price = sym_data.get("price", 0)
            change_pct = sym_data.get("change_pct", 0)

            if price <= 0:
                continue

            # Step 1: Compute composite signal score
            signal_score = compute_composite_signal(scorecard, ticker_weights)

            # Step 2: Detect signal-price divergence
            divergence = detect_divergence(signal_score, change_pct, ticker_weights)

            if divergence is None:
                continue

            # Step 3: Compute confidence
            confidence = self._params["base_confidence"]
            confidence += divergence["divergence_strength"] * self._params["signal_confidence_weight"]
            confidence += signal_score * 0.15  # Boost from signal strength

            if divergence["type"] == "momentum_confirmation":
                confidence += 0.05  # Small boost for confirmation trades

            confidence = min(confidence, self._params["max_confidence"])

            if confidence < 0.40:
                continue

            # Step 4: Risk management
            if divergence["type"] == "mean_reversion":
                stop_pct = -min(abs(change_pct) + 1.0, 4.0)
                target_pct = min(abs(change_pct) * 1.5, 6.0)
            else:  # momentum
                stop_pct = -min(abs(change_pct) * 0.4, 3.0)
                target_pct = min(abs(change_pct) * 2.0, 8.0)

            ideas.append({
                "strategy": "simons_pattern_recognition",
                "symbol": symbol,
                "direction": divergence["trade_direction"],
                "notional_usd": self._params["base_notional"],
                "confidence_score": round(confidence, 3),
                "confidence": round(confidence, 3),
                "stop_loss_pct": round(stop_pct, 2),
                "take_profit_pct": round(target_pct, 2),
                "tier": "tier_1",
                "tier_size_multiplier": 1.0,
                "entry_signal": (
                    f"Simons {divergence['type']}: signals={divergence['signal_direction']} "
                    f"vs price={divergence['price_direction']} on {symbol} "
                    f"(composite={signal_score:.2f}, div={divergence['divergence_strength']:.2f})"
                ),
                "metadata": {
                    "source": "verbal.is.here_simons_reel",
                    "framework": "Renaissance pattern recognition",
                    "divergence_type": divergence["type"],
                    "composite_signal": round(signal_score, 3),
                    "divergence_strength": divergence["divergence_strength"],
                },
            })

        # Sort by confidence and limit positions
        ideas.sort(key=lambda x: x["confidence_score"], reverse=True)
        return ideas[:self._params["max_positions"] * 2]


# Strategy engine integration
def evaluate_simons_pattern(
    strat: dict,
    scorecard: dict | None = None,
    bridge_results: dict | None = None,
    market_data: dict | None = None,
) -> list[dict[str, Any]]:
    """Entry point for strategy engine integration."""
    strategy = SimonsPatternRecognitionStrategy()
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

"""AMD Power of 3 Strategy — ICT Accumulation/Manipulation/Distribution.

Based on @tomcampcoaching's "ultimate cheat code to understanding AMD" —
the ICT Power of 3 framework by Michael J. Huddleston.

Strategy Logic:
  Markets move in 3 phases daily:
  1. ACCUMULATION — Smart money quietly builds positions in a tight range
     (typically Asian session / pre-market). Low volume, sideways action.
  2. MANIPULATION — Sharp false move to hunt stop losses and grab liquidity.
     Fake breakout below range (for bullish setup) or above (bearish).
     This is the TRAP that retail traders fall for.
  3. DISTRIBUTION — The REAL move. Smart money pushes price in the intended
     direction. This is where the money is made.

Entry Rules:
  - Identify the accumulation range (pre-market or Asian session range)
  - Wait for manipulation (stop hunt / false breakout beyond the range)
  - Enter AFTER manipulation reverses back into the range
  - Confirmation: price closes back inside range after the stop hunt
  - Direction: opposite of the manipulation move
    (manipulation down → enter long, manipulation up → enter short)

Exit Rules:
  - Stop loss: at the extreme of the manipulation phase
  - Take profit: 1:2 or 1:3 risk/reward ratio
  - Time-based: must see distribution within 2-4 hours of entry

Integration with Global Sentinel:
  - Uses GS market data to detect range/breakout/reversal patterns
  - Enhanced by GS geopolitical signals (war headlines create manipulation events)
  - Tier 2 allocation — strong ICT framework, needs live validation

Reference:
  - Instagram: @tomcampcoaching reel DWMBW_KiLTC
  - ICT Power of 3 / AMD framework by Michael J. Huddleston
  - https://innercircletrader.net/tutorials/ict-power-of-3/
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# AMD Phase Detection Parameters
PARAMS = {
    # Accumulation detection
    "accumulation_range_max_pct": 1.0,   # Range < 1% = accumulation
    "accumulation_min_bars": 3,           # Min bars in range before it counts
    # Manipulation detection
    "manipulation_breakout_min_pct": 0.5, # Min false breakout beyond range
    "manipulation_breakout_max_pct": 3.0, # Max before it's a real breakout
    "manipulation_reversal_pct": 0.3,     # Must reverse at least 0.3% back into range
    # Distribution (the trade)
    "distribution_target_rr": 2.0,        # Risk/reward ratio for take profit
    "distribution_max_hours": 4,          # Max hours to hold for distribution
    # Risk management
    "stop_at_manipulation_extreme": True,  # Stop at the false breakout high/low
    "max_risk_pct": 2.0,                  # Max 2% risk per trade
    # Time windows (ET) — AMD works best at specific times
    "accumulation_window_start": 4,       # 4 AM ET (pre-market / Asian session)
    "accumulation_window_end": 9,         # 9 AM ET
    "manipulation_window_start": 9,       # 9 AM ET (market open manipulation)
    "manipulation_window_end": 10,        # 10 AM ET
    "distribution_window_start": 10,      # 10 AM ET
    "distribution_window_end": 14,        # 2 PM ET
}

# Tickers where AMD pattern is most reliable
# (high institutional activity = more predictable AMD cycles)
AMD_WATCHLIST = [
    # Index ETFs — highest institutional manipulation
    "SPY", "QQQ", "IWM", "DIA",
    # Leveraged for amplified AMD moves
    "SOXL", "TQQQ", "SPXL", "TNA",
    # GS energy tickers — war headlines create manipulation events
    "XLE", "USO", "OXY", "XOP",
    # High-volume single names
    "NVDA", "TSLA", "AMD", "AAPL", "AMZN", "META", "GOOGL",
    "COIN", "PLTR", "SOFI", "HOOD", "MRVL",
    # GS defense/gold (geo events = AMD catalyst)
    "LMT", "RTX", "GLD", "GDX",
    # Airlines (war manipulation targets)
    "UAL", "DAL", "JETS",
]


class AMDPowerOf3Strategy:
    """ICT AMD / Power of 3 trading strategy.

    Detects the 3-phase market cycle:
    1. Accumulation (range-bound consolidation)
    2. Manipulation (false breakout / stop hunt)
    3. Distribution (real directional move)

    Enters after manipulation reversal, targets distribution phase.
    """

    def __init__(self, params: dict | None = None):
        self._params = {**PARAMS, **(params or {})}

    def detect_amd_phase(
        self,
        symbol: str,
        market_data: dict,
        scorecard: dict | None = None,
    ) -> dict[str, Any] | None:
        """Detect current AMD phase and generate trade signal if in manipulation reversal.

        Uses available market data to infer phase:
        - Small change + low range → Accumulation
        - Sharp spike then reversal → Manipulation (ENTRY SIGNAL)
        - Strong directional follow-through → Distribution (already in trade)
        """
        sym_data = market_data.get(symbol, {})
        price = sym_data.get("price", 0)
        change_pct = sym_data.get("change_pct", 0)
        volume = sym_data.get("volume", 0)

        if price <= 0:
            return None

        abs_change = abs(change_pct)

        # Phase detection from available data
        # Accumulation: very small moves, range-bound
        if abs_change < self._params["accumulation_range_max_pct"]:
            # In accumulation — no trade yet, but flag it
            return None  # Wait for manipulation

        # Manipulation detection: sharp move that's likely a stop hunt
        # Key insight: manipulation moves are SHARP but TEMPORARY
        # We detect this as: moderate-large move (0.5-3%) that appears
        # extended relative to recent action
        manip_min = self._params["manipulation_breakout_min_pct"]
        manip_max = self._params["manipulation_breakout_max_pct"]

        if manip_min <= abs_change <= manip_max:
            # This could be manipulation — the entry zone
            # AMD says: trade OPPOSITE to the manipulation direction
            # If price dropped sharply (stop hunt below range) → go LONG
            # If price spiked sharply (stop hunt above range) → go SHORT

            # Volume confirmation: manipulation often has a volume spike
            vol_ok = volume > 1_000_000 or volume == 0  # Allow if no volume data

            if not vol_ok:
                return None

            # Direction: opposite of the manipulation move
            if change_pct < -manip_min:
                # Price dropped = bearish manipulation = BULLISH setup
                direction = "long"
                manipulation_direction = "down"
                stop_distance_pct = abs_change + 0.5  # Stop below manipulation low
                confidence = 0.50 + abs_change * 0.06
            elif change_pct > manip_min:
                # Price spiked = bullish manipulation = BEARISH setup
                direction = "short"
                manipulation_direction = "up"
                stop_distance_pct = abs_change + 0.5
                confidence = 0.50 + abs_change * 0.06
            else:
                return None

            # GS regime context boost
            regime_boost = 1.0
            if scorecard:
                oil_regime = scorecard.get("v6_oil_regime", "NORMAL")
                geo = scorecard.get("component_scores", {}).get("geopolitical_tension", 0)

                # War headlines create PERFECT manipulation events
                # Institutions use war news to hunt stops before the real move
                if oil_regime in ("SHOCK", "DISLOCATION"):
                    regime_boost = 1.25
                    confidence += 0.08
                elif geo > 0.5:
                    regime_boost = 1.15
                    confidence += 0.05

            # Cap confidence
            confidence = min(confidence, 0.85)

            # Risk/reward
            target_distance_pct = stop_distance_pct * self._params["distribution_target_rr"]
            stop_loss_pct = -stop_distance_pct
            take_profit_pct = target_distance_pct

            return {
                "strategy": "amd_power_of_3",
                "symbol": symbol,
                "direction": direction,
                "notional_usd": 170,  # $858 equity * 20%
                "confidence_score": round(confidence, 3),
                "confidence": round(confidence, 3),
                "stop_loss_pct": round(max(stop_loss_pct, -4.0), 2),
                "take_profit_pct": round(min(take_profit_pct, 8.0), 2),
                "tier": "tier_2",
                "tier_size_multiplier": 0.50,
                "entry_signal": f"AMD manipulation {manipulation_direction} on {symbol} — entering {direction}",
                "amd_phase": "manipulation_reversal",
                "manipulation_direction": manipulation_direction,
                "regime_boost": regime_boost,
                "metadata": {
                    "source": "tomcampcoaching_amd",
                    "framework": "ICT Power of 3",
                    "risk_reward": f"1:{self._params['distribution_target_rr']}",
                },
            }

        # If move is > max manipulation, it might be real distribution
        # Don't fade a genuine breakout
        if abs_change > manip_max:
            return None

        return None

    def scan_watchlist(
        self,
        market_data: dict | None = None,
        scorecard: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Scan watchlist for AMD setups."""
        if market_data is None:
            return []

        ideas = []
        for symbol in AMD_WATCHLIST:
            idea = self.detect_amd_phase(symbol, market_data, scorecard)
            if idea:
                ideas.append(idea)
        return ideas


# Strategy engine integration
def evaluate_amd_po3(
    strat: dict,
    scorecard: dict | None = None,
    bridge_results: dict | None = None,
    market_data: dict | None = None,
) -> list[dict[str, Any]]:
    """Entry point for strategy engine integration."""
    strategy = AMDPowerOf3Strategy()
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

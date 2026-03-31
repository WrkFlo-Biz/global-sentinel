#!/usr/bin/env python3
"""Liquidity Sweep & Fair Value Gap Detector — ICT/Smart Money Concepts for GS.

Multi-timeframe liquidity analysis:
- Daily:  Previous day high/low as liquidity pools. Sweep + close back = reversal signal.
- 4H:    Swing highs/lows as liquidity. Reversal confirmed by Inverse FVG (IFVG).
- 1H:    Same structure. IFVG or CRT (Candle Reversal Trade) confirms direction.
- 5min:  Intraday patterns, most effective during London→New York overlap (8-11 AM ET).

Key concepts:
- Liquidity Sweep: Price takes out a known high/low then reverses.
- IFVG (Inverse Fair Value Gap): A gap that gets filled from the opposite direction,
  confirming the reversal.
- CRT (Candle Reversal Trade): When IFVG doesn't form, a reversal candle pattern
  provides the entry confirmation.
- Session Timing: London→NY overlap (8-11 AM ET) manipulations are most effective.

This detector enhances GS entry timing for existing war strategies by identifying
optimal execution windows when smart money is sweeping liquidity.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LiquidityLevel:
    """Represents a known liquidity pool (high or low)."""
    def __init__(self, price: float, level_type: str, timeframe: str, timestamp: str = ""):
        self.price = price
        self.level_type = level_type  # "high" or "low"
        self.timeframe = timeframe    # "daily", "4h", "1h", "5m"
        self.timestamp = timestamp
        self.swept = False
        self.sweep_time: Optional[str] = None


class FairValueGap:
    """Represents a Fair Value Gap (imbalance zone)."""
    def __init__(self, high: float, low: float, direction: str, timeframe: str):
        self.high = high
        self.low = low
        self.direction = direction  # "bullish" or "bearish"
        self.timeframe = timeframe
        self.filled = False
        self.inverse = False  # True if this is an IFVG (price came from opposite side)


class LiquiditySweepDetector:
    """Detects liquidity sweeps, IFVGs, and CRT patterns across timeframes."""

    # Session windows (ET timezone)
    LONDON_OPEN = time(3, 0)
    LONDON_CLOSE = time(12, 0)
    NY_OPEN = time(9, 30)
    NY_CLOSE = time(16, 0)
    OVERLAP_START = time(8, 0)
    OVERLAP_END = time(11, 0)

    # Sweep detection thresholds
    SWEEP_TOLERANCE_PCT = 0.001  # 0.1% beyond level counts as sweep
    FVG_MIN_SIZE_PCT = 0.002     # Minimum 0.2% gap size to be significant

    def __init__(self) -> None:
        self._liquidity_levels: Dict[str, List[LiquidityLevel]] = {}  # symbol → levels
        self._fvgs: Dict[str, List[FairValueGap]] = {}  # symbol → FVGs
        self._sweep_signals: List[Dict[str, Any]] = []

    def update_daily_levels(
        self,
        symbol: str,
        prev_day_high: float,
        prev_day_low: float,
        prev_day_close: float,
    ) -> None:
        """Register previous day's high/low as daily liquidity pools."""
        levels = self._liquidity_levels.setdefault(symbol, [])
        # Clear old daily levels
        levels[:] = [l for l in levels if l.timeframe != "daily"]
        levels.append(LiquidityLevel(prev_day_high, "high", "daily"))
        levels.append(LiquidityLevel(prev_day_low, "low", "daily"))

    def update_swing_levels(
        self,
        symbol: str,
        timeframe: str,
        swing_highs: List[float],
        swing_lows: List[float],
    ) -> None:
        """Register swing highs/lows as liquidity pools for a given timeframe."""
        levels = self._liquidity_levels.setdefault(symbol, [])
        # Clear old levels for this timeframe
        levels[:] = [l for l in levels if l.timeframe != timeframe]
        for h in swing_highs[-3:]:  # Keep last 3 swing points
            levels.append(LiquidityLevel(h, "high", timeframe))
        for l in swing_lows[-3:]:
            levels.append(LiquidityLevel(l, "low", timeframe))

    def detect_sweep(
        self,
        symbol: str,
        current_price: float,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        candle_open: float,
        timeframe: str = "1h",
    ) -> List[Dict[str, Any]]:
        """Check if current price action sweeps any known liquidity levels.

        A sweep occurs when price exceeds a level but closes back on the other side,
        indicating a liquidity grab / manipulation.

        Returns list of sweep signal dicts.
        """
        signals: List[Dict[str, Any]] = []
        levels = self._liquidity_levels.get(symbol, [])

        for level in levels:
            if level.swept:
                continue

            tolerance = level.price * self.SWEEP_TOLERANCE_PCT

            # High sweep: price goes above the high, then closes below it
            if level.level_type == "high":
                if candle_high > level.price + tolerance and candle_close < level.price:
                    level.swept = True
                    signal = {
                        "type": "LIQUIDITY_SWEEP",
                        "symbol": symbol,
                        "direction": "bearish",
                        "swept_level": level.price,
                        "level_type": "high",
                        "level_timeframe": level.timeframe,
                        "candle_timeframe": timeframe,
                        "sweep_high": candle_high,
                        "close": candle_close,
                        "rejection_pct": (candle_high - candle_close) / candle_high * 100,
                        "confirmation_needed": "IFVG or CRT",
                    }
                    signals.append(signal)
                    logger.info(
                        "SWEEP detected: %s %s high %.2f swept (closed %.2f)",
                        symbol, level.timeframe, level.price, candle_close,
                    )

            # Low sweep: price goes below the low, then closes above it
            elif level.level_type == "low":
                if candle_low < level.price - tolerance and candle_close > level.price:
                    level.swept = True
                    signal = {
                        "type": "LIQUIDITY_SWEEP",
                        "symbol": symbol,
                        "direction": "bullish",
                        "swept_level": level.price,
                        "level_type": "low",
                        "level_timeframe": level.timeframe,
                        "candle_timeframe": timeframe,
                        "sweep_low": candle_low,
                        "close": candle_close,
                        "rejection_pct": (candle_close - candle_low) / candle_close * 100,
                        "confirmation_needed": "IFVG or CRT",
                    }
                    signals.append(signal)
                    logger.info(
                        "SWEEP detected: %s %s low %.2f swept (closed %.2f)",
                        symbol, level.timeframe, level.price, candle_close,
                    )

        self._sweep_signals.extend(signals)
        return signals

    def detect_fvg(
        self,
        symbol: str,
        candles: List[Dict[str, float]],
        timeframe: str = "1h",
    ) -> List[FairValueGap]:
        """Detect Fair Value Gaps in a sequence of 3+ candles.

        A bullish FVG: candle[0].high < candle[2].low (gap between)
        A bearish FVG: candle[0].low > candle[2].high (gap between)

        Args:
            candles: List of dicts with keys: open, high, low, close
            timeframe: Timeframe label

        Returns:
            List of detected FVGs.
        """
        fvgs: List[FairValueGap] = []
        if len(candles) < 3:
            return fvgs

        for i in range(len(candles) - 2):
            c0, c1, c2 = candles[i], candles[i + 1], candles[i + 2]

            # Bullish FVG: gap between candle 0 high and candle 2 low
            if c2["low"] > c0["high"]:
                gap_size = (c2["low"] - c0["high"]) / c0["high"]
                if gap_size >= self.FVG_MIN_SIZE_PCT:
                    fvg = FairValueGap(c2["low"], c0["high"], "bullish", timeframe)
                    fvgs.append(fvg)

            # Bearish FVG: gap between candle 0 low and candle 2 high
            if c2["high"] < c0["low"]:
                gap_size = (c0["low"] - c2["high"]) / c0["low"]
                if gap_size >= self.FVG_MIN_SIZE_PCT:
                    fvg = FairValueGap(c0["low"], c2["high"], "bearish", timeframe)
                    fvgs.append(fvg)

        self._fvgs.setdefault(symbol, []).extend(fvgs)
        return fvgs

    def check_ifvg_confirmation(
        self,
        symbol: str,
        current_price: float,
        sweep_direction: str,
    ) -> Optional[Dict[str, Any]]:
        """Check if any existing FVG has been filled from the opposite direction (IFVG).

        An IFVG confirms the sweep reversal:
        - Bearish sweep + bullish FVG filled from above = confirmed sell
        - Bullish sweep + bearish FVG filled from below = confirmed buy

        Returns confirmation dict or None.
        """
        fvgs = self._fvgs.get(symbol, [])

        for fvg in fvgs:
            if fvg.filled or fvg.inverse:
                continue

            if sweep_direction == "bearish" and fvg.direction == "bullish":
                # Price should come down into the bullish FVG from above
                if fvg.low <= current_price <= fvg.high:
                    fvg.inverse = True
                    return {
                        "type": "IFVG_CONFIRMATION",
                        "symbol": symbol,
                        "direction": "bearish",
                        "fvg_high": fvg.high,
                        "fvg_low": fvg.low,
                        "fvg_timeframe": fvg.timeframe,
                        "entry_zone": f"{fvg.low:.2f} - {fvg.high:.2f}",
                        "thesis": "Inverse FVG confirms bearish sweep reversal",
                    }

            elif sweep_direction == "bullish" and fvg.direction == "bearish":
                # Price should come up into the bearish FVG from below
                if fvg.low <= current_price <= fvg.high:
                    fvg.inverse = True
                    return {
                        "type": "IFVG_CONFIRMATION",
                        "symbol": symbol,
                        "direction": "bullish",
                        "fvg_high": fvg.high,
                        "fvg_low": fvg.low,
                        "fvg_timeframe": fvg.timeframe,
                        "entry_zone": f"{fvg.low:.2f} - {fvg.high:.2f}",
                        "thesis": "Inverse FVG confirms bullish sweep reversal",
                    }

        return None

    def check_crt_confirmation(
        self,
        sweep_direction: str,
        candle_open: float,
        candle_high: float,
        candle_low: float,
        candle_close: float,
    ) -> Optional[Dict[str, Any]]:
        """Check for Candle Reversal Trade (CRT) pattern when IFVG not available.

        CRT is a strong reversal candle that forms after a liquidity sweep:
        - Bearish CRT: large upper wick, close near low (engulfing/pin bar)
        - Bullish CRT: large lower wick, close near high (hammer/engulfing)
        """
        body_size = abs(candle_close - candle_open)
        candle_range = candle_high - candle_low
        if candle_range == 0:
            return None

        upper_wick = candle_high - max(candle_open, candle_close)
        lower_wick = min(candle_open, candle_close) - candle_low

        if sweep_direction == "bearish":
            # Bearish CRT: large upper wick (>60% of range), close in lower 30%
            if upper_wick / candle_range > 0.60 and (candle_close - candle_low) / candle_range < 0.30:
                return {
                    "type": "CRT_CONFIRMATION",
                    "direction": "bearish",
                    "pattern": "bearish_rejection",
                    "upper_wick_pct": upper_wick / candle_range,
                    "thesis": "CRT bearish rejection candle after high sweep",
                }

        elif sweep_direction == "bullish":
            # Bullish CRT: large lower wick (>60% of range), close in upper 30%
            if lower_wick / candle_range > 0.60 and (candle_high - candle_close) / candle_range < 0.30:
                return {
                    "type": "CRT_CONFIRMATION",
                    "direction": "bullish",
                    "pattern": "bullish_rejection",
                    "lower_wick_pct": lower_wick / candle_range,
                    "thesis": "CRT bullish rejection candle after low sweep",
                }

        return None

    @staticmethod
    def get_session_context(utc_hour: int) -> Dict[str, Any]:
        """Determine current trading session and effectiveness rating.

        London→NY overlap (13:00-16:00 UTC / 8-11 AM ET) is most effective
        for liquidity sweep manipulations.
        """
        # Convert to ET approximation (UTC - 4 for EDT, UTC - 5 for EST)
        et_hour = (utc_hour - 4) % 24  # Approximate EDT

        session = "OFF_HOURS"
        effectiveness = 0.3

        if 3 <= et_hour < 8:
            session = "LONDON"
            effectiveness = 0.6
        elif 8 <= et_hour < 11:
            session = "LONDON_NY_OVERLAP"
            effectiveness = 1.0  # Maximum effectiveness
        elif 11 <= et_hour < 12:
            session = "LONDON_CLOSE"
            effectiveness = 0.5
        elif 9 <= et_hour < 16:
            session = "NEW_YORK"
            effectiveness = 0.7
        elif 20 <= et_hour or et_hour < 3:
            session = "ASIA"
            effectiveness = 0.4

        return {
            "session": session,
            "effectiveness": effectiveness,
            "is_overlap": session == "LONDON_NY_OVERLAP",
            "sweep_reliability": effectiveness,
        }

    def generate_entry_timing(
        self,
        symbol: str,
        sweep_signals: List[Dict[str, Any]],
        utc_hour: int,
    ) -> List[Dict[str, Any]]:
        """Generate entry timing recommendations based on sweeps + session.

        Combines sweep detection with session timing for optimal entries.
        Returns list of timed entry signals.
        """
        session = self.get_session_context(utc_hour)
        entries: List[Dict[str, Any]] = []

        for sweep in sweep_signals:
            if sweep.get("symbol") != symbol:
                continue

            direction = sweep["direction"]

            # Check for IFVG confirmation first
            ifvg = self.check_ifvg_confirmation(
                symbol, sweep.get("close", 0), direction
            )

            # If no IFVG, check for CRT
            crt = None
            if not ifvg:
                crt = self.check_crt_confirmation(
                    direction,
                    sweep.get("candle_open", sweep.get("close", 0)),
                    sweep.get("sweep_high", sweep.get("close", 0)),
                    sweep.get("sweep_low", sweep.get("close", 0)),
                    sweep.get("close", 0),
                )

            confirmation = ifvg or crt
            if not confirmation:
                continue

            # Compute entry quality
            quality = session["effectiveness"]
            if confirmation["type"] == "IFVG_CONFIRMATION":
                quality *= 1.2  # IFVG is stronger than CRT
            quality = min(quality, 1.0)

            entry = {
                "symbol": symbol,
                "direction": direction,
                "entry_type": "LIQUIDITY_SWEEP_REVERSAL",
                "sweep": sweep,
                "confirmation": confirmation,
                "session": session,
                "quality_score": round(quality, 3),
                "swept_timeframe": sweep.get("level_timeframe"),
                "recommendation": (
                    f"{direction.upper()} {symbol} — "
                    f"{sweep['level_timeframe']} {sweep['level_type']} swept, "
                    f"confirmed by {confirmation['type']} in {session['session']}"
                ),
            }
            entries.append(entry)

        return entries

    def format_telegram(self, entries: List[Dict[str, Any]]) -> str:
        """Format entry timing signals for Telegram."""
        if not entries:
            return ""

        lines = ["\U0001f4a7 Liquidity Sweep Signals:"]
        for entry in entries[:3]:
            quality = entry.get("quality_score", 0)
            stars = "\u2b50" * min(int(quality * 5), 5)
            lines.append(
                f"  {entry['direction'].upper()} {entry['symbol']} "
                f"| {entry['confirmation']['type']} "
                f"| {entry['session']['session']} "
                f"| Quality: {stars} ({quality:.0%})"
            )

        return "\n".join(lines)

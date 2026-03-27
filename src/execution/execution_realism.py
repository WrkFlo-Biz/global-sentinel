#!/usr/bin/env python3
"""Model realistic execution dynamics for shadow order quality assessment."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict

NORMAL_LIQUIDITY = "NORMAL_LIQUIDITY"
SPREAD_WIDENING = "SPREAD_WIDENING"
PARTIAL_FILL_HEAVY = "PARTIAL_FILL_HEAVY"
DELAYED_ACK = "DELAYED_ACK"
STALE_BROKER_STATE = "STALE_BROKER_STATE"
QUEUE_DECAY = "QUEUE_DECAY"
VOLATILITY_AUCTION_RISK = "VOLATILITY_AUCTION_RISK"

# Cancel/replace latency expectations by regime (milliseconds)
_CANCEL_REPLACE_LATENCY: Dict[str, float] = {
    NORMAL_LIQUIDITY: 50.0,
    SPREAD_WIDENING: 150.0,
    PARTIAL_FILL_HEAVY: 200.0,
    DELAYED_ACK: 500.0,
    STALE_BROKER_STATE: 1000.0,
    QUEUE_DECAY: 300.0,
    VOLATILITY_AUCTION_RISK: 750.0,
}

# Venue slippage base estimates (bps) by regime
_VENUE_SLIPPAGE_BPS: Dict[str, float] = {
    NORMAL_LIQUIDITY: 1.0,
    SPREAD_WIDENING: 5.0,
    PARTIAL_FILL_HEAVY: 3.0,
    DELAYED_ACK: 4.0,
    STALE_BROKER_STATE: 8.0,
    QUEUE_DECAY: 6.0,
    VOLATILITY_AUCTION_RISK: 12.0,
}


class ExecutionRealismEngine:
    """Estimate realistic execution frictions for shadow order quality scoring."""

    def __init__(
        self,
        luld_band_pct_large: float = 5.0,
        luld_band_pct_small: float = 10.0,
        small_cap_threshold: float = 50.0,
    ) -> None:
        self.luld_band_pct_large = luld_band_pct_large
        self.luld_band_pct_small = luld_band_pct_small
        self.small_cap_threshold = small_cap_threshold

    # ------------------------------------------------------------------
    # 1. Queue position estimation
    # ------------------------------------------------------------------

    def estimate_queue_position(self, order: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Estimate queue position based on limit price vs NBBO and order size vs ADV.

        Args:
            order: Must contain ``limit_price``, ``side``, ``qty``.
            market_data: Should contain ``bid``, ``ask``, ``adv`` (average daily volume).

        Returns:
            Dict with ``fill_probability``, ``expected_wait_seconds``, ``queue_depth_estimate``.
        """
        limit_price = float(order.get("limit_price", 0.0) or 0.0)
        side = str(order.get("side", "buy")).lower()
        qty = float(order.get("qty", 0.0) or 0.0)

        bid = float(market_data.get("bid", 0.0) or 0.0)
        ask = float(market_data.get("ask", 0.0) or 0.0)
        adv = float(market_data.get("adv", 1.0) or 1.0)
        mid = (bid + ask) / 2.0 if (bid + ask) > 0 else limit_price

        # Distance from aggressive side of NBBO
        if side == "buy":
            distance_pct = ((ask - limit_price) / max(ask, 1e-9)) * 100.0
        else:
            distance_pct = ((limit_price - bid) / max(bid, 1e-9)) * 100.0

        # ADV ratio penalises large orders
        adv_ratio = qty / max(adv, 1.0)

        # Fill probability degrades with distance from NBBO and order size
        base_prob = max(0.0, min(1.0, 1.0 - distance_pct / 1.0))  # 1% away → 0 probability
        size_penalty = max(0.0, 1.0 - adv_ratio * 10.0)  # 10% ADV → 0
        fill_probability = round(max(0.0, min(1.0, base_prob * size_penalty)), 4)

        # Expected wait increases with distance and order size
        expected_wait_seconds = round(max(0.5, (1.0 - fill_probability) * 60.0 + adv_ratio * 300.0), 2)

        queue_depth_estimate = int(max(1, adv_ratio * 5000))

        return {
            "fill_probability": fill_probability,
            "expected_wait_seconds": expected_wait_seconds,
            "queue_depth_estimate": queue_depth_estimate,
            "distance_from_nbbo_pct": round(distance_pct, 4),
            "adv_ratio": round(adv_ratio, 6),
        }

    # ------------------------------------------------------------------
    # 2. Cancel/replace latency
    # ------------------------------------------------------------------

    def estimate_cancel_replace_latency(self, regime: str) -> Dict[str, Any]:
        """Return expected cancel/replace latency for the given regime.

        Args:
            regime: One of the microstructure regime constants.

        Returns:
            Dict with ``latency_ms``, ``regime``, ``reliability`` (1.0 = most reliable).
        """
        latency_ms = _CANCEL_REPLACE_LATENCY.get(regime, _CANCEL_REPLACE_LATENCY[NORMAL_LIQUIDITY])
        reliability = round(max(0.3, 1.0 - (latency_ms - 50.0) / 1000.0), 4)
        return {
            "latency_ms": latency_ms,
            "regime": regime,
            "reliability": reliability,
        }

    # ------------------------------------------------------------------
    # 3. Auction window detection
    # ------------------------------------------------------------------

    def check_auction_window(self, timestamp_utc: str) -> Dict[str, Any]:
        """Detect whether *timestamp_utc* falls within an auction or regular session.

        Opening auction: 09:30-09:45 ET.
        Closing auction: 15:50-16:00 ET.

        Args:
            timestamp_utc: ISO-8601 UTC timestamp string.

        Returns:
            Dict with ``window_type``, ``auction_risk_level``, ``recommended_order_type``.
        """
        dt = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
        # Convert to US/Eastern naively via offset (ET = UTC-5 standard, UTC-4 daylight)
        # For simplicity we use UTC-4 (EDT) since US equities operate mostly in EDT
        et_hour = (dt.hour - 4) % 24
        et_minute = dt.minute

        total_minutes = et_hour * 60 + et_minute

        opening_start = 9 * 60 + 30   # 09:30
        opening_end = 9 * 60 + 45     # 09:45
        closing_start = 15 * 60 + 50  # 15:50
        closing_end = 16 * 60         # 16:00
        market_open = 9 * 60 + 30
        market_close = 16 * 60

        if opening_start <= total_minutes < opening_end:
            return {
                "window_type": "opening_auction",
                "auction_risk_level": "high",
                "recommended_order_type": "limit",
                "minutes_into_window": total_minutes - opening_start,
            }
        if closing_start <= total_minutes < closing_end:
            return {
                "window_type": "closing_auction",
                "auction_risk_level": "elevated",
                "recommended_order_type": "MOC",
                "minutes_into_window": total_minutes - closing_start,
            }
        if market_open <= total_minutes < market_close:
            return {
                "window_type": "regular_session",
                "auction_risk_level": "low",
                "recommended_order_type": "limit",
                "minutes_into_window": total_minutes - market_open,
            }
        return {
            "window_type": "outside_market_hours",
            "auction_risk_level": "none",
            "recommended_order_type": "none",
            "minutes_into_window": 0,
        }

    # ------------------------------------------------------------------
    # 4. LULD halt risk
    # ------------------------------------------------------------------

    def check_luld_halt_risk(self, symbol: str, price: float, ref_price: float) -> Dict[str, Any]:
        """Check proximity to LULD bands.

        Args:
            symbol: Ticker symbol (used for context; band width is by price threshold).
            price: Current or proposed trade price.
            ref_price: Reference price (e.g., previous 5-min average).

        Returns:
            Dict with ``halt_risk_level``, ``distance_to_band_pct``, ``band_pct``.
        """
        # Use wider bands for small-cap / low-price stocks
        band_pct = self.luld_band_pct_small if ref_price < self.small_cap_threshold else self.luld_band_pct_large

        if ref_price <= 0:
            return {
                "symbol": symbol,
                "halt_risk_level": "unknown",
                "distance_to_band_pct": 0.0,
                "band_pct": band_pct,
            }

        deviation_pct = abs(price - ref_price) / ref_price * 100.0
        distance_to_band_pct = round(band_pct - deviation_pct, 4)

        if distance_to_band_pct <= 0.5:
            risk_level = "imminent"
        elif distance_to_band_pct <= 2.0:
            risk_level = "elevated"
        else:
            risk_level = "low"

        return {
            "symbol": symbol,
            "halt_risk_level": risk_level,
            "distance_to_band_pct": distance_to_band_pct,
            "band_pct": band_pct,
            "deviation_pct": round(deviation_pct, 4),
        }

    # ------------------------------------------------------------------
    # 5. Overnight gap risk
    # ------------------------------------------------------------------

    def estimate_overnight_gap_risk(self, symbol: str, last_close: float, volatility: float) -> Dict[str, Any]:
        """Model expected overnight gap based on historical volatility.

        Uses a simplified model: expected overnight gap ~ 0.6 * daily vol (annualised
        volatility is converted to a single-day estimate via sqrt(252)).

        Args:
            symbol: Ticker symbol.
            last_close: Previous closing price.
            volatility: Annualised volatility (e.g. 0.30 for 30%).

        Returns:
            Dict with ``expected_gap_pct``, ``gap_risk_level``, ``symbol``.
        """
        if last_close <= 0 or volatility <= 0:
            return {
                "symbol": symbol,
                "expected_gap_pct": 0.0,
                "gap_risk_level": "unknown",
            }

        daily_vol = volatility / math.sqrt(252)
        # Overnight is roughly 60% of total daily vol
        expected_gap_pct = round(daily_vol * 0.6 * 100.0, 4)

        if expected_gap_pct >= 3.0:
            gap_risk_level = "high"
        elif expected_gap_pct >= 1.5:
            gap_risk_level = "moderate"
        else:
            gap_risk_level = "low"

        return {
            "symbol": symbol,
            "expected_gap_pct": expected_gap_pct,
            "gap_risk_level": gap_risk_level,
            "daily_vol_pct": round(daily_vol * 100.0, 4),
        }

    # ------------------------------------------------------------------
    # 6. Venue slippage assessment
    # ------------------------------------------------------------------

    def assess_venue_slippage(self, order: Dict[str, Any], regime: str) -> Dict[str, Any]:
        """Estimate slippage by venue and regime.

        Args:
            order: Should contain ``qty``, ``limit_price``, optionally ``venue``.
            regime: Microstructure regime string.

        Returns:
            Dict with ``expected_slippage_bps``, ``confidence``.
        """
        base_bps = _VENUE_SLIPPAGE_BPS.get(regime, _VENUE_SLIPPAGE_BPS[NORMAL_LIQUIDITY])

        qty = float(order.get("qty", 0) or 0)
        limit_price = float(order.get("limit_price", 0) or 0)
        notional = qty * limit_price if limit_price > 0 else 0.0

        # Larger notional → more slippage
        size_multiplier = 1.0 + max(0.0, math.log10(max(notional, 1.0)) - 3.0) * 0.2

        expected_slippage_bps = round(base_bps * size_multiplier, 2)

        # Confidence is lower in stressed regimes
        confidence = round(max(0.3, 0.95 - (expected_slippage_bps - 1.0) * 0.05), 4)

        return {
            "expected_slippage_bps": expected_slippage_bps,
            "confidence": confidence,
            "regime": regime,
            "notional": round(notional, 2),
        }

    # ------------------------------------------------------------------
    # 7. Full assessment
    # ------------------------------------------------------------------

    def full_assessment(
        self,
        order: Dict[str, Any],
        market_data: Dict[str, Any],
        regime: str,
        timestamp_utc: str,
    ) -> Dict[str, Any]:
        """Run all realism checks and return a combined assessment.

        Returns:
            Dict with per-check results and ``overall_realism_score`` in [0, 1].
        """
        queue = self.estimate_queue_position(order, market_data)
        latency = self.estimate_cancel_replace_latency(regime)
        auction = self.check_auction_window(timestamp_utc)

        symbol = str(order.get("symbol", "UNKNOWN"))
        price = float(order.get("limit_price", 0) or 0)
        ref_price = float(market_data.get("ref_price", price) or price)
        luld = self.check_luld_halt_risk(symbol, price, ref_price)

        last_close = float(market_data.get("last_close", price) or price)
        volatility = float(market_data.get("volatility", 0.25) or 0.25)
        gap = self.estimate_overnight_gap_risk(symbol, last_close, volatility)

        slippage = self.assess_venue_slippage(order, regime)

        # Compute composite realism score --------------------------------
        # Each sub-score contributes; lower is worse
        fill_score = queue["fill_probability"]
        latency_score = latency["reliability"]

        auction_penalty = {"high": 0.3, "elevated": 0.15, "low": 0.0, "none": 0.0}.get(
            auction["auction_risk_level"], 0.0
        )

        luld_penalty = {"imminent": 0.4, "elevated": 0.15, "low": 0.0, "unknown": 0.1}.get(
            luld["halt_risk_level"], 0.0
        )

        gap_penalty = {"high": 0.2, "moderate": 0.1, "low": 0.0, "unknown": 0.05}.get(
            gap["gap_risk_level"], 0.0
        )

        slippage_penalty = min(0.3, slippage["expected_slippage_bps"] / 100.0)

        raw_score = (
            0.30 * fill_score
            + 0.15 * latency_score
            + 0.55 * 1.0  # base
            - auction_penalty * 0.15
            - luld_penalty * 0.15
            - gap_penalty * 0.10
            - slippage_penalty * 0.15
        )

        overall_realism_score = round(max(0.0, min(1.0, raw_score)), 4)

        return {
            "queue_position": queue,
            "cancel_replace_latency": latency,
            "auction_window": auction,
            "luld_halt_risk": luld,
            "overnight_gap_risk": gap,
            "venue_slippage": slippage,
            "overall_realism_score": overall_realism_score,
            "timestamp_utc": timestamp_utc,
        }

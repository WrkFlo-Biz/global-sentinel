"""ICT Candle Range Theory strategy for gold and NQ futures.

Inspired by Instagram reels:
  - @vox (Tab 3): "Gold strategy... #liquidity #daytrading #ictconcepts
    #candlerangetheory #futurestrading"
  - @Keshav khurana (Tab 16): "How to profit from a 4hour candle strategy
    #smartmoneyconcepts #innercircletrader #nasdaq"
  - @advicefromtraders (Tab 12): "He didn't need fancy indicators or news
    just pure price action"

Implements ICT's Candle Range Theory (CRT):
  1. Identify the parent candle range (4H candle high/low)
  2. Detect liquidity sweep (wick beyond range = stop hunt)
  3. Enter on displacement candle after the sweep
  4. Target the opposite side of the range (Fair Value Gap fill)

Session awareness: London fix (10:30/15:00 GMT) and NY open (9:30 ET)
create the cleanest ICT setups on gold.

Transcript refinement from tab 3 (`novo.legacy`):
  - Check the dollar index every morning; gold bias is strongest when DXY
    confirms the inverse move.
  - Focus on the 10:30 AM ET window for the sweep / confirmation.
  - Use real-time news flow as a catalyst filter rather than delayed calendar
    expectations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Primary watchlist — gold and index futures + their ETF proxies
CRT_WATCHLIST = {
    "gold": ["GLD", "GC", "MGC", "IAU"],
    "nasdaq": ["QQQ", "NQ", "TQQQ"],
    "sp500": ["SPY", "ES", "SPXL"],
}

PARAMS: dict[str, Any] = {
    # CRT detection
    "min_range_pct": 0.4,
    "sweep_wick_ratio": 0.35,
    "displacement_min_pct": 0.25,
    # Fair Value Gap
    "fvg_min_gap_pct": 0.15,
    # Session windows (UTC hours)
    "london_fix_hours": [10, 15],
    "ny_open_hour": 14,  # 9:30 ET = 14:30 UTC
    "gold_confirmation_hour_et": 10,
    "gold_confirmation_minute_et": 30,
    "session_boost": 0.08,
    "dxy_inverse_boost": 0.08,
    "news_catalyst_boost": 0.05,
    # General
    "min_confidence": 0.45,
    "base_notional_usd": 900.0,
    "max_candidates": 3,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _detect_crt_setup(sym_data: dict[str, Any]) -> dict[str, Any] | None:
    """Detect Candle Range Theory setup from OHLC data.

    A CRT setup occurs when price sweeps beyond the parent candle range
    (liquidity grab) then reverses with displacement back into range.
    """
    high = _safe_float(sym_data.get("high"))
    low = _safe_float(sym_data.get("low"))
    open_price = _safe_float(sym_data.get("open"))
    close = _safe_float(sym_data.get("price") or sym_data.get("close"))
    prior_high = _safe_float(sym_data.get("prior_high") or sym_data.get("prev_high"))
    prior_low = _safe_float(sym_data.get("prior_low") or sym_data.get("prev_low"))

    if not all([high, low, open_price, close, prior_high, prior_low]):
        return None

    parent_range = prior_high - prior_low
    if parent_range <= 0:
        return None

    range_pct = (parent_range / prior_low) * 100.0
    if range_pct < PARAMS["min_range_pct"]:
        return None

    # Check for buy-side liquidity sweep (wick above prior high then close below)
    buy_sweep = high > prior_high and close < prior_high
    # Check for sell-side liquidity sweep (wick below prior low then close above)
    sell_sweep = low < prior_low and close > prior_low

    if not buy_sweep and not sell_sweep:
        return None

    # Measure displacement (body vs range)
    body = abs(close - open_price)
    body_pct = (body / close) * 100.0 if close > 0 else 0
    if body_pct < PARAMS["displacement_min_pct"]:
        return None

    # Sweep quality: how far the wick extended beyond range
    if buy_sweep:
        sweep_depth = (high - prior_high) / parent_range
        direction = "short"  # Bearish CRT — swept buy-side, expect reversal down
    else:
        sweep_depth = (prior_low - low) / parent_range
        direction = "long"  # Bullish CRT — swept sell-side, expect reversal up

    if sweep_depth < PARAMS["sweep_wick_ratio"]:
        return None

    return {
        "type": "buy_sweep" if buy_sweep else "sell_sweep",
        "direction": direction,
        "range_pct": round(range_pct, 3),
        "sweep_depth": round(sweep_depth, 3),
        "body_pct": round(body_pct, 3),
        "parent_range": round(parent_range, 4),
    }


def _detect_fvg(sym_data: dict[str, Any]) -> bool:
    """Check if a Fair Value Gap exists (simplified)."""
    high = _safe_float(sym_data.get("high"))
    low = _safe_float(sym_data.get("low"))
    prior_high = _safe_float(sym_data.get("prior_high"))
    if high and low and prior_high:
        gap = low - prior_high
        if abs(gap) / max(high, 1) * 100 > PARAMS["fvg_min_gap_pct"]:
            return True
    return False


def _session_boost(asset_class: str) -> float:
    """Extra confidence during key institutional session times."""
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    if hour in PARAMS["london_fix_hours"] or hour == PARAMS["ny_open_hour"]:
        return PARAMS["session_boost"]
    if asset_class == "gold":
        now_et = now_utc.astimezone(ET)
        if (
            now_et.hour == PARAMS["gold_confirmation_hour_et"]
            and abs(now_et.minute - PARAMS["gold_confirmation_minute_et"]) <= 5
        ):
            return PARAMS["session_boost"] + 0.03
    return 0.0


def _dxy_inverse_confirmation(
    market_data: dict[str, dict[str, Any]],
    setup_direction: str,
) -> bool:
    dxy = market_data.get("DXY") or market_data.get("DX") or market_data.get("UUP")
    if not dxy:
        return False
    dxy_change = _safe_float(dxy.get("change_pct"))
    if setup_direction == "long":
        return dxy_change < 0
    if setup_direction == "short":
        return dxy_change > 0
    return False


def _news_catalyst_score(
    scorecard: dict[str, Any] | None,
    sym_data: dict[str, Any],
) -> float:
    explicit = _safe_float(sym_data.get("news_catalyst_score") or sym_data.get("headline_intensity"))
    if explicit > 0:
        return explicit
    if not scorecard:
        return 0.0
    components = scorecard.get("component_scores", {})
    return _safe_float(
        components.get("macro_news")
        or components.get("policy_signals")
        or components.get("geopolitical_tension")
    )


class ICTCandleRangeTheoryStrategy:
    """Detect ICT CRT setups on gold and index futures."""

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
        for asset_class, symbols in CRT_WATCHLIST.items():
            session_extra = _session_boost(asset_class)
            for symbol in symbols:
                sd = market_data.get(symbol)
                if not sd:
                    continue

                setup = _detect_crt_setup(sd)
                if not setup:
                    continue

                has_fvg = _detect_fvg(sd)

                # Confidence scoring
                confidence = 0.40
                confidence += setup["sweep_depth"] * 0.20
                confidence += min(setup["body_pct"] * 0.03, 0.15)
                confidence += session_extra
                if has_fvg:
                    confidence += 0.07
                if asset_class == "gold":
                    confidence += 0.04  # Gold has cleaner CRT setups
                    if _dxy_inverse_confirmation(market_data, setup["direction"]):
                        confidence += self._params["dxy_inverse_boost"]

                    news_score = _news_catalyst_score(scorecard, sd)
                    if news_score >= 0.25:
                        confidence += min(
                            self._params["news_catalyst_boost"] + news_score * 0.04,
                            0.10,
                        )

                # Scorecard boost
                if scorecard:
                    geo = _safe_float(scorecard.get("component_scores", {}).get("geopolitical_tension"))
                    if asset_class == "gold" and geo > 0.3:
                        confidence += 0.06

                confidence = round(min(0.92, confidence), 3)
                if confidence < self._params["min_confidence"]:
                    continue

                ideas.append({
                    "strategy": "ict_candle_range_theory",
                    "symbol": symbol,
                    "direction": setup["direction"],
                    "confidence_score": confidence,
                    "confidence": confidence,
                    "holding_period": "intraday_momentum",
                    "entry_signal": f"ICT CRT {setup['type']} on {symbol}",
                    "rationale": (
                        f"ICT CRT: {setup['type']} on {symbol}, "
                        f"sweep {setup['sweep_depth']:.0%} of range, "
                        f"displacement {setup['body_pct']:.1f}%"
                        f"{', FVG present' if has_fvg else ''}"
                        f"{', session boost' if session_extra > 0 else ''}"
                    ),
                    "notional_usd": self._params["base_notional_usd"],
                    "asset_class": asset_class,
                    "crt_setup": setup,
                    "metadata": {
                        "source": "instagram_ict_candle_range_theory",
                        "asset_class": asset_class,
                        "has_fvg": has_fvg,
                        "session_boost": round(session_extra, 3),
                        "dxy_inverse_confirmation": (
                            _dxy_inverse_confirmation(market_data, setup["direction"])
                            if asset_class == "gold"
                            else False
                        ),
                        "news_catalyst_score": round(_news_catalyst_score(scorecard, sd), 3),
                    },
                })

        ideas.sort(key=lambda x: x["confidence"], reverse=True)
        logger.info("ICT CRT scan: %d setups found", len(ideas))
        return ideas[: self._params["max_candidates"]]


def evaluate_ict_candle_range_theory(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Strategy-engine adapter for ICT candle range theory."""
    params = dict((strat or {}).get("params", {}))
    strategy = ICTCandleRangeTheoryStrategy(params=params)
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

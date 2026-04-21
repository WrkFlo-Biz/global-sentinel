"""AI-optimized MGC strategy derived from an extracted Instagram reel.

Recovered reel text:
  - Account: quantcrawlerai
  - Caption theme: an already-profitable MGC setup improved via AI optimization

Implementation notes:
  - The accessible reel content did not expose the full rule set, so this module
    implements the most defensible inference from the caption plus public
    QuantCrawler material: a walk-forward-optimized Micro Gold opening-range
    breakout with a failed-break reversal path and macro filters.
  - The output shape follows existing ``src/alpha`` strategy modules so the
    ideas can flow into the existing execution/routing stack without adapters.

Design sources:
  - QuantCrawler public ORB results page: MGC is presented with a 04:10-10:15
    optimized window, 70/30 walk-forward split, and tick-validated testing.
  - CME MGC contract specs: MGC is a 10-ounce Micro Gold contract with nearly
    24-hour trading access.
  - World Gold Council research: gold remains highly sensitive to US real-rate
    and dollar conditions.
  - Gold intraday jump literature: scheduled US macro news and liquidity shocks
    materially explain intraday jump behavior in gold futures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
ET = ZoneInfo("America/New_York")

MGC_WATCHLIST = ["MGC", "GC", "GLD", "IAU", "GDX"]
_DOLLAR_PROXIES = ("DXY", "DX", "UUP")
_RATE_UP_PROXIES = ("TNX", "^TNX", "US10Y")
_RATE_DOWN_PROXIES = ("TLT", "IEF", "ZB")
_RISK_PROXIES = ("SPY", "QQQ")

PARAMS: dict[str, Any] = {
    "session_start_ct": "04:10",
    "session_end_ct": "10:15",
    "range_minutes": 20,
    "asia_session_start_et": "18:00",
    "asia_session_end_et": "03:00",
    "asia_range_minutes": 25,
    "skip_sunday_session": True,
    "min_relative_volume": 1.20,
    "macro_bias_floor": 0.08,
    "max_extension_pct": 0.65,
    "min_range_pct": 0.10,
    "max_range_pct": 1.25,
    "base_notional_futures": 1600.0,
    "base_notional_proxy": 700.0,
}


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    breakout_buffer_pct: float
    min_relative_volume: float
    stop_atr_multiple: float
    target_rr: float
    allow_failed_break_reversal: bool = True


PROFILE_LIBRARY: dict[str, StrategyProfile] = {
    "balanced_orb": StrategyProfile(
        name="balanced_orb",
        breakout_buffer_pct=0.08,
        min_relative_volume=1.25,
        stop_atr_multiple=1.10,
        target_rr=1.90,
    ),
    "macro_event_orb": StrategyProfile(
        name="macro_event_orb",
        breakout_buffer_pct=0.05,
        min_relative_volume=1.50,
        stop_atr_multiple=1.35,
        target_rr=2.25,
    ),
    "failed_break_reversal": StrategyProfile(
        name="failed_break_reversal",
        breakout_buffer_pct=0.04,
        min_relative_volume=1.10,
        stop_atr_multiple=0.95,
        target_rr=1.55,
        allow_failed_break_reversal=True,
    ),
    "asia_session_orb": StrategyProfile(
        name="asia_session_orb",
        breakout_buffer_pct=0.05,
        min_relative_volume=1.18,
        stop_atr_multiple=1.05,
        target_rr=2.05,
        allow_failed_break_reversal=False,
    ),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _pct_change(sym_data: dict[str, Any]) -> float:
    change_pct = sym_data.get("change_pct")
    if change_pct is not None:
        return _safe_float(change_pct)

    price = _safe_float(sym_data.get("price"))
    prior_close = _safe_float(sym_data.get("prior_close"))
    if price > 0 and prior_close > 0:
        return ((price - prior_close) / prior_close) * 100.0
    return 0.0


def _relative_volume(sym_data: dict[str, Any]) -> float:
    explicit = sym_data.get("relative_volume")
    if explicit is not None:
        return _safe_float(explicit, 1.0)

    volume = _safe_float(sym_data.get("volume"))
    avg_volume = _safe_float(sym_data.get("avg_volume"))
    if volume > 0 and avg_volume > 0:
        return volume / avg_volume
    return 1.0


def _coerce_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        ts = _safe_float(value, default=float("nan"))
        if ts != ts:
            return None
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CT)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def _is_in_clock_window(now_local: datetime, start_hhmm: str, end_hhmm: str) -> bool:
    start = _parse_hhmm(start_hhmm)
    end = _parse_hhmm(end_hhmm)
    current = now_local.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _active_session(now_ct: datetime, symbol: str, params: dict[str, Any]) -> tuple[str | None, datetime]:
    if _is_in_clock_window(now_ct, str(params["session_start_ct"]), str(params["session_end_ct"])):
        return "us_day_session", now_ct

    now_et = now_ct.astimezone(ET)
    if symbol in {"MGC", "GC"} and _is_in_clock_window(
        now_et,
        str(params["asia_session_start_et"]),
        str(params["asia_session_end_et"]),
    ):
        if params.get("skip_sunday_session", True) and now_et.weekday() == 6:
            return None, now_et
        return "asia_session_orb", now_et

    return None, now_ct


def _first_available_change(market_data: dict[str, dict[str, Any]], symbols: Iterable[str]) -> float:
    for symbol in symbols:
        sym_data = market_data.get(symbol)
        if sym_data:
            return _pct_change(sym_data)
    return 0.0


def _infer_opening_range(
    sym_data: dict[str, Any],
    range_minutes: int,
    session_start_local: str,
    tzinfo: ZoneInfo,
) -> tuple[float, float, str] | None:
    high = _safe_float(sym_data.get("opening_range_high") or sym_data.get("orb_high"))
    low = _safe_float(sym_data.get("opening_range_low") or sym_data.get("orb_low"))
    if high > low > 0:
        return high, low, "precomputed"

    bars = sym_data.get("intraday_bars") or sym_data.get("bars") or []
    if not isinstance(bars, list) or not bars:
        return None

    start_time = _parse_hhmm(session_start_local)
    range_high = 0.0
    range_low = 0.0
    used = 0

    for bar in bars:
        if not isinstance(bar, dict):
            continue
        bar_dt = _coerce_dt(bar.get("timestamp"))
        if bar_dt is None:
            continue
        bar_dt = bar_dt.astimezone(tzinfo)
        session_dt = bar_dt.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
        if start_time.hour > 12 and bar_dt.hour < 12:
            session_dt -= timedelta(days=1)
        delta_minutes = (bar_dt - session_dt).total_seconds() / 60.0
        if delta_minutes < 0 or delta_minutes > range_minutes:
            continue

        bar_high = _safe_float(bar.get("high"))
        bar_low = _safe_float(bar.get("low"))
        if bar_high <= 0 or bar_low <= 0:
            continue

        range_high = max(range_high, bar_high)
        range_low = bar_low if range_low == 0 else min(range_low, bar_low)
        used += 1

    if used >= 2 and range_high > range_low > 0:
        return range_high, range_low, "bars"
    return None


class MGCAIOptimizedStrategy:
    """Adaptive Micro Gold breakout strategy with macro confirmation."""

    def __init__(self, params: dict[str, Any] | None = None):
        self._params = {**PARAMS, **(params or {})}

    def _macro_bias(
        self,
        market_data: dict[str, dict[str, Any]],
        scorecard: dict[str, Any] | None = None,
    ) -> float:
        bias = 0.0

        dollar_chg = _first_available_change(market_data, _DOLLAR_PROXIES)
        if dollar_chg < -0.10:
            bias += 0.24
        elif dollar_chg > 0.10:
            bias -= 0.24

        rate_up_chg = _first_available_change(market_data, _RATE_UP_PROXIES)
        rate_down_chg = _first_available_change(market_data, _RATE_DOWN_PROXIES)
        if rate_up_chg < -0.20 or rate_down_chg > 0.20:
            bias += 0.18
        elif rate_up_chg > 0.20 or rate_down_chg < -0.20:
            bias -= 0.18

        risk_chg = 0.0
        count = 0
        for symbol in _RISK_PROXIES:
            sym_data = market_data.get(symbol)
            if not sym_data:
                continue
            risk_chg += _pct_change(sym_data)
            count += 1
        if count:
            risk_chg /= count
            if risk_chg < -0.35:
                bias += 0.10
            elif risk_chg > 0.35:
                bias -= 0.10

        if scorecard:
            components = scorecard.get("component_scores", {})
            geopolitical = _safe_float(components.get("geopolitical_tension"))
            market_vol = _safe_float(components.get("market_volatility"))
            policy = _safe_float(components.get("policy_signals"))
            bias += min(0.18, geopolitical * 0.12 + market_vol * 0.08 + policy * 0.05)

        return _clamp(bias, -1.0, 1.0)

    def _select_profile(
        self,
        sym_data: dict[str, Any],
        scorecard: dict[str, Any] | None,
        macro_bias: float,
        rel_vol: float,
        session_name: str,
    ) -> StrategyProfile:
        if session_name == "asia_session_orb":
            return PROFILE_LIBRARY["asia_session_orb"]
        failed_break = str(sym_data.get("failed_breakout_direction") or "").strip().lower()
        if failed_break in {"up", "down"}:
            return PROFILE_LIBRARY["failed_break_reversal"]

        components = (scorecard or {}).get("component_scores", {})
        market_vol = _safe_float(components.get("market_volatility"))
        geo = _safe_float(components.get("geopolitical_tension"))
        abs_change = abs(_pct_change(sym_data))

        if rel_vol >= 1.6 or abs_change >= 0.70 or market_vol >= 0.55 or geo >= 0.45 or abs(macro_bias) >= 0.35:
            return PROFILE_LIBRARY["macro_event_orb"]
        return PROFILE_LIBRARY["balanced_orb"]

    def evaluate(
        self,
        symbol: str,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if market_data is None or symbol not in MGC_WATCHLIST:
            return None

        sym_data = market_data.get(symbol, {})
        price = _safe_float(sym_data.get("price"))
        if price <= 0:
            return None

        now_ct = _coerce_dt(sym_data.get("timestamp")) or datetime.now(timezone.utc).astimezone(CT)
        session_name, session_dt = _active_session(now_ct, symbol, self._params)
        if not session_name:
            return None

        rel_vol = _relative_volume(sym_data)
        macro_bias = self._macro_bias(market_data, scorecard)
        profile = self._select_profile(sym_data, scorecard, macro_bias, rel_vol, session_name)

        if session_name == "asia_session_orb":
            range_minutes = int(self._params["asia_range_minutes"])
            session_start = str(self._params["asia_session_start_et"])
            session_tz = ET
        else:
            range_minutes = int(self._params["range_minutes"])
            session_start = str(self._params["session_start_ct"])
            session_tz = CT

        orb = _infer_opening_range(
            sym_data,
            range_minutes=range_minutes,
            session_start_local=session_start,
            tzinfo=session_tz,
        )
        if orb is None:
            return None
        opening_high, opening_low, opening_source = orb
        opening_mid = (opening_high + opening_low) / 2.0
        opening_range_pct = ((opening_high - opening_low) / opening_mid) * 100.0 if opening_mid else 0.0
        if opening_range_pct < self._params["min_range_pct"] or opening_range_pct > self._params["max_range_pct"]:
            return None

        vwap = _safe_float(sym_data.get("vwap"), default=price)
        atr = _safe_float(sym_data.get("atr_14") or sym_data.get("atr"), default=(opening_high - opening_low) * 0.9)
        atr = max(atr, price * 0.0015)
        change_pct = _pct_change(sym_data)

        breakout_up = opening_high * (1.0 + profile.breakout_buffer_pct / 100.0)
        breakout_down = opening_low * (1.0 - profile.breakout_buffer_pct / 100.0)
        extension_pct = 0.0
        setup = ""
        direction = ""

        if price > breakout_up and rel_vol >= profile.min_relative_volume and price >= vwap and macro_bias >= self._params["macro_bias_floor"]:
            direction = "long"
            setup = "orb_breakout"
            extension_pct = ((price - opening_high) / price) * 100.0
        elif price < breakout_down and rel_vol >= profile.min_relative_volume and price <= vwap and macro_bias <= -self._params["macro_bias_floor"]:
            direction = "short"
            setup = "orb_breakdown"
            extension_pct = ((opening_low - price) / price) * 100.0
        elif profile.allow_failed_break_reversal:
            failed_break = str(sym_data.get("failed_breakout_direction") or "").strip().lower()
            if failed_break == "down" and price > opening_mid and price >= vwap and macro_bias > 0.10:
                direction = "long"
                setup = "failed_breakdown_reversal"
                extension_pct = ((price - opening_mid) / price) * 100.0
            elif failed_break == "up" and price < opening_mid and price <= vwap and macro_bias < -0.10:
                direction = "short"
                setup = "failed_breakout_reversal"
                extension_pct = ((opening_mid - price) / price) * 100.0

        if not direction:
            return None

        if extension_pct > self._params["max_extension_pct"]:
            return None

        regime_boost = 1.0 + min(abs(macro_bias), 0.5) * 0.45
        confidence = 0.46
        confidence += min(rel_vol, 2.5) * 0.10
        confidence += min(abs(change_pct), 1.5) * 0.06
        confidence += min(abs(macro_bias), 0.5) * 0.18
        confidence += 0.05 if "reversal" in setup else 0.03
        confidence = _clamp(confidence * regime_boost, 0.0, 0.92)

        stop_distance = max(atr * profile.stop_atr_multiple, (opening_high - opening_low) * 0.55)
        stop_loss_pct = -((stop_distance / price) * 100.0)
        take_profit_pct = (abs(stop_loss_pct) * profile.target_rr)

        base_notional = self._params["base_notional_futures"] if symbol in {"MGC", "GC"} else self._params["base_notional_proxy"]
        notional = base_notional * (0.75 + confidence * 0.5)

        return {
            "strategy": "mgc_ai_optimized",
            "symbol": symbol,
            "direction": direction,
            "notional_usd": round(notional, 2),
            "confidence_score": round(confidence, 3),
            "confidence": round(confidence, 3),
            "stop_loss_pct": round(max(stop_loss_pct, -1.8), 2),
            "take_profit_pct": round(min(take_profit_pct, 3.6), 2),
            "tier": "tier_2",
            "tier_size_multiplier": round(min(0.85, 0.45 + confidence * 0.35), 2),
            "account": "day_trade",
            "entry_signal": f"MGC AI {setup} on {symbol} — {profile.name}",
            "regime_boost": round(regime_boost, 3),
            "metadata": {
                "source": "instagram_quantcrawlerai_mgc_ai",
                "setup_type": setup,
                "profile": profile.name,
                "macro_bias": round(macro_bias, 3),
                "relative_volume": round(rel_vol, 3),
                "opening_range_high": round(opening_high, 3),
                "opening_range_low": round(opening_low, 3),
                "opening_range_source": opening_source,
                "opening_range_pct": round(opening_range_pct, 3),
                "trade_window_ct": f"{self._params['session_start_ct']}-{self._params['session_end_ct']}",
                "active_session": session_name,
                "active_session_local_time": session_dt.isoformat(),
                "asia_session_et": (
                    f"{self._params['asia_session_start_et']}-{self._params['asia_session_end_et']}"
                ),
                "asia_range_minutes": int(self._params["asia_range_minutes"]),
                "contract_preference": "MGC futures / GLD proxy",
            },
        }

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if market_data is None:
            return []

        ideas = []
        for symbol in MGC_WATCHLIST:
            idea = self.evaluate(symbol=symbol, market_data=market_data, scorecard=scorecard)
            if idea:
                ideas.append(idea)
        ideas.sort(key=lambda item: item.get("confidence_score", 0.0), reverse=True)
        return ideas


def evaluate_mgc_ai_optimized(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Strategy-engine style adapter."""
    params = dict((strat or {}).get("params", {}))
    strategy = MGCAIOptimizedStrategy(params=params)
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

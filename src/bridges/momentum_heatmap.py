#!/usr/bin/env python3
"""
Global Sentinel — Intraday Momentum Heatmap

Every 15 minutes during market hours, scores 40 symbols on:
- Price momentum (% from open, from prev close)
- Volume momentum (current vs 20-day avg)
- RSI (14-period)
- VWAP position (above/below)
- Sector relative strength
Composite score 0-100. Output to data/quantum_feed/momentum_heatmap.json.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("global_sentinel.momentum_heatmap")

# 40 tracked symbols with sector mapping
SYMBOLS_SECTORS = {
    "SPY": "index", "QQQ": "index", "IWM": "index",
    "NVDA": "tech", "AMD": "tech", "INTC": "tech", "AVGO": "tech", "MU": "tech",
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "META": "tech", "AMZN": "tech",
    "CRM": "tech", "NFLX": "tech",
    "TSLA": "consumer", "DIS": "consumer", "COST": "consumer", "HD": "consumer", "WMT": "consumer",
    "PLTR": "tech", "COIN": "fintech",
    "XLE": "energy_etf", "XOM": "energy", "CVX": "energy",
    "XLF": "financial_etf", "JPM": "financial", "GS": "financial", "V": "financial", "MA": "financial",
    "SOXL": "semi_etf", "TQQQ": "tech_etf",
    "TLT": "bonds", "GLD": "commodities", "UVXY": "volatility",
    "UNH": "healthcare", "JNJ": "healthcare", "PFE": "healthcare", "LLY": "healthcare",
    "BA": "industrial",
}

# Sector ETF mapping for relative strength
SECTOR_ETFS = {
    "tech": "QQQ", "consumer": "SPY", "energy": "XLE", "financial": "XLF",
    "healthcare": "XLV", "industrial": "XLI", "fintech": "QQQ",
    "semi_etf": "QQQ", "tech_etf": "QQQ", "index": "SPY",
    "energy_etf": "SPY", "financial_etf": "SPY",
    "bonds": "SPY", "commodities": "SPY", "volatility": "SPY",
}

ALL_SYMBOLS = list(SYMBOLS_SECTORS.keys())
# Add XLV, XLI for sector comparisons if not already tracked
EXTRA_ETFS = ["XLV", "XLI"]


class MomentumHeatmap:
    def __init__(self):
        self.repo_root = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.data_url = "https://data.alpaca.markets"
        self.output_path = self.repo_root / "data" / "quantum_feed" / "momentum_heatmap.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _api_get(self, url: str) -> Optional[Any]:
        """Authenticated GET to Alpaca data API."""
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", self.api_key)
        req.add_header("APCA-API-SECRET-KEY", self.api_secret)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"API error: {e}")
            return None

    def _get_snapshots(self, symbols: List[str]) -> Dict[str, Any]:
        """Get stock snapshots for multiple symbols."""
        all_snaps = {}
        # Alpaca supports comma-separated symbols in snapshots
        batch_size = 20
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            syms_str = ",".join(batch)
            url = f"{self.data_url}/v2/stocks/snapshots?symbols={syms_str}&feed=iex"
            data = self._api_get(url)
            if data and isinstance(data, dict):
                all_snaps.update(data)
            time.sleep(0.2)
        return all_snaps

    def _get_historical_bars(self, symbol: str, days: int = 25) -> List[Dict[str, Any]]:
        """Get daily bars for RSI and volume average calculation."""
        end = date.today()
        start = end - timedelta(days=days + 10)  # extra days for weekends
        url = (
            f"{self.data_url}/v2/stocks/{symbol}/bars"
            f"?timeframe=1Day&start={start.isoformat()}&end={end.isoformat()}"
            f"&limit={days + 5}&feed=iex"
        )
        data = self._api_get(url)
        if data and isinstance(data, dict):
            return data.get("bars", [])
        return []

    def _compute_rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        """Compute RSI from a list of closing prices."""
        if len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def _compute_score(
        self,
        pct_from_open: float,
        pct_from_prev_close: float,
        volume_ratio: float,
        rsi: Optional[float],
        above_vwap: bool,
        sector_rel: float,
    ) -> float:
        """Compute composite momentum score 0-100."""
        # Price momentum from open: -5% to +5% mapped to 0-100
        price_score = max(0, min(100, 50 + pct_from_open * 10))

        # Price momentum from prev close
        prev_close_score = max(0, min(100, 50 + pct_from_prev_close * 10))

        # Volume ratio: 0.5x = weak, 1.0x = normal, 2.0x+ = strong
        vol_score = max(0, min(100, volume_ratio * 40))

        # RSI: already 0-100, higher = more bullish momentum
        rsi_score = rsi if rsi is not None else 50

        # VWAP: above = bullish
        vwap_score = 65 if above_vwap else 35

        # Sector relative: -3% to +3% mapped
        sector_score = max(0, min(100, 50 + sector_rel * 15))

        # Weighted composite
        composite = (
            price_score * 0.25 +
            prev_close_score * 0.15 +
            vol_score * 0.15 +
            rsi_score * 0.20 +
            vwap_score * 0.10 +
            sector_score * 0.15
        )
        return round(max(0, min(100, composite)), 1)

    def run(self) -> Dict[str, Any]:
        """Run full momentum heatmap scan."""
        if not self.api_key or not self.api_secret:
            logger.error("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY")
            return {"error": "missing_api_keys"}

        # Fetch snapshots for all symbols + extra ETFs
        all_syms = list(set(ALL_SYMBOLS + EXTRA_ETFS))
        logger.info(f"Fetching snapshots for {len(all_syms)} symbols...")
        snapshots = self._get_snapshots(all_syms)
        logger.info(f"  Got snapshots for {len(snapshots)} symbols")

        # Pre-compute sector ETF performance for relative strength
        sector_etf_perf: Dict[str, float] = {}
        for etf_sym in set(SECTOR_ETFS.values()):
            snap = snapshots.get(etf_sym, {})
            if snap:
                daily = snap.get("dailyBar", {}) or {}
                prev = snap.get("prevDailyBar", {}) or {}
                cur_close = daily.get("c", 0)
                prev_close = prev.get("c", 0)
                if cur_close and prev_close:
                    sector_etf_perf[etf_sym] = round(
                        (cur_close - prev_close) / prev_close * 100, 3
                    )

        # Score each tracked symbol
        scored_symbols: List[Dict[str, Any]] = []
        sector_groups: Dict[str, List[Dict[str, Any]]] = {}

        for symbol in ALL_SYMBOLS:
            snap = snapshots.get(symbol)
            if not snap:
                logger.debug(f"  No snapshot for {symbol}")
                continue

            try:
                daily = snap.get("dailyBar", {}) or {}
                prev = snap.get("prevDailyBar", {}) or {}
                minute = snap.get("minuteBar", {}) or {}
                latest_trade = snap.get("latestTrade", {}) or {}

                current_price = latest_trade.get("p", 0) or daily.get("c", 0)
                open_price = daily.get("o", 0)
                prev_close = prev.get("c", 0)
                daily_volume = daily.get("v", 0) or 0
                vwap = daily.get("vw", 0) or 0

                if not current_price or not prev_close:
                    continue

                # Price momentum
                pct_from_open = round(
                    (current_price - open_price) / open_price * 100, 3
                ) if open_price else 0
                pct_from_prev = round(
                    (current_price - prev_close) / prev_close * 100, 3
                ) if prev_close else 0

                # VWAP position
                above_vwap = current_price > vwap if vwap else True

                # Volume ratio vs 20-day average
                hist_bars = self._get_historical_bars(symbol, days=25)
                volumes_20d = [b.get("v", 0) for b in hist_bars[:-1]][-20:]  # exclude today
                avg_volume_20d = sum(volumes_20d) / max(len(volumes_20d), 1) if volumes_20d else 1
                volume_ratio = round(daily_volume / max(avg_volume_20d, 1), 2)

                # RSI from daily closes
                closes = [b.get("c", 0) for b in hist_bars if b.get("c")]
                if current_price and closes and closes[-1] != current_price:
                    closes.append(current_price)
                rsi = self._compute_rsi(closes)

                # Sector relative strength
                sector = SYMBOLS_SECTORS.get(symbol, "index")
                sector_etf = SECTOR_ETFS.get(sector, "SPY")
                sector_perf = sector_etf_perf.get(sector_etf, 0)
                sector_rel = round(pct_from_prev - sector_perf, 3)

                # Composite score
                score = self._compute_score(
                    pct_from_open, pct_from_prev, volume_ratio,
                    rsi, above_vwap, sector_rel,
                )

                # Classification
                if score >= 70:
                    momentum_class = "strong_bullish"
                elif score >= 60:
                    momentum_class = "bullish"
                elif score <= 30:
                    momentum_class = "strong_bearish"
                elif score <= 40:
                    momentum_class = "bearish"
                else:
                    momentum_class = "neutral"

                entry = {
                    "symbol": symbol,
                    "sector": sector,
                    "price": round(current_price, 2),
                    "pct_from_open": pct_from_open,
                    "pct_from_prev_close": pct_from_prev,
                    "volume": daily_volume,
                    "volume_ratio_20d": volume_ratio,
                    "rsi_14": rsi,
                    "vwap": round(vwap, 2) if vwap else None,
                    "above_vwap": above_vwap,
                    "sector_relative": sector_rel,
                    "momentum_score": score,
                    "momentum_class": momentum_class,
                }
                scored_symbols.append(entry)
                sector_groups.setdefault(sector, []).append(entry)

                time.sleep(0.15)  # rate limit for historical bars calls

            except Exception as e:
                logger.error(f"  {symbol} error: {e}")
                continue

        # Sort by score descending
        scored_symbols.sort(key=lambda x: x["momentum_score"], reverse=True)

        # Top 5 leaders / bottom 5 laggards
        leaders = scored_symbols[:5]
        laggards = scored_symbols[-5:][::-1] if len(scored_symbols) >= 5 else scored_symbols[::-1]

        # Sector breakdown
        sector_breakdown = {}
        for sector, entries in sector_groups.items():
            avg_score = round(sum(e["momentum_score"] for e in entries) / len(entries), 1)
            sector_breakdown[sector] = {
                "avg_score": avg_score,
                "count": len(entries),
                "momentum_class": (
                    "bullish" if avg_score >= 60 else
                    "bearish" if avg_score <= 40 else
                    "neutral"
                ),
                "symbols": [e["symbol"] for e in sorted(entries, key=lambda x: x["momentum_score"], reverse=True)],
            }

        # Market breadth
        bullish_count = len([s for s in scored_symbols if s["momentum_score"] >= 60])
        bearish_count = len([s for s in scored_symbols if s["momentum_score"] <= 40])
        avg_score = round(
            sum(s["momentum_score"] for s in scored_symbols) / max(len(scored_symbols), 1), 1
        )

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanner": "momentum_heatmap",
            "symbols_scored": len(scored_symbols),
            "market_breadth": {
                "avg_momentum_score": avg_score,
                "bullish_count": bullish_count,
                "bearish_count": bearish_count,
                "neutral_count": len(scored_symbols) - bullish_count - bearish_count,
                "market_bias": (
                    "bullish" if bullish_count > bearish_count * 1.5 else
                    "bearish" if bearish_count > bullish_count * 1.5 else
                    "neutral"
                ),
            },
            "leaders_top5": [
                {"symbol": s["symbol"], "score": s["momentum_score"], "pct_chg": s["pct_from_prev_close"]}
                for s in leaders
            ],
            "laggards_bottom5": [
                {"symbol": s["symbol"], "score": s["momentum_score"], "pct_chg": s["pct_from_prev_close"]}
                for s in laggards
            ],
            "sector_breakdown": sector_breakdown,
            "heatmap": scored_symbols,
        }

        self.output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        logger.info(f"Wrote momentum heatmap ({len(scored_symbols)} symbols) to {self.output_path}")
        return result


def main():
    heatmap = MomentumHeatmap()
    result = heatmap.run()
    breadth = result.get("market_breadth", {})
    print(json.dumps({
        "symbols_scored": result.get("symbols_scored", 0),
        "avg_score": breadth.get("avg_momentum_score", 0),
        "market_bias": breadth.get("market_bias", "unknown"),
        "leaders": result.get("leaders_top5", []),
        "laggards": result.get("laggards_bottom5", []),
    }, indent=2))


if __name__ == "__main__":
    main()

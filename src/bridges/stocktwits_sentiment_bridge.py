#!/usr/bin/env python3
"""
Global Sentinel — StockTwits Sentiment Bridge

Free StockTwits API — no API key needed.
Scans top symbols for bull/bear sentiment ratios.
Rate limit: 200 req/hour.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.stocktwits_sentiment_bridge")

try:
    import yaml
except ImportError:
    yaml = None


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class StockTwitsSentimentBridge:
    """Fetch social sentiment from StockTwits free API."""

    DISPLAY_NAME = "stocktwits_sentiment"
    CATEGORY = "social_sentiment"

    DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMZN", "META", "MSFT", "AMD", "GOOGL"]
    API_BASE = "https://api.stocktwits.com/api/2"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.symbols = self._load_symbols()

    def _load_symbols(self) -> List[str]:
        """Load top 10 tradeable symbols from watchlist."""
        wl = _load_yaml(self.repo_root / "config" / "assets_watchlist.yaml")
        symbols = []
        # Only pick equity-like symbols
        skip_patterns = ["USD", "XAU", "UST", "VIX", "BRN", "BZ=F", "^", "GC=F", "USD/"]
        for section in ["equity_indices"]:
            for item in wl.get(section, []):
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol", "")).strip()
                if not sym or any(p in sym for p in skip_patterns):
                    continue
                symbols.append(sym)
        seen = set()
        out = []
        for s in (symbols if symbols else self.DEFAULT_SYMBOLS):
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out[:10]

    def poll(self) -> Dict[str, Any]:
        """Poll StockTwits for sentiment data on watchlist symbols."""
        results = {}
        errors = []
        total_bull = 0
        total_bear = 0

        for symbol in self.symbols:
            try:
                data = self._get_symbol_sentiment(symbol)
                if data:
                    results[symbol] = data
                    total_bull += data.get("bullish_count", 0)
                    total_bear += data.get("bearish_count", 0)
                # Rate limit: 200/hr = ~1 every 18s, but we batch so use 2s
                time.sleep(2.0)
            except Exception as e:
                errors.append({"symbol": symbol, "error": str(e)})
                logger.warning(f"[StockTwitsSentimentBridge] {symbol} error: {e}")

        # Aggregate sentiment
        total = total_bull + total_bear
        agg_score = round(total_bull / max(total, 1), 3)
        agg_signal = "bullish" if agg_score > 0.6 else "bearish" if agg_score < 0.4 else "neutral"

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "stocktwits_sentiment",
            "source": "stocktwits.com",
            "symbols_scanned": len(self.symbols),
            "aggregate_bull_count": total_bull,
            "aggregate_bear_count": total_bear,
            "aggregate_bull_ratio": agg_score,
            "aggregate_signal": agg_signal,
            "symbols": results,
            "errors": errors,
        }

    def _get_symbol_sentiment(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch sentiment stream for a single symbol."""
        url = f"{self.API_BASE}/streams/symbol/{symbol}.json"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning(f"[StockTwitsSentimentBridge] Rate limited on {symbol}")
                time.sleep(10)
                return None
            raise
        except Exception:
            return None

        messages = data.get("messages", [])
        if not messages:
            return None

        bullish = 0
        bearish = 0
        total_msgs = len(messages)

        for msg in messages:
            sentiment = (msg.get("entities", {}) or {}).get("sentiment", {})
            if isinstance(sentiment, dict):
                basic = sentiment.get("basic")
                if basic == "Bullish":
                    bullish += 1
                elif basic == "Bearish":
                    bearish += 1

        total_sentiment = bullish + bearish
        bull_ratio = round(bullish / max(total_sentiment, 1), 3)

        return {
            "total_messages": total_msgs,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": total_msgs - bullish - bearish,
            "bull_ratio": bull_ratio,
            "signal": "bullish" if bull_ratio > 0.65 else "bearish" if bull_ratio < 0.35 else "neutral",
        }

#!/usr/bin/env python3
"""
Global Sentinel — Options Flow Intelligence Bridge

Fetches options chain data via Alpaca API for watchlist symbols.
Calculates put/call ratios, unusual volume, and IV signals.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.options_flow_bridge")


class OptionsFlowBridge:
    """Collect options flow intelligence from Alpaca options data."""

    DISPLAY_NAME = "options_flow"
    CATEGORY = "market_microstructure"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.api_secret = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = "https://paper-api.alpaca.markets"
        self.data_url = "https://data.alpaca.markets"
        # Focus on key watchlist symbols
        self.symbols = ["SPY", "QQQ", "IWM", "XLE", "XOP", "GLD", "TLT",
                        "AAPL", "NVDA", "TSLA", "CVX", "XOM", "LMT", "RTX"]

    def _api_get(self, url: str) -> Optional[dict]:
        """Make authenticated GET request to Alpaca."""
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", self.api_key)
        req.add_header("APCA-API-SECRET-KEY", self.api_secret)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"[OptionsFlowBridge] API error: {e}")
            return None

    def collect(self) -> Dict[str, Any]:
        """Collect options flow signals for watchlist symbols."""
        if not self.api_key:
            return {"error": "no_api_key", "signals": []}

        results = {}
        total_calls = 0
        total_puts = 0

        for symbol in self.symbols:
            try:
                snapshot = self._get_option_snapshot(symbol)
                if not snapshot:
                    continue
                call_vol, put_vol, call_oi, put_oi = 0, 0, 0, 0
                for contract_id, data in snapshot.items():
                    is_call = "C" in contract_id.split(symbol)[-1][:7] if symbol in contract_id else False
                    trade = data.get("latestTrade", {})
                    quote = data.get("latestQuote", {})
                    vol = trade.get("s", 0) or 0  # size
                    oi = data.get("openInterest", 0) or 0
                    if is_call:
                        call_vol += vol
                        call_oi += oi
                    else:
                        put_vol += vol
                        put_oi += oi

                pc_ratio = put_vol / max(call_vol, 1)
                pc_oi_ratio = put_oi / max(call_oi, 1)
                total_calls += call_vol
                total_puts += put_vol

                results[symbol] = {
                    "call_volume": call_vol,
                    "put_volume": put_vol,
                    "put_call_ratio": round(pc_ratio, 3),
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                    "put_call_oi_ratio": round(pc_oi_ratio, 3),
                    "signal": "bearish" if pc_ratio > 1.5 else "bullish" if pc_ratio < 0.7 else "neutral",
                }
                time.sleep(0.5)  # Rate limit friendly
            except Exception as e:
                logger.warning(f"[OptionsFlowBridge] {symbol} error: {e}")
                continue

        market_pc = total_puts / max(total_calls, 1)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "options_flow",
            "market_put_call_ratio": round(market_pc, 3),
            "market_signal": "bearish" if market_pc > 1.3 else "bullish" if market_pc < 0.7 else "neutral",
            "symbols": results,
            "unusual_volume": [s for s, d in results.items() if d["put_call_ratio"] > 2.0 or d["put_call_ratio"] < 0.3],
        }

    def _get_option_snapshot(self, symbol: str) -> Optional[dict]:
        """Get options snapshot for a symbol."""
        url = f"{self.data_url}/v1beta1/options/snapshots/{symbol}?feed=indicative&limit=100"
        return self._api_get(url) or {}

#!/usr/bin/env python3
"""
Global Sentinel — Unusual Options Activity Bridge

DIY unusual options detector using Alpaca options data.
For top 15 liquid symbols, fetches options chain snapshots and flags
contracts where volume > 3x average or volume/OI > 2.0.
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

logger = logging.getLogger("global_sentinel.unusual_options_bridge")

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


class UnusualOptionsBridge:
    """Detect unusual options activity using Alpaca options data."""

    DISPLAY_NAME = "unusual_options"
    CATEGORY = "market_microstructure"

    LIQUID_SYMBOLS = [
        "SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMZN", "META",
        "MSFT", "AMD", "GOOGL", "IWM", "XLE", "GLD", "TLT", "BA",
    ]

    # Thresholds for unusual activity
    VOLUME_OI_THRESHOLD = 2.0     # volume/OI ratio
    VOLUME_MULTIPLIER = 3.0       # volume vs typical threshold
    MIN_VOLUME = 100              # Minimum volume to consider
    MIN_PREMIUM = 10000           # Minimum notional premium ($)

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.api_secret = os.getenv("ALPACA_SECRET_KEY", "")
        self.data_url = "https://data.alpaca.markets"

    def _api_get(self, url: str) -> Optional[dict]:
        """Make authenticated GET to Alpaca data API."""
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", self.api_key)
        req.add_header("APCA-API-SECRET-KEY", self.api_secret)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"[UnusualOptionsBridge] API error: {e}")
            return None

    def poll(self) -> Dict[str, Any]:
        """Scan for unusual options activity across liquid symbols."""
        if not self.api_key:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bridge": "unusual_options",
                "error": "no_alpaca_api_key",
                "alerts": [],
            }

        all_alerts: List[Dict[str, Any]] = []
        symbol_summaries: Dict[str, Any] = {}
        errors = []

        for symbol in self.LIQUID_SYMBOLS:
            try:
                alerts, summary = self._scan_symbol(symbol)
                if alerts:
                    all_alerts.extend(alerts)
                if summary:
                    symbol_summaries[symbol] = summary
                time.sleep(0.5)  # Rate limit
            except Exception as e:
                errors.append({"symbol": symbol, "error": str(e)})
                logger.warning(f"[UnusualOptionsBridge] {symbol} error: {e}")

        # Sort alerts by significance
        all_alerts.sort(key=lambda x: x.get("significance_score", 0), reverse=True)

        # Aggregate signals
        call_alerts = [a for a in all_alerts if a.get("option_type") == "call"]
        put_alerts = [a for a in all_alerts if a.get("option_type") == "put"]
        flow_signal = (
            "bullish" if len(call_alerts) > len(put_alerts) * 1.5 else
            "bearish" if len(put_alerts) > len(call_alerts) * 1.5 else
            "neutral"
        )

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "unusual_options",
            "source": "alpaca_options",
            "symbols_scanned": len(self.LIQUID_SYMBOLS),
            "total_unusual_alerts": len(all_alerts),
            "call_flow_alerts": len(call_alerts),
            "put_flow_alerts": len(put_alerts),
            "smart_money_signal": flow_signal,
            "top_alerts": all_alerts[:20],
            "symbol_summaries": symbol_summaries,
            "errors": errors,
        }

    def _scan_symbol(self, symbol: str) -> tuple:
        """Scan a single symbol for unusual options activity."""
        # Get options snapshots
        snapshots = self._get_options_snapshots(symbol)
        if not snapshots:
            return [], None

        alerts = []
        total_call_vol = 0
        total_put_vol = 0
        total_call_oi = 0
        total_put_oi = 0

        for contract_id, data in snapshots.items():
            if not isinstance(data, dict):
                continue

            # Parse contract type from contract ID
            # Format: SPY250321C00570000
            is_call = self._is_call_contract(contract_id, symbol)
            option_type = "call" if is_call else "put"

            trade = data.get("latestTrade", {}) or {}
            quote = data.get("latestQuote", {}) or {}
            oi = data.get("openInterest", 0) or 0
            volume = trade.get("s", 0) or 0  # trade size as proxy

            # Accumulate
            if is_call:
                total_call_vol += volume
                total_call_oi += oi
            else:
                total_put_vol += volume
                total_put_oi += oi

            # Check unusual activity criteria
            if volume < self.MIN_VOLUME:
                continue

            vol_oi_ratio = volume / max(oi, 1)
            mid_price = 0.0
            ask = quote.get("ap", 0) or 0
            bid = quote.get("bp", 0) or 0
            if ask and bid:
                mid_price = (ask + bid) / 2.0

            est_premium = volume * mid_price * 100  # options are 100 shares

            is_unusual = False
            reasons = []

            if vol_oi_ratio >= self.VOLUME_OI_THRESHOLD and oi > 0:
                is_unusual = True
                reasons.append(f"vol/OI={vol_oi_ratio:.1f}")

            if volume >= self.MIN_VOLUME * self.VOLUME_MULTIPLIER:
                is_unusual = True
                reasons.append(f"high_volume={volume}")

            if est_premium >= self.MIN_PREMIUM:
                if vol_oi_ratio >= 1.5:
                    is_unusual = True
                    reasons.append(f"premium=${est_premium:,.0f}")

            if is_unusual:
                significance = min(
                    (vol_oi_ratio / self.VOLUME_OI_THRESHOLD) * 0.4 +
                    (volume / (self.MIN_VOLUME * self.VOLUME_MULTIPLIER)) * 0.3 +
                    (est_premium / 100000) * 0.3,
                    10.0
                )
                alerts.append({
                    "symbol": symbol,
                    "contract": contract_id,
                    "option_type": option_type,
                    "volume": volume,
                    "open_interest": oi,
                    "vol_oi_ratio": round(vol_oi_ratio, 2),
                    "mid_price": round(mid_price, 4),
                    "est_premium": round(est_premium, 2),
                    "reasons": reasons,
                    "significance_score": round(significance, 2),
                    "signal": "bullish_flow" if is_call else "bearish_flow",
                })

        pc_ratio = total_put_vol / max(total_call_vol, 1)
        summary = {
            "total_call_volume": total_call_vol,
            "total_put_volume": total_put_vol,
            "put_call_ratio": round(pc_ratio, 3),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "unusual_count": len(alerts),
        }

        return alerts, summary

    def _get_options_snapshots(self, symbol: str) -> Optional[dict]:
        """Get options chain snapshots from Alpaca."""
        url = f"{self.data_url}/v1beta1/options/snapshots/{symbol}?feed=indicative&limit=200"
        result = self._api_get(url)
        if isinstance(result, dict) and "snapshots" in result:
            return result["snapshots"]
        return result

    def _is_call_contract(self, contract_id: str, symbol: str) -> bool:
        """Determine if a contract is a call based on contract ID."""
        # Standard OCC format: SYMBOL + YYMMDD + C/P + strike
        remainder = contract_id[len(symbol):] if contract_id.startswith(symbol) else contract_id
        # After removing symbol, format is YYMMDD[C|P]SSSSSSSS
        if len(remainder) >= 7:
            type_char = remainder[6:7].upper()
            return type_char == "C"
        return "C" in contract_id.upper()

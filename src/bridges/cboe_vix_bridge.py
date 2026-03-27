#!/usr/bin/env python3
"""
Global Sentinel — CBOE VIX Data Bridge

Tracks VIX term structure (VIX, VIX9D, VIX3M, VIX6M, VIX1Y),
computes contango/backwardation ratios, detects regime shifts,
and fetches CBOE put/call ratio data.

Output: data/quantum_feed/cboe_vix_data.json
Tier 2, trust 0.8, TTL 30 min
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("global_sentinel.cboe_vix_bridge")

_VIX_SYMBOLS = {
    "VIX": "^VIX",
    "VIX9D": "^VIX9D",
    "VIX3M": "^VIX3M",
    "VIX6M": "^VIX6M",
    "VIX1Y": "^VIX1Y",
}


def _fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (GlobalSentinel/1.0)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return None


class CBOEVixBridge:
    """Fetch CBOE VIX term structure and put/call ratio data."""

    DISPLAY_NAME = "cboe_vix"
    CATEGORY = "market_volatility"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "cboe_vix_data.json"

    def _get_yahoo_vix(self, symbol: str) -> Optional[float]:
        """Get a VIX index value from Yahoo Finance."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        data = _fetch_json(url)
        if data and "chart" in data:
            try:
                return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return None

    def _get_term_structure(self) -> Dict[str, Optional[float]]:
        """Fetch all VIX term structure points."""
        result = {}
        for name, symbol in _VIX_SYMBOLS.items():
            result[name] = self._get_yahoo_vix(symbol)
        return result

    def _compute_contango_ratios(self, ts: Dict[str, Optional[float]]) -> Dict[str, Any]:
        """Compute contango/backwardation ratios between term structure points."""
        ratios = {}
        pairs = [
            ("VIX9D_to_VIX", "VIX9D", "VIX"),
            ("VIX_to_VIX3M", "VIX", "VIX3M"),
            ("VIX3M_to_VIX6M", "VIX3M", "VIX6M"),
            ("VIX6M_to_VIX1Y", "VIX6M", "VIX1Y"),
            ("front_to_back", "VIX9D", "VIX6M"),
        ]
        for label, near, far in pairs:
            near_val = ts.get(near)
            far_val = ts.get(far)
            if near_val and far_val and far_val > 0:
                ratio = round(near_val / far_val, 4)
                ratios[label] = {
                    "ratio": ratio,
                    "state": "backwardation" if ratio > 1.0 else "contango",
                    "near": near_val,
                    "far": far_val,
                }
            else:
                ratios[label] = {"ratio": None, "state": "unknown", "near": near_val, "far": far_val}
        return ratios

    def _detect_regime(self, ts: Dict[str, Optional[float]], ratios: Dict[str, Any]) -> Dict[str, Any]:
        """Detect VIX volatility regime from term structure shape."""
        vix = ts.get("VIX")
        if vix is None:
            return {"regime": "unknown", "description": "VIX data unavailable"}

        backwardation_count = sum(1 for v in ratios.values()
                                  if isinstance(v, dict) and v.get("state") == "backwardation")
        total_pairs = sum(1 for v in ratios.values()
                         if isinstance(v, dict) and v.get("ratio") is not None)

        if vix > 35 and backwardation_count >= 2:
            regime = "panic"
            desc = f"VIX at {vix:.1f} with term structure inversion — extreme fear"
        elif vix > 25 and backwardation_count >= 1:
            regime = "elevated_stress"
            desc = f"VIX at {vix:.1f} with partial backwardation — elevated stress"
        elif vix > 20:
            regime = "cautious"
            desc = f"VIX at {vix:.1f} — above average volatility"
        elif vix < 13 and backwardation_count == 0:
            regime = "complacency"
            desc = f"VIX at {vix:.1f} with steep contango — extreme complacency"
        elif vix < 16 and backwardation_count == 0:
            regime = "low_vol"
            desc = f"VIX at {vix:.1f} in contango — low volatility regime"
        else:
            regime = "normal"
            desc = f"VIX at {vix:.1f} — normal regime"

        return {
            "regime": regime,
            "vix_level": vix,
            "backwardation_points": backwardation_count,
            "total_measured_pairs": total_pairs,
            "description": desc,
        }

    def _get_put_call_ratio(self) -> Dict[str, Any]:
        """Fetch CBOE put/call ratio."""
        pcr_data = {}

        # Try Yahoo Finance for CBOE put/call indices
        for name, symbol in [("total_pcr", "^PCALL"), ("equity_pcr", "^EPCALL")]:
            val = self._get_yahoo_vix(symbol)
            if val is not None:
                pcr_data[name] = {
                    "value": val,
                    "signal": "bearish" if val > 1.2 else "bullish" if val < 0.7 else "neutral",
                }

        # Fallback: CBOE delayed quotes
        if not pcr_data:
            url = "https://cdn.cboe.com/api/global/delayed_quotes/options/_pcr.json"
            data = _fetch_json(url)
            if data and isinstance(data, dict):
                try:
                    total = data.get("data", {}).get("total_put_call_ratio")
                    equity = data.get("data", {}).get("equity_put_call_ratio")
                    if total:
                        pcr_data["total_pcr"] = {
                            "value": float(total),
                            "signal": "bearish" if float(total) > 1.2 else "bullish" if float(total) < 0.7 else "neutral",
                        }
                    if equity:
                        pcr_data["equity_pcr"] = {
                            "value": float(equity),
                            "signal": "bearish" if float(equity) > 1.2 else "bullish" if float(equity) < 0.7 else "neutral",
                        }
                except (ValueError, TypeError):
                    pass

        return pcr_data

    def poll(self) -> Dict[str, Any]:
        """Poll CBOE VIX term structure and put/call ratio data."""
        ts = self._get_term_structure()
        ratios = self._compute_contango_ratios(ts)
        regime = self._detect_regime(ts, ratios)
        pcr = self._get_put_call_ratio()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "cboe_vix",
            "data": {
                "term_structure": ts,
                "contango_ratios": ratios,
                "regime": regime,
                "put_call_ratio": pcr,
            },
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2))
        logger.info(f"[CBOEVixBridge] Regime: {regime.get('regime')}, VIX: {ts.get('VIX')}")

        return result

#!/usr/bin/env python3
"""Global Sentinel — VIX Term Structure Bridge

Fetches VIX spot and term structure data for volatility regime detection.

Data sources (in priority order):
  1. Yahoo Finance VIX indices (^VIX, ^VIX9D, ^VIX3M, ^VIX6M) — free, reliable
  2. CBOE CDN delayed quotes (legacy, currently returning 403 as of 2026-03)
  3. FRED API for VIX spot (requires FRED_API_KEY)

Calculates contango/backwardation ratio for regime signals.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("global_sentinel.vix_term_structure_bridge")

# Yahoo Finance VIX index symbols for term structure approximation.
# ^VIX9D  = 9-day VIX   (near-term, proxy for front-month)
# ^VIX    = 30-day VIX   (spot)
# ^VIX3M  = 3-month VIX  (proxy for second/third month futures)
# ^VIX6M  = 6-month VIX  (longer-dated)
_YAHOO_VIX_SYMBOLS = ["^VIX9D", "^VIX", "^VIX3M", "^VIX6M"]

# Legacy CBOE endpoint (403 since early 2026 — kept as fallback)
_CBOE_TERM_STRUCTURE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/term_structure/VIX.json"


class VixTermStructureBridge:
    DISPLAY_NAME = "vix_term_structure"
    CATEGORY = "market_volatility"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.fred_key = os.getenv("FRED_API_KEY", "")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _fetch_json(self, url: str, timeout: int = 15,
                    headers: Optional[Dict[str, str]] = None) -> Optional[dict]:
        hdrs = {"User-Agent": "Mozilla/5.0 (GlobalSentinel/1.0)"}
        if headers:
            hdrs.update(headers)
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"[VixTermStructure] fetch error for {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # VIX spot
    # ------------------------------------------------------------------

    def _get_vix_spot_yahoo(self) -> Optional[float]:
        """Get VIX spot from Yahoo Finance (no API key needed)."""
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
        data = self._fetch_json(url)
        if data and "chart" in data:
            try:
                return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return None

    def _get_vix_spot_fred(self) -> Optional[float]:
        """Get VIX spot from FRED (requires FRED_API_KEY)."""
        if not self.fred_key:
            return None
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=VIXCLS&sort_order=desc&limit=1"
            f"&api_key={self.fred_key}&file_type=json"
        )
        data = self._fetch_json(url)
        if data and data.get("observations"):
            val = data["observations"][0].get("value", ".")
            return float(val) if val != "." else None
        return None

    def _get_vix_spot(self) -> Optional[float]:
        """Get VIX spot — try Yahoo first, fall back to FRED."""
        spot = self._get_vix_spot_yahoo()
        if spot is not None:
            return spot
        return self._get_vix_spot_fred()

    # ------------------------------------------------------------------
    # Term structure — Yahoo Finance indices
    # ------------------------------------------------------------------

    def _get_yahoo_term_structure(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch VIX term structure using Yahoo Finance VIX index variants.

        Returns a list of dicts with 'symbol', 'tenor', and 'price' keys,
        sorted by tenor (short to long).
        """
        points: List[Dict[str, Any]] = []
        tenor_map = {
            "^VIX9D": ("9d", 9),
            "^VIX": ("30d", 30),
            "^VIX3M": ("3m", 90),
            "^VIX6M": ("6m", 180),
        }
        for sym in _YAHOO_VIX_SYMBOLS:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            data = self._fetch_json(url)
            if not data or "chart" not in data:
                continue
            try:
                price = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
                label, days = tenor_map[sym]
                points.append({
                    "symbol": sym,
                    "tenor": label,
                    "tenor_days": days,
                    "price": price,
                })
            except (KeyError, IndexError, TypeError, ValueError):
                continue

        if len(points) < 2:
            return None
        points.sort(key=lambda x: x["tenor_days"])
        return points

    # ------------------------------------------------------------------
    # Term structure — CBOE (legacy fallback)
    # ------------------------------------------------------------------

    def _get_cboe_term_structure(self) -> Optional[List[Dict[str, Any]]]:
        """Legacy CBOE CDN endpoint (currently 403, kept as fallback)."""
        data = self._fetch_json(_CBOE_TERM_STRUCTURE_URL)
        if not data or "data" not in data:
            return None
        futures: List[Dict[str, Any]] = []
        for item in data["data"]:
            try:
                futures.append({
                    "symbol": item.get("symbol", ""),
                    "expiration": item.get("expiration_date", ""),
                    "price": float(item.get("settle_price") or item.get("last_price") or 0),
                })
            except (ValueError, TypeError):
                continue
        futures.sort(key=lambda x: x.get("expiration", ""))
        return futures if len(futures) >= 2 else None

    # ------------------------------------------------------------------
    # Main collect
    # ------------------------------------------------------------------

    def collect(self) -> Dict[str, Any]:
        vix_spot = self._get_vix_spot()

        result: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "vix_term_structure",
            "vix_spot": vix_spot,
            "futures_count": 0,
            "data_source": "none",
        }

        # --- Try Yahoo term structure first ---
        yahoo_points = self._get_yahoo_term_structure()
        if yahoo_points and len(yahoo_points) >= 2:
            result["data_source"] = "yahoo_vix_indices"
            result["futures_count"] = len(yahoo_points)
            result["term_points"] = yahoo_points

            # Use 9-day (near-term) and 3-month (mid-term) for contango calc
            near = next((p for p in yahoo_points if p["tenor"] == "9d"), None)
            mid = next((p for p in yahoo_points if p["tenor"] == "3m"), None)
            # Fallback: use first two points
            if not near or not mid:
                near, mid = yahoo_points[0], yahoo_points[1]

            vx1 = near["price"]
            vx2 = mid["price"]
            if vx1 > 0:
                ratio = round(vx2 / vx1, 4)
                result["vx1"] = vx1
                result["vx1_label"] = near.get("tenor", "near")
                result["vx2"] = vx2
                result["vx2_label"] = mid.get("tenor", "mid")
                result["vx2_vx1_ratio"] = ratio
                result["structure"] = (
                    "contango" if ratio > 1.02
                    else "backwardation" if ratio < 0.98
                    else "flat"
                )
                result["regime_signal"] = (
                    "risk_on" if ratio > 1.05
                    else "crisis" if ratio < 0.95
                    else "neutral"
                )
        else:
            # --- Fallback to CBOE ---
            cboe_futures = self._get_cboe_term_structure()
            if cboe_futures and len(cboe_futures) >= 2:
                result["data_source"] = "cboe_cdn"
                result["futures_count"] = len(cboe_futures)
                vx1 = cboe_futures[0]["price"]
                vx2 = cboe_futures[1]["price"]
                if vx1 > 0:
                    ratio = round(vx2 / vx1, 4)
                    result["vx1"] = vx1
                    result["vx2"] = vx2
                    result["vx2_vx1_ratio"] = ratio
                    result["structure"] = (
                        "contango" if ratio > 1.02
                        else "backwardation" if ratio < 0.98
                        else "flat"
                    )
                    result["regime_signal"] = (
                        "risk_on" if ratio > 1.05
                        else "crisis" if ratio < 0.95
                        else "neutral"
                    )

        if vix_spot:
            result["vix_level"] = (
                "extreme_fear" if vix_spot > 35
                else "elevated" if vix_spot > 25
                else "normal" if vix_spot > 15
                else "complacent"
            )

        return result


if __name__ == "__main__":
    import pprint
    b = VixTermStructureBridge()
    pprint.pprint(b.collect())

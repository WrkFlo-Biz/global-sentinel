#!/usr/bin/env python3
"""
Global Sentinel — Earnings Calendar Bridge

Uses Finnhub API (existing FINNHUB_API_KEY) for:
- Upcoming earnings calendar dates
- Earnings surprise data for recently reported companies
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.earnings_calendar_bridge")

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


class EarningsCalendarBridge:
    """Fetch earnings calendar and surprise data from Finnhub."""

    DISPLAY_NAME = "earnings_calendar"
    CATEGORY = "fundamental_intelligence"
    API_BASE = "https://finnhub.io/api/v1"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.api_key = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_KEY", "")
        # Load watchlist symbols for surprise checks
        wl = _load_yaml(self.repo_root / "config" / "assets_watchlist.yaml")
        self.watchlist_symbols = self._extract_equity_symbols(wl)

    def _extract_equity_symbols(self, wl: Dict[str, Any]) -> List[str]:
        symbols = []
        skip = ["USD", "XAU", "UST", "VIX", "BRN", "BZ=F", "^", "GC=F", "USD/"]
        for section in ["equity_indices", "aviation_travel", "travel_hospitality",
                        "supply_chain", "insurance_risk"]:
            for item in wl.get(section, []):
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol", "")).strip()
                if sym and not any(p in sym for p in skip):
                    symbols.append(sym)
        seen = set()
        return [s for s in symbols if s not in seen and not seen.add(s)]

    def _api_get(self, endpoint: str, params: Dict[str, str]) -> Any:
        """Make authenticated GET to Finnhub."""
        params["token"] = self.api_key
        url = f"{self.API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def poll(self) -> Dict[str, Any]:
        """Poll for upcoming earnings and recent surprises."""
        if not self.api_key:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bridge": "earnings_calendar",
                "error": "no_finnhub_api_key",
                "upcoming": [],
                "surprises": [],
            }

        now = datetime.now(timezone.utc)
        upcoming = []
        surprises = []
        errors = []

        # 1. Get earnings calendar for next 7 days
        try:
            from_date = now.strftime("%Y-%m-%d")
            to_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")
            cal_data = self._api_get("calendar/earnings", {
                "from": from_date,
                "to": to_date,
            })
            earnings_list = (cal_data or {}).get("earningsCalendar", [])
            # Filter to watchlist symbols if present
            watchset = set(self.watchlist_symbols)
            for entry in earnings_list:
                sym = entry.get("symbol", "")
                rec = {
                    "symbol": sym,
                    "date": entry.get("date"),
                    "hour": entry.get("hour", "unknown"),  # bmo/amc
                    "eps_estimate": entry.get("epsEstimate"),
                    "revenue_estimate": entry.get("revenueEstimate"),
                    "quarter": entry.get("quarter"),
                    "year": entry.get("year"),
                    "in_watchlist": sym in watchset,
                }
                upcoming.append(rec)
            time.sleep(0.5)
        except Exception as e:
            errors.append({"source": "earnings_calendar", "error": str(e)})
            logger.warning(f"[EarningsCalendarBridge] Calendar error: {e}")

        # 2. Get earnings surprises for watchlist symbols (last quarter)
        surprise_symbols = [s for s in self.watchlist_symbols[:15]
                           if not s.startswith("X") and len(s) <= 5]  # Skip ETFs
        for symbol in surprise_symbols:
            try:
                data = self._api_get("stock/earnings", {"symbol": symbol, "limit": "4"})
                if isinstance(data, list) and data:
                    latest = data[0]
                    actual = latest.get("actual")
                    estimate = latest.get("estimate")
                    if actual is not None and estimate is not None:
                        surprise_pct = round(
                            ((actual - estimate) / max(abs(estimate), 0.001)) * 100, 2
                        ) if estimate != 0 else 0.0
                        surprises.append({
                            "symbol": symbol,
                            "period": latest.get("period"),
                            "actual_eps": actual,
                            "estimate_eps": estimate,
                            "surprise_pct": surprise_pct,
                            "signal": "beat" if surprise_pct > 5 else "miss" if surprise_pct < -5 else "inline",
                        })
                time.sleep(0.3)  # Finnhub rate limit
            except Exception as e:
                errors.append({"source": f"surprise_{symbol}", "error": str(e)})
                continue

        # Summarize
        watchlist_upcoming = [e for e in upcoming if e.get("in_watchlist")]
        big_beats = [s for s in surprises if s.get("surprise_pct", 0) > 10]
        big_misses = [s for s in surprises if s.get("surprise_pct", 0) < -10]

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "earnings_calendar",
            "source": "finnhub.io",
            "upcoming_count": len(upcoming),
            "watchlist_upcoming_count": len(watchlist_upcoming),
            "upcoming_next_7d": upcoming[:30],
            "watchlist_upcoming": watchlist_upcoming,
            "surprises": surprises,
            "big_beats": big_beats,
            "big_misses": big_misses,
            "errors": errors,
        }

#!/usr/bin/env python3
"""
Global Sentinel — Equity Screener Bridge

Uses finviz to screen for stocks matching high-conviction criteria:
  - Unusual volume (>2x average)
  - New 52-week highs/lows
  - Large insider buying
  - Heavy institutional accumulation
  - High short interest with improving fundamentals

Output: data/quantum_feed/screener_picks.json
Tier 2, trust 0.6, TTL 360 min
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.equity_screener_bridge")

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "screener_picks.json"


# ---------------------------------------------------------------------------
# Screener categories
# ---------------------------------------------------------------------------

def _screen_unusual_volume() -> List[Dict[str, Any]]:
    """Stocks with relative volume > 2x average."""
    try:
        from finviz.screener import Screener
        filters = ["sh_relvol_o2", "sh_avgvol_o400"]  # rel vol > 2, avg vol > 400K
        screen = Screener(filters=filters, order="-relativevolume", rows=25)
        results = []
        for row in screen.data[:25]:
            results.append({
                "ticker": row.get("Ticker", ""),
                "company": row.get("Company", ""),
                "price": row.get("Price", ""),
                "change": row.get("Change", ""),
                "volume": row.get("Volume", ""),
                "rel_volume": row.get("Rel Volume", ""),
                "sector": row.get("Sector", ""),
            })
        return results
    except Exception as exc:
        logger.warning("Unusual volume screen failed: %s", exc)
        return []


def _screen_52w_highs() -> List[Dict[str, Any]]:
    """Stocks hitting new 52-week highs."""
    try:
        from finviz.screener import Screener
        filters = ["ta_highlow52w_nh", "sh_avgvol_o200"]
        screen = Screener(filters=filters, order="-change", rows=25)
        results = []
        for row in screen.data[:25]:
            results.append({
                "ticker": row.get("Ticker", ""),
                "company": row.get("Company", ""),
                "price": row.get("Price", ""),
                "change": row.get("Change", ""),
                "52w_high": row.get("52W High", ""),
                "sector": row.get("Sector", ""),
            })
        return results
    except Exception as exc:
        logger.warning("52-week highs screen failed: %s", exc)
        return []


def _screen_52w_lows() -> List[Dict[str, Any]]:
    """Stocks hitting new 52-week lows (potential reversal candidates)."""
    try:
        from finviz.screener import Screener
        filters = ["ta_highlow52w_nl", "sh_avgvol_o200"]
        screen = Screener(filters=filters, order="change", rows=25)
        results = []
        for row in screen.data[:25]:
            results.append({
                "ticker": row.get("Ticker", ""),
                "company": row.get("Company", ""),
                "price": row.get("Price", ""),
                "change": row.get("Change", ""),
                "52w_low": row.get("52W Low", ""),
                "sector": row.get("Sector", ""),
            })
        return results
    except Exception as exc:
        logger.warning("52-week lows screen failed: %s", exc)
        return []


def _screen_insider_buying() -> List[Dict[str, Any]]:
    """Stocks with significant insider buying in the last month."""
    try:
        from finviz.screener import Screener
        filters = ["sh_insidertrans_verypos", "sh_avgvol_o100"]
        screen = Screener(filters=filters, order="-insidertransactions", rows=25)
        results = []
        for row in screen.data[:25]:
            results.append({
                "ticker": row.get("Ticker", ""),
                "company": row.get("Company", ""),
                "price": row.get("Price", ""),
                "insider_trans": row.get("Insider Trans", ""),
                "insider_own": row.get("Insider Own", ""),
                "sector": row.get("Sector", ""),
            })
        return results
    except Exception as exc:
        logger.warning("Insider buying screen failed: %s", exc)
        return []


def _screen_institutional_accumulation() -> List[Dict[str, Any]]:
    """Stocks with heavy institutional buying."""
    try:
        from finviz.screener import Screener
        filters = ["sh_insttrans_verypos", "sh_avgvol_o200"]
        screen = Screener(filters=filters, order="-insttransactions", rows=25)
        results = []
        for row in screen.data[:25]:
            results.append({
                "ticker": row.get("Ticker", ""),
                "company": row.get("Company", ""),
                "price": row.get("Price", ""),
                "inst_trans": row.get("Inst Trans", ""),
                "inst_own": row.get("Inst Own", ""),
                "sector": row.get("Sector", ""),
            })
        return results
    except Exception as exc:
        logger.warning("Institutional accumulation screen failed: %s", exc)
        return []


def _screen_high_short_interest() -> List[Dict[str, Any]]:
    """High short interest stocks with improving fundamentals (potential squeeze)."""
    try:
        from finviz.screener import Screener
        # High short float > 20%, positive earnings growth, positive sales growth
        filters = ["sh_short_o20", "fa_epsqoq_pos", "fa_salesqoq_pos", "sh_avgvol_o200"]
        screen = Screener(filters=filters, order="-shortfloat", rows=25)
        results = []
        for row in screen.data[:25]:
            results.append({
                "ticker": row.get("Ticker", ""),
                "company": row.get("Company", ""),
                "price": row.get("Price", ""),
                "short_float": row.get("Short Float", ""),
                "eps_growth_qoq": row.get("EPS Q/Q", ""),
                "sales_growth_qoq": row.get("Sales Q/Q", ""),
                "sector": row.get("Sector", ""),
            })
        return results
    except Exception as exc:
        logger.warning("High short interest screen failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Bridge interface
# ---------------------------------------------------------------------------

class EquityScreenerBridge:
    """Finviz equity screener bridge for Global Sentinel."""

    source_tier = "tier_2_operational"
    trust_weight = 0.6
    freshness_ttl_minutes = 360

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT
        self._last_fetch = None
        self._consecutive_failures = 0

    def fetch(self) -> Dict[str, Any]:
        """Run all screens and return combined results."""
        logger.info("Running equity screener bridge")
        now = datetime.now(timezone.utc)

        categories = {}
        errors = []

        screens = [
            ("unusual_volume", _screen_unusual_volume),
            ("52w_highs", _screen_52w_highs),
            ("52w_lows", _screen_52w_lows),
            ("insider_buying", _screen_insider_buying),
            ("institutional_accumulation", _screen_institutional_accumulation),
            ("high_short_interest_improving", _screen_high_short_interest),
        ]

        for name, func in screens:
            try:
                picks = func()
                categories[name] = {"count": len(picks), "picks": picks}
                time.sleep(1)  # Rate limit finviz
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                categories[name] = {"count": 0, "picks": [], "error": str(exc)}

        result = {
            "source": "equity_screener_bridge",
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "timestamp_utc": now.isoformat(),
            "fresh": True,
            "data": {
                "categories": categories,
                "total_picks": sum(c["count"] for c in categories.values()),
                "errors": errors,
            },
        }

        # Persist
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        logger.info("Screener picks saved to %s", OUTPUT_PATH)

        self._last_fetch = now
        self._consecutive_failures = 0
        return result

    def health(self) -> Dict[str, Any]:
        return {
            "source": "equity_screener_bridge",
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "consecutive_failures": self._consecutive_failures,
            "status": "ok",
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    bridge = EquityScreenerBridge()
    result = bridge.fetch()
    print(json.dumps(result, indent=2, default=str))

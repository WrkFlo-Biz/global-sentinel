#!/usr/bin/env python3
"""
Global Sentinel — Sector Relative Performance

Tracks 11 S&P sector ETFs relative to SPY, computes 1d/5d/20d
relative performance, identifies sector rotation and flow signals.

Output: data/quantum_feed/sector_rotation.json
Runs every 30 min during market hours
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.sector_relative")

try:
    import yfinance as yf
except ImportError:
    yf = None

SECTOR_ETFS = {
    "XLE": "Energy",
    "XLF": "Financials",
    "XLK": "Technology",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

BENCHMARK = "SPY"


def _pct_change(series: pd.Series, days: int) -> Optional[float]:
    if len(series) < days + 1:
        return None
    curr = float(series.iloc[-1])
    prev = float(series.iloc[-(days + 1)])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def compute_sector_rotation(repo_root: str = "/opt/global-sentinel") -> Dict[str, Any]:
    """Compute sector relative performance vs SPY."""
    if yf is None:
        return {"error": "yfinance not installed"}

    repo = Path(repo_root)
    output_path = repo / "data" / "quantum_feed" / "sector_rotation.json"

    all_symbols = list(SECTOR_ETFS.keys()) + [BENCHMARK]

    try:
        data = yf.download(all_symbols, period="2mo", interval="1d", group_by="ticker", progress=False)
    except Exception as exc:
        logger.error("Failed to download sector data: %s", exc)
        return {"error": str(exc)}

    # Get SPY returns
    try:
        spy_close = data[BENCHMARK]["Close"].dropna()
    except Exception:
        spy_close = pd.Series(dtype=float)

    spy_ret_1d = _pct_change(spy_close, 1)
    spy_ret_5d = _pct_change(spy_close, 5)
    spy_ret_20d = _pct_change(spy_close, 20)

    sectors = []
    for etf, sector_name in SECTOR_ETFS.items():
        try:
            close = data[etf]["Close"].dropna()
            if close.empty:
                continue

            ret_1d = _pct_change(close, 1)
            ret_5d = _pct_change(close, 5)
            ret_20d = _pct_change(close, 20)

            # Relative performance vs SPY
            rel_1d = round(ret_1d - spy_ret_1d, 2) if ret_1d is not None and spy_ret_1d is not None else None
            rel_5d = round(ret_5d - spy_ret_5d, 2) if ret_5d is not None and spy_ret_5d is not None else None
            rel_20d = round(ret_20d - spy_ret_20d, 2) if ret_20d is not None and spy_ret_20d is not None else None

            # Momentum shift: compare 5d vs 20d relative
            if rel_5d is not None and rel_20d is not None:
                momentum_shift = round(rel_5d - (rel_20d / 4), 2)  # normalize 20d to ~5d equiv
            else:
                momentum_shift = None

            # Classification
            if rel_5d is not None:
                if rel_5d > 1.0:
                    status = "leading"
                elif rel_5d < -1.0:
                    status = "lagging"
                else:
                    status = "inline"
            else:
                status = "unknown"

            sectors.append({
                "etf": etf,
                "sector": sector_name,
                "price": round(float(close.iloc[-1]), 2),
                "return_1d": ret_1d,
                "return_5d": ret_5d,
                "return_20d": ret_20d,
                "relative_1d_vs_spy": rel_1d,
                "relative_5d_vs_spy": rel_5d,
                "relative_20d_vs_spy": rel_20d,
                "momentum_shift": momentum_shift,
                "status": status,
            })
        except Exception as exc:
            logger.warning("Failed for %s: %s", etf, exc)

    # Sort by 5d relative performance
    sectors.sort(key=lambda x: x.get("relative_5d_vs_spy") or 0, reverse=True)

    leading = [s for s in sectors if s["status"] == "leading"]
    lagging = [s for s in sectors if s["status"] == "lagging"]

    # Detect rotation signals
    rotation_signals = []
    if leading and lagging:
        for leader in leading[:3]:
            for lagger in lagging[:3]:
                rotation_signals.append({
                    "signal": f"Money flowing from {lagger['sector']} to {leader['sector']}",
                    "from_sector": lagger["sector"],
                    "from_etf": lagger["etf"],
                    "to_sector": leader["sector"],
                    "to_etf": leader["etf"],
                    "strength": round(abs((leader.get("relative_5d_vs_spy") or 0) - (lagger.get("relative_5d_vs_spy") or 0)), 2),
                })

    rotation_signals.sort(key=lambda x: x.get("strength", 0), reverse=True)

    output = {
        "source": "sector_relative_performance",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": {
            "symbol": BENCHMARK,
            "return_1d": spy_ret_1d,
            "return_5d": spy_ret_5d,
            "return_20d": spy_ret_20d,
        },
        "sectors": sectors,
        "leading_sectors": leading,
        "lagging_sectors": lagging,
        "rotation_signals": rotation_signals[:5],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Sector rotation: %d leading, %d lagging, %d rotation signals",
                 len(leading), len(lagging), len(rotation_signals))
    return output


def main():
    logging.basicConfig(level=logging.INFO)
    result = compute_sector_rotation()
    if "error" not in result:
        print(json.dumps({
            "leading": [s["sector"] for s in result.get("leading_sectors", [])],
            "lagging": [s["sector"] for s in result.get("lagging_sectors", [])],
            "top_rotation": result.get("rotation_signals", [])[:2],
        }, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

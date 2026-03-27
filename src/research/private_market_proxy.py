#!/usr/bin/env python3
"""
Global Sentinel — Private Markets Proxy Tracker

Tracks publicly traded private equity / VC proxies and pre-IPO vehicles.
Monitors IPO pipeline via Finnhub IPO calendar.

Output: data/quantum_feed/private_market_signals.json
Runs daily at 18:30 UTC
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger("global_sentinel.private_market_proxy")

try:
    import yfinance as yf
except ImportError:
    yf = None

REPO_ROOT = Path(__file__).resolve().parents[2]
QUANTUM_FEED_DIR = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QUANTUM_FEED_DIR / "private_market_signals.json"

# Private equity / VC publicly traded proxies
PE_VC_PROXIES = {
    "BX":   "Blackstone Inc",
    "KKR":  "KKR & Co Inc",
    "APO":  "Apollo Global Management",
    "ARES": "Ares Management Corp",
}

# Pre-IPO / special purpose proxies
PRE_IPO_PROXIES = {
    "DXYZ": "Destiny Tech100 (pre-IPO basket)",
}

BENCHMARK = "SPY"
FINNHUB_BASE = "https://finnhub.io/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct_change(series: pd.Series, days: int) -> Optional[float]:
    if len(series) < days + 1:
        return None
    curr = float(series.iloc[-1])
    prev = float(series.iloc[-(days + 1)])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _relative_strength(fund_close: pd.Series, bench_close: pd.Series, days: int) -> Optional[float]:
    """Compute relative strength = fund return - benchmark return over N days."""
    fund_ret = _pct_change(fund_close, days)
    bench_ret = _pct_change(bench_close, days)
    if fund_ret is None or bench_ret is None:
        return None
    return round(fund_ret - bench_ret, 2)


def _compute_proxy_metrics(close: pd.Series, bench_close: pd.Series) -> Dict[str, Any]:
    """Compute key metrics for a PE/VC proxy."""
    aligned = pd.DataFrame({"proxy": close, "bench": bench_close}).dropna()
    if len(aligned) < 20:
        return {"error": "insufficient data"}

    ret_1d = _pct_change(aligned["proxy"], 1)
    ret_5d = _pct_change(aligned["proxy"], 5)
    ret_1m = _pct_change(aligned["proxy"], 21)
    ret_3m = _pct_change(aligned["proxy"], 63)

    rs_5d = _relative_strength(aligned["proxy"], aligned["bench"], 5)
    rs_1m = _relative_strength(aligned["proxy"], aligned["bench"], 21)

    # Volatility (20d annualised)
    proxy_ret = aligned["proxy"].pct_change().dropna()
    vol_20d = round(float(proxy_ret.tail(20).std() * np.sqrt(252) * 100), 2) if len(proxy_ret) >= 20 else None

    # NAV premium/discount estimate for DXYZ-type vehicles
    # (would need additional data source; placeholder)
    last_price = float(aligned["proxy"].iloc[-1])

    return {
        "last_price": round(last_price, 2),
        "return_1d_pct": ret_1d,
        "return_5d_pct": ret_5d,
        "return_1m_pct": ret_1m,
        "return_3m_pct": ret_3m,
        "relative_strength_5d": rs_5d,
        "relative_strength_1m": rs_1m,
        "volatility_20d_ann": vol_20d,
    }


def _fetch_ipo_calendar() -> List[Dict[str, Any]]:
    """Fetch upcoming IPO calendar from Finnhub."""
    api_key = os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_KEY", "")
    if not api_key:
        # Try loading from .env
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("FINNHUB_API_KEY=") or line.startswith("FINNHUB_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key:
        logger.warning("No Finnhub API key found; skipping IPO calendar")
        return []

    today = datetime.now(timezone.utc).date()
    from_date = today.isoformat()
    to_date = (today + timedelta(days=90)).isoformat()

    try:
        resp = requests.get(
            f"{FINNHUB_BASE}/calendar/ipo",
            params={"from": from_date, "to": to_date, "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ipos = data.get("ipoCalendar", [])
        # Return top 20 sorted by date
        ipos.sort(key=lambda x: x.get("date", ""))
        return ipos[:20]
    except Exception as exc:
        logger.error("Failed to fetch IPO calendar: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_private_markets(repo_root: str = "/opt/global-sentinel") -> Dict[str, Any]:
    """Run the private market proxy scanner."""
    if yf is None:
        return {"error": "yfinance not installed"}

    repo = Path(repo_root)
    output_path = repo / "data" / "quantum_feed" / "private_market_signals.json"

    all_proxies = {**PE_VC_PROXIES, **PRE_IPO_PROXIES}
    all_symbols = list(all_proxies.keys()) + [BENCHMARK]

    try:
        data = yf.download(all_symbols, period="6mo", interval="1d",
                           group_by="ticker", progress=False)
    except Exception as exc:
        logger.error("Failed to download proxy data: %s", exc)
        return {"error": str(exc)}

    try:
        bench_close = data[BENCHMARK]["Close"].dropna()
    except Exception:
        bench_close = pd.Series(dtype=float)

    pe_vc_results: List[Dict[str, Any]] = []
    pre_ipo_results: List[Dict[str, Any]] = []

    for symbol, name in all_proxies.items():
        try:
            close = data[symbol]["Close"].dropna()
        except Exception:
            entry = {"symbol": symbol, "name": name, "error": "no data"}
            if symbol in PE_VC_PROXIES:
                pe_vc_results.append(entry)
            else:
                pre_ipo_results.append(entry)
            continue

        metrics = _compute_proxy_metrics(close, bench_close)
        entry = {"symbol": symbol, "name": name, **metrics}

        # Classify momentum
        rs = metrics.get("relative_strength_1m")
        if rs is not None:
            if rs > 3:
                entry["momentum"] = "strong_outperform"
            elif rs > 0:
                entry["momentum"] = "mild_outperform"
            elif rs > -3:
                entry["momentum"] = "mild_underperform"
            else:
                entry["momentum"] = "strong_underperform"

        if symbol in PE_VC_PROXIES:
            pe_vc_results.append(entry)
        else:
            pre_ipo_results.append(entry)

    # Aggregate PE/VC sector signal
    avg_rs = [e.get("relative_strength_1m", 0) for e in pe_vc_results if isinstance(e.get("relative_strength_1m"), (int, float))]
    pe_sector_signal = "neutral"
    if avg_rs:
        mean_rs = np.mean(avg_rs)
        if mean_rs > 2:
            pe_sector_signal = "risk_on"
        elif mean_rs < -2:
            pe_sector_signal = "risk_off"

    # IPO pipeline
    ipo_calendar = _fetch_ipo_calendar()

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pe_vc_proxies": pe_vc_results,
        "pre_ipo_proxies": pre_ipo_results,
        "pe_sector_signal": pe_sector_signal,
        "ipo_pipeline": {
            "upcoming_count": len(ipo_calendar),
            "notable_ipos": ipo_calendar[:10],
        },
        "summary": {
            "pe_vc_tracked": len(PE_VC_PROXIES),
            "pre_ipo_tracked": len(PRE_IPO_PROXIES),
            "sector_signal": pe_sector_signal,
        },
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str))
        logger.info("Wrote private market signals to %s", output_path)
    except Exception as exc:
        logger.error("Failed to write output: %s", exc)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = scan_private_markets()
    print(json.dumps(result, indent=2, default=str))

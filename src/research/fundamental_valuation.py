#!/usr/bin/env python3
"""
Global Sentinel — Fundamental Valuation Engine

Computes valuation metrics (P/E, P/S, P/B, EV/EBITDA, FCF yield,
Debt/Equity, revenue growth, earnings growth) and a simple DCF
intrinsic value estimate for top 20 watchlist symbols.

Output: data/quantum_feed/fundamental_scores.json
Runs daily at 6:00 AM ET (10:00 UTC)
"""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("global_sentinel.fundamental_valuation")

try:
    import yaml
except ImportError:
    yaml = None

try:
    import yfinance as yf
except ImportError:
    yf = None

DEFAULT_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMZN", "META", "MSFT",
    "AMD", "GOOGL", "NFLX", "JPM", "V", "UNH", "XOM", "LLY",
    "AVGO", "MA", "COST", "HD",
]


def _load_watchlist(repo_root: Path) -> List[str]:
    wl_path = repo_root / "config" / "expanded_watchlist.yaml"
    if yaml is None or not wl_path.exists():
        return DEFAULT_SYMBOLS[:20]
    try:
        data = yaml.safe_load(wl_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return DEFAULT_SYMBOLS[:20]
    symbols = []
    skip = {"USD", "XAU", "UST", "VIX", "BRN", "BZ=F", "^", "GC=F"}
    for cat_data in (data.get("categories") or {}).values():
        for sym in (cat_data.get("symbols") or []):
            s = str(sym).strip()
            if s and not any(p in s for p in skip):
                symbols.append(s)
    seen = set()
    out = []
    for s in (symbols if symbols else DEFAULT_SYMBOLS):
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= 20:
            break
    return out or DEFAULT_SYMBOLS[:20]


def _safe_get(info: dict, key: str, default=None):
    val = info.get(key, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val


def _simple_dcf(fcf: float, growth_rate: float, discount_rate: float = 0.10, years: int = 5) -> float:
    """Simple DCF: project FCF forward, discount back."""
    if fcf <= 0 or growth_rate is None:
        return 0.0
    total = 0.0
    for yr in range(1, years + 1):
        projected_fcf = fcf * ((1 + growth_rate) ** yr)
        total += projected_fcf / ((1 + discount_rate) ** yr)
    # Terminal value (perpetuity growth at 2.5%)
    terminal_fcf = fcf * ((1 + growth_rate) ** years) * (1 + 0.025)
    terminal_value = terminal_fcf / (discount_rate - 0.025)
    total += terminal_value / ((1 + discount_rate) ** years)
    return total


def _compute_value_score(pe_trail, pe_fwd, ps, pb, ev_ebitda, fcf_yield, de_ratio, rev_growth, earn_growth, dcf_ratio):
    """Compute value score 0 (extremely overvalued) to 10 (deep value)."""
    score = 5.0  # neutral start

    # P/E trailing
    if pe_trail is not None:
        if pe_trail < 10:
            score += 1.5
        elif pe_trail < 15:
            score += 1.0
        elif pe_trail < 25:
            score += 0
        elif pe_trail < 40:
            score -= 1.0
        else:
            score -= 1.5

    # Forward P/E discount
    if pe_trail is not None and pe_fwd is not None and pe_trail > 0:
        if pe_fwd < pe_trail * 0.8:
            score += 0.5  # earnings expected to grow
        elif pe_fwd > pe_trail * 1.2:
            score -= 0.5

    # P/S
    if ps is not None:
        if ps < 2:
            score += 0.5
        elif ps > 10:
            score -= 0.5

    # P/B
    if pb is not None:
        if pb < 1.5:
            score += 0.5
        elif pb > 5:
            score -= 0.5

    # EV/EBITDA
    if ev_ebitda is not None:
        if ev_ebitda < 8:
            score += 0.5
        elif ev_ebitda > 20:
            score -= 0.5

    # FCF Yield
    if fcf_yield is not None:
        if fcf_yield > 0.08:
            score += 1.0
        elif fcf_yield > 0.05:
            score += 0.5
        elif fcf_yield < 0.01:
            score -= 0.5

    # Debt/Equity
    if de_ratio is not None:
        if de_ratio > 2.0:
            score -= 0.5
        elif de_ratio < 0.5:
            score += 0.3

    # Revenue growth
    if rev_growth is not None:
        if rev_growth > 0.20:
            score += 0.5
        elif rev_growth < -0.05:
            score -= 0.5

    # DCF ratio (intrinsic / market cap)
    if dcf_ratio is not None:
        if dcf_ratio > 1.5:
            score += 1.0
        elif dcf_ratio > 1.0:
            score += 0.5
        elif dcf_ratio < 0.5:
            score -= 1.0
        elif dcf_ratio < 0.75:
            score -= 0.5

    return round(max(0, min(10, score)), 1)


def analyze_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    """Compute fundamental valuation metrics for one symbol."""
    if yf is None:
        return None
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        if not info.get("currentPrice") and not info.get("regularMarketPrice"):
            return None

        price = _safe_get(info, "currentPrice") or _safe_get(info, "regularMarketPrice", 0)
        market_cap = _safe_get(info, "marketCap", 0)

        pe_trailing = _safe_get(info, "trailingPE")
        pe_forward = _safe_get(info, "forwardPE")
        ps_ratio = _safe_get(info, "priceToSalesTrailing12Months")
        pb_ratio = _safe_get(info, "priceToBook")
        ev_ebitda = _safe_get(info, "enterpriseToEbitda")
        fcf = _safe_get(info, "freeCashflow", 0)
        de_ratio = _safe_get(info, "debtToEquity")
        if de_ratio is not None:
            de_ratio = de_ratio / 100  # yfinance returns as percentage

        rev_growth = _safe_get(info, "revenueGrowth")
        earn_growth = _safe_get(info, "earningsGrowth")

        # FCF yield
        fcf_yield = (fcf / market_cap) if market_cap > 0 and fcf else None

        # Simple DCF
        growth_for_dcf = rev_growth if rev_growth is not None else 0.05
        growth_for_dcf = max(-0.10, min(0.30, growth_for_dcf))  # cap at 30%
        dcf_value = _simple_dcf(max(fcf, 0), growth_for_dcf) if fcf and fcf > 0 else 0
        dcf_ratio = (dcf_value / market_cap) if market_cap > 0 and dcf_value > 0 else None

        intrinsic_vs_price = "fair"
        if dcf_ratio is not None:
            if dcf_ratio > 1.2:
                intrinsic_vs_price = "undervalued"
            elif dcf_ratio < 0.8:
                intrinsic_vs_price = "overvalued"

        value_score = _compute_value_score(
            pe_trailing, pe_forward, ps_ratio, pb_ratio, ev_ebitda,
            fcf_yield, de_ratio, rev_growth, earn_growth, dcf_ratio
        )

        return {
            "symbol": symbol,
            "price": round(price, 2) if price else None,
            "market_cap": market_cap,
            "pe_trailing": round(pe_trailing, 2) if pe_trailing else None,
            "pe_forward": round(pe_forward, 2) if pe_forward else None,
            "ps_ratio": round(ps_ratio, 2) if ps_ratio else None,
            "pb_ratio": round(pb_ratio, 2) if pb_ratio else None,
            "ev_ebitda": round(ev_ebitda, 2) if ev_ebitda else None,
            "fcf": fcf,
            "fcf_yield": round(fcf_yield, 4) if fcf_yield else None,
            "debt_to_equity": round(de_ratio, 2) if de_ratio else None,
            "revenue_growth_yoy": round(rev_growth, 4) if rev_growth else None,
            "earnings_growth_yoy": round(earn_growth, 4) if earn_growth else None,
            "dcf_intrinsic_value": round(dcf_value, 0) if dcf_value else None,
            "dcf_vs_market_cap": round(dcf_ratio, 2) if dcf_ratio else None,
            "valuation_verdict": intrinsic_vs_price,
            "value_score": value_score,
        }
    except Exception as exc:
        logger.warning("Fundamental analysis failed for %s: %s", symbol, exc)
        return None


def run(repo_root: str = "/opt/global-sentinel"):
    """Run fundamental valuation for all watchlist symbols."""
    repo = Path(repo_root)
    symbols = _load_watchlist(repo)
    output_path = repo / "data" / "quantum_feed" / "fundamental_scores.json"

    results = []
    errors = []
    for sym in symbols:
        try:
            r = analyze_fundamentals(sym)
            if r:
                results.append(r)
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})

    deep_value = [r for r in results if r.get("value_score", 5) >= 7]
    overvalued = [r for r in results if r.get("value_score", 5) <= 3]

    output = {
        "source": "fundamental_valuation",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "symbols_analyzed": len(results),
        "deep_value_picks": sorted(deep_value, key=lambda x: x["value_score"], reverse=True),
        "overvalued_warnings": sorted(overvalued, key=lambda x: x["value_score"]),
        "all_scores": results,
        "errors": errors,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Fundamental valuation: %d symbols, %d deep value, %d overvalued",
                 len(results), len(deep_value), len(overvalued))
    return output


def main():
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(json.dumps({"symbols_analyzed": result["symbols_analyzed"],
                       "deep_value": len(result["deep_value_picks"]),
                       "overvalued": len(result["overvalued_warnings"])}, indent=2))


if __name__ == "__main__":
    main()

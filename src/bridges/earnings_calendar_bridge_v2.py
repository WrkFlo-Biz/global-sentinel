#!/usr/bin/env python3
"""
Global Sentinel — Earnings Calendar Integration

Fetches upcoming earnings for tracked symbols from multiple sources:
1. yfinance (primary)
2. FMP API (fallback)
Outputs to data/quantum_feed/earnings_calendar.json with 14-day lookahead.
Flags symbols with earnings within 2 days for position size reduction.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("global_sentinel.earnings_calendar")

# 40 tracked symbols
TRACKED_SYMBOLS = [
    "SPY", "QQQ", "NVDA", "TSLA", "AMD", "META", "AAPL", "AMZN",
    "GOOGL", "MSFT", "PLTR", "COIN", "XLE", "XLF", "SOXL", "TQQQ",
    "IWM", "TLT", "GLD", "UVXY",
    "BA", "JPM", "GS", "V", "MA", "UNH", "JNJ", "PFE", "LLY",
    "XOM", "CVX", "CRM", "NFLX", "DIS", "COST", "HD", "WMT",
    "INTC", "AVGO", "MU",
]


class EarningsCalendarIntegration:
    def __init__(self):
        self.repo_root = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
        self.fmp_key = os.environ.get("FMP_API_KEY", "")
        self.output_path = self.repo_root / "data" / "quantum_feed" / "earnings_calendar.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.today = date.today()
        self.lookahead = self.today + timedelta(days=14)
        self.warning_threshold = self.today + timedelta(days=2)

    def _fetch_yfinance(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch earnings dates from yfinance for each tracked symbol."""
        results: Dict[str, List[Dict[str, Any]]] = {}
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not available, skipping")
            return results

        for symbol in TRACKED_SYMBOLS:
            try:
                ticker = yf.Ticker(symbol)
                # earnings_dates returns a DataFrame with dates as index
                cal = ticker.earnings_dates
                if cal is None or cal.empty:
                    continue

                entries = []
                for idx, row in cal.iterrows():
                    try:
                        # idx is Timestamp
                        earn_date = idx.date() if hasattr(idx, 'date') else idx
                        if isinstance(earn_date, datetime):
                            earn_date = earn_date.date()
                        if not (self.today <= earn_date <= self.lookahead):
                            continue

                        eps_est = row.get("EPS Estimate")
                        eps_act = row.get("Reported EPS")
                        surprise = row.get("Surprise(%)")

                        # Determine BMO/AMC from hour if available
                        hour = idx.hour if hasattr(idx, 'hour') else None
                        time_label = "unknown"
                        if hour is not None:
                            time_label = "BMO" if hour < 12 else "AMC"

                        entry = {
                            "symbol": symbol,
                            "date": str(earn_date),
                            "time": time_label,
                            "eps_estimate": float(eps_est) if eps_est is not None and str(eps_est) != "nan" else None,
                            "eps_actual": float(eps_act) if eps_act is not None and str(eps_act) != "nan" else None,
                            "surprise_pct": float(surprise) if surprise is not None and str(surprise) != "nan" else None,
                            "source": "yfinance",
                        }
                        entries.append(entry)
                    except Exception:
                        continue

                if entries:
                    results[symbol] = entries
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"yfinance error for {symbol}: {e}")
                continue

        return results

    def _fetch_fmp(self) -> List[Dict[str, Any]]:
        """Fetch earnings calendar from FMP API."""
        if not self.fmp_key:
            logger.warning("No FMP_API_KEY, skipping FMP fallback")
            return []

        from_str = self.today.isoformat()
        to_str = self.lookahead.isoformat()
        url = (
            f"https://financialmodelingprep.com/stable/earning-calendar"
            f"?from={from_str}&to={to_str}&apikey={self.fmp_key}"
        )

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())

            if not isinstance(data, list):
                logger.warning(f"FMP returned unexpected format: {type(data)}")
                return []

            tracked_set = set(TRACKED_SYMBOLS)
            results = []
            for item in data:
                sym = item.get("symbol", "")
                if sym not in tracked_set:
                    continue
                earn_date = item.get("date", "")
                results.append({
                    "symbol": sym,
                    "date": earn_date,
                    "time": item.get("time", "unknown"),  # BMO/AMC
                    "eps_estimate": item.get("epsEstimated"),
                    "eps_actual": item.get("eps"),
                    "revenue_estimate": item.get("revenueEstimated"),
                    "revenue_actual": item.get("revenue"),
                    "source": "fmp",
                })
            return results
        except Exception as e:
            logger.error(f"FMP API error: {e}")
            return []

    def run(self) -> Dict[str, Any]:
        """Run the earnings calendar integration."""
        all_earnings: List[Dict[str, Any]] = []
        earnings_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        sources_used = []

        # Source 1: yfinance
        logger.info("Fetching earnings from yfinance...")
        yf_data = self._fetch_yfinance()
        if yf_data:
            sources_used.append("yfinance")
            for symbol, entries in yf_data.items():
                earnings_by_symbol.setdefault(symbol, []).extend(entries)
                all_earnings.extend(entries)
            logger.info(f"  yfinance: found earnings for {len(yf_data)} symbols")

        # Source 2: FMP fallback (also use to fill gaps)
        logger.info("Fetching earnings from FMP...")
        fmp_data = self._fetch_fmp()
        if fmp_data:
            sources_used.append("fmp")
            for entry in fmp_data:
                sym = entry["symbol"]
                # Only add if not already from yfinance for same date
                existing_dates = {e["date"] for e in earnings_by_symbol.get(sym, [])}
                if entry["date"] not in existing_dates:
                    earnings_by_symbol.setdefault(sym, []).extend([entry])
                    all_earnings.append(entry)
            logger.info(f"  FMP: found {len(fmp_data)} entries for tracked symbols")

        # Sort by date
        all_earnings.sort(key=lambda x: x.get("date", ""))

        # Flag earnings warnings (within 2 days)
        warning_symbols = []
        for entry in all_earnings:
            try:
                earn_date = date.fromisoformat(entry["date"])
                if earn_date <= self.warning_threshold:
                    entry["earnings_warning"] = True
                    if entry["symbol"] not in warning_symbols:
                        warning_symbols.append(entry["symbol"])
                else:
                    entry["earnings_warning"] = False
            except (ValueError, TypeError):
                entry["earnings_warning"] = False

        # Build per-symbol summary
        symbol_summary = {}
        for sym, entries in earnings_by_symbol.items():
            entries.sort(key=lambda x: x.get("date", ""))
            next_date = entries[0]["date"] if entries else None
            try:
                days_away = (date.fromisoformat(next_date) - self.today).days if next_date else None
            except (ValueError, TypeError):
                days_away = None
            symbol_summary[sym] = {
                "next_earnings_date": next_date,
                "days_until_earnings": days_away,
                "time": entries[0].get("time", "unknown") if entries else "unknown",
                "eps_estimate": entries[0].get("eps_estimate") if entries else None,
                "earnings_warning": sym in warning_symbols,
                "reduce_position": sym in warning_symbols,
            }

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanner": "earnings_calendar",
            "sources_used": sources_used,
            "lookahead_days": 14,
            "today": self.today.isoformat(),
            "warning_threshold": self.warning_threshold.isoformat(),
            "total_earnings_events": len(all_earnings),
            "symbols_with_earnings": len(earnings_by_symbol),
            "earnings_warning_symbols": warning_symbols,
            "position_size_note": "Reduce position by 50% for symbols with earnings within 2 days",
            "symbol_summary": symbol_summary,
            "earnings_calendar": all_earnings,
        }

        self.output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        logger.info(f"Wrote earnings calendar to {self.output_path}")
        logger.info(f"  {len(all_earnings)} events, {len(warning_symbols)} warnings: {warning_symbols}")
        return result


def main():
    ecal = EarningsCalendarIntegration()
    result = ecal.run()
    print(json.dumps({
        "total_events": result.get("total_earnings_events", 0),
        "symbols_with_earnings": result.get("symbols_with_earnings", 0),
        "warning_symbols": result.get("earnings_warning_symbols", []),
        "sources": result.get("sources_used", []),
    }, indent=2))


if __name__ == "__main__":
    main()

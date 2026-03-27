#!/usr/bin/env python3
"""
Global Sentinel — FINRA Short Interest Bridge (via Finviz)

Fetches short interest data for top watchlist symbols by scraping
Finviz stock screener (free, no API key needed).
Detects squeeze candidates (>20% short interest) and short interest momentum.

Output: data/quantum_feed/short_interest.json
Tier 2, trust 0.8, TTL 360 min
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.finra_short_interest_bridge")


def _fetch_text(url: str, timeout: int = 15) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return None


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text).strip()


def _parse_value(text: str) -> Optional[float]:
    """Parse a Finviz formatted number (e.g., '12.34%', '1.23M', '-')."""
    text = _strip_html(text).strip()
    if text in ("-", "", "N/A"):
        return None
    text = text.replace(",", "")
    if text.endswith("%"):
        try:
            return float(text[:-1])
        except ValueError:
            return None
    multiplier = 1.0
    if text.endswith("B"):
        multiplier = 1e9
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1e6
        text = text[:-1]
    elif text.endswith("K"):
        multiplier = 1e3
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


class FINRAShortInterestBridge:
    """Fetch short interest data via Finviz for watchlist symbols."""

    DISPLAY_NAME = "finra_short_interest"
    CATEGORY = "market_microstructure"

    DEFAULT_SYMBOLS = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM",
        "V", "JNJ", "XOM", "CVX", "LMT", "RTX", "BA", "CAT",
        "GS", "MS", "UNH", "PFE", "AMD", "INTC", "NFLX", "DIS",
        "COIN", "PLTR", "RIVN", "SOFI", "NIO", "GME",
    ]

    SQUEEZE_THRESHOLD = 20.0

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "short_interest.json"
        self._previous_data: Dict[str, float] = {}
        self._load_previous()

    def _load_previous(self):
        if self.output_path.exists():
            try:
                prev = json.loads(self.output_path.read_text())
                for entry in prev.get("data", {}).get("symbols", []):
                    sym = entry.get("symbol", "")
                    si_pct = entry.get("short_pct_of_float", 0)
                    if sym and si_pct:
                        self._previous_data[sym] = float(si_pct)
            except Exception:
                pass

    def _extract_finviz_field(self, html: str, label: str) -> Optional[str]:
        """Extract a field value from Finviz snapshot table.

        Finviz structure:
        <td ...>...<a ...>Short Float</a></td><td ...>...<b>15.77%</b>...</td>
        """
        # Pattern: label text in a td (possibly inside <a> tag), followed by value td
        pattern = (
            r'>' + re.escape(label) + r'</a></td>'
            r'\s*<td[^>]*>(.*?)</td>'
        )
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _strip_html(match.group(1))

        # Fallback: label directly in td text
        pattern2 = (
            r'>' + re.escape(label) + r'</td>'
            r'\s*<td[^>]*>(.*?)</td>'
        )
        match = re.search(pattern2, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _strip_html(match.group(1))

        return None

    def _scrape_finviz(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Scrape short interest data from Finviz stock page."""
        url = f"https://finviz.com/quote.ashx?t={symbol}"
        html = _fetch_text(url)
        if not html:
            return None

        short_float_str = self._extract_finviz_field(html, "Short Float")
        short_ratio_str = self._extract_finviz_field(html, "Short Ratio")
        short_interest_str = self._extract_finviz_field(html, "Short Interest")
        shs_float_str = self._extract_finviz_field(html, "Shs Float")
        shs_outstand_str = self._extract_finviz_field(html, "Shs Outstand")

        short_pct = _parse_value(short_float_str) if short_float_str else None
        short_ratio = _parse_value(short_ratio_str) if short_ratio_str else None
        short_shares = _parse_value(short_interest_str) if short_interest_str else None
        float_shares = _parse_value(shs_float_str) if shs_float_str else None
        shares_out = _parse_value(shs_outstand_str) if shs_outstand_str else None

        if short_pct is None and short_shares is None:
            return None

        # Momentum comparison
        prev_pct = self._previous_data.get(symbol, 0)
        pct_change = (short_pct - prev_pct) if short_pct and prev_pct else 0
        momentum = "increasing" if pct_change > 0.5 else "decreasing" if pct_change < -0.5 else "flat"

        return {
            "symbol": symbol,
            "short_pct_of_float": round(short_pct, 2) if short_pct else 0,
            "short_ratio_days_to_cover": round(short_ratio, 2) if short_ratio else 0,
            "short_interest_shares": int(short_shares) if short_shares else 0,
            "float_shares": int(float_shares) if float_shares else 0,
            "shares_outstanding": int(shares_out) if shares_out else 0,
            "pct_change_vs_prior": round(pct_change, 2),
            "momentum": momentum,
            "squeeze_candidate": (short_pct or 0) >= self.SQUEEZE_THRESHOLD,
        }

    def poll(self) -> Dict[str, Any]:
        """Poll Finviz for short interest data across watchlist."""
        symbols_data = []
        squeeze_candidates = []
        errors = []

        for symbol in self.DEFAULT_SYMBOLS:
            try:
                si_data = self._scrape_finviz(symbol)
                if not si_data:
                    continue
                symbols_data.append(si_data)
                if si_data.get("squeeze_candidate"):
                    squeeze_candidates.append({
                        "symbol": symbol,
                        "short_pct_float": si_data["short_pct_of_float"],
                        "short_ratio": si_data["short_ratio_days_to_cover"],
                        "momentum": si_data["momentum"],
                    })
                time.sleep(1.0)
            except Exception as e:
                errors.append({"symbol": symbol, "error": str(e)})
                logger.warning(f"[FINRAShortInterest] {symbol} error: {e}")

        symbols_data.sort(key=lambda x: x.get("short_pct_of_float", 0), reverse=True)

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "finra_short_interest",
            "symbols_scanned": len(self.DEFAULT_SYMBOLS),
            "symbols_with_data": len(symbols_data),
            "squeeze_candidates_count": len(squeeze_candidates),
            "data": {
                "symbols": symbols_data,
                "squeeze_candidates": squeeze_candidates,
                "summary": {
                    "avg_short_pct": round(
                        sum(s.get("short_pct_of_float", 0) for s in symbols_data) / max(len(symbols_data), 1), 2
                    ),
                    "max_short_pct": max((s.get("short_pct_of_float", 0) for s in symbols_data), default=0),
                    "increasing_count": sum(1 for s in symbols_data if s.get("momentum") == "increasing"),
                    "decreasing_count": sum(1 for s in symbols_data if s.get("momentum") == "decreasing"),
                    "high_short_ratio": [
                        {"symbol": s["symbol"], "days_to_cover": s["short_ratio_days_to_cover"]}
                        for s in symbols_data if s.get("short_ratio_days_to_cover", 0) > 5
                    ],
                },
            },
            "errors": errors if errors else None,
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2))
        logger.info(f"[FINRAShortInterest] Wrote {len(symbols_data)} symbols, {len(squeeze_candidates)} squeeze candidates")

        return result

#!/usr/bin/env python3
"""
Global Sentinel — Google Trends Bridge

Uses pytrends to get search interest for watchlist stock names.
Detects unusual search spikes (>2x baseline = retail attention incoming).

Output: data/quantum_feed/google_trends.json
Tier 3, trust 0.4, TTL 360 min
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.google_trends_bridge")

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "google_trends.json"

# Default watchlist — company names (Google Trends works better with names)
DEFAULT_WATCHLIST = {
    "AAPL": "Apple stock",
    "TSLA": "Tesla stock",
    "NVDA": "Nvidia stock",
    "MSFT": "Microsoft stock",
    "AMZN": "Amazon stock",
    "META": "Meta stock",
    "GOOGL": "Google stock",
    "AMD": "AMD stock",
    "NFLX": "Netflix stock",
    "COIN": "Coinbase stock",
    "GME": "GameStop stock",
    "AMC": "AMC stock",
    "PLTR": "Palantir stock",
    "SOFI": "SoFi stock",
    "SMCI": "Super Micro stock",
    "ARM": "ARM stock",
    "MSTR": "MicroStrategy stock",
    "SPY": "SPY ETF",
    "QQQ": "QQQ ETF",
}

SPIKE_THRESHOLD = 2.0  # 2x baseline = spike


def _load_dynamic_watchlist() -> Dict[str, str]:
    """Try to load watchlist from quantum_feed or config."""
    watchlist_path = REPO_ROOT / "data" / "quantum_feed" / "optimal_portfolio.json"
    try:
        if watchlist_path.exists():
            data = json.loads(watchlist_path.read_text(encoding="utf-8"))
            tickers = []
            if isinstance(data, dict):
                tickers = list(data.get("weights", {}).keys())[:20]
            if tickers:
                return {t: f"{t} stock" for t in tickers}
    except Exception:
        pass
    return DEFAULT_WATCHLIST


def _query_trends(keywords: List[str], timeframe: str = "now 7-d") -> Dict[str, Any]:
    """Query Google Trends for a batch of keywords (max 5 per request)."""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=300, retries=3, backoff_factor=1.0)
        pytrends.build_payload(keywords, timeframe=timeframe, geo="US")
        interest = pytrends.interest_over_time()

        if interest.empty:
            return {}

        results = {}
        for kw in keywords:
            if kw in interest.columns:
                series = interest[kw]
                current = float(series.iloc[-1]) if len(series) > 0 else 0
                baseline = float(series.iloc[:-1].mean()) if len(series) > 1 else current
                peak = float(series.max())

                spike_ratio = current / baseline if baseline > 0 else 0.0
                is_spike = spike_ratio >= SPIKE_THRESHOLD and current > 20  # min absolute threshold

                results[kw] = {
                    "current": current,
                    "baseline_avg": round(baseline, 1),
                    "peak": peak,
                    "spike_ratio": round(spike_ratio, 2),
                    "is_spike": is_spike,
                    "trend_data": [float(x) for x in series.values[-7:]],  # last 7 data points
                }
        return results
    except Exception as exc:
        logger.warning("Google Trends query failed for %s: %s", keywords, exc)
        return {}


# ---------------------------------------------------------------------------
# Bridge interface
# ---------------------------------------------------------------------------

class GoogleTrendsBridge:
    """Google Trends search interest bridge for Global Sentinel."""

    source_tier = "tier_3_alternative"
    trust_weight = 0.4
    freshness_ttl_minutes = 360

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT
        self._last_fetch = None
        self._consecutive_failures = 0

    def fetch(self) -> Dict[str, Any]:
        """Fetch Google Trends data for watchlist stocks."""
        logger.info("Running Google Trends bridge")
        now = datetime.now(timezone.utc)

        watchlist = _load_dynamic_watchlist()
        all_results = {}
        spikes = []

        # Process in batches of 5 (pytrends limit)
        keywords_list = list(watchlist.items())
        for i in range(0, len(keywords_list), 5):
            batch = keywords_list[i:i+5]
            search_terms = [v for _, v in batch]
            ticker_map = {v: k for k, v in batch}

            trends_data = _query_trends(search_terms)

            for search_term, data in trends_data.items():
                ticker = ticker_map.get(search_term, search_term)
                data["ticker"] = ticker
                data["search_term"] = search_term
                all_results[ticker] = data

                if data.get("is_spike"):
                    spikes.append({
                        "ticker": ticker,
                        "search_term": search_term,
                        "spike_ratio": data["spike_ratio"],
                        "current_interest": data["current"],
                        "baseline": data["baseline_avg"],
                    })

            # Rate limit between batches
            if i + 5 < len(keywords_list):
                time.sleep(2)

        # Sort spikes by ratio
        spikes.sort(key=lambda x: x["spike_ratio"], reverse=True)

        result = {
            "source": "google_trends_bridge",
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "timestamp_utc": now.isoformat(),
            "fresh": True,
            "data": {
                "trends": all_results,
                "spikes": spikes,
                "n_spikes": len(spikes),
                "n_tickers_scanned": len(all_results),
                "spike_threshold": SPIKE_THRESHOLD,
            },
        }

        # Persist
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        logger.info("Google Trends saved to %s (%d spikes detected)", OUTPUT_PATH, len(spikes))

        self._last_fetch = now
        self._consecutive_failures = 0
        return result

    def health(self) -> Dict[str, Any]:
        return {
            "source": "google_trends_bridge",
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "consecutive_failures": self._consecutive_failures,
            "status": "ok",
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    bridge = GoogleTrendsBridge()
    result = bridge.fetch()
    print(json.dumps({"n_spikes": result["data"]["n_spikes"], "spikes": result["data"]["spikes"]}, indent=2))

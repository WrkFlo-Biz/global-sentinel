#!/usr/bin/env python3
"""
Global Sentinel — ApeWisdom Social Sentiment Bridge

Fetches trending stock tickers from Reddit via ApeWisdom API.
Tracks mention counts, rank changes, and flags hot tickers (>100 mentions).

No API key needed.

Output: data/quantum_feed/reddit_trending.json
Tier 3, trust 0.5, TTL 30 min
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib.request

logger = logging.getLogger("global_sentinel.apewisdom_bridge")


def _fetch_json(url: str, timeout: int = 15) -> Optional[Any]:
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


class ApeWisdomBridge:
    """Fetch trending stock sentiment from Reddit via ApeWisdom."""

    DISPLAY_NAME = "apewisdom"
    CATEGORY = "social_sentiment"

    HOT_THRESHOLD = 100  # mentions in 24h to flag as "hot"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "reddit_trending.json"
        self._previous_data: Dict[str, int] = {}
        self._load_previous()

    def _load_previous(self):
        """Load previous data for momentum comparison."""
        if self.output_path.exists():
            try:
                prev = json.loads(self.output_path.read_text())
                for entry in prev.get("data", {}).get("all_stocks", []):
                    ticker = entry.get("ticker", "")
                    mentions = entry.get("mentions_24h", 0)
                    if ticker and mentions:
                        self._previous_data[ticker] = int(mentions)
            except Exception:
                pass

    def _fetch_filter(self, filter_name: str) -> List[Dict[str, Any]]:
        """Fetch a specific ApeWisdom filter."""
        url = f"https://apewisdom.io/api/v1.0/filter/{filter_name}"
        data = _fetch_json(url)
        if not data or "results" not in data:
            return []

        results = []
        for item in data["results"][:50]:  # Top 50
            ticker = item.get("ticker", "").upper()
            if not ticker or len(ticker) > 5:
                continue
            mentions = item.get("mentions", 0)
            rank = item.get("rank", 0)
            upvotes = item.get("upvotes", 0)

            # Momentum: compare to previous
            prev_mentions = self._previous_data.get(ticker, 0)
            mention_change = mentions - prev_mentions if prev_mentions else 0
            momentum = "surging" if mention_change > 50 else "rising" if mention_change > 10 else "falling" if mention_change < -10 else "stable"

            results.append({
                "ticker": ticker,
                "mentions_24h": mentions,
                "rank": rank,
                "upvotes": upvotes,
                "mention_change": mention_change,
                "momentum": momentum,
                "hot": mentions >= self.HOT_THRESHOLD,
            })

        return results

    def poll(self) -> Dict[str, Any]:
        """Poll ApeWisdom for Reddit trending stocks."""
        all_stocks = self._fetch_filter("all-stocks")
        wsb_stocks = self._fetch_filter("wallstreetbets")

        # Identify hot tickers
        hot_tickers = [s for s in all_stocks if s.get("hot")]
        surging_tickers = [s for s in all_stocks if s.get("momentum") == "surging"]

        # WSB-specific hot
        wsb_hot = [s for s in wsb_stocks if s.get("hot")]

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "apewisdom",
            "data": {
                "all_stocks": all_stocks[:30],
                "wallstreetbets": wsb_stocks[:20],
                "hot_tickers": [t["ticker"] for t in hot_tickers],
                "surging_tickers": [{"ticker": t["ticker"], "mentions": t["mentions_24h"], "change": t["mention_change"]} for t in surging_tickers],
                "wsb_hot": [t["ticker"] for t in wsb_hot],
                "summary": {
                    "total_tracked": len(all_stocks),
                    "hot_count": len(hot_tickers),
                    "surging_count": len(surging_tickers),
                    "wsb_hot_count": len(wsb_hot),
                    "top_ticker": all_stocks[0]["ticker"] if all_stocks else None,
                    "top_mentions": all_stocks[0]["mentions_24h"] if all_stocks else 0,
                },
            },
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2))
        logger.info(f"[ApeWisdomBridge] {len(all_stocks)} stocks tracked, {len(hot_tickers)} hot, {len(surging_tickers)} surging")

        return result

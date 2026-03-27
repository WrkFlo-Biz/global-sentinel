#!/usr/bin/env python3
"""
Global Sentinel — Social Trending Detector

Polls StockTwits trending symbols and Reddit r/wallstreetbets
to detect trending tickers with abnormal mention rates.

Output: data/quantum_feed/social_trending.json
Tier 3, trust 0.5, TTL 30 min
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.social_trending_bridge")

# Common words that look like tickers but are not
TICKER_BLACKLIST = {
    "A", "I", "AM", "PM", "DD", "CEO", "IPO", "ETF", "GDP", "CPI",
    "FBI", "SEC", "FDA", "FED", "ATH", "IMO", "YOLO", "FOMO", "HODL",
    "LMAO", "LOL", "OMG", "WTF", "FYI", "TIL", "ELI", "ITT", "WSB",
    "USA", "UK", "EU", "USD", "THE", "FOR", "ARE", "AND", "NOT", "ALL",
    "NEW", "HAS", "WAS", "CAN", "ANY", "NOW", "ONE", "TWO", "OUR",
    "RIP", "PSA", "TBH", "IMO", "EDIT", "TLDR", "JUST", "LIKE", "VERY",
}

VALID_TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b")
BARE_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")


def _fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
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


def _fetch_text(url: str, timeout: int = 15) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (GlobalSentinel/1.0)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return None


def get_stocktwits_trending() -> List[Dict[str, Any]]:
    """Get trending symbols from StockTwits."""
    data = _fetch_json("https://api.stocktwits.com/api/2/trending/symbols.json")
    if not data or "symbols" not in data:
        return []
    results = []
    for sym_data in data["symbols"]:
        results.append({
            "ticker": sym_data.get("symbol", ""),
            "title": sym_data.get("title", ""),
            "source": "stocktwits_trending",
        })
    return results


def get_reddit_wsb_tickers() -> Dict[str, int]:
    """Extract ticker mentions from r/wallstreetbets hot posts."""
    ticker_counts: Dict[str, int] = {}

    # Use Reddit JSON API (no auth needed for public subreddits)
    data = _fetch_json("https://www.reddit.com/r/wallstreetbets/hot.json?limit=50")
    if not data or "data" not in data:
        return ticker_counts

    for post in data.get("data", {}).get("children", []):
        post_data = post.get("data", {})
        title = post_data.get("title", "")
        selftext = post_data.get("selftext", "")[:500]
        text = f"{title} {selftext}"

        # Extract $TICKER mentions
        for match in VALID_TICKER_RE.findall(text):
            if match not in TICKER_BLACKLIST and len(match) >= 2:
                ticker_counts[match] = ticker_counts.get(match, 0) + 1

        # Also check bare uppercase words that look like tickers
        for match in BARE_TICKER_RE.findall(text):
            if match not in TICKER_BLACKLIST and len(match) >= 2:
                ticker_counts[match] = ticker_counts.get(match, 0) + 1

    return ticker_counts


def _load_previous(path: Path) -> Dict[str, int]:
    """Load previous mention counts for momentum calculation."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        prev = {}
        for item in data.get("trending_tickers", []):
            prev[item["ticker"]] = item.get("mentions", 0)
        return prev
    except Exception:
        return {}


class SocialTrendingBridge:
    """Bridge for social media trending detection."""

    DISPLAY_NAME = "social_trending"
    CATEGORY = "social_intelligence"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "social_trending.json"

    def poll(self) -> Dict[str, Any]:
        previous_counts = _load_previous(self.output_path)

        # StockTwits trending
        st_trending = get_stocktwits_trending()
        st_tickers = {item["ticker"] for item in st_trending}

        # Reddit WSB
        time.sleep(1)  # rate limit courtesy
        wsb_counts = get_reddit_wsb_tickers()

        # Merge
        all_tickers: Dict[str, Dict[str, Any]] = {}

        for item in st_trending:
            t = item["ticker"]
            all_tickers[t] = {
                "ticker": t,
                "mentions": wsb_counts.get(t, 0) + 1,  # +1 for trending
                "sources": ["stocktwits_trending"],
                "sentiment": "neutral",
            }

        for ticker, count in wsb_counts.items():
            if ticker in all_tickers:
                all_tickers[ticker]["mentions"] += count
                if "reddit_wsb" not in all_tickers[ticker]["sources"]:
                    all_tickers[ticker]["sources"].append("reddit_wsb")
            else:
                all_tickers[ticker] = {
                    "ticker": ticker,
                    "mentions": count,
                    "sources": ["reddit_wsb"],
                    "sentiment": "neutral",
                }

        # Compute momentum
        NORMAL_THRESHOLD = 3  # baseline mention count
        for ticker, data in all_tickers.items():
            prev = previous_counts.get(ticker, NORMAL_THRESHOLD)
            if prev < 1:
                prev = 1
            data["momentum"] = round(data["mentions"] / prev, 2)
            data["is_trending"] = data["momentum"] > 3.0 or (ticker in st_tickers)

        # Sort by mentions descending
        trending_list = sorted(all_tickers.values(), key=lambda x: x["mentions"], reverse=True)
        hot_tickers = [t for t in trending_list if t.get("is_trending")]

        output = {
            "source": "social_trending_bridge",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "total_tickers_detected": len(trending_list),
            "hot_trending_count": len(hot_tickers),
            "hot_trending": hot_tickers[:20],
            "trending_tickers": trending_list[:50],
            "stocktwits_trending_count": len(st_trending),
            "reddit_wsb_unique_tickers": len(wsb_counts),
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(output, indent=2, default=str))
        logger.info("Social trending: %d tickers, %d hot", len(trending_list), len(hot_tickers))

        return {
            "source": "social_trending_bridge",
            "source_tier": "tier_3_research",
            "trust_weight": 0.5,
            "timestamp_utc": output["timestamp_utc"],
            "fresh": True,
            "data": output,
        }


def main():
    logging.basicConfig(level=logging.INFO)
    bridge = SocialTrendingBridge()
    result = bridge.poll()
    print(json.dumps({
        "total": result["data"]["total_tickers_detected"],
        "hot": result["data"]["hot_trending_count"],
        "top_5": result["data"]["trending_tickers"][:5],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Global Sentinel V4 — DOL/BLS RSS Feed Ingestion MCP

Ingests Department of Labor / Bureau of Labor Statistics RSS feeds
for labor market disruption signals.
"""

import json
import sys
from datetime import datetime, timezone

try:
    import feedparser
except ImportError:
    feedparser = None

BLS_FEEDS = [
    "https://www.bls.gov/feed/bls_latest.rss",
    "https://www.bls.gov/feed/cpi_latest.rss",
    "https://www.bls.gov/feed/ppi_latest.rss",
    "https://www.bls.gov/feed/emp_latest.rss",
]


class DOLBLSIngester:
    """Ingest DOL/BLS RSS feeds and extract labor disruption signals."""

    def fetch_latest(self, max_entries: int = 20) -> dict:
        if feedparser is None:
            return {"error": "feedparser not installed", "entries": [], "fresh": False}

        entries = []
        for feed_url in BLS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:max_entries]:
                    entries.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "summary": entry.get("summary", "")[:500],
                        "source": feed_url,
                    })
            except Exception as e:
                entries.append({"error": str(e), "source": feed_url})

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entries": entries[:max_entries],
            "count": len(entries),
            "fresh": len(entries) > 0,
        }

    def compute_labor_disruption_score(self, entries: list) -> float:
        """Simple keyword-based scoring for labor disruption signals."""
        disruption_keywords = [
            "layoff", "unemployment", "jobless", "strike", "shutdown",
            "furlough", "recession", "decline", "contraction", "slowdown",
        ]
        if not entries:
            return 0.0

        hits = 0
        for entry in entries:
            text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
            if any(kw in text for kw in disruption_keywords):
                hits += 1

        return min(1.0, hits / max(len(entries), 1))


def serve_mcp():
    ingester = DOLBLSIngester()
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")
            if method == "fetch":
                data = ingester.fetch_latest()
                score = ingester.compute_labor_disruption_score(data["entries"])
                data["labor_disruption_score"] = score
                result = data
            elif method == "status":
                result = {"status": "ok", "service": "dol-bls-rss-mcp"}
            else:
                result = {"error": f"Unknown method: {method}"}
            print(json.dumps({"id": request.get("id"), "result": result}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    serve_mcp()

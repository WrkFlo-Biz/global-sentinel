#!/usr/bin/env python3
"""Global Sentinel V4 — USCIS Policy Updates RSS Feed MCP

Ingests USCIS and immigration policy feeds for policy uncertainty signals.
"""

import json
import sys
from datetime import datetime, timezone

try:
    import feedparser
except ImportError:
    feedparser = None

USCIS_FEEDS = [
    "https://www.uscis.gov/rss/news",
    "https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bagencies%5D%5B%5D=homeland-security-department",
]


class USCISIngester:
    """Ingest USCIS and immigration policy feeds."""

    def fetch_latest(self, max_entries: int = 20) -> dict:
        if feedparser is None:
            return {"error": "feedparser not installed", "entries": [], "fresh": False}

        entries = []
        for feed_url in USCIS_FEEDS:
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

    def compute_policy_uncertainty_score(self, entries: list) -> float:
        """Keyword-based scoring for policy uncertainty."""
        uncertainty_keywords = [
            "executive order", "ban", "restriction", "suspend", "revoke",
            "emergency", "moratorium", "expedited removal", "asylum",
            "deportation", "enforcement", "travel ban", "visa denial",
        ]
        if not entries:
            return 0.0

        hits = 0
        for entry in entries:
            text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
            if any(kw in text for kw in uncertainty_keywords):
                hits += 1

        return min(1.0, hits / max(len(entries), 1))


def serve_mcp():
    ingester = USCISIngester()
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")
            if method == "fetch":
                data = ingester.fetch_latest()
                score = ingester.compute_policy_uncertainty_score(data["entries"])
                data["policy_uncertainty_score"] = score
                result = data
            elif method == "status":
                result = {"status": "ok", "service": "uscis-rss-mcp"}
            else:
                result = {"error": f"Unknown method: {method}"}
            print(json.dumps({"id": request.get("id"), "result": result}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    serve_mcp()

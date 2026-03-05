#!/usr/bin/env python3
"""Global Sentinel V4 — Fallback News MCP

Public news sources (GDELT, RSS) as fallback when premium news APIs are unavailable.
"""

import json
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

try:
    import feedparser
except ImportError:
    feedparser = None

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]


class FallbackNewsIngester:
    """Fallback news ingestion from GDELT and public RSS."""

    def fetch_gdelt(self, query: str = "geopolitical crisis", max_records: int = 10) -> list:
        if not requests:
            return []
        try:
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": max_records,
                "format": "json",
            }
            resp = requests.get(GDELT_DOC_API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", [])
            return [{
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("domain", "GDELT"),
                "date": a.get("seendate", ""),
                "language": a.get("language", ""),
            } for a in articles]
        except Exception:
            return []

    def fetch_rss(self, max_per_feed: int = 5) -> list:
        if not feedparser:
            return []
        entries = []
        for feed_url in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:max_per_feed]:
                    entries.append({
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "source": feed_url.split("/")[2],
                        "date": entry.get("published", ""),
                    })
            except Exception:
                continue
        return entries

    def fetch_all(self) -> dict:
        gdelt = self.fetch_gdelt()
        rss = self.fetch_rss()
        all_articles = gdelt + rss

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "articles": all_articles,
            "gdelt_count": len(gdelt),
            "rss_count": len(rss),
            "total": len(all_articles),
            "fresh": len(all_articles) > 0,
            "is_fallback": True,
        }


def serve_mcp():
    ingester = FallbackNewsIngester()
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")
            if method == "fetch_all":
                result = ingester.fetch_all()
            elif method == "fetch_gdelt":
                query = request.get("params", {}).get("query", "geopolitical crisis")
                articles = ingester.fetch_gdelt(query=query)
                result = {"articles": articles, "count": len(articles)}
            elif method == "status":
                result = {"status": "ok", "service": "fallback-news-mcp"}
            else:
                result = {"error": f"Unknown method: {method}"}
            print(json.dumps({"id": request.get("id"), "result": result}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    serve_mcp()

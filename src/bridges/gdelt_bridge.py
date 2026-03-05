#!/usr/bin/env python3
"""
Global Sentinel V5.1 - GDELT Bridge

Free, no API key required. Queries GDELT 2.0 GKG (Global Knowledge Graph)
and Event Database for geopolitical event monitoring.

Sources:
- GDELT GKG API: https://api.gdeltproject.org/api/v2/doc/doc
- GDELT Event API: https://api.gdeltproject.org/api/v2/events/events

Outputs events with tone, theme, and location data for regime scoring.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_get_json(url: str, timeout: int = 20, retries: int = 2) -> Any:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "GlobalSentinel-GDELTBridge/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(5 * (attempt + 1))  # Backoff: 5s, 10s
                continue
            return None
        except Exception:
            return None


def safe_get_text(url: str, timeout: int = 20) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel-GDELTBridge/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


# Consolidated queries to reduce API calls (GDELT rate limits at ~1 req/sec)
GDELT_QUERIES = [
    "war OR conflict OR sanctions OR tariff OR embargo OR missile",
    "airline disruption OR flight cancellation OR airspace OR energy crisis OR OPEC",
    "central bank OR inflation OR recession OR cyber attack OR supply chain",
]


class GDELTBridge:
    """
    Queries GDELT for geopolitical events relevant to regime shift scoring.
    Free, no API key. Rate limit: be respectful (1 query/sec).
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "gdelt"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._seen_hashes: set = set()

    def poll(self) -> List[Dict[str, Any]]:
        """Query GDELT for recent geopolitical events. Returns normalized events."""
        events: List[Dict[str, Any]] = []

        for i, query in enumerate(GDELT_QUERIES):
            if i > 0:
                time.sleep(2)  # Respect GDELT rate limits
            try:
                batch = self._query_gdelt_doc(query)
                events.extend(batch)
            except Exception:
                continue

        # Deduplicate by content hash
        unique = self._deduplicate(events)

        # Cache results
        self._cache_results(unique)

        return unique

    def build_snapshot_section(self) -> Dict[str, Any]:
        """Returns the canonical snapshot['gdelt_events'] list."""
        events = self.poll()
        return {
            "timestamp_utc": iso_now(),
            "event_count": len(events),
            "events": events,
        }

    def _query_gdelt_doc(self, query: str) -> List[Dict[str, Any]]:
        """Query GDELT DOC API for articles matching a query."""
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": "25",
            "timespan": "24h",
            "format": "json",
            "sort": "ToneDesc",
        }
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?{urllib.parse.urlencode(params)}"
        data = safe_get_json(url)

        if not data:
            return []

        articles = data.get("articles", [])
        events = []
        for art in articles:
            if not isinstance(art, dict):
                continue

            title = art.get("title", "")
            source = art.get("domain", "")
            tone = art.get("tone", 0.0)
            url_link = art.get("url", "")
            seendate = art.get("seendate", "")

            if not title:
                continue

            events.append({
                "title": title,
                "source_domain": source,
                "url": url_link,
                "avg_tone": self._parse_tone(tone),
                "seen_date": seendate,
                "query_matched": query[:50],
                "timestamp_utc": iso_now(),
            })

        return events

    def _parse_tone(self, tone: Any) -> float:
        """Parse GDELT tone value. Negative = negative sentiment."""
        try:
            if isinstance(tone, str):
                # GDELT sometimes returns comma-separated tone components
                parts = tone.split(",")
                return float(parts[0])
            return float(tone)
        except Exception:
            return 0.0

    def _deduplicate(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate events by title hash."""
        unique = []
        for evt in events:
            h = hashlib.sha256(evt.get("title", "").encode()).hexdigest()[:16]
            if h not in self._seen_hashes:
                self._seen_hashes.add(h)
                unique.append(evt)
        return unique

    def _cache_results(self, events: List[Dict[str, Any]]):
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"gdelt_{tag}.json"
        cache_file.write_text(
            json.dumps({"event_count": len(events), "events": events}, indent=2),
            encoding="utf-8",
        )


# --- CLI ---
def main():
    import argparse
    p = argparse.ArgumentParser(description="Global Sentinel GDELT Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None)
    args = p.parse_args()

    bridge = GDELTBridge(Path(args.repo_root).resolve())
    result = bridge.build_snapshot_section()

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

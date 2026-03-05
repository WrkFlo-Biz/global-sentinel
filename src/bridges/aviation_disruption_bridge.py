#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Aviation & Travel Disruption Bridge

Monitors real-time news for aviation, travel, and shipping disruptions
caused by war, geopolitics, sanctions, and airspace closures.

Sources (all free/open):
- GDELT Project API (global news monitoring)
- FlightRadar24 RSS / NOTAM feeds (aviation-specific)
- Reuters/AP/BBC RSS feeds (general geopolitical)
- FAA NOTAM API (US airspace)
- Eurocontrol Network Manager (European airspace)

Emits normalized disruption_event packets for downstream scoring.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import feedparser
except ImportError:
    feedparser = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_get_json(url: str, timeout: int = 15) -> Any:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel-AviationBridge/1.0 (+shadow-mode)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


def safe_get_text(url: str, timeout: int = 15) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel-AviationBridge/1.0 (+shadow-mode)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --- Disruption keyword taxonomies ---
AVIATION_KEYWORDS = [
    "airspace closure", "airspace closed", "no-fly zone", "flight diversion",
    "flight cancellation", "NOTAM", "aviation ban", "grounded flights",
    "airport closure", "airport closed", "flight suspension", "air traffic",
    "airline disruption", "overfly ban", "overflight ban", "airspace restriction",
    "missile", "drone attack airport", "anti-aircraft", "air defense",
]

TRAVEL_DISRUPTION_KEYWORDS = [
    "travel ban", "travel advisory", "evacuation", "embassy closure",
    "border closure", "visa suspension", "tourism collapse", "hotel cancellation",
    "cruise diversion", "port closure", "tourist warning", "state of emergency",
    "martial law", "curfew", "refugee crisis",
]

SHIPPING_DISRUPTION_KEYWORDS = [
    "shipping disruption", "strait closure", "canal blockage", "piracy",
    "houthi attack", "red sea", "suez canal", "bab el-mandeb",
    "vessel seized", "tanker attack", "maritime security", "shipping reroute",
    "container shortage", "port congestion", "freight rate spike",
]

WAR_ESCALATION_KEYWORDS = [
    "war", "invasion", "military operation", "airstrike", "bombing",
    "ceasefire", "peace talks", "sanctions", "arms embargo", "military buildup",
    "nuclear threat", "escalation", "conflict zone", "humanitarian corridor",
]

ALL_DISRUPTION_KEYWORDS = (
    AVIATION_KEYWORDS + TRAVEL_DISRUPTION_KEYWORDS
    + SHIPPING_DISRUPTION_KEYWORDS + WAR_ESCALATION_KEYWORDS
)


# --- RSS feed sources for aviation/travel news ---
AVIATION_RSS_FEEDS = [
    # General aviation news
    ("https://www.flightglobal.com/rss", "flightglobal"),
    ("https://feeds.simpleflying.com/simpleflying", "simpleflying"),
    # Geopolitical / war news (for disruption signals)
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "bbc_world"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "nytimes_world"),
    ("https://www.aljazeera.com/xml/rss/all.xml", "aljazeera"),
    ("https://www.reuters.com/rssFeed/worldNews", "reuters_world"),
    # Defense / military
    ("https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "defensenews"),
]

# --- GDELT queries for disruption monitoring ---
GDELT_DISRUPTION_QUERIES = [
    "airspace closure OR flight cancellation OR aviation disruption",
    "shipping disruption OR red sea attack OR suez canal",
    "travel ban OR border closure OR evacuation order",
    "war escalation OR military operation OR airstrike",
]

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


# --- Affected sector mapping ---
DISRUPTION_SECTOR_MAP = {
    "airspace": ["DAL", "UAL", "AAL", "LUV", "JBLU", "JETS", "BA"],
    "aviation_general": ["DAL", "UAL", "AAL", "LUV", "JBLU", "JETS", "BA", "RTX"],
    "defense_escalation": ["RTX", "LMT", "NOC", "GD", "BA"],
    "travel_hospitality": ["MAR", "HLT", "BKNG", "EXPE", "ABNB", "CCL", "RCL"],
    "shipping": ["ZIM", "MATX", "FDX", "UPS"],
    "cruise": ["CCL", "RCL"],
    "insurance_risk": ["AIG", "BRK-B"],
    "oil_supply": ["CL"],  # from existing watchlist
    "broad_market": ["SPY", "QQQ", "VIX"],
}


class AviationDisruptionBridge:
    """
    Monitors aviation, travel, and shipping disruptions from open-source feeds.
    Emits normalized disruption_event packets.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "aviation_disruption_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.seen_hashes_path = self.cache_dir / "seen_hashes.json"
        self.seen_hashes = self._load_seen_hashes()

    def poll(self) -> List[Dict[str, Any]]:
        """Main entry point. Returns list of disruption event packets."""
        packets: List[Dict[str, Any]] = []

        # 1. GDELT disruption queries
        for query in GDELT_DISRUPTION_QUERIES:
            try:
                gdelt_articles = self._fetch_gdelt(query, max_records=15)
                for article in gdelt_articles:
                    packet = self._article_to_disruption_packet(article, source_type="gdelt")
                    if packet:
                        packets.append(packet)
            except Exception as e:
                packets.append(self._error_packet(f"gdelt_error: {e}"))

        # 2. RSS aviation/travel feeds
        if feedparser:
            for feed_url, feed_name in AVIATION_RSS_FEEDS:
                try:
                    rss_articles = self._fetch_rss(feed_url, feed_name, max_per_feed=10)
                    for article in rss_articles:
                        packet = self._article_to_disruption_packet(article, source_type="rss")
                        if packet:
                            packets.append(packet)
                except Exception as e:
                    packets.append(self._error_packet(f"rss_error:{feed_name}: {e}"))

        # 3. Deduplicate
        packets = self._deduplicate(packets)

        # 4. Save seen hashes
        self._save_seen_hashes()

        # 5. Cache results
        self._cache_results(packets)

        return packets

    # --- GDELT ---
    def _fetch_gdelt(self, query: str, max_records: int = 15) -> List[Dict[str, Any]]:
        params = urllib.parse.urlencode({
            "query": query,
            "mode": "artlist",
            "maxrecords": max_records,
            "format": "json",
            "sort": "DateDesc",
        })
        url = f"{GDELT_DOC_API}?{params}"
        data = safe_get_json(url)
        if not data:
            return []
        articles = data.get("articles") or []
        return [{
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "source": a.get("domain", "gdelt"),
            "date": a.get("seendate", ""),
            "language": a.get("language", "English"),
            "tone": a.get("tone", 0),
        } for a in articles]

    # --- RSS ---
    def _fetch_rss(self, feed_url: str, feed_name: str, max_per_feed: int = 10) -> List[Dict[str, Any]]:
        if not feedparser:
            return []
        feed = feedparser.parse(feed_url)
        articles = []
        for entry in feed.entries[:max_per_feed]:
            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": feed_name,
                "date": entry.get("published", ""),
                "summary": entry.get("summary", "")[:500],
            })
        return articles

    # --- Disruption classification ---
    def _article_to_disruption_packet(
        self, article: Dict[str, Any], source_type: str
    ) -> Optional[Dict[str, Any]]:
        title = (article.get("title") or "").lower()
        summary = (article.get("summary") or "").lower()
        text = f"{title} {summary}"

        # Check if article matches any disruption keywords
        matched_keywords = []
        for kw in ALL_DISRUPTION_KEYWORDS:
            if kw.lower() in text:
                matched_keywords.append(kw)

        if not matched_keywords:
            return None

        # Classify disruption type
        disruption_type = self._classify_disruption(matched_keywords)

        # Map to affected sectors/symbols
        affected_sectors = self._map_affected_sectors(disruption_type, matched_keywords)
        affected_symbols = []
        for sector in affected_sectors:
            affected_symbols.extend(DISRUPTION_SECTOR_MAP.get(sector, []))
        affected_symbols = sorted(set(affected_symbols))

        # Severity estimation based on keyword density and type
        severity = self._estimate_severity(matched_keywords, disruption_type)

        return {
            "schema_version": "disruption_event.v1",
            "timestamp_utc": iso_now(),
            "source_type": source_type,
            "source": article.get("source", "unknown"),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "article_date": article.get("date", ""),
            "disruption_type": disruption_type,
            "matched_keywords": matched_keywords,
            "affected_sectors": affected_sectors,
            "affected_symbols": affected_symbols,
            "severity": severity,
            "confidence": min(0.3 + 0.1 * len(matched_keywords), 0.85),
            "content_hash": content_hash(article.get("url", "") + article.get("title", "")),
        }

    def _classify_disruption(self, keywords: List[str]) -> str:
        kw_set = set(k.lower() for k in keywords)
        aviation_match = sum(1 for k in AVIATION_KEYWORDS if k.lower() in kw_set)
        travel_match = sum(1 for k in TRAVEL_DISRUPTION_KEYWORDS if k.lower() in kw_set)
        shipping_match = sum(1 for k in SHIPPING_DISRUPTION_KEYWORDS if k.lower() in kw_set)
        war_match = sum(1 for k in WAR_ESCALATION_KEYWORDS if k.lower() in kw_set)

        scores = {
            "aviation_disruption": aviation_match * 2,
            "travel_disruption": travel_match * 2,
            "shipping_disruption": shipping_match * 2,
            "war_escalation": war_match * 1.5,
        }
        return max(scores, key=scores.get)

    def _map_affected_sectors(self, disruption_type: str, keywords: List[str]) -> List[str]:
        sectors = []
        if disruption_type == "aviation_disruption":
            sectors.extend(["airspace", "aviation_general", "travel_hospitality", "insurance_risk"])
        elif disruption_type == "travel_disruption":
            sectors.extend(["travel_hospitality", "cruise", "aviation_general"])
        elif disruption_type == "shipping_disruption":
            sectors.extend(["shipping", "oil_supply", "insurance_risk"])
        elif disruption_type == "war_escalation":
            sectors.extend(["defense_escalation", "broad_market", "oil_supply", "insurance_risk"])
            # Check for aviation-specific war disruption
            kw_lower = set(k.lower() for k in keywords)
            if any(k in kw_lower for k in ["airspace", "no-fly zone", "flight", "airport"]):
                sectors.extend(["airspace", "aviation_general"])
            if any(k in kw_lower for k in ["shipping", "red sea", "suez", "tanker"]):
                sectors.extend(["shipping"])
        return sorted(set(sectors))

    def _estimate_severity(self, keywords: List[str], disruption_type: str) -> str:
        count = len(keywords)
        high_severity_kw = {
            "war", "invasion", "airstrike", "bombing", "nuclear threat",
            "airspace closure", "no-fly zone", "strait closure",
        }
        has_high = any(k.lower() in high_severity_kw for k in keywords)

        if has_high or count >= 4:
            return "high"
        elif count >= 2:
            return "medium"
        return "low"

    # --- Deduplication ---
    def _deduplicate(self, packets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique = []
        for p in packets:
            h = p.get("content_hash")
            if h and h not in self.seen_hashes:
                self.seen_hashes[h] = iso_now()
                unique.append(p)
        return unique

    def _load_seen_hashes(self) -> Dict[str, str]:
        if self.seen_hashes_path.exists():
            try:
                data = json.loads(self.seen_hashes_path.read_text(encoding="utf-8"))
                # Prune hashes older than 48 hours
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
                return {k: v for k, v in data.items() if v > cutoff}
            except Exception:
                return {}
        return {}

    def _save_seen_hashes(self):
        self.seen_hashes_path.write_text(
            json.dumps(self.seen_hashes, indent=2), encoding="utf-8"
        )

    # --- Caching ---
    def _cache_results(self, packets: List[Dict[str, Any]]):
        if not packets:
            return
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"disruption_events_{tag}.json"
        cache_file.write_text(json.dumps(packets, indent=2), encoding="utf-8")

    # --- Error packet ---
    def _error_packet(self, error_msg: str) -> Dict[str, Any]:
        return {
            "schema_version": "disruption_event.v1",
            "timestamp_utc": iso_now(),
            "source_type": "error",
            "disruption_type": "bridge_error",
            "error": error_msg,
            "severity": "low",
            "affected_sectors": [],
            "affected_symbols": [],
        }


# --- CLI ---
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None)
    args = p.parse_args()

    bridge = AviationDisruptionBridge(Path(args.repo_root).resolve())
    packets = bridge.poll()

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(packets, indent=2), encoding="utf-8")
    else:
        print(json.dumps(packets, indent=2))

    print(f"Disruption events found: {len(packets)}", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Global Sentinel V5.2 — Narrative Velocity Bridge

Measures the "infection rate" of news events — how fast a story is
propagating across global media. High velocity indicates the narrative
has captured collective attention.

Combined with GCP consciousness coherence:
- High velocity + High coherence = Systemic shock (Black Swan Shield)
- High velocity + Low coherence  = Noise/bear trap (Fake News Filter)
- Low velocity  + High coherence = Pre-event accumulation (Pre-Pulse)

Sources:
- GDELT DOC API (free, real-time article counts)
- Finnhub news sentiment (already polled)
- Headline deduplication and velocity calculation

No additional API keys required — uses existing GDELT and Finnhub data.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_get_json(url: str, timeout: int = 15) -> Any:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel-NarrativeVelocity/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


# Keywords that indicate geopolitically significant narratives
TRACKED_NARRATIVES = {
    "war_conflict": {
        "keywords": ["war", "invasion", "airstrike", "missile", "military operation",
                      "troops deployed", "bombardment", "casualties"],
        "weight": 2.0,
    },
    "iran_middle_east": {
        "keywords": ["iran", "tehran", "strait of hormuz", "hezbollah",
                      "israel strike", "beirut", "persian gulf", "IRGC"],
        "weight": 2.0,
    },
    "financial_crisis": {
        "keywords": ["bank failure", "credit default", "liquidity crisis",
                      "market crash", "circuit breaker", "margin call", "contagion"],
        "weight": 1.8,
    },
    "fed_policy": {
        "keywords": ["fed rate", "interest rate decision", "powell",
                      "rate hike", "rate cut", "quantitative tightening", "FOMC"],
        "weight": 1.5,
    },
    "energy_shock": {
        "keywords": ["oil price", "gasoline shortage", "OPEC cut",
                      "pipeline attack", "refinery explosion", "energy crisis"],
        "weight": 1.7,
    },
    "pandemic": {
        "keywords": ["pandemic", "lockdown", "variant", "WHO emergency",
                      "outbreak", "quarantine"],
        "weight": 1.6,
    },
    "trade_sanctions": {
        "keywords": ["sanctions", "trade war", "tariff", "embargo",
                      "export ban", "SWIFT ban"],
        "weight": 1.4,
    },
    "ai_technology_disruption": {
        "keywords": ["artificial intelligence", "AI breakthrough", "AGI", "GPU shortage",
                      "AI regulation", "AI executive order", "machine learning", "ChatGPT",
                      "AI arms race", "autonomous weapons", "AI chip export", "semiconductor",
                      "NVIDIA", "AI infrastructure", "data center", "AI job displacement",
                      "AI warfare", "deepfake", "AI safety", "superintelligence"],
        "weight": 1.8,
    },
    "tech_labor_disruption": {
        "keywords": ["tech layoffs", "automation unemployment", "AI replacing jobs",
                      "workforce disruption", "UBI", "universal basic income",
                      "reskilling", "gig economy collapse", "white collar automation"],
        "weight": 1.3,
    },
    "cyber_warfare": {
        "keywords": ["cyberattack", "ransomware", "critical infrastructure hack",
                      "state-sponsored hack", "zero day exploit", "cyber warfare",
                      "power grid attack", "election interference"],
        "weight": 1.6,
    },
}


class NarrativeVelocityBridge:
    """
    Measures narrative propagation speed across global media.
    The "Narrative Layer" — coinciding with events as they unfold.
    """

    # GDELT DOC API for real-time article volume
    GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "narrative_velocity"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def poll(self, finnhub_headlines: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Calculate narrative velocity across tracked themes.

        Args:
            finnhub_headlines: Optional pre-fetched Finnhub headlines from bridge

        Returns:
            Dict with velocity_score, dominant_narrative, theme_velocities,
            infection_rate, and evidence.
        """
        result = {
            "timestamp_utc": iso_now(),
            "velocity_score": 0.0,        # Aggregate velocity (0-100)
            "infection_rate": 0.0,         # How fast the dominant story is spreading
            "dominant_narrative": None,     # The fastest-spreading theme
            "theme_velocities": {},        # Per-theme velocity scores
            "article_counts": {},          # Raw article counts per theme
            "total_articles": 0,
            "evidence": [],
            "fresh": False,
        }

        # Poll GDELT DOC API for each narrative theme
        theme_scores = {}
        article_counts = {}
        total = 0

        import time as _time
        for theme_name, theme_config in TRACKED_NARRATIVES.items():
            keywords = theme_config["keywords"]
            weight = theme_config["weight"]

            _time.sleep(1)  # Rate limit: 1s between GDELT queries to avoid 429
            count = self._query_gdelt_article_count(keywords)
            if count is not None:
                article_counts[theme_name] = count
                # Weighted velocity: articles * narrative weight
                velocity = count * weight
                theme_scores[theme_name] = round(velocity, 1)
                total += count

        # Supplement with Finnhub headlines if provided
        if finnhub_headlines:
            fh_velocity = self._score_finnhub_velocity(finnhub_headlines)
            for theme, v in fh_velocity.items():
                theme_scores[theme] = theme_scores.get(theme, 0) + v

        if theme_scores:
            result["theme_velocities"] = theme_scores
            result["article_counts"] = article_counts
            result["total_articles"] = total
            result["fresh"] = True

            # Aggregate velocity score (normalized 0-100)
            max_velocity = max(theme_scores.values()) if theme_scores else 0
            result["velocity_score"] = round(min(100, max_velocity), 1)

            # Dominant narrative
            dominant = max(theme_scores, key=theme_scores.get)
            result["dominant_narrative"] = dominant
            result["infection_rate"] = round(theme_scores[dominant], 1)

            # Evidence
            result["evidence"] = self._generate_evidence(result)

        # Cache
        self._cache_result(result)

        return result

    def _query_gdelt_article_count(self, keywords: List[str], timespan: str = "1h") -> Optional[int]:
        """
        Query GDELT DOC API for article count matching keywords in the last hour.
        This measures how many articles globally are discussing a topic.
        """
        # Build query: OR of all keywords
        query = " OR ".join(f'"{kw}"' for kw in keywords[:5])  # Limit to 5 per query
        params = {
            "query": query,
            "mode": "ArtCount",           # Case-sensitive GDELT mode
            "timespan": timespan,         # Last 1 hour
            "format": "json",
        }

        url = f"{self.GDELT_DOC_URL}?{urllib.parse.urlencode(params)}"
        data = safe_get_json(url, timeout=12)

        if data is None:
            return None

        # GDELT returns article count in various formats
        if isinstance(data, dict):
            # Try common response fields
            count = data.get("artcount", data.get("count", data.get("timeline", [])))
            if isinstance(count, int):
                return count
            if isinstance(count, list) and count:
                # Timeline format: sum the last entries
                return sum(
                    int(entry.get("count", entry.get("value", 0)))
                    for entry in count[-4:]  # Last 4 time buckets (1 hour)
                    if isinstance(entry, dict)
                )
        elif isinstance(data, int):
            return data

        return 0

    def _score_finnhub_velocity(self, headlines: List[Dict]) -> Dict[str, float]:
        """Score narrative velocity from Finnhub headlines."""
        theme_hits: Dict[str, int] = {}

        for headline in headlines:
            text = (headline.get("headline", "") + " " + headline.get("summary", "")).lower()
            for theme_name, theme_config in TRACKED_NARRATIVES.items():
                for kw in theme_config["keywords"]:
                    if kw.lower() in text:
                        theme_hits[theme_name] = theme_hits.get(theme_name, 0) + 1
                        break  # One hit per headline per theme

        # Convert hits to velocity contribution
        return {
            theme: round(count * TRACKED_NARRATIVES[theme]["weight"] * 0.5, 1)
            for theme, count in theme_hits.items()
        }

    def _generate_evidence(self, result: Dict[str, Any]) -> List[str]:
        """Generate human-readable evidence."""
        evidence = []
        velocity = result.get("velocity_score", 0)
        dominant = result.get("dominant_narrative")
        total = result.get("total_articles", 0)

        if velocity > 50:
            evidence.append(
                f"NARRATIVE SURGE: velocity={velocity:.0f}, "
                f"dominant={dominant}, {total} articles/hr"
            )
        elif velocity > 20:
            evidence.append(
                f"Narrative heating: velocity={velocity:.0f}, "
                f"dominant={dominant}, {total} articles/hr"
            )
        elif velocity > 5:
            evidence.append(
                f"Narrative activity: {dominant} at velocity {velocity:.0f}"
            )

        # Report top themes
        themes = result.get("theme_velocities", {})
        top_themes = sorted(themes.items(), key=lambda x: x[1], reverse=True)[:3]
        for theme, v in top_themes:
            if v > 5:
                evidence.append(f"Theme: {theme.replace('_', ' ')} velocity={v:.0f}")

        return evidence

    def _cache_result(self, result: Dict[str, Any]):
        """Cache for historical analysis."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"narrative_{ts}.json"
        try:
            cache_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            files = sorted(self.cache_dir.glob("narrative_*.json"))
            for f in files[:-200]:
                f.unlink(missing_ok=True)
        except Exception:
            pass

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

    Uses GDELT TimelineVol mode (volume intensity over time) which is a
    working endpoint. Queries are batched to respect GDELT's 5-second
    rate limit. Falls back to cached data when GDELT is unavailable.
    """

    # GDELT DOC API for real-time article volume
    GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    # How many themes to query GDELT for per poll (to stay under rate limits).
    # With 6s between queries, 4 themes = ~24s per poll cycle.
    MAX_GDELT_THEMES_PER_POLL = 4

    # Seconds between GDELT API calls (GDELT enforces 5s minimum)
    GDELT_RATE_LIMIT_SECONDS = 6

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "narrative_velocity"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Rotation index file to cycle through themes across polls
        self._rotation_file = self.cache_dir / "_rotation_index.json"

    def _get_rotation_offset(self) -> int:
        """Get and increment the rotation offset for theme selection."""
        try:
            data = json.loads(self._rotation_file.read_text())
            offset = int(data.get("offset", 0))
        except Exception:
            offset = 0
        # Save next offset
        try:
            theme_count = len(TRACKED_NARRATIVES)
            next_offset = (offset + self.MAX_GDELT_THEMES_PER_POLL) % theme_count
            self._rotation_file.write_text(json.dumps({"offset": next_offset}))
        except Exception:
            pass
        return offset

    def _load_last_good_cache(self) -> Optional[Dict[str, Any]]:
        """Load the most recent cached result that was fresh."""
        try:
            files = sorted(self.cache_dir.glob("narrative_*.json"), reverse=True)
            for f in files[:10]:
                data = json.loads(f.read_text())
                if data.get("fresh"):
                    return data
        except Exception:
            pass
        return None

    def poll(self, finnhub_headlines: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Calculate narrative velocity across tracked themes.

        Strategy:
        1. Query GDELT TimelineVol for a rotating subset of themes
           (MAX_GDELT_THEMES_PER_POLL per cycle) to respect rate limits.
        2. Merge with any Finnhub headline data.
        3. Merge with cached scores for themes not queried this cycle.
        4. Fall back entirely to cache if GDELT returns nothing.

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

        # Load previous cache for merge
        cached = self._load_last_good_cache()
        cached_velocities = cached.get("theme_velocities", {}) if cached else {}
        cached_counts = cached.get("article_counts", {}) if cached else {}

        # Select which themes to query this cycle (rotating window)
        all_themes = list(TRACKED_NARRATIVES.keys())
        offset = self._get_rotation_offset()
        selected_themes = []
        for i in range(self.MAX_GDELT_THEMES_PER_POLL):
            idx = (offset + i) % len(all_themes)
            selected_themes.append(all_themes[idx])

        # Poll GDELT for selected themes
        theme_scores = {}
        article_counts = {}
        total = 0
        gdelt_success = False

        import time as _time
        for i, theme_name in enumerate(selected_themes):
            theme_config = TRACKED_NARRATIVES[theme_name]
            keywords = theme_config["keywords"]
            weight = theme_config["weight"]

            # Always sleep between GDELT queries (including before the first one)
            # to avoid hitting the 5-second rate limit from prior bridge activity
            if i > 0:
                _time.sleep(self.GDELT_RATE_LIMIT_SECONDS)

            count = self._query_gdelt_volume(keywords)
            if count is not None:
                gdelt_success = True
                article_counts[theme_name] = count
                velocity = count * weight
                theme_scores[theme_name] = round(velocity, 1)
                total += count

        # Merge cached scores for themes we didn't query this cycle
        for theme_name in all_themes:
            if theme_name not in theme_scores and theme_name in cached_velocities:
                theme_scores[theme_name] = cached_velocities[theme_name]
                if theme_name in cached_counts:
                    article_counts[theme_name] = cached_counts[theme_name]
                    total += cached_counts[theme_name]

        # Supplement with Finnhub headlines if provided
        if finnhub_headlines:
            fh_velocity = self._score_finnhub_velocity(finnhub_headlines)
            for theme, v in fh_velocity.items():
                theme_scores[theme] = theme_scores.get(theme, 0) + v

        # Determine freshness: we're fresh if GDELT returned data OR Finnhub contributed
        has_finnhub = bool(finnhub_headlines)
        if theme_scores and (gdelt_success or has_finnhub):
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
        elif cached and cached.get("fresh"):
            # Graceful degradation: use full cache but mark age
            cache_age_s = 0
            try:
                cache_ts = datetime.fromisoformat(cached["timestamp_utc"])
                cache_age_s = (datetime.now(timezone.utc) - cache_ts).total_seconds()
            except Exception:
                pass

            if cache_age_s < 3600:  # Cache less than 1 hour old
                result["theme_velocities"] = cached_velocities
                result["article_counts"] = cached_counts
                result["total_articles"] = cached.get("total_articles", 0)
                result["velocity_score"] = cached.get("velocity_score", 0)
                result["dominant_narrative"] = cached.get("dominant_narrative")
                result["infection_rate"] = cached.get("infection_rate", 0)
                result["fresh"] = True  # Still usable
                result["evidence"] = [
                    f"Using cached data ({int(cache_age_s)}s old) — GDELT rate-limited"
                ] + cached.get("evidence", [])

        # Cache
        self._cache_result(result)

        return result

    def _query_gdelt_volume(self, keywords: List[str], timespan: str = "24h") -> Optional[float]:
        """
        Query GDELT DOC API using TimelineVol mode for volume intensity.

        Returns the average volume intensity over the last hour (last 4
        fifteen-minute buckets), or None on failure.

        Note: GDELT TimelineVol returns relative volume intensity (not raw
        article counts), but it correlates with narrative attention.
        """
        # Build query: OR of top keywords
        query = " OR ".join(f'"{kw}"' for kw in keywords[:4])
        params = {
            "query": query,
            "mode": "TimelineVol",
            "timespan": timespan,
            "format": "json",
        }

        url = f"{self.GDELT_DOC_URL}?{urllib.parse.urlencode(params)}"
        data = safe_get_json(url, timeout=15)

        if data is None:
            return None

        # Check for rate-limit text response (GDELT returns HTML/text, not JSON)
        if isinstance(data, str) and "limit" in data.lower():
            return None

        if not isinstance(data, dict):
            return None

        # Parse TimelineVol response:
        # {"query_details": {...}, "timeline": [{"series": "Volume Intensity", "data": [...]}]}
        timeline = data.get("timeline", [])
        if not timeline or not isinstance(timeline, list):
            return None

        series = timeline[0] if timeline else {}
        datapoints = series.get("data", [])
        if not datapoints:
            return None

        # Sum the last 4 time buckets (approximately last hour at 15min resolution)
        recent = datapoints[-4:] if len(datapoints) >= 4 else datapoints
        total_volume = 0.0
        for entry in recent:
            if isinstance(entry, dict):
                val = entry.get("value", 0)
                try:
                    total_volume += float(val)
                except (ValueError, TypeError):
                    pass

        return round(total_volume, 2)

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

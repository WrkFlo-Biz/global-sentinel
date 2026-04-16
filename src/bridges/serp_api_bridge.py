#!/usr/bin/env python3
"""
Global Sentinel V5.3 - SerpAPI Search Bridge

Fallback/supplement web search bridge using SerpAPI (Google Search).
Designed as a tier_2 fallback to the Exa AI bridge, sharing the same
query categories and emitting compatible normalized packets.

Uses urllib.request (no external deps). Rate-limited to 100 requests/hour.

Emits normalized search_event packets for downstream scoring and signal boost.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.bridges.base_bridge import BaseBridge, utc_now_iso

# ---------------------------------------------------------------------------
# Shared query categories — mirrors exa_search_bridge.SEARCH_QUERIES
# ---------------------------------------------------------------------------
SEARCH_QUERIES = {
    "geopolitical_disruption": {
        "query": "breaking geopolitical crisis war sanctions military conflict market impact",
        "num_results": 5,
        "weight": 1.0,
    },
    "oil_supply_shock": {
        "query": "oil supply disruption OPEC production cut crude price spike pipeline attack Brent WTI",
        "num_results": 5,
        "weight": 0.9,
    },
    "hormuz_chokepoint": {
        "query": "Strait of Hormuz Iran blockade tanker seizure Persian Gulf oil disruption naval",
        "num_results": 5,
        "weight": 1.0,
    },
    "food_agriculture_crisis": {
        "query": "food price crisis grain shortage fertilizer cost wheat corn soybean export ban famine",
        "num_results": 4,
        "weight": 0.8,
    },
    "electricity_grid_crisis": {
        "query": "electricity price spike power grid crisis blackout natural gas power shortage utility",
        "num_results": 4,
        "weight": 0.75,
    },
    "central_bank_policy": {
        "query": "Federal Reserve interest rate decision ECB BOJ central bank policy surprise",
        "num_results": 5,
        "weight": 0.9,
    },
    "trade_tariff_sanctions": {
        "query": "trade war tariff sanctions export ban import restriction economic retaliation",
        "num_results": 5,
        "weight": 0.85,
    },
    "market_crash_volatility": {
        "query": "stock market crash sell-off volatility spike circuit breaker flash crash",
        "num_results": 5,
        "weight": 0.95,
    },
    "supply_chain_disruption": {
        "query": "supply chain disruption shipping crisis port congestion semiconductor shortage",
        "num_results": 4,
        "weight": 0.8,
    },
    "aviation_travel_disruption": {
        "query": "airline disruption airspace closure flight cancellation travel ban aviation crisis",
        "num_results": 4,
        "weight": 0.7,
    },
    "energy_infrastructure": {
        "query": "energy crisis natural gas shortage refinery outage power grid failure LNG",
        "num_results": 4,
        "weight": 0.8,
    },
    "cyber_attack_infrastructure": {
        "query": "cyberattack critical infrastructure ransomware financial system hack",
        "num_results": 3,
        "weight": 0.75,
    },
    "ai_technology_disruption": {
        "query": "AI breakthrough technology disruption regulation antitrust big tech",
        "num_results": 3,
        "weight": 0.6,
    },
    "fed_monetary_policy": {
        "query": "FOMC minutes Federal Reserve speech testimony Powell rate guidance forward",
        "num_results": 4,
        "weight": 0.9,
    },
    "treasury_yields_bonds": {
        "query": "treasury yield curve inversion bond market sell-off 10-year yield spike 2-year spread",
        "num_results": 4,
        "weight": 0.85,
    },
    "inflation_cpi_pce": {
        "query": "CPI inflation surprise PCE price index core inflation consumer prices accelerating",
        "num_results": 4,
        "weight": 0.9,
    },
    "labor_employment": {
        "query": "jobs report nonfarm payrolls unemployment claims layoffs labor market tight",
        "num_results": 3,
        "weight": 0.8,
    },
    "credit_spreads_default": {
        "query": "credit spread widening high yield default corporate bond distress bankruptcy",
        "num_results": 3,
        "weight": 0.85,
    },
    "congressional_insider_trading": {
        "query": "congressional stock trading insider politician portfolio disclosure unusual trades",
        "num_results": 3,
        "weight": 0.7,
    },
    "emerging_markets_crisis": {
        "query": "emerging market crisis capital flight currency collapse EM debt default contagion",
        "num_results": 3,
        "weight": 0.75,
    },
    "china_economy_trade": {
        "query": "China economy slowdown trade war tariff retaliation yuan devaluation property crisis",
        "num_results": 4,
        "weight": 0.8,
    },
    "shipping_chokepoint_global": {
        "query": "Red Sea Suez Canal Bab el-Mandeb shipping disruption freight rates Houthi attack container",
        "num_results": 4,
        "weight": 0.85,
    },
    "nuclear_uranium_energy": {
        "query": "nuclear energy uranium enrichment IAEA Iran nuclear deal reactor plant",
        "num_results": 3,
        "weight": 0.7,
    },
    "insurance_catastrophe_risk": {
        "query": "insurance catastrophe loss reinsurance war risk premium natural disaster hurricane",
        "num_results": 3,
        "weight": 0.65,
    },
    "petrochemical_feedstock": {
        "query": "petrochemical feedstock cost ethylene naphtha chemical plant shutdown plastic prices",
        "num_results": 3,
        "weight": 0.65,
    },
    "defense_military_spending": {
        "query": "defense spending military contract NATO arms deal weapons procurement budget",
        "num_results": 3,
        "weight": 0.7,
    },
    "semiconductor_chip_war": {
        "query": "semiconductor chip shortage export ban CHIPS Act TSMC Intel NVIDIA AI chip restriction",
        "num_results": 3,
        "weight": 0.75,
    },
    "global_recession_signal": {
        "query": "global recession GDP contraction economic downturn PMI manufacturing decline",
        "num_results": 4,
        "weight": 0.85,
    },
}

# Keywords that signal high market impact when found in result text
HIGH_IMPACT_KEYWORDS = {
    "war", "invasion", "sanctions", "embargo", "nuclear", "missile",
    "crash", "collapse", "default", "bankruptcy", "circuit breaker",
    "emergency", "crisis", "catastrophe", "shutdown", "attack",
    "rate hike", "rate cut", "FOMC", "inflation surprise",
    "supply shock", "oil spike", "pipeline explosion",
    "tariff", "trade war", "retaliation", "export ban",
}

# API key must be set via SERP_API_KEY env var (no hardcoded fallback)
_DEFAULT_SERP_API_KEY = ""

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


# ---------------------------------------------------------------------------
# Simple token-bucket rate limiter (100 requests / hour)
# ---------------------------------------------------------------------------
class _RateLimiter:
    """In-process sliding-window rate limiter."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 3600):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: List[float] = []

    def acquire(self) -> bool:
        """Return True if a request is allowed, False if rate-limited."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self.max_requests:
            return False
        self._timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        return max(0, self.max_requests - len(self._timestamps))


class SerpApiSearchBridge(BaseBridge):
    """Polls SerpAPI Google Search for real-time market-moving news.

    Acts as a tier_2 fallback/supplement to the Exa AI bridge.
    Uses urllib.request — no external dependencies required.
    """

    source = "serp_api_search"
    source_tier = "tier_2"
    trust_weight = 0.7
    freshness_ttl_minutes = 60

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(repo_root=repo_root, config=config)
        self.api_key = os.getenv("SERP_API_KEY", _DEFAULT_SERP_API_KEY)
        self.cache_dir = self.repo_root / "logs" / "bridge_cache" / "serp_api"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.seen_hashes_file = self.cache_dir / "seen_hashes.json"
        self.seen_hashes: Dict[str, str] = self._load_seen_hashes()
        self._rate_limiter = _RateLimiter(max_requests=100, window_seconds=3600)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self) -> Dict[str, Any]:
        """Run all query categories and return a normalized bridge payload.

        Compatible with BaseBridge.fetch() contract.
        """
        try:
            packets = self._poll_all_categories()
            high_severity = sum(1 for p in packets if p.get("severity") == "high")
            payload = {
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": utc_now_iso(),
                "fresh": True,
                "packet_count": len(packets),
                "high_severity_count": high_severity,
                "categories_polled": list(SEARCH_QUERIES.keys()),
                "packets": packets,
                "rate_limiter_remaining": self._rate_limiter.remaining,
                "data": packets,
                "error": None,
            }
            self._save_seen_hashes()
            self._cache_results(packets)
            return self._mark_success(payload)
        except Exception as exc:
            return self._mark_failure(exc)

    def search(self, query: str, num: int = 5) -> List[Dict[str, Any]]:
        """Ad-hoc search — returns a list of normalized result dicts.

        Useful for one-off or programmatic queries outside the
        standard category sweep.
        """
        raw = self._serpapi_request(query, num=num)
        if raw is None:
            return []
        return self._parse_organic_results(raw, category="ad_hoc", weight=0.5)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _poll_all_categories(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        for category, cfg in SEARCH_QUERIES.items():
            try:
                raw = self._serpapi_request(
                    cfg["query"], num=cfg.get("num_results", 5)
                )
                if raw is not None:
                    results = self._parse_organic_results(
                        raw, category=category, weight=cfg.get("weight", 1.0)
                    )
                    packets.extend(results)
            except Exception as exc:
                print(
                    f"[SerpApiSearchBridge] Error in {category}: {exc}",
                    file=sys.stderr,
                )
                packets.append(self._error_packet(category, str(exc)))
        return packets

    def _serpapi_request(
        self, query: str, num: int = 5
    ) -> Optional[Dict[str, Any]]:
        """Make an HTTP GET to SerpAPI. Returns parsed JSON or None."""
        if not self._rate_limiter.acquire():
            print(
                "[SerpApiSearchBridge] Rate limit reached (100/hr). Skipping.",
                file=sys.stderr,
            )
            return None

        params = urllib.parse.urlencode({
            "q": query,
            "api_key": self.api_key,
            "engine": "google",
            "num": num,
        })
        url = f"{SERPAPI_ENDPOINT}?{params}"

        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            print(
                f"[SerpApiSearchBridge] HTTP {exc.code}: {exc.reason}",
                file=sys.stderr,
            )
            return None
        except Exception as exc:
            print(
                f"[SerpApiSearchBridge] Request failed: {exc}",
                file=sys.stderr,
            )
            return None

    def _parse_organic_results(
        self,
        raw: Dict[str, Any],
        category: str,
        weight: float,
    ) -> List[Dict[str, Any]]:
        """Parse SerpAPI organic_results into normalized packets."""
        organic = raw.get("organic_results", [])
        packets: List[Dict[str, Any]] = []

        for item in organic:
            link = item.get("link", "")
            title = item.get("title", "Untitled")
            snippet = item.get("snippet", "")

            content_hash = hashlib.sha256(
                (link + title).encode()
            ).hexdigest()[:16]

            if content_hash in self.seen_hashes:
                continue
            self.seen_hashes[content_hash] = utc_now_iso()

            # Score for market impact
            combined = (title + " " + snippet).lower()
            impact_hits = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in combined)
            impact_score = min(impact_hits / 5.0, 1.0)

            if impact_score >= 0.6:
                severity = "high"
            elif impact_score >= 0.3:
                severity = "medium"
            else:
                severity = "low"

            packets.append({
                "schema_version": "serp_search_event.v1",
                "event_id": f"serp-{category}-{content_hash}",
                "timestamp_utc": utc_now_iso(),
                "source": self.source,
                "source_url": link,
                "source_tier": self.source_tier,
                "confidence_weight": self.trust_weight,
                "headline": title,
                "summary": snippet[:300],
                "published_date": item.get("date"),
                "category": category,
                "severity": severity,
                "parsing_meta": {
                    "search_category": category,
                    "impact_score": round(impact_score, 3),
                    "impact_keyword_count": impact_hits,
                    "category_weight": weight,
                    "search_engine": "google",
                    "position": item.get("position"),
                },
                "rate_regime_shock_candidate": impact_score >= 0.5,
            })

        return packets

    def _error_packet(self, category: str, error: str) -> Dict[str, Any]:
        return {
            "schema_version": "serp_search_event.v1",
            "event_id": f"serp-error-{category}",
            "timestamp_utc": utc_now_iso(),
            "source": self.source,
            "source_tier": self.source_tier,
            "category": category,
            "error": error,
            "severity": "none",
            "parsing_meta": {"error": True},
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_seen_hashes(self) -> Dict[str, str]:
        try:
            if self.seen_hashes_file.exists():
                data = json.loads(
                    self.seen_hashes_file.read_text(encoding="utf-8")
                )
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=48)
                ).isoformat()
                return {k: v for k, v in data.items() if v > cutoff}
        except Exception:
            pass
        return {}

    def _save_seen_hashes(self):
        try:
            self.seen_hashes_file.write_text(
                json.dumps(self.seen_hashes, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _cache_results(self, packets: List[Dict[str, Any]]):
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"serp_search_{tag}.json"
        try:
            cache_file.write_text(
                json.dumps(packets, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Snapshot helper (matches exa bridge API)
    # ------------------------------------------------------------------

    def build_snapshot_section(self) -> Dict[str, Any]:
        """Returns the canonical snapshot['serp_api_search'] dict."""
        result = self.fetch()
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    p = argparse.ArgumentParser(
        description="SerpAPI search bridge for Global Sentinel"
    )
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None)
    p.add_argument(
        "--search", default=None, help="Ad-hoc search query instead of full sweep"
    )
    p.add_argument("--num", type=int, default=5, help="Number of results for ad-hoc search")
    args = p.parse_args()

    bridge = SerpApiSearchBridge(repo_root=Path(args.repo_root).resolve())

    if args.search:
        results = bridge.search(args.search, num=args.num)
        output = {"query": args.search, "results": results}
    else:
        output = bridge.build_snapshot_section()

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

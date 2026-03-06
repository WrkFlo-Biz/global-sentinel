#!/usr/bin/env python3
"""
Global Sentinel V5.3 - Exa AI Search Bridge

Real-time AI-powered news and disruption search using Exa's search API.
Polls for market-moving events, geopolitical disruptions, supply shocks,
and macro developments that affect stocks and markets.

Requires: EXA_API_KEY environment variable (from https://dashboard.exa.ai)
Install: pip install exa-py

Emits normalized search_event packets for downstream scoring and signal boost.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Search queries organized by category — each targets a specific risk vector
SEARCH_QUERIES = {
    "geopolitical_disruption": {
        "query": "breaking geopolitical crisis war sanctions military conflict market impact",
        "category": "news",
        "num_results": 5,
        "weight": 1.0,
    },
    "oil_supply_shock": {
        "query": "oil supply disruption OPEC production cut crude price spike pipeline attack",
        "category": "news",
        "num_results": 5,
        "weight": 0.9,
    },
    "central_bank_policy": {
        "query": "Federal Reserve interest rate decision ECB BOJ central bank policy surprise",
        "category": "news",
        "num_results": 5,
        "weight": 0.9,
    },
    "trade_tariff_sanctions": {
        "query": "trade war tariff sanctions export ban import restriction economic retaliation",
        "category": "news",
        "num_results": 5,
        "weight": 0.85,
    },
    "market_crash_volatility": {
        "query": "stock market crash sell-off volatility spike circuit breaker flash crash",
        "category": "news",
        "num_results": 5,
        "weight": 0.95,
    },
    "supply_chain_disruption": {
        "query": "supply chain disruption shipping crisis port congestion semiconductor shortage",
        "category": "news",
        "num_results": 4,
        "weight": 0.8,
    },
    "aviation_travel_disruption": {
        "query": "airline disruption airspace closure flight cancellation travel ban aviation crisis",
        "category": "news",
        "num_results": 4,
        "weight": 0.7,
    },
    "energy_infrastructure": {
        "query": "energy crisis natural gas shortage refinery outage power grid failure LNG",
        "category": "news",
        "num_results": 4,
        "weight": 0.8,
    },
    "cyber_attack_infrastructure": {
        "query": "cyberattack critical infrastructure ransomware financial system hack",
        "category": "news",
        "num_results": 3,
        "weight": 0.75,
    },
    "ai_technology_disruption": {
        "query": "AI breakthrough technology disruption regulation antitrust big tech",
        "category": "news",
        "num_results": 3,
        "weight": 0.6,
    },
}

# Keywords that signal high market impact when found in article text
HIGH_IMPACT_KEYWORDS = {
    "war", "invasion", "sanctions", "embargo", "nuclear", "missile",
    "crash", "collapse", "default", "bankruptcy", "circuit breaker",
    "emergency", "crisis", "catastrophe", "shutdown", "attack",
    "rate hike", "rate cut", "FOMC", "inflation surprise",
    "supply shock", "oil spike", "pipeline explosion",
    "tariff", "trade war", "retaliation", "export ban",
}


class ExaSearchBridge:
    """Polls Exa AI search for real-time market-moving news and disruptions."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.api_key = os.getenv("EXA_API_KEY", "3d14b3ef-1a0b-4809-b77d-d02a38e6a339")
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "exa_search"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.seen_hashes_file = self.cache_dir / "seen_hashes.json"
        self.seen_hashes = self._load_seen_hashes()
        self.exa_client = None

    def _init_client(self):
        """Lazy-init the Exa client."""
        if self.exa_client is not None:
            return True
        if not self.api_key:
            return False
        try:
            from exa_py import Exa
            self.exa_client = Exa(api_key=self.api_key)
            return True
        except ImportError:
            print("[ExaSearchBridge] exa-py not installed. Run: pip install exa-py", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[ExaSearchBridge] Failed to init Exa client: {e}", file=sys.stderr)
            return False

    def poll(self) -> List[Dict[str, Any]]:
        """Poll all search categories and return normalized event packets."""
        if not self._init_client():
            return []

        packets: List[Dict[str, Any]] = []
        lookback = datetime.now(timezone.utc) - timedelta(hours=6)

        for category, config in SEARCH_QUERIES.items():
            try:
                results = self._search_category(category, config, lookback)
                packets.extend(results)
            except Exception as e:
                print(f"[ExaSearchBridge] Error in {category}: {e}", file=sys.stderr)
                packets.append(self._error_packet(category, str(e)))

        # Save seen hashes and cache
        self._save_seen_hashes()
        self._cache_results(packets)
        return packets

    def _search_category(
        self, category: str, config: Dict[str, Any], lookback: datetime
    ) -> List[Dict[str, Any]]:
        """Search one category and return normalized packets."""
        results = self.exa_client.search(
            query=config["query"],
            num_results=config.get("num_results", 5),
            type="auto",
            category=config.get("category", "news"),
            start_published_date=lookback.strftime("%Y-%m-%dT%H:%M:%SZ"),
            contents={"text": {"max_characters": 500}},
        )

        packets = []
        for result in results.results:
            content_hash = hashlib.sha256(
                (result.url + (result.title or "")).encode()
            ).hexdigest()[:16]

            if content_hash in self.seen_hashes:
                continue
            self.seen_hashes[content_hash] = iso_now()

            # Score the article for market impact
            text = (result.text or "").lower()
            title = (result.title or "").lower()
            combined = text + " " + title
            impact_hits = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in combined)
            impact_score = min(impact_hits / 5.0, 1.0)

            # Determine severity
            if impact_score >= 0.6:
                severity = "high"
            elif impact_score >= 0.3:
                severity = "medium"
            else:
                severity = "low"

            packets.append({
                "schema_version": "exa_search_event.v1",
                "event_id": f"exa-{category}-{content_hash}",
                "timestamp_utc": iso_now(),
                "source": "exa_ai_search",
                "source_url": result.url,
                "source_tier": "tier_c_osint_alt",
                "confidence_weight": 0.65,
                "headline": result.title or "Untitled",
                "summary": (result.text or "")[:300],
                "published_date": result.published_date if hasattr(result, 'published_date') else None,
                "category": category,
                "severity": severity,
                "parsing_meta": {
                    "search_category": category,
                    "impact_score": round(impact_score, 3),
                    "impact_keyword_count": impact_hits,
                    "category_weight": config.get("weight", 1.0),
                    "exa_search_type": "auto",
                },
                "rate_regime_shock_candidate": impact_score >= 0.5,
            })

        return packets

    def _error_packet(self, category: str, error: str) -> Dict[str, Any]:
        return {
            "schema_version": "exa_search_event.v1",
            "event_id": f"exa-error-{category}",
            "timestamp_utc": iso_now(),
            "source": "exa_ai_search",
            "category": category,
            "error": error,
            "severity": "none",
            "parsing_meta": {"error": True},
        }

    def _load_seen_hashes(self) -> Dict[str, str]:
        try:
            if self.seen_hashes_file.exists():
                data = json.loads(self.seen_hashes_file.read_text(encoding="utf-8"))
                # Prune entries older than 48h
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
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
        cache_file = self.cache_dir / f"exa_search_{tag}.json"
        try:
            cache_file.write_text(json.dumps(packets, indent=2), encoding="utf-8")
        except Exception:
            pass

    def build_snapshot_section(self) -> Dict[str, Any]:
        """Returns the canonical snapshot['exa_search'] dict."""
        packets = self.poll()
        high_severity = sum(1 for p in packets if p.get("severity") == "high")
        return {
            "timestamp_utc": iso_now(),
            "packet_count": len(packets),
            "high_severity_count": high_severity,
            "categories_polled": list(SEARCH_QUERIES.keys()),
            "packets": packets,
            "fresh": len(packets) > 0 or self.exa_client is not None,
        }


# --- CLI ---
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None)
    args = p.parse_args()

    bridge = ExaSearchBridge(Path(args.repo_root).resolve())
    snapshot = bridge.build_snapshot_section()

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    else:
        print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()

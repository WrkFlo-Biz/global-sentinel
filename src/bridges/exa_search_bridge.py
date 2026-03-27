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
import time
import json
import os
import sys
import urllib.error
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


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


CACHE_TTL_HOURS = 4
CATEGORIES_PER_CYCLE = 9
SERP_COOLDOWN_SECONDS = 5

# Search queries organized by category — each targets a specific risk vector
SEARCH_QUERIES = {
    "geopolitical_disruption": {
        "priority": 1,
        "query": "breaking geopolitical crisis war sanctions military conflict market impact",
        "category": "news",
        "num_results": 5,
        "weight": 1.0,
    },
    "oil_supply_shock": {
        "priority": 1,
        "query": "oil supply disruption OPEC production cut crude price spike pipeline attack Brent WTI",
        "category": "news",
        "num_results": 5,
        "weight": 0.9,
    },
    "hormuz_chokepoint": {
        "priority": 1,
        "query": "Strait of Hormuz Iran blockade tanker seizure Persian Gulf oil disruption naval",
        "category": "news",
        "num_results": 5,
        "weight": 1.0,
    },
    "food_agriculture_crisis": {
        "priority": 2,
        "query": "food price crisis grain shortage fertilizer cost wheat corn soybean export ban famine",
        "category": "news",
        "num_results": 4,
        "weight": 0.8,
    },
    "electricity_grid_crisis": {
        "priority": 3,
        "query": "electricity price spike power grid crisis blackout natural gas power shortage utility",
        "category": "news",
        "num_results": 4,
        "weight": 0.75,
    },
    "central_bank_policy": {
        "priority": 1,
        "query": "Federal Reserve interest rate decision ECB BOJ central bank policy surprise",
        "category": "news",
        "num_results": 5,
        "weight": 0.9,
    },
    "trade_tariff_sanctions": {
        "priority": 1,
        "query": "trade war tariff sanctions export ban import restriction economic retaliation",
        "category": "news",
        "num_results": 5,
        "weight": 0.85,
    },
    "market_crash_volatility": {
        "priority": 1,
        "query": "stock market crash sell-off volatility spike circuit breaker flash crash",
        "category": "news",
        "num_results": 5,
        "weight": 0.95,
    },
    "supply_chain_disruption": {
        "priority": 2,
        "query": "supply chain disruption shipping crisis port congestion semiconductor shortage",
        "category": "news",
        "num_results": 4,
        "weight": 0.8,
    },
    "aviation_travel_disruption": {
        "priority": 3,
        "query": "airline disruption airspace closure flight cancellation travel ban aviation crisis",
        "category": "news",
        "num_results": 4,
        "weight": 0.7,
    },
    "energy_infrastructure": {
        "priority": 2,
        "query": "energy crisis natural gas shortage refinery outage power grid failure LNG",
        "category": "news",
        "num_results": 4,
        "weight": 0.8,
    },
    "cyber_attack_infrastructure": {
        "priority": 2,
        "query": "cyberattack critical infrastructure ransomware financial system hack",
        "category": "news",
        "num_results": 3,
        "weight": 0.75,
    },
    "ai_technology_disruption": {
        "priority": 3,
        "query": "AI breakthrough technology disruption regulation antitrust big tech",
        "category": "news",
        "num_results": 3,
        "weight": 0.6,
    },
    # --- Matching existing bridge data sources ---
    "fed_monetary_policy": {
        "priority": 1,
        "query": "FOMC minutes Federal Reserve speech testimony Powell rate guidance forward",
        "category": "news",
        "num_results": 4,
        "weight": 0.9,
    },
    "treasury_yields_bonds": {
        "priority": 2,
        "query": "treasury yield curve inversion bond market sell-off 10-year yield spike 2-year spread",
        "category": "news",
        "num_results": 4,
        "weight": 0.85,
    },
    "inflation_cpi_pce": {
        "priority": 1,
        "query": "CPI inflation surprise PCE price index core inflation consumer prices accelerating",
        "category": "news",
        "num_results": 4,
        "weight": 0.9,
    },
    "labor_employment": {
        "priority": 2,
        "query": "jobs report nonfarm payrolls unemployment claims layoffs labor market tight",
        "category": "news",
        "num_results": 3,
        "weight": 0.8,
    },
    "credit_spreads_default": {
        "priority": 2,
        "query": "credit spread widening high yield default corporate bond distress bankruptcy",
        "category": "news",
        "num_results": 3,
        "weight": 0.85,
    },
    "congressional_insider_trading": {
        "priority": 3,
        "query": "congressional stock trading insider politician portfolio disclosure unusual trades",
        "category": "news",
        "num_results": 3,
        "weight": 0.7,
    },
    "emerging_markets_crisis": {
        "priority": 2,
        "query": "emerging market crisis capital flight currency collapse EM debt default contagion",
        "category": "news",
        "num_results": 3,
        "weight": 0.75,
    },
    "china_economy_trade": {
        "priority": 1,
        "query": "China economy slowdown trade war tariff retaliation yuan devaluation property crisis",
        "category": "news",
        "num_results": 4,
        "weight": 0.8,
    },
    "shipping_chokepoint_global": {
        "priority": 2,
        "query": "Red Sea Suez Canal Bab el-Mandeb shipping disruption freight rates Houthi attack container",
        "category": "news",
        "num_results": 4,
        "weight": 0.85,
    },
    "nuclear_uranium_energy": {
        "priority": 3,
        "query": "nuclear energy uranium enrichment IAEA Iran nuclear deal reactor plant",
        "category": "news",
        "num_results": 3,
        "weight": 0.7,
    },
    "insurance_catastrophe_risk": {
        "priority": 3,
        "query": "insurance catastrophe loss reinsurance war risk premium natural disaster hurricane",
        "category": "news",
        "num_results": 3,
        "weight": 0.65,
    },
    "petrochemical_feedstock": {
        "priority": 3,
        "query": "petrochemical feedstock cost ethylene naphtha chemical plant shutdown plastic prices",
        "category": "news",
        "num_results": 3,
        "weight": 0.65,
    },
    "defense_military_spending": {
        "priority": 3,
        "query": "defense spending military contract NATO arms deal weapons procurement budget",
        "category": "news",
        "num_results": 3,
        "weight": 0.7,
    },
    "semiconductor_chip_war": {
        "priority": 2,
        "query": "semiconductor chip shortage export ban CHIPS Act TSMC Intel NVIDIA AI chip restriction",
        "category": "news",
        "num_results": 3,
        "weight": 0.75,
    },
    "global_recession_signal": {
        "priority": 1,
        "query": "global recession GDP contraction economic downturn PMI manufacturing decline",
        "category": "news",
        "num_results": 4,
        "weight": 0.85,
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
        self.api_key = os.getenv("EXA_API_KEY", "")
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "exa_search"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.seen_hashes_file = self.cache_dir / "seen_hashes.json"
        self.seen_hashes = self._load_seen_hashes()
        self.exa_client = None
        self._exa_credits_exhausted = False
        self._category_cache_file = self.cache_dir / "category_cache.json"
        self._category_cache = self._load_category_cache()
        self._rotation_file = self.cache_dir / "rotation_index.json"
        self._last_serp_call = 0.0

    def _init_client(self):
        """Lazy-init the Exa client."""
        if self.exa_client is not None:
            return True
        if not self.api_key or self._exa_credits_exhausted:
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


    def _get_categories_for_this_cycle(self):
        all_cats = list(SEARCH_QUERIES.keys())
        p1 = [c for c in all_cats if SEARCH_QUERIES[c].get("priority", 3) == 1]
        others = [c for c in all_cats if SEARCH_QUERIES[c].get("priority", 3) > 1]
        rot = 0
        try:
            if self._rotation_file.exists(): rot = json.loads(self._rotation_file.read_text())
        except Exception: pass
        slots = max(0, CATEGORIES_PER_CYCLE - len(p1))
        sel = []
        if others and slots > 0:
            s = rot % len(others)
            sel = (others[s:] + others[:s])[:slots]
        try: self._rotation_file.write_text(json.dumps((rot + slots) % max(len(others), 1)))
        except Exception: pass
        return p1 + sel

    def _load_category_cache(self):
        try:
            if self._category_cache_file.exists():
                return json.loads(self._category_cache_file.read_text(encoding="utf-8"))
        except Exception: pass
        return {}

    def _save_category_cache(self):
        try: self._category_cache_file.write_text(json.dumps(self._category_cache, indent=2), encoding="utf-8")
        except Exception: pass

    def _is_category_fresh(self, category):
        entry = self._category_cache.get(category)
        if not entry: return False
        ca = entry.get("cached_at", "")
        if not ca: return False
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(ca)) < timedelta(hours=CACHE_TTL_HOURS)
        except Exception: return False

    def _get_cached_packets(self, category):
        return self._category_cache.get(category, {}).get("packets", [])

    def _cache_category_results(self, category, packets):
        self._category_cache[category] = {"cached_at": iso_now(), "packets": packets, "count": len(packets)}

    def _gdelt_fallback(self, category, config):
        try:
            import urllib.request as ur, urllib.parse as up
            q = config.get("query", category.replace("_", " "))
            params = up.urlencode({"query": q[:100], "mode": "ArtList", "maxrecords": "5", "format": "json", "timespan": "6h"})
            req = ur.Request(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}", headers={"User-Agent": "GS/6"})
            with ur.urlopen(req, timeout=15) as resp: data=json.loads(resp.read().decode("utf-8",errors="ignore"))
            pkts=[]
            for item in (data.get("articles") or [])[:5]:
                t,u=item.get("title",""),item.get("url","")
                h=hashlib.sha256((u+t).encode()).hexdigest()[:16]
                if h in self.seen_hashes: continue
                self.seen_hashes[h]=iso_now()
                hits=sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in t.lower())
                sc=min(hits/5.0,1.0)
                sev="high" if sc>=0.6 else "medium" if sc>=0.3 else "low"
                pkts.append({"schema_version":"exa_search_event.v1","event_id":f"gdelt-{category}-{h}","timestamp_utc":iso_now(),"source":"gdelt_fallback","source_url":u,"source_tier":"tier_c_osint_alt","confidence_weight":0.55,"headline":t,"summary":t[:300],"category":category,"severity":sev,"parsing_meta":{"fallback_source":"gdelt_gkg"}})
            return pkts
        except Exception: return []

    def _google_news_rss_fallback(self, category, config):
        return []  # RSS fallback stub


    def poll(self):
        self._init_client()
        packets = []
        cats = self._get_categories_for_this_cycle()
        lookback = datetime.now(timezone.utc) - timedelta(hours=6)
        cached_n = exa_402 = fb_n = 0
        for category in cats:
            config = SEARCH_QUERIES[category]
            if self._is_category_fresh(category):
                packets.extend(self._get_cached_packets(category))
                cached_n += 1
                continue
            cat_pkts = []
            exa_ok = False
            if not self._exa_credits_exhausted and self.exa_client:
                try:
                    cat_pkts = self._search_category(category, config, lookback)
                    exa_ok = True
                except Exception as e:
                    es = str(e)
                    if "402" in es or "NO_MORE_CREDITS" in es:
                        exa_402 += 1
                        self._exa_credits_exhausted = True
            if not exa_ok:
                for fn in [self._gdelt_fallback, self._google_news_rss_fallback, self._serp_fallback]:
                    try:
                        r = fn(category, config)
                        if r: cat_pkts = r; fb_n += 1; break
                    except Exception: continue
            if cat_pkts:
                packets.extend(cat_pkts)
                self._cache_category_results(category, cat_pkts)
        for cat in set(SEARCH_QUERIES.keys()) - set(cats):
            if self._is_category_fresh(cat):
                packets.extend(self._get_cached_packets(cat))
        self._save_seen_hashes()
        self._save_category_cache()
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

    def _serp_fallback(self, category: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback to SerpAPI when Exa credits are exhausted."""
        import urllib.request
        import urllib.parse
        elapsed = time.monotonic() - self._last_serp_call
        if elapsed < SERP_COOLDOWN_SECONDS: time.sleep(SERP_COOLDOWN_SECONDS - elapsed)
        self._last_serp_call = time.monotonic()
        serp_key = os.getenv("SERP_API_KEY", "")
        if not serp_key:
            return []
        query = config.get("query", category.replace("_", " "))
        params = urllib.parse.urlencode({
            "q": query,
            "api_key": serp_key,
            "engine": "google",
            "num": config.get("num_results", 5),
            "tbm": "nws",  # news search
        })
        url = f"https://serpapi.com/search.json?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/6.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return []
        packets = []
        for item in data.get("news_results", data.get("organic_results", []))[:5]:
            title = item.get("title", "")
            snippet = item.get("snippet", item.get("description", ""))
            link = item.get("link", "")
            content_hash = hashlib.sha256((link + title).encode()).hexdigest()[:16]
            if content_hash in self.seen_hashes:
                continue
            self.seen_hashes[content_hash] = iso_now()
            combined = (title + " " + snippet).lower()
            impact_hits = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in combined)
            impact_score = min(impact_hits / 5.0, 1.0)
            packets.append({
                "schema_version": "exa_search_event.v1",
                "event_id": f"serp-{content_hash}",
                "timestamp_utc": iso_now(),
                "source": "serp_api_fallback",
                "source_tier": "tier_2",
                "category": category,
                "title": title,
                "url": link,
                "text_snippet": snippet[:500],
                "severity": "high" if impact_score >= 0.5 else "medium" if impact_score >= 0.3 else "low",
                "impact_score": round(impact_score, 3),
                "parsing_meta": {
                    "fallback_source": "serpapi",
                    "impact_keyword_count": impact_hits,
                    "category_weight": config.get("weight", 1.0),
                },
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
            "fresh": len(packets) > 0,
            "exa_credits_exhausted": self._exa_credits_exhausted,
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

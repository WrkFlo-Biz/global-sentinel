#!/usr/bin/env python3
"""
Global Sentinel V4.5 - Finnhub Bridge (Skeleton)

Purpose:
- Pull company news + sentiment for configured watchlist symbols
- Emit normalized macro_policy_event packets (tier_c_osint_alt)
- NEVER confirms policy releases alone — non-confirming for official events
- Confidence penalty when used without primary confirmation

Requires FINNHUB_KEY env var (free from https://finnhub.io/)

Shadow / intelligence only:
- No execution logic
- Produces sentiment/headline packets for crisis_monitor triangulation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def safe_lower(x: Any) -> str:
    return str(x or "").lower()


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# Default watchlist symbols for geopolitical/macro relevance
DEFAULT_SYMBOLS = [
    "XOM",    # Exxon (oil/energy)
    "LMT",    # Lockheed Martin (defense)
    "CAT",    # Caterpillar (infrastructure/construction)
    "NVDA",   # NVIDIA (AI infrastructure)
    "MAERSK.CO",  # Maersk (shipping proxy — may need ADR/alternative)
    "KRE",    # Regional banks ETF (rate sensitive)
    "XLF",    # Financials ETF (rate sensitive)
    "SPY",    # Broad market
]


class FinnhubBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.parsing_rules = self.macro_cfg.get("parsing_rules", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.api_key = os.environ.get("FINNHUB_KEY", "")
        self.api_base = "https://finnhub.io/api/v1"

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "finnhub_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load watchlist from assets config or use defaults
        watchlist_cfg = load_yaml(repo_root / "config" / "assets_watchlist.yaml") if (repo_root / "config" / "assets_watchlist.yaml").exists() else {}
        self.symbols = watchlist_cfg.get("finnhub_symbols", DEFAULT_SYMBOLS)

    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        if not self.api_key:
            packets.append(self._error_packet("FINNHUB_KEY not set"))
            return packets

        for symbol in self.symbols:
            try:
                packets.extend(self._poll_symbol_news(symbol))
            except Exception as e:
                packets.append(self._error_packet(f"symbol_error:{symbol}:{e}"))
        return packets

    def _poll_symbol_news(self, symbol: str, max_items: int = 5) -> List[Dict[str, Any]]:
        # Fetch company news from Finnhub
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        params = urllib.parse.urlencode({
            "symbol": symbol,
            "from": today,
            "to": today,
            "token": self.api_key,
        })
        url = f"{self.api_base}/company-news?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinelFinnhubBridge/1.0 (+shadow-mode)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            articles = json.loads(resp.read().decode("utf-8", errors="ignore"))

        if not isinstance(articles, list):
            return []

        packets: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for article in articles[:max_items]:
            article_id = str(article.get("id", ""))
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            headline = str(article.get("headline", "")).strip()
            summary = str(article.get("summary", "")).strip()
            source = str(article.get("source", "")).strip()
            article_url = str(article.get("url", "")).strip()
            article_ts = article.get("datetime")

            published = None
            if article_ts:
                try:
                    published = datetime.fromtimestamp(int(article_ts), tz=timezone.utc).isoformat()
                except Exception:
                    pass

            if not headline:
                continue

            packets.append(self._make_packet(
                symbol=symbol,
                headline=headline,
                summary=summary,
                article_url=article_url,
                article_source=source,
                published_time_utc=published,
                article_id=article_id,
            ))

        return packets

    def _make_packet(
        self,
        symbol: str,
        headline: str,
        summary: str,
        article_url: str,
        article_source: str,
        published_time_utc: Optional[str],
        article_id: str,
    ) -> Dict[str, Any]:
        # Finnhub is Tier C — OSINT/alt, NON-CONFIRMING for policy events
        source_tier = "tier_c_osint_alt"
        source_conf = float((self.source_tiers.get(source_tier) or {}).get("confidence_weight", 0.60))

        # Check for rate-sensitive keywords
        text = f"{safe_lower(headline)} {safe_lower(summary)}"
        rate_sensitive_keywords = [safe_lower(k) for k in (((self.parsing_rules.get("rate_sensitive_topic_keywords") or {}).get("includes_any")) or [])]
        rate_regime = any(k in text for k in rate_sensitive_keywords)

        tags = []
        if rate_regime:
            tags.append("rate_regime_shock_candidate")
        # CRITICAL: Finnhub CANNOT confirm official policy events
        tags.append("osint_alt_source")
        tags.append("cannot_confirm_policy_event")

        event_id_input = f"finnhub|{symbol}|{article_id}|{headline}"
        event_id = f"finnhub-{sha1_hex(event_id_input)[:16]}"

        return {
            "schema_version": "macro_policy_event.v1",
            "event_id": event_id,
            "timestamp_utc": iso_now(),

            "source_domain": "finnhub.io",
            "source_url": article_url,
            "source_feed_url": self.api_base,
            "source_tier": source_tier,
            "source_type": "api",
            "official_source": False,  # Finnhub is NOT official

            "event_type": "macro_calendar_update",
            "headline": f"FINNHUB [{symbol}]: {headline}",
            "summary": summary[:500] if summary else None,
            "published_time_utc": published_time_utc,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime,
            "requires_rate_cross_asset_check": False,  # Tier C cannot force cross-asset checks
            "official_source_confirmed": False,
            "official_policy_confirmation": False,
            "cannot_confirm_policy_event": True,

            "policy_release_urgency_score": 0.30,  # Low urgency — OSINT only
            "source_confidence": source_conf,

            "release_window": {
                "release_key": None,
                "release_time_et_hint": None,
                "pre_buffer_minutes": 0,
                "post_buffer_minutes": 0,
            },

            "parsing_meta": {
                "symbol": symbol,
                "article_id": article_id,
                "article_source": article_source,
                "article_url": article_url,
            },
            "provenance": {
                "bridge": "finnhub_bridge",
                "bridge_version": "0.1.0-skeleton",
                "normalized_from": "api",
                "source_tier": source_tier,
                "raw_source_url": self.api_base,
            },

            "operator_summary": f"finnhub [{symbol}]: {headline[:80]} | conf={source_conf:.2f} | osint_only",
        }

    def _error_packet(self, error: str) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "finnhub.io",
            "source_tier": "tier_c_osint_alt",
            "official_source": False,
            "bridge": "finnhub_bridge",
            "error": error,
        }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel Finnhub Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true")
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--symbols", default="", help="Comma-separated symbols to override config")
    p.add_argument("--loop-seconds", type=int, default=300)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = FinnhubBridge(repo_root)

    if args.symbols:
        bridge.symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.once:
        packets = bridge.poll()
        if args.jsonl:
            for p in packets:
                print(json.dumps(p, ensure_ascii=False))
        else:
            print(json.dumps({"packets": packets, "count": len(packets)}, ensure_ascii=False, indent=2))
        return

    while True:
        packets = bridge.poll()
        if args.jsonl:
            for p in packets:
                print(json.dumps(p, ensure_ascii=False), flush=True)
        else:
            print(json.dumps({"timestamp_utc": iso_now(), "packets": packets, "count": len(packets)}, ensure_ascii=False), flush=True)
        time.sleep(args.loop_seconds)


if __name__ == "__main__":
    main()

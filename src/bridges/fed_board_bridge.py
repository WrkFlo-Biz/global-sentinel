#!/usr/bin/env python3
"""
Global Sentinel V4.5 - Fed Board Bridge (Skeleton)
Polls Federal Reserve RSS feeds + selected pages and emits normalized macro_policy_event packets.

Shadow / intelligence only:
- No execution logic
- No market orders
- Produces normalized event packets for crisis_monitor / macro policy layer ingestion

Planned sources:
- Fed press releases RSS
- Fed speeches RSS
- Fed testimony RSS
- FOMC calendar page (optional page scrape)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)


# -----------------------------
# Utilities
# -----------------------------
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def read_text_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GlobalSentinelFedBridge/1.0 (+shadow-mode)"
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def safe_lower(s: Any) -> str:
    return str(s or "").lower()


# -----------------------------
# Event packet model
# -----------------------------
@dataclass
class MacroPolicyEvent:
    event_id: str
    timestamp_utc: str
    source_domain: str
    source_url: str
    source_tier: str
    source_type: str  # rss / page / api
    official_source: bool

    event_type: str
    headline: str
    summary: Optional[str]
    published_time_utc: Optional[str]

    tags: List[str]
    rate_regime_shock_candidate: bool
    requires_rate_cross_asset_check: bool
    official_source_confirmed: bool

    policy_release_urgency_score: float
    source_confidence: float

    release_window: Dict[str, Any]
    parsing_meta: Dict[str, Any]
    provenance: Dict[str, Any]


# -----------------------------
# Bridge
# -----------------------------
class FedBoardBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.fed_cfg = self.macro_cfg.get("official_sources", {}).get("fed", {})
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "fed_board_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.rss_feeds = self.fed_cfg.get("rss_feeds", [])
        self.parsing_rules = self.macro_cfg.get("parsing_rules", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})

    # -------------------------
    # Public interface
    # -------------------------
    def poll(self) -> List[Dict[str, Any]]:
        """
        Polls configured Fed RSS feeds and returns normalized event packets.
        """
        packets: List[Dict[str, Any]] = []
        for url in self.rss_feeds:
            try:
                feed_packets = self._poll_rss(url)
                packets.extend(feed_packets)
            except Exception as e:
                packets.append(self._error_packet(url, str(e)))
        return packets

    def _poll_rss(self, feed_url: str) -> List[Dict[str, Any]]:
        xml_text = read_text_url(feed_url)
        root = ET.fromstring(xml_text)

        items = []
        # RSS 2.0 typical path
        channel = root.find("channel")
        if channel is not None:
            items = channel.findall("item")
        else:
            # Atom fallback (minimal support)
            items = root.findall("{http://www.w3.org/2005/Atom}entry")

        packets: List[Dict[str, Any]] = []
        for item in items:
            raw = self._extract_rss_item(item)
            if not raw.get("headline") or not raw.get("link"):
                continue

            pkt = self._normalize_raw_item(
                raw=raw,
                source_url=feed_url,
                source_domain="federalreserve.gov",
                source_type="rss"
            )
            if pkt:
                packets.append(pkt)
        return packets

    # -------------------------
    # RSS parsing
    # -------------------------
    def _extract_rss_item(self, item: ET.Element) -> Dict[str, Any]:
        # RSS 2.0 first
        title = item.findtext("title")
        link = item.findtext("link")
        pubdate = item.findtext("pubDate")
        description = item.findtext("description")

        if title or link:
            return {
                "headline": title,
                "link": link,
                "published": pubdate,
                "summary": self._strip_html(description) if description else None
            }

        # Atom fallback
        ns = {"a": "http://www.w3.org/2005/Atom"}
        title_el = item.find("a:title", ns)
        link_el = item.find("a:link", ns)
        updated_el = item.find("a:updated", ns)
        summary_el = item.find("a:summary", ns)

        return {
            "headline": title_el.text if title_el is not None else None,
            "link": link_el.attrib.get("href") if link_el is not None else None,
            "published": updated_el.text if updated_el is not None else None,
            "summary": summary_el.text if summary_el is not None else None
        }

    def _strip_html(self, s: str) -> str:
        return re.sub(r"<[^>]+>", " ", s or "").strip()

    # -------------------------
    # Normalization / classification
    # -------------------------
    def _normalize_raw_item(
        self,
        raw: Dict[str, Any],
        source_url: str,
        source_domain: str,
        source_type: str
    ) -> Optional[Dict[str, Any]]:
        headline = str(raw.get("headline") or "").strip()
        link = str(raw.get("link") or "").strip()
        summary = raw.get("summary")
        published = self._parse_published(raw.get("published"))

        event_type, parsing_meta = self._classify_event_type(headline, summary, link)
        if event_type is None:
            event_type = "macro_calendar_update"
            parsing_meta.setdefault("classification_fallback", True)

        rate_regime = self._is_rate_regime_shock_candidate(headline, summary, event_type)
        requires_cross_asset = bool(rate_regime)

        tags = []
        if rate_regime:
            tags.append("rate_regime_shock_candidate")
            tags.append("requires_rate_cross_asset_check")
        tags.append("official_source_confirmed")

        urgency_score = self._policy_release_urgency_score(event_type)
        source_confidence = self._source_confidence(tier="tier_a_official")

        release_window = self._infer_release_window_metadata(event_type, headline)

        event_id = f"fed-{sha1_hex(link or (headline + (published or '')))[:16]}"

        pkt = {
            "schema_version": "macro_policy_event.v1",
            "event_id": event_id,
            "timestamp_utc": iso_now(),
            "source_domain": source_domain,
            "source_url": link,
            "source_feed_url": source_url,
            "source_tier": "tier_a_official",
            "source_type": source_type,
            "official_source": True,

            "event_type": event_type,
            "headline": headline,
            "summary": summary,
            "published_time_utc": published,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime,
            "requires_rate_cross_asset_check": requires_cross_asset,
            "official_source_confirmed": True,

            "policy_release_urgency_score": urgency_score,
            "source_confidence": source_confidence,

            "release_window": release_window,

            "parsing_meta": parsing_meta,
            "provenance": {
                "bridge": "fed_board_bridge",
                "bridge_version": "0.1.0-skeleton",
                "normalized_from": "rss_item",
                "raw_source_url": source_url
            },

            "operator_summary": self._operator_summary(
                headline=headline,
                event_type=event_type,
                urgency_score=urgency_score,
                rate_regime=rate_regime,
                release_window=release_window
            )
        }
        return pkt

    def _parse_published(self, s: Optional[str]) -> Optional[str]:
        if not s:
            return None

        # Try common RSS pubDate format, then ISO fallback
        candidates = [
            ("%a, %d %b %Y %H:%M:%S %Z", s),
            ("%a, %d %b %Y %H:%M:%S %z", s),
        ]
        for fmt, val in candidates:
            try:
                dt = datetime.strptime(val, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass

        # ISO-ish fallback
        try:
            ss = s.strip()
            if ss.endswith("Z"):
                ss = ss[:-1] + "+00:00"
            dt = datetime.fromisoformat(ss)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return None

    def _classify_event_type(self, headline: str, summary: Optional[str], link: str) -> Tuple[Optional[str], Dict[str, Any]]:
        h = safe_lower(headline)
        s = safe_lower(summary)
        lnk = safe_lower(link)
        text = f"{h} {s} {lnk}"

        keyword_cfg = self.parsing_rules.get("headline_keywords", {})
        for event_type, rule in keyword_cfg.items():
            keywords = [safe_lower(x) for x in rule.get("includes_any", [])]
            matched = [kw for kw in keywords if kw in text]
            if matched:
                return event_type, {"matched_keywords": matched}

        return None, {"matched_keywords": []}

    def _is_rate_regime_shock_candidate(self, headline: str, summary: Optional[str], event_type: str) -> bool:
        h = safe_lower(headline)
        s = safe_lower(summary)
        text = f"{h} {s}"

        if event_type in {"central_bank_statement", "inflation_release", "labor_release"}:
            return True

        kw_cfg = self.parsing_rules.get("rate_sensitive_topic_keywords", {})
        kws = [safe_lower(x) for x in kw_cfg.get("includes_any", [])]
        return any(kw in text for kw in kws)

    def _policy_release_urgency_score(self, event_type: str) -> float:
        defaults = self.scoring_overlays.get("policy_release_urgency_score", {}).get("defaults", {})
        return float(defaults.get(event_type, 0.50))

    def _source_confidence(self, tier: str) -> float:
        tiers = self.macro_cfg.get("source_tiers", {})
        t = tiers.get(tier, {})
        return float(t.get("confidence_weight", 0.5))

    def _infer_release_window_metadata(self, event_type: str, headline: str) -> Dict[str, Any]:
        release_times = self.event_windows.get("release_times_et", {})
        pre = int(self.event_windows.get("pre_release_buffer_minutes_default", 10))
        post = int(self.event_windows.get("post_release_buffer_minutes_default", 20))

        release_key = None
        if event_type == "inflation_release":
            if "pce" in safe_lower(headline):
                release_key = "pce"
            else:
                release_key = "cpi"
        elif event_type == "labor_release":
            release_key = "employment_situation"
        elif event_type == "growth_release":
            if "retail" in safe_lower(headline):
                release_key = "retail_sales"
            elif "gdp" in safe_lower(headline):
                release_key = "gdp"
        elif event_type == "central_bank_statement":
            if "statement" in safe_lower(headline):
                release_key = "fomc_statement"

        return {
            "release_time_et_hint": release_times.get(release_key),
            "pre_buffer_minutes": pre,
            "post_buffer_minutes": post,
            "release_key": release_key
        }

    def _operator_summary(self, headline: str, event_type: str, urgency_score: float, rate_regime: bool, release_window: Dict[str, Any]) -> str:
        parts = [f"{event_type}: {headline}"]
        parts.append(f"urgency={urgency_score:.2f}")
        if rate_regime:
            parts.append("rate_regime_shock_candidate=true")
        if release_window.get("release_time_et_hint"):
            parts.append(f"release_time_et_hint={release_window['release_time_et_hint']}")
        return " | ".join(parts)

    def _error_packet(self, source_url: str, error: str) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_feed_url": source_url,
            "source_domain": "federalreserve.gov",
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "fed_board_bridge",
            "error": error
        }


# -----------------------------
# CLI / stdio behavior
# -----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel Fed Board Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true", help="Poll once and print JSON array")
    p.add_argument("--jsonl", action="store_true", help="Print one JSON packet per line")
    p.add_argument("--loop-seconds", type=int, default=300, help="Polling interval in loop mode")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = FedBoardBridge(repo_root)

    if args.once:
        packets = bridge.poll()
        if args.jsonl:
            for p in packets:
                print(json.dumps(p, ensure_ascii=False))
        else:
            print(json.dumps({"packets": packets, "count": len(packets)}, ensure_ascii=False, indent=2))
        return

    # Loop mode (simple daemon-ish stdout emitter)
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

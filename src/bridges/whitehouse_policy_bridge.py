#!/usr/bin/env python3
"""
Global Sentinel V4.5 - White House Policy Bridge (Skeleton)

Purpose:
- Poll White House Briefings & Statements and Presidential Actions pages
- Emit normalized macro_policy_event packets for:
  - executive_policy_action
  - macro_calendar_update
  - official_release_schedule_change (page change heuristic)

Shadow / intelligence only:
- No execution logic
- Produces macro_policy_event packets for crisis_monitor / macro policy layer
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def read_text_url(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GlobalSentinelWhiteHouseBridge/1.0 (+shadow-mode)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def strip_html(html: str) -> str:
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_page_title(html: str) -> str:
    m = re.search(r"(?is)<title>(.*?)</title>", html)
    return strip_html(m.group(1)) if m else ""


def find_links(html: str, base_url: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for m in re.finditer(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html or ""):
        href = m.group(1).strip()
        txt = strip_html(m.group(2))
        if not href:
            continue
        if href.startswith("/"):
            base = re.match(r"^(https?://[^/]+)", base_url)
            if base:
                href = base.group(1) + href
        elif href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        out.append((href, txt))
    return out


class WhiteHousePolicyBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.wh_cfg = (self.macro_cfg.get("official_sources", {}) or {}).get("white_house", {})
        self.parsing_rules = self.macro_cfg.get("parsing_rules", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "whitehouse_policy_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.pages = self.wh_cfg.get("pages", {}) or {}

    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        for page_name, url in self.pages.items():
            try:
                packets.extend(self._poll_page(page_name, url))
            except Exception as e:
                packets.append(self._error_packet(url, f"page_error:{e}", page_name=page_name))
        self._write_poll_snapshot(packets)
        return packets

    def _write_poll_snapshot(self, packets: List[Dict[str, Any]]) -> None:
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot = {
            "timestamp_utc": iso_now(),
            "bridge_name": "whitehouse_policy_bridge",
            "packet_count": len(packets),
            "page_count": len(self.pages),
            "packets": packets[:50],
        }
        path = self.cache_dir / f"whitehouse_policy_{tag}.json"
        try:
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _poll_page(self, page_name: str, url: str) -> List[Dict[str, Any]]:
        html = read_text_url(url)
        title = find_page_title(html)
        text = strip_html(html)
        links = find_links(html, url)

        packets: List[Dict[str, Any]] = []

        # page change detection
        content_hash = sha1_hex(text[:8000])
        cache_file = self.cache_dir / f"{page_name}_hash.txt"
        prev_hash = cache_file.read_text(encoding="utf-8").strip() if cache_file.exists() else None
        changed = prev_hash is not None and prev_hash != content_hash
        cache_file.write_text(content_hash, encoding="utf-8")

        if changed:
            packets.append(self._make_packet(
                source_url=url,
                source_feed_url=url,
                source_type="page",
                event_type="official_release_schedule_change",
                headline=f"WHITE HOUSE page changed: {page_name}",
                summary=f"Detected content change on White House page '{page_name}'. Re-validate policy triggers.",
                parsing_meta={"page_name": page_name, "page_title": title, "content_hash_changed": True},
                published_time_utc=None
            ))

        # page heartbeat
        page_event_type = "executive_policy_action" if page_name == "presidential_actions" else "macro_calendar_update"
        packets.append(self._make_packet(
            source_url=url,
            source_feed_url=url,
            source_type="page",
            event_type=page_event_type,
            headline=f"WHITE HOUSE page polled: {title or page_name}",
            summary=f"Official White House page '{page_name}' polled successfully.",
            parsing_meta={"page_name": page_name, "page_title": title, "content_hash_changed": changed},
            published_time_utc=None
        ))

        # linked item extraction
        packets.extend(self._extract_item_packets(page_name, url, title, links))
        return packets

    def _extract_item_packets(self, page_name: str, page_url: str, page_title: str, links: List[Tuple[str, str]], max_items: int = 12) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        filtered: List[Tuple[str, str]] = []

        for href, txt in links:
            t = safe_lower(txt)
            h = safe_lower(href)

            if page_name == "presidential_actions":
                if any(k in t for k in ["executive order", "proclamation", "memorandum", "presidential action", "order"]):
                    filtered.append((href, txt))
                elif "/presidential-actions/" in h:
                    filtered.append((href, txt))
            elif page_name == "briefings_statements":
                if any(k in t for k in ["statement", "briefing", "remarks", "fact sheet", "memorandum"]):
                    filtered.append((href, txt))
                elif "/briefings-statements/" in h:
                    filtered.append((href, txt))

        seen: set = set()
        for href, txt in filtered:
            key = (href.strip(), txt.strip())
            if key in seen:
                continue
            seen.add(key)

            event_type, parsing_meta = self._classify_item(page_name, txt, href)
            packets.append(self._make_packet(
                source_url=href,
                source_feed_url=page_url,
                source_type="page_link",
                event_type=event_type,
                headline=f"WHITE HOUSE linked item: {txt or href}",
                summary=f"Discovered item link on White House page '{page_name}'.",
                parsing_meta={
                    "page_name": page_name,
                    "parent_page_title": page_title,
                    "linked_item_href": href,
                    "linked_item_text": txt,
                    **parsing_meta
                },
                published_time_utc=None
            ))
            if len(packets) >= max_items:
                break

        return packets

    def _classify_item(self, page_name: str, headline: str, url: str) -> Tuple[str, Dict[str, Any]]:
        h = safe_lower(headline)
        u = safe_lower(url)
        text = f"{h} {u}"

        # White House pages are mostly policy/news; bias classification
        if page_name == "presidential_actions":
            return "executive_policy_action", {"matched_keywords": ["presidential_actions_page_bias"]}

        # Keyword config fallback
        keyword_cfg = (self.parsing_rules.get("headline_keywords", {}) or {})
        for event_type, rule in keyword_cfg.items():
            kws = [safe_lower(x) for x in rule.get("includes_any", [])]
            matched = [kw for kw in kws if kw in text]
            if matched:
                return event_type, {"matched_keywords": matched}

        return "macro_calendar_update", {"matched_keywords": []}

    def _make_packet(
        self,
        source_url: str,
        source_feed_url: str,
        source_type: str,
        event_type: str,
        headline: str,
        summary: Optional[str],
        parsing_meta: Dict[str, Any],
        published_time_utc: Optional[str]
    ) -> Dict[str, Any]:
        source_tier = "tier_a_official"
        source_conf = float((self.source_tiers.get(source_tier) or {}).get("confidence_weight", 0.90))

        urgency_defaults = ((self.scoring_overlays.get("policy_release_urgency_score") or {}).get("defaults") or {})
        urgency = float(urgency_defaults.get(event_type, 0.55))

        # Rate-regime candidate tagging
        rate_sensitive_keywords = [safe_lower(k) for k in (((self.parsing_rules.get("rate_sensitive_topic_keywords") or {}).get("includes_any")) or [])]
        text = f"{safe_lower(headline)} {safe_lower(summary)}"
        keyword_rate_sensitive = any(k in text for k in rate_sensitive_keywords)

        # Executive actions can impact rates/markets via tariffs/sanctions/immigration/trade/fiscal policy
        rate_regime = False
        if event_type == "executive_policy_action":
            rate_regime = keyword_rate_sensitive or any(k in text for k in ["tariff", "trade", "immigration", "oil", "energy", "sanction", "fiscal", "tax"])
        elif keyword_rate_sensitive:
            rate_regime = True

        requires_cross_asset = bool(rate_regime and self.guardrails.get("force_cross_asset_checks_for_rate_regime_shock_candidate", True))

        tags = ["official_source_confirmed"]
        if rate_regime:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]

        release_window = {
            "release_key": None,
            "release_time_et_hint": None,
            "pre_buffer_minutes": int(self.event_windows.get("pre_release_buffer_minutes_default", 10)),
            "post_buffer_minutes": int(self.event_windows.get("post_release_buffer_minutes_default", 20))
        }

        event_id_input = f"whitehouse|{source_url}|{headline}|{published_time_utc or ''}|{json.dumps(parsing_meta, sort_keys=True)}"
        event_id = f"whitehouse-{sha1_hex(event_id_input)[:16]}"

        return {
            "schema_version": "macro_policy_event.v1",
            "event_id": event_id,
            "timestamp_utc": iso_now(),

            "source_domain": "whitehouse.gov",
            "source_url": source_url,
            "source_feed_url": source_feed_url,
            "source_tier": source_tier,
            "source_type": source_type,
            "official_source": True,

            "event_type": event_type,
            "headline": headline,
            "summary": summary,
            "published_time_utc": published_time_utc,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime,
            "requires_rate_cross_asset_check": requires_cross_asset,
            "official_source_confirmed": True,

            "policy_release_urgency_score": urgency,
            "source_confidence": source_conf,

            "release_window": release_window,

            "parsing_meta": parsing_meta,
            "provenance": {
                "bridge": "whitehouse_policy_bridge",
                "bridge_version": "0.1.0-skeleton",
                "normalized_from": source_type,
                "raw_source_url": source_feed_url
            },

            "operator_summary": self._operator_summary(headline, event_type, urgency, rate_regime)
        }

    def _operator_summary(self, headline: str, event_type: str, urgency: float, rate_regime: bool) -> str:
        parts = [f"{event_type}: {headline}", f"urgency={urgency:.2f}"]
        if rate_regime:
            parts.append("rate_regime_shock_candidate=true")
        return " | ".join(parts)

    def _error_packet(self, source_url: str, error: str, page_name: Optional[str]) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "whitehouse.gov",
            "source_url": source_url,
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "whitehouse_policy_bridge",
            "page_name": page_name,
            "error": error
        }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel White House Policy Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true")
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--loop-seconds", type=int, default=300)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = WhiteHousePolicyBridge(repo_root)

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

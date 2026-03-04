#!/usr/bin/env python3
"""
Global Sentinel V4.5 - Macro Event Router

Purpose:
- Deduplicate and prioritize normalized macro_policy_event packets from multiple bridges
- Compute macro event quorum status
- Emit top-ranked events + suppressed duplicates + summary stats

Design goals:
- Deterministic
- Auditable
- Source-tier aware
- Official-source-first
- Policy-safe (no execution logic)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def safe_lower(v: Any) -> str:
    return str(v or "").lower()


def norm_text(v: Any) -> str:
    s = str(v or "").strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def event_fingerprint(ev: Dict[str, Any]) -> str:
    """
    Build a coarse content fingerprint to collapse obvious duplicates across bridges.
    Prioritizes same event_type + normalized headline + source URL path-ish.
    """
    event_type = str(ev.get("event_type", "unknown"))
    headline = norm_text(ev.get("headline", ""))[:180]
    src_url = str(ev.get("source_url", ""))[:220]
    base = f"{event_type}|{headline}|{src_url}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


@dataclass
class RouterConfig:
    max_top_events: int = 12
    duplicate_window_seconds: int = 3600  # placeholder; timestamps may be missing
    quorum_min_official_confirmations: int = 1
    quorum_min_total_events_for_high_conf: int = 2
    suppress_page_heartbeat_if_item_present: bool = True


class MacroEventRouter:
    def __init__(self, config: Optional[RouterConfig] = None):
        self.cfg = config or RouterConfig()

        # Source tier weights (aligned with macro_policy_intel tiers)
        self.source_tier_weights = {
            "tier_b_institutional": 1.00,
            "tier_a_official": 0.95,
            "tier_c_osint_alt": 0.60,
            "tier_d_free_fallback": 0.45,
        }

        # Event type priority (institutional-style routing)
        self.event_type_priority = {
            "central_bank_statement": 1.00,
            "inflation_release": 0.98,
            "labor_release": 0.96,
            "treasury_sanctions_or_regulatory_action": 0.90,
            "executive_policy_action": 0.88,
            "growth_release": 0.82,
            "fed_speech_testimony": 0.78,
            "official_release_schedule_change": 0.65,
            "macro_calendar_update": 0.45,
        }

    # -------------------------
    # Public API
    # -------------------------
    def route(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid = [e for e in events if self._is_valid_macro_policy_event(e)]

        # Score first (used in dedupe winner selection)
        scored = [self._annotate_with_routing_score(e) for e in valid]

        # Dedupe / suppress
        deduped, suppressed = self._dedupe(scored)

        # Optional heartbeat suppression (keep item-level > page heartbeat if overlap exists)
        if self.cfg.suppress_page_heartbeat_if_item_present:
            deduped, extra_suppressed = self._suppress_heartbeat_noise(deduped)
            suppressed.extend(extra_suppressed)

        # Sort final priority
        deduped_sorted = sorted(
            deduped,
            key=lambda e: (
                -safe_float(e.get("_router", {}).get("priority_score")),
                -safe_float(e.get("policy_release_urgency_score", 0.0)),
                str(e.get("timestamp_utc", "")),
            ),
        )

        top_events = deduped_sorted[: self.cfg.max_top_events]
        overflow_events = deduped_sorted[self.cfg.max_top_events :]

        quorum = self._compute_quorum(top_events)
        summary = self._build_summary(top_events, suppressed, overflow_events, quorum)

        return {
            "schema_version": "macro_event_router_output.v1",
            "timestamp_utc": iso_now(),
            "macro_events_priority_top": top_events,
            "macro_events_suppressed_duplicates": suppressed,
            "macro_events_overflow": overflow_events,
            "macro_event_quorum_status": quorum,
            "macro_event_router_summary": summary,
        }

    # -------------------------
    # Validation / scoring
    # -------------------------
    def _is_valid_macro_policy_event(self, ev: Dict[str, Any]) -> bool:
        schema = str(ev.get("schema_version", ""))
        return schema.startswith("macro_policy_event")

    def _annotate_with_routing_score(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        e = dict(ev)  # shallow copy
        tier = str(e.get("source_tier", "tier_d_free_fallback"))
        event_type = str(e.get("event_type", "macro_calendar_update"))

        tier_w = self.source_tier_weights.get(tier, 0.40)
        type_w = self.event_type_priority.get(event_type, 0.40)
        urgency = safe_float(e.get("policy_release_urgency_score"), 0.0)
        source_conf = safe_float(e.get("source_confidence"), 0.0)

        official_bonus = 0.08 if e.get("official_source_confirmed") is True else 0.0
        rate_regime_bonus = 0.08 if e.get("rate_regime_shock_candidate") is True else 0.0
        cross_asset_bonus = 0.04 if e.get("requires_rate_cross_asset_check") is True else 0.0

        # De-emphasize page heartbeat packets relative to item-level links/API series packets
        source_type = str(e.get("source_type", "unknown"))
        source_type_adj = {
            "api": 0.08,
            "rss": 0.06,
            "page_link": 0.03,
            "page": -0.06,
        }.get(source_type, 0.0)

        # Build a deterministic score
        score = (
            0.30 * urgency
            + 0.20 * source_conf
            + 0.20 * tier_w
            + 0.20 * type_w
            + official_bonus
            + rate_regime_bonus
            + cross_asset_bonus
            + source_type_adj
        )

        # Clamp for display sanity
        score = max(0.0, min(2.0, score))

        fp = event_fingerprint(e)
        e["_router"] = {
            "priority_score": round(score, 4),
            "source_tier_weight": tier_w,
            "event_type_priority": type_w,
            "fingerprint": fp,
        }
        return e

    # -------------------------
    # Dedupe / suppression
    # -------------------------
    def _dedupe(self, events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Dedupe by coarse fingerprint. Keep highest router score.
        """
        winners: Dict[str, Dict[str, Any]] = {}
        suppressed: List[Dict[str, Any]] = []

        for ev in events:
            fp = ev.get("_router", {}).get("fingerprint")
            if not fp:
                fp = hashlib.sha1(json.dumps(ev, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
                ev.setdefault("_router", {})["fingerprint"] = fp

            if fp not in winners:
                winners[fp] = ev
                continue

            current = winners[fp]
            curr_score = safe_float(current.get("_router", {}).get("priority_score"), 0.0)
            new_score = safe_float(ev.get("_router", {}).get("priority_score"), 0.0)

            if new_score > curr_score:
                suppressed.append(self._mark_suppressed(current, reason="duplicate_lower_priority", winner=ev))
                winners[fp] = ev
            else:
                suppressed.append(self._mark_suppressed(ev, reason="duplicate_lower_priority", winner=current))

        return list(winners.values()), suppressed

    def _suppress_heartbeat_noise(self, events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        If a page heartbeat and a richer page_link/API/RSS event exist from same source group/event_type family,
        suppress the low-information page heartbeat.
        """
        kept: List[Dict[str, Any]] = []
        suppressed: List[Dict[str, Any]] = []

        # Build a quick index of "richer" events
        richer_keys = set()
        for ev in events:
            st = str(ev.get("source_type", ""))
            if st in {"api", "rss", "page_link"}:
                richer_keys.add(self._heartbeat_group_key(ev))

        for ev in events:
            st = str(ev.get("source_type", ""))
            if st == "page":
                k = self._heartbeat_group_key(ev)
                if k in richer_keys:
                    suppressed.append(self._mark_suppressed(ev, reason="suppressed_page_heartbeat_item_present", winner=None))
                    continue
            kept.append(ev)

        return kept, suppressed

    def _heartbeat_group_key(self, ev: Dict[str, Any]) -> str:
        domain = str(ev.get("source_domain", "unknown"))
        event_type = str(ev.get("event_type", "unknown"))
        source_feed_url = str(ev.get("source_feed_url", ev.get("source_url", "")))
        return f"{domain}|{event_type}|{source_feed_url}"

    def _mark_suppressed(self, ev: Dict[str, Any], reason: str, winner: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        x = dict(ev)
        xr = dict(x.get("_router", {}))
        xr["suppressed"] = True
        xr["suppress_reason"] = reason
        if winner is not None:
            xr["suppressed_by_event_id"] = winner.get("event_id")
            xr["suppressed_by_priority_score"] = winner.get("_router", {}).get("priority_score")
        x["_router"] = xr
        return x

    # -------------------------
    # Quorum / summaries
    # -------------------------
    def _compute_quorum(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        official_confirmations = 0
        total_events = len(events)
        tiers = set()
        rate_regime_any = False
        high_urgency_count = 0

        for e in events:
            if e.get("official_source_confirmed") is True:
                official_confirmations += 1
            if e.get("source_tier"):
                tiers.add(str(e["source_tier"]))
            if e.get("rate_regime_shock_candidate") is True:
                rate_regime_any = True
            if safe_float(e.get("policy_release_urgency_score"), 0.0) >= 0.85:
                high_urgency_count += 1

        quorum_pass = (
            official_confirmations >= self.cfg.quorum_min_official_confirmations
            and total_events >= 1
        )
        high_conf_quorum = (
            official_confirmations >= self.cfg.quorum_min_official_confirmations
            and total_events >= self.cfg.quorum_min_total_events_for_high_conf
        )

        return {
            "quorum_pass": quorum_pass,
            "high_conf_quorum": high_conf_quorum,
            "official_confirmations": official_confirmations,
            "total_priority_events": total_events,
            "high_urgency_event_count": high_urgency_count,
            "rate_regime_shock_candidate_any": rate_regime_any,
            "source_tiers_present": sorted(tiers),
        }

    def _build_summary(
        self,
        top_events: List[Dict[str, Any]],
        suppressed: List[Dict[str, Any]],
        overflow: List[Dict[str, Any]],
        quorum: Dict[str, Any],
    ) -> Dict[str, Any]:
        event_type_counts: Dict[str, int] = {}
        source_domain_counts: Dict[str, int] = {}
        top_ids: List[str] = []
        top_headlines: List[str] = []

        for e in top_events:
            et = str(e.get("event_type", "unknown"))
            event_type_counts[et] = event_type_counts.get(et, 0) + 1
            sd = str(e.get("source_domain", "unknown"))
            source_domain_counts[sd] = source_domain_counts.get(sd, 0) + 1
            if e.get("event_id"):
                top_ids.append(str(e["event_id"]))
            if e.get("headline"):
                top_headlines.append(str(e["headline"]))

        return {
            "top_event_count": len(top_events),
            "suppressed_duplicate_count": len(suppressed),
            "overflow_count": len(overflow),
            "event_type_counts_top": event_type_counts,
            "source_domain_counts_top": source_domain_counts,
            "top_event_ids": top_ids[:10],
            "top_headlines_preview": top_headlines[:5],
            "quorum_pass": quorum.get("quorum_pass"),
            "high_conf_quorum": quorum.get("high_conf_quorum"),
        }


# -------------------------
# CLI helpers
# -------------------------
def _load_events_from_file(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    # JSON array or JSONL supported
    if text.startswith("["):
        data = json.loads(text)
        return data if isinstance(data, list) else []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel Macro Event Router")
    p.add_argument("--input", default=None, help="JSON or JSONL file of macro_policy_event packets")
    p.add_argument("--output", default=None, help="Optional output path")
    p.add_argument("--max-top", type=int, default=12)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = RouterConfig(max_top_events=args.max_top)
    router = MacroEventRouter(cfg)

    if args.input:
        events = _load_events_from_file(Path(args.input))
    else:
        # Read JSONL from stdin
        events = []
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    events.append(obj)
            except Exception:
                pass

    out = router.route(events)

    if args.output:
        p = Path(args.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    else:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

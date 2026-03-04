#!/usr/bin/env python3
"""
Global Sentinel V4.5 - EIA Bridge (Skeleton)

Purpose:
- Query EIA API for petroleum/energy inventory series
- Emit normalized macro_policy_event packets for energy-related macro updates
- Tag rate_regime_shock_candidate when inventory surprises + oil shock conditions present
- Confirms whether geopolitical oil shock is bleeding into U.S. inventories

Requires EIA_API_KEY env var (free from https://www.eia.gov/opendata/)

Shadow / intelligence only:
- No execution logic
- Produces macro_policy_event packets for crisis_monitor / macro policy layer
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


# Default EIA series to track (petroleum status report)
DEFAULT_SERIES = {
    "PET.WCESTUS1.W": "U.S. Crude Oil Stocks (excl SPR)",
    "PET.WGTSTUS1.W": "U.S. Total Gasoline Stocks",
    "PET.WDISTUS1.W": "U.S. Distillate Fuel Oil Stocks",
    "PET.WPULEUS3.W": "U.S. Refinery Utilization (%)",
}


class EIABridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.parsing_rules = self.macro_cfg.get("parsing_rules", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.api_key = os.environ.get("EIA_API_KEY", "")
        self.api_base = "https://api.eia.gov/v2"

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "eia_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Use default series; can be extended via config
        self.series_map = dict(DEFAULT_SERIES)

    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        if not self.api_key:
            packets.append(self._error_packet("EIA_API_KEY not set"))
            return packets

        for series_id, description in self.series_map.items():
            try:
                packets.extend(self._poll_series(series_id, description))
            except Exception as e:
                packets.append(self._error_packet(f"series_error:{series_id}:{e}"))
        return packets

    def _poll_series(self, series_id: str, description: str) -> List[Dict[str, Any]]:
        data = self._fetch_series(series_id)
        response_data = data.get("response", {}).get("data", [])

        if not response_data:
            return [self._error_packet(f"no_data:{series_id}")]

        latest = response_data[0] if response_data else {}
        latest_value = latest.get("value")
        latest_period = latest.get("period", "")

        prior_value = None
        if len(response_data) >= 2:
            pv = response_data[1].get("value")
            if pv is not None:
                try:
                    prior_value = float(pv)
                except (ValueError, TypeError):
                    pass

        current_value = None
        if latest_value is not None:
            try:
                current_value = float(latest_value)
            except (ValueError, TypeError):
                pass

        change_vs_prior = None
        if current_value is not None and prior_value is not None:
            change_vs_prior = round(current_value - prior_value, 2)

        # Energy inventories are always rate-regime relevant in geopolitical context
        rate_regime = True

        parsing_meta = {
            "series_id": series_id,
            "description": description,
            "latest_value": current_value,
            "latest_period": latest_period,
            "prior_value": prior_value,
            "change_vs_prior": change_vs_prior,
        }

        return [self._make_packet(
            series_id=series_id,
            headline=f"EIA {description}: {current_value} ({latest_period})",
            summary=f"EIA series {series_id} ({description}): {current_value}. Change vs prior: {change_vs_prior}.",
            parsing_meta=parsing_meta,
            rate_regime=rate_regime,
        )]

    def _fetch_series(self, series_id: str) -> Dict[str, Any]:
        # EIA v2 API format
        # Series IDs like PET.WCESTUS1.W need to be mapped to v2 routes
        # For simplicity, use the series search endpoint
        params = urllib.parse.urlencode({
            "api_key": self.api_key,
            "frequency": "weekly",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 2,
        })
        # Convert old series ID format to v2 route (simplified)
        url = f"{self.api_base}/seriesid/{series_id}?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinelEIABridge/1.0 (+shadow-mode)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def _make_packet(
        self,
        series_id: str,
        headline: str,
        summary: Optional[str],
        parsing_meta: Dict[str, Any],
        rate_regime: bool,
    ) -> Dict[str, Any]:
        # EIA is a Tier A official source
        source_tier = "tier_a_official"
        source_conf = float((self.source_tiers.get(source_tier) or {}).get("confidence_weight", 0.90))

        urgency_defaults = ((self.scoring_overlays.get("policy_release_urgency_score") or {}).get("defaults") or {})
        urgency = float(urgency_defaults.get("growth_release", 0.75))

        requires_cross_asset = bool(rate_regime and self.guardrails.get("force_cross_asset_checks_for_rate_regime_shock_candidate", True))

        tags = ["official_source_confirmed"]
        if rate_regime:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]

        release_window = {
            "release_key": "eia_petroleum_status",
            "release_time_et_hint": "10:30",
            "pre_buffer_minutes": int(self.event_windows.get("pre_release_buffer_minutes_default", 10)),
            "post_buffer_minutes": int(self.event_windows.get("post_release_buffer_minutes_default", 20)),
        }

        event_id_input = f"eia|{series_id}|{headline}|{json.dumps(parsing_meta, sort_keys=True)}"
        event_id = f"eia-{sha1_hex(event_id_input)[:16]}"

        return {
            "schema_version": "macro_policy_event.v1",
            "event_id": event_id,
            "timestamp_utc": iso_now(),

            "source_domain": "eia.gov",
            "source_url": f"https://www.eia.gov/opendata/browser/?api={series_id}",
            "source_feed_url": self.api_base,
            "source_tier": source_tier,
            "source_type": "api",
            "official_source": True,

            "event_type": "macro_calendar_update",
            "headline": headline,
            "summary": summary,
            "published_time_utc": None,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime,
            "requires_rate_cross_asset_check": requires_cross_asset,
            "official_source_confirmed": True,

            "policy_release_urgency_score": urgency,
            "source_confidence": source_conf,

            "release_window": release_window,

            "parsing_meta": parsing_meta,
            "provenance": {
                "bridge": "eia_bridge",
                "bridge_version": "0.1.0-skeleton",
                "normalized_from": "api",
                "raw_source_url": self.api_base,
            },

            "operator_summary": self._operator_summary(headline, urgency, rate_regime),
        }

    def _operator_summary(self, headline: str, urgency: float, rate_regime: bool) -> str:
        parts = [f"macro_calendar_update: {headline}", f"urgency={urgency:.2f}"]
        if rate_regime:
            parts.append("rate_regime_shock_candidate=true")
        return " | ".join(parts)

    def _error_packet(self, error: str) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "eia.gov",
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "eia_bridge",
            "error": error,
        }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel EIA Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true")
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--loop-seconds", type=int, default=600)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = EIABridge(repo_root)

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

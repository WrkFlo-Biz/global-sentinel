#!/usr/bin/env python3
"""
Global Sentinel V4.5 - FRED Bridge (Skeleton)

Purpose:
- Pull configured FRED series from config/macro_policy_intel.yaml
- Emit normalized macro_policy_event packets for macro_calendar_update
- Tag rate_regime_shock_candidate when rate-sensitive series are present
- Track series staleness and release metadata

Requires FRED_API_KEY env var (free from https://fred.stlouisfed.org/docs/api/api_key.html)

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


# Rate-sensitive series that should trigger rate_regime_shock_candidate tagging
RATE_SENSITIVE_SERIES = {"DGS10", "DFF", "CPIAUCSL", "PCEPI", "PAYEMS", "UNRATE"}

# Series-to-event-type mapping
SERIES_EVENT_TYPE_MAP = {
    "DGS10": "macro_calendar_update",
    "DFF": "macro_calendar_update",
    "CPIAUCSL": "inflation_release",
    "PCEPI": "inflation_release",
    "PAYEMS": "labor_release",
    "UNRATE": "labor_release",
    "VIXCLS": "macro_calendar_update",
}


class FredBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.fred_cfg = (self.macro_cfg.get("official_sources", {}) or {}).get("fred", {})
        self.parsing_rules = self.macro_cfg.get("parsing_rules", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.api_base = self.fred_cfg.get("api_base", "https://api.stlouisfed.org/fred")
        self.api_key = os.environ.get("FRED_API_KEY", "")

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "fred_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Collect all configured series
        self.series_ids: List[str] = []
        series_cfg = self.fred_cfg.get("series", {}) or {}
        for _group, ids in series_cfg.items():
            if isinstance(ids, list):
                self.series_ids.extend(ids)

    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        if not self.api_key:
            packets.append(self._error_packet("FRED_API_KEY not set"))
            return packets

        for series_id in self.series_ids:
            try:
                packets.extend(self._poll_series(series_id))
            except Exception as e:
                packets.append(self._error_packet(f"series_error:{series_id}:{e}"))
        return packets

    def _poll_series(self, series_id: str) -> List[Dict[str, Any]]:
        # Fetch latest observation
        obs_data = self._fetch_series_observations(series_id, limit=2)
        observations = obs_data.get("observations", [])
        if not observations:
            return [self._error_packet(f"no_observations:{series_id}")]

        latest = observations[-1]
        latest_value = latest.get("value", ".")
        latest_date = latest.get("date", "")

        # Compute prior value for change detection
        prior_value = None
        if len(observations) >= 2:
            pv = observations[-2].get("value", ".")
            if pv != ".":
                try:
                    prior_value = float(pv)
                except (ValueError, TypeError):
                    pass

        current_value = None
        if latest_value != ".":
            try:
                current_value = float(latest_value)
            except (ValueError, TypeError):
                pass

        # Staleness check
        days_since = None
        if latest_date:
            try:
                obs_dt = datetime.strptime(latest_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_since = (datetime.now(timezone.utc) - obs_dt).days
            except Exception:
                pass

        # Determine event type
        event_type = SERIES_EVENT_TYPE_MAP.get(series_id, "macro_calendar_update")
        rate_regime = series_id in RATE_SENSITIVE_SERIES

        change_vs_prior = None
        if current_value is not None and prior_value is not None:
            change_vs_prior = round(current_value - prior_value, 6)

        parsing_meta = {
            "series_id": series_id,
            "latest_value": current_value,
            "latest_date": latest_date,
            "prior_value": prior_value,
            "change_vs_prior": change_vs_prior,
            "days_since_observation": days_since,
        }

        return [self._make_packet(
            series_id=series_id,
            event_type=event_type,
            headline=f"FRED {series_id}: {current_value} ({latest_date})",
            summary=f"FRED series {series_id} latest observation: {current_value} on {latest_date}. Change vs prior: {change_vs_prior}.",
            parsing_meta=parsing_meta,
            rate_regime=rate_regime,
        )]

    def _fetch_series_observations(self, series_id: str, limit: int = 2) -> Dict[str, Any]:
        params = urllib.parse.urlencode({
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        })
        url = f"{self.api_base}/series/observations?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinelFredBridge/1.0 (+shadow-mode)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        # Reverse so oldest first
        if "observations" in data:
            data["observations"] = list(reversed(data["observations"]))
        return data

    def _make_packet(
        self,
        series_id: str,
        event_type: str,
        headline: str,
        summary: Optional[str],
        parsing_meta: Dict[str, Any],
        rate_regime: bool,
    ) -> Dict[str, Any]:
        source_tier = "tier_a_official"
        source_conf = float((self.source_tiers.get(source_tier) or {}).get("confidence_weight", 0.90))

        urgency_defaults = ((self.scoring_overlays.get("policy_release_urgency_score") or {}).get("defaults") or {})
        urgency = float(urgency_defaults.get(event_type, 0.40))

        requires_cross_asset = bool(rate_regime and self.guardrails.get("force_cross_asset_checks_for_rate_regime_shock_candidate", True))

        tags = ["official_source_confirmed"]
        if rate_regime:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]

        release_window = {
            "release_key": None,
            "release_time_et_hint": None,
            "pre_buffer_minutes": int(self.event_windows.get("pre_release_buffer_minutes_default", 10)),
            "post_buffer_minutes": int(self.event_windows.get("post_release_buffer_minutes_default", 20)),
        }

        event_id_input = f"fred|{series_id}|{headline}|{json.dumps(parsing_meta, sort_keys=True)}"
        event_id = f"fred-{sha1_hex(event_id_input)[:16]}"

        return {
            "schema_version": "macro_policy_event.v1",
            "event_id": event_id,
            "timestamp_utc": iso_now(),

            "source_domain": "fred.stlouisfed.org",
            "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
            "source_feed_url": self.api_base,
            "source_tier": source_tier,
            "source_type": "api",
            "official_source": True,

            "event_type": event_type,
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
                "bridge": "fred_bridge",
                "bridge_version": "0.1.0-skeleton",
                "normalized_from": "api",
                "raw_source_url": self.api_base,
            },

            "operator_summary": self._operator_summary(headline, event_type, urgency, rate_regime),
        }

    def _operator_summary(self, headline: str, event_type: str, urgency: float, rate_regime: bool) -> str:
        parts = [f"{event_type}: {headline}", f"urgency={urgency:.2f}"]
        if rate_regime:
            parts.append("rate_regime_shock_candidate=true")
        return " | ".join(parts)

    def _error_packet(self, error: str) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "fred.stlouisfed.org",
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "fred_bridge",
            "error": error,
        }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel FRED Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true")
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--series", default="", help="Comma-separated series IDs to override config")
    p.add_argument("--loop-seconds", type=int, default=300)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = FredBridge(repo_root)

    # Override series from CLI if provided
    if args.series:
        bridge.series_ids = [s.strip() for s in args.series.split(",") if s.strip()]

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

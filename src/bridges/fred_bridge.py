#!/usr/bin/env python3
"""
Global Sentinel V4.5 - FRED Bridge (Full Implementation)

Purpose:
- Pull configured FRED series from config/macro_policy_intel.yaml
- Emit normalized macro_policy_event packets for macro calendar / macro data updates
- Tag rate_regime_shock_candidate when rate-sensitive series move beyond thresholds
- Track basic staleness and observation metadata

Shadow / intelligence only:
- No execution logic
- No order routing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, date
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


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        if isinstance(v, str) and v.strip() in {".", "", "NaN", "nan", "null", "None"}:
            return default
        return float(v)
    except Exception:
        return default


def safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def safe_lower(x: Any) -> str:
    return str(x or "").lower()


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def read_json_url(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GlobalSentinelFREDBridge/1.0 (+shadow-mode)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def parse_obs_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


# -----------------------------
# Bridge
# -----------------------------
class FREDBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.fred_cfg = (self.macro_cfg.get("official_sources", {}) or {}).get("fred", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.api_base = (self.fred_cfg.get("api_base") or "https://api.stlouisfed.org/fred").rstrip("/")
        self.api_key = os.getenv("FRED_API_KEY")

        self.series_by_category = self._load_series_map()
        self.rate_check_thresholds = self._load_rate_check_thresholds()

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "fred_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _load_series_map(self) -> Dict[str, List[str]]:
        series_cfg = (self.fred_cfg.get("series") or {})
        out: Dict[str, List[str]] = {}
        for cat, series_list in series_cfg.items():
            if isinstance(series_list, list):
                out[cat] = [str(s) for s in series_list]
        return out

    def _load_rate_check_thresholds(self) -> Dict[str, float]:
        cfg = (self.fred_cfg.get("rate_check_thresholds") or {})
        return {
            "DGS10_abs_delta": float(cfg.get("DGS10_abs_delta", 0.10)),
            "DFF_abs_delta": float(cfg.get("DFF_abs_delta", 0.10)),
            "VIXCLS_abs_delta": float(cfg.get("VIXCLS_abs_delta", 2.0)),
            "CPIAUCSL_abs_delta": float(cfg.get("CPIAUCSL_abs_delta", 0.5)),
            "PCEPI_abs_delta": float(cfg.get("PCEPI_abs_delta", 0.3)),
            "PAYEMS_abs_delta": float(cfg.get("PAYEMS_abs_delta", 150.0)),
            "UNRATE_abs_delta": float(cfg.get("UNRATE_abs_delta", 0.2)),
            "staleness_days_warning": float(cfg.get("staleness_days_warning", 10)),
        }

    # -------------------------
    # Public API
    # -------------------------
    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        all_series = self._flatten_series()
        for category, series_id in all_series:
            try:
                packets.extend(self._poll_series(category, series_id))
            except Exception as e:
                packets.append(self._error_packet(series_id, f"fred_series_error:{e}", category))
        return packets

    def _flatten_series(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for cat, ids in self.series_by_category.items():
            for sid in ids:
                out.append((cat, sid))
        return out

    # -------------------------
    # Series polling
    # -------------------------
    def _poll_series(self, category: str, series_id: str) -> List[Dict[str, Any]]:
        meta = self._get_series_meta(series_id)
        obs = self._get_series_observations(series_id, limit=3)
        latest, prev = self._extract_latest_and_prev(obs)

        latest_date = parse_obs_date(latest.get("date") if latest else None) if latest else None
        prev_date = parse_obs_date(prev.get("date") if prev else None) if prev else None  # noqa: F841

        latest_value = safe_float(latest.get("value") if latest else None)
        prev_value = safe_float(prev.get("value") if prev else None)
        delta = (latest_value - prev_value) if (latest_value is not None and prev_value is not None) else None

        today = datetime.now(timezone.utc).date()
        staleness_days = (today - latest_date).days if latest_date else None

        series_name = (meta.get("seriess") or [{}])[0].get("title") if isinstance(meta.get("seriess"), list) else None
        units = (meta.get("seriess") or [{}])[0].get("units") if isinstance(meta.get("seriess"), list) else None
        frequency = (meta.get("seriess") or [{}])[0].get("frequency_short") if isinstance(meta.get("seriess"), list) else None
        last_updated = (meta.get("seriess") or [{}])[0].get("last_updated") if isinstance(meta.get("seriess"), list) else None

        event_type = self._map_category_to_event_type(category)
        urgency = self._policy_release_urgency_score(event_type)
        source_conf = self._source_confidence("tier_a_official")

        rate_regime_candidate, triggers = self._rate_regime_check(series_id, category, latest_value, prev_value, delta)
        requires_cross_asset = bool(rate_regime_candidate and self.guardrails.get("force_cross_asset_checks_for_rate_regime_shock_candidate", True))

        if staleness_days is not None and staleness_days > self.rate_check_thresholds["staleness_days_warning"]:
            triggers.append(f"staleness_warning:{staleness_days}d")

        tags = ["official_source_confirmed"]
        if rate_regime_candidate:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]

        release_window = self._release_window_hint_for_category(category, series_id)

        packet = {
            "schema_version": "macro_policy_event.v1",
            "event_id": f"fred-{series_id}-{latest.get('date') if latest else 'na'}",
            "timestamp_utc": iso_now(),

            "source_domain": "fred.stlouisfed.org",
            "source_url": self._fred_series_url(series_id),
            "source_feed_url": self._fred_series_url(series_id),
            "source_tier": "tier_a_official",
            "source_type": "api",
            "official_source": True,

            "event_type": event_type,
            "headline": f"FRED series update: {series_id} ({series_name or 'Unknown series'})",
            "summary": f"Latest observation fetched from FRED for category={category}.",
            "published_time_utc": None,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime_candidate,
            "requires_rate_cross_asset_check": requires_cross_asset,
            "official_source_confirmed": True,

            "policy_release_urgency_score": urgency,
            "source_confidence": source_conf,

            "release_window": release_window,

            "parsing_meta": {
                "series_id": series_id,
                "series_category": category,
                "series_name": series_name,
                "units": units,
                "frequency": frequency,
                "last_updated": last_updated,
                "latest_observation": latest,
                "previous_observation": prev,
                "latest_value": latest_value,
                "previous_value": prev_value,
                "delta": delta,
                "delta_triggers": triggers,
                "staleness_days": staleness_days,
                "latest_observation_date": latest.get("date") if latest else None,
                "previous_observation_date": prev.get("date") if prev else None,
            },

            "provenance": {
                "bridge": "fred_bridge",
                "bridge_version": "0.1.0",
                "normalized_from": "fred_api",
                "raw_source_url": self._fred_series_url(series_id)
            },

            "operator_summary": self._operator_summary(
                series_id=series_id,
                event_type=event_type,
                latest_value=latest_value,
                delta=delta,
                staleness_days=staleness_days,
                rate_regime=rate_regime_candidate,
                triggers=triggers
            )
        }

        return [packet]

    def _map_category_to_event_type(self, category: str) -> str:
        mapping = {
            "inflation": "inflation_release",
            "labor": "labor_release",
            "rates": "central_bank_statement",
            "equity_indices": "equity_index_update",
            "credit_spreads": "credit_spread_update",
        }
        return mapping.get(category, "macro_calendar_update")

    def _policy_release_urgency_score(self, event_type: str) -> float:
        defaults = ((self.scoring_overlays.get("policy_release_urgency_score") or {}).get("defaults") or {})
        return float(defaults.get(event_type, 0.50))

    def _source_confidence(self, tier: str) -> float:
        tiers = self.source_tiers or {}
        return float((tiers.get(tier) or {}).get("confidence_weight", 0.90))

    def _release_window_hint_for_category(self, category: str, series_id: str) -> Dict[str, Any]:
        release_times = self.event_windows.get("release_times_et", {}) or {}
        pre = int(self.event_windows.get("pre_release_buffer_minutes_default", 10))
        post = int(self.event_windows.get("post_release_buffer_minutes_default", 20))

        release_key = None
        if category == "inflation":
            release_key = "cpi" if series_id == "CPIAUCSL" else "pce"
        elif category == "labor":
            release_key = "employment_situation"
        elif category == "rates":
            release_key = None

        return {
            "release_key": release_key,
            "release_time_et_hint": release_times.get(release_key),
            "pre_buffer_minutes": pre,
            "post_buffer_minutes": post,
        }

    def _rate_regime_check(
        self,
        series_id: str,
        category: str,
        latest_value: Optional[float],
        prev_value: Optional[float],
        delta: Optional[float],
    ) -> Tuple[bool, List[str]]:
        triggers: List[str] = []
        if latest_value is None:
            return False, triggers

        sid = str(series_id)
        abs_delta = abs(delta) if delta is not None else None

        threshold_key = f"{sid}_abs_delta"
        threshold = self.rate_check_thresholds.get(threshold_key)

        if abs_delta is not None and threshold is not None and abs_delta >= float(threshold):
            triggers.append(f"abs_delta_threshold:{sid}>={threshold}")

        if category == "rates" and abs_delta is not None:
            if abs_delta > 0:
                triggers.append("rate_series_changed")
        if category in {"inflation", "labor"} and abs_delta is not None:
            triggers.append(f"{category}_series_update")

        highly_sensitive = {
            "DGS1MO", "DGS3MO", "DGS1", "DGS2", "DGS5", "DGS7",
            "DGS10", "DGS20", "DGS30",
            "DFF", "DFEDTARU", "T10Y2Y", "T10Y3M", "T10YFF",
            "CPIAUCSL", "CPILFESL", "PCEPI",
            "PAYEMS", "UNRATE", "ICSA", "VIXCLS",
            "SP500", "NASDAQCOM", "DJIA", "WILLSMLCAP",
            "BAMLH0A0HYM2", "BAMLC0A4CBBB", "TEDRATE",
        }
        rate_regime = (len(triggers) > 0) and (sid in highly_sensitive or category in {"rates", "inflation", "labor", "equity_indices", "credit_spreads"})

        return bool(rate_regime), triggers

    def _operator_summary(
        self,
        series_id: str,
        event_type: str,
        latest_value: Optional[float],
        delta: Optional[float],
        staleness_days: Optional[int],
        rate_regime: bool,
        triggers: List[str],
    ) -> str:
        parts = [f"{event_type}: {series_id}"]
        parts.append(f"value={latest_value}")
        parts.append(f"delta={delta}")
        parts.append(f"staleness_days={staleness_days}")
        if rate_regime:
            parts.append("rate_regime_shock_candidate=true")
        if triggers:
            parts.append(f"triggers={','.join(triggers)}")
        return " | ".join(parts)

    # -------------------------
    # FRED API calls
    # -------------------------
    def _fred_series_url(self, series_id: str) -> str:
        return f"https://fred.stlouisfed.org/series/{series_id}"

    def _fred_api_get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        q = dict(params)
        q["file_type"] = "json"
        if self.api_key:
            q["api_key"] = self.api_key
        url = f"{self.api_base}/{endpoint}?{urllib.parse.urlencode(q)}"
        return read_json_url(url)

    def _get_series_meta(self, series_id: str) -> Dict[str, Any]:
        return self._fred_api_get("series", {"series_id": series_id})

    def _get_series_observations(self, series_id: str, limit: int = 3) -> Dict[str, Any]:
        params = {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": limit,
        }
        return self._fred_api_get("series/observations", params)

    def _extract_latest_and_prev(self, obs_json: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        observations = obs_json.get("observations") or []
        cleaned = [o for o in observations if safe_float(o.get("value")) is not None]
        latest = cleaned[0] if len(cleaned) >= 1 else None
        prev = cleaned[1] if len(cleaned) >= 2 else None
        return latest, prev

    # -------------------------
    # Errors
    # -------------------------
    def _error_packet(self, series_id: str, error: str, category: str) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "fred.stlouisfed.org",
            "source_url": self._fred_series_url(series_id),
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "fred_bridge",
            "series_id": series_id,
            "category": category,
            "error": error
        }


# -----------------------------
# CLI
# -----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel FRED Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true", help="Poll once and print JSON output")
    p.add_argument("--jsonl", action="store_true", help="Emit one JSON packet per line")
    p.add_argument("--loop-seconds", type=int, default=300, help="Loop polling interval")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = FREDBridge(repo_root)

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

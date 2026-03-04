#!/usr/bin/env python3
"""
Global Sentinel V4.5 - EIA Bridge (Implementation)

Purpose:
- Pull configured EIA series (weekly petroleum status and related energy supply indicators)
- Emit normalized macro_policy_event packets for energy inventory confirmation
- Tag rate_regime_shock_candidate when inventory / refinery / implied supply signals support
  an inflationary energy shock narrative

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


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        if isinstance(v, str) and v.strip() in {"", ".", "NaN", "nan", "null", "None"}:
            return default
        return float(v)
    except Exception:
        return default


def safe_lower(v: Any) -> str:
    return str(v or "").lower()


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def read_json_url(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GlobalSentinelEIABridge/1.0 (+shadow-mode)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


# -----------------------------
# Bridge
# -----------------------------
class EIABridge:
    """
    Supports EIA API v2 style endpoints.

    Config expectation (recommended extension under macro_policy_intel.official_sources.eia):
      eia:
        enabled: true
        api_base: "https://api.eia.gov/v2"
        series:
          crude_stocks:
            route: "petroleum/stoc/wstk/data/"
            params:
              frequency: "weekly"
              data: ["value"]
              facets:
                product: ["EPC0"]
                area: ["R30"]
              sort:
                - {column: "period", direction: "desc"}
              offset: 0
              length: 3
          gasoline_stocks:
            ...
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.eia_cfg = (self.macro_cfg.get("official_sources", {}) or {}).get("eia", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.api_key = os.getenv("EIA_API_KEY")
        self.api_base = (self.eia_cfg.get("api_base") or "https://api.eia.gov/v2").rstrip("/")

        # configured series map
        self.series_cfg = self.eia_cfg.get("series", {}) or {}

        # thresholds for energy shock confirmation
        t = self.eia_cfg.get("confirmation_thresholds", {}) or {}
        self.thresholds = {
            "inventory_draw_abs": float(t.get("inventory_draw_abs", 2.0)),
            "inventory_build_abs": float(t.get("inventory_build_abs", 2.0)),
            "refinery_util_change_abs": float(t.get("refinery_util_change_abs", 1.0)),
            "staleness_days_warning": float(t.get("staleness_days_warning", 10)),
        }

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "eia_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Public API
    # -------------------------
    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []

        if not self.series_cfg:
            packets.append(self._error_packet(
                source_url=self.api_base,
                error="no_eia_series_configured",
                series_key=None
            ))
            return packets

        for series_key, spec in self.series_cfg.items():
            try:
                packets.extend(self._poll_series(series_key, spec))
            except Exception as e:
                packets.append(self._error_packet(
                    source_url=self.api_base,
                    error=f"eia_series_error:{e}",
                    series_key=series_key
                ))

        return packets

    # -------------------------
    # Series polling
    # -------------------------
    def _poll_series(self, series_key: str, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        route = str(spec.get("route", "")).lstrip("/")
        params = dict(spec.get("params", {}) or {})

        if not route:
            return [self._error_packet(self.api_base, "missing_route", series_key)]

        resp = self._eia_get(route, params)
        data_rows = self._extract_rows(resp)

        latest, prev = self._latest_and_prev_rows(data_rows)
        latest_value, prev_value = self._extract_numeric_values(latest, prev)

        delta = None
        if latest_value is not None and prev_value is not None:
            delta = latest_value - prev_value

        period = latest.get("period") if latest else None
        series_name = self._extract_series_name(resp, spec, series_key)
        units = self._extract_units(resp)
        category = self._categorize_series(series_key, spec)

        event_type = "macro_calendar_update"

        # Energy shock confirmation logic
        rate_regime_candidate, confirmation_tags, confirmation_meta = self._energy_shock_confirmation_logic(
            series_key=series_key,
            category=category,
            latest_value=latest_value,
            prev_value=prev_value,
            delta=delta,
            latest_row=latest,
            prev_row=prev
        )

        urgency = self._policy_release_urgency_score(event_type)
        source_conf = self._source_confidence("tier_a_official")
        requires_cross_asset = bool(rate_regime_candidate and self.guardrails.get("force_cross_asset_checks_for_rate_regime_shock_candidate", True))

        tags = ["official_source_confirmed", "energy_supply_confirmation"]
        if rate_regime_candidate:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]
        tags += confirmation_tags

        release_window = self._release_window_hint()

        packet = {
            "schema_version": "macro_policy_event.v1",
            "event_id": f"eia-{series_key}-{period or 'na'}",
            "timestamp_utc": iso_now(),

            "source_domain": "api.eia.gov",
            "source_url": f"{self.api_base}/{route}",
            "source_feed_url": f"{self.api_base}/{route}",
            "source_tier": "tier_a_official",
            "source_type": "api",
            "official_source": True,

            "event_type": event_type,
            "headline": f"EIA energy series update: {series_key} ({series_name})",
            "summary": f"EIA data pull for {category} series. Used for energy supply/inventory confirmation.",
            "published_time_utc": None,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime_candidate,
            "requires_rate_cross_asset_check": requires_cross_asset,
            "official_source_confirmed": True,

            "policy_release_urgency_score": urgency,
            "source_confidence": source_conf,

            "release_window": release_window,

            "parsing_meta": {
                "series_key": series_key,
                "series_name": series_name,
                "series_category": category,
                "route": route,
                "units": units,
                "latest_row": latest,
                "previous_row": prev,
                "latest_value": latest_value,
                "previous_value": prev_value,
                "delta": delta,
                "confirmation_meta": confirmation_meta,
                "raw_row_count": len(data_rows)
            },

            "provenance": {
                "bridge": "eia_bridge",
                "bridge_version": "0.1.0",
                "normalized_from": "eia_api",
                "raw_source_url": f"{self.api_base}/{route}"
            },

            "operator_summary": self._operator_summary(
                series_key=series_key,
                category=category,
                latest_value=latest_value,
                delta=delta,
                rate_regime_candidate=rate_regime_candidate,
                confirmation_meta=confirmation_meta
            )
        }

        return [packet]

    def _categorize_series(self, series_key: str, spec: Dict[str, Any]) -> str:
        k = safe_lower(series_key)
        if "crude" in k and "stock" in k:
            return "crude_inventory"
        if "gasoline" in k and "stock" in k:
            return "gasoline_inventory"
        if ("distill" in k or "diesel" in k) and "stock" in k:
            return "distillate_inventory"
        if "refinery" in k and ("util" in k or "utilization" in k):
            return "refinery_utilization"
        if "imports" in k:
            return "imports"
        if "production" in k:
            return "production"
        return "energy_supply"

    # -------------------------
    # Confirmation logic
    # -------------------------
    def _energy_shock_confirmation_logic(
        self,
        series_key: str,
        category: str,
        latest_value: Optional[float],
        prev_value: Optional[float],
        delta: Optional[float],
        latest_row: Optional[Dict[str, Any]],
        prev_row: Optional[Dict[str, Any]]
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        Tags events that support / contradict an inflationary energy shock narrative.
        This does NOT itself "confirm" geopolitics; it provides a physical/economic transmission signal.
        """
        tags: List[str] = []
        meta: Dict[str, Any] = {
            "supports_supply_shock_narrative": False,
            "contradicts_supply_shock_narrative": False,
            "signals": []
        }

        if latest_value is None:
            meta["signals"].append("no_numeric_value")
            return False, tags, meta

        # Inventory logic (draws can support tighter supply narrative)
        if category in {"crude_inventory", "gasoline_inventory", "distillate_inventory"} and delta is not None:
            draw_abs = self.thresholds["inventory_draw_abs"]
            build_abs = self.thresholds["inventory_build_abs"]

            if delta <= -draw_abs:
                tags.append("inventory_draw_signal")
                meta["supports_supply_shock_narrative"] = True
                meta["signals"].append(f"inventory_draw_abs>={draw_abs}")
            elif delta >= build_abs:
                tags.append("inventory_build_signal")
                meta["contradicts_supply_shock_narrative"] = True
                meta["signals"].append(f"inventory_build_abs>={build_abs}")

        # Refinery utilization logic
        if category == "refinery_utilization" and delta is not None:
            util_thresh = self.thresholds["refinery_util_change_abs"]
            if delta <= -util_thresh:
                tags.append("refinery_utilization_drop_signal")
                meta["supports_supply_shock_narrative"] = True
                meta["signals"].append(f"refinery_util_change_drop_abs>={util_thresh}")
            elif delta >= util_thresh:
                tags.append("refinery_utilization_rise_signal")
                meta["signals"].append(f"refinery_util_change_rise_abs>={util_thresh}")

        # Imports / production
        if category in {"imports", "production"} and delta is not None:
            if delta > 0:
                meta["signals"].append(f"{category}_increase")
            elif delta < 0:
                meta["signals"].append(f"{category}_decrease")

        rate_regime_candidate = bool(meta["supports_supply_shock_narrative"])

        if meta["supports_supply_shock_narrative"] and meta["contradicts_supply_shock_narrative"]:
            tags.append("energy_confirmation_ambiguous")
            rate_regime_candidate = False

        return rate_regime_candidate, tags, meta

    # -------------------------
    # EIA API handling
    # -------------------------
    def _eia_get(self, route: str, params: Dict[str, Any]) -> Dict[str, Any]:
        q = self._flatten_eia_params(params)
        if self.api_key:
            q["api_key"] = self.api_key

        url = f"{self.api_base}/{route}"
        if q:
            url = f"{url}?{urllib.parse.urlencode(q, doseq=True)}"
        return read_json_url(url)

    def _flatten_eia_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts nested EIA params into query-string style.
        Example:
          facets: {product: ["EPC0"]} -> facets[product][]=EPC0
          sort: [{column:"period", direction:"desc"}] -> sort[0][column]=period ...
        """
        flat: Dict[str, Any] = {}

        for k, v in (params or {}).items():
            if k == "facets" and isinstance(v, dict):
                for facet_key, facet_vals in v.items():
                    if isinstance(facet_vals, list):
                        flat[f"facets[{facet_key}][]"] = [str(x) for x in facet_vals]
                    else:
                        flat[f"facets[{facet_key}][]"] = [str(facet_vals)]
            elif k == "sort" and isinstance(v, list):
                for i, sort_obj in enumerate(v):
                    if isinstance(sort_obj, dict):
                        for sk, sv in sort_obj.items():
                            flat[f"sort[{i}][{sk}]"] = str(sv)
            elif k == "data" and isinstance(v, list):
                flat["data[]"] = [str(x) for x in v]
            else:
                flat[k] = v
        return flat

    def _extract_rows(self, resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        response = resp.get("response") or {}
        rows = response.get("data") or []
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        return []

    def _extract_series_name(self, resp: Dict[str, Any], spec: Dict[str, Any], series_key: str) -> str:
        response = resp.get("response") or {}
        return str(response.get("description") or spec.get("name") or series_key)

    def _extract_units(self, resp: Dict[str, Any]) -> Optional[str]:
        response = resp.get("response") or {}
        return response.get("units")

    def _latest_and_prev_rows(self, rows: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not rows:
            return None, None
        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else None
        return latest, prev

    def _extract_numeric_values(
        self,
        latest: Optional[Dict[str, Any]],
        prev: Optional[Dict[str, Any]]
    ) -> Tuple[Optional[float], Optional[float]]:
        candidates = ["value", "Value", "series-value"]
        latest_value = None
        prev_value = None

        if latest:
            for c in candidates:
                if c in latest:
                    latest_value = safe_float(latest.get(c))
                    if latest_value is not None:
                        break
        if prev:
            for c in candidates:
                if c in prev:
                    prev_value = safe_float(prev.get(c))
                    if prev_value is not None:
                        break

        return latest_value, prev_value

    # -------------------------
    # Meta helpers
    # -------------------------
    def _policy_release_urgency_score(self, event_type: str) -> float:
        defaults = ((self.scoring_overlays.get("policy_release_urgency_score") or {}).get("defaults") or {})
        return float(defaults.get(event_type, 0.55))

    def _source_confidence(self, tier: str) -> float:
        return float((self.source_tiers.get(tier) or {}).get("confidence_weight", 0.90))

    def _release_window_hint(self) -> Dict[str, Any]:
        pre = int(self.event_windows.get("pre_release_buffer_minutes_default", 10))
        post = int(self.event_windows.get("post_release_buffer_minutes_default", 20))
        return {
            "release_key": "eia_weekly_petroleum_status",
            "release_time_et_hint": None,
            "pre_buffer_minutes": pre,
            "post_buffer_minutes": post
        }

    def _operator_summary(
        self,
        series_key: str,
        category: str,
        latest_value: Optional[float],
        delta: Optional[float],
        rate_regime_candidate: bool,
        confirmation_meta: Dict[str, Any]
    ) -> str:
        parts = [f"energy_supply:{series_key} ({category})"]
        parts.append(f"value={latest_value}")
        parts.append(f"delta={delta}")
        if rate_regime_candidate:
            parts.append("rate_regime_shock_candidate=true")
        if confirmation_meta.get("supports_supply_shock_narrative"):
            parts.append("supports_supply_shock_narrative=true")
        if confirmation_meta.get("contradicts_supply_shock_narrative"):
            parts.append("contradicts_supply_shock_narrative=true")
        sigs = confirmation_meta.get("signals") or []
        if sigs:
            parts.append(f"signals={','.join([str(s) for s in sigs])}")
        return " | ".join(parts)

    # -------------------------
    # Errors
    # -------------------------
    def _error_packet(self, source_url: str, error: str, series_key: Optional[str]) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "api.eia.gov",
            "source_url": source_url,
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "eia_bridge",
            "series_key": series_key,
            "error": error
        }


# -----------------------------
# CLI
# -----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel EIA Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true", help="Poll once and print JSON output")
    p.add_argument("--jsonl", action="store_true", help="Emit one JSON packet per line")
    p.add_argument("--loop-seconds", type=int, default=1800, help="Polling interval (default 30 min)")
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

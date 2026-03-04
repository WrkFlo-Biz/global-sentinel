#!/usr/bin/env python3
"""
Global Sentinel V4.5 - BLS Release Bridge (Skeleton)

Purpose:
- Poll BLS release schedule pages (CPI, Employment Situation, current-year schedule)
- Optionally pull configured BLS API series (if series IDs provided)
- Emit normalized macro_policy_event packets for:
  - inflation_release
  - labor_release
  - macro_calendar_update
  - official_release_schedule_change (heuristic placeholder)

Shadow / intelligence only:
- No execution logic
- No market orders
- Produces normalized packets for crisis_monitor / macro policy layer ingestion
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


# -----------------------------
# Utilities
# -----------------------------
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
        headers={"User-Agent": "GlobalSentinelBLSBridge/1.0 (+shadow-mode)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def post_json_url(url: str, payload: Dict[str, Any], timeout: int = 20) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "GlobalSentinelBLSBridge/1.0 (+shadow-mode)",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def strip_html(html: str) -> str:
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_page_title(html: str) -> str:
    m = re.search(r"(?is)<title>(.*?)</title>", html)
    return strip_html(m.group(1)) if m else ""


def parse_possible_datetime(text: str) -> Optional[str]:
    """Best-effort parser for schedule date strings. Returns UTC ISO string if parseable."""
    if not text:
        return None
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return None


# -----------------------------
# Bridge
# -----------------------------
class BLSReleaseBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.bls_cfg = self.macro_cfg.get("official_sources", {}).get("bls", {})
        self.parsing_rules = self.macro_cfg.get("parsing_rules", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "bls_release_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.pages = self.bls_cfg.get("pages", {})
        self.api_base = self.bls_cfg.get("api_base", "https://api.bls.gov/publicAPI/v2")
        self.api_key = self._read_api_key()

        # Optional configured series to poll via BLS API
        self.series_cfg = self.bls_cfg.get("series", {})

    def _read_api_key(self) -> Optional[str]:
        import os
        return os.getenv("BLS_API_KEY")

    # -------------------------
    # Public API
    # -------------------------
    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []

        # 1) Schedule pages
        for page_name, page_url in self.pages.items():
            try:
                packets.extend(self._poll_schedule_page(page_name, page_url))
            except Exception as e:
                packets.append(self._error_packet(page_url, f"schedule_page_error:{e}", page_name=page_name))

        # 2) Optional BLS API series pulls
        if self.series_cfg:
            try:
                packets.extend(self._poll_bls_api_series())
            except Exception as e:
                packets.append(self._error_packet(self.api_base, f"bls_api_error:{e}", page_name="bls_api"))

        return packets

    # -------------------------
    # Schedule page polling
    # -------------------------
    def _poll_schedule_page(self, page_name: str, url: str) -> List[Dict[str, Any]]:
        html = read_text_url(url)
        title = find_page_title(html)
        text = strip_html(html)

        packets: List[Dict[str, Any]] = []

        event_type = self._classify_schedule_page(page_name=page_name, title=title, text=text)
        release_hints = self._extract_release_hints(page_name=page_name, text=text)

        # Content fingerprint for change detection
        content_hash = sha1_hex(text[:5000])
        cache_file = self.cache_dir / f"{page_name}_hash.txt"
        prev_hash = cache_file.read_text(encoding="utf-8").strip() if cache_file.exists() else None
        changed = (prev_hash is not None and prev_hash != content_hash)
        cache_file.write_text(content_hash, encoding="utf-8")

        if changed:
            packets.append(
                self._make_packet(
                    source_url=url,
                    source_feed_url=url,
                    source_type="page",
                    event_type="official_release_schedule_change",
                    headline=f"BLS schedule page changed: {page_name}",
                    summary=f"Detected content hash change on BLS page '{page_name}'. Re-validate release times and event windows.",
                    published_time_utc=None,
                    parsing_meta={
                        "page_name": page_name,
                        "page_title": title,
                        "content_hash_changed": True,
                        "detected_release_hints": release_hints
                    },
                    force_rate_regime_candidate=(page_name in {"cpi_schedule", "employment_situation"}),
                    release_window_hint=self._release_window_hint_for_page(page_name)
                )
            )

        packets.append(
            self._make_packet(
                source_url=url,
                source_feed_url=url,
                source_type="page",
                event_type=event_type,
                headline=f"BLS page polled: {title or page_name}",
                summary=f"Schedule/status page '{page_name}' polled successfully.",
                published_time_utc=None,
                parsing_meta={
                    "page_name": page_name,
                    "page_title": title,
                    "content_hash_changed": changed,
                    "detected_release_hints": release_hints
                },
                force_rate_regime_candidate=(page_name in {"cpi_schedule", "employment_situation"}),
                release_window_hint=self._release_window_hint_for_page(page_name)
            )
        )

        return packets

    def _classify_schedule_page(self, page_name: str, title: str, text: str) -> str:
        combined = f"{safe_lower(page_name)} {safe_lower(title)} {safe_lower(text[:2000])}"
        if "consumer price index" in combined or page_name == "cpi_schedule":
            return "inflation_release"
        if "employment situation" in combined or "empsit" in combined or page_name == "employment_situation":
            return "labor_release"
        return "macro_calendar_update"

    def _extract_release_hints(self, page_name: str, text: str) -> List[Dict[str, Any]]:
        """Best-effort extractor for dates/times from BLS pages."""
        hints: List[Dict[str, Any]] = []

        # Find common "8:30 a.m." mentions
        for m in re.finditer(r"(?i)\b(8:30\s*a\.?m\.?\s*(ET|EST|EDT)?)\b", text):
            hints.append({
                "type": "release_time_et_hint",
                "value": "08:30",
                "evidence": m.group(0)
            })
            break

        # Find likely date mentions
        date_patterns = [
            r"(?i)([A-Z][a-z]+ \d{1,2}, \d{4})",
            r"(?i)(\d{1,2}/\d{1,2}/\d{4})"
        ]
        seen: set = set()
        for pat in date_patterns:
            for m in re.finditer(pat, text):
                raw = m.group(1)
                if raw in seen:
                    continue
                seen.add(raw)
                parsed = parse_possible_datetime(raw)
                hints.append({
                    "type": "date_hint",
                    "raw": raw,
                    "parsed_utc_date": parsed
                })
                if len(hints) >= 5:
                    return hints
        return hints

    def _release_window_hint_for_page(self, page_name: str) -> Dict[str, Any]:
        release_times = self.event_windows.get("release_times_et", {})
        pre = int(self.event_windows.get("pre_release_buffer_minutes_default", 10))
        post = int(self.event_windows.get("post_release_buffer_minutes_default", 20))

        if page_name == "cpi_schedule":
            key = "cpi"
        elif page_name == "employment_situation":
            key = "employment_situation"
        else:
            key = None

        return {
            "release_key": key,
            "release_time_et_hint": release_times.get(key),
            "pre_buffer_minutes": pre,
            "post_buffer_minutes": post
        }

    # -------------------------
    # BLS API polling (optional)
    # -------------------------
    def _poll_bls_api_series(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        if not self.series_cfg:
            return packets

        endpoint = f"{self.api_base.rstrip('/')}/timeseries/data/"
        grouped_series: List[Tuple[str, str]] = []
        for category, series_ids in self.series_cfg.items():
            if isinstance(series_ids, list):
                for sid in series_ids:
                    grouped_series.append((category, str(sid)))

        chunk_size = 10
        for i in range(0, len(grouped_series), chunk_size):
            chunk = grouped_series[i:i + chunk_size]
            series_ids = [sid for _, sid in chunk]
            payload: Dict[str, Any] = {
                "seriesid": series_ids,
                "startyear": str(datetime.now().year - 1),
                "endyear": str(datetime.now().year)
            }
            if self.api_key:
                payload["registrationkey"] = self.api_key

            resp = post_json_url(endpoint, payload)
            status = safe_lower(resp.get("status"))
            results = (resp.get("Results") or {}).get("series", [])

            packets.append(
                self._make_packet(
                    source_url=endpoint,
                    source_feed_url=endpoint,
                    source_type="api",
                    event_type="macro_calendar_update",
                    headline=f"BLS API poll batch ({len(series_ids)} series) status={status}",
                    summary=None,
                    published_time_utc=None,
                    parsing_meta={
                        "batch_series_ids": series_ids,
                        "api_status": resp.get("status"),
                        "message": resp.get("message"),
                    },
                    force_rate_regime_candidate=False,
                    release_window_hint={}
                )
            )

            for series_obj in results:
                sid = str(series_obj.get("seriesID", ""))
                latest = self._extract_latest_bls_series_point(series_obj)
                category = next((c for c, s in chunk if s == sid), "unknown")

                event_type = "inflation_release" if category == "inflation" else ("labor_release" if category == "labor" else "macro_calendar_update")
                rate_candidate = category in {"inflation", "labor"}

                packets.append(
                    self._make_packet(
                        source_url=endpoint,
                        source_feed_url=endpoint,
                        source_type="api",
                        event_type=event_type,
                        headline=f"BLS series update: {sid}",
                        summary=f"Latest BLS series point fetched for category={category}.",
                        published_time_utc=None,
                        parsing_meta={
                            "series_id": sid,
                            "series_category": category,
                            "latest_point": latest
                        },
                        force_rate_regime_candidate=rate_candidate,
                        release_window_hint=self._release_window_hint_for_series_category(category)
                    )
                )

        return packets

    def _extract_latest_bls_series_point(self, series_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = series_obj.get("data") or []
        if not data:
            return None
        d0 = data[0]
        return {
            "year": d0.get("year"),
            "period": d0.get("period"),
            "periodName": d0.get("periodName"),
            "value": d0.get("value"),
            "footnotes": d0.get("footnotes")
        }

    def _release_window_hint_for_series_category(self, category: str) -> Dict[str, Any]:
        release_times = self.event_windows.get("release_times_et", {})
        pre = int(self.event_windows.get("pre_release_buffer_minutes_default", 10))
        post = int(self.event_windows.get("post_release_buffer_minutes_default", 20))

        if category == "inflation":
            key = "cpi"
        elif category == "labor":
            key = "employment_situation"
        else:
            key = None
        return {
            "release_key": key,
            "release_time_et_hint": release_times.get(key),
            "pre_buffer_minutes": pre,
            "post_buffer_minutes": post
        }

    # -------------------------
    # Packet construction
    # -------------------------
    def _make_packet(
        self,
        source_url: str,
        source_feed_url: str,
        source_type: str,
        event_type: str,
        headline: str,
        summary: Optional[str],
        published_time_utc: Optional[str],
        parsing_meta: Dict[str, Any],
        force_rate_regime_candidate: bool,
        release_window_hint: Dict[str, Any]
    ) -> Dict[str, Any]:
        source_tier = "tier_a_official"
        source_conf = float(self.source_tiers.get(source_tier, {}).get("confidence_weight", 0.90))

        urgency_defaults = (self.scoring_overlays.get("policy_release_urgency_score", {}) or {}).get("defaults", {})
        urgency = float(urgency_defaults.get(event_type, 0.40))

        rate_sensitive_keywords = [
            safe_lower(k) for k in ((self.parsing_rules.get("rate_sensitive_topic_keywords", {}) or {}).get("includes_any", []))
        ]
        text = f"{safe_lower(headline)} {safe_lower(summary)}"
        keyword_rate_sensitive = any(k in text for k in rate_sensitive_keywords)

        rate_regime = bool(force_rate_regime_candidate or keyword_rate_sensitive)
        requires_cross_asset = bool(rate_regime and self.guardrails.get("force_cross_asset_checks_for_rate_regime_shock_candidate", True))

        tags = ["official_source_confirmed"]
        if rate_regime:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]

        event_id_input = f"{source_url}|{headline}|{published_time_utc or ''}|{json.dumps(parsing_meta, sort_keys=True)}"
        event_id = f"bls-{sha1_hex(event_id_input)[:16]}"

        return {
            "schema_version": "macro_policy_event.v1",
            "event_id": event_id,
            "timestamp_utc": iso_now(),

            "source_domain": "bls.gov",
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

            "release_window": release_window_hint,

            "parsing_meta": parsing_meta,
            "provenance": {
                "bridge": "bls_release_bridge",
                "bridge_version": "0.1.0-skeleton",
                "normalized_from": source_type,
                "raw_source_url": source_feed_url
            },

            "operator_summary": self._operator_summary(
                headline=headline,
                event_type=event_type,
                urgency=urgency,
                rate_regime=rate_regime,
                release_window=release_window_hint
            )
        }

    def _operator_summary(self, headline: str, event_type: str, urgency: float, rate_regime: bool, release_window: Dict[str, Any]) -> str:
        parts = [f"{event_type}: {headline}", f"urgency={urgency:.2f}"]
        if rate_regime:
            parts.append("rate_regime_shock_candidate=true")
        if release_window.get("release_time_et_hint"):
            parts.append(f"release_time_et_hint={release_window['release_time_et_hint']}")
        return " | ".join(parts)

    def _error_packet(self, source_url: str, error: str, page_name: Optional[str] = None) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "bls.gov",
            "source_url": source_url,
            "source_tier": "tier_a_official",
            "official_source": True,
            "bridge": "bls_release_bridge",
            "page_name": page_name,
            "error": error
        }


# -----------------------------
# CLI / stdio behavior
# -----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel BLS Release Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true", help="Poll once and print JSON array")
    p.add_argument("--jsonl", action="store_true", help="Print one JSON packet per line")
    p.add_argument("--loop-seconds", type=int, default=300, help="Polling interval in loop mode")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = BLSReleaseBridge(repo_root)

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

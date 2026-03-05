#!/usr/bin/env python3
"""
Global Sentinel V4.5 - Finnhub Bridge (Implementation)

Purpose:
- Pull company news and sentiment-ish headline context for configured watchlist symbols
- Emit normalized macro_policy_event packets (OSINT/alt tier) for enrichment
- NEVER confirm policy releases / official actions on its own

Shadow / intelligence only:
- No execution logic
- No order routing
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
from datetime import datetime, timedelta, timezone
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


def read_json_url(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GlobalSentinelFinnhubBridge/1.0 (+shadow-mode)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def utc_date_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


# -----------------------------
# Bridge
# -----------------------------
class FinnhubBridge:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cfg = load_yaml(repo_root / "config" / "macro_policy_intel.yaml")
        self.macro_cfg = self.cfg.get("macro_policy_intel", {})
        self.source_tiers = self.macro_cfg.get("source_tiers", {})
        self.scoring_overlays = self.macro_cfg.get("scoring_overlays", {})
        self.guardrails = self.macro_cfg.get("guardrails", {})
        self.event_windows = self.macro_cfg.get("event_windows", {})

        self.api_key = os.getenv("FINNHUB_KEY")
        self.api_base = "https://finnhub.io/api/v1"

        # Load watchlist from config/assets_watchlist.yaml
        self.watchlist_cfg = load_yaml(repo_root / "config" / "assets_watchlist.yaml")
        self.symbols = self._load_symbols_from_watchlist(self.watchlist_cfg)

        # Optional finnhub-specific config extension
        self.finnhub_cfg = (
            (self.macro_cfg.get("official_sources", {}) or {}).get("finnhub", {})
            or self.cfg.get("finnhub_bridge", {})
            or {}
        )

        self.lookback_days = int(self.finnhub_cfg.get("news_lookback_days", 2))
        self.max_headlines_per_symbol = int(self.finnhub_cfg.get("max_headlines_per_symbol", 8))
        self.rate_sensitive_keywords = [
            "fed", "fomc", "inflation", "cpi", "pce", "jobs", "employment", "rates",
            "yield", "treasury", "tariff", "sanctions", "oil", "energy", "trade", "immigration"
        ]
        self.policy_keywords = [
            "executive order", "presidential", "white house", "treasury sanctions", "ofac", "fomc", "federal reserve"
        ]

        self.cache_dir = repo_root / "logs" / "bridge_cache" / "finnhub_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Public API
    # -------------------------
    def poll(self) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []

        if not self.api_key:
            packets.append(self._error_packet("missing_finnhub_key", symbol=None))
            return packets

        for symbol in self.symbols:
            try:
                packets.extend(self._poll_symbol(symbol))
            except Exception as e:
                packets.append(self._error_packet(f"symbol_poll_error:{e}", symbol=symbol))

        return packets

    # -------------------------
    # Symbol polling
    # -------------------------
    def _poll_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        news = self._get_company_news(symbol)

        if not isinstance(news, list):
            packets.append(self._error_packet("unexpected_news_response_type", symbol=symbol))
            return packets

        items = [n for n in news if isinstance(n, dict)][: self.max_headlines_per_symbol]

        # Aggregate packet (enrichment summary)
        agg_packet = self._build_aggregate_packet(symbol, items)
        packets.append(agg_packet)

        # Optional item-level packets for high-signal headlines
        top_items = self._select_high_signal_items(symbol, items, max_items=3)
        for item in top_items:
            packets.append(self._build_item_packet(symbol, item))

        return packets

    def _get_company_news(self, symbol: str) -> Any:
        now = datetime.now(timezone.utc)
        frm = now - timedelta(days=self.lookback_days)

        params = {
            "symbol": symbol,
            "from": utc_date_str(frm),
            "to": utc_date_str(now),
            "token": self.api_key,
        }
        url = f"{self.api_base}/company-news?{urllib.parse.urlencode(params)}"
        return read_json_url(url)

    # -------------------------
    # Packet builders
    # -------------------------
    def _build_aggregate_packet(self, symbol: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        headline_count = len(items)
        score = self._headline_pressure_score(items)
        rate_sensitive_count = 0
        policy_like_count = 0

        top_headlines = []
        for n in items:
            h = str(n.get("headline", "") or "")
            hs = safe_lower(h)
            if any(k in hs for k in self.rate_sensitive_keywords):
                rate_sensitive_count += 1
            if any(k in hs for k in self.policy_keywords):
                policy_like_count += 1
            if h:
                top_headlines.append(h)
        top_headlines = top_headlines[:5]

        tags = ["osint_alt_enrichment", "cannot_confirm_policy_event"]
        rate_regime_candidate = (rate_sensitive_count > 0 and score >= 0.40)
        if rate_regime_candidate:
            tags.append("rate_regime_shock_candidate")
            tags.append("requires_rate_cross_asset_check")

        event_type = "macro_calendar_update"

        urgency = self._policy_release_urgency_score(event_type)
        source_conf = self._source_confidence("tier_c_osint_alt")
        requires_cross_asset = bool(rate_regime_candidate)

        packet = {
            "schema_version": "macro_policy_event.v1",
            "event_id": f"finnhub-{symbol}-agg-{int(time.time())}",
            "timestamp_utc": iso_now(),

            "source_domain": "finnhub.io",
            "source_url": f"{self.api_base}/company-news",
            "source_feed_url": f"{self.api_base}/company-news",
            "source_tier": "tier_c_osint_alt",
            "source_type": "api",
            "official_source": False,

            "event_type": event_type,
            "headline": f"Finnhub news sentiment snapshot: {symbol}",
            "summary": f"OSINT/alt-tier headline summary for {symbol}; enrichment only.",
            "published_time_utc": None,

            "tags": tags,
            "rate_regime_shock_candidate": rate_regime_candidate,
            "requires_rate_cross_asset_check": requires_cross_asset,
            "official_source_confirmed": False,

            "policy_release_urgency_score": urgency,
            "source_confidence": source_conf,

            "release_window": {
                "release_key": None,
                "release_time_et_hint": None,
                "pre_buffer_minutes": int(self.event_windows.get("pre_release_buffer_minutes_default", 10)),
                "post_buffer_minutes": int(self.event_windows.get("post_release_buffer_minutes_default", 20))
            },

            "parsing_meta": {
                "symbol": symbol,
                "headline_count": headline_count,
                "headline_pressure_score": score,
                "rate_sensitive_headline_count": rate_sensitive_count,
                "policy_like_headline_count": policy_like_count,
                "top_headlines_preview": top_headlines,
                "cannot_confirm_policy_event": True
            },

            "provenance": {
                "bridge": "finnhub_bridge",
                "bridge_version": "0.1.0",
                "normalized_from": "finnhub_company_news",
                "raw_source_url": f"{self.api_base}/company-news"
            },

            "operator_summary": (
                f"osint_alt:{symbol} | headlines={headline_count} | "
                f"pressure={score:.2f} | rate_sensitive={rate_sensitive_count} | "
                f"policy_like={policy_like_count} | cannot_confirm_policy_event=true"
            )
        }
        return packet

    def _build_item_packet(self, symbol: str, item: Dict[str, Any]) -> Dict[str, Any]:
        headline = str(item.get("headline", "") or "")
        summary = str(item.get("summary", "") or "") if item.get("summary") else None
        url = str(item.get("url", "") or f"{self.api_base}/company-news")
        dt_iso = self._finnhub_ts_to_iso(item.get("datetime"))

        hs = safe_lower(headline)
        is_rate_sensitive = any(k in hs for k in self.rate_sensitive_keywords)
        is_policy_like = any(k in hs for k in self.policy_keywords)

        tags = ["osint_alt_enrichment", "headline_item", "cannot_confirm_policy_event"]
        if is_rate_sensitive:
            tags += ["rate_regime_shock_candidate", "requires_rate_cross_asset_check"]
        if is_policy_like:
            tags += ["policy_like_headline_unconfirmed"]

        urgency = self._policy_release_urgency_score("macro_calendar_update")
        source_conf = self._source_confidence("tier_c_osint_alt")

        packet = {
            "schema_version": "macro_policy_event.v1",
            "event_id": f"finnhub-{symbol}-item-{self._stable_id_fragment(symbol, headline, dt_iso)}",
            "timestamp_utc": iso_now(),

            "source_domain": "finnhub.io",
            "source_url": url,
            "source_feed_url": f"{self.api_base}/company-news",
            "source_tier": "tier_c_osint_alt",
            "source_type": "api",
            "official_source": False,

            "event_type": "macro_calendar_update",
            "headline": f"Finnhub headline ({symbol}): {headline}",
            "summary": summary,
            "published_time_utc": dt_iso,

            "tags": tags,
            "rate_regime_shock_candidate": bool(is_rate_sensitive),
            "requires_rate_cross_asset_check": bool(is_rate_sensitive),
            "official_source_confirmed": False,

            "policy_release_urgency_score": urgency,
            "source_confidence": source_conf,

            "release_window": {
                "release_key": None,
                "release_time_et_hint": None,
                "pre_buffer_minutes": int(self.event_windows.get("pre_release_buffer_minutes_default", 10)),
                "post_buffer_minutes": int(self.event_windows.get("post_release_buffer_minutes_default", 20))
            },

            "parsing_meta": {
                "symbol": symbol,
                "finnhub_category": item.get("category"),
                "finnhub_source": item.get("source"),
                "related": item.get("related"),
                "rate_sensitive_headline": is_rate_sensitive,
                "policy_like_headline_unconfirmed": is_policy_like,
                "cannot_confirm_policy_event": True
            },

            "provenance": {
                "bridge": "finnhub_bridge",
                "bridge_version": "0.1.0",
                "normalized_from": "finnhub_company_news",
                "raw_source_url": f"{self.api_base}/company-news"
            },

            "operator_summary": (
                f"osint_alt_headline:{symbol} | rate_sensitive={is_rate_sensitive} | "
                f"policy_like_unconfirmed={is_policy_like} | cannot_confirm_policy_event=true"
            )
        }
        return packet

    # -------------------------
    # Scoring / selection helpers
    # -------------------------
    def _headline_pressure_score(self, items: List[Dict[str, Any]]) -> float:
        if not items:
            return 0.0

        score = 0.0
        count = 0
        for n in items:
            h = safe_lower(n.get("headline", ""))
            if not h:
                continue
            s = 0.0
            if any(k in h for k in self.rate_sensitive_keywords):
                s += 0.45
            if any(k in h for k in ["surge", "plunge", "warning", "cuts", "raises", "sanctions", "tariff", "guidance", "downgrade"]):
                s += 0.25
            if any(k in h for k in ["fed", "fomc", "cpi", "inflation", "jobs", "treasury", "oil", "energy"]):
                s += 0.20
            count += 1
            score += min(s, 1.0)

        if count == 0:
            return 0.0
        return round(min(score / count, 1.0), 4)

    def _select_high_signal_items(self, symbol: str, items: List[Dict[str, Any]], max_items: int = 3) -> List[Dict[str, Any]]:
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for it in items:
            h = safe_lower(it.get("headline", ""))
            if not h:
                continue
            s = 0.0
            if any(k in h for k in self.rate_sensitive_keywords):
                s += 0.6
            if any(k in h for k in self.policy_keywords):
                s += 0.3
            if any(k in h for k in ["earnings", "guidance", "downgrade", "upgrade", "sanctions", "tariff"]):
                s += 0.2
            if s > 0:
                scored.append((s, it))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [it for _, it in scored[:max_items]]

    # -------------------------
    # Meta helpers
    # -------------------------
    def _load_symbols_from_watchlist(self, watchlist_cfg: Dict[str, Any]) -> List[str]:
        symbols: List[str] = []

        # Try flat list format first
        wl = watchlist_cfg.get("watchlist") or watchlist_cfg.get("assets") or []
        if wl:
            items = wl
        else:
            # Section-based format: equity_indices, aviation_travel, etc.
            items = []
            for section in ["equity_indices", "aviation_travel", "travel_hospitality",
                            "supply_chain", "insurance_risk"]:
                items.extend(watchlist_cfg.get(section, []))

        for item in items:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol", "")).strip()
            if not sym:
                continue

            skip_patterns = ["USD", "XAU", "UST", "VIX", "BRN", "BZ=F", "^", "GC=F", "USD/"]
            if any(sym.startswith(p) or p in sym for p in skip_patterns):
                continue

            symbols.append(sym)

        seen = set()
        out = []
        for s in symbols:
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _policy_release_urgency_score(self, event_type: str) -> float:
        defaults = ((self.scoring_overlays.get("policy_release_urgency_score") or {}).get("defaults") or {})
        base = float(defaults.get(event_type, 0.40))
        return min(base, 0.55)

    def _source_confidence(self, tier: str) -> float:
        return float((self.source_tiers.get(tier) or {}).get("confidence_weight", 0.60))

    def _finnhub_ts_to_iso(self, ts: Any) -> Optional[str]:
        try:
            if ts is None:
                return None
            val = float(ts)
            if val > 1e12:
                val /= 1000.0
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            return None

    def _stable_id_fragment(self, symbol: str, headline: str, dt_iso: Optional[str]) -> str:
        raw = f"{symbol}|{headline}|{dt_iso or ''}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # -------------------------
    # Errors
    # -------------------------
    def _error_packet(self, error: str, symbol: Optional[str]) -> Dict[str, Any]:
        return {
            "schema_version": "macro_policy_event_error.v1",
            "timestamp_utc": iso_now(),
            "source_domain": "finnhub.io",
            "source_url": f"{self.api_base}/company-news",
            "source_tier": "tier_c_osint_alt",
            "official_source": False,
            "bridge": "finnhub_bridge",
            "symbol": symbol,
            "error": error
        }


# -----------------------------
# CLI
# -----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel Finnhub Bridge")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true", help="Poll once and print JSON output")
    p.add_argument("--jsonl", action="store_true", help="Emit one JSON packet per line")
    p.add_argument("--loop-seconds", type=int, default=300, help="Polling interval")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = FinnhubBridge(repo_root)

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

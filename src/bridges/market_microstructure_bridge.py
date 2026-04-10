#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Market Microstructure Bridge

Provides per-symbol market microstructure data:
- adv_shares: average daily volume (20-day)
- sigma_daily_pct: daily realized volatility (20-day)
- last_price: latest close

Sources:
- Yahoo Finance (free, no API key) via public chart API
- Finnhub quote API (if FINNHUB_KEY set)

Output feeds into snapshot["market_microstructure"] for the impact budget gate.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
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


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def safe_get_json(url: str, timeout: int = 15) -> Any:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GlobalSentinel-MicrostructureBridge/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


class MarketMicrostructureBridge:
    """
    Fetches ADV and realized volatility for watchlist symbols.
    Populates snapshot["market_microstructure"][symbol].
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.watchlist = load_yaml(repo_root / "config" / "assets_watchlist.yaml")
        self.symbols = self._extract_symbols()
        self.max_symbols = int(os.getenv("GS_MICROSTRUCTURE_MAX_SYMBOLS", "40") or 40)
        self.cache_ttl_sec = int(os.getenv("GS_MICROSTRUCTURE_CACHE_TTL_SEC", "300") or 300)
        self.finnhub_key = os.getenv("FINNHUB_KEY")
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "market_microstructure"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _extract_symbols(self) -> List[str]:
        """Extract all equity symbols from watchlist."""
        syms = set()
        # Pull from ALL watchlist sections with individual items
        list_sections = [
            "equity_indices", "global_indexes", "index_futures", "treasury_futures",
            "commodity_futures", "aviation_travel", "travel_hospitality",
            "supply_chain", "insurance_risk", "fixed_income",
            "gasoline_refining", "defense_military",
        ]
        for section in list_sections:
            for item in self.watchlist.get(section, []):
                s = item.get("symbol")
                if s:
                    syms.add(str(s))
        # Pull from sections that use {symbols: [...]} format
        dict_sections = [
            "cybersecurity", "shipping_maritime", "uranium_nuclear",
            "agriculture_food", "electricity_utilities", "chemicals_petrochemicals",
            "leveraged_volatility", "insurance_reinsurance",
            "oil_majors", "midstream_pipelines", "natgas_lng",
            "ai_infrastructure", "ai_software",
            "ai_disrupted", "robotics_autonomous",
        ]
        for section in dict_sections:
            section_data = self.watchlist.get(section, {})
            if isinstance(section_data, dict):
                for s in section_data.get("symbols", []):
                    syms.add(str(s))
        if syms:
            return sorted(syms)

        # Fallback: expanded_watchlist.yaml (schema: {categories: {name: {symbols: [...]}}})
        try:
            expanded = load_yaml(self.repo_root / "config" / "expanded_watchlist.yaml")
        except Exception:
            expanded = {}
        if isinstance(expanded, dict):
            categories = expanded.get("categories") or {}
            if isinstance(categories, dict):
                for cat in categories.values():
                    if isinstance(cat, dict):
                        for s in (cat.get("symbols") or []):
                            if s:
                                syms.add(str(s))

        # Last-resort fallback: keep the bridge alive with a tiny universe.
        if not syms:
            syms.update({"SPY", "QQQ", "IWM"})

        return sorted(syms)

    def _latest_cache_file(self) -> Optional[Path]:
        try:
            candidates = sorted(self.cache_dir.glob("microstructure_*.json"))
            return candidates[-1] if candidates else None
        except Exception:
            return None

    def _load_cached_results(
        self, max_age_sec: Optional[int] = None
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        cache_file = self._latest_cache_file()
        if cache_file is None or not cache_file.exists():
            return None
        if max_age_sec is not None:
            try:
                age_sec = time.time() - cache_file.stat().st_mtime
                if age_sec > max_age_sec:
                    return None
            except Exception:
                return None
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        parsed: Dict[str, Dict[str, Any]] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, dict):
                parsed[k] = v
        return parsed or None

    def poll(self, symbols: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        """
        Returns: {symbol: {adv_shares, sigma_daily_pct, last_price, source, fresh}}
        """
        target_symbols = symbols or self.symbols

        # Best-effort caching (prevents heavy Yahoo polling every cycle).
        if symbols is None and self.cache_ttl_sec > 0:
            cached = self._load_cached_results(max_age_sec=self.cache_ttl_sec)
            if cached:
                return cached

        if symbols is None and self.max_symbols > 0 and len(target_symbols) > self.max_symbols:
            target_symbols = list(target_symbols)[: self.max_symbols]
        result: Dict[str, Dict[str, Any]] = {}

        for symbol in target_symbols:
            data = self._fetch_yahoo_microstructure(symbol)
            if data:
                result[symbol] = data
            elif self.finnhub_key:
                data = self._fetch_finnhub_quote(symbol)
                if data:
                    result[symbol] = data

        # Cache results (skip empty so we don't poison fallback caching).
        if result:
            self._cache_results(result)
        else:
            cached_any_age = self._load_cached_results(max_age_sec=None)
            if cached_any_age:
                return cached_any_age
        return result

    def build_snapshot_section(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Returns the canonical snapshot["market_microstructure"] dict.
        """
        micro = self.poll(symbols)
        return {
            "timestamp_utc": iso_now(),
            "symbol_count": len(micro),
            "symbols": micro,
        }

    # --- Yahoo Finance (free, no key) ---
    def _fetch_yahoo_microstructure(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Use Yahoo Finance chart API to get 30 days of daily OHLCV.
        Compute ADV(20) and sigma(20) from the data.
        """
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&range=30d"
        )
        data = safe_get_json(url)
        if not data:
            return None

        try:
            chart = data["chart"]["result"][0]
            indicators = chart["indicators"]["quote"][0]
            volumes = indicators.get("volume") or []
            closes = indicators.get("close") or []

            # Filter out None values
            valid_volumes = [v for v in volumes if v is not None and v > 0]
            valid_closes = [c for c in closes if c is not None and c > 0]

            if len(valid_volumes) < 5 or len(valid_closes) < 5:
                return None

            # ADV: 20-day average (or available)
            adv_window = valid_volumes[-20:] if len(valid_volumes) >= 20 else valid_volumes
            adv_shares = sum(adv_window) / len(adv_window)

            # Sigma: 20-day realized volatility from log returns
            close_window = valid_closes[-21:] if len(valid_closes) >= 21 else valid_closes
            if len(close_window) < 2:
                return None

            log_returns = []
            for i in range(1, len(close_window)):
                if close_window[i] > 0 and close_window[i - 1] > 0:
                    log_returns.append(math.log(close_window[i] / close_window[i - 1]))

            if len(log_returns) < 2:
                return None

            mean_ret = sum(log_returns) / len(log_returns)
            variance = sum((r - mean_ret) ** 2 for r in log_returns) / (len(log_returns) - 1)
            sigma_daily = math.sqrt(variance) * 100.0  # as percentage

            last_price = valid_closes[-1]

            return {
                "adv_shares": round(adv_shares, 0),
                "sigma_daily_pct": round(sigma_daily, 4),
                "last_price": round(last_price, 4),
                "source": "yahoo_finance",
                "fresh": True,
                "timestamp_utc": iso_now(),
                "data_points": len(close_window),
            }
        except Exception:
            return None

    # --- Finnhub fallback ---
    def _fetch_finnhub_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not self.finnhub_key:
            return None
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={self.finnhub_key}"
        data = safe_get_json(url)
        if not data or data.get("c") is None:
            return None

        # Finnhub quote doesn't give ADV or sigma directly
        # We can estimate very roughly from the single quote
        last_price = safe_float(data.get("c"), 0.0)
        high = safe_float(data.get("h"), 0.0)
        low = safe_float(data.get("l"), 0.0)

        # Rough intraday vol proxy
        if last_price > 0 and high > 0 and low > 0:
            intraday_range_pct = ((high - low) / last_price) * 100.0
        else:
            intraday_range_pct = 0.0

        return {
            "adv_shares": 0.0,  # Not available from quote endpoint
            "sigma_daily_pct": round(intraday_range_pct * 0.6, 4),  # rough proxy
            "last_price": round(last_price, 4),
            "source": "finnhub_quote",
            "fresh": True,
            "timestamp_utc": iso_now(),
            "data_points": 1,
            "note": "finnhub_quote_only_rough_sigma_estimate",
        }

    # --- Cache ---
    def _cache_results(self, result: Dict[str, Any]):
        if not result:
            return
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"microstructure_{tag}.json"
        cache_file.write_text(json.dumps(result, indent=2), encoding="utf-8")


# --- CLI ---
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--symbols", nargs="*", default=None, help="Override symbols to fetch")
    p.add_argument("--output-json", default=None)
    args = p.parse_args()

    bridge = MarketMicrostructureBridge(Path(args.repo_root).resolve())
    snapshot = bridge.build_snapshot_section(symbols=args.symbols)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    else:
        print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()

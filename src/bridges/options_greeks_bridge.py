#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Options Greeks Bridge

Provides options flow and Greeks-derived signals for key index ETFs:
- Put/Call open interest ratio
- Aggregate gamma exposure estimate
- ATM implied volatility (Vega sensitivity proxy)
- Net Delta of major strikes
- Gamma squeeze risk indicator
- VIX term structure (contango vs backwardation)
- Implied volatility rank (0-100 vs 52-week range)

Sources:
- Alpaca Options API (paper account)
- Fallback: VIX-based heuristics from market microstructure bridge

Output feeds into snapshot["options_greeks"] for the crisis monitor.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)

from src.core.structured_logger import get_logger
from src.core.telemetry import record_metric, start_span


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def safe_get_json(url: str, headers: Optional[Dict[str, str]] = None,
                  timeout: int = 15) -> Any:
    """HTTP GET returning parsed JSON or None on any error. Rate-limited for Alpaca calls."""
    try:
        hdrs = {"User-Agent": "GlobalSentinel-OptionsGreeksBridge/1.0"}
        if headers:
            hdrs.update(headers)

        # Apply rate limiting for Alpaca API calls
        alpaca_key = hdrs.get("APCA-API-KEY-ID")
        if alpaca_key:
            try:
                from src.utils.rate_limiter import get_limiter
                get_limiter(alpaca_key, max_rpm=180).acquire(timeout=30.0)
            except ImportError:
                pass

        def _do():
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))

        # Retry with backoff for Alpaca calls
        if alpaca_key:
            try:
                from src.utils.rate_limiter import retry_with_backoff
                return retry_with_backoff(_do, max_retries=2, base_delay=1.0)
            except ImportError:
                pass

        return _do()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core symbols for options analysis
# ---------------------------------------------------------------------------
OPTIONS_FOCUS_SYMBOLS = ["SPY", "QQQ", "IWM"]
VIX_SYMBOL = "VIX"
DEFAULT_SOURCE_TIER = "tier_3_research"
DEFAULT_TRUST_WEIGHT = 0.5
DEFAULT_CACHE_TTL_MINUTES = 15


logger = get_logger("options_greeks_bridge")


class OptionsGreeksBridge:
    """
    Fetches options Greeks data and derives risk signals for crisis monitoring.

    Primary source: Alpaca Options API (paper).
    Fallback: VIX-based heuristics when options data is unavailable.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.watchlist = load_yaml(repo_root / "config" / "assets_watchlist.yaml")
        self.trust_hierarchy = load_yaml(repo_root / "config" / "data_trust_hierarchy.yaml")
        self.freshness_policy = load_yaml(repo_root / "config" / "freshness_policy.yaml")

        # Alpaca credentials
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )
        self.data_url = "https://data.alpaca.markets"

        # Cache directory
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "options_greeks"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.source_tier, self.trust_weight = self._resolve_trust_metadata()

    # ------------------------------------------------------------------
    # Alpaca auth header
    # ------------------------------------------------------------------
    def _alpaca_headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }

    def _has_alpaca_creds(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _resolve_trust_metadata(self) -> tuple[str, float]:
        tiers = (self.trust_hierarchy or {}).get("tiers") or {}
        for tier_name, tier_config in tiers.items():
            sources = {str(item) for item in (tier_config.get("sources") or [])}
            if {"options_greeks", "options_greeks_bridge"} & sources:
                return tier_name, safe_float(tier_config.get("weight"), DEFAULT_TRUST_WEIGHT)
        return DEFAULT_SOURCE_TIER, DEFAULT_TRUST_WEIGHT

    def _cache_ttl_minutes(self) -> int:
        sources = (self.freshness_policy or {}).get("sources") or {}
        for source_key in ("options_greeks_bridge", "options_greeks"):
            policy = sources.get(source_key)
            if isinstance(policy, dict) and policy.get("freshness_ttl_minutes"):
                return int(policy["freshness_ttl_minutes"])
        return DEFAULT_CACHE_TTL_MINUTES

    def _load_latest_cached_result(self) -> Optional[Dict[str, Any]]:
        cache_files = sorted(self.cache_dir.glob("options_greeks_*.json"), reverse=True)
        if not cache_files:
            return None

        max_age_minutes = self._cache_ttl_minutes()
        now = datetime.now(timezone.utc)
        for cache_file in cache_files:
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
                ts_raw = payload.get("timestamp_utc")
                if not ts_raw:
                    continue
                ts = datetime.fromisoformat(str(ts_raw))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_minutes = (now - ts).total_seconds() / 60.0
                if age_minutes > max_age_minutes:
                    continue
                symbols = payload.get("symbols") or {}
                if not any(
                    isinstance(item, dict) and item.get("fresh")
                    for item in symbols.values()
                ):
                    continue
                payload = dict(payload)
                payload["cache_used"] = True
                payload["cache_file"] = str(cache_file)
                payload["cache_age_minutes"] = round(age_minutes, 2)
                return payload
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Fetch underlying last price (needed to find ATM strikes)
    # ------------------------------------------------------------------
    def _fetch_underlying_price(self, symbol: str) -> Optional[float]:
        """Get latest price via Alpaca snapshot or Yahoo fallback."""
        if self._has_alpaca_creds():
            url = f"{self.data_url}/v2/stocks/{symbol}/snapshot"
            data = safe_get_json(url, headers=self._alpaca_headers())
            if data and "latestTrade" in data:
                return safe_float(data["latestTrade"].get("p"))

        # Yahoo fallback
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&range=1d"
        )
        data = safe_get_json(url)
        if data:
            try:
                closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                valid = [c for c in closes if c is not None]
                if valid:
                    return float(valid[-1])
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Fetch VIX price (for fallback heuristics and term structure)
    # ------------------------------------------------------------------
    def _fetch_vix_price(self) -> Optional[float]:
        """Get current VIX level via Yahoo Finance (^VIX)."""
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
            "?interval=1d&range=5d"
        )
        data = safe_get_json(url)
        if not data:
            return None
        try:
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            valid = [c for c in closes if c is not None]
            return float(valid[-1]) if valid else None
        except Exception:
            return None

    def _fetch_vix_history(self, days: int = 252) -> List[float]:
        """Get VIX closing prices for IV rank calculation."""
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
            f"?interval=1d&range=1y"
        )
        data = safe_get_json(url)
        if not data:
            return []
        try:
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            return [c for c in closes if c is not None and c > 0]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Fetch VIX futures for term structure (via VIX ETFs as proxy)
    # ------------------------------------------------------------------
    def _fetch_vix_term_structure(self) -> Dict[str, Any]:
        """
        Approximate VIX term structure using VIX spot vs VIXM (mid-term VIX ETF).
        Contango: VIXM > VIX spot (normal).
        Backwardation: VIX spot > VIXM (fear/stress).
        """
        vix_spot = self._fetch_vix_price()
        if vix_spot is None:
            return {"available": False}

        # Use VIXM (mid-term VIX futures ETF) as a term structure proxy
        vixm_price = self._fetch_underlying_price("VIXM")

        result: Dict[str, Any] = {
            "available": True,
            "vix_spot": round(vix_spot, 2),
        }

        if vixm_price and vixm_price > 0:
            # We can't directly compare prices (different scales), but
            # we track the ratio change. For a simpler signal, use VIX level.
            result["vixm_price"] = round(vixm_price, 2)

        # Heuristic term structure from VIX level
        if vix_spot < 18:
            result["structure"] = "contango"
            result["signal"] = "normal"
        elif vix_spot < 25:
            result["structure"] = "flat"
            result["signal"] = "caution"
        elif vix_spot < 35:
            result["structure"] = "backwardation_likely"
            result["signal"] = "fear"
        else:
            result["structure"] = "deep_backwardation"
            result["signal"] = "panic"

        return result

    # ------------------------------------------------------------------
    # Alpaca options chain fetch
    # ------------------------------------------------------------------
    def _fetch_option_chain(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch option contracts for a symbol via Alpaca.
        GET /v2/options/contracts?underlying_symbol=SPY&status=active&type=...
        """
        if not self._has_alpaca_creds():
            return None

        # Get contracts expiring within the next 30 days
        today = datetime.now(timezone.utc).date()
        exp_start = today.isoformat()
        exp_end = (today + timedelta(days=30)).isoformat()

        url = (
            f"{self.base_url}/v2/options/contracts"
            f"?underlying_symbol={symbol}"
            f"&status=active"
            f"&expiration_date_gte={exp_start}"
            f"&expiration_date_lte={exp_end}"
            f"&limit=100"
        )
        return safe_get_json(url, headers=self._alpaca_headers())

    def _fetch_options_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch options snapshots from Alpaca data API.
        GET /v1beta1/options/snapshots/{underlying_symbol}
        """
        if not self._has_alpaca_creds():
            return None

        url = (
            f"{self.data_url}/v1beta1/options/snapshots/{symbol}"
            f"?feed=indicative&limit=100"
        )
        return safe_get_json(url, headers=self._alpaca_headers())

    # ------------------------------------------------------------------
    # Analyze options data for a single symbol
    # ------------------------------------------------------------------
    def _analyze_symbol_options(self, symbol: str) -> Dict[str, Any]:
        """
        Attempt to fetch and analyze options data for a symbol.
        Returns metrics dict or fallback heuristics.
        """
        result: Dict[str, Any] = {
            "symbol": symbol,
            "timestamp_utc": iso_now(),
            "source": "fallback_heuristic",
            "fresh": False,
        }

        underlying_price = self._fetch_underlying_price(symbol)
        if underlying_price:
            result["underlying_price"] = round(underlying_price, 2)

        # Try Alpaca options snapshot first
        snapshot_data = self._fetch_options_snapshot(symbol)
        if snapshot_data and "snapshots" in snapshot_data:
            return self._parse_options_snapshot(
                symbol, snapshot_data, underlying_price, result
            )

        # Try Alpaca options contracts endpoint
        chain_data = self._fetch_option_chain(symbol)
        if chain_data and "option_contracts" in chain_data:
            contracts = chain_data["option_contracts"]
            if contracts:
                return self._parse_option_contracts(
                    symbol, contracts, underlying_price, result
                )

        # Fallback: VIX-based heuristics
        return self._build_fallback_metrics(symbol, underlying_price, result)

    def _parse_options_snapshot(
        self, symbol: str, snapshot_data: Dict, underlying_price: Optional[float],
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parse Alpaca options snapshot data into Greeks metrics."""
        snapshots = snapshot_data.get("snapshots", {})
        if not snapshots:
            return self._build_fallback_metrics(symbol, underlying_price, result)

        total_call_oi = 0
        total_put_oi = 0
        total_call_gamma = 0.0
        total_put_gamma = 0.0
        total_call_delta = 0.0
        total_put_delta = 0.0
        iv_values: List[float] = []

        for contract_symbol, snap in snapshots.items():
            greeks = snap.get("greeks", {})
            quote = snap.get("latestQuote", {})

            # Determine if call or put from contract symbol
            is_call = "C" in contract_symbol.split(symbol)[-1][:3] if symbol in contract_symbol else True

            oi = safe_float(snap.get("openInterest", 0))
            delta = safe_float(greeks.get("delta", 0))
            gamma = safe_float(greeks.get("gamma", 0))
            iv = safe_float(greeks.get("impliedVolatility", 0))

            if is_call:
                total_call_oi += oi
                total_call_gamma += gamma * oi * 100  # per contract = 100 shares
                total_call_delta += delta * oi * 100
            else:
                total_put_oi += oi
                total_put_gamma += gamma * oi * 100
                total_put_delta += delta * oi * 100

            if iv > 0:
                iv_values.append(iv)

        # Compute metrics
        total_oi = total_call_oi + total_put_oi
        pc_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0
        net_gamma = total_call_gamma - total_put_gamma
        net_delta = total_call_delta + total_put_delta  # put delta is already negative
        avg_iv = (sum(iv_values) / len(iv_values) * 100) if iv_values else 0.0

        result.update({
            "source": "alpaca_options_snapshot",
            "fresh": True,
            "total_call_oi": int(total_call_oi),
            "total_put_oi": int(total_put_oi),
            "total_open_interest": int(total_oi),
            "put_call_ratio": round(pc_ratio, 4),
            "net_gamma_exposure": round(net_gamma, 2),
            "net_delta_exposure": round(net_delta, 2),
            "avg_implied_volatility_pct": round(avg_iv, 2),
            "contracts_analyzed": len(snapshots),
        })

        # Derived signals
        result["gamma_squeeze_risk"] = self._compute_gamma_squeeze_risk(
            pc_ratio, net_gamma, total_call_oi, avg_iv
        )
        result["put_call_signal"] = self._interpret_put_call_ratio(pc_ratio)

        if not self._has_substantive_options_data(result):
            fallback = self._build_fallback_metrics(symbol, underlying_price, dict(result))
            fallback["note"] = "alpaca_snapshot_empty_using_vix_heuristic"
            fallback["source_detail"] = "alpaca_snapshot_empty"
            fallback["contracts_analyzed"] = len(snapshots)
            return fallback

        return result

    def _parse_option_contracts(
        self, symbol: str, contracts: List[Dict], underlying_price: Optional[float],
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parse Alpaca option contracts list into basic OI metrics."""
        total_call_oi = 0
        total_put_oi = 0

        for contract in contracts:
            oi = safe_float(contract.get("open_interest", 0))
            ctype = contract.get("type", "").lower()
            if ctype == "call":
                total_call_oi += oi
            elif ctype == "put":
                total_put_oi += oi

        total_oi = total_call_oi + total_put_oi
        pc_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0

        result.update({
            "source": "alpaca_options_contracts",
            "fresh": True,
            "total_call_oi": int(total_call_oi),
            "total_put_oi": int(total_put_oi),
            "total_open_interest": int(total_oi),
            "put_call_ratio": round(pc_ratio, 4),
            "net_gamma_exposure": 0.0,  # Not available from contracts endpoint
            "net_delta_exposure": 0.0,
            "avg_implied_volatility_pct": 0.0,
            "contracts_analyzed": len(contracts),
        })

        result["gamma_squeeze_risk"] = self._compute_gamma_squeeze_risk(
            pc_ratio, 0.0, total_call_oi, 0.0
        )
        result["put_call_signal"] = self._interpret_put_call_ratio(pc_ratio)

        if not self._has_substantive_options_data(result):
            fallback = self._build_fallback_metrics(symbol, underlying_price, dict(result))
            fallback["note"] = "alpaca_contracts_empty_using_vix_heuristic"
            fallback["source_detail"] = "alpaca_contracts_empty"
            fallback["contracts_analyzed"] = len(contracts)
            return fallback

        return result

    def _build_fallback_metrics(
        self, symbol: str, underlying_price: Optional[float],
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate heuristic metrics when Alpaca options API is unavailable.
        Uses VIX level as proxy for overall options market stress.
        """
        vix = self._fetch_vix_price()

        if vix is not None:
            result.update({
                "source": "vix_heuristic",
                "fresh": True,
                "note": "options_api_unavailable_using_vix_heuristics",
                "vix_proxy": round(vix, 2),
            })
        else:
            result.update({
                "source": "fallback_heuristic",
                "fresh": False,
                "note": "no_options_or_vix_data_available",
            })

        if vix is not None:

            # Heuristic put/call ratio from VIX
            # Higher VIX => more put buying => higher P/C ratio
            if vix < 15:
                est_pc = 0.65
            elif vix < 20:
                est_pc = 0.80
            elif vix < 25:
                est_pc = 0.95
            elif vix < 30:
                est_pc = 1.10
            elif vix < 40:
                est_pc = 1.30
            else:
                est_pc = 1.60

            result["put_call_ratio"] = round(est_pc, 4)
            result["put_call_signal"] = self._interpret_put_call_ratio(est_pc)
            result["avg_implied_volatility_pct"] = round(vix, 2)

            # Gamma squeeze heuristic
            if vix > 35:
                result["gamma_squeeze_risk"] = "high"
            elif vix > 25:
                result["gamma_squeeze_risk"] = "elevated"
            elif vix < 15:
                result["gamma_squeeze_risk"] = "low_but_complacent"
            else:
                result["gamma_squeeze_risk"] = "normal"
        else:
            result["put_call_ratio"] = 0.0
            result["gamma_squeeze_risk"] = "unknown"
            result["put_call_signal"] = "no_data"

        return result

    # ------------------------------------------------------------------
    # Derived signal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_gamma_squeeze_risk(
        pc_ratio: float, net_gamma: float, call_oi: float, avg_iv: float
    ) -> str:
        """
        Assess gamma squeeze risk.
        High risk when: low P/C ratio + high call OI concentration + high gamma.
        """
        risk_score = 0

        # Low P/C => lots of calls => dealers short gamma
        if pc_ratio < 0.6:
            risk_score += 3
        elif pc_ratio < 0.75:
            risk_score += 2
        elif pc_ratio < 0.9:
            risk_score += 1

        # High absolute gamma exposure
        if abs(net_gamma) > 1_000_000:
            risk_score += 3
        elif abs(net_gamma) > 500_000:
            risk_score += 2
        elif abs(net_gamma) > 100_000:
            risk_score += 1

        # High call OI
        if call_oi > 500_000:
            risk_score += 2
        elif call_oi > 200_000:
            risk_score += 1

        # Low IV (compressed vol => snap risk)
        if 0 < avg_iv < 15:
            risk_score += 2
        elif 0 < avg_iv < 20:
            risk_score += 1

        if risk_score >= 7:
            return "high"
        elif risk_score >= 4:
            return "elevated"
        elif risk_score >= 2:
            return "moderate"
        return "low"

    @staticmethod
    def _interpret_put_call_ratio(pc_ratio: float) -> str:
        """Interpret P/C ratio into a directional signal."""
        if pc_ratio <= 0:
            return "no_data"
        elif pc_ratio < 0.7:
            return "bullish_complacency"
        elif pc_ratio < 0.9:
            return "neutral"
        elif pc_ratio < 1.1:
            return "balanced"
        elif pc_ratio < 1.3:
            return "bearish_hedging"
        else:
            return "extreme_fear"

    # ------------------------------------------------------------------
    # Implied Volatility Rank (0-100)
    # ------------------------------------------------------------------
    def _compute_iv_rank(self) -> Dict[str, Any]:
        """
        IV Rank: where current VIX sits relative to its 52-week range.
        IV Rank = (current - 52w_low) / (52w_high - 52w_low) * 100
        """
        vix_history = self._fetch_vix_history()
        vix_current = self._fetch_vix_price()

        if not vix_history or vix_current is None:
            return {"available": False, "iv_rank": None}

        low_52w = min(vix_history)
        high_52w = max(vix_history)
        range_52w = high_52w - low_52w

        if range_52w <= 0:
            return {"available": True, "iv_rank": 50, "note": "flat_range"}

        iv_rank = ((vix_current - low_52w) / range_52w) * 100
        iv_rank = max(0, min(100, iv_rank))

        return {
            "available": True,
            "iv_rank": round(iv_rank, 1),
            "vix_current": round(vix_current, 2),
            "vix_52w_low": round(low_52w, 2),
            "vix_52w_high": round(high_52w, 2),
            "interpretation": (
                "low_vol" if iv_rank < 25 else
                "below_average" if iv_rank < 45 else
                "average" if iv_rank < 55 else
                "above_average" if iv_rank < 75 else
                "high_vol"
            ),
        }

    @staticmethod
    def _has_substantive_options_data(result: Dict[str, Any]) -> bool:
        """Treat all-zero option snapshots as degraded rather than meaningful live data."""
        return any(
            safe_float(result.get(field), 0.0) > 0.0
            for field in (
                "total_call_oi",
                "total_put_oi",
                "total_open_interest",
                "put_call_ratio",
                "net_gamma_exposure",
                "net_delta_exposure",
                "avg_implied_volatility_pct",
            )
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def poll(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Fetch options Greeks data for target symbols.

        Returns: {
            "symbols": {symbol: {metrics...}, ...},
            "vix_term_structure": {...},
            "implied_vol_rank": {...},
            "aggregate_signals": {...},
            "timestamp_utc": "...",
            "source_priority": "alpaca | fallback_heuristic",
        }
        """
        with start_span("bridge.fetch.options_greeks", bridge_name="options_greeks_bridge"):
            target_symbols = symbols or OPTIONS_FOCUS_SYMBOLS
            symbol_data: Dict[str, Any] = {}

            for sym in target_symbols:
                symbol_data[sym] = self._analyze_symbol_options(sym)

            vix_term = self._fetch_vix_term_structure()
            iv_rank = self._compute_iv_rank()
            aggregate = self._compute_aggregate_signals(symbol_data, vix_term, iv_rank)

            sources = {d.get("source", "unknown") for d in symbol_data.values()}
            if "alpaca_options_snapshot" in sources:
                primary_source = "alpaca_options_snapshot"
            elif "alpaca_options_contracts" in sources:
                primary_source = "alpaca_options_contracts"
            elif "vix_heuristic" in sources:
                primary_source = "vix_heuristic"
            else:
                primary_source = "fallback_heuristic"

            result = {
                "timestamp_utc": iso_now(),
                "source_priority": primary_source,
                "bridge_name": "options_greeks_bridge",
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "symbols": symbol_data,
                "vix_term_structure": vix_term,
                "implied_vol_rank": iv_rank,
                "aggregate_signals": aggregate,
            }

            fresh_symbols = sum(1 for item in symbol_data.values() if item.get("fresh"))
            if fresh_symbols == 0:
                cached = self._load_latest_cached_result()
                if cached is not None:
                    record_metric("bridge_fetch_success_total", 1, bridge_name="options_greeks_bridge", mode="cache")
                    record_metric("bridge_packet_throughput_total", 1, bridge_name="options_greeks_bridge", mode="cache")
                    logger.warning(
                        "options_greeks_live_unavailable_using_cache",
                        bridge_name="options_greeks_bridge",
                        cache_file=cached.get("cache_file"),
                        cache_age_minutes=cached.get("cache_age_minutes"),
                    )
                    return cached

                record_metric("bridge_fetch_failure_total", 1, bridge_name="options_greeks_bridge", mode="live")
                logger.warning(
                    "options_greeks_poll_degraded",
                    bridge_name="options_greeks_bridge",
                    primary_source=primary_source,
                    fresh_symbols=fresh_symbols,
                )
            else:
                record_metric("bridge_fetch_success_total", 1, bridge_name="options_greeks_bridge", mode="live")
                record_metric("bridge_packet_throughput_total", 1, bridge_name="options_greeks_bridge", mode="live")
                logger.info(
                    "bridge_poll_success",
                    bridge_name="options_greeks_bridge",
                    primary_source=primary_source,
                    fresh_symbols=fresh_symbols,
                )

            self._cache_results(result)
            return result

    def build_snapshot_section(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Returns the canonical snapshot["options_greeks"] dict
        for the crisis monitor.
        """
        data = self.poll(symbols)
        snapshot = self._build_snapshot_from_poll_data(data)
        if snapshot.get("fresh"):
            return snapshot

        cached_snapshot = self.load_latest_cached_snapshot()
        if cached_snapshot and cached_snapshot.get("fresh"):
            cached_snapshot = dict(cached_snapshot)
            cached_snapshot["source"] = cached_snapshot.get("source", data.get("source_priority", "cache"))
            cached_snapshot["source_detail"] = "cached_snapshot_fallback"
            return cached_snapshot

        return snapshot

    def _build_snapshot_from_poll_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        per_symbol = data["symbols"]
        aggregate = data["aggregate_signals"]
        any_fresh = any(
            s.get("fresh", False) for s in per_symbol.values()
        )
        aggregate_gamma = round(
            sum(safe_float(entry.get("net_gamma_exposure"), 0.0) for entry in per_symbol.values()),
            2,
        )
        avg_iv = [
            safe_float(entry.get("avg_implied_volatility_pct"), 0.0)
            for entry in per_symbol.values()
            if safe_float(entry.get("avg_implied_volatility_pct"), 0.0) > 0
        ]
        return {
            "timestamp_utc": data["timestamp_utc"],
            "source": data["source_priority"],
            "primary_source": data["source_priority"],
            "bridge_name": "options_greeks_bridge",
            "source_tier": data.get("source_tier", self.source_tier),
            "trust_weight": data.get("trust_weight", self.trust_weight),
            "fresh": any_fresh,
            "symbol_count": len(data["symbols"]),
            "symbols": per_symbol,
            "per_symbol": per_symbol,
            "put_call_ratio": aggregate.get("avg_put_call_ratio", 0.0),
            "gamma_squeeze_risk": aggregate.get("max_gamma_squeeze_risk", "unknown"),
            "aggregate_gamma_exposure": aggregate_gamma,
            "net_gamma_exposure": aggregate_gamma,
            "avg_implied_volatility_pct": round(sum(avg_iv) / len(avg_iv), 2) if avg_iv else 0.0,
            "open_interest": {
                symbol: int(safe_float(metrics.get("total_open_interest"), 0))
                for symbol, metrics in per_symbol.items()
            },
            "vix_term_structure": (data["vix_term_structure"] or {}).get("structure", "unknown"),
            "vix_term_structure_detail": data["vix_term_structure"],
            "implied_vol_rank": data["implied_vol_rank"],
            "aggregate_signals": aggregate,
            "cache_used": bool(data.get("cache_used")),
            "cache_age_minutes": data.get("cache_age_minutes"),
        }

    def fetch(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """Compatibility alias used by some bridge health checks."""
        return self.build_snapshot_section(symbols=symbols)

    def load_latest_cached_snapshot(self) -> Optional[Dict[str, Any]]:
        cached = self._load_latest_cached_result()
        if not cached:
            return None
        try:
            return self._build_snapshot_from_poll_data(cached)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Aggregate signals
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_aggregate_signals(
        symbol_data: Dict[str, Any],
        vix_term: Dict[str, Any],
        iv_rank: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Combine per-symbol metrics into portfolio-level risk signals."""
        pc_ratios = []
        gamma_risks = []

        for sym, data in symbol_data.items():
            pc = safe_float(data.get("put_call_ratio"), 0.0)
            if pc > 0:
                pc_ratios.append(pc)
            gr = data.get("gamma_squeeze_risk", "unknown")
            gamma_risks.append(gr)

        avg_pc = (sum(pc_ratios) / len(pc_ratios)) if pc_ratios else 0.0

        # Worst-case gamma risk
        risk_order = {"high": 4, "elevated": 3, "moderate": 2,
                      "low_but_complacent": 1.5, "low": 1, "normal": 1,
                      "unknown": 0}
        max_gamma_risk = max(gamma_risks, key=lambda r: risk_order.get(r, 0)) \
            if gamma_risks else "unknown"

        # Overall options risk level
        risk_level = "normal"
        ivr = safe_float(iv_rank.get("iv_rank"), 50.0)
        vix_sig = vix_term.get("signal", "normal")

        if max_gamma_risk in ("high",) or avg_pc > 1.3 or ivr > 80:
            risk_level = "high"
        elif max_gamma_risk in ("elevated",) or avg_pc > 1.1 or ivr > 65 or \
                vix_sig in ("fear", "panic"):
            risk_level = "elevated"
        elif avg_pc < 0.65 or ivr < 15:
            risk_level = "complacent"

        return {
            "avg_put_call_ratio": round(avg_pc, 4),
            "max_gamma_squeeze_risk": max_gamma_risk,
            "options_risk_level": risk_level,
            "vix_signal": vix_sig,
            "iv_rank_value": ivr,
        }

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _cache_results(self, result: Dict[str, Any]):
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"options_greeks_{tag}.json"
        try:
            cache_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception:
            pass  # Non-fatal: caching failure should not break the bridge


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Options Greeks Bridge - fetch options flow and Greeks signals"
    )
    p.add_argument("--repo-root", default=".")
    p.add_argument("--symbols", nargs="*", default=None,
                   help="Override symbols (default: SPY QQQ IWM)")
    p.add_argument("--output-json", default=None,
                   help="Write output to JSON file instead of stdout")
    args = p.parse_args()

    bridge = OptionsGreeksBridge(Path(args.repo_root).resolve())
    snapshot = bridge.build_snapshot_section(symbols=args.symbols)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    else:
        print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()

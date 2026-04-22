#!/usr/bin/env python3
"""
Global Sentinel V5.5 — FX (Forex) Monitor Bridge

Monitors major FX pairs via FRED daily exchange rate series, computes
momentum / mean-reversion / carry-trade signals, and correlates with
geopolitical risk scores from crisis_monitor.

Supported pairs:
  EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CHF, USD/CAD

Data source:
  FRED API (free, official) — daily exchange rate series:
    DEXUSEU (EUR/USD), DEXUSUK (GBP/USD), DEXJPUS (JPY/USD),
    DEXUSAL (AUD/USD), DEXSZUS (CHF/USD), DEXCAUS (CAD/USD)

Output:
  Standardized signal packets for the strategy orchestrator.
  Shadow / intelligence only — no direct execution.

Execution routing:
  When signals are acted upon, the multi_broker_router routes
  asset_class="forex" to IBKR (IDEALPRO) or TastyTrade FX.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("global_sentinel.fx_bridge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_DIR = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_PATH = OUTPUT_DIR / "fx_monitor.json"
CACHE_DIR = REPO_ROOT / "logs" / "bridge_cache" / "fx_bridge"

# FRED series IDs for major FX pairs
# Note: FRED quotes these as foreign currency per USD or USD per foreign,
# so we normalize everything to standard market convention.
FRED_FX_SERIES = {
    "EURUSD": {"series_id": "DEXUSEU", "invert": False, "label": "EUR/USD"},
    "GBPUSD": {"series_id": "DEXUSUK", "invert": False, "label": "GBP/USD"},
    "USDJPY": {"series_id": "DEXJPUS", "invert": False, "label": "USD/JPY"},
    "AUDUSD": {"series_id": "DEXUSAL", "invert": False, "label": "AUD/USD"},
    "USDCHF": {"series_id": "DEXSZUS", "invert": False, "label": "USD/CHF"},
    "USDCAD": {"series_id": "DEXCAUS", "invert": False, "label": "USD/CAD"},
}

# Approximate central bank policy rates for carry trade ranking (updated periodically)
# These are used as a baseline; the bridge will attempt to fetch from FRED if available.
POLICY_RATES = {
    "USD": 5.25,
    "EUR": 4.50,
    "GBP": 5.25,
    "JPY": 0.10,
    "AUD": 4.35,
    "CHF": 1.75,
    "CAD": 5.00,
}

# FRED series for policy rates (attempt to fetch live)
FRED_RATE_SERIES = {
    "USD": "DFEDTARU",   # Fed funds target upper
    "EUR": "ECBMLFR",    # ECB marginal lending facility rate
    "GBP": "IUDSOIA",    # Bank of England rate (proxy)
    "JPY": "IRSTCB01JPM156N",  # Japan policy rate
}

# Moving average window for mean-reversion signal
MA_WINDOW = 20

# FRED API rate limiter integration
try:
    from src.utils.fred_rate_limiter import acquire_fred_token, save_fred_state
    _HAS_FRED_LIMITER = True
except ImportError:
    _HAS_FRED_LIMITER = False


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def _fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    """Fetch JSON from a URL with a standard user-agent."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GlobalSentinelFXBridge/1.0 (+shadow-mode)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


# ---------------------------------------------------------------------------
# FX Bridge
# ---------------------------------------------------------------------------

class FXBridge:
    """
    FX monitor bridge: fetches FRED exchange rate data, computes trading
    signals, and emits standardized packets for the strategy orchestrator.
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT
        self.api_key = os.getenv("FRED_API_KEY", "")
        self.api_base = "https://api.stlouisfed.org/fred"

        self.cache_dir = self.repo_root / "logs" / "bridge_cache" / "fx_bridge"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.output_dir = self.repo_root / "data" / "quantum_feed"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._rate_cache: Dict[str, List[Dict[str, Any]]] = {}

    # -----------------------------------------------------------------
    # FRED API
    # -----------------------------------------------------------------

    def _fred_api_get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call FRED API with rate limiting."""
        if _HAS_FRED_LIMITER:
            if not acquire_fred_token(timeout=5.0):
                raise RuntimeError("FRED rate limit exceeded, skipping")
            save_fred_state()

        q = dict(params)
        q["file_type"] = "json"
        if self.api_key:
            q["api_key"] = self.api_key
        url = f"{self.api_base}/{endpoint}?{urllib.parse.urlencode(q)}"
        return _fetch_json(url)

    def _fetch_series(self, series_id: str, limit: int = 60) -> List[Dict[str, Any]]:
        """Fetch recent observations for a FRED series."""
        try:
            data = self._fred_api_get("series/observations", {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": limit,
            })
            observations = data.get("observations", [])
            # Filter out missing values and reverse to chronological order
            cleaned = [
                {"date": o["date"], "value": safe_float(o.get("value"))}
                for o in observations
                if safe_float(o.get("value")) is not None
            ]
            cleaned.reverse()

            # Cache to disk for fallback
            cache_path = self.cache_dir / f"{series_id}.json"
            cache_path.write_text(json.dumps(cleaned, indent=2))

            return cleaned
        except Exception as e:
            logger.warning("FRED fetch failed for %s: %s — trying cache", series_id, e)
            return self._load_cached_series(series_id)

    def _load_cached_series(self, series_id: str) -> List[Dict[str, Any]]:
        """Load cached series data from disk."""
        cache_path = self.cache_dir / f"{series_id}.json"
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text())
                logger.info("Loaded cached data for %s (%d observations)", series_id, len(data))
                return data
            except Exception:
                pass
        logger.warning("No cached data for %s", series_id)
        return []

    # -----------------------------------------------------------------
    # Signal computation
    # -----------------------------------------------------------------

    def _compute_momentum(self, values: List[float], periods: int = 5) -> Optional[float]:
        """Rate of change over N periods (percentage)."""
        if len(values) < periods + 1:
            return None
        current = values[-1]
        past = values[-(periods + 1)]
        if past == 0:
            return None
        return ((current - past) / past) * 100

    def _compute_ma_distance(self, values: List[float], window: int = MA_WINDOW) -> Optional[float]:
        """Distance from N-period moving average (percentage)."""
        if len(values) < window:
            return None
        ma = sum(values[-window:]) / window
        if ma == 0:
            return None
        return ((values[-1] - ma) / ma) * 100

    def _compute_volatility(self, values: List[float], window: int = 20) -> Optional[float]:
        """Rolling standard deviation of returns over N periods."""
        if len(values) < window + 1:
            return None
        returns = []
        for i in range(len(values) - window, len(values)):
            if values[i - 1] != 0:
                returns.append((values[i] - values[i - 1]) / values[i - 1])
        if not returns:
            return None
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return variance ** 0.5

    def _carry_differential(self, pair: str) -> Optional[float]:
        """Interest rate differential for a pair (base - quote)."""
        # Extract base and quote currencies
        if pair.startswith("USD"):
            base, quote = "USD", pair[3:]
        elif pair.endswith("USD"):
            base, quote = pair[:3], "USD"
        else:
            return None

        base_rate = POLICY_RATES.get(base)
        quote_rate = POLICY_RATES.get(quote)
        if base_rate is None or quote_rate is None:
            return None
        return base_rate - quote_rate

    def _classify_signal(
        self,
        momentum_5d: Optional[float],
        momentum_20d: Optional[float],
        ma_distance: Optional[float],
        carry_diff: Optional[float],
        geo_risk_score: Optional[float],
        pair: str,
    ) -> Tuple[str, float]:
        """
        Classify signal direction and strength.

        Returns (direction, strength) where:
          - direction: "bullish", "bearish", or "neutral"
          - strength: 0.0 to 1.0
        """
        scores: List[float] = []

        # Momentum signal: positive momentum = bullish for the pair
        if momentum_5d is not None:
            if abs(momentum_5d) > 0.1:
                scores.append(min(max(momentum_5d / 2.0, -1.0), 1.0))

        if momentum_20d is not None:
            if abs(momentum_20d) > 0.2:
                scores.append(min(max(momentum_20d / 4.0, -1.0), 1.0))

        # Mean reversion signal: far from MA = contrarian signal
        if ma_distance is not None:
            if abs(ma_distance) > 1.0:
                # Overshoot: expect mean reversion (negative of distance)
                scores.append(min(max(-ma_distance / 3.0, -1.0), 1.0))

        # Carry trade signal: positive carry = bullish
        if carry_diff is not None:
            scores.append(min(max(carry_diff / 5.0, -1.0), 1.0))

        # Geopolitical risk: boosts safe-haven pairs (JPY, CHF), hurts risk-on (AUD)
        if geo_risk_score is not None and geo_risk_score > 0.5:
            risk_factor = (geo_risk_score - 0.5) * 2  # 0-1 scale
            if pair in ("USDJPY",):
                # High geo risk -> JPY strengthens -> USDJPY falls
                scores.append(-risk_factor * 0.5)
            elif pair in ("USDCHF",):
                # High geo risk -> CHF strengthens -> USDCHF falls
                scores.append(-risk_factor * 0.4)
            elif pair in ("AUDUSD",):
                # High geo risk -> AUD weakens -> AUDUSD falls
                scores.append(-risk_factor * 0.3)

        if not scores:
            return "neutral", 0.0

        avg_score = sum(scores) / len(scores)
        strength = min(abs(avg_score), 1.0)

        if avg_score > 0.05:
            return "bullish", round(strength, 4)
        elif avg_score < -0.05:
            return "bearish", round(strength, 4)
        else:
            return "neutral", round(strength, 4)

    # -----------------------------------------------------------------
    # Geopolitical risk integration
    # -----------------------------------------------------------------

    def _get_geo_risk_score(self) -> Optional[float]:
        """
        Read the latest geopolitical risk score from crisis_monitor output
        or GPR index bridge cache.
        """
        # Try crisis_monitor scorecard first
        scorecards_dir = self.repo_root / "logs" / "scorecards"
        if scorecards_dir.exists():
            try:
                files = sorted(scorecards_dir.glob("scorecard_*.json"), reverse=True)
                if files:
                    scorecard = json.loads(files[0].read_text())
                    # Extract composite risk score (normalized 0-1)
                    risk_score = scorecard.get("risk_score")
                    if risk_score is not None:
                        return min(max(float(risk_score), 0.0), 1.0)
                    # Fallback: look for regime classification
                    mode = scorecard.get("mode", "NORMAL")
                    mode_scores = {
                        "NORMAL": 0.2, "ELEVATED": 0.5,
                        "CRISIS": 0.8, "MANUAL_REVIEW": 0.9,
                    }
                    return mode_scores.get(mode, 0.3)
            except Exception as e:
                logger.debug("Could not read scorecard: %s", e)

        # Fallback: GPR index cache
        gpr_cache = self.repo_root / "artifacts" / "cache" / "gpr_latest.csv"
        if gpr_cache.exists():
            try:
                import csv
                import io
                text = gpr_cache.read_text()
                reader = csv.DictReader(io.StringIO(text))
                rows = list(reader)
                if rows:
                    latest = rows[-1]
                    # GPR index: historical mean ~100, spikes to 300+ in crises
                    for key, val in latest.items():
                        if "gpr" in key.lower() and "gprd" not in key.lower():
                            gpr_val = safe_float(val)
                            if gpr_val is not None:
                                # Normalize: 100 = 0.3, 200 = 0.6, 300+ = 0.9
                                return min(max(gpr_val / 333.0, 0.0), 1.0)
            except Exception as e:
                logger.debug("Could not read GPR cache: %s", e)

        return None

    # -----------------------------------------------------------------
    # Main poll
    # -----------------------------------------------------------------

    def poll(self) -> Dict[str, Any]:
        """
        Poll all FX pairs, compute signals, and return a standardized
        signal packet for the strategy orchestrator.
        """
        pairs_data: Dict[str, Dict[str, Any]] = {}
        carry_rankings: List[Dict[str, Any]] = []
        errors: List[str] = []

        geo_risk = self._get_geo_risk_score()

        for pair, cfg in FRED_FX_SERIES.items():
            try:
                observations = self._fetch_series(cfg["series_id"], limit=60)
                if not observations:
                    errors.append(f"{pair}: no data from FRED series {cfg['series_id']}")
                    continue

                values = [o["value"] for o in observations if o["value"] is not None]
                if len(values) < 5:
                    errors.append(f"{pair}: insufficient data ({len(values)} points)")
                    continue

                latest_rate = values[-1]
                latest_date = observations[-1]["date"] if observations else None

                # Apply inversion if needed for standard market convention
                if cfg.get("invert"):
                    latest_rate = 1.0 / latest_rate if latest_rate != 0 else 0
                    values = [1.0 / v if v != 0 else 0 for v in values]

                # Compute signals
                momentum_5d = self._compute_momentum(values, 5)
                momentum_20d = self._compute_momentum(values, 20)
                ma_distance = self._compute_ma_distance(values, MA_WINDOW)
                volatility = self._compute_volatility(values, min(20, len(values) - 1))
                carry_diff = self._carry_differential(pair)

                signal_dir, signal_str = self._classify_signal(
                    momentum_5d, momentum_20d, ma_distance,
                    carry_diff, geo_risk, pair,
                )

                # Moving average value
                ma_20 = sum(values[-MA_WINDOW:]) / MA_WINDOW if len(values) >= MA_WINDOW else None

                pairs_data[pair] = {
                    "rate": round(latest_rate, 6),
                    "label": cfg["label"],
                    "signal": signal_dir,
                    "strength": signal_str,
                    "latest_date": latest_date,
                    "momentum_5d_pct": round(momentum_5d, 4) if momentum_5d is not None else None,
                    "momentum_20d_pct": round(momentum_20d, 4) if momentum_20d is not None else None,
                    "ma_20": round(ma_20, 6) if ma_20 is not None else None,
                    "ma_distance_pct": round(ma_distance, 4) if ma_distance is not None else None,
                    "volatility_20d": round(volatility, 6) if volatility is not None else None,
                    "carry_differential": round(carry_diff, 2) if carry_diff is not None else None,
                    "data_points": len(values),
                    "fred_series": cfg["series_id"],
                }

                # Carry trade ranking entry
                if carry_diff is not None:
                    carry_rankings.append({
                        "pair": pair,
                        "carry_differential": round(carry_diff, 2),
                        "direction": "long" if carry_diff > 0 else "short",
                        "rate": round(latest_rate, 6),
                    })

            except Exception as e:
                error_msg = f"{pair}: {str(e)[:200]}"
                errors.append(error_msg)
                logger.warning("FX poll error for %s: %s", pair, e)

        # Sort carry rankings by absolute differential (highest first)
        carry_rankings.sort(key=lambda x: abs(x["carry_differential"]), reverse=True)

        # Build output packet
        packet = {
            "bridge": "fx_monitor",
            "schema_version": "fx_signal.v1",
            "timestamp_utc": iso_now(),
            "pairs": pairs_data,
            "carry_trade_rankings": carry_rankings,
            "geopolitical_risk_score": round(geo_risk, 4) if geo_risk is not None else None,
            "policy_rates_snapshot": POLICY_RATES,
            "data_source": "FRED (Federal Reserve Economic Data)",
            "data_freshness": "daily (business days)",
            "pairs_monitored": len(pairs_data),
            "errors": errors if errors else None,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "routing_hint": "asset_class=forex -> IBKR (IDEALPRO) or TastyTrade FX",
                "shadow_mode": True,
                "bridge_version": "1.0.0",
            },
        }

        # Write output
        try:
            output_path = self.output_dir / "fx_monitor.json"
            output_path.write_text(json.dumps(packet, indent=2))
            logger.info(
                "FX monitor: %d pairs polled, %d errors, geo_risk=%.2f",
                len(pairs_data), len(errors),
                geo_risk if geo_risk is not None else -1,
            )
        except Exception as e:
            logger.error("Failed to write FX output: %s", e)

        return packet


# ---------------------------------------------------------------------------
# Standalone poll function (for crisis_monitor integration)
# ---------------------------------------------------------------------------

def poll(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Module-level poll function for crisis_monitor integration.
    Returns a standardized FX signal packet.
    """
    bridge = FXBridge(repo_root=repo_root)
    return bridge.poll()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Global Sentinel FX Monitor Bridge")
    p.add_argument("--repo-root", default=".", help="Repository root path")
    p.add_argument("--once", action="store_true", help="Poll once and print JSON output")
    p.add_argument("--loop-seconds", type=int, default=900, help="Loop polling interval (default: 15 min)")
    p.add_argument("--pair", default=None, help="Poll a specific pair only (e.g. EURUSD)")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] FX_BRIDGE: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    bridge = FXBridge(repo_root=repo_root)

    if args.once:
        result = bridge.poll()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Continuous monitoring loop
    logger.info("FX Monitor starting (interval=%ds)", args.loop_seconds)
    while True:
        try:
            result = bridge.poll()
            n_pairs = result.get("pairs_monitored", 0)
            n_errors = len(result.get("errors") or [])
            logger.info("Cycle complete: %d pairs, %d errors", n_pairs, n_errors)
        except Exception as e:
            logger.error("Poll cycle error: %s", e)

        time.sleep(args.loop_seconds)


if __name__ == "__main__":
    main()

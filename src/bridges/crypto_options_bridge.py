#!/usr/bin/env python3
"""
Global Sentinel — TastyTrade Crypto Options Bridge

Fetches crypto option chains (BTC, ETH) via TastyTrade SDK, computes
Greeks and IV surface data, scores opportunities by IV percentile / skew /
term structure, and emits standardized signal packets for the strategy engine.

This bridge is read-only intelligence; it never places orders.

Auth: tries TASTYTRADE_REMEMBER_TOKEN first, falls back to username/password.
Output: data/quantum_feed/crypto_options_data.json
Tier 2, trust 0.7, TTL 30 min
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("global_sentinel.crypto_options_bridge")

# ---------------------------------------------------------------------------
# TastyTrade SDK — graceful fallback
# ---------------------------------------------------------------------------
try:
    from tastytrade import Session, Account
    from tastytrade.instruments import Option, get_option_chain
    HAS_TASTYTRADE = True
except ImportError:
    HAS_TASTYTRADE = False
    logger.warning("tastytrade SDK not installed. Run: pip install tastytrade")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
DEFAULT_UNDERLIERS = ["BTC", "ETH"]
IV_PERCENTILE_WINDOW = int(os.getenv("CRYPTO_IV_WINDOW_DAYS", "30"))
HIGH_IV_THRESHOLD = float(os.getenv("CRYPTO_HIGH_IV_PCT", "80"))
MOMENTUM_CALL_DELTA_MIN = float(os.getenv("CRYPTO_MOM_DELTA_MIN", "0.15"))
MOMENTUM_CALL_DELTA_MAX = float(os.getenv("CRYPTO_MOM_DELTA_MAX", "0.35"))


# ---------------------------------------------------------------------------
# Session helpers (mirrors tastytrade_auth.py pattern)
# ---------------------------------------------------------------------------

def _create_session() -> Tuple[Optional[Any], Optional[str]]:
    """Create a TastyTrade session.

    Tries the remember-token flow first (no 2FA), then falls back to
    username/password.  Returns (session, error_string).
    """
    if not HAS_TASTYTRADE:
        return None, "tastytrade SDK not installed"

    remember_token = os.getenv("TASTYTRADE_REMEMBER_TOKEN", "")
    username = os.getenv("TASTYTRADE_USERNAME", "")
    password = os.getenv("TASTYTRADE_PASSWORD", "")

    # Attempt 1: remember token
    if username and remember_token:
        try:
            session = Session(username, remember_token=remember_token)
            return session, None
        except Exception as exc:
            logger.info("Remember-token auth failed (%s), trying password", exc)

    # Attempt 2: username + password
    if username and password:
        try:
            session = Session(username, password)
            return session, None
        except Exception as exc:
            return None, f"Auth failed: {exc}"

    return None, "TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD (or TASTYTRADE_REMEMBER_TOKEN) required"


# ---------------------------------------------------------------------------
# Greeks helpers (lightweight, SDK-provided when available)
# ---------------------------------------------------------------------------

def _extract_greeks(option_data: Any) -> Dict[str, Optional[float]]:
    """Pull delta, theta, IV from an option object or dict if available."""
    greeks: Dict[str, Optional[float]] = {
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "implied_volatility": None,
    }
    for field in greeks:
        val = getattr(option_data, field, None)
        if val is None and isinstance(option_data, dict):
            val = option_data.get(field)
        if val is not None:
            try:
                greeks[field] = round(float(val), 6)
            except (TypeError, ValueError):
                pass
    return greeks


def _compute_iv_percentile(iv_values: List[float], current_iv: float) -> Optional[float]:
    """Return the percentile rank of *current_iv* within *iv_values*."""
    if not iv_values or current_iv is None:
        return None
    count_below = sum(1 for v in iv_values if v < current_iv)
    return round((count_below / len(iv_values)) * 100, 1)


# ---------------------------------------------------------------------------
# Opportunity scoring
# ---------------------------------------------------------------------------

def _score_vol_sell(iv_pct: Optional[float], iv: Optional[float], delta: Optional[float]) -> float:
    """Score a short-put income opportunity. Higher = better sell candidate."""
    if iv_pct is None or iv is None:
        return 0.0
    score = 0.0
    # IV percentile is the primary driver
    if iv_pct >= HIGH_IV_THRESHOLD:
        score += 40 + (iv_pct - HIGH_IV_THRESHOLD) * 1.5
    # Prefer moderate delta (OTM puts)
    if delta is not None:
        abs_delta = abs(delta)
        if 0.15 <= abs_delta <= 0.35:
            score += 20
        elif 0.10 <= abs_delta <= 0.45:
            score += 10
    # Raw IV bonus
    if iv > 0.8:
        score += 15
    elif iv > 0.6:
        score += 8
    return round(min(score, 100), 1)


def _score_momentum_call(iv_pct: Optional[float], delta: Optional[float]) -> float:
    """Score a long OTM call for momentum breakout. Higher = better."""
    if delta is None:
        return 0.0
    score = 0.0
    abs_delta = abs(delta)
    # Want OTM calls in the momentum sweet spot
    if MOMENTUM_CALL_DELTA_MIN <= abs_delta <= MOMENTUM_CALL_DELTA_MAX:
        score += 35
    elif 0.10 <= abs_delta <= 0.45:
        score += 15
    # Lower IV is better for buying calls (cheaper premium)
    if iv_pct is not None:
        if iv_pct < 40:
            score += 30
        elif iv_pct < 60:
            score += 15
    return round(min(score, 100), 1)


# ---------------------------------------------------------------------------
# IV surface builder
# ---------------------------------------------------------------------------

def _build_iv_surface(
    chain_data: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build a simplified IV surface from chain data keyed by expiration."""
    surface: Dict[str, Any] = {"expirations": {}, "term_structure": []}
    exp_avg_ivs: List[Tuple[str, float]] = []

    for exp_str, strikes in chain_data.items():
        ivs = [s["greeks"]["implied_volatility"] for s in strikes if s["greeks"].get("implied_volatility")]
        if not ivs:
            continue
        avg_iv = statistics.mean(ivs)
        surface["expirations"][exp_str] = {
            "avg_iv": round(avg_iv, 4),
            "min_iv": round(min(ivs), 4),
            "max_iv": round(max(ivs), 4),
            "skew": round(max(ivs) - min(ivs), 4),
            "n_strikes": len(ivs),
        }
        exp_avg_ivs.append((exp_str, avg_iv))

    # Term structure: sorted by expiration
    exp_avg_ivs.sort(key=lambda x: x[0])
    surface["term_structure"] = [{"expiration": e, "avg_iv": round(v, 4)} for e, v in exp_avg_ivs]

    if len(exp_avg_ivs) >= 2:
        front = exp_avg_ivs[0][1]
        back = exp_avg_ivs[-1][1]
        surface["contango"] = front < back
        surface["front_back_spread"] = round(back - front, 4)
    else:
        surface["contango"] = None
        surface["front_back_spread"] = None

    return surface


# ---------------------------------------------------------------------------
# Main bridge class
# ---------------------------------------------------------------------------

class CryptoOptionsBridge:
    """Fetch crypto option chains from TastyTrade and emit scored signal packets."""

    DISPLAY_NAME = "crypto_options"
    CATEGORY = "crypto_derivatives"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "crypto_options_data.json"
        self.underliers = [
            u.strip()
            for u in os.getenv("CRYPTO_OPTIONS_UNDERLIERS", ",".join(DEFAULT_UNDERLIERS)).split(",")
            if u.strip()
        ]
        self._session: Optional[Any] = None
        self._session_error: Optional[str] = None
        # Rolling IV history per underlier (kept in-process; not persisted)
        self._iv_history: Dict[str, List[float]] = {u: [] for u in self.underliers}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_session(self) -> Tuple[Optional[Any], Optional[str]]:
        if self._session is not None:
            return self._session, None
        session, err = _create_session()
        if err:
            self._session_error = err
            return None, err
        self._session = session
        return session, None

    # ------------------------------------------------------------------
    # Chain fetching
    # ------------------------------------------------------------------

    def _fetch_chain(self, session: Any, underlier: str) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch option chain for a crypto underlier.

        Returns a dict keyed by expiration date string, each value a list
        of strike dicts with greeks.
        """
        result: Dict[str, List[Dict[str, Any]]] = {}
        try:
            chain = get_option_chain(session, underlier)
        except Exception as exc:
            logger.warning("[CryptoOptionsBridge] Chain fetch failed for %s: %s", underlier, exc)
            return result

        for exp_date, strikes in chain.items():
            exp_str = str(exp_date)
            exp_entries: List[Dict[str, Any]] = []
            for strike, option_obj in strikes.items():
                greeks = _extract_greeks(option_obj)
                entry = {
                    "strike": float(strike) if strike else None,
                    "expiration": exp_str,
                    "option_type": getattr(option_obj, "option_type", "unknown"),
                    "symbol": getattr(option_obj, "streamer_symbol", str(option_obj)),
                    "greeks": greeks,
                }
                exp_entries.append(entry)
            if exp_entries:
                result[exp_str] = exp_entries

        return result

    # ------------------------------------------------------------------
    # Opportunity generation
    # ------------------------------------------------------------------

    def _generate_opportunities(
        self,
        underlier: str,
        chain_data: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Score and return the top opportunities from the chain."""
        # Gather all IVs for percentile calculation
        all_ivs: List[float] = []
        for strikes in chain_data.values():
            for s in strikes:
                iv = s["greeks"].get("implied_volatility")
                if iv is not None and iv > 0:
                    all_ivs.append(iv)

        # Update rolling history
        if all_ivs:
            self._iv_history.setdefault(underlier, [])
            self._iv_history[underlier].extend(all_ivs)
            # Keep a bounded window
            max_history = IV_PERCENTILE_WINDOW * 50
            self._iv_history[underlier] = self._iv_history[underlier][-max_history:]

        current_median_iv = statistics.median(all_ivs) if all_ivs else None
        iv_pct = _compute_iv_percentile(self._iv_history.get(underlier, []), current_median_iv) if current_median_iv else None

        opportunities: List[Dict[str, Any]] = []
        for exp_str, strikes in chain_data.items():
            for s in strikes:
                delta = s["greeks"].get("delta")
                iv = s["greeks"].get("implied_volatility")

                vol_sell_score = _score_vol_sell(iv_pct, iv, delta)
                mom_call_score = _score_momentum_call(iv_pct, delta)

                if vol_sell_score >= 30 or mom_call_score >= 30:
                    opp = {
                        "symbol": s.get("symbol"),
                        "strike": s.get("strike"),
                        "expiration": exp_str,
                        "option_type": s.get("option_type"),
                        "greeks": s["greeks"],
                        "scores": {
                            "vol_sell": vol_sell_score,
                            "momentum_call": mom_call_score,
                        },
                        "iv_percentile": iv_pct,
                    }
                    opportunities.append(opp)

        # Sort by best combined score, take top 20
        opportunities.sort(
            key=lambda o: max(o["scores"]["vol_sell"], o["scores"]["momentum_call"]),
            reverse=True,
        )
        return opportunities[:20]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll(self) -> Dict[str, Any]:
        """Poll TastyTrade for crypto option chain data and emit signal packet."""
        session, err = self._get_session()
        if err:
            logger.error("[CryptoOptionsBridge] Session error: %s", err)
            return {
                "bridge": "crypto_options",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "error": err,
                "data": None,
            }

        underlier_packets: List[Dict[str, Any]] = []

        for underlier in self.underliers:
            logger.info("[CryptoOptionsBridge] Fetching chain for %s", underlier)
            chain_data = self._fetch_chain(session, underlier)

            if not chain_data:
                underlier_packets.append({
                    "symbol": underlier,
                    "instrument_type": "crypto_option",
                    "error": "no_chain_data",
                    "opportunities": [],
                    "iv_surface": {},
                })
                continue

            opportunities = self._generate_opportunities(underlier, chain_data)
            iv_surface = _build_iv_surface(chain_data)

            # Gather all IVs for the current median
            all_ivs = [
                s["greeks"]["implied_volatility"]
                for strikes in chain_data.values()
                for s in strikes
                if s["greeks"].get("implied_volatility")
            ]
            median_iv = round(statistics.median(all_ivs), 4) if all_ivs else None
            iv_pct = _compute_iv_percentile(
                self._iv_history.get(underlier, []), median_iv
            ) if median_iv else None

            total_strikes = sum(len(v) for v in chain_data.values())
            expirations_available = len(chain_data)

            underlier_packets.append({
                "symbol": underlier,
                "instrument_type": "crypto_option",
                "chain_summary": {
                    "expirations": expirations_available,
                    "total_strikes": total_strikes,
                    "median_iv": median_iv,
                    "iv_percentile": iv_pct,
                },
                "iv_surface": iv_surface,
                "opportunities": opportunities,
                "top_vol_sell": [
                    o for o in opportunities if o["scores"]["vol_sell"] >= 40
                ][:5],
                "top_momentum_calls": [
                    o for o in opportunities if o["scores"]["momentum_call"] >= 40
                ][:5],
            })

        result: Dict[str, Any] = {
            "bridge": "crypto_options",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "underliers": underlier_packets,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "bridge_version": "1.0.0",
                "sdk_available": HAS_TASTYTRADE,
                "underliers_polled": self.underliers,
            },
        }

        # Persist to quantum feed
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        logger.info(
            "[CryptoOptionsBridge] Polled %d underliers, wrote %s",
            len(self.underliers),
            self.output_path,
        )

        return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the TastyTrade crypto options bridge output.",
    )
    parser.add_argument(
        "--repo-root",
        default=os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"),
        help="Global Sentinel repository root",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("GS_LOG_LEVEL", "INFO"),
        help="Python logging level",
    )
    parser.add_argument(
        "--underliers",
        default=None,
        help="Comma-separated crypto underliers (default: BTC,ETH)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bridge = CryptoOptionsBridge(repo_root=args.repo_root)
    if args.underliers:
        bridge.underliers = [u.strip() for u in args.underliers.split(",") if u.strip()]

    result = bridge.poll()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

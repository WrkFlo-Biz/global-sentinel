#!/usr/bin/env python3
"""
Global Sentinel — Futures Monitor Bridge

Monitors key futures contracts via IBKR gateway (ib_async) with ETF proxy
fallback for when the gateway is unavailable.

Tracked contracts:
  ES  (S&P 500 E-mini)   — GLOBEX
  NQ  (Nasdaq 100 E-mini) — GLOBEX
  CL  (Crude Oil)         — NYMEX
  GC  (Gold)              — COMEX
  ZB  (30-Year Treasury)  — CBOT

Computes:
  - Basis (futures premium/discount to spot) — contango/backwardation
  - Roll yield opportunity
  - Cross-asset momentum (equity futures vs commodity futures divergence)
  - Term structure slope

Output: data/quantum_feed/futures_monitor.json
Tier 2, trust 0.8, TTL 15 min
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("global_sentinel.futures_bridge")

# ---------------------------------------------------------------------------
# Contract definitions
# ---------------------------------------------------------------------------

FUTURES_CONTRACTS: Dict[str, Dict[str, str]] = {
    "ES": {"exchange": "GLOBEX", "currency": "USD", "description": "S&P 500 E-mini",
            "multiplier": "50", "asset_group": "equity_index"},
    "NQ": {"exchange": "GLOBEX", "currency": "USD", "description": "Nasdaq 100 E-mini",
            "multiplier": "20", "asset_group": "equity_index"},
    "CL": {"exchange": "NYMEX", "currency": "USD", "description": "WTI Crude Oil",
            "multiplier": "1000", "asset_group": "energy"},
    "GC": {"exchange": "COMEX", "currency": "USD", "description": "Gold",
            "multiplier": "100", "asset_group": "precious_metals"},
    "ZB": {"exchange": "CBOT", "currency": "USD", "description": "30-Year Treasury Bond",
            "multiplier": "1000", "asset_group": "fixed_income"},
}

# ETF proxies for fallback spot pricing when IBKR gateway is unavailable
ETF_PROXY_MAP: Dict[str, str] = {
    "ES": "SPY",
    "NQ": "QQQ",
    "CL": "USO",
    "GC": "GLD",
    "ZB": "TLT",
}

# Approximate multiplier to convert ETF price to futures-equivalent level
# These are rough scaling factors, not exact
ETF_TO_FUTURES_SCALE: Dict[str, float] = {
    "ES": 10.0,     # SPY ~520 -> ES ~5200
    "NQ": 44.0,     # QQQ ~440 -> NQ ~19360 (approx)
    "CL": 1.0,      # USO is a rough proxy, not directly scalable
    "GC": 18.5,     # GLD ~180 -> GC ~3330 (approx, GLD tracks 1/10 oz)
    "ZB": 1.0,      # TLT is a bond ETF, not directly scalable
}


# ---------------------------------------------------------------------------
# ib_async helpers
# ---------------------------------------------------------------------------

try:
    from ib_async import IB, Contract
    HAS_IB = True
except ImportError:
    HAS_IB = False


IB_HOST = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_GATEWAY_PORT", "4001"))
IB_CLIENT_ID = int(os.getenv("IB_FUTURES_CLIENT_ID", "10"))


def _safe_float(val: Any) -> Optional[float]:
    """Convert ib_async nan/None to None for JSON."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


async def _fetch_futures_via_ibkr() -> Dict[str, Dict[str, Any]]:
    """Connect to IBKR gateway and fetch quotes for all futures contracts."""
    if not HAS_IB:
        raise ImportError("ib_async not installed")

    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=15)
        logger.info("Connected to IBKR gateway at %s:%s for futures data", IB_HOST, IB_PORT)
    except Exception as e:
        raise ConnectionError(f"Cannot connect to IB Gateway: {e}")

    results: Dict[str, Dict[str, Any]] = {}
    try:
        for symbol, meta in FUTURES_CONTRACTS.items():
            try:
                contract = Contract(
                    secType="FUT",
                    symbol=symbol,
                    exchange=meta["exchange"],
                    currency=meta["currency"],
                )
                qualified = await ib.qualifyContractsAsync(contract)
                if not qualified:
                    logger.warning("Could not qualify futures contract: %s", symbol)
                    results[symbol] = {"error": f"qualification_failed", "source": "ibkr"}
                    continue

                contract = qualified[0]
                ticker = ib.reqMktData(contract, snapshot=True)
                # Wait for snapshot
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    if ticker.last is not None and _safe_float(ticker.last) is not None:
                        break

                results[symbol] = {
                    "price": _safe_float(ticker.last),
                    "bid": _safe_float(ticker.bid),
                    "ask": _safe_float(ticker.ask),
                    "volume": _safe_float(ticker.volume),
                    "high": _safe_float(ticker.high),
                    "low": _safe_float(ticker.low),
                    "close": _safe_float(ticker.close),
                    "contract_id": contract.conId,
                    "last_trade_date": getattr(contract, "lastTradeDateOrContractMonth", ""),
                    "exchange": meta["exchange"],
                    "multiplier": meta["multiplier"],
                    "asset_group": meta["asset_group"],
                    "source": "ibkr",
                }
            except Exception as exc:
                logger.warning("Error fetching %s from IBKR: %s", symbol, exc)
                results[symbol] = {"error": str(exc), "source": "ibkr"}
    finally:
        ib.disconnect()

    return results


def _run_async(coro):
    """Run async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        try:
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        except ImportError:
            raise RuntimeError("nest_asyncio required for nested event loop")
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ETF proxy fallback (Alpaca / Yahoo Finance)
# ---------------------------------------------------------------------------

_ALPACA_BASE = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
_ALPACA_KEY = os.getenv("APCA_API_KEY_ID", "")
_ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY", "")


def _fetch_json(url: str, headers: Optional[Dict[str, str]] = None,
                timeout: int = 15) -> Optional[dict]:
    """Generic JSON fetch."""
    hdrs = {"User-Agent": "Mozilla/5.0 (GlobalSentinel/1.0)", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("Fetch failed %s: %s", url, exc)
        return None


def _get_etf_price_yahoo(symbol: str) -> Optional[float]:
    """Fetch last price from Yahoo Finance (no API key needed)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    data = _fetch_json(url)
    if data and "chart" in data:
        try:
            return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return None


def _get_etf_price_alpaca(symbol: str) -> Optional[float]:
    """Fetch last trade from Alpaca market data."""
    if not _ALPACA_KEY or not _ALPACA_SECRET:
        return None
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest"
    headers = {
        "APCA-API-KEY-ID": _ALPACA_KEY,
        "APCA-API-SECRET-KEY": _ALPACA_SECRET,
    }
    data = _fetch_json(url, headers=headers)
    if data and "trade" in data:
        try:
            return float(data["trade"]["p"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _get_etf_price_finnhub(symbol: str) -> Optional[float]:
    """Fetch last price from Finnhub (requires FINNHUB_API_KEY)."""
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return None
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={key}"
    data = _fetch_json(url)
    if data and data.get("c"):
        try:
            return float(data["c"])
        except (TypeError, ValueError):
            pass
    return None


def _fetch_etf_proxy_prices() -> Dict[str, Dict[str, Any]]:
    """Fetch ETF proxy prices for all futures contracts using free data sources."""
    results: Dict[str, Dict[str, Any]] = {}

    for fut_symbol, etf_symbol in ETF_PROXY_MAP.items():
        meta = FUTURES_CONTRACTS[fut_symbol]
        price = None
        source = "unknown"

        # Try sources in priority order
        price = _get_etf_price_alpaca(etf_symbol)
        if price is not None:
            source = "alpaca_proxy"
        else:
            price = _get_etf_price_yahoo(etf_symbol)
            if price is not None:
                source = "yahoo_proxy"
            else:
                price = _get_etf_price_finnhub(etf_symbol)
                if price is not None:
                    source = "finnhub_proxy"

        if price is not None:
            scale = ETF_TO_FUTURES_SCALE.get(fut_symbol, 1.0)
            estimated_futures = round(price * scale, 2)
            results[fut_symbol] = {
                "price": estimated_futures,
                "etf_price": price,
                "etf_symbol": etf_symbol,
                "scale_factor": scale,
                "bid": None,
                "ask": None,
                "volume": None,
                "high": None,
                "low": None,
                "close": None,
                "contract_id": None,
                "last_trade_date": None,
                "exchange": meta["exchange"],
                "multiplier": meta["multiplier"],
                "asset_group": meta["asset_group"],
                "source": source,
                "is_proxy": True,
            }
        else:
            results[fut_symbol] = {
                "error": "no_data_source_available",
                "etf_symbol": etf_symbol,
                "source": "none",
            }

    return results


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _compute_basis_bps(futures_price: Optional[float],
                       spot_price: Optional[float]) -> Optional[float]:
    """Compute basis in basis points: (futures - spot) / spot * 10000."""
    if futures_price is None or spot_price is None or spot_price == 0:
        return None
    return round((futures_price - spot_price) / spot_price * 10000, 1)


def _basis_signal(basis_bps: Optional[float]) -> str:
    """Classify basis as contango, backwardation, or flat."""
    if basis_bps is None:
        return "unknown"
    if basis_bps > 20:
        return "contango"
    elif basis_bps < -20:
        return "backwardation"
    return "flat"


def _momentum_signal(price: Optional[float], close: Optional[float]) -> Tuple[str, Optional[float]]:
    """Simple session momentum: compare current price to prior close."""
    if price is None or close is None or close == 0:
        return "unknown", None
    pct = round((price - close) / close * 100, 2)
    if pct > 0.5:
        return "bullish", pct
    elif pct < -0.5:
        return "bearish", pct
    return "neutral", pct


def _directional_signal(momentum_pct: Optional[float]) -> str:
    """Translate momentum pct into a signal label."""
    if momentum_pct is None:
        return "neutral"
    if momentum_pct > 1.0:
        return "strong_bullish"
    if momentum_pct > 0.3:
        return "bullish"
    if momentum_pct < -1.0:
        return "strong_bearish"
    if momentum_pct < -0.3:
        return "bearish"
    return "neutral"


def _compute_contract_signals(data: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a single contract's data with computed signals."""
    if data.get("error"):
        return data

    price = data.get("price")
    close = data.get("close")
    etf_price = data.get("etf_price")

    # Basis: if we have both futures price and ETF spot, compute basis
    basis_bps = None
    if etf_price is not None and price is not None:
        # For proxy data, basis is implicit in scale factor difference
        basis_bps = _compute_basis_bps(price, etf_price * ETF_TO_FUTURES_SCALE.get("", 1.0))
    elif not data.get("is_proxy") and price is not None and close is not None:
        # For IBKR data, use prior close as approximate spot
        basis_bps = _compute_basis_bps(price, close)

    momentum_label, momentum_pct = _momentum_signal(price, close)
    signal = _directional_signal(momentum_pct)

    data["basis_bps"] = basis_bps
    data["basis_condition"] = _basis_signal(basis_bps)
    data["momentum_pct"] = momentum_pct
    data["momentum"] = momentum_label
    data["signal"] = signal

    return data


def _compute_cross_asset_signals(contracts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute cross-asset divergence and term structure signals."""
    signals: Dict[str, Any] = {}

    # Equity index momentum
    es_mom = contracts.get("ES", {}).get("momentum_pct")
    nq_mom = contracts.get("NQ", {}).get("momentum_pct")

    # Commodity momentum
    cl_mom = contracts.get("CL", {}).get("momentum_pct")
    gc_mom = contracts.get("GC", {}).get("momentum_pct")

    # Treasury momentum
    zb_mom = contracts.get("ZB", {}).get("momentum_pct")

    # Equity vs commodity divergence
    if es_mom is not None and cl_mom is not None:
        divergence = round(es_mom - cl_mom, 2)
        signals["equity_vs_oil_divergence"] = divergence
        if abs(divergence) > 1.5:
            signals["equity_oil_signal"] = "diverging"
        else:
            signals["equity_oil_signal"] = "correlated"

    # Gold as risk barometer: gold up + equities down = risk-off
    if gc_mom is not None and es_mom is not None:
        if gc_mom > 0.3 and es_mom < -0.3:
            signals["risk_regime"] = "risk_off"
        elif gc_mom < -0.3 and es_mom > 0.3:
            signals["risk_regime"] = "risk_on"
        else:
            signals["risk_regime"] = "mixed"

    # Bond signal: ZB up = flight to safety
    if zb_mom is not None:
        if zb_mom > 0.3:
            signals["bond_signal"] = "flight_to_safety"
        elif zb_mom < -0.3:
            signals["bond_signal"] = "risk_appetite"
        else:
            signals["bond_signal"] = "neutral"

    # NQ vs ES relative strength (tech leadership)
    if nq_mom is not None and es_mom is not None:
        tech_relative = round(nq_mom - es_mom, 2)
        signals["tech_relative_strength"] = tech_relative
        signals["tech_leadership"] = "tech_leading" if tech_relative > 0.3 else \
                                      "tech_lagging" if tech_relative < -0.3 else "inline"

    # Roll yield opportunity: strong contango = negative roll yield for longs
    for symbol in ("ES", "NQ", "CL", "GC"):
        basis = contracts.get(symbol, {}).get("basis_bps")
        if basis is not None:
            if basis > 50:
                signals[f"{symbol.lower()}_roll_yield"] = "negative_for_longs"
            elif basis < -50:
                signals[f"{symbol.lower()}_roll_yield"] = "positive_for_longs"
            else:
                signals[f"{symbol.lower()}_roll_yield"] = "neutral"

    # Overall cross-asset signal
    bullish_count = sum(1 for s in [es_mom, nq_mom, cl_mom, gc_mom]
                        if s is not None and s > 0.3)
    bearish_count = sum(1 for s in [es_mom, nq_mom, cl_mom, gc_mom]
                        if s is not None and s < -0.3)
    total = bullish_count + bearish_count
    if total > 0:
        signals["cross_asset_breadth"] = round((bullish_count - bearish_count) / total, 2)
    else:
        signals["cross_asset_breadth"] = 0.0

    return signals


# ---------------------------------------------------------------------------
# Main bridge class
# ---------------------------------------------------------------------------

class FuturesMonitorBridge:
    """Monitors key futures contracts and emits standardized signal packets."""

    DISPLAY_NAME = "futures_monitor"
    CATEGORY = "futures_macro"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "futures_monitor.json"
        self._ibkr_available: Optional[bool] = None

    def _try_ibkr(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Attempt to fetch data via IBKR gateway."""
        if not HAS_IB:
            logger.info("[FuturesMonitor] ib_async not installed, skipping IBKR")
            return None
        try:
            data = _run_async(_fetch_futures_via_ibkr())
            self._ibkr_available = True
            return data
        except Exception as exc:
            logger.warning("[FuturesMonitor] IBKR gateway unavailable: %s", exc)
            self._ibkr_available = False
            return None

    def _fetch_contracts(self) -> Tuple[Dict[str, Dict[str, Any]], str]:
        """Fetch contract data, trying IBKR first then falling back to ETF proxies."""
        # Try IBKR gateway first
        ibkr_data = self._try_ibkr()
        if ibkr_data is not None:
            # Check if we got useful data for at least some contracts
            valid = sum(1 for v in ibkr_data.values()
                        if v.get("price") is not None and not v.get("error"))
            if valid >= 2:
                return ibkr_data, "ibkr"

        # Fall back to ETF proxies
        logger.info("[FuturesMonitor] Using ETF proxy fallback")
        proxy_data = _fetch_etf_proxy_prices()
        return proxy_data, "etf_proxy"

    def poll(self) -> Dict[str, Any]:
        """Poll futures data and compute signals."""
        contracts, data_source = self._fetch_contracts()

        # Compute per-contract signals
        enriched: Dict[str, Dict[str, Any]] = {}
        for symbol, data in contracts.items():
            enriched[symbol] = _compute_contract_signals(data)

        # Compute cross-asset signals
        cross_signals = _compute_cross_asset_signals(enriched)

        result: Dict[str, Any] = {
            "bridge": "futures_monitor",
            "data_source": data_source,
            "ibkr_connected": self._ibkr_available if self._ibkr_available is not None else False,
            "contracts": enriched,
            "cross_asset_signals": cross_signals,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "execution_metadata": {
                "not_for_direct_execution": True,
                "purpose": "signal_generation_and_monitoring",
                "contracts_tracked": list(FUTURES_CONTRACTS.keys()),
                "etf_proxies_used": data_source == "etf_proxy",
            },
        }

        # Persist output
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2))

        # Log summary
        active = [s for s, d in enriched.items() if d.get("price") is not None]
        logger.info(
            "[FuturesMonitor] %s — %d/%d contracts active | risk_regime=%s | breadth=%.2f",
            data_source,
            len(active),
            len(FUTURES_CONTRACTS),
            cross_signals.get("risk_regime", "unknown"),
            cross_signals.get("cross_asset_breadth", 0.0),
        )

        return result


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def poll() -> Dict[str, Any]:
    """Module-level poll function for bridge registry compatibility."""
    bridge = FuturesMonitorBridge()
    return bridge.poll()


def main() -> int:
    parser = argparse.ArgumentParser(description="Futures Monitor Bridge — fetch and analyze futures data")
    parser.add_argument(
        "--repo-root",
        default=os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"),
        help="Global Sentinel repository root",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print full JSON output to stdout",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    bridge = FuturesMonitorBridge(repo_root=args.repo_root)
    result = bridge.poll()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        # Summary output
        src = result.get("data_source", "unknown")
        contracts = result.get("contracts", {})
        cross = result.get("cross_asset_signals", {})

        print(f"\n{'='*60}")
        print(f"  FUTURES MONITOR — {result.get('timestamp_utc', '')}")
        print(f"  Data source: {src}")
        print(f"{'='*60}")

        for sym, data in contracts.items():
            meta = FUTURES_CONTRACTS.get(sym, {})
            price = data.get("price", "N/A")
            signal = data.get("signal", "N/A")
            mom = data.get("momentum_pct", "N/A")
            basis = data.get("basis_condition", "N/A")
            err = data.get("error")
            if err:
                print(f"  {sym:4s} ({meta.get('description', ''):25s}) ERROR: {err}")
            else:
                print(f"  {sym:4s} ({meta.get('description', ''):25s}) "
                      f"${price:<10} signal={signal:<15} mom={mom}%  basis={basis}")

        print(f"\n  Cross-asset signals:")
        for k, v in cross.items():
            print(f"    {k}: {v}")
        print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

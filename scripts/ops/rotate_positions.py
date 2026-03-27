#!/usr/bin/env python3
"""
Global Sentinel — Position Rotation Script
Closes misaligned positions and opens new high-conviction positions.

Usage:
    python scripts/ops/rotate_positions.py              # dry-run (default)
    python scripts/ops/rotate_positions.py --execute     # live execution

Reads API keys from .env file at project root.
Logs all actions to logs/execution/position_rotation.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
LOG_FILE = PROJECT_ROOT / "logs" / "execution" / "position_rotation.jsonl"

PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"

# Positions to close
CLOSE_DAY_TRADE = [
    {"symbol": "LEU",  "qty": 12,  "side": "long", "asset": "equity"},
    {"symbol": "CCJ",  "qty": 17,  "side": "long", "asset": "equity"},
    {"symbol": "URA",  "qty": 40,  "side": "long", "asset": "equity"},
    {"symbol": "UUUU", "qty": 123, "side": "long", "asset": "equity"},
    {"symbol": "RKLB", "qty": 33,  "side": "long", "asset": "equity"},
    {"symbol": "PLTR", "qty": 12,  "side": "long", "asset": "equity"},
    {"symbol": "CRWD", "qty": 4,   "side": "long", "asset": "equity"},
    {"symbol": "IRDM", "qty": 83,  "side": "long", "asset": "equity"},
    {"symbol": "RDW",  "qty": 232, "side": "long", "asset": "equity"},
    {"symbol": "PANW", "qty": 12,  "side": "long", "asset": "equity"},
    {"symbol": "FTNT", "qty": 23,  "side": "long", "asset": "equity"},
    {"symbol": "ZS",   "qty": 14,  "side": "long", "asset": "equity"},
]

CLOSE_MEDLONG = [
    {"symbol": "TRUMPUSD", "qty": 7394.020727092,        "side": "long", "asset": "crypto"},
    {"symbol": "BONKUSD",  "qty": 2096641891.7501893,    "side": "long", "asset": "crypto"},
    {"symbol": "SHIBUSD",  "qty": 2331245590,            "side": "long", "asset": "crypto"},
    {"symbol": "PAXGUSD",  "qty": 24.829094609,          "side": "long", "asset": "crypto"},
    {"symbol": "XRPUSD",   "qty": 54887.853006844,       "side": "long", "asset": "crypto"},
]

# New positions to open
OPEN_MEDLONG = [
    {"symbol": "STNG", "notional": 45000, "side": "buy",  "note": "shipping"},
    {"symbol": "FRO",  "notional": 37500, "side": "buy",  "note": "shipping"},
    {"symbol": "ZIM",  "notional": 37500, "side": "buy",  "note": "shipping"},
    {"symbol": "NAT",  "notional": 30000, "side": "buy",  "note": "shipping"},
    {"symbol": "LMT",  "notional": 45000, "side": "buy",  "note": "defense"},
    {"symbol": "RTX",  "notional": 37500, "side": "buy",  "note": "defense"},
    {"symbol": "NOC",  "notional": 30000, "side": "buy",  "note": "defense"},
    {"symbol": "LNG",  "notional": 45000, "side": "buy",  "note": "europe energy"},
    {"symbol": "EZU",  "notional": 37500, "side": "sell", "note": "europe energy short"},
    {"symbol": "EWG",  "notional": 22500, "side": "sell", "note": "europe energy short"},
    {"symbol": "GLD",  "notional": 60000, "side": "buy",  "note": "gold safe haven"},
    {"symbol": "MOS",  "notional": 30000, "side": "buy",  "note": "fertilizer"},
    {"symbol": "CF",   "notional": 30000, "side": "buy",  "note": "fertilizer"},
]

OPEN_DAY_TRADE = [
    {"symbol": "MPC", "notional": 15000, "side": "buy", "note": "refiner"},
    {"symbol": "VLO", "notional": 15000, "side": "buy", "note": "refiner"},
    {"symbol": "PSX", "notional": 12000, "side": "buy", "note": "refiner"},
    {"symbol": "PBF", "notional": 12000, "side": "buy", "note": "refiner"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env(path: Path) -> Dict[str, str]:
    """Parse a .env file: line by line, split on first '=', strip quotes."""
    env = {}
    if not path.exists():
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            env[key] = val
    return env


def make_headers(api_key: str, api_secret: str) -> Dict[str, str]:
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def log_action(entry: dict):
    """Append a JSON line to the log file."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_latest_price(symbol: str, headers: Dict[str, str]) -> Optional[float]:
    """Fetch latest quote from Alpaca data API and return midpoint or ask price."""
    url = f"{DATA_BASE}/v2/stocks/{symbol}/quotes/latest"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"  [WARN] Could not get quote for {symbol}: HTTP {resp.status_code}")
            return None
        data = resp.json()
        quote = data.get("quote", {})
        ask = _to_float(quote.get("ap"))
        bid = _to_float(quote.get("bp"))
        if ask and bid and ask > 0 and bid > 0:
            return (ask + bid) / 2.0
        if ask and ask > 0:
            return ask
        if bid and bid > 0:
            return bid
        return None
    except Exception as e:
        print(f"  [WARN] Quote fetch error for {symbol}: {e}")
        return None


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Close helpers
# ---------------------------------------------------------------------------
def close_crypto_position(
    symbol: str, headers: Dict[str, str], execute: bool
) -> Dict[str, Any]:
    """Close a crypto position via DELETE /v2/positions/{symbol}."""
    url = f"{PAPER_BASE}/v2/positions/{symbol}"
    result = {
        "action": "close",
        "symbol": symbol,
        "asset": "crypto",
        "method": "DELETE /v2/positions",
        "timestamp": iso_now(),
    }
    if not execute:
        result["status"] = "dry_run"
        return result
    try:
        resp = requests.delete(url, headers=headers, timeout=20)
        result["http_status"] = resp.status_code
        if resp.status_code < 300:
            result["status"] = "submitted"
            try:
                result["response"] = resp.json()
            except Exception:
                pass
        else:
            result["status"] = "error"
            result["error"] = resp.text[:500]
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


def close_equity_position(
    symbol: str, qty: int, headers: Dict[str, str], execute: bool
) -> Dict[str, Any]:
    """Close an equity position via market sell order."""
    result = {
        "action": "close",
        "symbol": symbol,
        "qty": qty,
        "asset": "equity",
        "method": "POST /v2/orders (market sell)",
        "timestamp": iso_now(),
    }
    if not execute:
        result["status"] = "dry_run"
        return result
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
    }
    try:
        resp = requests.post(
            f"{PAPER_BASE}/v2/orders", headers=headers, json=payload, timeout=20
        )
        result["http_status"] = resp.status_code
        if resp.status_code < 300:
            result["status"] = "submitted"
            try:
                body = resp.json()
                result["order_id"] = body.get("id")
            except Exception:
                pass
        else:
            result["status"] = "error"
            result["error"] = resp.text[:500]
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Open helpers
# ---------------------------------------------------------------------------
def open_position(
    symbol: str,
    notional: float,
    side: str,
    time_in_force: str,
    headers: Dict[str, str],
    execute: bool,
    note: str = "",
) -> Dict[str, Any]:
    """Open a new equity position using a market order with notional amount.

    For shorts (side='sell'), we need to calculate qty from notional since
    Alpaca does not support notional for sell-short orders.
    """
    is_short = side == "sell"

    result = {
        "action": "open",
        "symbol": symbol,
        "notional": notional,
        "side": side,
        "time_in_force": time_in_force,
        "note": note,
        "timestamp": iso_now(),
    }

    # For shorts, compute qty from notional / price
    if is_short:
        price = get_latest_price(symbol, headers)
        if price is None or price <= 0:
            result["status"] = "error"
            result["error"] = f"Cannot get price for {symbol} to calculate short qty"
            return result
        qty = int(notional / price)
        if qty < 1:
            result["status"] = "error"
            result["error"] = f"Calculated qty={qty} too small (price={price:.2f})"
            return result
        result["calculated_qty"] = qty
        result["price_used"] = round(price, 4)
        result["method"] = "POST /v2/orders (market sell-short, qty-based)"

        if not execute:
            result["status"] = "dry_run"
            return result

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "sell",
            "type": "market",
            "time_in_force": time_in_force,
        }
    else:
        result["method"] = "POST /v2/orders (market buy, notional)"

        if not execute:
            # Still fetch price for the summary
            price = get_latest_price(symbol, headers)
            if price:
                result["approx_shares"] = round(notional / price, 2)
                result["price_used"] = round(price, 4)
            result["status"] = "dry_run"
            return result

        payload = {
            "symbol": symbol,
            "notional": str(notional),
            "side": "buy",
            "type": "market",
            "time_in_force": time_in_force,
        }

    try:
        resp = requests.post(
            f"{PAPER_BASE}/v2/orders", headers=headers, json=payload, timeout=20
        )
        result["http_status"] = resp.status_code
        if resp.status_code < 300:
            result["status"] = "submitted"
            try:
                body = resp.json()
                result["order_id"] = body.get("id")
            except Exception:
                pass
        else:
            result["status"] = "error"
            result["error"] = resp.text[:500]
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(results: List[Dict[str, Any]]):
    closes = [r for r in results if r["action"] == "close"]
    opens = [r for r in results if r["action"] == "open"]

    print("\n" + "=" * 80)
    print("POSITION ROTATION SUMMARY")
    print("=" * 80)

    if closes:
        print(f"\n{'─'*40}")
        print("CLOSES")
        print(f"{'─'*40}")
        print(f"{'Symbol':<12} {'Asset':<8} {'Status':<12} {'Detail'}")
        print(f"{'─'*12} {'─'*8} {'─'*12} {'─'*30}")
        for r in closes:
            detail = ""
            if r["status"] == "error":
                detail = r.get("error", "")[:50]
            elif r["status"] == "submitted":
                detail = r.get("order_id", "")[:36]
            print(f"{r['symbol']:<12} {r.get('asset',''):<8} {r['status']:<12} {detail}")

    if opens:
        print(f"\n{'─'*40}")
        print("OPENS")
        print(f"{'─'*40}")
        print(f"{'Symbol':<8} {'Side':<6} {'Notional':>10} {'~Shares':>10} {'TIF':<5} {'Status':<12} {'Note'}")
        print(f"{'─'*8} {'─'*6} {'─'*10} {'─'*10} {'─'*5} {'─'*12} {'─'*20}")
        for r in opens:
            shares = ""
            if r.get("approx_shares"):
                shares = f"{r['approx_shares']:.1f}"
            elif r.get("calculated_qty"):
                shares = str(r["calculated_qty"])
            print(
                f"{r['symbol']:<8} {r['side']:<6} ${r['notional']:>9,.0f} {shares:>10} "
                f"{r['time_in_force']:<5} {r['status']:<12} {r.get('note','')}"
            )

    # Totals
    n_ok = sum(1 for r in results if r["status"] in ("submitted", "dry_run"))
    n_err = sum(1 for r in results if r["status"] == "error")
    print(f"\n{'─'*40}")
    print(f"Total: {len(results)} actions | {n_ok} ok | {n_err} errors")
    print("=" * 80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Rotate positions for oil-shock thesis")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True, help="Print what would happen (default)")
    group.add_argument("--execute", action="store_true", help="Actually submit orders")
    args = parser.parse_args()

    execute = args.execute

    mode_label = "LIVE EXECUTION" if execute else "DRY RUN"
    print(f"\n{'*' * 60}")
    print(f"  Position Rotation — {mode_label}")
    print(f"  Timestamp: {iso_now()}")
    print(f"{'*' * 60}")

    if execute:
        print("\n  ⚠  LIVE MODE — orders will be submitted to Alpaca paper API")
        print("  Press Ctrl+C within 5 seconds to abort...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Aborted.")
            sys.exit(0)

    # Load env
    env = load_env(ENV_FILE)

    dt_key = env.get("ALPACA_API_KEY") or env.get("ALPACA_API_KEY_DAYTRADE") or env.get("APCA_API_KEY_ID")
    dt_secret = env.get("ALPACA_SECRET_KEY") or env.get("ALPACA_SECRET_KEY_DAYTRADE") or env.get("APCA_API_SECRET_KEY")
    ml_key = env.get("ALPACA_API_KEY_MEDLONG") or env.get("APCA_API_KEY_ID_MEDLONG")
    ml_secret = env.get("ALPACA_SECRET_KEY_MEDLONG") or env.get("APCA_API_SECRET_KEY_MEDLONG")

    if not dt_key or not dt_secret:
        print("[ERROR] Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
        sys.exit(1)
    if not ml_key or not ml_secret:
        print("[ERROR] Missing ALPACA_API_KEY_MEDLONG / ALPACA_SECRET_KEY_MEDLONG in .env")
        sys.exit(1)

    dt_headers = make_headers(dt_key, dt_secret)
    ml_headers = make_headers(ml_key, ml_secret)

    results: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Phase 1: Close positions
    # -----------------------------------------------------------------------
    print("\n--- Phase 1: Closing misaligned positions ---")

    # Day trade equity closes
    print("\n[DAY_TRADE account — equity closes]")
    for pos in CLOSE_DAY_TRADE:
        symbol = pos["symbol"]
        qty = pos["qty"]
        print(f"  Closing {symbol} ({qty} shares)...", end=" ")
        result = close_equity_position(symbol, qty, dt_headers, execute)
        result["account"] = "day_trade"
        results.append(result)
        log_action(result)
        print(result["status"])
        if execute:
            time.sleep(0.35)  # rate limit courtesy

    # Medium-long crypto closes
    print("\n[MEDIUM_LONG account — crypto closes]")
    for pos in CLOSE_MEDLONG:
        symbol = pos["symbol"]
        print(f"  Closing {symbol}...", end=" ")
        result = close_crypto_position(symbol, ml_headers, execute)
        result["account"] = "medium_long"
        results.append(result)
        log_action(result)
        print(result["status"])
        if execute:
            time.sleep(0.35)

    # -----------------------------------------------------------------------
    # Phase 2: Open new positions
    # -----------------------------------------------------------------------
    print("\n--- Phase 2: Opening new positions ---")

    # Medium-long opens
    print("\n[MEDIUM_LONG account — new positions]")
    for order in OPEN_MEDLONG:
        symbol = order["symbol"]
        notional = order["notional"]
        side = order["side"]
        note = order.get("note", "")
        print(f"  Opening {symbol} ({side} ${notional:,.0f} — {note})...", end=" ")
        # Alpaca requires time_in_force="day" for notional/fractional orders
        result = open_position(
            symbol, notional, side, "day", ml_headers, execute, note
        )
        result["account"] = "medium_long"
        results.append(result)
        log_action(result)
        print(result["status"])
        if execute:
            time.sleep(0.35)

    # Day trade opens
    print("\n[DAY_TRADE account — new positions]")
    for order in OPEN_DAY_TRADE:
        symbol = order["symbol"]
        notional = order["notional"]
        side = order["side"]
        note = order.get("note", "")
        print(f"  Opening {symbol} ({side} ${notional:,.0f} — {note})...", end=" ")
        result = open_position(
            symbol, notional, side, "day", dt_headers, execute, note
        )
        result["account"] = "day_trade"
        results.append(result)
        log_action(result)
        print(result["status"])
        if execute:
            time.sleep(0.35)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print_summary(results)

    # Final log entry
    summary_entry = {
        "event": "rotation_complete",
        "mode": "execute" if execute else "dry_run",
        "timestamp": iso_now(),
        "total_actions": len(results),
        "submitted": sum(1 for r in results if r["status"] == "submitted"),
        "dry_run": sum(1 for r in results if r["status"] == "dry_run"),
        "errors": sum(1 for r in results if r["status"] == "error"),
    }
    log_action(summary_entry)
    print(f"\nLog written to: {LOG_FILE}")


if __name__ == "__main__":
    main()

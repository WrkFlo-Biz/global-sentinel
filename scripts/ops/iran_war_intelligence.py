#!/usr/bin/env python3
"""Iran War Intelligence Engine for Global Sentinel.

Scrapes every available data source for intelligence on the Iran war,
Strait of Hormuz closure, oil disruption, energy cascade effects,
aviation disruptions, and all direct/indirect global market impacts.

Goal: maximize profitability on $125 live capital at Monday US market open.

Schedule:
  - NOW until 7:00 AM CST (8:00 AM ET): research every 30 min
  - 7:00 AM CST (8:00 AM ET): comprehensive morning brief
  - 7:00-8:30 AM CST: updates every 15 min
  - 8:25 AM CST (9:25 AM ET): final pre-market alert

Usage:
    python scripts/ops/iran_war_intelligence.py
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup & env loading
# ---------------------------------------------------------------------------
sys.path.insert(0, "/opt/global-sentinel")

from src.core.control_state_snapshot import read_control_state_snapshot
from src.monitoring.notification_window import notifications_muted

ENV_PATH = Path("/opt/global-sentinel/.env")
REPO_ROOT = Path("/opt/global-sentinel")
RESEARCH_LOG = REPO_ROOT / "logs" / "research" / "iran_war_intel.jsonl"
BRIEF_PATH = REPO_ROOT / "reports" / "flash" / "iran_war_brief.json"
FINAL_PLAN_PATH = REPO_ROOT / "reports" / "flash" / "final_125_plan.json"

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("US/Eastern")
    CT = ZoneInfo("US/Central")
except ImportError:
    ET = timezone(timedelta(hours=-4))  # EDT fallback
    CT = timezone(timedelta(hours=-5))  # CDT fallback

CAPITAL = 125.0  # Default fallback — overridden by check_live_account() at execution time
LIVE_BASE_URL = "https://api.alpaca.markets"  # LIVE (not paper)


def load_dotenv(path: Path) -> None:
    """Minimal .env loader — no dependencies."""
    if not path.exists():
        print(f"[WARN] .env not found at {path}")
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val


load_dotenv(ENV_PATH)

# API keys
SERP_API_KEY = os.getenv("SERP_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_ROLE_UPDATES_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_V6_THREAD_ID = os.getenv("TELEGRAM_V6_DIGEST_THREAD_ID", "")

# Live account keys ($125)
ALPACA_API_KEY_LIVE = os.getenv("ALPACA_API_KEY_LIVE", "")
ALPACA_SECRET_KEY_LIVE = os.getenv("ALPACA_SECRET_KEY_LIVE", "")

TELEGRAM_BOT_TOKEN_DARKBOT = os.getenv("TELEGRAM_BOT_TOKEN_DARKBOT", TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID_DARKBOT = os.getenv("TELEGRAM_CHAT_ID_DARKBOT", TELEGRAM_CHAT_ID)
TELEGRAM_BOT_TOKEN_DRKBOT = os.getenv("TELEGRAM_BOT_TOKEN_DRKBOT", TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID_DRKBOT = os.getenv("TELEGRAM_CHAT_ID_DRKBOT", TELEGRAM_CHAT_ID)

# ---------------------------------------------------------------------------
# Impact bucket definitions
# ---------------------------------------------------------------------------
IMPACT_BUCKETS = {
    "OIL_SUPPLY": {
        "keywords": ["hormuz", "strait", "oil supply", "crude oil", "oil production",
                      "opec", "sanctions", "barrel", "brent", "wti", "oil embargo",
                      "petroleum", "oil export", "oil import", "pipeline",
                      "oil disruption", "oil shock", "oil spike", "crude spike",
                      "supply disruption", "persian gulf", "oil blockade", "irgc navy",
                      "crack spread", "oil futures", "contango", "backwardation"],
        "score": 0,
        "signals": [],
    },
    "ENERGY_CASCADE": {
        "keywords": ["lng", "natural gas", "nat gas", "electricity", "coal",
                      "power grid", "energy crisis", "energy price", "utility",
                      "refinery", "gasoline", "diesel", "heating oil",
                      "energy disruption", "blackout", "fuel shortage", "petrol",
                      "energy security", "strategic reserve", "spr release"],
        "score": 0,
        "signals": [],
    },
    "AVIATION": {
        "keywords": ["airline", "aviation", "airspace", "flight", "airport",
                      "jet fuel", "boeing", "airbus", "faa", "no-fly zone",
                      "grounded", "travel ban", "flight cancellation",
                      "air traffic", "overflight", "airspace closure"],
        "score": 0,
        "signals": [],
    },
    "SHIPPING": {
        "keywords": ["tanker", "shipping", "freight", "container", "port",
                      "maritime", "insurance premium", "war risk", "suez",
                      "chokepoint", "vessel", "cargo", "bab el-mandeb",
                      "houthi", "red sea", "tanker rerouting", "cape route",
                      "lloyd's war risk", "marine insurance", "piracy",
                      "strait closure", "naval blockade", "mine sweeping"],
        "score": 0,
        "signals": [],
    },
    "DEFENSE": {
        "keywords": ["defense", "military", "weapons", "missile", "drone",
                      "pentagon", "nato", "troops", "army", "navy", "air force",
                      "lockheed", "raytheon", "northrop", "general dynamics",
                      "bae systems"],
        "score": 0,
        "signals": [],
    },
    "SAFE_HAVEN": {
        "keywords": ["gold", "treasury", "safe haven", "swiss franc", "yen",
                      "bond yield", "flight to safety", "risk off", "dollar",
                      "precious metal", "silver"],
        "score": 0,
        "signals": [],
    },
    "FOOD_CHAIN": {
        "keywords": ["fertilizer", "agriculture", "wheat", "corn", "food price",
                      "food supply", "grain", "farming", "crop", "livestock",
                      "food crisis"],
        "score": 0,
        "signals": [],
    },
    "INFLATION": {
        "keywords": ["inflation", "cpi", "interest rate", "fed", "federal reserve",
                      "consumer price", "cost of living", "stagflation",
                      "rate hike", "rate cut", "monetary policy"],
        "score": 0,
        "signals": [],
    },
    "TECH_SELLOFF": {
        "keywords": ["tech sell", "nasdaq", "growth stock", "tech sector",
                      "semiconductor", "chip", "ai stock", "magnificent seven",
                      "faang", "tech bubble", "valuation"],
        "score": 0,
        "signals": [],
    },
    "GEOPOLITICAL": {
        "keywords": ["escalation", "de-escalation", "ceasefire", "diplomat",
                      "negotiation", "un security", "iran", "israel",
                      "saudi", "china", "russia", "ally", "coalition",
                      "retaliatory", "nuclear"],
        "score": 0,
        "signals": [],
    },
}

# ---------------------------------------------------------------------------
# Search queries for SerpAPI
# ---------------------------------------------------------------------------
SERP_QUERIES = [
    "Iran war latest",
    "Strait of Hormuz shipping disruption",
    "oil price surge crude",
    "airline fuel costs jet fuel",
    "defense spending military contract",
    "gold safe haven demand",
    "tanker rates war risk premium",
    "Iran oil supply disruption sanctions",
    "energy crisis natural gas LNG",
    "oil inflation CPI impact",
    "Houthi Red Sea shipping attack",
    "Iran IRGC navy Persian Gulf",
    "oil refining crack spread margins",
    "fertilizer food prices oil impact",
]

# Reddit subreddits
REDDIT_SUBS = [
    "wallstreetbets",
    "options",
    "stocks",
    "geopolitics",
    "energy",
    "shipping",
]

# Yahoo Finance symbols
YAHOO_SYMBOLS = [
    ("CL=F", "WTI Crude"),
    ("BZ=F", "Brent Crude"),
    ("NG=F", "Natural Gas"),
    ("RB=F", "Gasoline RBOB"),
    ("GC=F", "Gold"),
    ("^VIX", "VIX"),
    ("^GSPC", "S&P 500"),
    ("ES=F", "S&P Futures"),
    ("NQ=F", "Nasdaq Futures"),
    ("^N225", "Nikkei"),
    ("^HSI", "Hang Seng"),
]

# Crypto symbols for Alpaca
CRYPTO_SYMBOLS = "BTC/USD,ETH/USD,SOL/USD"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
# Create an SSL context that doesn't verify certificates (some sites block)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    raw: bool = False,
) -> Any:
    """GET JSON from url, return parsed dict or None on error."""
    hdrs = {"Accept": "application/json", "User-Agent": "GlobalSentinel/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
            data = resp.read().decode("utf-8")
            if raw:
                return data
            return json.loads(data)
    except Exception as exc:
        print(f"  [HTTP ERR] {url[:90]}... => {exc}")
        return None


def send_telegram(text: str, bot_token: str, chat_id: str,
                  thread_id: Optional[str] = None) -> bool:
    """Send a Telegram message using urllib (no requests dependency)."""
    if notifications_muted():
        print("  [TG] Automated updates muted, skipping send")
        return False
    if not bot_token or not chat_id:
        print("  [TG] Missing bot_token or chat_id, skipping send")
        return False

    # Telegram max message length is 4096
    text = text[:4096]

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except (ValueError, TypeError):
            pass

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                return True
            print(f"  [TG] API error: {body}")
            return False
    except Exception as exc:
        print(f"  [TG] Send failed: {exc}")
        return False


def send_to_all_channels(text: str) -> None:
    """Send message to the v6_digest topic and Mo's direct bot route."""
    # 1) Forum topic via default bot
    if TELEGRAM_V6_THREAD_ID:
        send_telegram(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_V6_THREAD_ID)

    # 2) mo2darkbot direct
    send_telegram(text, TELEGRAM_BOT_TOKEN_DARKBOT, TELEGRAM_CHAT_ID_DARKBOT)


# ---------------------------------------------------------------------------
# Options chain helpers
# ---------------------------------------------------------------------------

def fetch_options_chain(underlying: str, opt_type: str = "call",
                        exp_gte: str = "", exp_lte: str = "",
                        strike_gte: float = 0, strike_lte: float = 0,
                        limit: int = 10) -> List[Dict]:
    """Fetch options contracts from Alpaca live API."""
    if not ALPACA_API_KEY_LIVE:
        return []
    params = {
        "underlying_symbols": underlying,
        "status": "active",
        "type": opt_type,
        "limit": str(limit),
    }
    if exp_gte:
        params["expiration_date_gte"] = exp_gte
    if exp_lte:
        params["expiration_date_lte"] = exp_lte
    if strike_gte:
        params["strike_price_gte"] = str(strike_gte)
    if strike_lte:
        params["strike_price_lte"] = str(strike_lte)

    qs = urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(
            f"{LIVE_BASE_URL}/v2/options/contracts?{qs}",
            headers={
                "APCA-API-KEY-ID": ALPACA_API_KEY_LIVE,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY_LIVE,
            },
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            return data.get("option_contracts", [])
    except Exception as exc:
        print(f"  [OPT] Chain fetch failed for {underlying}: {exc}")
        return []


def submit_option_order(symbol: str, qty: int = 1, side: str = "buy",
                        order_type: str = "market") -> Dict:
    """Submit an options order on the live account."""
    if not ALPACA_API_KEY_LIVE:
        return {"ok": False, "error": "no_live_keys"}

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY_LIVE,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY_LIVE,
        "Content-Type": "application/json",
    }
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": "day",
    }
    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{LIVE_BASE_URL}/v2/orders",
            data=req_data,
            headers=headers,
            method="POST",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            body = json.loads(resp.read().decode())
            print(f"  [OPT] {symbol} {side} {qty}x -> {body.get('status','?')} (id={body.get('id','?')[:8]})")
            return {"ok": True, "order_id": body.get("id"), "status": body.get("status")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200] if hasattr(e, "read") else str(e)
        print(f"  [OPT] {symbol} {side} {qty}x -> ERROR {e.code}: {detail}")
        return {"ok": False, "error": f"HTTP {e.code}", "detail": detail}
    except Exception as exc:
        print(f"  [OPT] {symbol} {side} {qty}x -> EXCEPTION: {exc}")
        return {"ok": False, "error": str(exc)}


def submit_spread_order(buy_leg: str, sell_leg: str, net_debit: float,
                        qty: int = 1) -> Dict:
    """Submit a vertical spread as two separate legs (buy + sell).

    Alpaca doesn't support native multi-leg orders on all account types,
    so we submit as two individual orders. The buy leg goes first to ensure
    we own the long side before writing the short side.
    """
    if not ALPACA_API_KEY_LIVE:
        return {"ok": False, "error": "no_live_keys"}

    results = {"buy_leg": None, "sell_leg": None, "ok": False, "net_debit": net_debit}

    # Leg 1: Buy the long option
    buy_result = submit_option_order(buy_leg, qty=qty, side="buy", order_type="market")
    results["buy_leg"] = buy_result
    if not buy_result.get("ok"):
        print(f"  [SPREAD] Buy leg failed — aborting spread")
        return results

    time.sleep(1)  # Wait for fill

    # Leg 2: Sell the short option (only if buy succeeded)
    sell_result = submit_option_order(sell_leg, qty=qty, side="sell", order_type="market")
    results["sell_leg"] = sell_result
    results["ok"] = sell_result.get("ok", False)

    if results["ok"]:
        print(f"  [SPREAD] {buy_leg} / {sell_leg} spread filled (est. debit ${net_debit:.2f})")
    else:
        print(f"  [SPREAD] WARNING: Buy filled but sell failed — holding naked long {buy_leg}")

    return results


# ---------------------------------------------------------------------------
# Live order execution ($125 account)
# ---------------------------------------------------------------------------

def submit_live_orders(doubling_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Submit the diversified doubling plan as LIVE market orders.

    Uses notional (dollar) amounts. Only buys — no shorting on this account.
    Returns list of order results.
    """
    if not ALPACA_API_KEY_LIVE or not ALPACA_SECRET_KEY_LIVE:
        print("  [LIVE] No live API keys configured — skipping order submission")
        return []

    positions = doubling_plan.get("positions", [])
    if not positions:
        print("  [LIVE] No positions in doubling plan — skipping")
        return []

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY_LIVE,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY_LIVE,
        "Content-Type": "application/json",
    }

    strategy_mode = doubling_plan.get("strategy_mode", "UNKNOWN")
    print(f"  [LIVE] Strategy: {strategy_mode}")

    results = []
    for pos in positions:
        ticker = pos.get("vehicle", "")
        amount = pos.get("amount", 0)
        direction = pos.get("direction", "LONG").upper()
        order_type = pos.get("order_type", "")
        strategy_type = pos.get("strategy_type", "fractional")

        # Skip shorts — account doesn't support shorting (but buying puts is OK)
        if direction in ("SHORT", "SHORT AIRLINES", "SHORT TECH") and order_type not in ("option", "spread"):
            print(f"  [LIVE] Skipping {ticker} — shorting not enabled on live account")
            results.append({"symbol": ticker, "ok": False, "reason": "shorting_disabled"})
            continue

        if amount < 1:
            continue

        # Spreads: submit as two legs using submit_spread_order()
        if order_type == "spread":
            spread_type = pos.get("spread_type", "bull_call")
            print(f"  [LIVE] Submitting {spread_type} spread on {ticker} (${amount:.0f} budget)")

            # Parse the OCC symbol to extract underlying, expiry, type, strike
            # OCC format: ROOT + YYMMDD + C/P + strike*1000 (8 digits)
            spread_submitted = False
            try:
                import re
                occ_match = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', ticker)
                if occ_match:
                    underlying = occ_match.group(1)
                    expiry_str = occ_match.group(2)  # YYMMDD
                    opt_flag = occ_match.group(3)     # C or P
                    strike_raw = int(occ_match.group(4)) / 1000.0

                    # Convert YYMMDD to YYYY-MM-DD for API
                    exp_yyyy = f"20{expiry_str[:2]}-{expiry_str[2:4]}-{expiry_str[4:6]}"
                    opt_type = "call" if opt_flag == "C" else "put"

                    # Fetch chain for same underlying, expiry, and type
                    chain = fetch_options_chain(
                        underlying, opt_type=opt_type,
                        exp_gte=exp_yyyy, exp_lte=exp_yyyy,
                        limit=20,
                    )

                    if chain:
                        # Sort contracts by strike price
                        for c in chain:
                            c["_strike"] = float(c.get("strike_price", 0))
                        chain.sort(key=lambda c: c["_strike"])

                        # Find the ATM contract (closest to our target strike)
                        atm_idx = None
                        for i, c in enumerate(chain):
                            if c["_strike"] >= strike_raw:
                                atm_idx = i
                                break
                        if atm_idx is None:
                            atm_idx = len(chain) - 1

                        buy_leg_sym = None
                        sell_leg_sym = None

                        if spread_type == "bull_call":
                            # Buy ATM call, sell next strike up
                            if atm_idx < len(chain) - 1:
                                buy_leg_sym = chain[atm_idx].get("symbol")
                                sell_leg_sym = chain[atm_idx + 1].get("symbol")
                        elif spread_type == "bear_put":
                            # Buy ATM put, sell next strike down
                            if atm_idx > 0:
                                buy_leg_sym = chain[atm_idx].get("symbol")
                                sell_leg_sym = chain[atm_idx - 1].get("symbol")

                        if buy_leg_sym and sell_leg_sym:
                            print(f"  [LIVE] Spread legs: BUY {buy_leg_sym} / SELL {sell_leg_sym}")
                            spread_result = submit_spread_order(
                                buy_leg=buy_leg_sym,
                                sell_leg=sell_leg_sym,
                                net_debit=amount,
                                qty=1,
                            )
                            result = {
                                "symbol": ticker,
                                "amount": amount,
                                "strategy_type": strategy_type,
                                "spread_type": spread_type,
                                "buy_leg": buy_leg_sym,
                                "sell_leg": sell_leg_sym,
                                "ok": spread_result.get("ok", False),
                                "spread_result": spread_result,
                            }
                            if spread_result.get("ok"):
                                result["note"] = f"{spread_type} spread filled: {buy_leg_sym} / {sell_leg_sym}"
                            elif spread_result.get("buy_leg", {}).get("ok"):
                                result["ok"] = True  # Long leg filled, that's acceptable
                                result["note"] = f"{spread_type} sell leg failed (cash acct?) — holding long {buy_leg_sym}"
                            else:
                                result["note"] = f"{spread_type} spread failed entirely"
                            results.append(result)
                            spread_submitted = True
                        else:
                            print(f"  [LIVE] Could not find strike pair for {spread_type} spread on {underlying}")
                    else:
                        print(f"  [LIVE] Options chain empty for {underlying} {opt_type} exp {exp_yyyy}")
                else:
                    print(f"  [LIVE] Could not parse OCC symbol: {ticker}")
            except Exception as exc:
                print(f"  [LIVE] Spread chain lookup failed for {ticker}: {exc}")

            # Fallback: submit long leg only if spread construction failed
            if not spread_submitted:
                print(f"  [LIVE] Falling back to long leg only for {ticker}")
                result = submit_option_order(ticker, qty=1, side="buy", order_type="market")
                result["symbol"] = ticker
                result["amount"] = amount
                result["strategy_type"] = strategy_type
                result["note"] = f"Spread intended ({spread_type}) — submitted as long leg only (chain lookup failed)"
                results.append(result)

            time.sleep(0.5)
            continue

        # Options: buy 1 contract (qty-based, not notional)
        if order_type == "option":
            print(f"  [LIVE] Submitting {strategy_type}: {ticker} (${amount:.0f} budget)")
            result = submit_option_order(ticker, qty=1, side="buy", order_type="market")
            result["symbol"] = ticker
            result["amount"] = amount
            result["strategy_type"] = strategy_type
            results.append(result)
            time.sleep(0.5)
            continue

        # Fractional shares: notional buy
        print(f"  [LIVE] Submitting fractional: {ticker} ${amount:.0f}")
        payload = {
            "symbol": ticker,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "notional": str(round(amount, 2)),
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{LIVE_BASE_URL}/v2/orders",
                data=req_data,
                headers=headers,
                method="POST",
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                body = json.loads(resp.read().decode())
                order_id = body.get("id", "?")
                status = body.get("status", "?")
                print(f"  [LIVE] {ticker} ${amount:.0f} BUY -> {status} (order={order_id})")
                results.append({
                    "symbol": ticker, "ok": True, "amount": amount,
                    "order_id": order_id, "status": status,
                    "strategy_type": strategy_type,
                })
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200] if hasattr(e, "read") else str(e)
            print(f"  [LIVE] {ticker} ${amount:.0f} BUY -> ERROR {e.code}: {body}")
            results.append({
                "symbol": ticker, "ok": False, "amount": amount,
                "error": f"HTTP {e.code}", "detail": body,
            })
        except Exception as exc:
            print(f"  [LIVE] {ticker} ${amount:.0f} BUY -> EXCEPTION: {exc}")
            results.append({
                "symbol": ticker, "ok": False, "amount": amount,
                "error": str(exc),
            })

        time.sleep(0.5)  # Rate limit between orders

    # Send Telegram notification of order results
    ok_count = sum(1 for r in results if r.get("ok"))
    err_count = len(results) - ok_count
    msg_lines = [
        f"LIVE ORDERS SUBMITTED ({ok_count}/{len(results)} OK)",
        f"Strategy: {strategy_mode}",
    ]
    for r in results:
        status = "OK" if r.get("ok") else f"ERR: {r.get('error','?')}"
        st = r.get("strategy_type", "?")
        msg_lines.append(f"  {r['symbol']} ${r.get('amount',0):.0f} [{st}] -> {status}")
    send_to_all_channels("\n".join(msg_lines))

    return results


def check_live_positions() -> List[Dict[str, Any]]:
    """Check current positions on the live account."""
    if not ALPACA_API_KEY_LIVE:
        return []
    try:
        req = urllib.request.Request(
            f"{LIVE_BASE_URL}/v2/positions",
            headers={
                "APCA-API-KEY-ID": ALPACA_API_KEY_LIVE,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY_LIVE,
            },
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  [LIVE] Position check failed: {exc}")
        return []


def check_live_account() -> Dict[str, Any]:
    """Get live account info."""
    if not ALPACA_API_KEY_LIVE:
        return {}
    try:
        req = urllib.request.Request(
            f"{LIVE_BASE_URL}/v2/account",
            headers={
                "APCA-API-KEY-ID": ALPACA_API_KEY_LIVE,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY_LIVE,
            },
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  [LIVE] Account check failed: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Data source fetchers
# ---------------------------------------------------------------------------

def fetch_serp_news() -> List[Dict[str, Any]]:
    """Fetch Google News via SerpAPI for all war-related queries."""
    if not SERP_API_KEY:
        print("  [SERP] No API key, skipping")
        return []

    all_results = []
    for query in SERP_QUERIES:
        encoded = urllib.parse.quote(query)
        url = f"https://serpapi.com/search.json?engine=google_news&q={encoded}&api_key={SERP_API_KEY}"
        data = http_get(url, timeout=20)
        if not data:
            continue

        articles = data.get("news_results", []) or data.get("organic_results", [])
        for art in articles[:10]:
            item = {
                "source": "serp_google_news",
                "query": query,
                "title": art.get("title", ""),
                "link": art.get("link", ""),
                "snippet": art.get("snippet", art.get("description", ""))[:300],
                "date": art.get("date", ""),
            }
            all_results.append(item)
        print(f"  [SERP] '{query}' => {len(articles)} results")
        time.sleep(0.5)  # Rate limit courtesy

    return all_results


def fetch_reddit() -> List[Dict[str, Any]]:
    """Fetch hot posts from relevant subreddits. Falls back to SerpAPI if blocked."""
    all_results = []
    blocked = False
    for sub in REDDIT_SUBS:
        url = f"https://old.reddit.com/r/{sub}/hot.json?limit=10"
        data = http_get(url, headers={"User-Agent": "GlobalSentinel/1.0"})
        if not data:
            blocked = True
            continue

        children = data.get("data", {}).get("children", [])
        for child in children:
            d = child.get("data", {})
            if d.get("stickied"):
                continue
            item = {
                "source": "reddit",
                "subreddit": sub,
                "title": d.get("title", ""),
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "url": d.get("url", ""),
                "selftext": (d.get("selftext") or "")[:200],
                "created_utc": d.get("created_utc", 0),
            }
            all_results.append(item)
        print(f"  [REDDIT] r/{sub} => {len(children)} posts")
        time.sleep(1.0)  # Be polite to Reddit

    # Fallback: use SerpAPI to search Reddit if direct access is blocked
    if blocked and not all_results and SERP_API_KEY:
        print("  [REDDIT] Direct access blocked — falling back to SerpAPI Reddit search")
        reddit_queries = [
            "Iran war oil site:reddit.com",
            "Hormuz shipping disruption site:reddit.com",
            "oil stocks trading site:reddit.com/r/wallstreetbets",
        ]
        for q in reddit_queries:
            params = urllib.parse.urlencode({
                "q": q, "api_key": SERP_API_KEY,
                "engine": "google", "num": 10,
            })
            data = http_get(f"https://serpapi.com/search.json?{params}")
            if not data:
                continue
            for r in data.get("organic_results", []):
                item = {
                    "source": "reddit_serp",
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", "")[:200],
                    "url": r.get("link", ""),
                    "score": 0,
                }
                all_results.append(item)
            count = len(data.get("organic_results", []))
            print(f"  [REDDIT-SERP] '{q[:40]}...' => {count} results")
            time.sleep(0.5)

    return all_results


def fetch_finnhub_news() -> List[Dict[str, Any]]:
    """Fetch general news from Finnhub."""
    if not FINNHUB_API_KEY:
        print("  [FINNHUB] No API key, skipping")
        return []

    url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
    data = http_get(url)
    if not data or not isinstance(data, list):
        return []

    results = []
    for art in data[:30]:
        item = {
            "source": "finnhub",
            "title": art.get("headline", ""),
            "summary": (art.get("summary") or "")[:300],
            "news_source": art.get("source", ""),
            "datetime": art.get("datetime", 0),
            "url": art.get("url", ""),
        }
        results.append(item)
    print(f"  [FINNHUB] => {len(results)} articles")
    return results


def fetch_gdelt() -> List[Dict[str, Any]]:
    """Fetch articles from GDELT DOC API (free, no key). Retry once on 429."""
    url = "https://api.gdeltproject.org/api/v2/doc/doc?query=iran%20war%20oil&mode=ArtList&maxrecords=20&format=json"
    data = http_get(url, timeout=20)
    if not data:
        # GDELT often 429s — wait and retry once
        print("  [GDELT] First attempt failed, retrying in 10s...")
        time.sleep(10)
        data = http_get(url, timeout=20)
    if not data:
        return []

    articles = data.get("articles", [])
    results = []
    for art in articles:
        item = {
            "source": "gdelt",
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "seendate": art.get("seendate", ""),
            "domain": art.get("domain", ""),
            "tone": art.get("tone", 0),
        }
        results.append(item)
    print(f"  [GDELT] => {len(results)} articles")
    return results


def fetch_yahoo_quotes() -> Dict[str, Dict[str, Any]]:
    """Fetch real-time quotes from Yahoo Finance."""
    quotes = {}
    for symbol, name in YAHOO_SYMBOLS:
        encoded = urllib.parse.quote(symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1m&range=1d"
        data = http_get(url)
        if not data:
            # Try v10 spark as fallback
            url2 = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=5d"
            data = http_get(url2)
        if data:
            result = data.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                price = meta.get("regularMarketPrice", 0)
                prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                quotes[symbol] = {
                    "name": name,
                    "price": price,
                    "prev_close": prev_close,
                    "change_pct": round(change_pct, 2),
                }
                print(f"  [YAHOO] {name}: ${price:,.2f} ({change_pct:+.2f}%)")
            else:
                print(f"  [YAHOO] {name}: no result data")
        time.sleep(0.3)

    return quotes


def fetch_alpaca_crypto() -> Dict[str, Dict[str, Any]]:
    """Fetch latest crypto quotes from Alpaca (24/7)."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("  [ALPACA] No API keys, skipping crypto")
        return {}

    url = f"https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes?symbols={CRYPTO_SYMBOLS}"
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    data = http_get(url, headers=headers)
    if not data:
        return {}

    quotes = data.get("quotes", {})
    results = {}
    for sym, q in quotes.items():
        mid = (q.get("ap", 0) + q.get("bp", 0)) / 2 if q.get("ap") and q.get("bp") else 0
        results[sym] = {
            "ask": q.get("ap", 0),
            "bid": q.get("bp", 0),
            "mid": round(mid, 2),
        }
        print(f"  [CRYPTO] {sym}: ${mid:,.2f}")

    return results


def compute_fear_greed_proxy(quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute a fear & greed proxy from VIX level."""
    vix = quotes.get("^VIX", {})
    vix_level = vix.get("price", 0)
    if vix_level > 35:
        label = "EXTREME FEAR"
        score = 10
    elif vix_level > 30:
        label = "FEAR"
        score = 25
    elif vix_level > 25:
        label = "ELEVATED FEAR"
        score = 35
    elif vix_level > 20:
        label = "NERVOUS"
        score = 45
    elif vix_level > 15:
        label = "NEUTRAL"
        score = 55
    elif vix_level > 12:
        label = "GREED"
        score = 70
    else:
        label = "EXTREME GREED"
        score = 85

    return {"vix": vix_level, "label": label, "score": score}


# ---------------------------------------------------------------------------
# Intelligence analysis
# ---------------------------------------------------------------------------

def classify_item(text: str) -> List[Tuple[str, str]]:
    """Classify a text item into impact buckets. Returns list of (bucket, matched_keyword)."""
    text_lower = text.lower()
    matches = []
    for bucket_name, bucket_info in IMPACT_BUCKETS.items():
        for kw in bucket_info["keywords"]:
            if kw in text_lower:
                matches.append((bucket_name, kw))
                break  # One match per bucket is enough
    return matches


def analyze_intelligence(
    serp_news: List[Dict],
    reddit_posts: List[Dict],
    finnhub_news: List[Dict],
    gdelt_articles: List[Dict],
) -> Dict[str, Dict[str, Any]]:
    """Score all impact buckets based on intelligence gathered."""
    # Reset scores
    buckets = {}
    for name in IMPACT_BUCKETS:
        buckets[name] = {"score": 0, "signals": [], "count": 0}

    # Process all items — deduplicate by title to prevent score inflation
    all_items = []
    seen_titles = set()
    for item in serp_news:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("SERP", item.get("title", "") + " " + item.get("snippet", ""), item))
    for item in reddit_posts:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("REDDIT", item.get("title", "") + " " + item.get("selftext", ""), item))
    for item in finnhub_news:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("FINNHUB", item.get("title", "") + " " + item.get("summary", ""), item))
    for item in gdelt_articles:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("GDELT", item.get("title", ""), item))

    for source_tag, text, item in all_items:
        matches = classify_item(text)
        for bucket_name, keyword in matches:
            buckets[bucket_name]["count"] += 1
            title = item.get("title", item.get("headline", ""))[:80]
            signal_entry = f"[{source_tag}] {title}"
            if len(buckets[bucket_name]["signals"]) < 5:
                buckets[bucket_name]["signals"].append(signal_entry)

    # Compute scores (0-10) based on signal count + source diversity
    for name, info in buckets.items():
        count = info["count"]
        if count == 0:
            info["score"] = 0
        elif count <= 2:
            info["score"] = 2
        elif count <= 5:
            info["score"] = 4
        elif count <= 10:
            info["score"] = 6
        elif count <= 20:
            info["score"] = 8
        else:
            info["score"] = 10

    return buckets


def analyze_reddit_sentiment(reddit_posts: List[Dict]) -> Dict[str, Any]:
    """Analyze Reddit sentiment across subreddits."""
    bullish_words = ["bull", "call", "long", "buy", "moon", "rocket", "pump",
                     "breakout", "undervalued", "dip buy", "btfd"]
    bearish_words = ["bear", "put", "short", "sell", "crash", "dump", "tank",
                     "overvalued", "bubble", "recession", "collapse"]

    sentiment = {"bullish": 0, "bearish": 0, "neutral": 0, "top_topics": []}
    topic_counter: Dict[str, int] = {}

    for post in reddit_posts:
        text = (post.get("title", "") + " " + post.get("selftext", "")).lower()
        score = post.get("score", 0)

        b_count = sum(1 for w in bullish_words if w in text)
        s_count = sum(1 for w in bearish_words if w in text)

        if b_count > s_count:
            sentiment["bullish"] += 1
        elif s_count > b_count:
            sentiment["bearish"] += 1
        else:
            sentiment["neutral"] += 1

        # Track top mentioned topics
        for word in ["oil", "gold", "defense", "airline", "vix", "puts",
                     "calls", "iran", "war", "tanker", "crude", "uvxy",
                     "gush", "sqqq", "jets"]:
            if word in text:
                topic_counter[word] = topic_counter.get(word, 0) + score

    # Sort topics by weighted score
    sorted_topics = sorted(topic_counter.items(), key=lambda x: x[1], reverse=True)[:5]
    sentiment["top_topics"] = [t[0] for t in sorted_topics]

    total = sentiment["bullish"] + sentiment["bearish"] + sentiment["neutral"]
    if total > 0:
        if sentiment["bearish"] > sentiment["bullish"]:
            sentiment["label"] = "BEARISH"
        elif sentiment["bullish"] > sentiment["bearish"]:
            sentiment["label"] = "BULLISH"
        else:
            sentiment["label"] = "MIXED"
    else:
        sentiment["label"] = "NO DATA"

    return sentiment


# ---------------------------------------------------------------------------
# Trade idea generation
# ---------------------------------------------------------------------------

def generate_trade_ideas(
    buckets: Dict[str, Dict],
    quotes: Dict[str, Dict],
    crypto: Dict[str, Dict],
    sentiment: Dict[str, Any],
    fear_greed: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Generate ranked trade ideas for $125 capital."""
    ideas: List[Dict[str, Any]] = []

    oil_score = buckets.get("OIL_SUPPLY", {}).get("score", 0)
    energy_score = buckets.get("ENERGY_CASCADE", {}).get("score", 0)
    aviation_score = buckets.get("AVIATION", {}).get("score", 0)
    shipping_score = buckets.get("SHIPPING", {}).get("score", 0)
    defense_score = buckets.get("DEFENSE", {}).get("score", 0)
    safe_haven_score = buckets.get("SAFE_HAVEN", {}).get("score", 0)
    tech_score = buckets.get("TECH_SELLOFF", {}).get("score", 0)
    geopolitical_score = buckets.get("GEOPOLITICAL", {}).get("score", 0)
    food_score = buckets.get("FOOD_CHAIN", {}).get("score", 0)
    inflation_score = buckets.get("INFLATION", {}).get("score", 0)
    vix_level = fear_greed.get("vix", 0)

    oil_price = quotes.get("CL=F", {}).get("price", 0)
    oil_change = quotes.get("CL=F", {}).get("change_pct", 0)

    # 1) Oil play — GUSH (3x bull oil)
    if oil_score >= 4:
        ev_mult = 1.0 + (oil_score / 10) * 0.3  # 10-30% upside estimate
        ideas.append({
            "rank": 0,
            "vehicle": "GUSH",
            "ticker": "GUSH",
            "bucket": "OIL_SUPPLY",
            "direction": "LONG",
            "entry": "Market open 9:30 AM",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * ev_mult, 2),
            "ev": round(CAPITAL * ev_mult, 2),
            "ev_pct": round((ev_mult - 1) * 100, 1),
            "signal": f"Oil supply score {oil_score}/10, crude at ${oil_price:.0f} ({oil_change:+.1f}%)",
            "risk": "3x leverage cuts both ways — stop at -10%",
            "confidence": min(oil_score * 10, 90),
            "note": "3x Bull Oil ETF — pure Hormuz play",
        })

    # 2) UCO — 2x oil, less volatile than GUSH
    if oil_score >= 5:
        ideas.append({
            "rank": 0,
            "vehicle": "UCO",
            "ticker": "UCO",
            "bucket": "OIL_SUPPLY",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.2, 2),
            "ev": round(CAPITAL * 1.2, 2),
            "ev_pct": 20,
            "signal": f"Oil supply disruption score {oil_score}/10, 2x oil ETF safer than 3x",
            "risk": "Lower leverage = less downside but also less upside than GUSH",
            "confidence": min(oil_score * 8, 80),
            "note": "2x Bull Oil ETF — moderate leverage",
        })

    # 3) FAA (inverse airlines via TPOR) or fractional GLD hedge
    #    Can't short JETS on cash account. Use inverse energy/airline plays instead.
    if aviation_score >= 5:
        # High aviation disruption → buy more oil (airlines losing = oil winning)
        ideas.append({
            "rank": 0,
            "vehicle": "XLE",
            "ticker": "XLE",
            "bucket": "AVIATION",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": min(CAPITAL, 40),
            "ev_usd": round(min(CAPITAL, 40) * 1.15, 2),
            "ev": round(min(CAPITAL, 40) * 1.15, 2),
            "ev_pct": 15,
            "signal": f"Aviation disruption score {aviation_score}/10 → energy sector benefits",
            "risk": "Airlines priced in, but XLE benefits from same catalyst (high oil)",
            "confidence": min(aviation_score * 9, 70),
            "note": "Energy Select Sector ETF — airline pain = oil gain",
        })

    # 4) UVXY — volatility spike
    if vix_level > 20 or tech_score >= 4:
        ideas.append({
            "rank": 0,
            "vehicle": "UVXY",
            "ticker": "UVXY",
            "bucket": "TECH_SELLOFF",
            "direction": "LONG",
            "entry": "Shares at open, sell by 11 AM if no spike",
            "capital": min(CAPITAL, 60),
            "ev_usd": round(min(CAPITAL, 60) * 1.8, 2),
            "ev": round(min(CAPITAL, 60) * 1.8, 2),
            "ev_pct": 80,
            "signal": f"VIX at {vix_level:.1f}, tech selloff score {tech_score}/10",
            "risk": "VIX contango decay. Intraday only — sell before close.",
            "confidence": 60 if vix_level > 25 else 45,
            "note": "1.5x VIX short-term futures ETF — vol spike play",
        })

    # 5) SQQQ — inverse Nasdaq (3x) — BUY this to short Nasdaq
    if tech_score >= 5:
        ideas.append({
            "rank": 0,
            "vehicle": "SQQQ",
            "ticker": "SQQQ",
            "bucket": "TECH_SELLOFF",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.2, 2),
            "ev": round(CAPITAL * 1.2, 2),
            "ev_pct": 20,
            "signal": f"Tech selloff score {tech_score}/10, oil-driven inflation fear",
            "risk": "Any de-escalation reverses this fast",
            "confidence": min(tech_score * 8, 70),
            "note": "3x Short Nasdaq — tech selloff play",
        })

    # 6) STNG — tanker stocks (single ticker, most liquid)
    if shipping_score >= 4:
        ideas.append({
            "rank": 0,
            "vehicle": "STNG",
            "ticker": "STNG",
            "bucket": "SHIPPING",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.15, 2),
            "ev": round(CAPITAL * 1.15, 2),
            "ev_pct": 15,
            "signal": f"Shipping/tanker score {shipping_score}/10, war risk premiums soaring",
            "risk": "Already extended? Check premarket gap before entry.",
            "confidence": min(shipping_score * 9, 75),
            "note": "Scorpio Tankers — Hormuz rerouting = tanker demand surge",
        })

    # 7) ITA — defense ETF (single ticker)
    if defense_score >= 4:
        ideas.append({
            "rank": 0,
            "vehicle": "ITA",
            "ticker": "ITA",
            "bucket": "DEFENSE",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.12, 2),
            "ev": round(CAPITAL * 1.12, 2),
            "ev_pct": 12,
            "signal": f"Defense spending score {defense_score}/10",
            "risk": "Defense usually front-runs conflict. May be late.",
            "confidence": min(defense_score * 8, 65),
            "note": "iShares U.S. Aerospace & Defense ETF",
        })

    # 8) GLD — gold (single ticker, most liquid)
    if safe_haven_score >= 4:
        ideas.append({
            "rank": 0,
            "vehicle": "GLD",
            "ticker": "GLD",
            "bucket": "SAFE_HAVEN",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.18, 2),
            "ev": round(CAPITAL * 1.18, 2),
            "ev_pct": 18,
            "signal": f"Safe haven score {safe_haven_score}/10, flight to gold",
            "risk": "Gold at highs — watch for reversal if de-escalation",
            "confidence": min(safe_haven_score * 8, 70),
            "note": "SPDR Gold Trust — pure safe haven play",
        })

    # 9) OPTIONS: JETS put — defined risk airline short
    #    Dynamic expiry: nearest Friday 7-14 days out for weekly decay/value balance
    if aviation_score >= 3 or oil_score >= 6:
        from datetime import date
        today = date.today()
        # Find next Friday 7-14 days out
        days_to_fri = (4 - today.weekday()) % 7 or 7
        if days_to_fri < 7:
            days_to_fri += 7
        exp_date = today + timedelta(days=days_to_fri)
        exp_str = exp_date.strftime("%y%m%d")
        # Dynamic JETS strike: fetch current JETS price estimate from Yahoo, or use $20 default
        jets_price = quotes.get("JETS", {}).get("price", 0)
        if jets_price <= 0:
            jets_price = 20  # reasonable default
        jets_strike = round(jets_price * 0.95 / 1) * 1  # ~5% OTM put, round to $1
        jets_strike = max(jets_strike, 10)  # floor at $10
        jets_strike_str = f"{int(jets_strike * 1000):08d}"
        jets_sym = f"JETS{exp_str}P{jets_strike_str}"
        ideas.append({
            "rank": 0,
            "vehicle": jets_sym,
            "ticker": jets_sym,
            "bucket": "AVIATION_OPT",
            "direction": "LONG",
            "order_type": "option",
            "entry": f"1 contract at open — JETS ${jets_strike:.0f} put exp {exp_date.strftime('%m/%d')}",
            "capital": 125,
            "ev_usd": round(125 * 2.5, 2),
            "ev": round(125 * 2.5, 2),
            "ev_pct": 150,
            "signal": f"Aviation score {aviation_score}/10, oil at ${oil_price:.0f} crushing airline margins",
            "risk": "Max loss = premium paid. Time decay if JETS doesn't drop.",
            "confidence": min((oil_score + aviation_score) * 5, 85),
            "note": f"JETS ${jets_strike:.0f} put {exp_date.strftime('%m/%d')} — defined risk airline short (dynamic strike)",
        })

    # 10) OPTIONS: USO call — leveraged oil upside (dynamic expiry + strike)
    if oil_score >= 6:
        from datetime import date
        today = date.today()
        days_to_fri = (4 - today.weekday()) % 7 or 7
        if days_to_fri < 7:
            days_to_fri += 7
        exp_date = today + timedelta(days=days_to_fri)
        exp_str = exp_date.strftime("%y%m%d")
        # Dynamic strike: ~5% OTM based on current oil price mapped to USO
        uso_est = oil_price * 1.1 if oil_price > 0 else 80  # rough USO estimate
        uso_strike = round(uso_est * 1.05 / 5) * 5  # round to nearest $5
        uso_strike_str = f"{int(uso_strike * 1000):08d}"
        uso_sym = f"USO{exp_str}C{uso_strike_str}"
        ideas.append({
            "rank": 0,
            "vehicle": uso_sym,
            "ticker": uso_sym,
            "bucket": "OIL_SUPPLY_OPT",
            "direction": "LONG",
            "order_type": "option",
            "entry": f"1 contract at open — USO ${uso_strike:.0f} call exp {exp_date.strftime('%m/%d')}",
            "capital": 125,
            "ev_usd": round(125 * 3, 2),
            "ev": round(125 * 3, 2),
            "ev_pct": 200,
            "signal": f"Oil score {oil_score}/10, crude at ${oil_price:.0f}, USO ${uso_strike} ~5% OTM",
            "risk": "Premium elevated at open due to gap. Max loss = premium.",
            "confidence": min(oil_score * 8, 80),
            "note": f"USO ${uso_strike:.0f} call {exp_date.strftime('%m/%d')} — leveraged oil, defined risk",
        })

    # 11) OPTIONS: GLD call — gold safe haven with leverage (dynamic expiry + strike)
    if safe_haven_score >= 5:
        from datetime import date
        today = date.today()
        days_to_fri = (4 - today.weekday()) % 7 or 7
        if days_to_fri < 7:
            days_to_fri += 7
        exp_date = today + timedelta(days=days_to_fri)
        exp_str = exp_date.strftime("%y%m%d")
        # GLD strike: ~2% OTM from gold futures price / 10 (rough GLD mapping)
        gold_price = quotes.get("GC=F", {}).get("price", 0)
        gld_est = gold_price / 10 if gold_price > 0 else 260  # GLD ≈ gold/10
        gld_strike = round(gld_est * 1.02 / 5) * 5
        gld_strike_str = f"{int(gld_strike * 1000):08d}"
        gld_sym = f"GLD{exp_str}C{gld_strike_str}"
        ideas.append({
            "rank": 0,
            "vehicle": gld_sym,
            "ticker": gld_sym,
            "bucket": "SAFE_HAVEN_OPT",
            "direction": "LONG",
            "order_type": "option",
            "entry": f"1 contract at open — GLD ${gld_strike:.0f} call exp {exp_date.strftime('%m/%d')}",
            "capital": 125,
            "ev_usd": round(125 * 2.5, 2),
            "ev": round(125 * 2.5, 2),
            "ev_pct": 150,
            "signal": f"Safe haven score {safe_haven_score}/10, gold at ${gold_price:.0f}, GLD ~${gld_est:.0f}",
            "risk": "Premium reprices at open. Max loss = premium paid.",
            "confidence": min(safe_haven_score * 8, 70),
            "note": f"GLD ${gld_strike:.0f} call {exp_date.strftime('%m/%d')} — leveraged gold via options",
        })

    # -----------------------------------------------------------------------
    # 2nd/3rd order effects — indirect plays from Iran war / oil disruption
    # -----------------------------------------------------------------------

    # 12) MOS — fertilizer/food chain (oil → fertilizer costs → food prices)
    if food_score >= 3 or (oil_score >= 6 and energy_score >= 4):
        fert_conf = max(food_score, oil_score - 2) * 8
        ideas.append({
            "rank": 0,
            "vehicle": "MOS",
            "ticker": "MOS",
            "bucket": "FOOD_CHAIN",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.18, 2),
            "ev": round(CAPITAL * 1.18, 2),
            "ev_pct": 18,
            "signal": f"Food chain score {food_score}/10, oil at ${oil_price:.0f} → fertilizer cost surge",
            "risk": "Slower-moving trade — may take days to fully price in",
            "confidence": min(fert_conf, 65),
            "note": "Mosaic Co — fertilizer producer, 2nd-order oil/natgas → food chain play",
        })

    # 13) VLO — refining crack spread (crude spike → refining margin expansion)
    if oil_score >= 5 and energy_score >= 3:
        ideas.append({
            "rank": 0,
            "vehicle": "VLO",
            "ticker": "VLO",
            "bucket": "ENERGY_CASCADE",
            "direction": "LONG",
            "entry": "Shares at open",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.20, 2),
            "ev": round(CAPITAL * 1.20, 2),
            "ev_pct": 20,
            "signal": f"Oil {oil_score}/10, energy cascade {energy_score}/10 → crack spread widens",
            "risk": "Refining margins can compress if demand drops with price spikes",
            "confidence": min((oil_score + energy_score) * 5, 72),
            "note": "Valero Energy — refining crack spread play, oil spike = margin expansion",
        })

    # 14) EEM short via inverse ETF (EM capital flight on oil shock)
    if oil_score >= 6 and geopolitical_score >= 5:
        ideas.append({
            "rank": 0,
            "vehicle": "EWZ",
            "ticker": "EWZ",
            "bucket": "GEOPOLITICAL",
            "direction": "LONG",
            "entry": "Shares at open — EM oil exporters benefit (Brazil)",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.14, 2),
            "ev": round(CAPITAL * 1.14, 2),
            "ev_pct": 14,
            "signal": f"Oil {oil_score}/10, geopolitical {geopolitical_score}/10 → EM oil exporter gains",
            "risk": "EM risk-off may overpower oil exporter thesis",
            "confidence": min((oil_score + geopolitical_score) * 4, 60),
            "note": "Brazil ETF — EM oil exporter benefits from Hormuz disruption + Petrobras",
        })

    # 15) KOLD — inverse nat gas (if nat gas hasn't spiked yet, it will)
    if energy_score >= 5 and oil_score >= 5:
        ideas.append({
            "rank": 0,
            "vehicle": "BOIL",
            "ticker": "BOIL",
            "bucket": "ENERGY_CASCADE",
            "direction": "LONG",
            "entry": "Shares at open — nat gas 2x bull",
            "capital": min(CAPITAL, 50),
            "ev_usd": round(min(CAPITAL, 50) * 1.35, 2),
            "ev": round(min(CAPITAL, 50) * 1.35, 2),
            "ev_pct": 35,
            "signal": f"Energy cascade {energy_score}/10 — LNG disruption lagging oil spike",
            "risk": "2x leverage, nat gas is volatile. Intraday preferred.",
            "confidence": min(energy_score * 9, 68),
            "note": "ProShares Ultra Bloomberg Natural Gas — 2nd order energy cascade",
        })

    # 16) TIP — inflation hedge (oil spike → inflation expectation repricing)
    if inflation_score >= 4 or (oil_score >= 7 and oil_change > 3):
        ideas.append({
            "rank": 0,
            "vehicle": "TIP",
            "ticker": "TIP",
            "bucket": "INFLATION",
            "direction": "LONG",
            "entry": "Shares at open — TIPS ETF",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.08, 2),
            "ev": round(CAPITAL * 1.08, 2),
            "ev_pct": 8,
            "signal": f"Inflation score {inflation_score}/10, oil ${oil_price:.0f} → CPI repricing",
            "risk": "Low volatility play — small but consistent if inflation thesis holds",
            "confidence": min(max(inflation_score, oil_score - 2) * 8, 55),
            "note": "iShares TIPS Bond ETF — inflation hedge, oil shock → CPI repricing",
        })

    # 17) LNG — Cheniere Energy (Qatar LNG shutdown = US LNG fills the gap)
    if energy_score >= 4 or (oil_score >= 6 and shipping_score >= 5):
        ideas.append({
            "rank": 0,
            "vehicle": "LNG",
            "ticker": "LNG",
            "bucket": "ENERGY_CASCADE",
            "direction": "LONG",
            "entry": "Shares at open — US LNG exporter",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.25, 2),
            "ev": round(CAPITAL * 1.25, 2),
            "ev_pct": 25,
            "signal": f"Energy cascade {energy_score}/10 + shipping {shipping_score}/10 — Qatar LNG halted, US fills gap",
            "risk": "Already extended if LNG gapped up pre-market. Check price vs. 5-day avg",
            "confidence": min((energy_score + shipping_score) * 5, 78),
            "note": "Cheniere Energy — largest US LNG exporter, direct Qatar shutdown beneficiary",
        })

    # 18) FRO — Frontline (VLCC tanker rates at all-time highs from Hormuz rerouting)
    if shipping_score >= 5 or (oil_score >= 7 and shipping_score >= 3):
        ideas.append({
            "rank": 0,
            "vehicle": "FRO",
            "ticker": "FRO",
            "bucket": "SHIPPING",
            "direction": "LONG",
            "entry": "Shares at open — VLCC tanker play",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.30, 2),
            "ev": round(CAPITAL * 1.30, 2),
            "ev_pct": 30,
            "signal": f"Shipping {shipping_score}/10, oil {oil_score}/10 — VLCC rates at all-time high $423K/day",
            "risk": "Tanker stocks can whipsaw on ceasefire headlines. Pure momentum play.",
            "confidence": min(shipping_score * 10, 82),
            "note": "Frontline Ltd — VLCC tanker company, Hormuz rerouting = massive rate surge",
        })

    # 19) CF — CF Industries (fertilizer — 1/3 of global urea transits Hormuz)
    if food_score >= 4 or (oil_score >= 7 and shipping_score >= 5):
        ideas.append({
            "rank": 0,
            "vehicle": "CF",
            "ticker": "CF",
            "bucket": "FOOD_CHAIN",
            "direction": "LONG",
            "entry": "Shares at open — nitrogen fertilizer play",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.22, 2),
            "ev": round(CAPITAL * 1.22, 2),
            "ev_pct": 22,
            "signal": f"Food chain {food_score}/10 — 1/3 of global urea transits Hormuz, now blocked",
            "risk": "Fertilizer price pass-through takes days/weeks. Slower-moving 2nd order play.",
            "confidence": min(max(food_score, shipping_score) * 8, 68),
            "note": "CF Industries — nitrogen fertilizer, direct Hormuz urea blockade beneficiary",
        })

    # 20) LMT — Lockheed Martin (defense, $194B backlog, F-35 + missile systems)
    if defense_score >= 5 or geopolitical_score >= 7:
        ideas.append({
            "rank": 0,
            "vehicle": "LMT",
            "ticker": "LMT",
            "bucket": "DEFENSE",
            "direction": "LONG",
            "entry": "Shares at open — prime defense contractor",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.15, 2),
            "ev": round(CAPITAL * 1.15, 2),
            "ev_pct": 15,
            "signal": f"Defense {defense_score}/10, geopolitical {geopolitical_score}/10 — $194B backlog, active conflict",
            "risk": "Defense stocks front-run conflicts. May be priced in. Check pre-market gap.",
            "confidence": min(max(defense_score, geopolitical_score) * 8, 72),
            "note": "Lockheed Martin — F-35, missiles, $194B backlog. Structural winner from conflict.",
        })

    # 21) PANW — Palo Alto Networks (Iran cyber retaliation is known playbook)
    if geopolitical_score >= 6 and defense_score >= 4:
        ideas.append({
            "rank": 0,
            "vehicle": "PANW",
            "ticker": "PANW",
            "bucket": "DEFENSE",
            "direction": "LONG",
            "entry": "Shares at open — cybersecurity play",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.18, 2),
            "ev": round(CAPITAL * 1.18, 2),
            "ev_pct": 18,
            "signal": f"Geopolitical {geopolitical_score}/10 — Iran cyber retaliation expected, defense spending up",
            "risk": "Tech names may sell off broadly; PANW benefits from defense but is still tech.",
            "confidence": min(geopolitical_score * 7, 62),
            "note": "Palo Alto Networks — cybersecurity, Iran cyber retaliation = enterprise spending surge",
        })

    # 22) DBA — Agriculture ETF (food supply chain disruption from fertilizer + shipping)
    if food_score >= 3 and (shipping_score >= 4 or oil_score >= 6):
        ideas.append({
            "rank": 0,
            "vehicle": "DBA",
            "ticker": "DBA",
            "bucket": "FOOD_CHAIN",
            "direction": "LONG",
            "entry": "Shares at open — broad agriculture",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.12, 2),
            "ev": round(CAPITAL * 1.12, 2),
            "ev_pct": 12,
            "signal": f"Food {food_score}/10, shipping {shipping_score}/10 — fertilizer + freight cascade → food prices",
            "risk": "Slow-moving macro trade. DBA has low volatility. Better as portfolio hedge.",
            "confidence": min((food_score + shipping_score) * 4, 55),
            "note": "Invesco DB Agriculture Fund — broad food commodity exposure, 3rd-order war play",
        })

    # 23) KTOS — Kratos Defense (drone/UAV manufacturer — drones central to this conflict)
    if defense_score >= 5 and geopolitical_score >= 5:
        ideas.append({
            "rank": 0,
            "vehicle": "KTOS",
            "ticker": "KTOS",
            "bucket": "DEFENSE",
            "direction": "LONG",
            "entry": "Shares at open — drone/UAV play",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.25, 2),
            "ev": round(CAPITAL * 1.25, 2),
            "ev_pct": 25,
            "signal": f"Defense {defense_score}/10 — drones are central weapon in Iran conflict, Kratos is pure-play UAV",
            "risk": "Small-cap defense, more volatile than LMT/RTX. Higher beta = higher reward/risk.",
            "confidence": min((defense_score + geopolitical_score) * 5, 70),
            "note": "Kratos Defense — pure-play drone/UAV, highest beta defense stock for active conflict",
        })

    # 24) GUSH call option — ALL-IN oil conviction play (highest EV for oil spike)
    if oil_score >= 8 and shipping_score >= 6:
        from datetime import date
        today = date.today()
        days_to_fri = (4 - today.weekday()) % 7 or 7
        if days_to_fri < 7:
            days_to_fri += 7
        exp_date = today + timedelta(days=days_to_fri)
        exp_str = exp_date.strftime("%y%m%d")
        # GUSH strike: fetch current price estimate, ~5-10% OTM
        gush_est = oil_price * 0.5 if oil_price > 0 else 50  # rough GUSH vs WTI mapping
        gush_strike = round(gush_est * 1.08 / 5) * 5  # 8% OTM, round to $5
        gush_strike = max(gush_strike, 5)  # floor
        gush_strike_str = f"{int(gush_strike * 1000):08d}"
        gush_sym = f"GUSH{exp_str}C{gush_strike_str}"
        ideas.append({
            "rank": 0,
            "vehicle": gush_sym,
            "ticker": gush_sym,
            "bucket": "OIL_SUPPLY_OPT",
            "direction": "LONG",
            "order_type": "option",
            "entry": f"1 contract at open — GUSH ${gush_strike:.0f} call exp {exp_date.strftime('%m/%d')}",
            "capital": 125,
            "ev_usd": round(125 * 4.0, 2),
            "ev": round(125 * 4.0, 2),
            "ev_pct": 300,
            "signal": f"EXTREME OIL: {oil_score}/10 + shipping {shipping_score}/10 — 3x leveraged oil call for max upside",
            "risk": "HIGHEST RISK/REWARD: 3x oil ETF call. Can go to zero. Max loss = premium.",
            "confidence": min(oil_score * 9, 88),
            "note": f"GUSH ${gush_strike:.0f} call {exp_date.strftime('%m/%d')} — ALL-IN OIL CONVICTION (3x leveraged call)",
            "conviction_pick": True,
        })

    # === MELTDOWN-AWARE TRADE IDEAS (from Yardeni/Goldman/MS/JPM research) ===
    # Key insight: 35% recession probability, stagflation trap if oil stays >$100
    # Duration of Hormuz closure is the key variable — multi-month = global recession

    # 25) TLT — Long-term Treasuries (flight to safety if recession materializes)
    # Goldman + JPM both say rates will plunge if recession hits — TLT surges
    meltdown_prob = 0.35  # consensus from Yardeni + JPM
    if oil_price >= 95 and (safe_haven_score >= 3 or geopolitical_score >= 6):
        # Stagflation paradox: oil high → recession → Fed eventually cuts → bonds rally
        tlt_ev = 1.10 + (meltdown_prob * 0.3)  # 10-20% upside if recession
        ideas.append({
            "rank": 0,
            "vehicle": "TLT",
            "ticker": "TLT",
            "bucket": "MELTDOWN_HEDGE",
            "direction": "LONG",
            "entry": "Shares — recession hedge, hold 2-4 weeks",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * tlt_ev, 2),
            "ev": round(CAPITAL * tlt_ev, 2),
            "ev_pct": round((tlt_ev - 1) * 100, 1),
            "signal": f"Meltdown prob 35% (Yardeni/JPM). Oil ${oil_price:.0f} → stagflation → eventual rate cuts → bonds rally",
            "risk": "If Fed stays hawkish despite recession (stagflation), TLT bleeds. Timing dependent.",
            "confidence": 55,
            "note": "iShares 20+ Year Treasury — recession/meltdown hedge (Goldman: 2008 warning signs)",
        })

    # 26) XLV — Healthcare (defensive rotation — Goldman + MS overweight)
    if geopolitical_score >= 5 and oil_price >= 90:
        ideas.append({
            "rank": 0,
            "vehicle": "XLV",
            "ticker": "XLV",
            "bucket": "MELTDOWN_HEDGE",
            "direction": "LONG",
            "entry": "Shares — defensive sector rotation",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.08, 2),
            "ev": round(CAPITAL * 1.08, 2),
            "ev_pct": 8,
            "signal": f"All 4 banks overweight healthcare. War duration unknown → rotate to defensives",
            "risk": "Low beta = low upside. Opportunity cost if oil spike continues.",
            "confidence": 50,
            "note": "Health Care Select SPDR — defensive rotation play (MS + Goldman overweight)",
        })

    # 27) XLP — Consumer Staples (recession-proof, JPM recommended)
    if oil_price >= 100 and tech_score >= 4:
        ideas.append({
            "rank": 0,
            "vehicle": "XLP",
            "ticker": "XLP",
            "bucket": "MELTDOWN_HEDGE",
            "direction": "LONG",
            "entry": "Shares — consumer staples hold up in stagflation",
            "capital": CAPITAL,
            "ev_usd": round(CAPITAL * 1.06, 2),
            "ev": round(CAPITAL * 1.06, 2),
            "ev_pct": 6,
            "signal": f"JPM: 35% recession. Oil ${oil_price:.0f} crushing consumers. Staples outperform in downturn",
            "risk": "Very low upside potential. Pure capital preservation play.",
            "confidence": 45,
            "note": "Consumer Staples Select SPDR — Dimon's 'worse than normal' credit cycle hedge",
        })

    # Sort by expected value
    ideas.sort(key=lambda x: x["ev_usd"], reverse=True)
    for i, idea in enumerate(ideas):
        idea["rank"] = i + 1

    return ideas[:16]  # Top 16 (expanded — includes meltdown hedges)


# ---------------------------------------------------------------------------
# Strategy modes — the 5 playing cards
# ---------------------------------------------------------------------------
# Each strategy is scored dynamically every cycle based on signal data.
# The engine picks the highest-scoring strategy for the current regime.

STRATEGY_MODES = {
    "FRACTIONAL_ONLY": {
        "label": "Fractional Only (Conservative)",
        "description": "100% fractional shares across 3-5 tickers. No options. No expiration risk.",
        "min_confidence": 0,  # always available as fallback
    },
    "ASYMMETRIC_SPLIT": {
        "label": "Asymmetric Split (Balanced)",
        "description": "~45% fractional + ~35% long put + ~20% long call. Mixed leverage.",
        "min_confidence": 40,
    },
    "SPREAD_MAXIMIZER": {
        "label": "Spread Maximizer (Defined Risk)",
        "description": "~50% bull call spread + ~30% bear put spread + ~20% fractional anchor.",
        "min_confidence": 50,
    },
    "ALL_IN_CONVICTION": {
        "label": "All-In Conviction (Maximum Leverage)",
        "description": "~65% long call + ~35% long put. No fractional safety net. 0 or hero.",
        "min_confidence": 70,
    },
    "MIXED_ARSENAL": {
        "label": "Mixed Arsenal (Full Playbook)",
        "description": "Best of all strategies: fractional + calls + puts + spreads. Dynamically weighted.",
        "min_confidence": 55,
    },
}


def compute_strategy_scores(
    buckets: Dict[str, Dict],
    quotes: Dict[str, Dict],
    ideas: List[Dict],
    fear_greed: Dict[str, Any],
) -> Dict[str, float]:
    """Score each strategy mode based on current signal strength.

    Heavily weights Iran war / oil disruption signals as the primary driver.
    Returns dict of {strategy_name: score} where higher = better fit.
    """
    oil_score = buckets.get("OIL_SUPPLY", {}).get("score", 0)
    energy_score = buckets.get("ENERGY_CASCADE", {}).get("score", 0)
    aviation_score = buckets.get("AVIATION", {}).get("score", 0)
    shipping_score = buckets.get("SHIPPING", {}).get("score", 0)
    defense_score = buckets.get("DEFENSE", {}).get("score", 0)
    safe_haven_score = buckets.get("SAFE_HAVEN", {}).get("score", 0)
    geopolitical_score = buckets.get("GEOPOLITICAL", {}).get("score", 0)
    vix_level = fear_greed.get("vix", 0)

    oil_price = quotes.get("CL=F", {}).get("price", 0)
    oil_change_pct = abs(quotes.get("CL=F", {}).get("change_pct", 0))
    brent_price = quotes.get("BZ=F", {}).get("price", 0)
    natgas_change = abs(quotes.get("NG=F", {}).get("change_pct", 0))
    gasoline_change = abs(quotes.get("RB=F", {}).get("change_pct", 0))

    # Brent-WTI spread widening = Hormuz-specific signal (Brent is more ME-exposed)
    brent_wti_spread = (brent_price - oil_price) if brent_price > 0 and oil_price > 0 else 0
    hormuz_spread_signal = min(max(brent_wti_spread - 3, 0) / 5, 1.0)  # normalizes $3-$8 spread to 0-1

    # Energy cascade confirmation: nat gas + gasoline moving together = broad energy shock
    energy_cascade_confirm = min((natgas_change + gasoline_change) / 10, 1.0)

    # === MELTDOWN FACTOR (from Yardeni/Goldman/MS/JPM research) ===
    # Stagflation trap: oil >$100 → Fed can't cut → economy weakens → meltdown
    # Duration is the key variable: 4-week war = buy the dip, multi-month = recession
    meltdown_risk = 0.0
    if oil_price >= 120:
        meltdown_risk = 0.5  # Goldman worst case: oil $150, "2008 warning signs"
    elif oil_price >= 100:
        meltdown_risk = 0.35  # Yardeni + JPM consensus: 35% meltdown
    elif oil_price >= 90:
        meltdown_risk = 0.2  # Elevated but manageable

    # Composite war intensity: Hormuz-weighted formula
    # Oil 3x, geopolitical 2x, shipping 2x (Hormuz = shipping chokepoint), energy 1.5x, rest 1x
    war_intensity = (oil_score * 3 + geopolitical_score * 2 + shipping_score * 2 +
                     energy_score * 1.5 + aviation_score + defense_score) / 10.5

    # Directional conviction: how confident are we in the direction?
    # High oil + high shipping (Hormuz) + high energy cascade = strong supply shock signal
    directional_conviction = (oil_score * 1.5 + shipping_score * 1.3 + aviation_score +
                              energy_score) / 4.8

    # Deescalation risk: check for ceasefire/diplomatic signals in GEOPOLITICAL bucket
    geo_signals = buckets.get("GEOPOLITICAL", {}).get("signals", [])
    deesc_keywords = ["ceasefire", "de-escalation", "peace", "negotiate", "diplomatic", "truce"]
    deesc_count = sum(1 for s in geo_signals if any(k in s.lower() for k in deesc_keywords))
    deesc_risk = min(deesc_count / max(len(geo_signals), 1), 1.0) if geo_signals else 0.0

    # Average idea confidence
    avg_confidence = sum(i.get("confidence", 0) for i in ideas) / max(len(ideas), 1)

    # Number of high-confidence ideas (>70%)
    high_conf_ideas = sum(1 for i in ideas if i.get("confidence", 0) >= 70)

    scores = {}

    # --- FRACTIONAL ONLY ---
    # Best when: low conviction, high deescalation risk, uncertain signals
    # Worst when: strong directional signals (wasting leverage opportunity)
    # Meltdown factor: fractional is SAFER when meltdown risk is high (preserve capital)
    scores["FRACTIONAL_ONLY"] = (
        30  # base score (always viable)
        + deesc_risk * 40  # rises sharply if ceasefire signals detected
        - war_intensity * 3  # drops when war signals are strong (should use leverage)
        - directional_conviction * 2
        + (10 if avg_confidence < 40 else 0)  # low confidence → safer to go fractional
        + meltdown_risk * 20  # meltdown risk → preserve capital in fractional
    )

    # --- ASYMMETRIC SPLIT ---
    # Best when: moderate-strong signals, want leverage + safety net
    # This is the balanced default for SHOCK regime
    scores["ASYMMETRIC_SPLIT"] = (
        50  # solid base for war scenario
        + war_intensity * 4  # rises with war intensity
        + directional_conviction * 3
        - deesc_risk * 25  # drops if ceasefire signals
        + (10 if oil_score >= 6 else 0)  # oil confirmation bonus
        + (10 if aviation_score >= 5 else 0)  # airline pain confirmation
        + (5 if safe_haven_score >= 4 else 0)  # gold running = thesis confirmed
        - (15 if avg_confidence < 40 else 0)  # low confidence penalty
    )

    # --- SPREAD MAXIMIZER ---
    # Best when: strong directional but want defined risk + better breakevens
    # Better than Asymmetric when premiums are expensive (high IV)
    scores["SPREAD_MAXIMIZER"] = (
        40
        + war_intensity * 3
        + directional_conviction * 4  # loves strong direction
        + (15 if vix_level > 25 else 5 if vix_level > 20 else 0)  # high VIX = expensive options = spreads better
        + (10 if oil_change_pct > 5 else 0)  # big moves = spreads capture efficiently
        - deesc_risk * 20
        + (5 if high_conf_ideas >= 2 else 0)  # need multiple high-conf ideas for spread selection
        - (10 if avg_confidence < 50 else 0)
    )

    # --- ALL-IN CONVICTION ---
    # Best when: extreme signals, zero deescalation, maximum conviction
    # This is the "war intensifies" play — everything on the line
    # Meltdown factor: paradoxically HELPS short-term (oil spike = GUSH spike)
    # but adds tail risk if war extends beyond 4 weeks (Goldman: recession)
    scores["ALL_IN_CONVICTION"] = (
        20  # low base — need signals to justify
        + war_intensity * 6  # rises fast with war intensity
        + directional_conviction * 5
        - deesc_risk * 50  # HEAVILY penalized by ceasefire signals
        + (20 if oil_score >= 8 else 0)  # extreme oil signal = green light
        + (15 if aviation_score >= 7 else 0)  # extreme aviation signal
        + (10 if geopolitical_score >= 8 else 0)  # extreme geopolitical escalation
        - (30 if avg_confidence < 60 else 0)  # need high average confidence
        - (20 if deesc_count > 0 else 0)  # any ceasefire signal = big penalty
        + (15 if oil_price >= 110 else 5 if oil_price >= 100 else -10)  # oil price confirms thesis
        + hormuz_spread_signal * 15  # Brent-WTI spread widening = Hormuz-specific confirmation
        + energy_cascade_confirm * 10  # broad energy shock = full escalation likely
        - meltdown_risk * 15  # high meltdown risk adds caution (stagflation trap)
    )

    # --- MIXED ARSENAL ---
    # Best when: multiple strong signals across different sectors
    # Uses the full playbook: fractional + calls + puts + spreads
    # Meltdown factor: MIXED benefits from hedging — can include TLT/XLV alongside GUSH
    scores["MIXED_ARSENAL"] = (
        35
        + war_intensity * 4
        + directional_conviction * 3
        + high_conf_ideas * 8  # loves having many strong ideas to pick from
        + (10 if len(ideas) >= 6 else 0)  # needs a full menu of ideas
        - deesc_risk * 25
        + (10 if oil_score >= 5 and aviation_score >= 4 else 0)  # multi-sector confirmation
        + (10 if vix_level > 22 else 0)  # moderate vol = spreads viable within mix
        - (10 if len(ideas) < 4 else 0)  # not enough ideas = can't fill all slots
        + energy_cascade_confirm * 8  # broad energy = more sectors to play
        + hormuz_spread_signal * 5  # Hormuz signal adds conviction for multi-sector
        + meltdown_risk * 15  # meltdown risk HELPS mixed — can hedge with TLT/XLV
    )

    return scores


def select_optimal_strategy(
    ideas: List[Dict],
    buckets: Dict[str, Dict],
    quotes: Dict[str, Dict],
    fear_greed: Dict[str, Any],
) -> str:
    """Pick the highest-scoring strategy for current conditions."""
    scores = compute_strategy_scores(buckets, quotes, ideas, fear_greed)

    # Filter out strategies that don't meet minimum confidence threshold
    avg_confidence = sum(i.get("confidence", 0) for i in ideas) / max(len(ideas), 1)
    eligible = {}
    for name, score in scores.items():
        min_conf = STRATEGY_MODES[name]["min_confidence"]
        if avg_confidence >= min_conf:
            eligible[name] = score

    if not eligible:
        return "FRACTIONAL_ONLY"

    winner = max(eligible, key=eligible.get)

    # Log the scoring
    print(f"\n  [STRATEGY SELECTOR] Scores:")
    for name in sorted(scores, key=scores.get, reverse=True):
        marker = " <<<" if name == winner else ""
        flag = " (filtered)" if name not in eligible else ""
        print(f"    {name}: {scores[name]:.0f}{flag}{marker}")
    print(f"  [STRATEGY] Selected: {STRATEGY_MODES[winner]['label']}")

    return winner


def build_doubling_plan(
    ideas: List[Dict],
    buckets: Optional[Dict[str, Dict]] = None,
    quotes: Optional[Dict[str, Dict]] = None,
    fear_greed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dynamically select and build the optimal strategy for $125 capital.

    Considers all available strategies (fractional, long calls, long puts,
    spreads, mixed) and picks based on real-time signal data with heavy
    weighting on Iran war / oil disruption intelligence.
    """
    if not ideas:
        return {
            "strategy": "STAND ASIDE",
            "strategy_mode": "NONE",
            "entry": "No high-conviction plays found. Stand aside.",
            "positions": [],
            "target1": "N/A",
            "target2": "N/A",
            "stop": "N/A",
        }

    # Default signal data if not provided (backward compat)
    if buckets is None:
        buckets = {}
    if quotes is None:
        quotes = {}
    if fear_greed is None:
        fear_greed = {}

    # Select optimal strategy
    if buckets:
        strategy_mode = select_optimal_strategy(ideas, buckets, quotes, fear_greed)
        strategy_scores = compute_strategy_scores(buckets, quotes, ideas, fear_greed)
    else:
        strategy_mode = "ASYMMETRIC_SPLIT"
        strategy_scores = {}

    # === MANUAL OVERRIDE: Force ALL_IN_CONVICTION for max profit ===
    # Override active when oil signals are extreme (oil >= 8)
    # Prioritizes highest-EV oil call (GUSH call) with bulk of capital
    oil_override = buckets.get("OIL_SUPPLY", {}).get("score", 0) >= 8
    if oil_override and strategy_mode != "ALL_IN_CONVICTION":
        print(f"  [OVERRIDE] Forcing ALL_IN_CONVICTION (was {strategy_mode}) — oil signal {buckets.get('OIL_SUPPLY', {}).get('score', 0)}/10")
        strategy_mode = "ALL_IN_CONVICTION"

    share_ideas = [i for i in ideas if i.get("order_type") != "option"]
    option_ideas = [i for i in ideas if i.get("order_type") == "option"]
    call_ideas = [o for o in option_ideas if "C0" in o.get("vehicle", "") or "call" in o.get("note", "").lower()]
    put_ideas = [o for o in option_ideas if "P0" in o.get("vehicle", "") or "put" in o.get("note", "").lower()]

    allocations = []
    seen_sectors = set()

    def pick_share(budget: float, max_picks: int = 2) -> float:
        """Pick fractional share positions from different sectors. Returns remaining budget."""
        remaining = budget
        count = 0
        for idea in share_ideas:
            if count >= max_picks or remaining < 10:
                break
            sector = idea.get("bucket", "")
            if sector in seen_sectors:
                continue
            seen_sectors.add(sector)
            if count == 0 and max_picks > 1:
                alloc = round(remaining * 0.55, 2)
            else:
                alloc = remaining
            remaining -= alloc
            entry = {
                "vehicle": idea["vehicle"],
                "direction": idea["direction"],
                "amount": alloc,
                "bucket": sector,
                "confidence": idea.get("confidence", 0),
                "ev": idea.get("ev", 0),
                "strategy_type": "fractional",
            }
            if idea.get("note"):
                entry["note"] = idea["note"]
            allocations.append(entry)
            count += 1
        return remaining

    def pick_option(opt_list: List[Dict], budget: float, label: str = "option") -> float:
        """Pick one option from a new sector. Returns remaining budget."""
        for opt in opt_list:
            base_sector = opt["bucket"].replace("_OPT", "")
            if base_sector in seen_sectors:
                continue
            seen_sectors.add(base_sector)
            entry = {
                "vehicle": opt["vehicle"],
                "direction": opt["direction"],
                "amount": budget,
                "bucket": opt.get("bucket", ""),
                "confidence": opt.get("confidence", 0),
                "ev": opt.get("ev", 0),
                "order_type": "option",
                "strategy_type": label,
            }
            if opt.get("note"):
                entry["note"] = opt["note"]
            allocations.append(entry)
            return 0.0
        return budget

    def pick_spread(opt_list: List[Dict], budget: float, spread_type: str) -> float:
        """Pick a spread position. The vehicle stores buy_leg|sell_leg."""
        for opt in opt_list:
            base_sector = opt["bucket"].replace("_OPT", "")
            if base_sector in seen_sectors:
                continue
            seen_sectors.add(base_sector)
            # For spreads, we mark them and the order executor will handle leg construction
            entry = {
                "vehicle": opt["vehicle"],
                "direction": opt["direction"],
                "amount": budget,
                "bucket": opt.get("bucket", ""),
                "confidence": opt.get("confidence", 0),
                "ev": opt.get("ev", 0),
                "order_type": "spread",
                "spread_type": spread_type,
                "strategy_type": f"{spread_type}_spread",
            }
            if opt.get("note"):
                entry["note"] = f"[SPREAD] {opt['note']}"
            allocations.append(entry)
            return 0.0
        return budget

    # =========================================================================
    # Build allocations based on selected strategy
    # =========================================================================

    if strategy_mode == "FRACTIONAL_ONLY":
        # 100% fractional across 3-5 tickers
        pick_share(CAPITAL, max_picks=5)

    elif strategy_mode == "ASYMMETRIC_SPLIT":
        # ~36% fractional + ~36% put + ~28% call
        frac_budget = round(CAPITAL * 0.36, 2)
        put_budget = round(CAPITAL * 0.36, 2)
        call_budget = round(CAPITAL - frac_budget - put_budget, 2)

        pick_share(frac_budget, max_picks=1)
        leftover = pick_option(put_ideas, put_budget, "long_put")
        leftover += pick_option(call_ideas, call_budget, "long_call")
        # If options not available, redirect to more fractional
        if leftover > 10:
            pick_share(leftover, max_picks=2)

    elif strategy_mode == "SPREAD_MAXIMIZER":
        # ~48% bull call spread + ~32% bear put spread + ~20% fractional anchor
        call_spread_budget = round(CAPITAL * 0.48, 2)
        put_spread_budget = round(CAPITAL * 0.32, 2)
        frac_budget = round(CAPITAL - call_spread_budget - put_spread_budget, 2)

        leftover = pick_spread(call_ideas, call_spread_budget, "bull_call")
        leftover += pick_spread(put_ideas, put_spread_budget, "bear_put")
        frac_budget += leftover
        pick_share(frac_budget, max_picks=1)

    elif strategy_mode == "ALL_IN_CONVICTION":
        # Heavy oil call allocation — max profit mode
        # 75% into highest-EV call (GUSH call), 25% into put hedge
        call_budget = round(CAPITAL * 0.75, 2)
        put_budget = round(CAPITAL - call_budget, 2)

        # Sort calls by EV to ensure highest-EV oil call gets the bulk
        call_ideas_sorted = sorted(call_ideas, key=lambda x: x.get("ev_usd", 0), reverse=True)
        leftover = pick_option(call_ideas_sorted, call_budget, "long_call")
        leftover += pick_option(put_ideas, put_budget, "long_put")
        # If either option unavailable, fall back to fractional for that portion
        if leftover > 10:
            pick_share(leftover, max_picks=2)

    elif strategy_mode == "MIXED_ARSENAL":
        # Full playbook: fractional + call + put + spread, weighted by signal strength
        oil_score = buckets.get("OIL_SUPPLY", {}).get("score", 0)
        aviation_score = buckets.get("AVIATION", {}).get("score", 0)

        # Dynamic weighting based on which signals are strongest
        if oil_score >= 7 and aviation_score >= 5:
            # Both strong — heavy on options
            frac_pct, call_pct, put_pct, spread_pct = 0.20, 0.30, 0.25, 0.25
        elif oil_score >= 6:
            # Oil strong, aviation moderate — tilt toward calls
            frac_pct, call_pct, put_pct, spread_pct = 0.25, 0.35, 0.20, 0.20
        elif aviation_score >= 5:
            # Aviation strong — tilt toward puts
            frac_pct, call_pct, put_pct, spread_pct = 0.25, 0.20, 0.35, 0.20
        else:
            # Moderate signals — balanced
            frac_pct, call_pct, put_pct, spread_pct = 0.30, 0.25, 0.25, 0.20

        frac_budget = round(CAPITAL * frac_pct, 2)
        call_budget = round(CAPITAL * call_pct, 2)
        put_budget = round(CAPITAL * put_pct, 2)
        spread_budget = round(CAPITAL * spread_pct, 2)

        pick_share(frac_budget, max_picks=1)
        leftover = pick_option(call_ideas, call_budget, "long_call")
        leftover += pick_option(put_ideas, put_budget, "long_put")
        leftover += pick_spread(
            [i for i in call_ideas if i["bucket"].replace("_OPT", "") not in seen_sectors] or
            [i for i in put_ideas if i["bucket"].replace("_OPT", "") not in seen_sectors],
            spread_budget, "bull_call"
        )
        if leftover > 10:
            pick_share(leftover, max_picks=2)

    # =========================================================================
    # Format the plan output
    # =========================================================================

    pos_lines = []
    for a in allocations:
        st = a.get("strategy_type", "fractional")
        pos_lines.append(f"  ${a['amount']:.0f} → {a['vehicle']} {a['direction']} [{st}] ({a['bucket']})")

    double_target = CAPITAL * 2
    mode_info = STRATEGY_MODES.get(strategy_mode, {})

    plan = {
        "strategy": f"{mode_info.get('label', strategy_mode)}",
        "strategy_mode": strategy_mode,
        "strategy_description": mode_info.get("description", ""),
        "strategy_scores": strategy_scores,
        "positions": allocations,
        "entry": f"At 9:30 AM open — {len(allocations)} positions ({strategy_mode}):\n" + "\n".join(pos_lines),
        "target1": f"Portfolio hits ${double_target:.0f} → sell original ${CAPITAL:.0f}, keep ${CAPITAL:.0f} profit riding",
        "target2": f"Repeat: ride ${CAPITAL:.0f} profit → double again to ${double_target * 1.5:.0f}",
        "stop": f"Per-position: exit if any single position down 20%. Portfolio: exit all if total down 15% (${CAPITAL * 0.85:.0f})",
        "risk_note": f"Strategy: {strategy_mode} | Diversified across {len(allocations)} positions, {len(set(a['bucket'] for a in allocations))} sectors",
    }

    return plan


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_intel_update(
    cycle_time: datetime,
    buckets: Dict[str, Dict],
    quotes: Dict[str, Dict],
    crypto: Dict[str, Dict],
    ideas: List[Dict],
    sentiment: Dict[str, Any],
    doubling_plan: Dict[str, Any],
    news_count: int,
    breaking: str = "",
) -> str:
    """Format the intelligence update for Telegram."""
    et_str = cycle_time.astimezone(ET).strftime("%H:%M ET")

    oil = quotes.get("CL=F", {})
    gold = quotes.get("GC=F", {})
    vix = quotes.get("^VIX", {})
    nikkei = quotes.get("^N225", {})
    sp_fut = quotes.get("ES=F", {})
    btc = crypto.get("BTC/USD", {})

    lines = [
        f"IRAN WAR INTEL UPDATE [{et_str}]",
        "=" * 36,
    ]

    if breaking:
        lines.append(f"BREAKING: {breaking}")
        lines.append("")

    # Signal strength
    lines.append("SIGNAL STRENGTH (0-10):")
    lines.append(f"  Oil Supply: {buckets.get('OIL_SUPPLY', {}).get('score', 0)}/10 | "
                 f"Energy: {buckets.get('ENERGY_CASCADE', {}).get('score', 0)}/10")
    lines.append(f"  Aviation: {buckets.get('AVIATION', {}).get('score', 0)}/10 | "
                 f"Shipping: {buckets.get('SHIPPING', {}).get('score', 0)}/10")
    lines.append(f"  Defense: {buckets.get('DEFENSE', {}).get('score', 0)}/10 | "
                 f"Safe Haven: {buckets.get('SAFE_HAVEN', {}).get('score', 0)}/10")
    lines.append(f"  Food: {buckets.get('FOOD_CHAIN', {}).get('score', 0)}/10 | "
                 f"Inflation: {buckets.get('INFLATION', {}).get('score', 0)}/10")
    lines.append(f"  Tech Risk: {buckets.get('TECH_SELLOFF', {}).get('score', 0)}/10 | "
                 f"Geopol: {buckets.get('GEOPOLITICAL', {}).get('score', 0)}/10")
    lines.append("")

    # Market pulse
    lines.append("MARKET PULSE:")
    if oil:
        lines.append(f"  Oil: ${oil.get('price', 0):,.2f} ({oil.get('change_pct', 0):+.1f}%)")
    if gold:
        lines.append(f"  Gold: ${gold.get('price', 0):,.2f} ({gold.get('change_pct', 0):+.1f}%)")
    if vix:
        lines.append(f"  VIX: {vix.get('price', 0):.1f} ({vix.get('change_pct', 0):+.1f}%)")
    if nikkei:
        lines.append(f"  Nikkei: {nikkei.get('change_pct', 0):+.1f}%")
    if sp_fut:
        lines.append(f"  S&P Futures: {sp_fut.get('change_pct', 0):+.1f}%")
    if btc:
        lines.append(f"  BTC: ${btc.get('mid', 0):,.0f}")
    lines.append("")

    # Trade ideas
    if ideas:
        lines.append(f"TOP {len(ideas)} TRADE IDEAS (${CAPITAL:.0f} capital):")
        for idea in ideas:
            lines.append(f"{idea['rank']}. {idea['vehicle']} {idea['direction']} "
                         f"-- EV: ${idea['ev_usd']:.0f} (+{idea['ev_pct']:.0f}%)")
            lines.append(f"   Signal: {idea['signal'][:70]}")
        lines.append("")

    # Conviction pick highlight
    conviction_picks = [i for i in ideas if i.get("conviction_pick")]
    if not conviction_picks:
        oil_plays = [i for i in ideas if "OIL" in i.get("bucket", "") or i.get("ticker", "") in ("GUSH", "UCO", "USO")]
        conviction_picks = sorted(oil_plays, key=lambda x: x.get("ev_usd", 0), reverse=True)[:1]
    if conviction_picks:
        cp = conviction_picks[0]
        lines.append(f"CONVICTION PICK: {cp['vehicle']} {cp['direction']} (EV +{cp['ev_pct']:.0f}%, conf {cp.get('confidence',0)}%)")
        lines.append("")

    # Reddit sentiment
    topics_str = ", ".join(sentiment.get("top_topics", [])[:3]) or "none"
    lines.append(f"REDDIT SENTIMENT: {sentiment.get('label', 'N/A')} on [{topics_str}]")
    lines.append(f"NEWS VELOCITY: {news_count} articles this cycle")
    lines.append("")

    # Doubling plan
    strategy_name = doubling_plan.get('strategy', 'DOUBLING PLAN')
    lines.append(f"{strategy_name}:")
    lines.append(f"  Entry: {doubling_plan.get('entry', 'TBD')}")
    lines.append(f"  Target 1: {doubling_plan.get('target1', 'TBD')}")
    lines.append(f"  Target 2: {doubling_plan.get('target2', 'TBD')}")
    lines.append(f"  Stop: {doubling_plan.get('stop', 'TBD')}")
    if doubling_plan.get('risk_note'):
        lines.append(f"  Risk: {doubling_plan['risk_note']}")

    return "\n".join(lines)


def format_morning_brief(
    buckets: Dict[str, Dict],
    quotes: Dict[str, Dict],
    crypto: Dict[str, Dict],
    ideas: List[Dict],
    sentiment: Dict[str, Any],
    doubling_plan: Dict[str, Any],
    fear_greed: Dict[str, Any],
    all_signals: Dict[str, List],
) -> str:
    """Format comprehensive morning brief."""
    now_et = datetime.now(ET).strftime("%H:%M ET")

    lines = [
        "COMPREHENSIVE MORNING BRIEF",
        "=" * 36,
        f"Time: {now_et} | Capital: ${CAPITAL:.0f}",
        "",
        "OVERNIGHT INTELLIGENCE SUMMARY",
        "-" * 30,
    ]

    # Bucket summary with top signals
    for name, info in buckets.items():
        score = info.get("score", 0)
        if score > 0:
            lines.append(f"{name}: {score}/10")
            for sig in info.get("signals", [])[:2]:
                lines.append(f"  -> {sig[:70]}")
    lines.append("")

    # Fear & Greed
    lines.append(f"FEAR & GREED: {fear_greed.get('label', 'N/A')} (VIX: {fear_greed.get('vix', 0):.1f})")
    lines.append("")

    # Market overview
    lines.append("MARKET OVERVIEW:")
    for sym, data in quotes.items():
        lines.append(f"  {data.get('name', sym)}: ${data.get('price', 0):,.2f} ({data.get('change_pct', 0):+.1f}%)")
    for sym, data in crypto.items():
        lines.append(f"  {sym}: ${data.get('mid', 0):,.0f}")
    lines.append("")

    # Trade plan
    lines.append(f"TRADE PLAN (${CAPITAL:.0f}):")
    lines.append("-" * 30)
    for idea in ideas:
        conf = idea.get("confidence", 0)
        lines.append(f"{idea['rank']}. [{conf}% conf] {idea['vehicle']} {idea['direction']}")
        lines.append(f"   EV: ${idea['ev_usd']:.0f} (+{idea['ev_pct']:.0f}%)")
        lines.append(f"   Signal: {idea['signal'][:80]}")
        lines.append(f"   Risk: {idea['risk'][:80]}")
        lines.append("")

    # Conviction pick — the single best all-in play
    conviction_picks = [i for i in ideas if i.get("conviction_pick")]
    if not conviction_picks:
        # Auto-select: highest EV oil play as conviction pick
        oil_plays = [i for i in ideas if "OIL" in i.get("bucket", "") or i.get("ticker", "") in ("GUSH", "UCO", "USO")]
        conviction_picks = sorted(oil_plays, key=lambda x: x.get("ev_usd", 0), reverse=True)[:1]
    if not conviction_picks:
        conviction_picks = ideas[:1]  # fallback to top idea

    if conviction_picks:
        cp = conviction_picks[0]
        lines.append("")
        lines.append("=" * 36)
        lines.append("ALL-IN CONVICTION PICK")
        lines.append("=" * 36)
        lines.append(f"IF YOU COULD ONLY MAKE ONE TRADE:")
        lines.append(f"  {cp['vehicle']} {cp['direction']}")
        lines.append(f"  EV: ${cp['ev_usd']:.0f} (+{cp['ev_pct']:.0f}%)")
        lines.append(f"  Confidence: {cp.get('confidence', 0)}%")
        lines.append(f"  Signal: {cp['signal'][:100]}")
        lines.append(f"  Note: {cp.get('note', '')[:100]}")
        lines.append("")
        lines.append("WHY THIS PICK:")
        lines.append("  - Hormuz physically closed (Day 9, zero crossings)")
        lines.append("  - 20% of global oil offline — this is 1979-class, not 2019")
        lines.append("  - No de-escalation signals — new Supreme Leader pledged retaliation")
        lines.append("  - G7 SPR release may cap but not reverse (supply gap too large)")
        lines.append("  - Brent $108+, VLCC rates all-time high $423K/day")
        lines.append("")
        lines.append("SCENARIOS:")
        lines.append("  BULL: Oil $120+ this week, GUSH/UCO +30-50%")
        lines.append("  BASE: Oil $100-115, energy stocks +10-20%")
        lines.append("  BEAR: G7 SPR release caps at $95, +5% then flat")
        lines.append("  RISK: Ceasefire headline → oil -15%, leveraged ETFs -30%+")
        lines.append("")
        lines.append("BANK MELTDOWN FACTOR (Yardeni/Goldman/MS/JPM):")
        lines.append("  - Meltdown probability: 35% (Yardeni + JPM consensus)")
        lines.append("  - Goldman cut S&P to 6,200, warns oil could hit $150")
        lines.append("  - MS: market 'basically crashed' internally (breadth collapse)")
        lines.append("  - JPM Dimon: 'worse than normal' credit cycle ahead")
        lines.append("  - KEY VARIABLE: Duration. <4 wks = buy dip. >4 wks = recession")
        lines.append("  - STAGFLATION TRAP: Oil >$100 → Fed can't cut → economy weakens")
        lines.append("  - HEDGES: TLT (treasuries), XLV (healthcare), XLP (staples)")
        lines.append("")
        lines.append("ALSO CONSIDER (ideas you may not have thought of):")
        # Find non-obvious plays (including meltdown hedges)
        non_obvious = [i for i in ideas if i.get("ticker", "") in ("LNG", "FRO", "CF", "KTOS", "PANW", "DBA", "NTR", "TLT", "XLV", "XLP")]
        for no_idea in non_obvious[:5]:
            lines.append(f"  - {no_idea['vehicle']}: {no_idea.get('note', '')[:80]}")
        if not non_obvious:
            lines.append("  - LNG (Cheniere): US LNG exporter, Qatar shutdown beneficiary")
            lines.append("  - FRO (Frontline): VLCC tanker, rates at all-time highs")
            lines.append("  - CF (CF Industries): Fertilizer, 1/3 urea blocked at Hormuz")
            lines.append("  - TLT (Treasuries): Recession hedge — rates plunge if meltdown")
            lines.append("  - XLV (Healthcare): Defensive rotation — all banks overweight")
        lines.append("")

    # Doubling plan
    strategy_name = doubling_plan.get('strategy', 'DOUBLING MACHINE')
    lines.append(f"{strategy_name}:")
    lines.append(f"  Entry: {doubling_plan.get('entry', 'TBD')}")
    lines.append(f"  T1: ${CAPITAL * 2:.0f} -> sell ${CAPITAL:.0f}, keep ${CAPITAL:.0f}")
    lines.append(f"  T2: ${CAPITAL * 3:.0f} -> sell ${CAPITAL:.0f}, keep ${CAPITAL:.0f}")
    lines.append(f"  Stop: {doubling_plan.get('stop', 'TBD')}")
    if doubling_plan.get('risk_note'):
        lines.append(f"  Risk: {doubling_plan['risk_note']}")

    return "\n".join(lines)


def format_final_alert(
    ideas: List[Dict],
    doubling_plan: Dict[str, Any],
    quotes: Dict[str, Dict],
    buckets: Dict[str, Dict],
) -> str:
    """Format the 8:25 AM CST final pre-market alert."""
    lines = [
        "FINAL PRE-MARKET ALERT 9:25 ET",
        "=" * 36,
        f"CAPITAL: ${CAPITAL:.0f} | MARKET OPENS IN 5 MIN",
        "",
        "EXACT ORDERS TO PLACE:",
        "-" * 30,
    ]

    if ideas:
        top = ideas[0]
        lines.append(f"PRIMARY: {top['vehicle']} {top['direction']}")
        lines.append(f"  Order: MARKET BUY at 9:30:00")
        lines.append(f"  Amount: ${top.get('capital', CAPITAL):.0f}")
        lines.append(f"  Take profit: +{top.get('ev_pct', 0):.0f}%")
        lines.append(f"  Stop loss: -15%")
        lines.append(f"  Confidence: {top.get('confidence', 0)}%")
        lines.append("")

        if len(ideas) > 1:
            alt = ideas[1]
            lines.append(f"BACKUP: {alt['vehicle']} {alt['direction']}")
            lines.append(f"  If primary gaps too far, switch to this")
            lines.append("")

    # Conviction pick
    conviction_picks = [i for i in ideas if i.get("conviction_pick")]
    if not conviction_picks:
        oil_plays = [i for i in ideas if "OIL" in i.get("bucket", "") or i.get("ticker", "") in ("GUSH", "UCO", "USO")]
        conviction_picks = sorted(oil_plays, key=lambda x: x.get("ev_usd", 0), reverse=True)[:1]
    if conviction_picks:
        cp = conviction_picks[0]
        lines.append("ALL-IN CONVICTION PICK:")
        lines.append(f"  {cp['vehicle']} {cp['direction']} — EV +{cp['ev_pct']:.0f}%")
        lines.append(f"  {cp.get('note', '')[:80]}")
        lines.append(f"  Confidence: {cp.get('confidence', 0)}%")
        lines.append("")

    # Additional plays to consider
    extra_tickers = ("LNG", "FRO", "CF", "KTOS", "PANW", "LMT")
    extra_plays = [i for i in ideas if i.get("ticker", "") in extra_tickers]
    if extra_plays:
        lines.append("ALSO CONSIDER:")
        for ep in extra_plays[:3]:
            lines.append(f"  {ep['vehicle']}: {ep.get('note', '')[:70]}")
        lines.append("")

    # Current prices
    lines.append("LIVE PRICES:")
    oil = quotes.get("CL=F", {})
    if oil:
        lines.append(f"  Oil: ${oil.get('price', 0):,.2f} ({oil.get('change_pct', 0):+.1f}%)")
    vix = quotes.get("^VIX", {})
    if vix:
        lines.append(f"  VIX: {vix.get('price', 0):.1f}")
    sp = quotes.get("ES=F", {})
    if sp:
        lines.append(f"  S&P Futures: ${sp.get('price', 0):,.2f} ({sp.get('change_pct', 0):+.1f}%)")
    lines.append("")

    # Doubling plan
    lines.append("EXECUTION:")
    lines.append(f"  1. Buy at 9:30, set alerts")
    lines.append(f"  2. At +100% ($250): sell ${CAPITAL:.0f}, keep rest")
    lines.append(f"  3. At +200% ($375): sell ${CAPITAL:.0f}, keep rest")
    lines.append(f"  4. Stop loss at -15% (${CAPITAL * 0.85:.0f})")
    lines.append("")
    lines.append("GOOD LUCK. STAY DISCIPLINED.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def ensure_dirs():
    """Create log/report directories if needed."""
    RESEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_log(entry: Dict[str, Any]):
    """Append a JSON line to the research log."""
    try:
        with open(RESEARCH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        print(f"  [LOG ERR] {exc}")


def save_brief(data: Dict[str, Any]):
    """Overwrite the brief JSON."""
    try:
        with open(BRIEF_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        print(f"  [BRIEF ERR] {exc}")


def save_final_plan(data: Dict[str, Any]):
    """Save the final $125 plan."""
    try:
        with open(FINAL_PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        print(f"  [PLAN ERR] {exc}")


# ---------------------------------------------------------------------------
# Scenario-aware position monitoring & exit signal engine
# ---------------------------------------------------------------------------
# Tracks actual P&L against predicted hourly scenario curves for each strategy.
# Identifies which scenario (upside/medium/downside) is playing out.
# Dynamically adjusts exit/hold recommendations based on scenario tracking,
# signal changes, and time-of-day considerations.

# NOTE: Per-strategy exit rules are in STRATEGY_EXIT_RULES below.
# These defaults are only used for display when no strategy is selected yet.
_DEFAULT_TP_PCT = 100.0
_DEFAULT_SL_PCT = -40.0
_DEFAULT_PS_PCT = -30.0

# Track alerts sent to avoid spam
_sent_alerts: Dict[str, float] = {}


def _should_alert(key: str, cooldown_sec: int = 300) -> bool:
    """Rate-limit alerts to once per cooldown period."""
    now = time.time()
    last = _sent_alerts.get(key, 0)
    if now - last < cooldown_sec:
        return False
    _sent_alerts[key] = now
    return True


# ---------------------------------------------------------------------------
# Scenario curves: expected portfolio value at each hour for each strategy
# Hours since market open: 0=9:30, 1=10:30, 2=11:30, ..., 6=3:30, 6.5=4:00
# Values are portfolio total ($) starting from $125
# ---------------------------------------------------------------------------

SCENARIO_CURVES = {
    "ALL_IN_CONVICTION": {
        "upside":   {0: 125, 1: 165, 2: 255, 3: 440, 4: 670, 5: 880, 6: 1150, 6.5: 1420},
        "medium":   {0: 125, 1: 127, 2: 148, 3: 160, 4: 140, 5: 167, 6: 147,  6.5: 160},
        "downside": {0: 125, 1: 60,  2: 30,  3: 15,  4: 7,   5: 5,   6: 3,    6.5: 3},
        "probabilities": {"upside": 0.35, "medium": 0.40, "downside": 0.25},
    },
    "ASYMMETRIC_SPLIT": {
        "upside":   {0: 125, 1: 155, 2: 220, 3: 350, 4: 530, 5: 720, 6: 900,  6.5: 1072},
        "medium":   {0: 125, 1: 135, 2: 155, 3: 175, 4: 185, 5: 195, 6: 199,  6.5: 199},
        "downside": {0: 125, 1: 95,  2: 70,  3: 50,  4: 35,  5: 30,  6: 27,   6.5: 27},
        "probabilities": {"upside": 0.35, "medium": 0.40, "downside": 0.25},
    },
    "SPREAD_MAXIMIZER": {
        "upside":   {0: 125, 1: 160, 2: 230, 3: 340, 4: 430, 5: 490, 6: 530,  6.5: 540},
        "medium":   {0: 125, 1: 140, 2: 170, 3: 210, 4: 230, 5: 240, 6: 345,  6.5: 350},
        "downside": {0: 125, 1: 85,  2: 55,  3: 35,  4: 22,  5: 18,  6: 16,   6.5: 16},
        "probabilities": {"upside": 0.35, "medium": 0.40, "downside": 0.25},
    },
    "MIXED_ARSENAL": {
        "upside":   {0: 125, 1: 155, 2: 210, 3: 310, 4: 450, 5: 580, 6: 680,  6.5: 750},
        "medium":   {0: 125, 1: 138, 2: 160, 3: 185, 4: 200, 5: 210, 6: 220,  6.5: 225},
        "downside": {0: 125, 1: 90,  2: 65,  3: 45,  4: 35,  5: 32,  6: 31,   6.5: 31},
        "probabilities": {"upside": 0.35, "medium": 0.40, "downside": 0.25},
    },
    "FRACTIONAL_ONLY": {
        "upside":   {0: 125, 1: 132, 2: 138, 3: 142, 4: 147, 5: 150, 6: 152,  6.5: 152},
        "medium":   {0: 125, 1: 127, 2: 130, 3: 132, 4: 133, 5: 134, 6: 135,  6.5: 135},
        "downside": {0: 125, 1: 118, 2: 112, 3: 108, 4: 105, 5: 104, 6: 103,  6.5: 103},
        "probabilities": {"upside": 0.35, "medium": 0.40, "downside": 0.25},
    },
}

# Dynamic exit thresholds per strategy (adjust based on strategy risk profile)
STRATEGY_EXIT_RULES = {
    "ALL_IN_CONVICTION": {
        "take_profit_pct": 80,    # Lower threshold — take profits faster (options decay)
        "stop_loss_pct": -35,
        "portfolio_stop_pct": -50,
        "max_hold_hours_options": 6,  # Sell options same day unless deep ITM
        "trailing_stop_pct": 25,  # Once up 50%+, trail with 25% stop
    },
    "ASYMMETRIC_SPLIT": {
        "take_profit_pct": 100,
        "stop_loss_pct": -40,
        "portfolio_stop_pct": -30,
        "max_hold_hours_options": 24,  # Options can hold overnight if thesis intact
        "trailing_stop_pct": 30,
    },
    "SPREAD_MAXIMIZER": {
        "take_profit_pct": 150,   # Spreads have capped upside, let them run
        "stop_loss_pct": -50,     # Spreads have defined risk, wider stop
        "portfolio_stop_pct": -40,
        "max_hold_hours_options": 48,
        "trailing_stop_pct": 35,
    },
    "MIXED_ARSENAL": {
        "take_profit_pct": 100,
        "stop_loss_pct": -35,
        "portfolio_stop_pct": -30,
        "max_hold_hours_options": 12,
        "trailing_stop_pct": 25,
    },
    "FRACTIONAL_ONLY": {
        "take_profit_pct": 200,   # Let shares run — no expiration
        "stop_loss_pct": -20,     # Tighter stop — these are shares, preserve capital
        "portfolio_stop_pct": -15,
        "max_hold_hours_options": 999,  # No options
        "trailing_stop_pct": 15,
    },
}

# Track high-water marks for trailing stops
_position_high_water: Dict[str, float] = {}  # symbol -> highest unrealized_plpc seen
_orders_filled_time: Optional[float] = None  # timestamp when orders filled


def _hours_since_open() -> float:
    """Hours elapsed since 9:30 AM ET market open."""
    now_et = datetime.now(ET)
    open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    delta = (now_et - open_time).total_seconds() / 3600
    return max(0, delta)


def _interpolate_scenario(curve: Dict[float, float], hours: float) -> float:
    """Linearly interpolate expected value from scenario curve."""
    keys = sorted(curve.keys())
    if hours <= keys[0]:
        return curve[keys[0]]
    if hours >= keys[-1]:
        return curve[keys[-1]]
    for i in range(len(keys) - 1):
        if keys[i] <= hours <= keys[i + 1]:
            frac = (hours - keys[i]) / (keys[i + 1] - keys[i])
            return curve[keys[i]] + frac * (curve[keys[i + 1]] - curve[keys[i]])
    return curve[keys[-1]]


def identify_active_scenario(
    strategy_mode: str,
    actual_portfolio_value: float,
) -> Tuple[str, float, str]:
    """Compare actual portfolio value to scenario curves.

    Returns (scenario_name, deviation_pct, recommendation).
    """
    hours = _hours_since_open()
    curves = SCENARIO_CURVES.get(strategy_mode, SCENARIO_CURVES["MIXED_ARSENAL"])

    # Get expected values for each scenario at current time
    expected = {}
    for scenario in ("upside", "medium", "downside"):
        expected[scenario] = _interpolate_scenario(curves[scenario], hours)

    # Find closest scenario
    closest = min(expected, key=lambda s: abs(expected[s] - actual_portfolio_value))
    closest_val = expected[closest]
    deviation = ((actual_portfolio_value - closest_val) / closest_val * 100) if closest_val > 0 else 0

    # Generate recommendation based on scenario + deviation
    probs = curves.get("probabilities", {})
    rules = STRATEGY_EXIT_RULES.get(strategy_mode, STRATEGY_EXIT_RULES["MIXED_ARSENAL"])

    if closest == "upside" and deviation >= 0:
        rec = "RIDING UPSIDE — hold, set trailing stop"
    elif closest == "upside" and deviation < -15:
        rec = "UPSIDE FADING — tighten stops, consider partial exit"
    elif closest == "medium" and deviation >= 10:
        rec = "OUTPERFORMING MEDIUM — hold with trailing stop"
    elif closest == "medium" and deviation < -10:
        rec = "UNDERPERFORMING — watch for downside transition"
    elif closest == "downside" and hours < 2:
        rec = f"DOWNSIDE TRACKING — cut losses if below ${CAPITAL * 0.6:.0f}"
    elif closest == "downside" and hours >= 2:
        rec = "DEEP DOWNSIDE — EXIT NOW, preserve remaining capital"
    else:
        rec = "ON TRACK — hold position"

    # Time-based adjustments for options-heavy strategies
    if strategy_mode in ("ALL_IN_CONVICTION", "MIXED_ARSENAL", "ASYMMETRIC_SPLIT"):
        max_hours = rules["max_hold_hours_options"]
        if hours >= max_hours and closest != "upside":
            rec = f"OPTIONS TIME LIMIT ({max_hours}h) — sell options, theta decay accelerating"
        elif hours >= 5 and closest == "medium":
            rec = "LATE SESSION MEDIUM — sell options before close, keep shares"

    return closest, deviation, rec


def monitor_live_positions(strategy_mode: str = "MIXED_ARSENAL") -> Dict[str, Any]:
    """Scenario-aware position monitoring with dynamic exit signals.

    Compares actual P&L against predicted scenario curves for the active
    strategy. Generates time-aware, strategy-specific recommendations.
    """
    positions = check_live_positions()
    account = check_live_account()

    if not positions and not account:
        return {"status": "no_data", "positions": [], "alerts": []}

    equity = float(account.get("equity", 0))
    cash = float(account.get("cash", 0))
    portfolio_value = float(account.get("portfolio_value", 0))
    hours = _hours_since_open()

    rules = STRATEGY_EXIT_RULES.get(strategy_mode, STRATEGY_EXIT_RULES["MIXED_ARSENAL"])

    alerts = []
    pos_details = []
    total_cost = 0
    total_market_value = 0
    total_unrealized_pl = 0

    for p in positions:
        symbol = p.get("symbol", "?")
        qty = float(p.get("qty", 0))
        side = p.get("side", "long")
        market_value = float(p.get("market_value", 0))
        cost_basis = float(p.get("cost_basis", 0))
        unrealized_pl = float(p.get("unrealized_pl", 0))
        unrealized_plpc = float(p.get("unrealized_plpc", 0)) * 100
        current_price = float(p.get("current_price", 0))
        avg_entry = float(p.get("avg_entry_price", 0))
        change_today = float(p.get("change_today", 0)) * 100
        asset_class = p.get("asset_class", "us_equity")
        is_option = asset_class == "us_option"

        total_cost += cost_basis
        total_market_value += abs(market_value)
        total_unrealized_pl += unrealized_pl

        # Track high-water mark for trailing stops
        prev_high = _position_high_water.get(symbol, 0)
        if unrealized_plpc > prev_high:
            _position_high_water[symbol] = unrealized_plpc
        high_water = _position_high_water.get(symbol, 0)

        detail = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "avg_entry": avg_entry,
            "current_price": current_price,
            "market_value": market_value,
            "cost_basis": cost_basis,
            "unrealized_pl": unrealized_pl,
            "unrealized_plpc": unrealized_plpc,
            "change_today": change_today,
            "is_option": is_option,
            "high_water_pct": high_water,
        }
        pos_details.append(detail)

        # === DYNAMIC EXIT SIGNALS (strategy-aware) ===

        tp_thresh = rules["take_profit_pct"]
        sl_thresh = rules["stop_loss_pct"]
        trail_thresh = rules["trailing_stop_pct"]

        # 1. Take profit (strategy-specific threshold)
        if unrealized_plpc >= tp_thresh:
            alert_key = f"tp_{symbol}"
            if _should_alert(alert_key, cooldown_sec=600):
                action = "SELL NOW" if is_option else "Consider selling or setting tight trailing stop"
                alerts.append({
                    "type": "TAKE_PROFIT",
                    "symbol": symbol,
                    "msg": (
                        f"TAKE PROFIT: {symbol} +{unrealized_plpc:.1f}% (${unrealized_pl:+.2f})\n"
                        f"  Strategy threshold: +{tp_thresh:.0f}% | High water: +{high_water:.1f}%\n"
                        f"  ACTION: {action}"
                    ),
                })

        # 2. Trailing stop (activated once past 50% of take-profit threshold)
        elif high_water >= tp_thresh * 0.5 and (high_water - unrealized_plpc) >= trail_thresh:
            alert_key = f"trail_{symbol}"
            if _should_alert(alert_key, cooldown_sec=600):
                alerts.append({
                    "type": "TRAILING_STOP",
                    "symbol": symbol,
                    "msg": (
                        f"TRAILING STOP: {symbol}\n"
                        f"  Current: {unrealized_plpc:+.1f}% | Peak: +{high_water:.1f}% | Drop: {high_water - unrealized_plpc:.1f}%\n"
                        f"  Trailing stop: {trail_thresh:.0f}% from peak\n"
                        f"  ACTION: SELL — gains are reversing"
                    ),
                })

        # 3. Stop loss (strategy-specific)
        elif unrealized_plpc <= sl_thresh:
            alert_key = f"sl_{symbol}"
            if _should_alert(alert_key, cooldown_sec=600):
                alerts.append({
                    "type": "STOP_LOSS",
                    "symbol": symbol,
                    "msg": (
                        f"STOP LOSS: {symbol} {unrealized_plpc:+.1f}% (${unrealized_pl:+.2f})\n"
                        f"  Strategy stop: {sl_thresh:.0f}% | Entry: ${avg_entry:.2f} -> ${current_price:.2f}\n"
                        f"  ACTION: CUT LOSSES NOW"
                    ),
                })

        # 4. Options time decay warning
        elif is_option and hours >= rules["max_hold_hours_options"] * 0.75:
            alert_key = f"theta_{symbol}"
            if _should_alert(alert_key, cooldown_sec=1800):
                hrs_left = rules["max_hold_hours_options"] - hours
                alerts.append({
                    "type": "THETA_WARNING",
                    "symbol": symbol,
                    "msg": (
                        f"THETA WARNING: {symbol} (option)\n"
                        f"  {hours:.1f}h held | Max hold: {rules['max_hold_hours_options']}h | ~{max(0, hrs_left):.1f}h remaining\n"
                        f"  P&L: {unrealized_plpc:+.1f}% | Time decay accelerating\n"
                        f"  ACTION: {'SELL before close' if hrs_left < 1 else 'Plan exit within ' + f'{hrs_left:.0f}h'}"
                    ),
                })

        # 5. Warning zone
        elif unrealized_plpc <= sl_thresh * 0.5:  # Half way to stop loss
            alert_key = f"warn_{symbol}"
            if _should_alert(alert_key, cooldown_sec=900):
                alerts.append({
                    "type": "WARNING",
                    "symbol": symbol,
                    "msg": (
                        f"WARNING: {symbol} {unrealized_plpc:+.1f}%\n"
                        f"  Stop loss at {sl_thresh:.0f}% | Monitoring"
                    ),
                })

        # 6. Strong gain milestone
        elif unrealized_plpc >= tp_thresh * 0.5:
            alert_key = f"win50_{symbol}"
            if _should_alert(alert_key, cooldown_sec=1800):
                alerts.append({
                    "type": "STRONG_GAIN",
                    "symbol": symbol,
                    "msg": (
                        f"STRONG GAIN: {symbol} +{unrealized_plpc:.1f}% (${unrealized_pl:+.2f})\n"
                        f"  Trailing stop active at {trail_thresh:.0f}% from peak\n"
                        f"  High water: +{high_water:.1f}%"
                    ),
                })

    # === PORTFOLIO-LEVEL CHECKS ===
    if total_cost > 0:
        portfolio_pct = (total_unrealized_pl / total_cost) * 100
        pf_stop = rules["portfolio_stop_pct"]

        if portfolio_pct <= pf_stop:
            alert_key = "portfolio_stop"
            if _should_alert(alert_key, cooldown_sec=600):
                alerts.append({
                    "type": "PORTFOLIO_STOP",
                    "symbol": "ALL",
                    "msg": (
                        f"PORTFOLIO STOP: Down {portfolio_pct:.1f}% (limit: {pf_stop:.0f}%)\n"
                        f"  Total P&L: ${total_unrealized_pl:+.2f}\n"
                        f"  Equity: ${equity:.2f}\n"
                        f"  ACTION: EXIT ALL POSITIONS"
                    ),
                })

    # === SCENARIO TRACKING ===
    actual_value = equity  # Total account value including cash + positions
    scenario, deviation, recommendation = identify_active_scenario(strategy_mode, actual_value)

    # Get expected values for display
    curves = SCENARIO_CURVES.get(strategy_mode, SCENARIO_CURVES["MIXED_ARSENAL"])
    expected_up = _interpolate_scenario(curves["upside"], hours)
    expected_mid = _interpolate_scenario(curves["medium"], hours)
    expected_down = _interpolate_scenario(curves["downside"], hours)

    # Alert on scenario transitions
    if scenario == "downside" and hours >= 1:
        alert_key = "scenario_downside"
        if _should_alert(alert_key, cooldown_sec=900):
            alerts.append({
                "type": "SCENARIO_ALERT",
                "symbol": "PORTFOLIO",
                "msg": (
                    f"SCENARIO: DOWNSIDE TRACKING\n"
                    f"  Actual: ${actual_value:.2f} | Expected downside: ${expected_down:.0f}\n"
                    f"  Deviation: {deviation:+.1f}% from downside curve\n"
                    f"  {recommendation}"
                ),
            })

    result = {
        "status": "active" if positions else "no_positions",
        "equity": equity,
        "cash": cash,
        "portfolio_value": portfolio_value,
        "total_cost": total_cost,
        "total_market_value": total_market_value,
        "total_unrealized_pl": total_unrealized_pl,
        "total_unrealized_plpc": (total_unrealized_pl / total_cost * 100) if total_cost > 0 else 0,
        "positions": pos_details,
        "alerts": alerts,
        "position_count": len(positions),
        # Scenario tracking data
        "scenario": {
            "active": scenario,
            "deviation_pct": deviation,
            "recommendation": recommendation,
            "hours_since_open": round(hours, 2),
            "expected_upside": round(expected_up, 2),
            "expected_medium": round(expected_mid, 2),
            "expected_downside": round(expected_down, 2),
            "actual_value": round(actual_value, 2),
        },
        "strategy_mode": strategy_mode,
    }

    return result


def format_position_update(
    mon: Dict[str, Any],
    buckets: Dict[str, Dict] = None,
) -> str:
    """Format a scenario-aware position update for Telegram."""
    lines = []
    now_et = datetime.now(ET).strftime("%H:%M ET")
    strategy_mode = mon.get("strategy_mode", "?")

    total_pl = mon.get("total_unrealized_pl", 0)
    total_pct = mon.get("total_unrealized_plpc", 0)
    prefix = "+" if total_pl >= 0 else ""

    lines.append(f"POSITION MONITOR [{now_et}]")
    lines.append(f"Strategy: {strategy_mode}")
    lines.append("=" * 36)
    lines.append(f"Portfolio: ${mon.get('equity', 0):.2f} | P&L: {prefix}${total_pl:.2f} ({prefix}{total_pct:.1f}%)")
    lines.append("")

    # Per-position details
    for p in mon.get("positions", []):
        sym = p["symbol"]
        pl = p["unrealized_pl"]
        pct = p["unrealized_plpc"]
        hw = p.get("high_water_pct", 0)
        opt_tag = " [OPT]" if p.get("is_option") else ""
        pf = "+" if pl >= 0 else ""
        hw_str = f" (peak +{hw:.0f}%)" if hw > 10 else ""
        lines.append(f"  {sym}{opt_tag}: ${p['current_price']:.2f} | {pf}${pl:.2f} ({pf}{pct:.1f}%){hw_str}")

    # Scenario tracking
    sc = mon.get("scenario", {})
    if sc:
        lines.append("")
        active = sc.get("active", "?").upper()
        hours = sc.get("hours_since_open", 0)
        actual = sc.get("actual_value", 0)
        exp_up = sc.get("expected_upside", 0)
        exp_mid = sc.get("expected_medium", 0)
        exp_down = sc.get("expected_downside", 0)
        dev = sc.get("deviation_pct", 0)

        lines.append(f"SCENARIO: {active} ({dev:+.1f}% deviation)")
        lines.append(f"  Hour {hours:.1f} | Actual: ${actual:.0f}")
        lines.append(f"  Expected: Up ${exp_up:.0f} | Mid ${exp_mid:.0f} | Down ${exp_down:.0f}")
        rec = sc.get("recommendation", "")
        if rec:
            lines.append(f"  >> {rec}")

    # Signal strength
    if buckets:
        oil = buckets.get("OIL_SUPPLY", {}).get("score", 0)
        geo = buckets.get("GEOPOLITICAL", {}).get("score", 0)
        avi = buckets.get("AVIATION", {}).get("score", 0)
        shp = buckets.get("SHIPPING", {}).get("score", 0)
        lines.append("")
        lines.append(f"Signals: Oil {oil}/10 | Geo {geo}/10 | Avi {avi}/10 | Ship {shp}/10")

        # Deescalation check
        geo_signals = buckets.get("GEOPOLITICAL", {}).get("signals", [])
        deesc_kw = ["ceasefire", "de-escalation", "peace", "negotiate", "diplomatic", "truce"]
        deesc = [s for s in geo_signals if any(k in s.lower() for k in deesc_kw)]
        if deesc:
            lines.append(f"DEESCALATION: {'; '.join(deesc[:3])}")

    # Alerts
    alerts = mon.get("alerts", [])
    if alerts:
        lines.append("")
        for a in alerts:
            lines.append(a["msg"])

    return "\n".join(lines)


def run_position_monitor_cycle(cycle_num: int) -> Dict[str, Any]:
    """Scenario-aware monitoring cycle during market hours.

    Checks positions against scenario curves, fetches fresh signals,
    generates time-aware exit/hold recommendations per strategy.
    """
    cycle_start = datetime.now(timezone.utc)
    now_et = cycle_start.astimezone(ET)
    now_ct = cycle_start.astimezone(CT)
    hours = _hours_since_open()

    print(f"\n{'=' * 60}")
    print(f"[MONITOR {cycle_num}] {now_et.strftime('%H:%M ET')} / {now_ct.strftime('%H:%M CT')} (hour {hours:.1f})")
    print(f"{'=' * 60}")

    # Load active strategy from the saved plan
    brief_data = json.loads(BRIEF_PATH.read_text()) if BRIEF_PATH.exists() else {}
    active_mode = brief_data.get("doubling_plan", {}).get("strategy_mode", "MIXED_ARSENAL")
    print(f"  Active strategy: {active_mode}")

    # Record when orders first filled (for time-based exits)
    global _orders_filled_time
    if _orders_filled_time is None:
        _orders_filled_time = time.time()

    # 1. Check positions (scenario-aware)
    print(f"  [1/5] Position check (scenario-aware)...")
    mon = monitor_live_positions(strategy_mode=active_mode)
    n_pos = mon.get("position_count", 0)
    total_pl = mon.get("total_unrealized_pl", 0)
    total_pct = mon.get("total_unrealized_plpc", 0)
    sc = mon.get("scenario", {})
    print(f"  Positions: {n_pos} | P&L: ${total_pl:+.2f} ({total_pct:+.1f}%)")
    print(f"  Scenario: {sc.get('active', '?').upper()} | Actual: ${sc.get('actual_value', 0):.0f} "
          f"(up=${sc.get('expected_upside', 0):.0f} mid=${sc.get('expected_medium', 0):.0f} "
          f"down=${sc.get('expected_downside', 0):.0f})")
    print(f"  >> {sc.get('recommendation', 'N/A')}")

    # 2. Quick signal refresh (SerpAPI + Yahoo)
    print(f"  [2/5] Signal refresh...")
    buckets = None
    quotes = {}
    try:
        serp_news = fetch_serp_news()
        quotes = fetch_yahoo_quotes()
        buckets = analyze_intelligence(serp_news, [], [], [])
    except Exception as exc:
        print(f"  [ERR] Signal refresh failed: {exc}")

    # 3. Deescalation scan
    print(f"  [3/5] Deescalation scan...")
    if buckets:
        geo_signals = buckets.get("GEOPOLITICAL", {}).get("signals", [])
        deesc_kw = ["ceasefire", "de-escalation", "peace", "negotiate", "diplomatic", "truce", "deal"]
        deesc = [s for s in geo_signals if any(k in s.lower() for k in deesc_kw)]
        if deesc:
            print(f"  DEESCALATION DETECTED: {deesc[:3]}")
            if _should_alert("deescalation", cooldown_sec=600):
                # Deescalation + options = urgent exit signal
                has_options = any(p.get("is_option") for p in mon.get("positions", []))
                urgency = "URGENT" if has_options else "Monitor"
                send_to_all_channels(
                    f"DEESCALATION ALERT [{now_et.strftime('%H:%M ET')}]\n"
                    f"Signals: {'; '.join(deesc[:5])}\n"
                    f"Priority: {urgency}\n"
                    f"{'OPTIONS AT RISK — sell immediately to preserve value' if has_options else 'Watch for thesis reversal'}"
                )

    # 4. Strategy re-score
    print(f"  [4/5] Strategy re-score...")
    current_best = active_mode
    if buckets and quotes:
        fear_greed = compute_fear_greed_proxy(quotes)
        try:
            crypto = fetch_alpaca_crypto()
        except Exception:
            crypto = {}
        sentiment = {"label": "N/A", "top_topics": []}
        ideas = generate_trade_ideas(buckets, quotes, crypto, sentiment, fear_greed)
        scores = compute_strategy_scores(buckets, quotes, ideas, fear_greed)
        current_best = max(scores, key=scores.get) if scores else active_mode
        print(f"  Optimal now: {current_best} (score: {scores.get(current_best, 0):.0f})")
        if current_best != active_mode:
            print(f"  DRIFT: Active={active_mode} vs Optimal={current_best}")
            if _should_alert("strategy_drift", cooldown_sec=1800):
                send_to_all_channels(
                    f"STRATEGY DRIFT [{now_et.strftime('%H:%M ET')}]\n"
                    f"Active: {active_mode} | Optimal now: {current_best}\n"
                    f"Signals may have shifted — review positions"
                )

    # 5. End-of-day checks
    print(f"  [5/5] Time-of-day checks...")
    rules = STRATEGY_EXIT_RULES.get(active_mode, STRATEGY_EXIT_RULES["MIXED_ARSENAL"])

    # 30 min before close: warn about option decay
    if hours >= 6.0:  # 3:30 PM ET
        has_options = any(p.get("is_option") for p in mon.get("positions", []))
        if has_options and _should_alert("eod_options", cooldown_sec=900):
            opt_positions = [p for p in mon.get("positions", []) if p.get("is_option")]
            opt_pl = sum(p["unrealized_pl"] for p in opt_positions)
            send_to_all_channels(
                f"END OF DAY WARNING [{now_et.strftime('%H:%M ET')}]\n"
                f"Options P&L: ${opt_pl:+.2f}\n"
                f"30 min to close — sell options unless deep ITM with strong thesis\n"
                f"Holding options overnight = theta + gap risk"
            )

    # After-hours transition (4:00 PM+)
    if hours >= 6.5 and _should_alert("after_hours", cooldown_sec=3600):
        send_to_all_channels(
            f"AFTER HOURS [{now_et.strftime('%H:%M ET')}]\n"
            f"Portfolio: ${mon.get('equity', 0):.2f} | Day P&L: ${total_pl:+.2f} ({total_pct:+.1f}%)\n"
            f"Scenario: {sc.get('active', '?').upper()}\n"
            f"Monitoring overnight for gaps and news"
        )

    # 6. Format and send
    msg = format_position_update(mon, buckets)
    alerts = mon.get("alerts", [])

    if alerts:
        print(f"  Sending {len(alerts)} alert(s)...")
        for a in alerts:
            send_to_all_channels(f"{'=' * 30}\n{a['msg']}\n{'=' * 30}")
        send_to_all_channels(msg)
    else:
        # Telegram: every 30 min for regular updates, every 5 min during first hour
        minute = now_ct.minute
        if hours < 1:
            # First hour after open: Telegram every 15 min
            if minute % 15 < 5:
                print(f"  Sending first-hour update...")
                send_to_all_channels(msg)
            else:
                print(f"  First-hour check OK — logged")
        elif minute < 5 or (minute >= 30 and minute < 35):
            print(f"  Sending 30-min position update...")
            send_to_all_channels(msg)
        else:
            print(f"  Position check OK — next Telegram at :{0 if minute >= 30 else 30:02d}")

    # Log
    append_log({
        "event": "position_monitor",
        "cycle": cycle_num,
        "timestamp_utc": cycle_start.isoformat(),
        "strategy_mode": active_mode,
        "scenario": sc,
        "monitor": {
            "position_count": n_pos,
            "total_pl": total_pl,
            "total_pct": total_pct,
            "equity": mon.get("equity", 0),
        },
        "alerts": [a["type"] + ":" + a["symbol"] for a in alerts],
    })

    duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    print(f"  Monitor cycle done in {duration:.1f}s")
    return mon


# ---------------------------------------------------------------------------
# Main research cycle
# ---------------------------------------------------------------------------

def run_cycle(cycle_num: int, is_morning_brief: bool = False,
              is_final_alert: bool = False) -> Dict[str, Any]:
    """Run one complete intelligence gathering and analysis cycle."""
    cycle_start = datetime.now(timezone.utc)
    now_et = cycle_start.astimezone(ET)
    now_ct = cycle_start.astimezone(CT)

    phase = "FINAL ALERT" if is_final_alert else ("MORNING BRIEF" if is_morning_brief else "RESEARCH")
    print(f"\n{'=' * 60}")
    print(f"[CYCLE {cycle_num}] {phase} — {now_et.strftime('%H:%M ET')} / {now_ct.strftime('%H:%M CT')}")
    print(f"{'=' * 60}")

    # 1. Fetch all data sources
    print("\n[1/7] SerpAPI Google News...")
    serp_news = []
    try:
        serp_news = fetch_serp_news()
    except Exception as exc:
        print(f"  [ERR] SerpAPI failed: {exc}")

    print("\n[2/7] Reddit...")
    reddit_posts = []
    try:
        reddit_posts = fetch_reddit()
    except Exception as exc:
        print(f"  [ERR] Reddit failed: {exc}")

    print("\n[3/7] Finnhub News...")
    finnhub_news = []
    try:
        finnhub_news = fetch_finnhub_news()
    except Exception as exc:
        print(f"  [ERR] Finnhub failed: {exc}")

    print("\n[4/7] GDELT...")
    gdelt_articles = []
    try:
        gdelt_articles = fetch_gdelt()
    except Exception as exc:
        print(f"  [ERR] GDELT failed: {exc}")

    print("\n[5/7] Yahoo Finance quotes...")
    quotes = {}
    try:
        quotes = fetch_yahoo_quotes()
    except Exception as exc:
        print(f"  [ERR] Yahoo failed: {exc}")

    print("\n[6/7] Alpaca Crypto...")
    crypto = {}
    try:
        crypto = fetch_alpaca_crypto()
    except Exception as exc:
        print(f"  [ERR] Alpaca crypto failed: {exc}")

    print("\n[7/7] Analysis...")

    # 2. Analyze intelligence
    buckets = analyze_intelligence(serp_news, reddit_posts, finnhub_news, gdelt_articles)
    sentiment = analyze_reddit_sentiment(reddit_posts)
    fear_greed = compute_fear_greed_proxy(quotes)

    # 3. Generate trade ideas + dynamic strategy selection
    ideas = generate_trade_ideas(buckets, quotes, crypto, sentiment, fear_greed)
    doubling_plan = build_doubling_plan(ideas, buckets, quotes, fear_greed)

    total_articles = len(serp_news) + len(reddit_posts) + len(finnhub_news) + len(gdelt_articles)
    print(f"\n  Total items analyzed: {total_articles}")
    print(f"  Trade ideas generated: {len(ideas)}")

    # 4. Build cycle result
    cycle_result = {
        "cycle": cycle_num,
        "timestamp_utc": cycle_start.isoformat(),
        "phase": phase,
        "data_counts": {
            "serp": len(serp_news),
            "reddit": len(reddit_posts),
            "finnhub": len(finnhub_news),
            "gdelt": len(gdelt_articles),
            "total": total_articles,
        },
        "buckets": {k: {"score": v["score"], "count": v["count"]} for k, v in buckets.items()},
        "sentiment": sentiment,
        "fear_greed": fear_greed,
        "quotes": quotes,
        "crypto": crypto,
        "ideas": ideas,
        "doubling_plan": doubling_plan,
    }

    # 5. Save to log and brief
    append_log(cycle_result)
    save_brief(cycle_result)

    # 6. Detect breaking news (high-score new signals)
    breaking = ""
    hot_buckets = [k for k, v in buckets.items() if v["score"] >= 8]
    if hot_buckets:
        breaking = f"HIGH ALERT on: {', '.join(hot_buckets)}"

    # 7. Format and send Telegram
    if is_final_alert:
        msg = format_final_alert(ideas, doubling_plan, quotes, buckets)
        save_final_plan(cycle_result)
        print(f"\n  Saved final plan to {FINAL_PLAN_PATH}")
    elif is_morning_brief:
        all_signals = {k: v.get("signals", []) for k, v in buckets.items()}
        msg = format_morning_brief(
            buckets, quotes, crypto, ideas, sentiment,
            doubling_plan, fear_greed, all_signals,
        )
    else:
        msg = format_intel_update(
            cycle_start, buckets, quotes, crypto, ideas,
            sentiment, doubling_plan, total_articles, breaking,
        )

    # Only send Telegram at/after 7:00 AM CST — before that, just log to file
    now_ct_tg = datetime.now(CT)
    if now_ct_tg.hour < 7 and not is_morning_brief and not is_final_alert:
        print(f"\n  [PRE-7AM] Telegram suppressed ({len(msg)} chars) — logging only")
    else:
        print(f"\n  Sending to Telegram ({len(msg)} chars)...")
        send_to_all_channels(msg)

    cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    print(f"\n  Cycle completed in {cycle_duration:.1f}s")

    return cycle_result


def check_kill_switch() -> bool:
    """Return True if the kill switch is active."""
    return read_control_state_snapshot(REPO_ROOT).get("kill_switch", False)


# ---------------------------------------------------------------------------
# After-hours / overnight monitoring
# ---------------------------------------------------------------------------

# Track last-known oil price for delta alerts
_last_oil_price: Optional[float] = None


def run_afterhours_cycle(cycle_num: int) -> Dict[str, Any]:
    """Run a lightweight after-hours monitoring cycle.

    - Position check (if any positions held)
    - Light signal refresh (SerpAPI + Yahoo only — no Reddit/GDELT/Finnhub)
    - Oil price alert if >3% move since last check
    - Deescalation headline scan
    - Returns results dict
    """
    global _last_oil_price

    cycle_start = datetime.now(timezone.utc)
    now_et = cycle_start.astimezone(ET)
    now_ct = cycle_start.astimezone(CT)

    print(f"\n{'=' * 60}")
    print(f"[AFTERHOURS {cycle_num}] {now_et.strftime('%H:%M ET')} / {now_ct.strftime('%H:%M CT')}")
    print(f"{'=' * 60}")

    result: Dict[str, Any] = {
        "cycle": cycle_num,
        "phase": "AFTERHOURS",
        "timestamp_utc": cycle_start.isoformat(),
        "alerts": [],
    }

    # 1. Position check
    positions_data: Dict[str, Any] = {}
    try:
        positions_data = monitor_live_positions()
        n_pos = positions_data.get("position_count", 0)
        total_pl = positions_data.get("total_unrealized_pl", 0)
        total_pct = positions_data.get("total_unrealized_plpc", 0)
        print(f"  Positions: {n_pos} | P&L: ${total_pl:+.2f} ({total_pct:+.1f}%)")
        result["positions"] = {
            "count": n_pos,
            "total_pl": total_pl,
            "total_pct": total_pct,
            "equity": positions_data.get("equity", 0),
        }

        # Alert on significant after-hours moves (>5%)
        for p in positions_data.get("positions", []):
            pct = abs(p.get("unrealized_plpc", 0))
            if pct >= 5:
                alert_msg = (
                    f"AFTER-HOURS ALERT: {p['symbol']} "
                    f"${p.get('unrealized_pl', 0):+.2f} ({p.get('unrealized_plpc', 0):+.1f}%)"
                )
                result["alerts"].append(alert_msg)
    except Exception as exc:
        print(f"  [ERR] Position check failed: {exc}")

    # 2. Light signal refresh — SerpAPI only
    serp_news = []
    try:
        print("\n  [1/2] SerpAPI Google News (light)...")
        serp_news = fetch_serp_news()
        result["serp_count"] = len(serp_news)
    except Exception as exc:
        print(f"  [ERR] SerpAPI failed: {exc}")

    # 3. Yahoo quotes
    quotes: Dict[str, Any] = {}
    try:
        print("  [2/2] Yahoo Finance quotes...")
        quotes = fetch_yahoo_quotes()
        result["quotes"] = quotes
    except Exception as exc:
        print(f"  [ERR] Yahoo failed: {exc}")

    # 4. Oil price delta alert (>3% move since last check)
    oil_price = quotes.get("CL=F", {}).get("price", 0)
    if oil_price and _last_oil_price:
        oil_delta_pct = ((oil_price - _last_oil_price) / _last_oil_price) * 100
        if abs(oil_delta_pct) >= 3:
            oil_alert = (
                f"OIL PRICE ALERT: WTI ${oil_price:.2f} "
                f"({oil_delta_pct:+.1f}% since last check, was ${_last_oil_price:.2f})"
            )
            result["alerts"].append(oil_alert)
            print(f"  {oil_alert}")
    if oil_price:
        _last_oil_price = oil_price

    # 5. Deescalation / escalation headline scan
    escalation_keywords = [
        "hormuz", "strait", "blockade", "escalat", "nuclear", "strike",
        "ceasefire", "de-escalat", "deescalat", "peace", "negotiat", "diplomacy",
    ]
    hot_headlines = []
    for article in serp_news:
        title = (article.get("title") or "").lower()
        snippet = (article.get("snippet") or "").lower()
        text = title + " " + snippet
        for kw in escalation_keywords:
            if kw in text:
                hot_headlines.append(article.get("title", "")[:120])
                break

    if hot_headlines:
        result["hot_headlines"] = hot_headlines
        headline_alert = f"BREAKING HEADLINES ({len(hot_headlines)}):\n" + "\n".join(
            f"  - {h}" for h in hot_headlines[:5]
        )
        result["alerts"].append(headline_alert)
        print(f"  Found {len(hot_headlines)} escalation/deescalation headlines")

    # 6. Send alerts via Telegram (only if there are significant alerts)
    if result["alerts"]:
        alert_msg = f"AFTER-HOURS INTEL ({now_et.strftime('%H:%M ET')})\n" + "\n".join(
            f"{'=' * 30}\n{a}" for a in result["alerts"]
        )
        send_to_all_channels(alert_msg)
        print(f"  Sent {len(result['alerts'])} alert(s) to Telegram")
    else:
        print(f"  No significant alerts — quiet cycle")

    # 7. Log
    append_log({
        "event": "afterhours_cycle",
        "cycle": cycle_num,
        "timestamp_utc": cycle_start.isoformat(),
        "alert_count": len(result["alerts"]),
        "oil_price": oil_price,
        "position_count": result.get("positions", {}).get("count", 0),
    })

    duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    print(f"  After-hours cycle done in {duration:.1f}s")
    return result


def overnight_summary() -> str:
    """Build and send an overnight summary at 6:00 AM ET.

    Includes: positions held overnight, overnight oil/gold/natgas moves,
    key headlines found, and next day's preliminary strategy assessment.
    """
    now_et = datetime.now(ET)
    lines = [
        f"OVERNIGHT SUMMARY — {now_et.strftime('%A %B %d, %Y %H:%M ET')}",
        "=" * 40,
        "",
    ]

    # 1. Positions held overnight
    try:
        mon = monitor_live_positions()
        n_pos = mon.get("position_count", 0)
        total_pl = mon.get("total_unrealized_pl", 0)
        total_pct = mon.get("total_unrealized_plpc", 0)
        lines.append(f"POSITIONS HELD OVERNIGHT: {n_pos}")
        lines.append(f"  Portfolio P&L: ${total_pl:+.2f} ({total_pct:+.1f}%)")
        lines.append(f"  Equity: ${mon.get('equity', 0):.2f}")
        for p in mon.get("positions", []):
            pl = p.get("unrealized_pl", 0)
            pct = p.get("unrealized_plpc", 0)
            lines.append(f"  {p['symbol']}: ${p.get('current_price', 0):.2f} | ${pl:+.2f} ({pct:+.1f}%)")
    except Exception as exc:
        lines.append(f"  [ERR] Position check failed: {exc}")

    lines.append("")

    # 2. Overnight commodity moves (oil, gold, natgas)
    try:
        quotes = fetch_yahoo_quotes()
        lines.append("OVERNIGHT COMMODITY MOVES:")
        for sym, name in [("CL=F", "WTI Crude"), ("BZ=F", "Brent Crude"),
                          ("GC=F", "Gold"), ("NG=F", "Natural Gas")]:
            q = quotes.get(sym, {})
            if q:
                lines.append(f"  {name}: ${q['price']:,.2f} ({q['change_pct']:+.2f}%)")

        # Futures for pre-market context
        for sym, name in [("ES=F", "S&P Futures"), ("NQ=F", "Nasdaq Futures")]:
            q = quotes.get(sym, {})
            if q:
                lines.append(f"  {name}: {q['price']:,.2f} ({q['change_pct']:+.2f}%)")
    except Exception as exc:
        lines.append(f"  [ERR] Quote fetch failed: {exc}")

    lines.append("")

    # 3. Key headlines (SerpAPI)
    try:
        serp_news = fetch_serp_news()
        escalation_kw = [
            "iran", "hormuz", "oil", "crude", "strike", "escalat",
            "ceasefire", "nuclear", "sanction", "opec", "energy",
        ]
        key_headlines = []
        for article in serp_news:
            title = (article.get("title") or "").lower()
            for kw in escalation_kw:
                if kw in title:
                    key_headlines.append(article.get("title", "")[:120])
                    break

        lines.append(f"KEY OVERNIGHT HEADLINES ({len(key_headlines)}):")
        if key_headlines:
            for h in key_headlines[:8]:
                lines.append(f"  - {h}")
        else:
            lines.append("  (No major geopolitical headlines detected)")
    except Exception as exc:
        lines.append(f"  [ERR] SerpAPI failed: {exc}")

    lines.append("")

    # 4. Preliminary strategy assessment for next day
    lines.append("NEXT DAY STRATEGY ASSESSMENT:")
    try:
        oil_change = quotes.get("CL=F", {}).get("change_pct", 0)
        vix = quotes.get("^VIX", {}).get("price", 0)
        if abs(oil_change) >= 5:
            lines.append(f"  OIL: Major overnight move ({oil_change:+.2f}%) — high volatility expected")
        elif abs(oil_change) >= 2:
            lines.append(f"  OIL: Notable move ({oil_change:+.2f}%) — watch for continuation")
        else:
            lines.append(f"  OIL: Stable overnight ({oil_change:+.2f}%)")

        if vix >= 30:
            lines.append(f"  VIX: Elevated ({vix:.1f}) — risk-off environment")
        elif vix >= 20:
            lines.append(f"  VIX: Moderately elevated ({vix:.1f})")
        else:
            lines.append(f"  VIX: Normal ({vix:.1f})")

        if key_headlines:
            lines.append(f"  HEADLINES: {len(key_headlines)} relevant — pre-market research critical")
        lines.append(f"  Transitioning to Phase 1 (pre-market research)...")
    except Exception:
        lines.append("  (Assessment unavailable)")

    summary_msg = "\n".join(lines)
    send_to_all_channels(summary_msg)
    print(summary_msg)

    append_log({
        "event": "overnight_summary",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    })

    return summary_msg


# ---------------------------------------------------------------------------
# Main loop with schedule awareness
# ---------------------------------------------------------------------------

def main():
    """Main entry point — runs continuously across trading days.

    Schedule (all times CT / ET):
      PRE-MARKET RESEARCH PHASE:
        Before 7:00 AM CT: research every 30 min
        7:00 AM CT: morning brief
        7:00-8:25 AM CT: research every 15 min
        8:25 AM CT: final alert (locks strategy)

      EXECUTION:
        8:31 AM CT (9:31 AM ET): submit live orders

      MARKET HOURS MONITORING:
        8:35 AM - 3:00 PM CT (9:35 AM - 4:00 PM ET):
          Position check every 5 min (logged)
          Telegram update every 30 min
          Signal refresh every 5 min (SerpAPI + Yahoo)
          Deescalation scan continuous
          Take-profit alert at +100%
          Stop-loss alert at -40%
          Portfolio stop at -30%
          Strategy re-score every 30 min

      AFTER-HOURS (3:05 PM - 5:00 PM CT / 4:05 PM - 6:00 PM ET):
        Position check every 15 min
        Alert on >5% position swings or breaking geopolitical news

      OVERNIGHT (5:00 PM CT - 5:00 AM CT / 6:00 PM - 6:00 AM ET):
        Light research cycle every 60 min (SerpAPI + Yahoo only)
        Alert on oil >3% move or Hormuz/escalation headlines

      MORNING TRANSITION (5:00 AM CT / 6:00 AM ET):
        Send overnight summary
        Reset daily flags
        Transition back to Phase 1 (pre-market research)

      Runs indefinitely until manually stopped or kill switch activated.
    """
    global CAPITAL
    ensure_dirs()

    print("=" * 60)
    print("IRAN WAR INTELLIGENCE ENGINE v3")
    print("  Pre-market -> Execution -> Market -> After-hours -> Overnight")
    print("  Runs 24/7 across trading days until kill switch or manual stop")
    print("=" * 60)
    print(f"Capital: ${CAPITAL:.0f}")
    print(f"SerpAPI: {'SET' if SERP_API_KEY else 'NOT SET'}")
    print(f"Finnhub: {'SET' if FINNHUB_API_KEY else 'NOT SET'}")
    print(f"Alpaca:  {'SET' if ALPACA_API_KEY else 'NOT SET'}")
    print(f"Live:    {'SET' if ALPACA_API_KEY_LIVE else 'NOT SET'} (orders at 9:31 ET, 24/7 monitoring)")
    print(f"Telegram: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    print(f"V6 Thread: {'SET' if TELEGRAM_V6_THREAD_ID else 'NOT SET'}")
    print(f"Kill switch: {KILL_SWITCH_PATH}")
    print(f"Log: {RESEARCH_LOG}")
    print(f"Brief: {BRIEF_PATH}")
    print(f"Final: {FINAL_PLAN_PATH}")
    print()

    cycle_num = 0
    morning_brief_sent = False
    final_alert_sent = False
    live_orders_sent = False
    market_monitoring_active = False
    afterhours_active = False
    overnight_active = False
    overnight_summary_sent = False
    monitor_cycle_num = 0
    afterhours_cycle_num = 0
    eod_summary_sent = False

    # Send an immediate first cycle
    cycle_num += 1
    print("[STARTUP] Running initial intelligence cycle...")
    try:
        run_cycle(cycle_num)
    except Exception as exc:
        print(f"[ERR] Initial cycle failed: {exc}")
        traceback.print_exc()

    while True:
        now_ct = datetime.now(CT)
        now_et = datetime.now(ET)
        hour_ct = now_ct.hour
        minute_ct = now_ct.minute
        hour_et = now_et.hour

        # ==================================================================
        # KILL SWITCH CHECK (every cycle)
        # ==================================================================
        if check_kill_switch():
            print(f"\n[KILL SWITCH] Kill switch activated! Sending final summary and exiting...")
            try:
                mon = monitor_live_positions()
                n_pos = mon.get("position_count", 0)
                total_pl = mon.get("total_unrealized_pl", 0)
                total_pct = mon.get("total_unrealized_plpc", 0)
                kill_msg = (
                    f"KILL SWITCH ACTIVATED\n"
                    f"{'=' * 32}\n"
                    f"Positions: {n_pos}\n"
                    f"Portfolio P&L: ${total_pl:+.2f} ({total_pct:+.1f}%)\n"
                    f"Equity: ${mon.get('equity', 0):.2f}\n"
                )
                for p in mon.get("positions", []):
                    pl = p.get("unrealized_pl", 0)
                    pct = p.get("unrealized_plpc", 0)
                    kill_msg += f"\n  {p['symbol']}: ${p.get('current_price', 0):.2f} | ${pl:+.2f} ({pct:+.1f}%)"
                kill_msg += f"\n\nEngine stopped at {now_et.strftime('%H:%M ET')} by kill switch."
                send_to_all_channels(kill_msg)
                print(kill_msg)
            except Exception as exc:
                print(f"[ERR] Kill switch summary failed: {exc}")
                send_to_all_channels(f"KILL SWITCH ACTIVATED — engine stopping. (Summary failed: {exc})")

            append_log({"event": "kill_switch_exit", "timestamp_utc": datetime.now(timezone.utc).isoformat()})
            print(f"\nEngine stopped by kill switch. Total cycles: {cycle_num}, Monitor: {monitor_cycle_num}, After-hours: {afterhours_cycle_num}")
            break

        # ==================================================================
        # PHASE 4: OVERNIGHT SUMMARY + NEW DAY RESET (5:00 AM CT / 6:00 AM ET)
        # ==================================================================
        if overnight_active and not overnight_summary_sent and hour_ct >= 5:
            print(f"\n[OVERNIGHT SUMMARY] 5:00 AM CT / 6:00 AM ET — sending overnight summary")
            try:
                overnight_summary()
                overnight_summary_sent = True
            except Exception as exc:
                print(f"[ERR] Overnight summary failed: {exc}")
                traceback.print_exc()

            # Reset daily flags for the new trading day
            print(f"\n[NEW DAY] Resetting daily flags for {now_ct.strftime('%A %B %d')}")
            morning_brief_sent = False
            final_alert_sent = False
            live_orders_sent = False
            market_monitoring_active = False
            afterhours_active = False
            overnight_active = False
            overnight_summary_sent = False
            eod_summary_sent = False
            monitor_cycle_num = 0
            afterhours_cycle_num = 0

            append_log({
                "event": "new_day_reset",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "day": now_ct.strftime("%Y-%m-%d"),
            })
            # Fall through to Phase 1 (pre-market research)
            continue

        # ==================================================================
        # PHASE 3B: OVERNIGHT MONITORING (5:00 PM CT - 5:00 AM CT / 6 PM - 6 AM ET)
        # ==================================================================
        if (afterhours_active or overnight_active) and (hour_ct >= 17 or hour_ct < 5):
            if not overnight_active:
                overnight_active = True
                print(f"\n[OVERNIGHT] Transitioning to overnight monitoring mode")
                print(f"  Light research every 60 min (SerpAPI + Yahoo only)")
                print(f"  Oil >3% alert | Escalation headline scan")
                print(f"  Until 5:00 AM CT (6:00 AM ET) overnight summary")
                send_to_all_channels(
                    f"OVERNIGHT MONITORING ACTIVE\n"
                    f"Light cycle every 60 min (SerpAPI + Yahoo)\n"
                    f"Oil >3% and headline alerts\n"
                    f"Overnight summary at 6:00 AM ET"
                )

            afterhours_cycle_num += 1
            try:
                run_afterhours_cycle(afterhours_cycle_num)
            except Exception as exc:
                print(f"[ERR] Overnight cycle {afterhours_cycle_num} failed: {exc}")
                traceback.print_exc()

            # 60-minute sleep for overnight (with kill switch checks every 30s)
            print(f"\n[SLEEP] Overnight — next cycle in 60 min")
            sleep_until = time.time() + 60 * 60
            while time.time() < sleep_until:
                if check_kill_switch():
                    break
                # Check if we've crossed into morning summary time
                if datetime.now(CT).hour >= 5:
                    break
                time.sleep(30)
            continue

        # ==================================================================
        # PHASE 3A: AFTER-HOURS MONITORING (3:05 PM - 5:00 PM CT / 4:05 PM - 6:00 PM ET)
        # ==================================================================
        if (hour_ct == 15 and minute_ct >= 5) or hour_ct == 16:
            if not afterhours_active:
                afterhours_active = True
                print(f"\n[AFTER-HOURS] Market closed — transitioning to after-hours monitoring")
                print(f"  Position checks every 15 min")
                print(f"  Alert on >5% swings or breaking geopolitical news")
                print(f"  Until 5:00 PM CT (6:00 PM ET)")

                # Send EOD summary once at transition
                if not eod_summary_sent:
                    eod_summary_sent = True
                    try:
                        mon = monitor_live_positions()
                        total_pl = mon.get("total_unrealized_pl", 0)
                        total_pct = mon.get("total_unrealized_plpc", 0)
                        n_pos = mon.get("position_count", 0)

                        eod_lines = [
                            "END OF DAY SUMMARY",
                            "=" * 32,
                            f"Positions: {n_pos}",
                            f"Portfolio P&L: ${total_pl:+.2f} ({total_pct:+.1f}%)",
                            f"Equity: ${mon.get('equity', 0):.2f}",
                            "",
                        ]
                        for p in mon.get("positions", []):
                            pl = p["unrealized_pl"]
                            pct = p["unrealized_plpc"]
                            eod_lines.append(f"  {p['symbol']}: ${p['current_price']:.2f} | ${pl:+.2f} ({pct:+.1f}%)")

                        brief_data = json.loads(BRIEF_PATH.read_text()) if BRIEF_PATH.exists() else {}
                        active_mode = brief_data.get("doubling_plan", {}).get("strategy_mode", "?")
                        eod_lines.append(f"\nStrategy used: {active_mode}")
                        eod_lines.append(f"Monitor cycles completed: {monitor_cycle_num}")
                        eod_lines.append(f"\nTransitioning to after-hours monitoring...")

                        eod_msg = "\n".join(eod_lines)
                        send_to_all_channels(eod_msg)
                        print(eod_msg)
                    except Exception as exc:
                        print(f"[ERR] EOD summary failed: {exc}")

            afterhours_cycle_num += 1
            try:
                run_afterhours_cycle(afterhours_cycle_num)
            except Exception as exc:
                print(f"[ERR] After-hours cycle {afterhours_cycle_num} failed: {exc}")
                traceback.print_exc()

            # 15-minute sleep for after-hours (with kill switch checks every 30s)
            print(f"\n[SLEEP] After-hours — next cycle in 15 min")
            sleep_until = time.time() + 15 * 60
            while time.time() < sleep_until:
                if check_kill_switch():
                    break
                # Check if we've crossed into overnight time
                if datetime.now(CT).hour >= 17:
                    break
                time.sleep(30)
            continue

        # ==================================================================
        # PHASE 2: MARKET HOURS MONITORING (8:35 AM CT+ / after orders sent)
        # ==================================================================
        if live_orders_sent and hour_ct >= 8 and minute_ct >= 35:
            if not market_monitoring_active:
                market_monitoring_active = True
                print(f"\n[MARKET MONITOR] Entering market hours monitoring mode")
                print(f"  Position checks: every 5 min")
                print(f"  Telegram updates: every 30 min + on alerts")
                print(f"  Take profit: +{_DEFAULT_TP_PCT:.0f}% | Stop loss: {_DEFAULT_SL_PCT:.0f}% | Portfolio stop: {_DEFAULT_PS_PCT:.0f}%")
                print(f"  Runs until: 3:05 PM CT (4:05 PM ET)")
                send_to_all_channels(
                    f"MARKET MONITOR ACTIVE\n"
                    f"Position checks every 5 min\n"
                    f"Take profit: +{_DEFAULT_TP_PCT:.0f}% | Stop loss: {_DEFAULT_SL_PCT:.0f}%\n"
                    f"Monitoring until 4:00 PM ET"
                )

            monitor_cycle_num += 1
            try:
                run_position_monitor_cycle(monitor_cycle_num)
            except Exception as exc:
                print(f"[ERR] Monitor cycle {monitor_cycle_num} failed: {exc}")
                traceback.print_exc()

            # 5-minute sleep between position checks
            time.sleep(5 * 60)
            continue

        # ==================================================================
        # PHASE 1: PRE-MARKET RESEARCH (before 8:35 AM CT)
        # ==================================================================

        # Live order execution (8:31 AM CT = 9:31 AM ET)
        if final_alert_sent and not live_orders_sent and (hour_ct == 8 and minute_ct >= 31):
            print(f"\n[LIVE ORDERS] 8:31 AM CT — submitting $125 diversified plan to LIVE account")
            try:
                brief_data = json.loads(BRIEF_PATH.read_text()) if BRIEF_PATH.exists() else {}
                latest_plan = brief_data.get("doubling_plan", {})
                if latest_plan.get("positions"):
                    acct = check_live_account()
                    # Dynamic capital: use actual equity instead of hardcoded $125
                    actual_equity = float(acct.get("equity", 0))
                    if actual_equity > 50:
                        old_cap = CAPITAL
                        CAPITAL = round(actual_equity * 0.95, 2)  # Use 95% of equity (keep 5% buffer)
                        if abs(CAPITAL - old_cap) > 5:
                            print(f"  [LIVE] Capital updated: ${old_cap:.0f} -> ${CAPITAL:.0f} (equity=${actual_equity:.0f})")
                    bp = float(acct.get("buying_power", 0))
                    opt_bp = float(acct.get("options_buying_power", 0))
                    print(f"  [LIVE] Account equity=${acct.get('equity','?')} buying_power=${bp:.0f} options_bp=${opt_bp:.0f}")

                    # --- PRE-EXECUTION OPTIONS CHECK ---
                    # If options buying power is $0, rebuild plan as fractional-only
                    has_options = any(
                        p.get("order_type") in ("option", "spread")
                        for p in latest_plan.get("positions", [])
                    )
                    if has_options and opt_bp < 50:
                        original_mode = latest_plan.get("strategy_mode", "?")
                        print(f"  [LIVE] OPTIONS BUYING POWER TOO LOW (${opt_bp:.0f})")
                        print(f"  [LIVE] Plan has options but account can't trade them")
                        print(f"  [LIVE] Rebuilding plan as FRACTIONAL_ONLY (was {original_mode})")
                        send_to_all_channels(
                            f"OPTIONS BP = ${opt_bp:.0f} — CANNOT TRADE OPTIONS\n"
                            f"Original strategy: {original_mode}\n"
                            f"Rebuilding as FRACTIONAL_ONLY to ensure execution"
                        )
                        # Reload ideas from the brief and rebuild as fractional
                        ideas_for_rebuild = brief_data.get("ideas", [])
                        # Filter to share-only ideas
                        share_ideas = [i for i in ideas_for_rebuild if i.get("order_type") != "option"]
                        if share_ideas:
                            # Force fractional strategy
                            fallback_plan = build_doubling_plan(share_ideas)
                            fallback_plan["strategy_mode"] = "FRACTIONAL_ONLY"
                            fallback_plan["strategy"] = "Fractional Only (Auto-fallback: options_bp=$0)"
                            fallback_plan["note"] = f"Auto-downgraded from {original_mode} — options_buying_power=${opt_bp:.0f}"
                            latest_plan = fallback_plan
                            print(f"  [LIVE] Rebuilt plan: {len(fallback_plan.get('positions', []))} fractional positions")
                        else:
                            print(f"  [LIVE] No share ideas available — trying original plan anyway")
                    elif has_options:
                        print(f"  [LIVE] Options buying power OK (${opt_bp:.0f})")

                    # Guard: check for existing positions to prevent doubling on restart
                    try:
                        existing = monitor_live_positions()
                        n_existing = existing.get("position_count", 0)
                        if n_existing > 0:
                            print(f"  [LIVE] ALREADY HAVE {n_existing} POSITIONS — skipping order submission")
                            print(f"  [LIVE] Existing P&L: ${existing.get('total_unrealized_pl', 0):+.2f}")
                            send_to_all_channels(
                                f"ORDER GUARD: {n_existing} positions already open\n"
                                f"P&L: ${existing.get('total_unrealized_pl', 0):+.2f}\n"
                                f"Skipping duplicate order submission"
                            )
                            live_orders_sent = True
                            time.sleep(60)
                            continue
                    except Exception as guard_exc:
                        print(f"  [LIVE] Position guard check failed: {guard_exc} — proceeding with caution")

                    if bp >= 100:
                        order_results = submit_live_orders(latest_plan)
                        live_orders_sent = True
                        append_log({"event": "live_orders_submitted", "results": order_results,
                                    "options_bp": opt_bp, "strategy": latest_plan.get("strategy_mode")})
                    else:
                        print(f"  [LIVE] Insufficient buying power (${bp:.0f}) — skipping")
                        live_orders_sent = True
                else:
                    print("  [LIVE] No positions in plan — skipping")
                    live_orders_sent = True
            except Exception as exc:
                print(f"[ERR] Live order submission failed: {exc}")
                traceback.print_exc()
                # DON'T set live_orders_sent=True on error — allow retry next loop
                send_to_all_channels(f"ORDER SUBMISSION FAILED\n{exc}\nWill retry in 60s")
            time.sleep(60)
            continue

        # Final alert (8:25 AM CT)
        if not final_alert_sent and (hour_ct == 8 and minute_ct >= 25):
            cycle_num += 1
            print(f"\n[FINAL ALERT] 8:25 AM CT — sending final pre-market alert")

            # Pre-check options buying power for early warning
            try:
                pre_acct = check_live_account()
                pre_opt_bp = float(pre_acct.get("options_buying_power", 0))
                pre_bp = float(pre_acct.get("buying_power", 0))
                print(f"  [PRE-CHECK] buying_power=${pre_bp:.0f} options_bp=${pre_opt_bp:.0f}")
                if pre_opt_bp < 50:
                    send_to_all_channels(
                        f"OPTIONS BP WARNING\n"
                        f"Options buying power: ${pre_opt_bp:.0f}\n"
                        f"Cash buying power: ${pre_bp:.0f}\n"
                        f"If options BP stays $0 at 9:31 ET, strategy will auto-downgrade to FRACTIONAL_ONLY"
                    )
            except Exception:
                pass  # Non-critical check

            try:
                run_cycle(cycle_num, is_final_alert=True)
                final_alert_sent = True
            except Exception as exc:
                print(f"[ERR] Final alert cycle failed: {exc}")
                traceback.print_exc()
            time.sleep(60)
            continue

        # Morning brief (7:00 AM CT)
        if not morning_brief_sent and hour_ct >= 7:
            cycle_num += 1
            print(f"\n[MORNING BRIEF] 7:00 AM CT — sending comprehensive brief")
            try:
                run_cycle(cycle_num, is_morning_brief=True)
                morning_brief_sent = True
            except Exception as exc:
                print(f"[ERR] Morning brief cycle failed: {exc}")
                traceback.print_exc()

        # Determine sleep interval for pre-market
        if hour_ct >= 7:
            interval = 15 * 60
            phase_label = "15-min pre-market"
        else:
            interval = 30 * 60
            phase_label = "30-min overnight research"

        next_cycle = now_ct + timedelta(seconds=interval)
        print(f"\n[SLEEP] {phase_label} cycle — next at {next_cycle.strftime('%H:%M CT')}")

        # Sleep with interruptibility
        sleep_until = time.time() + interval
        while time.time() < sleep_until:
            now_ct_check = datetime.now(CT)
            h, m = now_ct_check.hour, now_ct_check.minute

            if check_kill_switch():
                break
            if not morning_brief_sent and h >= 7:
                break
            if not final_alert_sent and h == 8 and m >= 25:
                break
            if final_alert_sent and not live_orders_sent and h == 8 and m >= 31:
                break

            time.sleep(30)

        # Run next pre-market cycle
        cycle_num += 1
        now_ct = datetime.now(CT)
        is_mb = not morning_brief_sent and now_ct.hour >= 7
        is_fa = not final_alert_sent and now_ct.hour == 8 and now_ct.minute >= 25

        # Don't run research cycles if we should be in monitoring mode
        if live_orders_sent:
            continue

        try:
            result = run_cycle(
                cycle_num,
                is_morning_brief=is_mb,
                is_final_alert=is_fa,
            )
            if is_mb:
                morning_brief_sent = True
            if is_fa:
                final_alert_sent = True
        except Exception as exc:
            print(f"[ERR] Cycle {cycle_num} failed: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()

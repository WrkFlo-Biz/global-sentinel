#!/usr/bin/env python3
"""Congress Trading Bridge — fetches Senate/House disclosures from FMP API.

Flags trades in tracked symbols and sends Telegram alerts for significant buys.
"""
import json, os, datetime, urllib.request, urllib.error
from pathlib import Path
import sys

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"

# Load .env
env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

FMP_API_KEY = env.get("FMP_API_KEY", "")

# Tracked symbols — the core 40
SYMBOLS = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AMD", "AVGO", "CRM", "NFLX", "ORCL", "ADBE",
    "JPM", "GS", "MS", "V", "MA",
    "XOM", "CVX", "LMT", "RTX", "BA", "CAT",
    "UNH", "PFE", "JNJ", "LLY", "ABBV",
    "XLE", "XLF", "XLK", "XLV", "XLI",
    "GLD", "TLT",
]
SYMBOLS_SET = set(SYMBOLS)

OUTPUT_FILE = QF / "congress_trades.json"
HISTORY_FILE = QF / "congress_history.jsonl"

# Amount range parsing
AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, 100000000),
}


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="congress")
            return
        except Exception:
            pass
    try:
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        payload = json.dumps({
            "chat_id": "7091381625",
            "text": msg[:4000],
            "parse_mode": "HTML", "message_thread_id": 74,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")


def fetch_json(url):
    """Fetch JSON from URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/1.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code} for {url}: {e.reason}")
        return []
    except Exception as e:
        print(f"Fetch error for {url}: {e}")
        return []


def parse_amount_range(amount_str):
    """Parse FMP amount range string to (min, max) tuple."""
    if not amount_str:
        return (0, 0)
    # Try known ranges
    for pattern, values in AMOUNT_RANGES.items():
        if pattern.lower() in amount_str.lower():
            return values
    # Try to extract numbers
    import re
    nums = re.findall(r'[\d,]+', amount_str.replace(',', ''))
    if len(nums) >= 2:
        return (int(nums[0]), int(nums[1]))
    elif len(nums) == 1:
        return (int(nums[0]), int(nums[0]))
    return (0, 0)


def score_trade(trade, today):
    """Score a trade by recency, amount, and relevance. Higher = more notable."""
    score = 0.0

    # Recency (last 7 days = highest)
    trade_date = trade.get("date", "")
    if trade_date:
        try:
            td = datetime.date.fromisoformat(trade_date[:10])
            days_ago = (today - td).days
            if days_ago <= 1:
                score += 50
            elif days_ago <= 3:
                score += 40
            elif days_ago <= 7:
                score += 30
            elif days_ago <= 14:
                score += 15
            else:
                score += 5
        except Exception:
            score += 5
    else:
        score += 5

    # Amount
    min_amt = trade.get("amount_min", 0)
    if min_amt >= 1000001:
        score += 40
    elif min_amt >= 250001:
        score += 30
    elif min_amt >= 100001:
        score += 25
    elif min_amt >= 50001:
        score += 20
    elif min_amt >= 15001:
        score += 10
    else:
        score += 5

    # Buy vs sell (buys more interesting)
    tx_type = trade.get("transaction_type", "").lower()
    if "purchase" in tx_type or "buy" in tx_type:
        score += 15
    elif "sale" in tx_type or "sell" in tx_type:
        score += 5

    # Tracked symbol bonus
    if trade.get("in_tracked_symbols"):
        score += 20

    return score


def _parse_fmp_trade(item, source):
    """Parse a single FMP trade record (works for both senate-latest and house-latest)."""
    symbol = (item.get("symbol") or item.get("ticker") or "").strip().upper()
    if " - " in symbol:
        symbol = symbol.split(" - ")[0].strip()
    if " " in symbol:
        symbol = symbol.split()[0].strip()

    amount_str = item.get("amount") or ""
    amt_min, amt_max = parse_amount_range(str(amount_str))

    member = (item.get("firstName", "") + " " + item.get("lastName", "")).strip()
    if not member or member == " ":
        member = item.get("office", "") or item.get("representative", "")

    return {
        "source": source,
        "member": member,
        "party": item.get("party", ""),
        "state": item.get("district", "") or item.get("state", ""),
        "symbol": symbol,
        "transaction_type": item.get("type", ""),
        "asset_type": item.get("assetType", ""),
        "asset_description": item.get("assetDescription", ""),
        "amount_range": str(amount_str),
        "amount_min": amt_min,
        "amount_max": amt_max,
        "date": item.get("transactionDate") or item.get("disclosureDate") or "",
        "disclosure_date": item.get("disclosureDate") or "",
        "comment": item.get("comment") or item.get("description") or "",
        "owner": item.get("owner", ""),
        "link": item.get("link", ""),
        "in_tracked_symbols": symbol in SYMBOLS_SET,
    }


def fetch_senate_trades():
    """Fetch Senate trading disclosures from FMP stable API."""
    url = f"https://financialmodelingprep.com/stable/senate-latest?apikey={FMP_API_KEY}"
    raw = fetch_json(url)
    if not isinstance(raw, list):
        print(f"Senate API returned non-list: {type(raw)}")
        return []
    return [_parse_fmp_trade(item, "senate") for item in raw]


def fetch_house_trades():
    """Fetch House trading disclosures from FMP stable API."""
    url = f"https://financialmodelingprep.com/stable/house-latest?apikey={FMP_API_KEY}"
    raw = fetch_json(url)
    if not isinstance(raw, list):
        print(f"House API returned non-list: {type(raw)}")
        return []
    return [_parse_fmp_trade(item, "house") for item in raw]


def run():
    print(f"[{iso_now()}] Congress Trading Bridge running...")

    if not FMP_API_KEY:
        print("ERROR: FMP_API_KEY not found in .env")
        return

    today = datetime.date.today()
    seven_days_ago = today - datetime.timedelta(days=7)

    # Fetch from both chambers
    senate = fetch_senate_trades()
    house = fetch_house_trades()
    all_trades = senate + house

    print(f"  Fetched {len(senate)} Senate + {len(house)} House = {len(all_trades)} total trades")

    if not all_trades:
        print("  No trades returned, saving empty result.")
        save_json(OUTPUT_FILE, {
            "timestamp": iso_now(),
            "total_trades": 0,
            "tracked_trades": [],
            "notable_trades": [],
            "alerts_sent": 0,
        })
        return

    # Score all trades
    for trade in all_trades:
        trade["score"] = score_trade(trade, today)

    # Sort by score descending
    all_trades.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Filter to tracked symbols
    tracked = [t for t in all_trades if t.get("in_tracked_symbols")]
    recent_tracked = [t for t in tracked if _is_recent(t, seven_days_ago)]

    # Notable: tracked + high score
    notable = [t for t in all_trades if t.get("score", 0) >= 50][:20]

    print(f"  Tracked symbol trades: {len(tracked)} ({len(recent_tracked)} in last 7d)")
    print(f"  Notable trades (score >= 50): {len(notable)}")

    # Build alert for significant buys of tracked symbols in last 7 days
    alerts = []
    for t in recent_tracked:
        tx = t.get("transaction_type", "").lower()
        is_buy = "purchase" in tx or "buy" in tx
        if is_buy and t.get("amount_min", 0) >= 50001:
            alerts.append(t)

    # Send Telegram alerts
    alerts_sent = 0
    if alerts:
        msg = "<b>Congress Trading Alert</b>\n\n"
        msg += f"{len(alerts)} significant buy(s) in tracked symbols:\n\n"
        for a in alerts[:10]:
            msg += f"<b>{a['symbol']}</b> — {a['member'].strip()}\n"
            msg += f"  {a['source'].upper()} | {a['transaction_type']} | {a['amount_range']}\n"
            msg += f"  Date: {a['date']} | {a.get('party', '')} ({a.get('state', '')})\n"
            if a.get("comment"):
                msg += f"  Note: {a['comment'][:80]}\n"
            msg += "\n"
        send_telegram(msg)
        alerts_sent = len(alerts)
        print(f"  Sent Telegram alert for {alerts_sent} significant buys")

    # Save output
    output = {
        "timestamp": iso_now(),
        "total_trades": len(all_trades),
        "senate_count": len(senate),
        "house_count": len(house),
        "tracked_trades": [_clean_trade(t) for t in tracked[:50]],
        "notable_trades": [_clean_trade(t) for t in notable],
        "recent_buys_tracked": [_clean_trade(t) for t in alerts],
        "alerts_sent": alerts_sent,
        "top_symbols": _count_symbols(all_trades),
    }
    save_json(OUTPUT_FILE, output)

    # Append to history
    history_entry = {
        "timestamp": iso_now(),
        "total_fetched": len(all_trades),
        "tracked_count": len(tracked),
        "alerts_sent": alerts_sent,
        "alert_symbols": [a["symbol"] for a in alerts],
        "top_traded_symbols": _count_symbols(all_trades)[:10],
    }
    append_jsonl(HISTORY_FILE, history_entry)

    print(f"  Output saved to {OUTPUT_FILE}")
    print("  Done.")


def _is_recent(trade, cutoff_date):
    """Check if trade date is on or after cutoff."""
    d = trade.get("date", "")
    if not d:
        return False
    try:
        td = datetime.date.fromisoformat(d[:10])
        return td >= cutoff_date
    except Exception:
        return False


def _clean_trade(t):
    """Return a clean version of trade dict for JSON output."""
    return {
        "source": t.get("source", ""),
        "member": t.get("member", "").strip(),
        "party": t.get("party", ""),
        "state": t.get("state", ""),
        "symbol": t.get("symbol", ""),
        "transaction_type": t.get("transaction_type", ""),
        "amount_range": t.get("amount_range", ""),
        "amount_min": t.get("amount_min", 0),
        "date": t.get("date", ""),
        "score": t.get("score", 0),
        "in_tracked": t.get("in_tracked_symbols", False),
    }


def _count_symbols(trades):
    """Count trades per symbol, return sorted list."""
    counts = {}
    for t in trades:
        sym = t.get("symbol", "")
        if sym:
            counts[sym] = counts.get(sym, 0) + 1
    return sorted([{"symbol": s, "count": c} for s, c in counts.items()],
                  key=lambda x: x["count"], reverse=True)


if __name__ == "__main__":
    run()

#!/usr/bin/env python3
"""
Correlation-Based Position Limits Guard
Called before opening any new paper trade position.
Checks correlation with existing positions, sector exposure, and position count limits.
"""
import json, os, sys, time, datetime, urllib.request, urllib.error, traceback
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())

# Paper account keys (day trade)
DT_KEY = env.get("ALPACA_API_KEY", "")
DT_SECRET = env.get("ALPACA_SECRET_KEY", "")
DT_BASE = "https://paper-api.alpaca.markets"

# Live account for data
LIVE_KEY = env.get("ALPACA_API_KEY_LIVE", env.get("ALPACA_API_KEY", ""))
LIVE_SECRET = env.get("ALPACA_SECRET_KEY_LIVE", env.get("ALPACA_SECRET_KEY", ""))

DATA_DIR = REPO_ROOT / "data" / "quantum_feed"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CORR_CACHE_PATH = Path("/tmp/gs_corr_cache.json")
CORR_CACHE_TTL = 4 * 3600  # 4 hours
CORRELATION_THRESHOLD = 0.80
MAX_POSITIONS = 8
SECTOR_LIMIT_PCT = 0.40

# ============ SECTOR MAPPING ============
SECTOR_MAP = {
    # Technology
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "GOOG": "tech",
    "META": "tech", "NVDA": "tech", "AMD": "tech", "AVGO": "tech",
    "SMCI": "tech", "ARM": "tech", "MU": "tech", "PLTR": "tech",
    "COIN": "tech", "MSTR": "tech", "SOFI": "tech",
    # ETF - Tech
    "QQQ": "tech_etf", "SOXL": "tech_etf", "XLK": "tech_etf",
    # ETF - Broad Market
    "SPY": "broad_market", "IWM": "broad_market",
    # Energy
    "XLE": "energy", "USO": "energy",
    # Financials
    "XLF": "finance",
    # Consumer / Travel
    "CCL": "travel", "DAL": "travel", "UAL": "travel",
    "NKE": "consumer",
    # Aerospace / Defense
    "BA": "aerospace",
    # Automotive / EV
    "TSLA": "auto_ev", "RIVN": "auto_ev",
    # Precious Metals
    "GLD": "precious_metals",
    # Volatility
    "UVXY": "volatility",
    # Crypto-adjacent
    "AMZN": "tech",
}


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[{ts}] [CORR-GUARD] {msg}", flush=True)


def log_decision(decision):
    """Append decision to JSONL log."""
    log_path = LOG_DIR / "position_guard.jsonl"
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(decision, default=str) + "\n")
    except Exception as e:
        log(f"Log write error: {e}")


def alpaca_request(base, key, secret, method, path):
    url = f"{base}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("APCA-API-KEY-ID", key)
    req.add_header("APCA-API-SECRET-KEY", secret)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode() if e.fp else str(e)
        log(f"API error {e.code}: {err[:200]}")
        return None
    except Exception as e:
        log(f"Request error: {e}")
        return None


def data_request(path):
    url = f"https://data.alpaca.markets{path}"
    req = urllib.request.Request(url)
    req.add_header("APCA-API-KEY-ID", LIVE_KEY)
    req.add_header("APCA-API-SECRET-KEY", LIVE_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"Data error: {e}")
        return None


def get_open_positions(account="day_trade"):
    """Get current open positions from paper account."""
    if account == "day_trade":
        positions = alpaca_request(DT_BASE, DT_KEY, DT_SECRET, "GET", "/v2/positions")
    else:
        ml_key = env.get("ALPACA_API_KEY_MEDLONG", DT_KEY)
        ml_secret = env.get("ALPACA_SECRET_KEY_MEDLONG", DT_SECRET)
        positions = alpaca_request(DT_BASE, ml_key, ml_secret, "GET", "/v2/positions")
    if not positions:
        return []
    # Extract underlying symbols (for options, parse underlying)
    result = []
    for p in positions:
        sym = p.get("symbol", "")
        # Options symbols are like AAPL260328C00200000 - extract underlying
        underlying = extract_underlying(sym)
        result.append({
            "symbol": sym,
            "underlying": underlying,
            "market_value": float(p.get("market_value", 0)),
            "qty": float(p.get("qty", 0)),
            "side": p.get("side", "long"),
        })
    return result


def extract_underlying(symbol):
    """Extract underlying ticker from options symbol or return as-is for stocks."""
    if len(symbol) > 10 and any(c.isdigit() for c in symbol[3:]):
        # Likely an options symbol: AAPL260328C00200000
        # Find where digits start
        for i, c in enumerate(symbol):
            if c.isdigit() and i >= 1:
                return symbol[:i]
        return symbol[:4]
    return symbol


def get_correlation_matrix(symbols):
    """Get 30-day correlation matrix for given symbols. Uses cache."""
    # Check cache
    if CORR_CACHE_PATH.exists():
        try:
            cache = json.loads(CORR_CACHE_PATH.read_text())
            cache_time = cache.get("timestamp", 0)
            if time.time() - cache_time < CORR_CACHE_TTL:
                cached_symbols = set(cache.get("symbols", []))
                if set(symbols).issubset(cached_symbols):
                    log("Using cached correlation matrix")
                    return cache.get("matrix", {})
        except Exception:
            pass

    # Fetch 30-day daily bars
    log(f"Computing correlation matrix for {symbols}...")
    end = datetime.date.today()
    start = end - datetime.timedelta(days=45)  # extra days for holidays
    sym_str = ",".join(symbols)
    data = data_request(
        f"/v2/stocks/bars?symbols={sym_str}&timeframe=1Day"
        f"&start={start}&end={end}&limit=50&sort=asc"
    )
    if not data or "bars" not in data:
        log("Failed to fetch bars for correlation")
        return {}

    # Compute returns
    returns = {}
    for sym in symbols:
        bars = data.get("bars", {}).get(sym, [])
        closes = [b["c"] for b in bars]
        if len(closes) > 1:
            rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            returns[sym] = rets

    if len(returns) < 2:
        log("Insufficient data for correlation")
        return {}

    # Align lengths
    min_len = min(len(v) for v in returns.values())
    if min_len < 5:
        log(f"Only {min_len} days of data, need at least 5")
        return {}

    # Build correlation dict
    import numpy as np
    syms_with_data = [s for s in symbols if s in returns]
    mat = np.array([returns[s][:min_len] for s in syms_with_data])
    corr_np = np.corrcoef(mat)

    matrix = {}
    for i, s1 in enumerate(syms_with_data):
        matrix[s1] = {}
        for j, s2 in enumerate(syms_with_data):
            matrix[s1][s2] = round(float(corr_np[i][j]), 4)

    # Save cache
    cache_data = {
        "timestamp": time.time(),
        "symbols": syms_with_data,
        "matrix": matrix,
        "computed": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        CORR_CACHE_PATH.write_text(json.dumps(cache_data, indent=2))
    except Exception as e:
        log(f"Cache write error: {e}")

    return matrix


def get_sector(symbol):
    """Get sector for a symbol."""
    return SECTOR_MAP.get(symbol, "unknown")


def check_position(proposed_symbol, account="day_trade"):
    """
    Check if a proposed new position is allowed.
    Returns: {"allowed": bool, "reason": str, "warnings": [...],
              "correlation_with_existing": {...}, "sector_exposure": {...}}
    """
    result = {
        "allowed": True,
        "reason": "OK",
        "warnings": [],
        "correlation_with_existing": {},
        "sector_exposure": {},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "proposed_symbol": proposed_symbol,
    }

    # Get underlying for proposed symbol
    proposed_underlying = extract_underlying(proposed_symbol)

    # Get current positions
    positions = get_open_positions(account)
    existing_underlyings = list(set(p["underlying"] for p in positions))
    position_count = len(positions)

    log(f"Checking {proposed_underlying}: {position_count} existing positions: {existing_underlyings}")

    # Rule 1: Hard position limit
    if position_count >= MAX_POSITIONS:
        result["allowed"] = False
        result["reason"] = f"BLOCKED: Max {MAX_POSITIONS} positions reached ({position_count} open)"
        log(result["reason"])
        log_decision(result)
        return result

    # Rule 2: Correlation check
    if existing_underlyings:
        all_symbols = list(set(existing_underlyings + [proposed_underlying]))
        corr_matrix = get_correlation_matrix(all_symbols)

        if corr_matrix and proposed_underlying in corr_matrix:
            high_corr_positions = []
            for existing_sym in existing_underlyings:
                if existing_sym in corr_matrix.get(proposed_underlying, {}):
                    corr_val = corr_matrix[proposed_underlying][existing_sym]
                    result["correlation_with_existing"][existing_sym] = corr_val
                    if corr_val > CORRELATION_THRESHOLD:
                        high_corr_positions.append((existing_sym, corr_val))

            # Check cluster rule: >0.80 correlation AND 2+ positions in same cluster
            if high_corr_positions:
                # Find all symbols that are highly correlated with each other
                cluster_symbols = [proposed_underlying] + [s for s, _ in high_corr_positions]
                # Count how many existing positions are in this cluster
                cluster_count = len([s for s in existing_underlyings
                                    if s in [x[0] for x in high_corr_positions]])

                if cluster_count >= 2:
                    corr_details = ", ".join(
                        [f"{s}={c:.2f}" for s, c in high_corr_positions]
                    )
                    result["allowed"] = False
                    result["reason"] = (
                        f"BLOCKED: {proposed_underlying} has correlation >{CORRELATION_THRESHOLD} "
                        f"with {cluster_count} existing positions ({corr_details}). "
                        f"Cluster already has 2+ positions."
                    )
                    log(result["reason"])
                    log_decision(result)
                    return result
                elif cluster_count >= 1:
                    result["warnings"].append(
                        f"High correlation with {high_corr_positions[0][0]} "
                        f"({high_corr_positions[0][1]:.2f}). One more correlated position "
                        f"will trigger block."
                    )

    # Rule 3: Sector exposure check
    proposed_sector = get_sector(proposed_underlying)
    sector_counts = {}
    total_value = 0.0

    # Get account equity for percentage calculation
    acct = alpaca_request(DT_BASE, DT_KEY, DT_SECRET, "GET", "/v2/account")
    equity = float(acct.get("equity", 100000)) if acct else 100000

    for p in positions:
        sector = get_sector(p["underlying"])
        mv = abs(p["market_value"])
        sector_counts.setdefault(sector, {"count": 0, "value": 0.0})
        sector_counts[sector]["count"] += 1
        sector_counts[sector]["value"] += mv
        total_value += mv

    # Add proposed position (estimate value as 1 unit of equity share)
    avg_position_value = total_value / max(position_count, 1) if position_count > 0 else equity * 0.08
    sector_counts.setdefault(proposed_sector, {"count": 0, "value": 0.0})
    sector_counts[proposed_sector]["count"] += 1
    sector_counts[proposed_sector]["value"] += avg_position_value

    # Compute sector exposure percentages
    total_with_new = total_value + avg_position_value
    for sector, info in sector_counts.items():
        pct = info["value"] / max(total_with_new, 1) if total_with_new > 0 else 0
        result["sector_exposure"][sector] = {
            "count": info["count"],
            "value": round(info["value"], 2),
            "pct_of_portfolio": round(pct, 4),
        }

        if pct > SECTOR_LIMIT_PCT:
            result["warnings"].append(
                f"WARN: Sector '{sector}' exposure {pct:.1%} exceeds {SECTOR_LIMIT_PCT:.0%} limit "
                f"({info['count']} positions, ${info['value']:.0f})"
            )
            log(f"Sector warning: {sector} at {pct:.1%}")

    # Log warnings
    for w in result["warnings"]:
        log(w)

    if result["allowed"]:
        log(f"ALLOWED: {proposed_underlying} passes all checks")

    # Save position risk metrics
    try:
        risk_path = DATA_DIR / "position_risk.json"
        risk_data = {
            "timestamp": result["timestamp"],
            "open_positions": position_count,
            "max_positions": MAX_POSITIONS,
            "sector_exposure": result["sector_exposure"],
            "last_check": {
                "symbol": proposed_underlying,
                "allowed": result["allowed"],
                "reason": result["reason"],
                "correlation_with_existing": result["correlation_with_existing"],
            },
        }
        risk_path.write_text(json.dumps(risk_data, indent=2))
    except Exception as e:
        log(f"Risk data write error: {e}")

    log_decision(result)
    return result


# ============ TEST / CLI ============

def run_mock_test():
    """
    Test scenario: NVDA, AMD, TSLA are open. Try to add AVGO.
    Should warn about tech concentration.
    """
    log("=== Running mock test scenario ===")
    log("Mock: pretending NVDA, AMD, TSLA are open positions")

    # Override get_open_positions for test
    mock_positions = [
        {"symbol": "NVDA", "underlying": "NVDA", "market_value": 15000, "qty": 10, "side": "long"},
        {"symbol": "AMD", "underlying": "AMD", "market_value": 12000, "qty": 50, "side": "long"},
        {"symbol": "TSLA", "underlying": "TSLA", "market_value": 18000, "qty": 30, "side": "long"},
    ]

    # Save original and monkey-patch
    original_get_positions = globals().get("get_open_positions")

    def mock_get_positions(account="day_trade"):
        return mock_positions

    globals()["get_open_positions"] = mock_get_positions

    # Test AVGO
    log("Testing: add AVGO with NVDA, AMD, TSLA open...")
    result = check_position("AVGO")
    log(f"Result: allowed={result['allowed']}")
    log(f"Reason: {result['reason']}")
    log(f"Warnings: {result['warnings']}")
    log(f"Correlations: {result['correlation_with_existing']}")
    log(f"Sector exposure: {json.dumps(result['sector_exposure'], indent=2)}")

    # Restore
    if original_get_positions:
        globals()["get_open_positions"] = original_get_positions

    return result


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_mock_test()
    elif len(sys.argv) > 1:
        symbol = sys.argv[1]
        result = check_position(symbol)
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Usage: position_correlation_guard.py <SYMBOL>")
        print("       position_correlation_guard.py --test")

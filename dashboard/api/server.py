#!/usr/bin/env python3
"""
Global Sentinel V5.1 — Dashboard API Server

FastAPI backend serving scorecards, heartbeat, bridge status, execution logs,
and real-time updates via WebSocket.

Usage:
    uvicorn dashboard.api.server:app --host 0.0.0.0 --port 8501
    python3 -m dashboard.api.server  # dev mode
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(os.getenv("GS_REPO_ROOT", "/opt/global-sentinel")).resolve()
API_KEY = os.getenv("GS_DASHBOARD_API_KEY", "")

app = FastAPI(title="Global Sentinel Dashboard API", version="5.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    """Require API key for /api/ endpoints when GS_DASHBOARD_API_KEY is set."""
    if API_KEY and request.url.path.startswith("/api/"):
        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if key != API_KEY:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_jsonl(path: Path, limit: int = 500) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-limit:]


def load_scorecards(limit: int = 200) -> List[Dict[str, Any]]:
    d = REPO_ROOT / "logs" / "scorecards"
    if not d.exists():
        return []
    files = sorted(d.glob("scorecard_*.json"), reverse=True)[:limit]
    cards = []
    for f in reversed(files):
        try:
            cards.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return cards


ALPACA_ACCOUNT_CACHE_TTL_SECONDS = 5.0
ALPACA_HISTORY_CACHE_TTL_SECONDS = {
    "1H": 10.0,
    "1D": 30.0,
}
_ALPACA_RESPONSE_CACHE: Dict[str, Dict[str, Any]] = {}
_ALPACA_RESPONSE_CACHE_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _iso_from_unix_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _cache_lookup(key: str, ttl_seconds: float) -> Optional[Dict[str, Any]]:
    now_monotonic = time.monotonic()
    with _ALPACA_RESPONSE_CACHE_LOCK:
        entry = _ALPACA_RESPONSE_CACHE.get(key)
        if not entry:
            return None
        age_seconds = now_monotonic - float(entry.get("stored_at_monotonic", 0.0))
        if age_seconds > ttl_seconds:
            return None
        return {
            "value": copy.deepcopy(entry["value"]),
            "fetched_at_utc": str(entry["fetched_at_utc"]),
            "cache_age_ms": round(age_seconds * 1000.0, 1),
            "cache_status": "hit",
        }


def _cache_store(key: str, value: Dict[str, Any]) -> str:
    fetched_at_utc = _utc_now_iso()
    with _ALPACA_RESPONSE_CACHE_LOCK:
        _ALPACA_RESPONSE_CACHE[key] = {
            "value": copy.deepcopy(value),
            "stored_at_monotonic": time.monotonic(),
            "fetched_at_utc": fetched_at_utc,
        }
    return fetched_at_utc


def _cache_status_from_items(items: List[Dict[str, Any]]) -> str:
    statuses = [str(item.get("cache_status") or "miss") for item in items if isinstance(item, dict)]
    if not statuses:
        return "miss"
    if all(status == "hit" for status in statuses):
        return "hit"
    if any(status == "hit" for status in statuses):
        return "mixed"
    return "miss"


def _freshness_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    parsed_source_times = [
        parsed
        for parsed in (_parse_iso_datetime(item.get("source_timestamp_utc")) for item in items)
        if parsed is not None
    ]
    parsed_fetched_times = [
        parsed
        for parsed in (_parse_iso_datetime(item.get("fetched_at_utc")) for item in items)
        if parsed is not None
    ]
    cache_ages = [
        float(item.get("cache_age_ms") or 0.0)
        for item in items
        if isinstance(item, dict)
    ]
    return {
        "source_timestamp_utc": min(parsed_source_times).isoformat() if parsed_source_times else None,
        "latest_source_timestamp_utc": max(parsed_source_times).isoformat() if parsed_source_times else None,
        "fetched_at_utc": max(parsed_fetched_times).isoformat() if parsed_fetched_times else _utc_now_iso(),
        "cache_age_ms": round(max(cache_ages), 1) if cache_ages else 0.0,
        "cache_status": _cache_status_from_items(items),
    }


# ---------------------------------------------------------------------------
# Dual Alpaca Account Helpers
# ---------------------------------------------------------------------------

def _load_env():
    """Load .env once per request if not already loaded."""
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _get_alpaca_accounts() -> List[Dict[str, Any]]:
    """Return credential dicts for all configured Alpaca paper accounts."""
    _load_env()
    accounts = []

    # Primary account (day_trade)
    key1 = os.getenv("ALPACA_API_KEY")
    sec1 = os.getenv("ALPACA_SECRET_KEY")
    url1 = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
    if key1 and sec1:
        accounts.append({
            "label": "day_trade",
            "api_key": key1,
            "api_secret": sec1,
            "base_url": url1,
        })

    # Also check DAYTRADE-specific keys (may be same as primary)
    key_dt = os.getenv("ALPACA_API_KEY_DAYTRADE")
    sec_dt = os.getenv("ALPACA_SECRET_KEY_DAYTRADE")
    if key_dt and sec_dt and key_dt != key1:
        accounts.append({
            "label": "day_trade_2",
            "api_key": key_dt,
            "api_secret": sec_dt,
            "base_url": os.getenv("ALPACA_BASE_URL_DAYTRADE", "https://paper-api.alpaca.markets/v2"),
        })

    # Medium/Long account
    key_ml = os.getenv("ALPACA_API_KEY_MEDLONG")
    sec_ml = os.getenv("ALPACA_SECRET_KEY_MEDLONG")
    if key_ml and sec_ml:
        accounts.append({
            "label": "medium_long",
            "api_key": key_ml,
            "api_secret": sec_ml,
            "base_url": os.getenv("ALPACA_BASE_URL_MEDLONG", "https://paper-api.alpaca.markets/v2"),
        })

    return accounts


def _fetch_alpaca_account(acct: Dict[str, str]) -> Dict[str, Any]:
    """Fetch account info + positions for a single Alpaca account."""
    import urllib.request
    import urllib.error
    headers = {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["api_secret"],
    }
    base = acct["base_url"]

    # Rate limiter for this account's API key
    try:
        from src.utils.rate_limiter import get_limiter, retry_with_backoff
        limiter = get_limiter(acct["api_key"], max_rpm=180)
    except ImportError:
        limiter = None

    def _get(path: str) -> Any:
        def _do():
            if limiter:
                limiter.acquire(timeout=30.0)
            url = f"{base}{path}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        try:
            return retry_with_backoff(_do, max_retries=2, base_delay=1.0)
        except Exception:
            return _do()

    account = _get("/account")
    positions_raw = _get("/positions")
    positions = []
    for p in positions_raw:
        positions.append({
            "symbol": p.get("symbol"),
            "qty": float(p.get("qty", 0)),
            "side": p.get("side", "long"),
            "avg_entry_price": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
            "market_value": float(p.get("market_value", 0)),
        })
    return {
        "label": acct["label"],
        "account_number": account.get("account_number", ""),
        "equity": float(account.get("equity", 0)),
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "portfolio_value": float(account.get("portfolio_value", 0)),
        "positions": positions,
        "position_count": len(positions),
        "status": "ok",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_alpaca_history(acct: Dict[str, str], period: str, timeframe: str) -> Dict[str, Any]:
    """Fetch portfolio history for a single Alpaca account."""
    import urllib.request
    headers = {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["api_secret"],
    }

    try:
        from src.utils.rate_limiter import get_limiter, retry_with_backoff
        limiter = get_limiter(acct["api_key"], max_rpm=180)
    except ImportError:
        limiter = None

    def _do():
        if limiter:
            limiter.acquire(timeout=30.0)
        url = f"{acct['base_url']}/account/portfolio/history?period={period}&timeframe={timeframe}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        return retry_with_backoff(_do, max_retries=2, base_delay=1.0)
    except Exception:
        return _do()


def _get_cached_alpaca_account(acct: Dict[str, str]) -> Dict[str, Any]:
    cache_key = f"alpaca_account:{acct['label']}"
    cached = _cache_lookup(cache_key, ttl_seconds=ALPACA_ACCOUNT_CACHE_TTL_SECONDS)
    if cached:
        payload = cached["value"]
        payload["source_timestamp_utc"] = payload.get("source_timestamp_utc") or payload.get("timestamp_utc") or cached["fetched_at_utc"]
        payload["fetched_at_utc"] = cached["fetched_at_utc"]
        payload["cache_age_ms"] = cached["cache_age_ms"]
        payload["cache_status"] = cached["cache_status"]
        return payload

    payload = _fetch_alpaca_account(acct)
    fetched_at_utc = _cache_store(cache_key, payload)
    payload = copy.deepcopy(payload)
    payload["source_timestamp_utc"] = payload.get("timestamp_utc") or fetched_at_utc
    payload["fetched_at_utc"] = fetched_at_utc
    payload["cache_age_ms"] = 0.0
    payload["cache_status"] = "miss"
    return payload


def _get_cached_alpaca_history(acct: Dict[str, str], period: str, timeframe: str) -> Dict[str, Any]:
    cache_key = f"alpaca_history:{acct['label']}:{period}:{timeframe}"
    ttl_seconds = ALPACA_HISTORY_CACHE_TTL_SECONDS.get(timeframe, 30.0)
    cached = _cache_lookup(cache_key, ttl_seconds=ttl_seconds)
    if cached:
        payload = cached["value"]
        timestamps = payload.get("timestamp") or []
        payload["schema_version"] = "dashboard.portfolio_history.v1"
        payload["account"] = acct["label"]
        payload["requested_period"] = period
        payload["requested_timeframe"] = timeframe
        payload["source_timestamp_utc"] = payload.get("source_timestamp_utc") or _iso_from_unix_timestamp(timestamps[-1] if timestamps else None) or cached["fetched_at_utc"]
        payload["fetched_at_utc"] = cached["fetched_at_utc"]
        payload["cache_age_ms"] = cached["cache_age_ms"]
        payload["cache_status"] = cached["cache_status"]
        return payload

    payload = _fetch_alpaca_history(acct, period, timeframe)
    fetched_at_utc = _cache_store(cache_key, payload)
    payload = copy.deepcopy(payload)
    timestamps = payload.get("timestamp") or []
    payload["schema_version"] = "dashboard.portfolio_history.v1"
    payload["account"] = acct["label"]
    payload["requested_period"] = period
    payload["requested_timeframe"] = timeframe
    payload["source_timestamp_utc"] = _iso_from_unix_timestamp(timestamps[-1] if timestamps else None) or fetched_at_utc
    payload["fetched_at_utc"] = fetched_at_utc
    payload["cache_age_ms"] = 0.0
    payload["cache_status"] = "miss"
    return payload


def _merge_portfolio_histories(histories: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Merge per-account Alpaca portfolio history into one combined equity curve."""
    valid_histories = {
        label: hist
        for label, hist in histories.items()
        if isinstance(hist, dict) and hist.get("timestamp") and hist.get("equity")
    }
    if not valid_histories:
        return {"accounts": histories, "error": "No valid history from any account"}

    all_timestamps = sorted({
        int(ts)
        for hist in valid_histories.values()
        for ts in (hist.get("timestamp") or [])
        if ts is not None
    })
    if not all_timestamps:
        return {"accounts": histories, "error": "No valid history from any account"}

    per_account_points: Dict[str, Dict[int, Dict[str, float]]] = {}
    per_account_base: Dict[str, float] = {}

    for label, hist in valid_histories.items():
        timestamps = hist.get("timestamp") or []
        equities = hist.get("equity") or []
        profits = hist.get("profit_loss") or []
        profit_pcts = hist.get("profit_loss_pct") or []
        base_value = float(hist.get("base_value") or 0.0)
        point_map: Dict[int, Dict[str, float]] = {}
        for idx, raw_ts in enumerate(timestamps):
            if raw_ts is None:
                continue
            ts = int(raw_ts)
            eq = float(equities[idx]) if idx < len(equities) and equities[idx] is not None else 0.0
            pl = float(profits[idx]) if idx < len(profits) and profits[idx] is not None else 0.0
            plpc = (
                float(profit_pcts[idx])
                if idx < len(profit_pcts) and profit_pcts[idx] is not None
                else 0.0
            )
            point_map[ts] = {
                "equity": eq,
                "profit_loss": pl,
                "profit_loss_pct": plpc,
            }
        per_account_points[label] = point_map
        if base_value > 0:
            per_account_base[label] = base_value
        elif timestamps and equities:
            per_account_base[label] = float(equities[0] or 0.0)
        else:
            per_account_base[label] = 0.0

    merged_timestamp: List[int] = []
    merged_equity: List[float] = []
    merged_profit_loss: List[float] = []
    merged_profit_loss_pct: List[float] = []
    combined_base_value = sum(per_account_base.values())

    last_seen_by_account: Dict[str, Dict[str, float]] = {}
    for ts in all_timestamps:
        total_equity = 0.0
        total_profit_loss = 0.0
        contributing_accounts = 0

        for label, point_map in per_account_points.items():
            if ts in point_map:
                last_seen_by_account[label] = point_map[ts]
            point = last_seen_by_account.get(label)
            if point is None:
                continue
            total_equity += float(point.get("equity") or 0.0)
            total_profit_loss += float(point.get("profit_loss") or 0.0)
            contributing_accounts += 1

        if contributing_accounts == 0:
            continue

        merged_timestamp.append(ts)
        merged_equity.append(total_equity)
        merged_profit_loss.append(total_profit_loss)
        base = combined_base_value or total_equity
        merged_profit_loss_pct.append((total_profit_loss / base) if base else 0.0)

    timeframe = next((hist.get("timeframe") for hist in valid_histories.values() if hist.get("timeframe")), "1D")
    return {
        "timestamp": merged_timestamp,
        "equity": merged_equity,
        "profit_loss": merged_profit_loss,
        "profit_loss_pct": merged_profit_loss_pct,
        "base_value": combined_base_value or (merged_equity[0] if merged_equity else 0.0),
        "timeframe": timeframe,
        "accounts": histories,
    }


def _fetch_alpaca_orders(acct: Dict[str, str], limit: int = 100, status: str = "all") -> List[Dict[str, Any]]:
    """Fetch recent orders for a single Alpaca account."""
    import urllib.parse
    import urllib.request

    headers = {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["api_secret"],
    }

    try:
        from src.utils.rate_limiter import get_limiter, retry_with_backoff
        limiter = get_limiter(acct["api_key"], max_rpm=180)
    except ImportError:
        limiter = None

    def _do():
        if limiter:
            limiter.acquire(timeout=30.0)
        params = urllib.parse.urlencode({
            "status": status,
            "direction": "desc",
            "nested": "false",
            "limit": max(1, min(limit, 500)),
        })
        url = f"{acct['base_url']}/orders?{params}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        return retry_with_backoff(_do, max_retries=2, base_delay=1.0)
    except Exception:
        return _do()


def _parse_iso_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _alpaca_order_timestamp(order: Dict[str, Any]) -> Optional[datetime]:
    for key in ("submitted_at", "created_at", "updated_at", "filled_at"):
        parsed = _parse_iso_utc(order.get(key))
        if parsed is not None:
            return parsed
    return None


def _normalize_live_order_status(raw_status: Any) -> str:
    status = str(raw_status or "").lower()
    if status == "filled":
        return "filled"
    if status == "partially_filled":
        return "partially_filled"
    if status in {"rejected"}:
        return "rejected"
    if status in {"canceled", "cancelled"}:
        return "canceled"
    if status in {"expired"}:
        return "expired"
    if status in {
        "new",
        "accepted",
        "pending",
        "pending_new",
        "accepted_for_bidding",
        "accepted_for_execution",
        "pending_replace",
        "pending_cancel",
        "replaced",
        "done_for_day",
        "stopped",
        "suspended",
        "calculated",
    }:
        return "open"
    return "other"


def _derived_skip_reason(item: Dict[str, Any]) -> str:
    reason = str(item.get("reason") or item.get("error") or "unknown")
    if reason == "risk_gate_blocked":
        failed_gates = []
        for gate in ((item.get("risk_gate") or {}).get("gates") or []):
            if gate.get("pass", True) is False:
                failed_gates.append(str(gate.get("gate") or gate.get("reason") or "unknown"))
        if failed_gates:
            return f"risk_gate:{'+'.join(sorted(set(failed_gates)))}"
    return reason


def _categorize_execution_reason(reason: str) -> str:
    low = reason.lower()
    if "risk_gate" in low or "impact_budget" in low or "exposure" in low or "var" in low:
        return "Risk Gate"
    if "max_orders" in low or "max orders" in low or "capacity" in low:
        return "Capacity"
    if "confidence" in low:
        return "Confidence"
    if "short" in low or "shortable" in low:
        return "Shortability"
    if "manual_review" in low or "manual review" in low:
        return "Manual Review"
    if "watchlist" in low or "time_window" in low or "window" in low:
        return "Time Window"
    if "data" in low or "missing" in low or "microstructure" in low:
        return "Data / Market Data"
    return "Other"


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp_utc": datetime.now(timezone.utc).isoformat()}


@app.get("/api/heartbeat")
def heartbeat():
    return load_json(REPO_ROOT / "logs" / "heartbeat.json")


@app.get("/api/controls")
def controls():
    return {
        "kill_switch": load_json(REPO_ROOT / "control" / "kill_switch.json"),
        "manual_veto": load_json(REPO_ROOT / "control" / "manual_veto.json"),
    }


@app.get("/api/scorecard/latest")
def latest_scorecard():
    cards = load_scorecards(limit=1)
    if cards:
        return cards[0]
    return {"error": "no scorecards found"}


@app.get("/api/scorecards")
def scorecards(limit: int = Query(default=100, le=500)):
    return load_scorecards(limit=limit)


@app.get("/api/scorecards/timeline")
def scorecard_timeline(limit: int = Query(default=200, le=500)):
    """Condensed timeline for charting — regime_p, confidence, mode, component_scores over time."""
    cards = load_scorecards(limit=limit)
    timeline = []
    for sc in cards:
        timeline.append({
            "timestamp_utc": sc.get("timestamp_utc"),
            "cycle": sc.get("cycle"),
            "mode": sc.get("mode"),
            "regime_p": sc.get("regime_shift_probability"),
            "confidence": sc.get("confidence"),
            "components": sc.get("component_scores", {}),
            "bridge_summary": sc.get("bridge_summary", {}),
            "shadow_eligible": sc.get("shadow_execution_eligible"),
            "fallback": sc.get("fallback_mode_status"),
        })
    return timeline


@app.get("/api/bridges")
def bridge_status():
    """Current bridge health from latest scorecard."""
    sc = load_scorecards(limit=1)
    if not sc:
        return {"bridges": {}, "freshness": {}}
    card = sc[0]
    return {
        "bridge_summary": card.get("bridge_summary", {}),
        "data_freshness": card.get("data_freshness_status", {}),
        "fallback_mode": card.get("fallback_mode_status", False),
        "timestamp_utc": card.get("timestamp_utc"),
    }


@app.get("/api/trade-analysis")
def trade_analysis():
    """Generate trade ideas from current regime state and historical patterns."""
    cards = load_scorecards(limit=2)
    if not cards:
        return {"error": "no scorecards"}

    current = cards[-1]
    prev_mode = cards[-2].get("mode") if len(cards) > 1 else None

    # Load microstructure cache
    micro = {}
    cache_dir = REPO_ROOT / "logs" / "bridge_cache" / "market_microstructure"
    if cache_dir.exists():
        cache_files = sorted(cache_dir.glob("microstructure_*.json"), reverse=True)
        if cache_files:
            try:
                cache_data = json.loads(cache_files[0].read_text(encoding="utf-8"))
                micro = cache_data.get("symbols", {})
            except Exception:
                pass

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.alpha.trade_analysis_engine import TradeAnalysisEngine
        engine = TradeAnalysisEngine(REPO_ROOT)
        return engine.analyze(current, previous_mode=prev_mode, microstructure=micro)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/performance")
def performance():
    """Shadow trading performance summary."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.execution.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker(REPO_ROOT)
        return tracker.generate_summary()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/execution/orders")
def execution_orders(limit: int = Query(default=100, le=500)):
    return load_jsonl(REPO_ROOT / "logs" / "execution" / "shadow_order_router.jsonl", limit=limit)


@app.get("/api/execution/bindings")
def execution_bindings(limit: int = Query(default=100, le=500)):
    return load_jsonl(REPO_ROOT / "logs" / "execution" / "router_order_bindings.jsonl", limit=limit)


@app.get("/api/execution/intents")
def execution_intents(limit: int = Query(default=100, le=500)):
    return load_jsonl(REPO_ROOT / "logs" / "execution" / "order_intents.jsonl", limit=limit)


@app.get("/api/execution/summary")
def execution_summary(
    router_limit: int = Query(default=100, ge=1, le=500),
    broker_limit: int = Query(default=100, ge=1, le=500),
    lookback_hours: int = Query(default=24, ge=1, le=168),
):
    router_events = load_jsonl(REPO_ROOT / "logs" / "execution" / "shadow_order_router.jsonl", limit=router_limit)

    processed_candidate_count = 0
    submit_attempt_count = 0
    submit_success_count = 0
    broker_rejected_count = 0
    skipped_count = 0
    error_count = 0
    event_count = 0
    raw_block_reason_counts: Dict[str, int] = {}
    block_reason_category_counts: Dict[str, int] = {}

    for event in router_events:
        if event.get("event_type") != "route_package_complete":
            continue

        payload = event.get("payload") or {}
        event_count += 1

        event_submit_attempt_count = int(payload.get("submit_attempt_count") or 0)
        event_submit_success_count = payload.get("submitted_open_or_ack_count")
        if event_submit_success_count is None:
            event_submit_success_count = payload.get("broker_acknowledged_count")
        if event_submit_success_count is None:
            event_submit_success_count = max(
                event_submit_attempt_count - int(payload.get("broker_rejected_count") or 0),
                0,
            )
        event_submit_success_count = int(event_submit_success_count or 0)

        event_broker_rejected_count = int(payload.get("broker_rejected_count") or 0)
        event_skipped = list(payload.get("skipped_candidates") or [])
        event_errors = list(payload.get("errors") or [])
        event_error_count = len(event_errors)
        event_skipped_count = len(event_skipped)
        event_candidate_count = int(payload.get("candidate_count_in_package") or 0)
        if event_candidate_count <= 0:
            event_candidate_count = (
                event_submit_success_count
                + event_broker_rejected_count
                + event_skipped_count
                + event_error_count
            )

        processed_candidate_count += event_candidate_count
        submit_attempt_count += event_submit_attempt_count
        submit_success_count += event_submit_success_count
        broker_rejected_count += event_broker_rejected_count
        skipped_count += event_skipped_count
        error_count += event_error_count

        for item in event_skipped + event_errors:
            derived_reason = _derived_skip_reason(item)
            raw_block_reason_counts[derived_reason] = raw_block_reason_counts.get(derived_reason, 0) + 1
            category = _categorize_execution_reason(derived_reason)
            block_reason_category_counts[category] = block_reason_category_counts.get(category, 0) + 1

    broker_attempt_total = submit_success_count + broker_rejected_count
    candidate_conversion_rate = submit_success_count / max(processed_candidate_count, 1)
    broker_accept_rate = submit_success_count / max(broker_attempt_total, 1) if broker_attempt_total else 0.0
    skip_or_block_rate = (skipped_count + error_count) / max(processed_candidate_count, 1)

    live_orders: Dict[str, Any] = {
        "status": "unavailable",
        "lookback_hours": lookback_hours,
        "sample_window_start_utc": (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat(),
        "account_count": 0,
        "order_count_total": 0,
        "filled_count": 0,
        "partially_filled_count": 0,
        "open_count": 0,
        "rejected_count": 0,
        "canceled_count": 0,
        "expired_count": 0,
        "other_count": 0,
        "fill_rate_any": 0.0,
        "fill_rate_full": 0.0,
        "open_rate": 0.0,
        "by_account": {},
        "raw_status_counts": {},
        "account_errors": [],
    }

    accounts = _get_alpaca_accounts()
    if accounts:
        live_orders["account_count"] = len(accounts)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        success_count = 0

        for acct in accounts:
            try:
                rows = _fetch_alpaca_orders(acct, limit=broker_limit, status="all")
                filtered_orders = []
                status_counts: Dict[str, int] = {
                    "filled": 0,
                    "partially_filled": 0,
                    "open": 0,
                    "rejected": 0,
                    "canceled": 0,
                    "expired": 0,
                    "other": 0,
                }
                raw_status_counts: Dict[str, int] = {}

                for row in rows:
                    order_ts = _alpaca_order_timestamp(row)
                    if order_ts is not None and order_ts < cutoff:
                        continue
                    filtered_orders.append(row)
                    raw_status = str(row.get("status") or "unknown").lower()
                    raw_status_counts[raw_status] = raw_status_counts.get(raw_status, 0) + 1
                    normalized = _normalize_live_order_status(raw_status)
                    status_counts[normalized] += 1

                success_count += 1
                live_orders["order_count_total"] += len(filtered_orders)
                for key, value in status_counts.items():
                    live_orders[f"{key}_count"] += value
                for raw_status, value in raw_status_counts.items():
                    live_orders["raw_status_counts"][raw_status] = live_orders["raw_status_counts"].get(raw_status, 0) + value

                order_count_total = len(filtered_orders)
                live_orders["by_account"][acct["label"]] = {
                    "order_count_total": order_count_total,
                    **status_counts,
                    "fill_rate_any": round(
                        (status_counts["filled"] + status_counts["partially_filled"]) / max(order_count_total, 1),
                        4,
                    ) if order_count_total else 0.0,
                    "fill_rate_full": round(status_counts["filled"] / max(order_count_total, 1), 4) if order_count_total else 0.0,
                }
            except Exception as e:
                live_orders["account_errors"].append({"label": acct["label"], "error": str(e)})

        if success_count == len(accounts):
            live_orders["status"] = "ok"
        elif success_count > 0:
            live_orders["status"] = "partial"
        else:
            live_orders["status"] = "error"

        total_live_orders = max(live_orders["order_count_total"], 1)
        live_orders["fill_rate_any"] = round(
            (live_orders["filled_count"] + live_orders["partially_filled_count"]) / total_live_orders,
            4,
        ) if live_orders["order_count_total"] else 0.0
        live_orders["fill_rate_full"] = round(live_orders["filled_count"] / total_live_orders, 4) if live_orders["order_count_total"] else 0.0
        live_orders["open_rate"] = round(live_orders["open_count"] / total_live_orders, 4) if live_orders["order_count_total"] else 0.0

    return {
        "schema_version": "dashboard.execution_summary.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "routing": {
            "event_count": event_count,
            "processed_candidate_count": processed_candidate_count,
            "submit_attempt_count": submit_attempt_count,
            "submit_success_count": submit_success_count,
            "broker_rejected_count": broker_rejected_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "candidate_conversion_rate": round(candidate_conversion_rate, 4),
            "broker_accept_rate": round(broker_accept_rate, 4),
            "skip_or_block_rate": round(skip_or_block_rate, 4),
            "block_reason_category_counts": block_reason_category_counts,
            "raw_block_reason_counts": raw_block_reason_counts,
        },
        "live_orders": live_orders,
    }


@app.get("/api/alerts")
def alerts(limit: int = Query(default=50, le=200)):
    return load_jsonl(REPO_ROOT / "logs" / "events" / "alerts.jsonl", limit=limit)


@app.get("/api/events")
def events(limit: int = Query(default=100, le=500)):
    return load_jsonl(REPO_ROOT / "logs" / "events" / "crisis_monitor_events.jsonl", limit=limit)


@app.get("/api/graduation")
def graduation():
    report = load_json(REPO_ROOT / "reports" / "weekly" / "graduation_assessment.json")
    if not report:
        return {"error": "no graduation assessment found"}
    return report


@app.get("/api/thresholds")
def thresholds():
    try:
        import yaml
        return yaml.safe_load(
            (REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8")
        )
    except Exception:
        return {"error": "could not load thresholds"}


@app.get("/api/consciousness")
def consciousness():
    """Latest GCP consciousness coherence data. Runs bridge on-demand if no cache."""
    cache_dir = REPO_ROOT / "logs" / "bridge_cache" / "gcp_consciousness"
    if cache_dir.exists():
        cache_files = sorted(cache_dir.glob("gcp_*.json"), reverse=True)
        if cache_files:
            data = load_json(cache_files[0])
            if data and data.get("fresh"):
                return data

    # Run bridge on-demand
    try:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from src.bridges.gcp_consciousness_bridge import GCPConsciousnessBridge
        bridge = GCPConsciousnessBridge(REPO_ROOT)
        result = bridge.poll()
        # Cache the result
        cache_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = cache_dir / f"gcp_{tag}.json"
        cache_file.write_text(json.dumps(result, indent=2))
        return result
    except Exception as e:
        return {"error": f"consciousness bridge failed: {str(e)}"}


@app.get("/api/politician-alpha")
def politician_alpha():
    """Latest congressional trading / politician alpha data from bridge cache.
    Falls back to running bridge on-demand if no cache exists."""
    cache_dir = REPO_ROOT / "logs" / "bridge_cache" / "politician_alpha"
    if cache_dir.exists():
        cache_files = sorted(cache_dir.glob("politician_alpha_*.json"), reverse=True)
        if cache_files:
            data = load_json(cache_files[0])
            if data and data.get("fresh"):
                return data

    # Try running the bridge on-demand
    try:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from src.bridges.politician_alpha_bridge import PoliticianAlphaBridge
        bridge = PoliticianAlphaBridge(REPO_ROOT)
        result = bridge.poll()
        if result.get("fresh"):
            return result
        # Bridge returned non-fresh (no API key) — check cache again
        if cache_dir.exists():
            cache_files = sorted(cache_dir.glob("politician_alpha_*.json"), reverse=True)
            if cache_files:
                return load_json(cache_files[0])
        return result  # Return whatever the bridge gave us (includes reason)
    except Exception as e:
        return {"error": f"politician alpha bridge failed: {str(e)}"}


@app.get("/api/time_window")
def time_window():
    sc = load_scorecards(limit=1)
    if not sc:
        return {}
    return sc[0].get("time_window", {})


@app.get("/api/portfolio-history")
def portfolio_history(period: str = Query("1M"), timeframe: str = Query("1D"), account: str = Query("all")):
    """Fetch Alpaca paper portfolio history for equity curve.
    period: 1D, 1W, 1M, 3M, 1A   timeframe: 1H, 1D
    account: all | day_trade | medium_long"""
    return _build_portfolio_history_payload(period=period, timeframe=timeframe, account=account)


def _build_portfolio_history_payload(period: str = "1M", timeframe: str = "1D", account: str = "all") -> Dict[str, Any]:
    if period not in ("1D", "1W", "1M", "3M", "1A"):
        period = "1M"
    if timeframe not in ("1H", "1D"):
        timeframe = "1D"

    accounts = _get_alpaca_accounts()
    if not accounts:
        return {"error": "Alpaca credentials not configured"}

    if account != "all":
        accounts = [a for a in accounts if a["label"] == account]
        if not accounts:
            return {"error": f"Account '{account}' not found"}

    # If single account requested, return raw history
    if len(accounts) == 1:
        try:
            hist = _get_cached_alpaca_history(accounts[0], period, timeframe)
            hist["timestamp_utc"] = _utc_now_iso()
            return hist
        except Exception as e:
            return {"error": str(e)}

    # Multi-account: return per-account histories
    results = {}
    successful_histories = []
    for acct in accounts:
        try:
            hist = _get_cached_alpaca_history(acct, period, timeframe)
            results[acct["label"]] = hist
            successful_histories.append(hist)
        except Exception as e:
            results[acct["label"]] = {"error": str(e)}
    merged = _merge_portfolio_histories(results)
    merged.update({
        "schema_version": "dashboard.portfolio_history.v1",
        "account": account,
        "requested_period": period,
        "requested_timeframe": timeframe,
        "timestamp_utc": _utc_now_iso(),
    })
    merged.update(_freshness_summary(successful_histories))
    if dashboard_live_state_manager is not None:
        merged["stream_health"] = dashboard_live_state_manager._stream_status_payload()
    return merged


@app.get("/api/portfolio")
def portfolio(account: str = Query("all")):
    """Fetch Alpaca paper account positions. Supports dual accounts.
    account: all | day_trade | medium_long"""
    return _build_portfolio_payload(account=account)


def _build_portfolio_payload(account: str = "all") -> Dict[str, Any]:
    """Fetch Alpaca paper account positions. Supports dual accounts.
    account: all | day_trade | medium_long"""
    accounts = _get_alpaca_accounts()
    if not accounts:
        return {"error": "Alpaca credentials not configured"}

    if account != "all":
        accounts = [a for a in accounts if a["label"] == account]
        if not accounts:
            return {"error": f"Account '{account}' not found"}

    all_positions = []
    total_equity = 0.0
    total_cash = 0.0
    total_buying_power = 0.0
    total_portfolio_value = 0.0
    account_details = []
    account_errors = []
    position_count_by_account: Dict[str, int] = {}
    requested_accounts = [acct["label"] for acct in accounts]

    for acct in accounts:
        try:
            data = _get_cached_alpaca_account(acct)
            total_equity += data["equity"]
            total_cash += data["cash"]
            total_buying_power += data["buying_power"]
            total_portfolio_value += data["portfolio_value"]
            # Tag positions with account label
            for p in data["positions"]:
                p["account"] = data["label"]
            all_positions.extend(data["positions"])
            position_count_by_account[data["label"]] = data.get("position_count", len(data["positions"]))
            account_details.append(data)
        except Exception as e:
            position_count_by_account[acct["label"]] = 0
            account_errors.append({"label": acct["label"], "error": str(e)})
            account_details.append({
                "label": acct["label"],
                "status": "error",
                "error": str(e),
                "equity": 0.0,
                "cash": 0.0,
                "buying_power": 0.0,
                "portfolio_value": 0.0,
                "positions": [],
                "position_count": 0,
                "timestamp_utc": _utc_now_iso(),
            })

    account_count_success = sum(1 for detail in account_details if detail.get("status") != "error")
    account_count_error = len(account_errors)
    position_count_total_from_accounts = sum(position_count_by_account.values())

    if not account_details:
        status = "error"
    elif account_count_error == len(account_details):
        status = "error"
    elif account_errors:
        status = "partial"
    else:
        status = "ok"

    payload = {
        "schema_version": "dashboard.portfolio.v1",
        "status": status,
        "equity": total_equity,
        "cash": total_cash,
        "buying_power": total_buying_power,
        "portfolio_value": total_portfolio_value,
        "positions": all_positions,
        "accounts": account_details,
        "account_errors": account_errors,
        "position_count_total": len(all_positions),
        "position_count_by_account": position_count_by_account,
        "account_count": len(accounts),
        "consistency": {
            "account_count_requested": len(accounts),
            "account_count_success": account_count_success,
            "account_count_error": account_count_error,
            "position_count_total": len(all_positions),
            "position_count_total_from_accounts": position_count_total_from_accounts,
            "position_count_by_account": position_count_by_account,
            "requested_accounts": requested_accounts,
            "accounts_match_requested": len(account_details) == len(accounts),
            "positions_match_total": position_count_total_from_accounts == len(all_positions),
            "has_account_errors": bool(account_errors),
        },
        "timestamp_utc": _utc_now_iso(),
    }
    payload.update(_freshness_summary([
        detail for detail in account_details
        if isinstance(detail, dict) and detail.get("status") != "error"
    ]))
    if dashboard_live_state_manager is not None:
        payload["stream_health"] = dashboard_live_state_manager._stream_status_payload()
    return payload


# ---------------------------------------------------------------------------
# Execution Mode & Telegram Approval
# ---------------------------------------------------------------------------

def get_execution_mode_data():
    """Helper: return execution_mode dict from config."""
    try:
        import yaml
        config = yaml.safe_load(
            (REPO_ROOT / "config" / "execution_mode.yaml").read_text(encoding="utf-8")
        )
        return config.get("execution_mode", {})
    except Exception:
        return {}


@app.get("/api/execution-mode")
def get_execution_mode():
    """Get current execution mode for both strategies."""
    try:
        import yaml
        config = yaml.safe_load(
            (REPO_ROOT / "config" / "execution_mode.yaml").read_text(encoding="utf-8")
        )
        return {
            "strategies": config.get("strategies", {}),
            "execution_mode": config.get("execution_mode", {}),
            "bot_permissions": config.get("bot_permissions", {}),
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/execution-mode")
async def set_execution_mode(request: Request):
    """Toggle execution mode for a strategy. Body: {"strategy": "day_trade"|"medium_long", "mode": "auto"|"manual"}"""
    try:
        import yaml
        body = await request.json()
        strategy = body.get("strategy")
        mode = body.get("mode")

        if strategy not in ("day_trade", "medium_long"):
            return JSONResponse(status_code=400, content={"error": "invalid strategy"})
        if mode not in ("auto", "manual"):
            return JSONResponse(status_code=400, content={"error": "invalid mode"})

        config_path = REPO_ROOT / "config" / "execution_mode.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["execution_mode"][strategy] = mode
        config_path.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")

        return {"status": "ok", "strategy": strategy, "mode": mode, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/telegram/approve")
async def telegram_approve(request: Request):
    """Approve pending manual-mode orders. Body: {"strategy": "day_trade"|"medium_long", "action": "approve"|"reject"}"""
    try:
        body = await request.json()
        strategy = body.get("strategy")
        action = body.get("action", "approve")

        if strategy not in ("day_trade", "medium_long"):
            return JSONResponse(status_code=400, content={"error": "invalid strategy"})

        # Write approval to a file the crisis monitor checks
        approval_path = REPO_ROOT / "control" / f"pending_approval_{strategy}.json"
        approval_data = {
            "strategy": strategy,
            "action": action,
            "approved": action == "approve",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        approval_path.write_text(json.dumps(approval_data, indent=2), encoding="utf-8")

        return {"status": "ok", **approval_data}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/pending-orders")
def pending_orders():
    """Get pending manual-mode orders waiting for approval."""
    pending = {}
    for strategy in ("day_trade", "medium_long"):
        pending_path = REPO_ROOT / "control" / f"pending_orders_{strategy}.json"
        if pending_path.exists():
            pending[strategy] = load_json(pending_path)
    return pending


# ---------------------------------------------------------------------------
# WebSocket for real-time updates
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = ConnectionManager()


def _portfolio_signature(payload: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "status": payload.get("status"),
            "equity": round(float(payload.get("equity") or 0.0), 2),
            "position_count_total": payload.get("position_count_total"),
            "source_timestamp_utc": payload.get("source_timestamp_utc"),
            "latest_source_timestamp_utc": payload.get("latest_source_timestamp_utc"),
        },
        sort_keys=True,
    )


def _portfolio_history_signature(payload: Dict[str, Any]) -> str:
    timestamps = payload.get("timestamp") or []
    equities = payload.get("equity") or []
    profits = payload.get("profit_loss") or []
    return json.dumps(
        {
            "point_count": len(timestamps),
            "last_timestamp": timestamps[-1] if timestamps else None,
            "last_equity": round(float(equities[-1]), 2) if equities else None,
            "last_profit_loss": round(float(profits[-1]), 2) if profits else None,
            "source_timestamp_utc": payload.get("source_timestamp_utc"),
            "latest_source_timestamp_utc": payload.get("latest_source_timestamp_utc"),
        },
        sort_keys=True,
    )


def _alpaca_trade_stream_url(acct: Dict[str, str]) -> str:
    base_url = str(acct.get("base_url") or "").lower()
    if "paper-api.alpaca.markets" in base_url:
        return "wss://paper-api.alpaca.markets/stream"
    return "wss://api.alpaca.markets/stream"


def _decode_ws_payload(message: Any) -> Optional[Dict[str, Any]]:
    try:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        data = json.loads(message)
        if isinstance(data, list):
            data = data[0] if data else {}
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _refresh_account_snapshot_sync(
    acct: Dict[str, str],
    history_specs: Optional[List[tuple[str, str]]] = None,
) -> Dict[str, Any]:
    history_specs = history_specs or [("1D", "1H"), ("1M", "1D")]
    account_payload = _fetch_alpaca_account(acct)
    _cache_store(f"alpaca_account:{acct['label']}", account_payload)

    refreshed_histories: Dict[str, Any] = {}
    for period, timeframe in history_specs:
        hist = _fetch_alpaca_history(acct, period, timeframe)
        _cache_store(f"alpaca_history:{acct['label']}:{period}:{timeframe}", hist)
        refreshed_histories[f"{period}:{timeframe}"] = hist

    return {
        "label": acct["label"],
        "account": account_payload,
        "histories": refreshed_histories,
    }


class DashboardLiveStateManager:
    def __init__(self):
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[Any]] = []
        self._latest_portfolio: Optional[Dict[str, Any]] = None
        self._latest_portfolio_history_intraday: Optional[Dict[str, Any]] = None
        self._latest_portfolio_signature: Optional[str] = None
        self._latest_portfolio_history_signature: Optional[str] = None
        self._stream_status: Dict[str, Dict[str, Any]] = {}

    def _stream_status_payload(self) -> Dict[str, Any]:
        return {
            label: {
                "status": details.get("status"),
                "last_event_utc": details.get("last_event_utc"),
                "last_error": details.get("last_error"),
                "reconnect_count": details.get("reconnect_count", 0),
            }
            for label, details in self._stream_status.items()
        }

    def get_latest_portfolio(self) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._latest_portfolio) if self._latest_portfolio else None

    def get_latest_portfolio_history_intraday(self) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._latest_portfolio_history_intraday) if self._latest_portfolio_history_intraday else None

    async def start(self):
        accounts = _get_alpaca_accounts()
        self._tasks.append(asyncio.create_task(self._poll_loop(accounts)))
        for acct in accounts:
            self._stream_status[acct["label"]] = {
                "status": "starting",
                "last_event_utc": None,
                "last_error": None,
                "reconnect_count": 0,
            }
            self._tasks.append(asyncio.create_task(self._trade_updates_loop(acct)))
        await self.refresh_and_broadcast(force=True, reason="startup")

    async def stop(self):
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _poll_loop(self, accounts: List[Dict[str, str]]):
        history_counter = 0
        while not self._stop_event.is_set():
            history_specs = [("1D", "1H")]
            if history_counter % 4 == 0:
                history_specs.append(("1M", "1D"))
            for acct in accounts:
                try:
                    await asyncio.to_thread(_refresh_account_snapshot_sync, acct, history_specs)
                except Exception as e:
                    details = self._stream_status.setdefault(acct["label"], {})
                    details["last_error"] = str(e)
                    details["status"] = "poll_error"
                    details["last_event_utc"] = _utc_now_iso()
            await self.refresh_and_broadcast(force=False, reason="poll")
            history_counter += 1
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                continue

    async def _trade_updates_loop(self, acct: Dict[str, str]):
        try:
            import websockets
        except Exception as e:
            details = self._stream_status.setdefault(acct["label"], {})
            details["status"] = "stream_unavailable"
            details["last_error"] = f"websockets import failed: {e}"
            details["last_event_utc"] = _utc_now_iso()
            return

        label = acct["label"]
        reconnect_delay = 1.0
        while not self._stop_event.is_set():
            ws = None
            try:
                details = self._stream_status.setdefault(label, {})
                details["status"] = "connecting"
                details["last_error"] = None
                details["last_event_utc"] = _utc_now_iso()
                ws = await websockets.connect(
                    _alpaca_trade_stream_url(acct),
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=128,
                )
                await ws.send(json.dumps({
                    "action": "auth",
                    "key": acct["api_key"],
                    "secret": acct["api_secret"],
                }))

                auth_deadline = asyncio.get_running_loop().time() + 10.0
                authorized = False
                while asyncio.get_running_loop().time() < auth_deadline and not authorized:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = _decode_ws_payload(raw) or {}
                    if data.get("stream") == "authorization":
                        status = ((data.get("data") or {}).get("status") or "").lower()
                        if status == "authorized":
                            authorized = True
                            break
                        raise RuntimeError(f"{label} unauthorized on Alpaca trade stream")
                if not authorized:
                    raise RuntimeError(f"{label} auth timeout on Alpaca trade stream")

                await ws.send(json.dumps({
                    "action": "listen",
                    "data": {"streams": ["trade_updates"]},
                }))

                details["status"] = "connected"
                details["last_event_utc"] = _utc_now_iso()
                reconnect_delay = 1.0

                while not self._stop_event.is_set():
                    raw = await ws.recv()
                    data = _decode_ws_payload(raw) or {}
                    details["last_event_utc"] = _utc_now_iso()
                    stream_name = str(data.get("stream") or "")
                    if stream_name == "authorization":
                        continue
                    if stream_name == "listening":
                        details["status"] = "listening"
                        continue
                    if stream_name != "trade_updates":
                        continue

                    details["status"] = "event"
                    event = str((data.get("data") or {}).get("event") or "")
                    if event:
                        details["last_event"] = event

                    await asyncio.to_thread(
                        _refresh_account_snapshot_sync,
                        acct,
                        [("1D", "1H"), ("1M", "1D")],
                    )
                    await self.refresh_and_broadcast(force=False, reason=f"trade_update:{label}")
            except asyncio.CancelledError:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                raise
            except Exception as e:
                details = self._stream_status.setdefault(label, {})
                details["status"] = "error"
                details["last_error"] = str(e)
                details["last_event_utc"] = _utc_now_iso()
                details["reconnect_count"] = int(details.get("reconnect_count", 0)) + 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=reconnect_delay)
                except asyncio.TimeoutError:
                    reconnect_delay = min(reconnect_delay * 2.0, 30.0)
                    continue

    async def refresh_and_broadcast(self, force: bool, reason: str):
        portfolio_payload = await asyncio.to_thread(_build_portfolio_payload, "all")
        portfolio_history_intraday = await asyncio.to_thread(
            _build_portfolio_history_payload,
            "1D",
            "1H",
            "all",
        )
        portfolio_payload["stream_health"] = self._stream_status_payload()
        portfolio_history_intraday["stream_health"] = self._stream_status_payload()

        portfolio_sig = _portfolio_signature(portfolio_payload)
        portfolio_history_sig = _portfolio_history_signature(portfolio_history_intraday)
        changed = (
            force
            or portfolio_sig != self._latest_portfolio_signature
            or portfolio_history_sig != self._latest_portfolio_history_signature
        )

        self._latest_portfolio = portfolio_payload
        self._latest_portfolio_history_intraday = portfolio_history_intraday
        self._latest_portfolio_signature = portfolio_sig
        self._latest_portfolio_history_signature = portfolio_history_sig

        if changed:
            await manager.broadcast({
                "type": "update",
                "portfolio": portfolio_payload,
                "portfolio_history_intraday": portfolio_history_intraday,
                "stream_refresh_reason": reason,
            })


dashboard_live_state_manager: Optional[DashboardLiveStateManager] = None


@app.on_event("startup")
async def _startup_dashboard_live_state():
    global dashboard_live_state_manager
    dashboard_live_state_manager = DashboardLiveStateManager()
    await dashboard_live_state_manager.start()


@app.on_event("shutdown")
async def _shutdown_dashboard_live_state():
    global dashboard_live_state_manager
    if dashboard_live_state_manager is not None:
        await dashboard_live_state_manager.stop()
        dashboard_live_state_manager = None


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if API_KEY:
        key = ws.query_params.get("api_key")
        if key != API_KEY:
            await ws.close(code=1008)
            return
    await manager.connect(ws)
    try:
        # Send initial state
        hb = load_json(REPO_ROOT / "logs" / "heartbeat.json")
        cards = load_scorecards(limit=1)
        portfolio_payload = None
        portfolio_history_intraday = None
        if dashboard_live_state_manager is not None:
            portfolio_payload = dashboard_live_state_manager.get_latest_portfolio()
            portfolio_history_intraday = dashboard_live_state_manager.get_latest_portfolio_history_intraday()
        if portfolio_payload is None:
            portfolio_payload = _build_portfolio_payload(account="all")
        if portfolio_history_intraday is None:
            portfolio_history_intraday = _build_portfolio_history_payload(period="1D", timeframe="1H", account="all")
        await ws.send_json({
            "type": "init",
            "heartbeat": hb,
            "scorecard": cards[0] if cards else None,
            "controls": {
                "kill_switch": load_json(REPO_ROOT / "control" / "kill_switch.json"),
                "manual_veto": load_json(REPO_ROOT / "control" / "manual_veto.json"),
            },
            "execution_mode": get_execution_mode_data(),
            "portfolio": portfolio_payload,
            "portfolio_history_intraday": portfolio_history_intraday,
        })
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("text") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except asyncio.CancelledError:
        manager.disconnect(ws)
        raise
    finally:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# GSS Signal Timeline (Econophysics / Three-Layer Graph)
# ---------------------------------------------------------------------------

@app.get("/api/gss-timeline")
def gss_timeline(limit: int = Query(100, ge=1, le=500)):
    """
    GSS three-layer signal timeline for the real-time econophysics graph.
    Returns Z-score, narrative velocity, VIX, and GSS signal over time.
    """
    # Read from GSS signal log (written by crisis_monitor)
    log_path = REPO_ROOT / "logs" / "gss" / "signal_timeline.jsonl"
    points: List[Dict[str, Any]] = []

    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[-limit:]:
                if not line.strip():
                    continue
                try:
                    points.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

    # If no GSS log yet, build from scorecard timeline + consciousness cache
    if not points:
        scorecards = load_scorecards(limit=limit)
        for sc in scorecards:
            points.append({
                "timestamp_utc": sc.get("timestamp_utc", ""),
                "z_score": sc.get("component_scores", {}).get("consciousness_coherence", 0) * 4,
                "narrative_velocity": sc.get("component_scores", {}).get("geopolitical_tension", 0) * 3,
                "vix": sc.get("component_scores", {}).get("market_volatility", 0) * 40 + 12,
                "regime_p": sc.get("regime_shift_probability", 0),
                "confidence": sc.get("confidence", 0),
                "gss_signal": sc.get("gss_signal", "NEUTRAL"),
                "mode": sc.get("mode", "NORMAL"),
            })

    return points


@app.get("/api/gss-latest")
def gss_latest():
    """Latest GSS signal analysis result."""
    log_path = REPO_ROOT / "logs" / "gss" / "latest_signal.json"
    if log_path.exists():
        return load_json(log_path)

    # Fallback: run GSS analysis on latest bridge data
    try:
        from src.alpha.gss_execution_engine import GSSExecutionEngine

        gss = GSSExecutionEngine(REPO_ROOT)
        # Load latest bridge data
        snapshot: Dict[str, Any] = {}
        for bridge_name in ("gcp_consciousness", "narrative_velocity", "market_microstructure", "options_greeks", "politician_alpha"):
            cache_dir = REPO_ROOT / "logs" / "bridge_cache" / bridge_name
            if cache_dir.exists():
                files = sorted(cache_dir.glob("*.json"), reverse=True)
                if files:
                    snapshot[bridge_name] = load_json(files[0])

        scorecards = load_scorecards(limit=1)
        scorecard = scorecards[0] if scorecards else {"mode": "NORMAL", "regime_shift_probability": 0}

        if snapshot:
            result = gss.analyze(snapshot, scorecard)
            return result
    except Exception as e:
        return {"error": str(e), "gss_signal": "UNAVAILABLE"}

    return {"gss_signal": "NO_DATA", "reason": "No bridge data available"}


# ---------------------------------------------------------------------------
# Control API — Write endpoints for remote management (OpenClaw bots, Telegram)
# ---------------------------------------------------------------------------

from pydantic import BaseModel

class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""

class VetoRequest(BaseModel):
    active: bool
    reason: str = ""

class ModeOverrideRequest(BaseModel):
    mode: str  # NORMAL, ELEVATED, CRISIS, MANUAL_REVIEW

class ServiceActionRequest(BaseModel):
    action: str  # restart, status

@app.post("/api/control/kill-switch")
def set_kill_switch(req: KillSwitchRequest):
    """Activate or deactivate the kill switch."""
    ks_path = REPO_ROOT / "control" / "kill_switch.json"
    data = {
        "kill_switch": req.active,
        "reason": req.reason or ("Activated via API" if req.active else None),
        "set_by": "api",
        "set_at": datetime.now(timezone.utc).isoformat() if req.active else None,
        "notes": "Set to true to halt ALL monitoring and agent activity. Emergency use only.",
    }
    ks_path.write_text(json.dumps(data, indent=2))
    return {"ok": True, "kill_switch": req.active, "reason": req.reason}

@app.post("/api/control/veto")
def set_veto(req: VetoRequest):
    """Activate or deactivate manual veto."""
    veto_path = REPO_ROOT / "control" / "manual_veto.json"
    data = {
        "manual_veto": req.active,
        "reason": req.reason or ("Activated via API" if req.active else None),
        "set_by": "api",
        "set_at": datetime.now(timezone.utc).isoformat() if req.active else None,
        "notes": "Set to true to halt all shadow draft generation. Requires human action to clear.",
    }
    veto_path.write_text(json.dumps(data, indent=2))
    return {"ok": True, "manual_veto": req.active, "reason": req.reason}

@app.get("/api/control/status")
def control_status():
    """Full system status for bot consumption."""
    hb = load_json(REPO_ROOT / "logs" / "heartbeat.json")
    ks = load_json(REPO_ROOT / "control" / "kill_switch.json")
    veto = load_json(REPO_ROOT / "control" / "manual_veto.json")
    cards = load_scorecards(limit=1)
    sc = cards[0] if cards else {}

    # Get execution mode
    exec_mode = {}
    exec_mode_path = REPO_ROOT / "config" / "execution_mode.yaml"
    if exec_mode_path.exists():
        try:
            import yaml
            exec_mode = yaml.safe_load(exec_mode_path.read_text()) or {}
        except Exception:
            pass

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": sc.get("mode", hb.get("mode", "UNKNOWN") if hb else "UNKNOWN"),
        "cycle": sc.get("cycle", hb.get("cycle", 0) if hb else 0),
        "regime_p": sc.get("regime_shift_probability", 0),
        "confidence": sc.get("confidence", 0),
        "kill_switch": ks.get("kill_switch", False) if ks else False,
        "manual_veto": veto.get("manual_veto", False) if veto else False,
        "shadow_eligible": sc.get("shadow_execution_eligible", False),
        "fallback_mode": sc.get("fallback_mode_status", False),
        "execution_mode": exec_mode.get("execution_mode", {}),
        "evidence": sc.get("evidence", [])[:5],
    }

@app.get("/api/control/portfolio-summary")
def portfolio_summary():
    """Compact portfolio summary for bot consumption."""
    try:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
        sys.path.insert(0, str(REPO_ROOT))
        from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter
        adapter = AlpacaPaperAdapter()
        acct = adapter.get_account_state()
        positions = adapter.list_positions()
        pos_list = []
        for p in positions:
            pos_list.append({
                "symbol": p.get("symbol", ""),
                "qty": p.get("qty", 0),
                "pnl": p.get("unrealized_pl", 0),
                "pnl_pct": p.get("unrealized_plpc", 0),
            })
        return {
            "equity": acct.get("equity"),
            "cash": acct.get("cash"),
            "buying_power": acct.get("buying_power"),
            "positions": pos_list,
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/control/gss-signal")
def gss_signal_summary():
    """Latest GSS signal for bot consumption."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.alpha.gss_execution_engine import GSSExecutionEngine
        gss = GSSExecutionEngine(REPO_ROOT)
        snapshot = {}
        for bridge_name in ("gcp_consciousness", "narrative_velocity", "market_microstructure", "options_greeks"):
            cache_dir = REPO_ROOT / "logs" / "bridge_cache" / bridge_name
            if cache_dir.exists():
                files = sorted(cache_dir.glob("*.json"), reverse=True)
                if files:
                    data = load_json(files[0])
                    if data:
                        snapshot[bridge_name] = data
        scorecards = load_scorecards(limit=1)
        scorecard = scorecards[0] if scorecards else {"mode": "NORMAL"}
        if snapshot:
            result = gss.analyze(snapshot, scorecard)
            return {
                "signal": result.get("gss_signal", "UNKNOWN"),
                "confidence": result.get("confidence", 0),
                "action": result.get("action", "HOLD"),
                "reason": result.get("reason", ""),
            }
    except Exception as e:
        return {"error": str(e)}
    return {"signal": "NO_DATA"}


# ---------------------------------------------------------------------------
# Dashboard Layout API — Bot-controllable widget layout
# ---------------------------------------------------------------------------

LAYOUT_PATH = REPO_ROOT / "config" / "dashboard_layout.json"
LAYOUT_BACKUP_DIR = REPO_ROOT / "config" / "dashboard_layout_backups"

class LayoutUpdateRequest(BaseModel):
    rows: List[Dict[str, Any]]
    updated_by: str = "api"


@app.get("/api/dashboard/layout")
def get_dashboard_layout():
    """Get current dashboard layout config."""
    if LAYOUT_PATH.exists():
        return load_json(LAYOUT_PATH)
    return {"error": "no layout config found"}


@app.put("/api/dashboard/layout")
async def set_dashboard_layout(request: Request):
    """Update dashboard layout. Bots can reorder, resize, show/hide widgets.
    Body: {"rows": [...], "updated_by": "bot_name"}"""
    try:
        body = await request.json()
        rows = body.get("rows")
        updated_by = body.get("updated_by", "api")

        if not rows or not isinstance(rows, list):
            return JSONResponse(status_code=400, content={"error": "rows must be a non-empty list"})

        # Validate structure
        valid_widget_ids = {
            "equity_curve", "portfolio", "execution_mode", "performance",
            "pnl_waterfall", "trade_analysis", "order_flow", "regime_gauge",
            "component_radar", "component_bars", "system_controls",
            "gss_signal_graph", "regime_timeline", "evidence_log",
            "politician_alpha", "alert_feed", "drawdown_chart",
            "consciousness", "order_success_rate", "sector_exposure",
            "graduation",
        }

        for row in rows:
            widgets = row.get("widgets", [])
            if not isinstance(widgets, list):
                return JSONResponse(status_code=400, content={"error": f"row {row.get('id', '?')} widgets must be a list"})
            total_cols = 0
            for w in widgets:
                if not w.get("id"):
                    return JSONResponse(status_code=400, content={"error": "each widget must have an 'id'"})
                if w["id"] not in valid_widget_ids:
                    return JSONResponse(status_code=400, content={"error": f"unknown widget id: {w['id']}"})
                cols = w.get("cols", 12)
                if not (1 <= cols <= 12):
                    return JSONResponse(status_code=400, content={"error": f"cols must be 1-12, got {cols}"})
                total_cols += cols
            if total_cols > 12:
                return JSONResponse(status_code=400, content={"error": f"row {row.get('id', '?')} total cols ({total_cols}) exceeds 12"})

        # Backup current layout
        if LAYOUT_PATH.exists():
            LAYOUT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup = LAYOUT_BACKUP_DIR / f"layout_{ts}.json"
            backup.write_text(LAYOUT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

        # Increment version
        current = load_json(LAYOUT_PATH) if LAYOUT_PATH.exists() else {}
        version = current.get("version", 0) + 1

        new_layout = {
            "version": version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
            "rows": rows,
        }
        LAYOUT_PATH.write_text(json.dumps(new_layout, indent=2), encoding="utf-8")

        return {"ok": True, "version": version, "updated_by": updated_by}
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.patch("/api/dashboard/layout/widget")
async def patch_widget(request: Request):
    """Update a single widget's properties (cols, visible, title).
    Body: {"widget_id": "equity_curve", "cols": 8, "visible": false, "updated_by": "bot"}"""
    try:
        body = await request.json()
        widget_id = body.get("widget_id")
        updated_by = body.get("updated_by", "api")

        if not widget_id:
            return JSONResponse(status_code=400, content={"error": "widget_id required"})

        layout = load_json(LAYOUT_PATH) if LAYOUT_PATH.exists() else {}
        if not layout.get("rows"):
            return JSONResponse(status_code=404, content={"error": "no layout config"})

        # Find and update widget
        found = False
        for row in layout["rows"]:
            for w in row.get("widgets", []):
                if w["id"] == widget_id:
                    if "cols" in body:
                        w["cols"] = max(1, min(12, int(body["cols"])))
                    if "visible" in body:
                        w["visible"] = bool(body["visible"])
                    if "title" in body:
                        w["title"] = str(body["title"])
                    found = True
                    break
            if found:
                break

        if not found:
            return JSONResponse(status_code=404, content={"error": f"widget '{widget_id}' not found"})

        # Backup + save
        LAYOUT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = LAYOUT_BACKUP_DIR / f"layout_{ts}.json"
        backup.write_text(LAYOUT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

        layout["version"] = layout.get("version", 0) + 1
        layout["updated_at"] = datetime.now(timezone.utc).isoformat()
        layout["updated_by"] = updated_by
        LAYOUT_PATH.write_text(json.dumps(layout, indent=2), encoding="utf-8")

        return {"ok": True, "widget_id": widget_id, "version": layout["version"]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/dashboard/layout/swap")
async def swap_widgets(request: Request):
    """Swap two widgets' positions. Body: {"widget_a": "equity_curve", "widget_b": "portfolio", "updated_by": "bot"}"""
    try:
        body = await request.json()
        wa_id = body.get("widget_a")
        wb_id = body.get("widget_b")
        updated_by = body.get("updated_by", "api")

        if not wa_id or not wb_id:
            return JSONResponse(status_code=400, content={"error": "widget_a and widget_b required"})

        layout = load_json(LAYOUT_PATH) if LAYOUT_PATH.exists() else {}
        if not layout.get("rows"):
            return JSONResponse(status_code=404, content={"error": "no layout config"})

        # Find both widgets
        wa_loc = wb_loc = None
        for ri, row in enumerate(layout["rows"]):
            for wi, w in enumerate(row.get("widgets", [])):
                if w["id"] == wa_id:
                    wa_loc = (ri, wi)
                if w["id"] == wb_id:
                    wb_loc = (ri, wi)

        if wa_loc is None:
            return JSONResponse(status_code=404, content={"error": f"widget '{wa_id}' not found"})
        if wb_loc is None:
            return JSONResponse(status_code=404, content={"error": f"widget '{wb_id}' not found"})

        # Swap
        rows = layout["rows"]
        wa = rows[wa_loc[0]]["widgets"][wa_loc[1]]
        wb = rows[wb_loc[0]]["widgets"][wb_loc[1]]
        rows[wa_loc[0]]["widgets"][wa_loc[1]] = wb
        rows[wb_loc[0]]["widgets"][wb_loc[1]] = wa

        # Backup + save
        LAYOUT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = LAYOUT_BACKUP_DIR / f"layout_{ts}.json"
        backup.write_text(LAYOUT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

        layout["version"] = layout.get("version", 0) + 1
        layout["updated_at"] = datetime.now(timezone.utc).isoformat()
        layout["updated_by"] = updated_by
        LAYOUT_PATH.write_text(json.dumps(layout, indent=2), encoding="utf-8")

        return {"ok": True, "swapped": [wa_id, wb_id], "version": layout["version"]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# Admin / Deploy API — Bot-triggered builds and service restarts
# ---------------------------------------------------------------------------

import subprocess
import shlex

DEPLOY_LOCK = asyncio.Lock()

@app.post("/api/admin/deploy")
async def admin_deploy(request: Request):
    """Rebuild frontend and restart dashboard. Body: {"action": "rebuild"|"restart"|"full", "requested_by": "bot"}
    - rebuild: npm run build in frontend dir
    - restart: restart dashboard systemd service
    - full: rebuild + restart
    """
    try:
        body = await request.json()
        action = body.get("action", "full")
        requested_by = body.get("requested_by", "api")

        if action not in ("rebuild", "restart", "full"):
            return JSONResponse(status_code=400, content={"error": "action must be rebuild, restart, or full"})

        if DEPLOY_LOCK.locked():
            return JSONResponse(status_code=409, content={"error": "deploy already in progress"})

        async with DEPLOY_LOCK:
            results = {"action": action, "requested_by": requested_by, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
            frontend_dir = REPO_ROOT / "dashboard" / "frontend"

            if action in ("rebuild", "full"):
                # Run npm build
                try:
                    proc = subprocess.run(
                        ["npm", "run", "build"],
                        cwd=str(frontend_dir),
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    results["build_exit_code"] = proc.returncode
                    results["build_stdout"] = proc.stdout[-500:] if proc.stdout else ""
                    results["build_stderr"] = proc.stderr[-500:] if proc.stderr else ""
                    if proc.returncode != 0:
                        results["error"] = "build failed"
                        return JSONResponse(status_code=500, content=results)
                except subprocess.TimeoutExpired:
                    results["error"] = "build timed out (120s)"
                    return JSONResponse(status_code=500, content=results)

            if action in ("restart", "full"):
                # Restart dashboard service
                try:
                    proc = subprocess.run(
                        ["sudo", "systemctl", "restart", "global-sentinel-dashboard"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    results["restart_exit_code"] = proc.returncode
                    results["restart_stderr"] = proc.stderr[-200:] if proc.stderr else ""
                except subprocess.TimeoutExpired:
                    results["restart_error"] = "restart timed out"

            results["ok"] = True
            return results
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/admin/service-status")
def admin_service_status():
    """Get systemd service status for dashboard and main sentinel."""
    services = {}
    for svc in ("global-sentinel-dashboard", "global-sentinel"):
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            services[svc] = proc.stdout.strip()
        except Exception:
            services[svc] = "unknown"
    return {"services": services, "timestamp_utc": datetime.now(timezone.utc).isoformat()}


@app.post("/api/admin/service")
async def admin_service_action(request: Request):
    """Restart or check a service. Body: {"service": "global-sentinel-dashboard"|"global-sentinel", "action": "restart"|"status", "requested_by": "bot"}"""
    try:
        body = await request.json()
        service = body.get("service", "global-sentinel-dashboard")
        action = body.get("action", "status")
        requested_by = body.get("requested_by", "api")

        allowed_services = ("global-sentinel-dashboard", "global-sentinel")
        if service not in allowed_services:
            return JSONResponse(status_code=400, content={"error": f"service must be one of {allowed_services}"})

        if action == "status":
            proc = subprocess.run(["systemctl", "status", service, "--no-pager"], capture_output=True, text=True, timeout=10)
            return {"service": service, "output": proc.stdout[-1000:], "exit_code": proc.returncode}

        if action == "restart":
            proc = subprocess.run(["sudo", "systemctl", "restart", service], capture_output=True, text=True, timeout=30)
            return {"ok": proc.returncode == 0, "service": service, "action": "restart", "requested_by": requested_by, "stderr": proc.stderr[-200:] if proc.stderr else ""}

        return JSONResponse(status_code=400, content={"error": "action must be restart or status"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# Serve frontend static files (production)
# ---------------------------------------------------------------------------

frontend_dist = Path(__file__).parent.parent / "frontend" / "out"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8501)

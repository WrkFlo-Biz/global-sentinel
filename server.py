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
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(os.getenv("GS_REPO_ROOT", "/opt/global-sentinel")).resolve()
API_KEY = os.getenv("GS_DASHBOARD_API_KEY", "")
ORCHESTRATOR_APPROVAL_COMMAND = (
    "wrkflo-orchestrator approve --kind gs.trade.execute_shadow "
    "--target global-sentinel/trade-ticket/<ticket_id>"
)
LEGACY_APPROVAL_ENDPOINT_MESSAGE = (
    "Legacy dashboard approval endpoint is disabled; prepare a scoped GS trade "
    "ticket and route approval through orchestrator tokens instead."
)

app = FastAPI(title="Global Sentinel Dashboard API", version="5.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8501"],
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
            return _fetch_alpaca_history(accounts[0], period, timeframe)
        except Exception as e:
            return {"error": str(e)}

    # Multi-account: return per-account histories
    results = {}
    for acct in accounts:
        try:
            results[acct["label"]] = _fetch_alpaca_history(acct, period, timeframe)
        except Exception as e:
            results[acct["label"]] = {"error": str(e)}

    # Also merge into a combined timeline for the primary history response
    # Use the first account with valid data as the base
    for label, hist in results.items():
        if "timestamp" in hist and "equity" in hist:
            return {**hist, "accounts": results}
    return {"accounts": results, "error": "No valid history from any account"}


@app.get("/api/portfolio")
def portfolio(account: str = Query("all")):
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

    for acct in accounts:
        try:
            data = _fetch_alpaca_account(acct)
            total_equity += data["equity"]
            total_cash += data["cash"]
            total_buying_power += data["buying_power"]
            total_portfolio_value += data["portfolio_value"]
            # Tag positions with account label
            for p in data["positions"]:
                p["account"] = data["label"]
            all_positions.extend(data["positions"])
            position_count_by_account[data["label"]] = len(data["positions"])
            account_details.append(data)
        except Exception as e:
            account_errors.append({"label": acct["label"], "error": str(e)})
            account_details.append({"label": acct["label"], "status": "error", "error": str(e), "positions": [], "position_count": 0})

    if not account_details:
        status = "error"
    elif account_errors and len(account_errors) == len(account_details):
        status = "error"
    elif account_errors:
        status = "partial"
    else:
        status = "ok"

    return {
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
            "account_count_success": len(account_details) - len(account_errors),
            "account_count_error": len(account_errors),
            "position_count_total": len(all_positions),
            "position_count_by_account": position_count_by_account,
        },
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


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
    """Legacy approval-file bridge is disabled in favor of orchestrator mediation."""
    return JSONResponse(
        status_code=410,
        content={
            "error": "legacy_approval_file_bridge_disabled",
            "message": LEGACY_APPROVAL_ENDPOINT_MESSAGE,
            "orchestrator_command": ORCHESTRATOR_APPROVAL_COMMAND,
        },
    )


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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Send initial state
        hb = load_json(REPO_ROOT / "logs" / "heartbeat.json")
        cards = load_scorecards(limit=1)
        await ws.send_json({
            "type": "init",
            "heartbeat": hb,
            "scorecard": cards[0] if cards else None,
            "controls": {
                "kill_switch": load_json(REPO_ROOT / "control" / "kill_switch.json"),
                "manual_veto": load_json(REPO_ROOT / "control" / "manual_veto.json"),
            },
            "execution_mode": get_execution_mode_data(),
        })
        # Keep alive — poll for changes every 10s
        last_cycle = hb.get("cycle", 0)
        while True:
            await asyncio.sleep(10)
            hb = load_json(REPO_ROOT / "logs" / "heartbeat.json")
            current_cycle = hb.get("cycle", 0)
            if current_cycle != last_cycle:
                last_cycle = current_cycle
                cards = load_scorecards(limit=1)
                await ws.send_json({
                    "type": "update",
                    "heartbeat": hb,
                    "scorecard": cards[0] if cards else None,
                    "controls": {
                        "kill_switch": load_json(REPO_ROOT / "control" / "kill_switch.json"),
                        "manual_veto": load_json(REPO_ROOT / "control" / "manual_veto.json"),
                    },
                    "execution_mode": get_execution_mode_data(),
                })
    except WebSocketDisconnect:
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

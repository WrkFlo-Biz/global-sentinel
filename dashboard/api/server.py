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


@app.get("/api/time_window")
def time_window():
    sc = load_scorecards(limit=1)
    if not sc:
        return {}
    return sc[0].get("time_window", {})


@app.get("/api/portfolio")
def portfolio():
    """Fetch Alpaca paper account positions via the adapter."""
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_SECRET_KEY")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

    if not api_key or not api_secret:
        return {"error": "Alpaca credentials not configured"}

    import urllib.request
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    def alpaca_get(path: str) -> Any:
        url = f"{base_url}{path}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        account = alpaca_get("/account")
        positions_raw = alpaca_get("/positions")
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
            "equity": float(account.get("equity", 0)),
            "cash": float(account.get("cash", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "portfolio_value": float(account.get("portfolio_value", 0)),
            "positions": positions,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


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
                })
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Serve frontend static files (production)
# ---------------------------------------------------------------------------

frontend_dist = Path(__file__).parent.parent / "frontend" / "out"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8501)

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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.control_state_snapshot import (
    read_control_state_snapshot,
    read_control_wrapper_snapshot,
)

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
APPROVAL_REQUIRED_ERROR = "orchestrator_approval_required"
CONTROL_SURFACE_APPROVAL_MESSAGE = (
    "This control-surface mutator is demoted. Route Tier-2 control changes "
    "through orchestrator approval tokens instead of writing local control "
    "files."
)
PENDING_ORDERS_DEMOTED_MESSAGE = (
    "Pending orders are no longer served from GS-local pending-order files. "
    "Route approval state through orchestrator-mediated guarded execution."
)
ALPACA_STOCK_STREAM_FEED = os.getenv("ALPACA_STOCK_STREAM_FEED", "iex").strip().lower() or "iex"
LIVE_EQUITY_SAMPLE_MIN_INTERVAL_SECONDS = 5.0
LIVE_EQUITY_SAMPLE_RETENTION_SECONDS = 86400.0
REST_QUOTE_REFRESH_INTERVAL_SECONDS = 15.0
REST_QUOTE_COOLDOWN_SECONDS = 5.0
MARKET_DATA_CONNECTION_LIMIT_COOLDOWN_SECONDS = 120.0
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"


dashboard_live_state_manager: Optional["DashboardLiveStateManager"] = None


def _approval_command(kind: str, target: str) -> str:
    return f"wrkflo-orchestrator approve --kind {kind} --target {target}"


def _approval_guidance_response(
    *,
    kind: str,
    target: str,
    requested_change: Dict[str, Any] | None = None,
) -> JSONResponse:
    content: Dict[str, Any] = {
        "ok": False,
        "status": "approval_required",
        "error": APPROVAL_REQUIRED_ERROR,
        "message": CONTROL_SURFACE_APPROVAL_MESSAGE,
        "kind": kind,
        "target": target,
        "orchestrator_command": _approval_command(kind, target),
    }
    if requested_change is not None:
        content["requested_change"] = requested_change
    return JSONResponse(status_code=410, content=content)


def _pending_orders_demoted_response() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "day_trade": None,
            "medium_long": None,
            "approval_required": True,
            "legacy_approval_file_bridge_disabled": True,
            "status": "approval_required",
            "message": PENDING_ORDERS_DEMOTED_MESSAGE,
            "orchestrator_command": ORCHESTRATOR_APPROVAL_COMMAND,
        },
    )


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    global dashboard_live_state_manager
    dashboard_live_state_manager = DashboardLiveStateManager()
    await dashboard_live_state_manager.start()
    try:
        yield
    finally:
        if dashboard_live_state_manager is not None:
            await dashboard_live_state_manager.stop()
            dashboard_live_state_manager = None


app = FastAPI(title="Global Sentinel Dashboard API", version="5.1", lifespan=app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _apply_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    """Require API key for /api/ endpoints when GS_DASHBOARD_API_KEY is set."""
    if API_KEY and request.url.path.startswith("/api/"):
        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if key != API_KEY:
            return _apply_no_cache_headers(JSONResponse(status_code=401, content={"error": "unauthorized"}))
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path in {"/warroom", "/warroom.html"}:
        _apply_no_cache_headers(response)
    return response


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


BRIDGE_PANEL_META: List[Dict[str, Any]] = [
    {"key": "market_microstructure", "label": "Market Data", "summary_key": "microstructure_symbols", "cache_dir": "market_microstructure"},
    {"key": "finnhub", "label": "Finnhub News", "summary_key": "finnhub_packet_count", "cache_dir": "finnhub_bridge"},
    {"key": "fred", "label": "FRED Macro", "cache_dir": "fred_bridge"},
    {"key": "gdelt", "label": "GDELT Geopolitics", "summary_key": "gdelt_event_count", "cache_dir": "gdelt", "empty_if_zero": True},
    {"key": "aviation_disruption", "label": "Aviation", "summary_key": "aviation_disruption_count", "cache_dir": "aviation_disruption_bridge", "empty_if_zero": True},
    {"key": "eia", "label": "EIA Energy", "cache_dir": "eia_bridge"},
    {"key": "gcp_consciousness", "label": "GCP Consciousness", "cache_dir": "gcp_consciousness"},
    {"key": "narrative_velocity", "label": "Narrative Velocity", "cache_dir": "narrative_velocity"},
    {"key": "options_greeks", "label": "Options Greeks", "summary_key": "put_call_ratio", "cache_dir": "options_greeks"},
    {"key": "politician_alpha", "label": "Politician Alpha", "cache_dir": "politician_alpha"},
    {"key": "fed_board", "label": "Fed Board", "cache_dir": "fed_board_bridge"},
    {"key": "treasury_ofac", "label": "Treasury OFAC", "cache_dir": "treasury_ofac_bridge"},
    {"key": "whitehouse_policy", "label": "White House Policy", "cache_dir": "whitehouse_policy_bridge"},
    {"key": "bls_releases", "label": "BLS Releases", "cache_dir": "bls_release_bridge"},
    {"key": "exa_search", "label": "Exa Search", "summary_key": "exa_packet_count", "cache_dir": "exa_search"},
]

BRIDGE_SNAPSHOT_LIVE_MAX_AGE_MIN = 20.0


def _is_bridge_snapshot_artifact(path: Path) -> bool:
    """Filter out housekeeping files so operator age reflects real payload snapshots."""
    name = path.name.lower()
    if name in {"seen_hashes.json", "seen_urls.json", "seen_ids.json"}:
        return False
    if name.endswith("_hash.txt"):
        return False
    return path.suffix.lower() == ".json"


def _bridge_cache_snapshot(cache_dir_name: Optional[str]) -> Dict[str, Any]:
    if not cache_dir_name:
        return {
            "exists": False,
            "file_count": 0,
            "json_file_count": 0,
            "hash_file_count": 0,
            "latest_file": None,
            "latest_age_min": None,
        }

    cache_dir = REPO_ROOT / "logs" / "bridge_cache" / cache_dir_name
    if not cache_dir.exists():
        return {
            "exists": False,
            "file_count": 0,
            "json_file_count": 0,
            "hash_file_count": 0,
            "latest_file": None,
            "latest_age_min": None,
        }

    files = sorted((p for p in cache_dir.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    json_files = [p for p in files if p.suffix.lower() == ".json"]
    hash_files = [p for p in files if p.name.endswith("_hash.txt")]
    snapshot_files = [p for p in files if _is_bridge_snapshot_artifact(p)]
    latest_file = snapshot_files[0] if snapshot_files else None
    latest_age_min = None
    if latest_file is not None:
        latest_age_min = round((time.time() - latest_file.stat().st_mtime) / 60.0, 1)

    return {
        "exists": True,
        "file_count": len(files),
        "json_file_count": len(json_files),
        "hash_file_count": len(hash_files),
        "latest_file": latest_file.name if latest_file is not None else None,
        "latest_age_min": latest_age_min,
    }


def _build_bridge_panel_status(card: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    freshness = card.get("data_freshness_status", {}) or {}
    summary = card.get("bridge_summary", {}) or {}
    bridges: Dict[str, Dict[str, Any]] = {}

    for meta in BRIDGE_PANEL_META:
        key = str(meta["key"])
        cache = _bridge_cache_snapshot(meta.get("cache_dir"))
        fresh = freshness.get(key)
        count = summary.get(meta["summary_key"]) if meta.get("summary_key") else None
        latest_age_min = cache.get("latest_age_min")
        snapshot_recent = (
            cache.get("latest_file") is not None
            and isinstance(latest_age_min, (int, float))
            and float(latest_age_min) <= BRIDGE_SNAPSHOT_LIVE_MAX_AGE_MIN
        )

        status = "unknown"
        display_status = "N/A"
        detail = "No recent bridge status available."

        if fresh is False:
            if meta.get("empty_if_zero") and isinstance(count, (int, float)) and float(count) == 0.0 and snapshot_recent:
                status = "empty"
                display_status = "EMPTY"
                detail = "Source is polling live, but the latest payload was empty."
            elif snapshot_recent:
                status = "source_live"
                display_status = "SOURCE LIVE"
                detail = "Recent source snapshot exists, but the latest scorecard marked integration stale."
            else:
                status = "stale"
                display_status = "STALE"
                detail = "Latest scorecard marked this bridge stale."
        elif fresh is True:
            if meta.get("empty_if_zero") and isinstance(count, (int, float)) and float(count) == 0.0:
                status = "empty"
                display_status = "EMPTY"
                detail = "Fresh poll, but the latest payload was empty."
            elif cache["file_count"] == 0:
                status = "no_snapshot"
                display_status = "NO SNAPSHOT"
                detail = "Fresh flag is set, but no rotating snapshot files were found."
            else:
                status = "live"
                display_status = "LIVE"
                detail = "Fresh payload with recent bridge cache activity."
        elif snapshot_recent:
            if meta.get("empty_if_zero") and isinstance(count, (int, float)) and float(count) == 0.0:
                status = "empty"
                display_status = "EMPTY"
                detail = "Source is polling live, but the latest payload was empty."
            else:
                status = "source_live"
                display_status = "SOURCE LIVE"
                detail = "Recent source snapshot exists, but no scorecard freshness bit was available."
        elif cache.get("hash_file_count"):
            status = "snapshot_only"
            display_status = "HASH ONLY"
            detail = "Page-change monitor activity is present, but no recent payload snapshot was found."
        elif cache["file_count"] == 0 and cache["exists"]:
            status = "no_snapshot"
            display_status = "NO SNAPSHOT"
            detail = "Bridge cache directory exists, but no rotating snapshot files were found."
        elif cache["exists"]:
            status = "stale"
            display_status = "STALE"
            detail = "Bridge cache exists, but the latest usable snapshot is stale."

        bridges[key] = {
            "label": meta["label"],
            "status": status,
            "display_status": display_status,
            "fresh": fresh,
            "snapshot_recent": snapshot_recent,
            "count": count,
            "detail": detail,
            **cache,
        }

    return bridges


ALPACA_ACCOUNT_CACHE_TTL_SECONDS = 15.0
ALPACA_ORDERS_CACHE_TTL_SECONDS = 15.0
ALPACA_HISTORY_CACHE_TTL_SECONDS = {
    "1H": 10.0,
    "1D": 30.0,
}
BROKER_ACCOUNT_CACHE_TTL_SECONDS = 15.0
BROKER_DISCOVERY_CACHE_TTL_SECONDS = 300.0
TASTYTRADE_SESSION_CACHE_TTL_SECONDS = 3600.0
PORTFOLIO_SNAPSHOT_FILE = Path(os.getenv("BROKER_SNAPSHOT_FILE", str(REPO_ROOT / "data" / "broker_snapshots" / "portfolio.json")))
TASTYTRADE_SNAPSHOT_FILE = Path(os.getenv("TASTYTRADE_SNAPSHOT_FILE", str(REPO_ROOT / "data" / "broker_snapshots" / "tastytrade.json")))
TASTYTRADE_SESSION_CACHE_FILE = Path(os.getenv("TASTYTRADE_SESSION_CACHE_FILE", str(REPO_ROOT / ".tastytrade_session.json")))
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


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


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _dedupe_strings(values: List[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _env_csv_values(*names: str) -> List[str]:
    _load_env()
    values: List[str] = []
    for name in names:
        raw = os.getenv(name, "")
        if not raw:
            continue
        values.extend(part.strip() for part in raw.split(","))
    return _dedupe_strings(values)


def _env_sequential_values(prefix: str) -> List[str]:
    _load_env()
    values: List[tuple[int, str]] = []
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix):]
        if not suffix.isdigit():
            continue
        cleaned = str(raw or "").strip()
        if not cleaned:
            continue
        values.append((int(suffix), cleaned))
    return _dedupe_strings([value for _, value in sorted(values)])


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
    verify_ssl: bool = True,
) -> Any:
    import ssl
    import urllib.request

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = dict(headers or {})
    req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    context = None if verify_ssl else ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        raw = resp.read().decode("utf-8")
    if not raw:
        return {}
    return json.loads(raw)


def _payload_object(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _payload_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("items", "positions", "accounts", "values"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        for key in ("items", "positions", "accounts", "values"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _account_public_metadata(acct: Dict[str, Any]) -> Dict[str, Any]:
    broker = str(acct.get("broker") or "alpaca")
    label = str(acct.get("label") or broker)
    account_number = str(acct.get("account_number") or "")
    display_label = str(acct.get("display_label") or label.replace("_", " "))
    is_live = bool(acct.get("is_live"))
    return {
        "label": label,
        "broker": broker,
        "display_label": display_label,
        "account_number": account_number,
        "is_live": is_live,
    }


def _normalize_ibkr_base_url(base_url: Optional[str]) -> str:
    normalized = (base_url or os.getenv("IBKR_CLIENT_PORTAL_BASE_URL") or os.getenv("IBKR_BASE_URL") or "https://localhost:5000/v1/api").rstrip("/")
    if normalized.endswith("/v1/api"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/api"
    if normalized.endswith("/api"):
        return normalized
    return f"{normalized}/v1/api"


def _load_tastytrade_snapshot() -> Dict[str, Any]:
    for snapshot_file in (PORTFOLIO_SNAPSHOT_FILE, TASTYTRADE_SNAPSHOT_FILE):
        if not snapshot_file.exists():
            continue
        snapshot = load_json(snapshot_file)
        if isinstance(snapshot, dict):
            return snapshot
    return {}


def _load_tastytrade_session_cache() -> Dict[str, Any]:
    if not TASTYTRADE_SESSION_CACHE_FILE.exists():
        return {}
    session = load_json(TASTYTRADE_SESSION_CACHE_FILE)
    return session if isinstance(session, dict) else {}


def _snapshot_accounts(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    accounts = snapshot.get("accounts")
    if isinstance(accounts, list):
        return [item for item in accounts if isinstance(item, dict)]
    return []


def _find_tastytrade_snapshot_account(account_number: str, label: str) -> Optional[Dict[str, Any]]:
    snapshot = _load_tastytrade_snapshot()
    if not snapshot:
        return None

    for item in _snapshot_accounts(snapshot):
        item_account_number = str(_first_present(item.get("account_number"), item.get("account-number")) or "").strip()
        item_label = str(item.get("label") or "").strip()
        if account_number and item_account_number == account_number:
            payload = copy.deepcopy(item)
            payload["_snapshot_generated_at_utc"] = snapshot.get("generated_at_utc")
            payload["_snapshot_source"] = snapshot.get("source")
            return payload
        if label and item_label == label:
            payload = copy.deepcopy(item)
            payload["_snapshot_generated_at_utc"] = snapshot.get("generated_at_utc")
            payload["_snapshot_source"] = snapshot.get("source")
            return payload

    return None


def _snapshot_account_fallback(metadata: Dict[str, Any], account_number: str, exc: Exception) -> Dict[str, Any]:
    snapshot = _find_tastytrade_snapshot_account(account_number, str(metadata.get("label") or ""))
    if snapshot is None:
        raise exc

    snapshot = copy.deepcopy(snapshot)
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        positions = []
    positions = [item for item in positions if isinstance(item, dict)]
    timestamp_utc = str(
        _first_present(
            snapshot.get("timestamp_utc"),
            snapshot.get("source_timestamp_utc"),
            snapshot.get("_snapshot_generated_at_utc"),
        )
        or _utc_now_iso()
    )
    equity = _safe_float(snapshot.get("equity"))
    cash = _safe_float(snapshot.get("cash"))
    buying_power = _safe_float(snapshot.get("buying_power"))
    portfolio_value = _safe_float(snapshot.get("portfolio_value") or equity or sum(position.get("market_value", 0.0) for position in positions))
    return {
        **metadata,
        **{k: v for k, v in snapshot.items() if k not in {"positions", "timestamp_utc", "source_timestamp_utc", "_snapshot_generated_at_utc", "_snapshot_source"}},
        "account_number": account_number or str(snapshot.get("account_number") or ""),
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "portfolio_value": portfolio_value,
        "positions": positions,
        "position_count": len(positions),
        "status": "ok",
        "timestamp_utc": timestamp_utc,
        "source_timestamp_utc": snapshot.get("timestamp_utc") or snapshot.get("source_timestamp_utc") or snapshot.get("_snapshot_generated_at_utc") or timestamp_utc,
        "fetched_at_utc": snapshot.get("fetched_at_utc") or timestamp_utc,
        "cache_age_ms": snapshot.get("cache_age_ms", 0.0),
        "cache_status": "snapshot",
        "data_source": "snapshot",
        "snapshot_error": str(exc),
    }


def _get_tastytrade_session(
    *,
    base_url: str,
    username: Optional[str],
    password: Optional[str],
    force_refresh: bool = False,
) -> str:
    _load_env()
    env_token = str(os.getenv("TASTYTRADE_SESSION_TOKEN", "")).strip()
    if env_token and not force_refresh:
        return env_token

    cache_key = f"tastytrade_session:{base_url}:{username or 'default'}"
    if not force_refresh:
        cached = _cache_lookup(cache_key, ttl_seconds=TASTYTRADE_SESSION_CACHE_TTL_SECONDS)
        if cached:
            token = str((cached["value"] or {}).get("session_token") or "")
            if token:
                return token
        session_cache = _load_tastytrade_session_cache()
        cached_token = str(
            _first_present(
                session_cache.get("session_token"),
                session_cache.get("session-token"),
            )
            or ""
        ).strip()
        if cached_token:
            _cache_store(cache_key, {"session_token": cached_token})
            return cached_token

    if not username or not password:
        raise RuntimeError("TastyTrade credentials not configured")

    payload = _json_request(
        f"{base_url}/sessions",
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        body={"login": username, "password": password, "remember-me": True},
        timeout=20,
    )
    data = _payload_object(payload)
    session_token = str(
        _first_present(
            data.get("session-token"),
            data.get("session_token"),
            data.get("sessionToken"),
            payload.get("session-token") if isinstance(payload, dict) else None,
            payload.get("session_token") if isinstance(payload, dict) else None,
        )
        or ""
    ).strip()
    if not session_token:
        raise RuntimeError("TastyTrade session token missing from response")
    _cache_store(cache_key, {"session_token": session_token})
    return session_token


def _tastytrade_request(
    acct: Dict[str, Any],
    path: str,
    *,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    force_refresh: bool = False,
) -> Any:
    import urllib.error

    token = _get_tastytrade_session(
        base_url=acct["base_url"],
        username=acct.get("username"),
        password=acct.get("password"),
        force_refresh=force_refresh,
    )
    headers = {
        "Accept": "application/json",
        "Authorization": token,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    url = f"{acct['base_url']}{path}"
    try:
        return _json_request(url, method=method, headers=headers, body=body, timeout=20)
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and not force_refresh:
            return _tastytrade_request(acct, path, method=method, body=body, force_refresh=True)
        raise


def _discover_tastytrade_account_numbers(base_url: str, username: Optional[str], password: Optional[str]) -> List[str]:
    cache_key = f"tastytrade_accounts:{base_url}:{username or 'default'}"
    cached = _cache_lookup(cache_key, ttl_seconds=BROKER_DISCOVERY_CACHE_TTL_SECONDS)
    if cached:
        return _dedupe_strings(list((cached["value"] or {}).get("account_numbers") or []))

    payload = _tastytrade_request(
        {
            "base_url": base_url,
            "username": username,
            "password": password,
        },
        "/customers/me/accounts",
    )
    account_numbers: List[str] = []
    for item in _payload_items(payload):
        account = item.get("account") if isinstance(item.get("account"), dict) else item
        account_numbers.append(
            str(
                _first_present(
                    account.get("account-number") if isinstance(account, dict) else None,
                    account.get("account_number") if isinstance(account, dict) else None,
                    item.get("account-number"),
                    item.get("account_number"),
                    item.get("accountNumber"),
                    item.get("account-number-short"),
                )
                or ""
            ).strip()
        )
    account_numbers = _dedupe_strings(account_numbers)
    _cache_store(cache_key, {"account_numbers": account_numbers})
    return account_numbers


def _parse_tastytrade_positions(payload: Any) -> List[Dict[str, Any]]:
    positions: List[Dict[str, Any]] = []
    for item in _payload_items(payload):
        quantity = abs(_safe_float(_first_present(item.get("quantity"), item.get("signed-quantity"), item.get("signed_quantity")), 0.0))
        quantity_direction = str(_first_present(item.get("quantity-direction"), item.get("quantity_direction"), item.get("side"), item.get("direction"), "long")).lower()
        side = "short" if "short" in quantity_direction else "long"
        multiplier = _safe_float(_first_present(item.get("multiplier"), item.get("contract-multiplier")), 1.0)
        if multiplier <= 0:
            multiplier = 1.0
        current_price = _safe_float(_first_present(item.get("mark-price"), item.get("mark_price"), item.get("mark"), item.get("last-price"), item.get("last_price")))
        avg_entry_price = _safe_float(_first_present(item.get("average-open-price"), item.get("average_open_price"), item.get("average-price"), item.get("average_price")))
        market_value = _safe_float(_first_present(item.get("market-value"), item.get("market_value")))
        if not market_value and current_price and quantity:
            market_value = current_price * quantity * multiplier
        unrealized_pl = _safe_float(_first_present(item.get("unrealized-day-gain"), item.get("unrealized_day_gain"), item.get("unrealized-gain"), item.get("unrealized_gain")))
        if not unrealized_pl and current_price and avg_entry_price and quantity:
            signed_qty = -quantity if side == "short" else quantity
            unrealized_pl = (current_price - avg_entry_price) * signed_qty * multiplier
        cost_basis = avg_entry_price * quantity * multiplier
        unrealized_plpc = (unrealized_pl / cost_basis) if cost_basis else 0.0
        symbol = str(
            _first_present(
                item.get("symbol"),
                item.get("underlying-symbol"),
                item.get("underlying_symbol"),
                ((item.get("instrument") or {}).get("symbol") if isinstance(item.get("instrument"), dict) else None),
            )
            or ""
        ).strip()
        asset_class = str(
            _first_present(
                item.get("instrument-type"),
                item.get("instrument_type"),
                ((item.get("instrument") or {}).get("instrument-type") if isinstance(item.get("instrument"), dict) else None),
                "equity",
            )
            or "equity"
        ).lower()
        positions.append({
            "symbol": symbol,
            "qty": quantity,
            "side": side,
            "asset_class": asset_class,
            "avg_entry_price": avg_entry_price,
            "current_price": current_price,
            "unrealized_pl": unrealized_pl,
            "unrealized_plpc": unrealized_plpc,
            "market_value": market_value,
            "pricing_source": "tastytrade_rest",
            "pricing_timestamp_utc": _utc_now_iso(),
        })
    return positions


def _fetch_tastytrade_account(acct: Dict[str, Any]) -> Dict[str, Any]:
    account_number = str(acct.get("account_number") or "").strip()
    if not account_number:
        raise RuntimeError("TastyTrade account number missing")

    metadata = _account_public_metadata(acct)
    try:
        balances_payload = _tastytrade_request(acct, f"/accounts/{account_number}/balances")
        balances = _payload_object(balances_payload)
        positions = _parse_tastytrade_positions(_tastytrade_request(acct, f"/accounts/{account_number}/positions"))

        equity = _safe_float(
            _first_present(
                balances.get("net-liquidating-value"),
                balances.get("net_liquidating_value"),
                balances.get("liquidation-value"),
                balances.get("liquidation_value"),
                balances.get("equity"),
            )
        )
        cash = _safe_float(
            _first_present(
                balances.get("cash-balance"),
                balances.get("cash_balance"),
                balances.get("cash-available-to-withdraw"),
                balances.get("cash_available_to_withdraw"),
                balances.get("settled-cash"),
                balances.get("settled_cash"),
            )
        )
        buying_power = _safe_float(
            _first_present(
                balances.get("derivative-buying-power"),
                balances.get("derivative_buying_power"),
                balances.get("equity-buying-power"),
                balances.get("equity_buying_power"),
                balances.get("buying-power"),
                balances.get("buying_power"),
                balances.get("day-trade-excess"),
                balances.get("day_trade_excess"),
                equity,
            )
        )
        return {
            **metadata,
            "account_number": account_number,
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "portfolio_value": equity or _safe_float(sum(position.get("market_value", 0.0) for position in positions)),
            "positions": positions,
            "position_count": len(positions),
            "status": "ok",
            "timestamp_utc": _utc_now_iso(),
            "cache_status": "miss",
        }
    except Exception as exc:
        return _snapshot_account_fallback(metadata, "", exc)


def _discover_ibkr_account_numbers(base_url: str) -> List[str]:
    cache_key = f"ibkr_accounts:{base_url}"
    cached = _cache_lookup(cache_key, ttl_seconds=BROKER_DISCOVERY_CACHE_TTL_SECONDS)
    if cached:
        return _dedupe_strings(list((cached["value"] or {}).get("account_numbers") or []))

    payload = _json_request(
        f"{base_url}/portfolio/accounts",
        headers={"Accept": "application/json"},
        verify_ssl=False,
        timeout=20,
    )
    account_numbers: List[str] = []
    for item in _payload_items(payload):
        account_numbers.append(
            str(
                _first_present(
                    item.get("accountId"),
                    item.get("account_id"),
                    item.get("id"),
                    item.get("accountIdKey"),
                )
                or ""
            ).strip()
        )
    account_numbers = _dedupe_strings(account_numbers)
    _cache_store(cache_key, {"account_numbers": account_numbers})
    return account_numbers


def _ibkr_summary_map(payload: Any) -> Dict[str, float]:
    summary: Dict[str, float] = {}
    for item in _payload_items(payload):
        tag = str(_first_present(item.get("tag"), item.get("key"), item.get("name")) or "").strip().lower()
        if not tag:
            continue
        summary[tag] = _safe_float(_first_present(item.get("amount"), item.get("value"), item.get("amt")))
    if summary:
        return summary

    if isinstance(payload, dict):
        candidate = payload.get("summary")
        if isinstance(candidate, list):
            for item in _payload_items(candidate):
                tag = str(_first_present(item.get("tag"), item.get("key"), item.get("name")) or "").strip().lower()
                if tag:
                    summary[tag] = _safe_float(_first_present(item.get("amount"), item.get("value"), item.get("amt")))
        else:
            for key, value in payload.items():
                normalized = str(key).strip().lower()
                if isinstance(value, dict):
                    summary[normalized] = _safe_float(_first_present(value.get("amount"), value.get("value"), value.get("amt")))
                elif isinstance(value, (int, float, str)):
                    summary[normalized] = _safe_float(value)
    return summary


def _ibkr_cash_from_ledger(payload: Any) -> float:
    if not isinstance(payload, dict):
        return 0.0
    preferred_keys = ["BASE", "USD"]
    for key in preferred_keys + [k for k in payload.keys() if k not in preferred_keys]:
        bucket = payload.get(key)
        if not isinstance(bucket, dict):
            continue
        cash = _safe_float(
            _first_present(
                bucket.get("cashbalance"),
                bucket.get("cashBalance"),
                bucket.get("totalcashvalue"),
                bucket.get("settledcash"),
            )
        )
        if cash:
            return cash
    return 0.0


def _parse_ibkr_positions(payload: Any) -> List[Dict[str, Any]]:
    positions: List[Dict[str, Any]] = []
    for item in _payload_items(payload):
        signed_qty = _safe_float(_first_present(item.get("position"), item.get("qty"), item.get("quantity")))
        quantity = abs(signed_qty)
        side = "short" if signed_qty < 0 else "long"
        current_price = _safe_float(_first_present(item.get("mktPrice"), item.get("marketPrice"), item.get("price")))
        market_value = _safe_float(_first_present(item.get("mktValue"), item.get("marketValue"), item.get("market_value")))
        if not market_value and current_price and quantity:
            market_value = current_price * quantity
        avg_entry_price = _safe_float(_first_present(item.get("avgPrice"), item.get("avgCost"), item.get("avg_cost")))
        unrealized_pl = _safe_float(_first_present(item.get("unrealizedPnl"), item.get("unrealized_pl")))
        cost_basis = avg_entry_price * quantity
        unrealized_plpc = (unrealized_pl / cost_basis) if cost_basis else 0.0
        symbol = str(
            _first_present(
                item.get("ticker"),
                item.get("symbol"),
                item.get("contractDesc"),
                item.get("description"),
            )
            or ""
        ).strip()
        positions.append({
            "symbol": symbol,
            "qty": quantity,
            "side": side,
            "asset_class": str(_first_present(item.get("assetClass"), item.get("asset_class"), item.get("type"), "equity") or "equity").lower(),
            "avg_entry_price": avg_entry_price,
            "current_price": current_price,
            "unrealized_pl": unrealized_pl,
            "unrealized_plpc": unrealized_plpc,
            "market_value": market_value,
            "pricing_source": "ibkr_rest",
            "pricing_timestamp_utc": _utc_now_iso(),
        })
    return positions


def _fetch_ibkr_account(acct: Dict[str, Any]) -> Dict[str, Any]:
    account_number = str(acct.get("account_number") or "").strip()
    if not account_number:
        raise RuntimeError("IBKR account number missing")

    metadata = _account_public_metadata(acct)
    base_url = _normalize_ibkr_base_url(acct.get("base_url"))
    try:
        summary_map = _ibkr_summary_map(
            _json_request(
                f"{base_url}/portfolio/{account_number}/summary",
                headers={"Accept": "application/json"},
                verify_ssl=False,
                timeout=20,
            )
        )
        ledger_payload = _json_request(
            f"{base_url}/portfolio/{account_number}/ledger",
            headers={"Accept": "application/json"},
            verify_ssl=False,
            timeout=20,
        )
        positions = _parse_ibkr_positions(
            _json_request(
                f"{base_url}/portfolio/{account_number}/positions/0",
                headers={"Accept": "application/json"},
                verify_ssl=False,
                timeout=20,
            )
        )

        equity = _safe_float(
            _first_present(
                summary_map.get("netliquidation"),
                summary_map.get("net_liquidation"),
                summary_map.get("equitywithloanvalue"),
                summary_map.get("equity"),
            )
        )
        cash = _safe_float(
            _first_present(
                _ibkr_cash_from_ledger(ledger_payload),
                summary_map.get("totalcashvalue"),
                summary_map.get("cashbalance"),
            )
        )
        buying_power = _safe_float(
            _first_present(
                summary_map.get("buyingpower"),
                summary_map.get("availablefunds"),
                summary_map.get("availablefund"),
                summary_map.get("excessliquidity"),
                equity,
            )
        )
        return {
            **metadata,
            "account_number": account_number,
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "portfolio_value": equity or _safe_float(sum(position.get("market_value", 0.0) for position in positions)),
            "positions": positions,
            "position_count": len(positions),
            "status": "ok",
            "timestamp_utc": _utc_now_iso(),
            "cache_status": "miss",
        }
    except Exception as exc:
        return _snapshot_account_fallback(metadata, "", exc)


def _get_tastytrade_accounts() -> List[Dict[str, Any]]:
    _load_env()
    username = str(_first_present(os.getenv("TASTYTRADE_USERNAME"), os.getenv("TASTYTRADE_LOGIN")) or "").strip()
    password = str(os.getenv("TASTYTRADE_PASSWORD", "")).strip()
    if not username or not password:
        return []

    base_url = str(os.getenv("TASTYTRADE_BASE_URL", "https://api.tastyworks.com")).rstrip("/")
    account_numbers = _dedupe_strings(
        _env_csv_values("TASTYTRADE_ACCOUNT_NUMBERS", "TASTYTRADE_ACCOUNTS")
        + [
            str(_first_present(os.getenv("TASTYTRADE_CASH_ACCOUNT"), os.getenv("TASTYTRADE_CASH_ACCOUNT_NUMBER")) or "").strip(),
            str(_first_present(os.getenv("TASTYTRADE_MARGIN_ACCOUNT"), os.getenv("TASTYTRADE_MARGIN_ACCOUNT_NUMBER")) or "").strip(),
        ]
        + _env_sequential_values("TASTYTRADE_ACCOUNT_")
    )
    if not account_numbers:
        try:
            account_numbers = _discover_tastytrade_account_numbers(base_url, username, password)
        except Exception:
            account_numbers = []

    return [
        {
            "label": f"tastytrade_{account_number}",
            "broker": "tastytrade",
            "display_label": f"TastyTrade {account_number}",
            "account_number": account_number,
            "username": username,
            "password": password,
            "base_url": base_url,
            "is_live": True,
        }
        for account_number in account_numbers
    ]


def _get_ibkr_accounts() -> List[Dict[str, Any]]:
    _load_env()
    base_url = _normalize_ibkr_base_url(None)
    account_numbers = _dedupe_strings(
        _env_csv_values("IBKR_ACCOUNT_NUMBERS", "IBKR_ACCOUNT_IDS", "IBKR_ACCOUNTS")
        + _env_sequential_values("IBKR_ACCOUNT_")
    )
    if not account_numbers:
        try:
            account_numbers = _discover_ibkr_account_numbers(base_url)
        except Exception:
            account_numbers = []

    return [
        {
            "label": f"ibkr_{account_number}",
            "broker": "ibkr",
            "display_label": f"IBKR {account_number}",
            "account_number": account_number,
            "base_url": base_url,
            "is_live": True,
        }
        for account_number in account_numbers
    ]


def _get_portfolio_accounts() -> List[Dict[str, Any]]:
    accounts: List[Dict[str, Any]] = []
    seen_labels: set[str] = set()
    for source in (_get_alpaca_accounts(), _get_tastytrade_accounts(), _get_ibkr_accounts()):
        for acct in source:
            label = str(acct.get("label") or "")
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            accounts.append(acct)
    return accounts


def _fetch_portfolio_account(acct: Dict[str, Any]) -> Dict[str, Any]:
    broker = str(acct.get("broker") or "alpaca")
    if broker == "alpaca":
        return _fetch_alpaca_account(acct)
    if broker == "tastytrade":
        return _fetch_tastytrade_account(acct)
    if broker == "ibkr":
        return _fetch_ibkr_account(acct)
    raise RuntimeError(f"Unsupported broker '{broker}'")


def _get_cached_portfolio_account(acct: Dict[str, Any]) -> Dict[str, Any]:
    broker = str(acct.get("broker") or "alpaca")
    if broker == "alpaca":
        return _get_cached_alpaca_account(acct)

    cache_key = f"{broker}_account:{acct['label']}"
    cached = _cache_lookup(cache_key, ttl_seconds=BROKER_ACCOUNT_CACHE_TTL_SECONDS)
    if cached:
        payload = cached["value"]
        payload["source_timestamp_utc"] = payload.get("source_timestamp_utc") or payload.get("timestamp_utc") or cached["fetched_at_utc"]
        payload["fetched_at_utc"] = cached["fetched_at_utc"]
        payload["cache_age_ms"] = cached["cache_age_ms"]
        payload["cache_status"] = cached["cache_status"]
        return payload

    payload = _fetch_portfolio_account(acct)
    fetched_at_utc = _cache_store(cache_key, payload)
    payload = copy.deepcopy(payload)
    payload["source_timestamp_utc"] = payload.get("timestamp_utc") or fetched_at_utc
    payload["fetched_at_utc"] = fetched_at_utc
    payload["cache_age_ms"] = 0.0
    payload["cache_status"] = "miss"
    return payload


def _freshness_metadata(
    source_timestamp_utc: Optional[str],
    *,
    stale_after_seconds: int = 300,
    degraded_after_seconds: int = 60,
) -> Dict[str, Any]:
    parsed = _parse_iso_datetime(source_timestamp_utc)
    if parsed is None:
        return {
            "source_timestamp_utc": source_timestamp_utc,
            "source_age_seconds": None,
            "source_freshness": "unknown",
            "source_stale": None,
        }

    age_seconds = max(0, int((_utc_now() - parsed).total_seconds()))
    freshness = "live"
    if age_seconds >= stale_after_seconds:
        freshness = "stale"
    elif age_seconds >= degraded_after_seconds:
        freshness = "degraded"

    return {
        "source_timestamp_utc": parsed.isoformat(),
        "source_age_seconds": age_seconds,
        "source_freshness": freshness,
        "source_stale": freshness == "stale",
    }


def _portfolio_pricing_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    positions = list(payload.get("positions") or [])
    pricing_times = [
        parsed
        for parsed in (_parse_iso_datetime(position.get("pricing_timestamp_utc")) for position in positions)
        if parsed is not None
    ]
    latest_pricing = max(pricing_times).isoformat() if pricing_times else None
    oldest_pricing = min(pricing_times).isoformat() if pricing_times else None
    latest_age_seconds = _freshness_metadata(latest_pricing, stale_after_seconds=900, degraded_after_seconds=300)
    oldest_age_seconds = _freshness_metadata(oldest_pricing, stale_after_seconds=900, degraded_after_seconds=300)

    stream_health = payload.get("stream_health") or {}
    error_accounts = []
    degraded_accounts = []
    for label, details in stream_health.items():
        if not isinstance(details, dict) or label.startswith("_"):
            continue
        market_status = str(details.get("market_data_status") or "")
        if market_status == "error":
            error_accounts.append(label)
        elif market_status and market_status not in {"listening", "event", "connected"}:
            degraded_accounts.append(label)

    delayed_positions = 0
    stale_positions = 0
    for position in positions:
        meta = _freshness_metadata(
            position.get("pricing_timestamp_utc"),
            stale_after_seconds=900,
            degraded_after_seconds=300,
        )
        if meta["source_freshness"] == "stale":
            stale_positions += 1
        elif meta["source_freshness"] == "degraded":
            delayed_positions += 1

    market_data_health = "live"
    if error_accounts:
        market_data_health = "degraded"
    elif degraded_accounts:
        market_data_health = "delayed"
    if positions and stale_positions == len(positions):
        market_data_health = "stale"

    return {
        "priced_position_count": len(pricing_times),
        "position_count": len(positions),
        "latest_pricing_timestamp_utc": latest_pricing,
        "latest_pricing_age_seconds": latest_age_seconds["source_age_seconds"],
        "oldest_pricing_timestamp_utc": oldest_pricing,
        "oldest_pricing_age_seconds": oldest_age_seconds["source_age_seconds"],
        "delayed_position_count": delayed_positions,
        "stale_position_count": stale_positions,
        "market_data_health": market_data_health,
        "stream_error_accounts": error_accounts,
        "stream_degraded_accounts": degraded_accounts,
    }


def _is_stock_market_data_symbol(symbol: str) -> bool:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return False
    crypto_suffixes = ("USD", "/USD", "USDT", "/USDT")
    if any(normalized.endswith(suffix) for suffix in crypto_suffixes):
        return False
    return True


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


def _normalize_alpaca_base_url(base_url: Optional[str], default: str) -> str:
    normalized = str(base_url or default).strip().rstrip("/")
    if not normalized:
        normalized = default
    if normalized.endswith("/v2"):
        return normalized
    return f"{normalized}/v2"


def _get_alpaca_accounts() -> List[Dict[str, Any]]:
    """Return credential dicts for all configured Alpaca paper accounts."""
    _load_env()
    accounts = []

    # Primary account (day_trade)
    key1 = os.getenv("ALPACA_API_KEY")
    sec1 = os.getenv("ALPACA_SECRET_KEY")
    url1 = _normalize_alpaca_base_url(
        os.getenv("ALPACA_BASE_URL"),
        "https://paper-api.alpaca.markets/v2",
    )
    if key1 and sec1:
        accounts.append({
            "label": "day_trade",
            "broker": "alpaca",
            "display_label": "Alpaca Day Trade",
            "api_key": key1,
            "api_secret": sec1,
            "base_url": url1,
            "is_live": "paper" not in url1,
        })

    # Also check DAYTRADE-specific keys (may be same as primary)
    key_dt = os.getenv("ALPACA_API_KEY_DAYTRADE")
    sec_dt = os.getenv("ALPACA_SECRET_KEY_DAYTRADE")
    if key_dt and sec_dt and key_dt != key1:
        accounts.append({
            "label": "day_trade_2",
            "broker": "alpaca",
            "display_label": "Alpaca Day Trade 2",
            "api_key": key_dt,
            "api_secret": sec_dt,
            "base_url": _normalize_alpaca_base_url(
                os.getenv("ALPACA_BASE_URL_DAYTRADE"),
                "https://paper-api.alpaca.markets/v2",
            ),
            "is_live": False,
        })

    # Medium/Long account
    key_ml = os.getenv("ALPACA_API_KEY_MEDLONG")
    sec_ml = os.getenv("ALPACA_SECRET_KEY_MEDLONG")
    if key_ml and sec_ml:
        accounts.append({
            "label": "medium_long",
            "broker": "alpaca",
            "display_label": "Alpaca Med/Long",
            "api_key": key_ml,
            "api_secret": sec_ml,
            "base_url": _normalize_alpaca_base_url(
                os.getenv("ALPACA_BASE_URL_MEDLONG"),
                "https://paper-api.alpaca.markets/v2",
            ),
            "is_live": False,
        })

    # Live trading account ($125)
    key_live = os.getenv("ALPACA_API_KEY_LIVE")
    sec_live = os.getenv("ALPACA_SECRET_KEY_LIVE")
    if key_live and sec_live:
        accounts.append({
            "label": "live",
            "broker": "alpaca",
            "display_label": "Alpaca Live",
            "api_key": key_live,
            "api_secret": sec_live,
            "base_url": _normalize_alpaca_base_url(
                os.getenv("ALPACA_BASE_URL_LIVE"),
                "https://api.alpaca.markets/v2",
            ),
            "is_live": True,
        })

    return accounts


def _fetch_alpaca_account(acct: Dict[str, str]) -> Dict[str, Any]:
    """Fetch account info + positions for a single Alpaca account."""
    import urllib.request
    import urllib.error
    metadata = _account_public_metadata(acct)
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

    try:
        account = _get("/account")
        positions_raw = _get("/positions")
        positions = []
        for p in positions_raw:
            positions.append({
                "symbol": p.get("symbol"),
                "qty": float(p.get("qty", 0)),
                "side": p.get("side", "long"),
                "asset_class": p.get("asset_class"),
                "avg_entry_price": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
                "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
                "market_value": float(p.get("market_value", 0)),
                "pricing_source": "alpaca_rest_position",
                "pricing_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "broker": metadata["broker"],
            })
        return {
            **metadata,
            "account_number": account.get("account_number", ""),
            "equity": float(account.get("equity", 0)),
            "cash": float(account.get("cash", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "portfolio_value": float(account.get("portfolio_value", 0)),
            "positions": positions,
            "position_count": len(positions),
            "status": "ok",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "cache_status": "miss",
        }
    except Exception as exc:
        return _snapshot_account_fallback(metadata, "", exc)


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


def _get_cached_alpaca_orders(acct: Dict[str, str], limit: int = 100, status: str = "all") -> List[Dict[str, Any]]:
    cache_key = f"alpaca_orders:{acct['label']}:{limit}:{status}"
    cached = _cache_lookup(cache_key, ttl_seconds=ALPACA_ORDERS_CACHE_TTL_SECONDS)
    if cached:
        return copy.deepcopy(cached["value"].get("rows") or [])

    payload = _fetch_alpaca_orders(acct, limit=limit, status=status)
    _cache_store(cache_key, {"rows": payload})
    return copy.deepcopy(payload)


def _slice_portfolio_payload(payload: Dict[str, Any], account: str) -> Dict[str, Any]:
    if account == "all" or not isinstance(payload, dict):
        sliced = copy.deepcopy(payload)
        if isinstance(sliced, dict):
            sliced["pricing_summary"] = _portfolio_pricing_summary(sliced)
        return sliced

    sliced = copy.deepcopy(payload)
    accounts = [acct for acct in (sliced.get("accounts") or []) if acct.get("label") == account]
    if not accounts:
        return {"error": f"Account '{account}' not found"}

    selected = accounts[0]
    positions = list(selected.get("positions") or [])
    for position in positions:
        position["account"] = account
        position["account_label"] = account
        position["broker"] = selected.get("broker")

    sliced["accounts"] = accounts
    sliced["account_errors"] = [
        err for err in (sliced.get("account_errors") or [])
        if err.get("label") == account
    ]
    sliced["equity"] = _safe_float(selected.get("equity"))
    sliced["cash"] = _safe_float(selected.get("cash"))
    sliced["buying_power"] = _safe_float(selected.get("buying_power"))
    sliced["portfolio_value"] = _safe_float(selected.get("portfolio_value"))
    sliced["positions"] = positions
    sliced["position_count_total"] = len(positions)
    sliced["position_count_by_account"] = {account: len(positions)}
    sliced["account_count"] = 1
    consistency = sliced.get("consistency") or {}
    sliced["consistency"] = {
        **consistency,
        "account_count_requested": 1,
        "account_count_success": 1 if selected.get("status") != "error" else 0,
        "account_count_error": len(sliced["account_errors"]),
        "position_count_total": len(positions),
        "position_count_total_from_accounts": len(positions),
        "position_count_by_account": {account: len(positions)},
        "requested_accounts": [account],
        "accounts_match_requested": True,
        "positions_match_total": True,
        "has_account_errors": bool(sliced["account_errors"]),
    }
    sliced["status"] = "error" if selected.get("status") == "error" else ("partial" if sliced["account_errors"] else "ok")
    sliced["pricing_summary"] = _portfolio_pricing_summary(sliced)
    return sliced


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


def _controls_wrapper_payload() -> Dict[str, Dict[str, Any]]:
    return read_control_wrapper_snapshot(REPO_ROOT)


def _canonical_control_status_payload(
    *,
    heartbeat_payload: Optional[Dict[str, Any]] = None,
    scorecard_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    hb = heartbeat_payload if isinstance(heartbeat_payload, dict) else load_json(
        REPO_ROOT / "logs" / "heartbeat.json"
    )
    sc = scorecard_payload if isinstance(scorecard_payload, dict) else {}
    if not sc:
        cards = load_scorecards(limit=1)
        sc = cards[0] if cards else {}

    control_snapshot = read_control_state_snapshot(REPO_ROOT)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": sc.get("mode", hb.get("mode", "UNKNOWN") if hb else "UNKNOWN"),
        "cycle": sc.get("cycle", hb.get("cycle", 0) if hb else 0),
        "regime_p": sc.get("regime_shift_probability", 0),
        "confidence": sc.get("confidence", 0),
        "kill_switch": control_snapshot["kill_switch"],
        "manual_veto": control_snapshot["manual_veto"],
        "shadow_eligible": sc.get("shadow_execution_eligible", False),
        "fallback_mode": sc.get("fallback_mode_status", False),
        "execution_mode": get_execution_mode_data(),
        "evidence": sc.get("evidence", [])[:5],
    }


@app.get("/api/controls")
def controls():
    return _controls_wrapper_payload()


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
    """Operator-facing bridge health from latest scorecard plus cache activity."""
    sc = load_scorecards(limit=1)
    if not sc:
        return {"bridges": {}, "data_freshness": {}, "bridge_summary": {}}
    card = sc[0]
    return {
        "bridges": _build_bridge_panel_status(card),
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
        result = engine.analyze(current, previous_mode=prev_mode, microstructure=micro)
        if isinstance(result, dict):
            result.update(_freshness_metadata(current.get("timestamp_utc"), stale_after_seconds=900, degraded_after_seconds=300))
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/performance")
def performance():
    """Shadow trading performance summary."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.execution.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker(REPO_ROOT)
        summary = tracker.generate_summary()
        if not isinstance(summary, dict):
            return summary

        snapshot = summary.get("open_positions_snapshot") or {}
        source_ts = snapshot.get("source_timestamp_utc") or snapshot.get("timestamp_utc") or summary.get("timestamp_utc")
        summary.update(_freshness_metadata(source_ts, stale_after_seconds=900, degraded_after_seconds=300))
        summary["open_positions_snapshot_timestamp_utc"] = source_ts
        summary["open_positions_snapshot_freshness"] = summary.get("source_freshness")
        summary["open_positions_snapshot_age_seconds"] = summary.get("source_age_seconds")
        return summary
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
                rows = _get_cached_alpaca_orders(acct, limit=broker_limit, status="all")
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


@app.get("/api/quantum")
def quantum_summary():
    """Quantum vs classical comparison data for dashboard panel."""
    from datetime import datetime, timezone

    research_dir = REPO_ROOT / "reports" / "research"
    comparisons_dir = research_dir / "comparisons"
    batches_dir = research_dir / "overnight_batches"

    # Latest comparison artifact
    latest_comparison = None
    comparison_count = 0
    if comparisons_dir.exists():
        comp_files = sorted(comparisons_dir.glob("comparison_*.json"))
        comparison_count = len(comp_files)
        if comp_files:
            latest_comparison = load_json(comp_files[-1])

    # Latest overnight batch
    overnight_batch = None
    if batches_dir.exists():
        batch_files = sorted(batches_dir.glob("overnight_batch_*.json"))
        if batch_files:
            overnight_batch = load_json(batch_files[-1])

    # Build summary from comparison artifacts (compute winner from objective values)
    summary = None
    if comparisons_dir.exists():
        try:
            comp_data = []
            for cf in sorted(comparisons_dir.glob("comparison_*.json")):
                try:
                    comp_data.append(json.loads(cf.read_text(encoding="utf-8")))
                except Exception:
                    continue
            if comp_data:
                q_wins, c_wins, ties = 0, 0, 0
                q_objs, c_objs = [], []
                for cd in comp_data:
                    results = cd.get("results", {})
                    obj_vals = {}
                    for bk, bv in results.items():
                        if isinstance(bv, dict) and bv.get("status") == "success":
                            ov = bv.get("objective_value")
                            if ov is not None:
                                obj_vals[bk] = ov
                    if not obj_vals:
                        continue
                    q_bk = {k: v for k, v in obj_vals.items() if k.startswith("q") or k.startswith("pennylane")}
                    c_bk = {k: v for k, v in obj_vals.items() if k.startswith("classical")}
                    best_q = max(q_bk.values()) if q_bk else None
                    best_c = max(c_bk.values()) if c_bk else None
                    if q_bk:
                        q_objs.append(best_q)
                    if c_bk:
                        c_objs.append(best_c)
                    if best_q is not None and best_c is not None:
                        if best_q > best_c:
                            q_wins += 1
                        elif best_c > best_q:
                            c_wins += 1
                        else:
                            ties += 1
                total = q_wins + c_wins + ties
                summary = {
                    "schema_version": "research_quantum_summary.v2",
                    "evaluation_count": total,
                    "quantum_win_rate": q_wins / max(total, 1),
                    "classical_win_rate": c_wins / max(total, 1),
                    "tie_rate": ties / max(total, 1),
                    "avg_quantum_overlap_score": sum(q_objs) / max(len(q_objs), 1) if q_objs else 0.0,
                    "avg_classical_overlap_score": sum(c_objs) / max(len(c_objs), 1) if c_objs else 0.0,
                }
        except Exception:
            pass

    # Quantum stage from latest scorecard
    scorecard_quantum = None
    cards = load_scorecards(limit=1)
    if cards:
        gov = cards[-1].get("v4_governance", {})
        if gov:
            scorecard_quantum = {
                "quantum_stage": gov.get("quantum_stage", "shadow"),
                "quantum_influence_cap": gov.get("quantum_influence_cap", 0.0),
            }

    latest_artifact_timestamp = None
    for candidate in (
        (latest_comparison or {}).get("timestamp_utc"),
        (overnight_batch or {}).get("timestamp_utc"),
    ):
        parsed = _parse_iso_datetime(candidate)
        if parsed is None:
            continue
        if latest_artifact_timestamp is None or parsed > latest_artifact_timestamp:
            latest_artifact_timestamp = parsed

    payload = {
        "schema_version": "quantum_dashboard.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "latest_comparison": latest_comparison,
        "overnight_batch": overnight_batch,
        "summary": summary,
        "comparison_count": comparison_count,
        "scorecard_quantum": scorecard_quantum,
    }
    payload.update(
        _freshness_metadata(
            latest_artifact_timestamp.isoformat() if latest_artifact_timestamp else None,
            stale_after_seconds=3600,
            degraded_after_seconds=900,
        )
    )
    payload["latest_artifact_timestamp_utc"] = latest_artifact_timestamp.isoformat() if latest_artifact_timestamp else None
    return payload


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
    if (
        dashboard_live_state_manager is not None
        and account == "all"
        and period == "1D"
        and timeframe == "1H"
    ):
        latest = dashboard_live_state_manager.get_latest_portfolio_history_intraday()
        if latest is not None:
            return latest
    return _build_portfolio_history_payload(period=period, timeframe=timeframe, account=account)


@app.get("/api/pnl-history")
def pnl_history(account: str = Query("all")):
    """Return intraday live equity samples for sparkline P&L charts.
    These are collected every ~5s by the live state manager and retained for 24h."""
    if dashboard_live_state_manager is not None:
        samples = dashboard_live_state_manager.get_live_equity_samples(account)
    else:
        samples = []
    return {"account": account, "samples": samples}


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
            if dashboard_live_state_manager is not None:
                hist = dashboard_live_state_manager.augment_history_with_live_samples(hist, accounts[0]["label"])
                hist["stream_health"] = dashboard_live_state_manager._stream_status_payload()
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
        merged = dashboard_live_state_manager.augment_history_with_live_samples(merged, account)
        merged["stream_health"] = dashboard_live_state_manager._stream_status_payload()
    return merged


@app.get("/api/portfolio")
def portfolio(account: str = Query("all")):
    """Fetch unified broker account positions and balances."""
    if dashboard_live_state_manager is not None:
        latest = dashboard_live_state_manager.get_latest_portfolio()
        if latest is not None:
            return _slice_portfolio_payload(latest, account)
    return _build_portfolio_payload(account=account)


def _build_portfolio_payload(account: str = "all") -> Dict[str, Any]:
    """Fetch broker account positions and balances across all configured brokers."""
    accounts = _get_portfolio_accounts()
    if not accounts:
        return {"error": "No broker accounts configured"}

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
            data = _get_cached_portfolio_account(acct)
            total_equity += data["equity"]
            total_cash += data["cash"]
            total_buying_power += data["buying_power"]
            total_portfolio_value += data["portfolio_value"]
            # Tag positions with account label
            for p in data["positions"]:
                p["account"] = data["label"]
                p["account_label"] = data["label"]
                p["broker"] = data.get("broker")
            all_positions.extend(data["positions"])
            position_count_by_account[data["label"]] = data.get("position_count", len(data["positions"]))
            account_details.append(data)
        except Exception as e:
            position_count_by_account[acct["label"]] = 0
            account_errors.append({"label": acct["label"], "error": str(e)})
            metadata = _account_public_metadata(acct)
            account_details.append({
                **metadata,
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
        payload = dashboard_live_state_manager.apply_live_market_prices(payload)
        payload["stream_health"] = dashboard_live_state_manager._stream_status_payload()
        payload["last_quote_refresh"] = dashboard_live_state_manager._rest_quote_refresh_utc
    payload["pricing_summary"] = _portfolio_pricing_summary(payload)
    return payload


# ---------------------------------------------------------------------------
# Candlestick Bars API
# ---------------------------------------------------------------------------

CRYPTO_SUFFIXES = ("USD", "USDT", "USDC", "EUR", "GBP", "JPY", "BTC", "ETH")

def _is_crypto_symbol(symbol: str) -> bool:
    """Detect crypto symbols like BTCUSD, BTC/USD, ETHUSD, SOLUSD, etc."""
    s = symbol.upper().replace("/", "")
    crypto_bases = {
        "BTC", "ETH", "SOL", "DOGE", "SHIB", "AVAX", "DOT", "LINK",
        "MATIC", "UNI", "AAVE", "ADA", "XRP", "LTC", "BCH", "ALGO",
        "ATOM", "FTM", "NEAR", "APE", "ARB", "OP", "MKR", "CRV",
        "PEPE", "BONK", "WIF", "RENDER", "FET", "GRT", "INJ", "TIA",
        "SUI", "SEI", "JUP", "PYTH", "WLD", "ONDO", "ENA", "PENDLE",
    }
    for base in crypto_bases:
        for suffix in CRYPTO_SUFFIXES:
            if s == base + suffix:
                return True
    if "/" in symbol:
        return True
    return False

def _to_crypto_api_symbol(symbol: str) -> str:
    """Convert BTCUSD -> BTC/USD for Alpaca crypto API."""
    if "/" in symbol:
        return symbol.upper()
    s = symbol.upper()
    for suffix in CRYPTO_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            return f"{s[:-len(suffix)]}/{suffix}"
    return symbol

def _yahoo_bars(symbol: str, yf_interval: str, yf_range: str) -> list:
    """Fetch OHLCV bars from Yahoo Finance (real-time, no subscription needed).

    Returns list of dicts with keys: t, o, h, l, c, v  (matching Alpaca format).
    """
    import urllib.request
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={yf_interval}&range={yf_range}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode())

    result = data.get("chart", {}).get("result", [])
    if not result:
        return []

    r = result[0]
    timestamps = r.get("timestamp", [])
    q = r.get("indicators", {}).get("quote", [{}])[0]
    opens = q.get("open", [])
    highs = q.get("high", [])
    lows = q.get("low", [])
    closes = q.get("close", [])
    volumes = q.get("volume", [])

    bars = []
    for i in range(len(timestamps)):
        o = opens[i] if i < len(opens) and opens[i] is not None else 0
        h = highs[i] if i < len(highs) and highs[i] is not None else 0
        lo = lows[i] if i < len(lows) and lows[i] is not None else 0
        c = closes[i] if i < len(closes) and closes[i] is not None else 0
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
        if c == 0 and o == 0:
            continue  # skip empty candles
        t_iso = datetime.fromtimestamp(timestamps[i], tz=timezone.utc).isoformat()
        bars.append({"t": t_iso, "o": o, "h": h, "l": lo, "c": c, "v": v})

    return bars


# Map Alpaca-style timeframes to Yahoo Finance interval + range
_TF_TO_YF = {
    "1Min":   ("1m",  "1d"),
    "5Min":   ("5m",  "5d"),
    "15Min":  ("15m", "5d"),
    "1Hour":  ("1h",  "1mo"),
    "1Day":   ("1d",  "6mo"),
    "1Week":  ("1wk", "2y"),
}


@app.get("/api/bars/{symbol}")
def stock_bars(
    symbol: str,
    timeframe: str = Query("5Min"),
    start: str = Query(""),
    limit: int = Query(200),
):
    """Fetch OHLCV bars — Yahoo Finance for stocks (real-time), Alpaca for crypto."""
    import urllib.request
    import urllib.error
    _load_env()

    is_crypto = _is_crypto_symbol(symbol)

    # --- Stocks: use Yahoo Finance (real-time, free) ---
    if not is_crypto:
        yf_map = _TF_TO_YF.get(timeframe, ("5m", "5d"))
        yf_interval, yf_range = yf_map
        try:
            bars = _yahoo_bars(symbol, yf_interval, yf_range)
            if limit and len(bars) > limit:
                bars = bars[-limit:]
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "bars": bars,
                "count": len(bars),
                "source": "yahoo",
                "timestamp_utc": _utc_now_iso(),
            }
        except Exception as yf_exc:
            # Fall back to Alpaca IEX if Yahoo fails
            pass

    # --- Crypto or Yahoo fallback: use Alpaca ---
    api_key = os.getenv("ALPACA_API_KEY_LIVE") or os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_SECRET_KEY_LIVE") or os.getenv("ALPACA_SECRET_KEY", "")

    if not api_key:
        return {"error": "No API key configured"}

    if not start:
        if "Min" in timeframe or "Hour" in timeframe:
            lookback = timedelta(days=3) if is_crypto else timedelta(days=5)
            start_dt = (datetime.now(timezone.utc) - lookback).strftime("%Y-%m-%d")
            start = f"{start_dt}T00:00:00Z"
        else:
            six_mo_ago = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
            start = f"{six_mo_ago}T00:00:00Z"

    if is_crypto:
        crypto_sym = _to_crypto_api_symbol(symbol)
        params = {
            "symbols": crypto_sym,
            "timeframe": timeframe,
            "limit": str(limit),
            "sort": "asc",
            "start": start,
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{ALPACA_DATA_BASE_URL}/v1beta3/crypto/us/bars?{qs}"
    else:
        params = {"timeframe": timeframe, "limit": str(limit), "sort": "asc", "feed": "iex", "start": start}
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{ALPACA_DATA_BASE_URL}/v2/stocks/{symbol}/bars?{qs}"

    try:
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            if is_crypto:
                bars_dict = data.get("bars") or {}
                bars = []
                for sym_key, sym_bars in bars_dict.items():
                    bars = sym_bars
                    break
            else:
                bars = data.get("bars") or []
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "bars": bars,
                "count": len(bars),
                "source": "alpaca",
                "timestamp_utc": _utc_now_iso(),
            }
    except Exception as exc:
        return {"error": str(exc), "symbol": symbol}


@app.get("/api/accounts")
def list_accounts():
    """List all configured broker accounts with basic info."""
    accounts = _get_portfolio_accounts()
    result = []
    for acct in accounts:
        metadata = _account_public_metadata(acct)
        result.append({
            **metadata,
            "base_url": acct.get("base_url"),
        })
    return {"accounts": result, "count": len(result)}


# ---------------------------------------------------------------------------
# Order Management
# ---------------------------------------------------------------------------

def _find_alpaca_account(label: str) -> Optional[Dict[str, Any]]:
    """Find an Alpaca account by label. Returns None if not found."""
    accounts = _get_alpaca_accounts()
    for acct in accounts:
        if acct["label"] == label:
            return acct
    return None


def _alpaca_request(acct: Dict[str, Any], path: str, method: str = "GET",
                    body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make an authenticated request to the Alpaca API for the given account."""
    import urllib.request
    import urllib.error

    headers = {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["api_secret"],
    }
    url = f"{acct['base_url']}{path}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, headers=headers, data=data, method=method)

    # Rate limiter for this account's API key
    try:
        from src.utils.rate_limiter import get_limiter
        limiter = get_limiter(acct["api_key"], max_rpm=180)
        limiter.acquire(timeout=30.0)
    except (ImportError, Exception):
        pass

    with urllib.request.urlopen(req, timeout=15) as resp:
        resp_body = resp.read().decode("utf-8")
        if resp_body:
            return json.loads(resp_body)
        return {"status": "ok"}


@app.post("/api/orders")
async def submit_order(request: Request):
    """Submit an order to a specific Alpaca account.

    Body: {"account": "live"|"day_trade"|"medium_long", "symbol": str,
           "side": "buy"|"sell", "type": "market"|"limit",
           "time_in_force": "day"|"gtc", "notional": float (optional),
           "qty": float (optional), "limit_price": float (optional)}
    """
    import urllib.error
    try:
        body = await request.json()
        account_label = body.get("account")
        if not account_label:
            return JSONResponse(status_code=400, content={"error": "account is required"})

        acct = _find_alpaca_account(account_label)
        if not acct:
            return JSONResponse(status_code=404, content={"error": f"account '{account_label}' not found"})

        # Validate required fields
        symbol = body.get("symbol")
        side = body.get("side")
        order_type = body.get("type", "market")
        tif = body.get("time_in_force", "day")

        if not symbol or not side:
            return JSONResponse(status_code=400, content={"error": "symbol and side are required"})
        if side not in ("buy", "sell"):
            return JSONResponse(status_code=400, content={"error": "side must be 'buy' or 'sell'"})
        if order_type not in ("market", "limit"):
            return JSONResponse(status_code=400, content={"error": "type must be 'market' or 'limit'"})
        if tif not in ("day", "gtc"):
            return JSONResponse(status_code=400, content={"error": "time_in_force must be 'day' or 'gtc'"})

        notional = body.get("notional")
        qty = body.get("qty")
        limit_price = body.get("limit_price")

        if not notional and not qty:
            return JSONResponse(status_code=400, content={"error": "either notional or qty is required"})
        if order_type == "limit" and not limit_price:
            return JSONResponse(status_code=400, content={"error": "limit_price is required for limit orders"})

        # Build the Alpaca order payload
        order_payload: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": order_type,
            "time_in_force": tif,
        }
        if notional is not None:
            order_payload["notional"] = float(notional)
        if qty is not None:
            order_payload["qty"] = str(float(qty))
        if limit_price is not None:
            order_payload["limit_price"] = str(float(limit_price))

        result = _alpaca_request(acct, "/orders", method="POST", body=order_payload)
        result["account_label"] = account_label
        return result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        try:
            error_body = json.loads(error_body)
        except Exception:
            pass
        return JSONResponse(status_code=e.code, content={"error": error_body, "account": body.get("account", "")})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/orders")
def list_orders(
    account: str = Query(default="all"),
    status: str = Query(default="all"),
    limit: int = Query(default=50),
):
    """List orders for an account. If account='all', merge from all accounts."""
    import urllib.error
    try:
        if account == "all":
            accounts = _get_alpaca_accounts()
        else:
            acct = _find_alpaca_account(account)
            if not acct:
                return JSONResponse(status_code=404, content={"error": f"account '{account}' not found"})
            accounts = [acct]

        all_orders = []
        for acct in accounts:
            try:
                path = f"/orders?status={status}&limit={limit}"
                orders = _alpaca_request(acct, path, method="GET")
                if isinstance(orders, list):
                    for order in orders:
                        order["account_label"] = acct["label"]
                    all_orders.extend(orders)
                elif isinstance(orders, dict) and "status" not in orders:
                    # Single order or unexpected shape
                    orders["account_label"] = acct["label"]
                    all_orders.append(orders)
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue

        # Sort by created_at descending
        all_orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
        return {"orders": all_orders[:limit], "count": len(all_orders[:limit])}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: str, account: str = Query(...)):
    """Cancel an order by ID. Query param 'account' is required."""
    import urllib.error
    try:
        acct = _find_alpaca_account(account)
        if not acct:
            return JSONResponse(status_code=404, content={"error": f"account '{account}' not found"})

        _alpaca_request(acct, f"/orders/{order_id}", method="DELETE")
        return {"status": "cancelled", "order_id": order_id, "account": account}

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        try:
            error_body = json.loads(error_body)
        except Exception:
            pass
        return JSONResponse(status_code=e.code, content={"error": error_body, "order_id": order_id, "account": account})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/positions/{symbol}/close")
def close_position(symbol: str, account: str = Query(...), qty: Optional[float] = Query(default=None)):
    """Close a position (full or partial). Alpaca uses DELETE on positions endpoint."""
    import urllib.error
    try:
        acct = _find_alpaca_account(account)
        if not acct:
            return JSONResponse(status_code=404, content={"error": f"account '{account}' not found"})

        path = f"/positions/{symbol.upper()}"
        if qty is not None:
            path += f"?qty={qty}"

        _alpaca_request(acct, path, method="DELETE")
        return {"status": "closed", "symbol": symbol.upper(), "account": account,
                "qty": qty if qty is not None else "all"}

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        try:
            error_body = json.loads(error_body)
        except Exception:
            pass
        return JSONResponse(status_code=e.code, content={"error": error_body, "symbol": symbol, "account": account})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


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
        body = await request.json()
        strategy = body.get("strategy")
        mode = body.get("mode")

        if strategy not in ("day_trade", "medium_long"):
            return JSONResponse(status_code=400, content={"error": "invalid strategy"})
        if mode not in ("auto", "manual"):
            return JSONResponse(status_code=400, content={"error": "invalid mode"})

        return _approval_guidance_response(
            kind="gs.control.execution_mode.set",
            target=f"global-sentinel/control/execution-mode/{strategy}/{mode}",
            requested_change={"strategy": strategy, "mode": mode},
        )
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
    """Legacy pending-order file bridge is demoted in favor of orchestrator state."""
    return _pending_orders_demoted_response()


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


def _alpaca_market_data_stream_url() -> str:
    return f"wss://stream.data.alpaca.markets/v2/{ALPACA_STOCK_STREAM_FEED}"


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


def _fetch_rest_snapshots(symbols: List[str], api_key: str, api_secret: str) -> Dict[str, Dict[str, Any]]:
    """Fetch latest stock snapshots via Alpaca REST data API (batch, up to 1000 symbols).
    Returns {symbol: {trade_price, trade_timestamp_utc, bid_price, ask_price, quote_timestamp_utc}}."""
    import urllib.request
    import urllib.parse

    if not symbols:
        return {}

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    try:
        from src.utils.rate_limiter import get_limiter
        limiter = get_limiter(f"data:{api_key}", max_rpm=180)
    except ImportError:
        limiter = None

    result: Dict[str, Dict[str, Any]] = {}
    # Alpaca snapshots endpoint accepts up to ~200 symbols per request reliably
    batch_size = 200
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        params = urllib.parse.urlencode({
            "symbols": ",".join(batch),
            "feed": ALPACA_STOCK_STREAM_FEED,
        })
        url = f"{ALPACA_DATA_BASE_URL}/v2/stocks/snapshots?{params}"
        try:
            if limiter:
                limiter.acquire(timeout=10.0)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for sym, snap in data.items():
                entry: Dict[str, Any] = {}
                latest_trade = snap.get("latestTrade") or {}
                if latest_trade.get("p"):
                    entry["trade_price"] = _safe_float(latest_trade["p"])
                    entry["trade_timestamp_utc"] = str(latest_trade.get("t") or _utc_now_iso())
                latest_quote = snap.get("latestQuote") or {}
                if latest_quote.get("bp"):
                    entry["bid_price"] = _safe_float(latest_quote["bp"])
                if latest_quote.get("ap"):
                    entry["ask_price"] = _safe_float(latest_quote["ap"])
                if latest_quote.get("t"):
                    entry["quote_timestamp_utc"] = str(latest_quote["t"])
                if entry:
                    entry["source"] = "rest_snapshot"
                    result[sym.upper()] = entry
        except Exception:
            # Silently skip batch failures — next cycle will retry
            continue
    return result


class DashboardLiveStateManager:
    def __init__(self):
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[Any]] = []
        self._latest_portfolio: Optional[Dict[str, Any]] = None
        self._latest_portfolio_history_intraday: Optional[Dict[str, Any]] = None
        self._latest_portfolio_signature: Optional[str] = None
        self._latest_portfolio_history_signature: Optional[str] = None
        self._stream_status: Dict[str, Dict[str, Any]] = {}
        self._held_symbols_by_account: Dict[str, set[str]] = {}
        self._latest_market_data: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._shared_market_data: Dict[str, Dict[str, Any]] = {}
        self._live_equity_samples: Dict[str, List[Dict[str, Any]]] = {}
        self._last_rest_quote_refresh: float = 0.0  # monotonic
        self._rest_quote_refresh_utc: Optional[str] = None

    def _stream_status_payload(self) -> Dict[str, Any]:
        per_account = {
            label: {
                "status": details.get("status"),
                "trade_updates_status": details.get("trade_updates_status"),
                "market_data_status": details.get("market_data_status"),
                "last_event_utc": details.get("last_event_utc"),
                "last_quote_utc": details.get("last_quote_utc"),
                "last_trade_utc": details.get("last_trade_utc"),
                "last_error": details.get("last_error"),
                "reconnect_count": details.get("reconnect_count", 0),
                "subscribed_symbols": sorted(details.get("subscribed_symbols", [])),
            }
            for label, details in self._stream_status.items()
        }
        per_account["_rest_quote_refresh"] = {
            "last_refresh_utc": self._rest_quote_refresh_utc,
            "interval_seconds": REST_QUOTE_REFRESH_INTERVAL_SECONDS,
            "symbols_in_cache": len(self._shared_market_data),
        }
        return per_account

    def get_latest_portfolio(self) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._latest_portfolio) if self._latest_portfolio else None

    def get_latest_portfolio_history_intraday(self) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._latest_portfolio_history_intraday) if self._latest_portfolio_history_intraday else None

    def get_live_equity_samples(self, account: str = "all") -> List[Dict[str, Any]]:
        """Return the in-memory live equity samples for sparkline P&L charts."""
        key = account if account != "all" else "all"
        samples = self._live_equity_samples.get(key) or []
        return copy.deepcopy(samples)

    def apply_live_market_prices(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload or payload.get("error"):
            return payload

        live_timestamps: List[datetime] = []
        total_equity = 0.0
        total_portfolio_value = 0.0
        total_cash = 0.0
        total_buying_power = 0.0
        all_positions: List[Dict[str, Any]] = []

        for account in payload.get("accounts") or []:
            if account.get("status") == "error":
                continue
            label = str(account.get("label") or "")
            total_cash += _safe_float(account.get("cash"))
            total_buying_power += _safe_float(account.get("buying_power"))
            live_market_value = 0.0
            saw_live_price = False

            for position in account.get("positions") or []:
                position["account"] = label
                position["account_label"] = label
                qty = abs(_safe_float(position.get("qty")))
                if qty <= 0:
                    all_positions.append(position)
                    continue
                side = str(position.get("side") or "long").lower()
                mark_price, mark_source, mark_timestamp = self._mark_price_for_position(label, position)
                if mark_price is None or mark_price <= 0:
                    mark_price = _safe_float(position.get("current_price"))
                    mark_source = str(position.get("pricing_source") or "alpaca_rest")
                    mark_timestamp = position.get("pricing_timestamp_utc") or position.get("timestamp_utc")
                else:
                    saw_live_price = True

                avg_entry = _safe_float(position.get("avg_entry_price"))
                signed_qty = -qty if side == "short" else qty
                # Options have a 100x multiplier (1 contract = 100 shares)
                asset_class = str(position.get("asset_class") or "").lower()
                multiplier = 100.0 if "option" in asset_class else 1.0
                market_value = signed_qty * mark_price * multiplier
                unrealized_pl = ((avg_entry - mark_price) if side == "short" else (mark_price - avg_entry)) * qty * multiplier
                cost_basis = avg_entry * qty * multiplier
                unrealized_plpc = (unrealized_pl / cost_basis) if cost_basis else 0.0

                position["current_price"] = mark_price
                position["market_value"] = market_value
                position["unrealized_pl"] = unrealized_pl
                position["unrealized_plpc"] = unrealized_plpc
                position["pricing_source"] = mark_source
                if mark_timestamp:
                    position["pricing_timestamp_utc"] = mark_timestamp
                    parsed = _parse_iso_datetime(mark_timestamp)
                    if parsed is not None:
                        live_timestamps.append(parsed)
                live_market_value += market_value
                all_positions.append(position)

            account["position_count"] = len(account.get("positions") or [])
            account["portfolio_value"] = _safe_float(account.get("cash")) + live_market_value
            account["equity"] = account["portfolio_value"]
            account["live_market_value"] = live_market_value
            account["pricing_source"] = "market_data_stream" if saw_live_price else "alpaca_rest"
            total_equity += _safe_float(account["equity"])
            total_portfolio_value += _safe_float(account["portfolio_value"])

        payload["positions"] = all_positions
        payload["cash"] = total_cash
        payload["buying_power"] = total_buying_power
        payload["portfolio_value"] = total_portfolio_value
        payload["equity"] = total_equity
        payload["position_count_total"] = len(all_positions)
        if isinstance(payload.get("consistency"), dict):
            payload["consistency"]["position_count_total"] = len(all_positions)
            payload["consistency"]["position_count_total_from_accounts"] = sum(
                int(value) for value in (payload.get("position_count_by_account") or {}).values()
            )
            payload["consistency"]["positions_match_total"] = (
                payload["consistency"]["position_count_total_from_accounts"] == len(all_positions)
            )

        if live_timestamps:
            live_latest = max(live_timestamps).isoformat()
            payload["source_timestamp_utc"] = live_latest
            payload["latest_source_timestamp_utc"] = live_latest
            payload["pricing_source"] = "market_data_stream"
        return payload

    def augment_history_with_live_samples(self, payload: Dict[str, Any], account: str) -> Dict[str, Any]:
        if not payload or payload.get("error"):
            return payload
        if payload.get("requested_timeframe") != "1H":
            return payload

        sample_key = account if account != "all" else "all"
        samples = self._live_equity_samples.get(sample_key) or []
        if not samples:
            return payload

        timestamps = list(payload.get("timestamp") or [])
        equities = list(payload.get("equity") or [])
        profits = list(payload.get("profit_loss") or [])
        profit_pcts = list(payload.get("profit_loss_pct") or [])
        base_value = _safe_float(payload.get("base_value"), equities[0] if equities else 0.0)

        # Fix stale trailing points: if the latest live sample's equity diverges
        # significantly from the Alpaca history tail, replace those stale points.
        # This fixes Alpaca's portfolio history misreporting option position values.
        if samples and equities:
            latest_live_equity = _safe_float(samples[-1].get("equity"))
            if latest_live_equity > 0:
                # Walk backwards and remove history points that are stale
                # (same timestamp window as live samples but with wrong equity)
                earliest_sample_ts = int(samples[0].get("timestamp") or 0)
                replaced = 0
                while (
                    timestamps
                    and int(timestamps[-1]) >= earliest_sample_ts
                    and abs(equities[-1] - latest_live_equity) / max(latest_live_equity, 1) > 0.10
                ):
                    timestamps.pop()
                    equities.pop()
                    profits.pop()
                    profit_pcts.pop()
                    replaced += 1
                if replaced:
                    payload["stale_points_replaced"] = replaced

        last_ts = int(timestamps[-1]) if timestamps else 0
        appended = 0

        for sample in samples:
            sample_ts = int(sample.get("timestamp") or 0)
            if sample_ts <= last_ts:
                continue
            equity = _safe_float(sample.get("equity"))
            pl = equity - base_value
            timestamps.append(sample_ts)
            equities.append(equity)
            profits.append(pl)
            profit_pcts.append((pl / base_value) if base_value else 0.0)
            appended += 1

        if appended or payload.get("stale_points_replaced"):
            payload["timestamp"] = timestamps
            payload["equity"] = equities
            payload["profit_loss"] = profits
            payload["profit_loss_pct"] = profit_pcts
            live_latest = _iso_from_unix_timestamp(timestamps[-1])
            if live_latest:
                payload["source_timestamp_utc"] = live_latest
                payload["latest_source_timestamp_utc"] = live_latest
            payload["live_sample_count"] = len(samples)
            payload["live_augmented"] = True
        return payload

    def _update_held_symbols_from_portfolio(self, payload: Dict[str, Any]) -> None:
        held_symbols: Dict[str, set[str]] = {}
        for account in payload.get("accounts") or []:
            label = str(account.get("label") or "")
            held_symbols[label] = {
                str(position.get("symbol") or "").upper()
                for position in account.get("positions") or []
                if position.get("symbol") and _is_stock_market_data_symbol(str(position.get("symbol") or ""))
            }
        self._held_symbols_by_account = held_symbols

    def _mark_price_for_position(self, label: str, position: Dict[str, Any]) -> tuple[Optional[float], Optional[str], Optional[str]]:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol:
            return None, None, None
        market_entry = ((self._latest_market_data.get(label) or {}).get(symbol) or {})
        if not market_entry:
            market_entry = self._shared_market_data.get(symbol) or {}
        trade_price = _safe_float(market_entry.get("trade_price"))
        trade_ts = market_entry.get("trade_timestamp_utc")
        if trade_price > 0:
            return trade_price, "market_data_trade", trade_ts
        bid_price = _safe_float(market_entry.get("bid_price"))
        ask_price = _safe_float(market_entry.get("ask_price"))
        quote_ts = market_entry.get("quote_timestamp_utc")
        if bid_price > 0 and ask_price > 0:
            return (bid_price + ask_price) / 2.0, "market_data_quote_mid", quote_ts
        if bid_price > 0:
            return bid_price, "market_data_bid", quote_ts
        if ask_price > 0:
            return ask_price, "market_data_ask", quote_ts
        return None, None, None

    def _append_live_sample(self, key: str, equity: float) -> None:
        now_dt = _utc_now()
        now_unix = int(now_dt.timestamp())
        samples = self._live_equity_samples.setdefault(key, [])
        if samples:
            last = samples[-1]
            if now_unix - int(last.get("timestamp") or 0) < LIVE_EQUITY_SAMPLE_MIN_INTERVAL_SECONDS:
                last["timestamp"] = now_unix
                last["timestamp_utc"] = now_dt.isoformat()
                last["equity"] = equity
                return
        samples.append({
            "timestamp": now_unix,
            "timestamp_utc": now_dt.isoformat(),
            "equity": equity,
        })
        cutoff = now_unix - int(LIVE_EQUITY_SAMPLE_RETENTION_SECONDS)
        self._live_equity_samples[key] = [sample for sample in samples if int(sample.get("timestamp") or 0) >= cutoff]

    def _record_live_equity_samples(self, payload: Dict[str, Any]) -> None:
        self._append_live_sample("all", _safe_float(payload.get("equity")))
        for account in payload.get("accounts") or []:
            label = str(account.get("label") or "")
            if not label or account.get("status") == "error":
                continue
            self._append_live_sample(label, _safe_float(account.get("equity")))

    async def _rest_quote_refresh_loop(self, accounts: List[Dict[str, str]]):
        """Background task: fetch latest quotes via REST snapshots every 15s as fallback."""
        if not accounts:
            return
        # Use first account's credentials for data API access
        data_key = accounts[0]["api_key"]
        data_secret = accounts[0]["api_secret"]

        while not self._stop_event.is_set():
            try:
                # Collect all unique symbols from held positions
                all_symbols = sorted({
                    sym
                    for syms in self._held_symbols_by_account.values()
                    for sym in syms
                    if sym
                })
                if all_symbols:
                    # Cooldown check
                    now_mono = time.monotonic()
                    if now_mono - self._last_rest_quote_refresh < REST_QUOTE_COOLDOWN_SECONDS:
                        pass  # skip this cycle, too soon
                    else:
                        snapshots = await asyncio.to_thread(
                            _fetch_rest_snapshots, all_symbols, data_key, data_secret
                        )
                        if snapshots:
                            # Update shared market data — only overwrite if no recent
                            # websocket data (ws data has no "source" key or source != "rest_snapshot")
                            now_iso = _utc_now_iso()
                            for sym, entry in snapshots.items():
                                existing = self._shared_market_data.get(sym) or {}
                                existing_source = existing.get("source", "")
                                # Always update if no existing data or existing is also from REST
                                # If existing is from websocket stream, only update if ws data is stale (>60s)
                                if existing_source in ("", "rest_snapshot") or not existing.get("trade_timestamp_utc"):
                                    self._shared_market_data[sym] = entry
                                else:
                                    # Check staleness of ws data
                                    ws_ts = _parse_iso_datetime(existing.get("trade_timestamp_utc"))
                                    if ws_ts is None or (_utc_now() - ws_ts).total_seconds() > 60:
                                        self._shared_market_data[sym] = entry

                            self._last_rest_quote_refresh = time.monotonic()
                            self._rest_quote_refresh_utc = now_iso
                            await self.refresh_and_broadcast(force=False, reason="rest_quote_refresh")
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # Silently continue — this is a best-effort fallback

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=REST_QUOTE_REFRESH_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue

    async def start(self):
        accounts = _get_alpaca_accounts()
        self._tasks.append(asyncio.create_task(self._poll_loop(accounts)))
        for acct in accounts:
            self._stream_status[acct["label"]] = {
                "status": "starting",
                "trade_updates_status": "starting",
                "market_data_status": "starting",
                "last_event_utc": None,
                "last_quote_utc": None,
                "last_trade_utc": None,
                "last_error": None,
                "reconnect_count": 0,
                "subscribed_symbols": [],
            }
            self._tasks.append(asyncio.create_task(self._trade_updates_loop(acct)))
        if accounts:
            self._tasks.append(asyncio.create_task(self._market_data_loop(accounts)))
            self._tasks.append(asyncio.create_task(self._rest_quote_refresh_loop(accounts)))
        # Warm the portfolio cache in the background so startup does not block
        # on broker discovery or slow third-party auth/login paths.
        async def _startup_refresh() -> None:
            try:
                await self.refresh_and_broadcast(force=True, reason="startup")
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        self._tasks.append(asyncio.create_task(_startup_refresh()))

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
                details["trade_updates_status"] = "connecting"
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
                details["trade_updates_status"] = "connected"
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
                        details["trade_updates_status"] = "listening"
                        continue
                    if stream_name != "trade_updates":
                        continue

                    details["status"] = "event"
                    details["trade_updates_status"] = "event"
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
                details["trade_updates_status"] = "error"
                details["last_error"] = str(e)
                details["last_event_utc"] = _utc_now_iso()
                details["reconnect_count"] = int(details.get("reconnect_count", 0)) + 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=reconnect_delay)
                except asyncio.TimeoutError:
                    reconnect_delay = min(reconnect_delay * 2.0, 30.0)
                    continue

    async def _market_data_loop(self, accounts: List[Dict[str, str]]):
        try:
            import websockets
        except Exception as e:
            for details in self._stream_status.values():
                details["market_data_status"] = "unavailable"
                details["last_error"] = f"websockets import failed: {e}"
                details["last_event_utc"] = _utc_now_iso()
            return

        reconnect_delay = 1.0
        current_subscribed: set[str] = set()
        acct_index = 0
        while not self._stop_event.is_set():
            ws = None
            acct = accounts[acct_index % len(accounts)]
            label = acct["label"]
            try:
                for details in self._stream_status.values():
                    details["market_data_status"] = "connecting"
                    details["last_error"] = None
                    details["market_data_key_label"] = label
                ws = await websockets.connect(
                    _alpaca_market_data_stream_url(),
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=512,
                )
                await ws.send(json.dumps({
                    "action": "auth",
                    "key": acct["api_key"],
                    "secret": acct["api_secret"],
                }))

                authorized = False
                auth_deadline = asyncio.get_running_loop().time() + 10.0
                while asyncio.get_running_loop().time() < auth_deadline and not authorized:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    items = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                    if not isinstance(items, list):
                        items = [items]
                    for item in items:
                        if item.get("T") == "success" and item.get("msg") == "authenticated":
                            authorized = True
                            break
                        if item.get("T") == "error":
                            raise RuntimeError(str(item.get("msg") or "market data auth failed"))
                if not authorized:
                    raise RuntimeError(f"{label} auth timeout on Alpaca market data stream")

                for details in self._stream_status.values():
                    details["market_data_status"] = "connected"
                reconnect_delay = 1.0

                while not self._stop_event.is_set():
                    desired_symbols = set().union(*self._held_symbols_by_account.values()) if self._held_symbols_by_account else set()
                    add_symbols = desired_symbols - current_subscribed
                    remove_symbols = current_subscribed - desired_symbols
                    if add_symbols:
                        await ws.send(json.dumps({
                            "action": "subscribe",
                            "trades": sorted(add_symbols),
                            "quotes": sorted(add_symbols),
                        }))
                    if remove_symbols:
                        await ws.send(json.dumps({
                            "action": "unsubscribe",
                            "trades": sorted(remove_symbols),
                            "quotes": sorted(remove_symbols),
                        }))
                    if add_symbols or remove_symbols:
                        current_subscribed = desired_symbols
                        for details in self._stream_status.values():
                            details["subscribed_symbols"] = sorted(current_subscribed)

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue

                    items = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                    if not isinstance(items, list):
                        items = [items]

                    saw_market_update = False
                    for item in items:
                        item_type = str(item.get("T") or "")
                        if item_type == "subscription":
                            trades = item.get("trades") or []
                            quotes = item.get("quotes") or []
                            subscribed_symbols = sorted(set(trades) | set(quotes))
                            for details in self._stream_status.values():
                                details["market_data_status"] = "listening"
                                details["subscribed_symbols"] = subscribed_symbols
                            current_subscribed = set(subscribed_symbols)
                            continue
                        if item_type == "success":
                            continue
                        if item_type == "error":
                            raise RuntimeError(str(item.get("msg") or "market data stream error"))
                        symbol = str(item.get("S") or "").upper()
                        if not symbol:
                            continue
                        for details in self._stream_status.values():
                            details["market_data_status"] = "listening"
                        symbol_entry = self._shared_market_data.setdefault(symbol, {})
                        if item_type == "t":
                            symbol_entry["trade_price"] = _safe_float(item.get("p"))
                            symbol_entry["trade_timestamp_utc"] = str(item.get("t") or _utc_now_iso())
                            symbol_entry["source"] = "websocket"
                            for details in self._stream_status.values():
                                details["last_trade_utc"] = symbol_entry["trade_timestamp_utc"]
                            saw_market_update = True
                        elif item_type == "q":
                            symbol_entry["bid_price"] = _safe_float(item.get("bp"))
                            symbol_entry["ask_price"] = _safe_float(item.get("ap"))
                            symbol_entry["quote_timestamp_utc"] = str(item.get("t") or _utc_now_iso())
                            symbol_entry["source"] = "websocket"
                            for details in self._stream_status.values():
                                details["last_quote_utc"] = symbol_entry["quote_timestamp_utc"]
                            saw_market_update = True

                    if saw_market_update:
                        for details in self._stream_status.values():
                            details["last_event_utc"] = _utc_now_iso()
                        await self.refresh_and_broadcast(force=False, reason=f"market_data:{label}")
            except asyncio.CancelledError:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                raise
            except Exception as e:
                error_text = str(e)
                is_connection_limit = "connection limit exceeded" in error_text.lower()
                for details in self._stream_status.values():
                    details["market_data_status"] = "rest_fallback" if is_connection_limit else "error"
                    details["last_error"] = error_text
                    details["last_event_utc"] = _utc_now_iso()
                    details["reconnect_count"] = int(details.get("reconnect_count", 0)) + 1
                current_subscribed = set()
                if is_connection_limit and accounts:
                    acct_index = (acct_index + 1) % len(accounts)
                    reconnect_delay = max(reconnect_delay, MARKET_DATA_CONNECTION_LIMIT_COOLDOWN_SECONDS)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=reconnect_delay)
                except asyncio.TimeoutError:
                    if is_connection_limit:
                        reconnect_delay = max(MARKET_DATA_CONNECTION_LIMIT_COOLDOWN_SECONDS, reconnect_delay)
                    else:
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
        portfolio_payload = self.apply_live_market_prices(portfolio_payload)
        self._update_held_symbols_from_portfolio(portfolio_payload)
        self._record_live_equity_samples(portfolio_payload)
        portfolio_history_intraday = self.augment_history_with_live_samples(portfolio_history_intraday, "all")
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
            "control_status": _canonical_control_status_payload(
                heartbeat_payload=hb,
                scorecard_payload=cards[0] if cards else None,
            ),
            "controls": _controls_wrapper_payload(),
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
    return _approval_guidance_response(
        kind="gs.control.kill_switch.set",
        target=f"global-sentinel/control/kill-switch/{'on' if req.active else 'off'}",
        requested_change={"kill_switch": req.active, "reason": req.reason or ""},
    )

@app.post("/api/control/veto")
def set_veto(req: VetoRequest):
    """Activate or deactivate manual veto."""
    return _approval_guidance_response(
        kind="gs.control.manual_veto.set",
        target=f"global-sentinel/control/manual-veto/{'on' if req.active else 'off'}",
        requested_change={"manual_veto": req.active, "reason": req.reason or ""},
    )

@app.get("/api/control/status")
def control_status():
    """Full system status for bot consumption."""
    return _canonical_control_status_payload()

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
LAYOUT_REQUIRED_WIDGETS: List[Dict[str, Any]] = [
    {
        "row_id": "row_quantum",
        "widget": {
            "id": "quantum_comparison",
            "cols": 12,
            "title": "Quantum vs Classical — Optimization Research",
            "visible": True,
            "badge": "BOUNDED SECONDARY SIGNAL",
        },
    }
]

class LayoutUpdateRequest(BaseModel):
    rows: List[Dict[str, Any]]
    updated_by: str = "api"


@app.get("/api/dashboard/layout")
def get_dashboard_layout():
    """Get current dashboard layout config."""
    if LAYOUT_PATH.exists():
        layout = load_json(LAYOUT_PATH)
        rows = list(layout.get("rows") or [])
        existing_ids = {
            widget.get("id")
            for row in rows
            for widget in (row.get("widgets") or [])
            if isinstance(widget, dict)
        }
        upgraded_widgets: List[str] = []
        for required in LAYOUT_REQUIRED_WIDGETS:
            widget = required["widget"]
            if widget["id"] in existing_ids:
                continue
            rows.append({"id": required["row_id"], "widgets": [widget]})
            upgraded_widgets.append(widget["id"])
        if upgraded_widgets:
            layout = {**layout, "rows": rows, "upgraded_widgets": upgraded_widgets}
        return layout
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
            "quantum_comparison",
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
# V4 Module Status Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v4/status")
def v4_module_status():
    """Return status of all V4 governance/hardening modules."""
    status = {"timestamp_utc": _utc_now_iso(), "modules": {}}
    # Policy Engine
    try:
        from src.core.policy_engine import PolicyEngine
        pe = PolicyEngine(config_dir=REPO_ROOT / "config")
        status["modules"]["policy_engine"] = {"available": True, "config_loaded": bool(pe._config)}
    except Exception as e:
        status["modules"]["policy_engine"] = {"available": False, "error": str(e)}
    # Circuit Breaker
    try:
        from src.execution.circuit_breaker import CircuitBreaker
        status["modules"]["circuit_breaker"] = {"available": True}
    except Exception as e:
        status["modules"]["circuit_breaker"] = {"available": False, "error": str(e)}
    # Pre-Trade Controls
    try:
        from src.execution.pre_trade_controls import PreTradeControls
        ptc = PreTradeControls(config_dir=REPO_ROOT / "config")
        status["modules"]["pre_trade_controls"] = {"available": True, "config_loaded": bool(ptc._config)}
    except Exception as e:
        status["modules"]["pre_trade_controls"] = {"available": False, "error": str(e)}
    # Source Quorum
    try:
        from src.core.source_quorum_engine import SourceQuorumEngine
        status["modules"]["source_quorum"] = {"available": True}
    except Exception as e:
        status["modules"]["source_quorum"] = {"available": False, "error": str(e)}
    # Feature Store
    try:
        from src.research.feature_store_builder import FeatureStoreBuilder
        status["modules"]["feature_store"] = {"available": True}
    except Exception as e:
        status["modules"]["feature_store"] = {"available": False, "error": str(e)}
    # Microstructure Regime Classifier
    try:
        from src.execution.microstructure_regime_classifier import MicrostructureRegimeClassifier
        status["modules"]["microstructure_regime"] = {"available": True}
    except Exception as e:
        status["modules"]["microstructure_regime"] = {"available": False, "error": str(e)}
    return status


@app.get("/api/v4/policy-audit")
def v4_policy_audit():
    """Return latest policy audit report if available."""
    try:
        from src.reports.policy_audit_report import build_policy_audit_report
        log_path = REPO_ROOT / "logs" / "policy_engine" / "evaluations.jsonl"
        if not log_path.exists():
            return {"schema_version": "policy_audit_report.v1", "evaluation_count": 0, "note": "no evaluations yet"}
        entries = []
        for line in log_path.read_text(encoding="utf-8").splitlines()[-500:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return build_policy_audit_report(entries)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# V4 Governance Endpoints
# ---------------------------------------------------------------------------

def _load_v4_feature_timestamps() -> tuple[Dict[str, datetime], Dict[str, Any]]:
    """Best-effort discovery of runtime feature timestamps for dashboard display."""
    searched_paths = [
        REPO_ROOT / "logs" / "feature_timestamps.json",
        REPO_ROOT / "logs" / "feature_store" / "feature_timestamps.json",
        REPO_ROOT / "reports" / "feature_timestamps.json",
        REPO_ROOT / "reports" / "feature_store" / "feature_timestamps.json",
    ]

    def _extract_timestamp(value: Any) -> Optional[datetime]:
        if isinstance(value, dict):
            for key in ("timestamp_utc", "last_updated", "updated_at", "last_seen_utc"):
                parsed = _parse_iso_datetime(value.get(key))
                if parsed is not None:
                    return parsed
            return None
        return _parse_iso_datetime(value)

    for path in searched_paths:
        payload = load_json(path)
        if not payload:
            continue
        raw_timestamps = payload.get("feature_timestamps", payload)
        if not isinstance(raw_timestamps, dict):
            continue

        timestamps: Dict[str, datetime] = {}
        for name, raw_value in raw_timestamps.items():
            parsed = _extract_timestamp(raw_value)
            if parsed is not None:
                timestamps[str(name)] = parsed

        return timestamps, {
            "timestamp_source": str(path),
            "searched_paths": [str(item) for item in searched_paths],
            "loaded_feature_count": len(timestamps),
        }

    latest_scorecard = load_scorecards(limit=1)
    if latest_scorecard:
        raw_timestamps = latest_scorecard[-1].get("feature_timestamps", {})
        if isinstance(raw_timestamps, dict):
            timestamps = {}
            for name, raw_value in raw_timestamps.items():
                parsed = _extract_timestamp(raw_value)
                if parsed is not None:
                    timestamps[str(name)] = parsed
            return timestamps, {
                "timestamp_source": "scorecard.feature_timestamps",
                "searched_paths": [str(item) for item in searched_paths],
                "loaded_feature_count": len(timestamps),
            }

    return {}, {
        "timestamp_source": "not_found",
        "searched_paths": [str(item) for item in searched_paths],
        "loaded_feature_count": 0,
    }


def _resolve_manifest_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize possible manifest container shapes into a raw manifest dict."""
    if not isinstance(payload, dict):
        return None
    if "artifact_id" in payload and "artifact_type" in payload:
        return payload
    if isinstance(payload.get("_artifact_manifest"), dict):
        return payload["_artifact_manifest"]
    if isinstance(payload.get("manifest"), dict):
        return payload["manifest"]
    return None


def _lookup_v4_lineage_manifest(artifact_id: str) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Search a small set of lineage stores for an artifact manifest."""
    from src.lineage.artifact_manifest_builder import LineageResolver

    search_paths = [
        REPO_ROOT / "logs" / "lineage" / "manifests.jsonl",
        REPO_ROOT / "reports" / "lineage" / "manifests.jsonl",
    ]
    resolver = LineageResolver()
    matched_path: Optional[str] = None

    for path in search_paths:
        if not path.exists():
            continue
        for row in load_jsonl(path, limit=5000):
            manifest_payload = _resolve_manifest_payload(row)
            if manifest_payload is None:
                continue
            try:
                resolver.register_dict(manifest_payload)
                if str(manifest_payload.get("artifact_id")) == str(artifact_id):
                    matched_path = str(path)
            except Exception:
                continue

    manifest = resolver.get(str(artifact_id))
    trace = {
        "artifact_id": str(artifact_id),
        "searched_paths": [str(item) for item in search_paths],
        "matched_path": matched_path,
        "registered_manifest_count": resolver.manifest_count,
    }
    if manifest is None:
        return None, trace

    ancestry = [item.to_dict() for item in resolver.get_ancestry(str(artifact_id))]
    validation = resolver.validate_lineage(str(artifact_id))
    trace["validation"] = validation
    trace["ancestry_depth"] = len(ancestry)
    return {
        "manifest": manifest.to_dict(),
        "validation": validation,
        "ancestry": ancestry,
    }, trace


@app.get("/api/v4/governance")
def v4_governance_status():
    """Return governance and promotion gating state with decision traces."""
    try:
        from src.core.policy_engine import PolicyEngine
        from src.core.promotion_policy_loader import load_promotion_policy
        from src.research.encoder_promotion_gate import EncoderPromotionGate

        config_path = REPO_ROOT / "config" / "promotion_policy.yaml"
        policy = load_promotion_policy(config_path)
        gate = EncoderPromotionGate(config_path=config_path)
        policy_engine = PolicyEngine(config_dir=REPO_ROOT / "config")
        current_mode = policy_engine._current_mode()

        probe_metrics = {
            "eval_days": 120,
            "trade_count": 300,
            "drawdown_delta_bps": 20,
            "slippage_adjusted_win_delta_bps": 25,
            "failure_rate": 0.01,
            "cumulative_drift_std": 0.5,
        }

        signal_traces = []
        for signal_type in sorted(policy.signal_thresholds):
            decision = gate.evaluate(
                probe_metrics,
                signal_type=signal_type,
                current_mode=current_mode,
            )
            signal_traces.append({
                "signal_type": signal_type,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "gate_results": decision.gate_results,
            })

        blocked_examples = {
            "frozen_mode": gate.evaluate(
                probe_metrics,
                signal_type="default",
                current_mode="CRISIS",
            ).to_dict(),
            "politician_alpha": gate.evaluate(
                probe_metrics,
                signal_type="politician_alpha",
                current_mode=current_mode,
            ).to_dict(),
        }

        return {
            "schema_version": "dashboard_governance.v1",
            "timestamp_utc": _utc_now_iso(),
            "current_mode": current_mode,
            "frozen_modes": list(policy.frozen_modes),
            "policy_engine": {
                "available": True,
                "current_mode": current_mode,
                "quantum_stage": policy_engine._quantum_stage(),
                "quantum_influence_cap": policy_engine._quantum_max_influence(),
            },
            "policy": {
                "schema_version": policy.schema_version,
                "human_approval_required": policy.human_approval_required,
                "dual_run_required": policy.dual_run_required,
                "rollback_required": policy.rollback_required,
                "blocked_signals": [
                    signal_type
                    for signal_type, thresholds in policy.signal_thresholds.items()
                    if thresholds.promotion_blocked
                ],
            },
            "decision_trace": {
                "signal_traces": signal_traces,
                "blocked_examples": blocked_examples,
                "config_path": str(config_path),
            },
        }
    except Exception as e:
        return {"error": str(e), "timestamp_utc": _utc_now_iso()}


@app.get("/api/v4/freshness")
def v4_feature_freshness():
    """Return per-group feature freshness state with stale reasons."""
    try:
        from src.core.feature_freshness_enforcer import FeatureFreshnessEnforcer
        from src.core.feature_registry_loader import load_feature_registry

        config_dir = REPO_ROOT / "config"
        registry = load_feature_registry(config_dir / "feature_registry.yaml")
        enforcer = FeatureFreshnessEnforcer(config_dir=config_dir)
        timestamps, timestamp_trace = _load_v4_feature_timestamps()
        now = _utc_now()
        group_results = enforcer.check_all_groups(timestamps, now)

        groups = {}
        for group_name, result in group_results.items():
            groups[group_name] = {
                "policy": result.policy,
                "compliant": result.compliant,
                "degraded": result.degraded,
                "confidence_penalty": result.confidence_penalty,
                "fresh_count": result.fresh_count,
                "stale_count": result.stale_count,
                "missing_count": result.missing_count,
                "decision_trace": [
                    {
                        "feature_name": feature.feature_name,
                        "status": feature.status,
                        "ttl_minutes": feature.ttl_minutes,
                        "age_minutes": feature.age_minutes,
                        "confidence_penalty": feature.confidence_penalty,
                        "reason": feature.stale_reason,
                    }
                    for feature in result.feature_results
                ],
            }

        return {
            "schema_version": "dashboard_feature_freshness.v1",
            "timestamp_utc": _utc_now_iso(),
            "summary": enforcer.summary(timestamps, now),
            "groups": groups,
            "decision_trace": {
                "timestamp_trace": timestamp_trace,
                "registry_feature_count": len(registry.features),
                "features_without_runtime_timestamp": [
                    feature.name
                    for feature in registry.list_features()
                    if feature.name not in timestamps
                ],
            },
        }
    except Exception as e:
        return {"error": str(e), "timestamp_utc": _utc_now_iso()}


@app.get("/api/v4/features")
def v4_feature_registry():
    """Return the typed feature registry used by V4 freshness governance."""
    try:
        from src.core.feature_registry_loader import load_feature_registry

        config_path = REPO_ROOT / "config" / "feature_registry.yaml"
        registry = load_feature_registry(config_path)
        features_by_source: Dict[str, List[str]] = {}
        for feature in registry.list_features():
            features_by_source.setdefault(feature.source, []).append(feature.name)

        return {
            "schema_version": "dashboard_feature_registry.v1",
            "timestamp_utc": _utc_now_iso(),
            "registry": registry.to_dict(),
            "features_by_source": {
                source: sorted(feature_names)
                for source, feature_names in sorted(features_by_source.items())
            },
            "decision_trace": {
                "config_path": str(config_path),
                "validation_errors": list(registry.validation_errors),
            },
        }
    except Exception as e:
        return {"error": str(e), "timestamp_utc": _utc_now_iso()}


@app.get("/api/v4/lineage/{artifact_id}")
def v4_lineage_lookup(artifact_id: str):
    """Lookup a stored artifact manifest and lineage trace."""
    try:
        payload, trace = _lookup_v4_lineage_manifest(artifact_id)
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "artifact_not_found",
                    "artifact_id": artifact_id,
                    "decision_trace": trace,
                    "timestamp_utc": _utc_now_iso(),
                },
            )
        return {
            "schema_version": "dashboard_lineage_lookup.v1",
            "timestamp_utc": _utc_now_iso(),
            "artifact_id": artifact_id,
            **payload,
            "decision_trace": trace,
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": str(e),
                "artifact_id": artifact_id,
                "timestamp_utc": _utc_now_iso(),
            },
        )


# ---------------------------------------------------------------------------
# SIGNAL FEED ENDPOINT (reads quantum_feed latest signal)
# ---------------------------------------------------------------------------

@app.get("/api/signal-feed")
def signal_feed():
    """Read latest signal from the 24/7 data gatherer quantum feed."""
    signal_path = REPO_ROOT / "data" / "quantum_feed" / "latest_signal.json"
    if not signal_path.exists():
        return JSONResponse(
            {"error": "No signal data available", "timestamp_utc": _utc_now_iso()},
            status_code=404,
        )
    try:
        raw = signal_path.read_text()
        data = json.loads(raw)
        data["_served_at"] = _utc_now_iso()
        return data
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to read signal feed: {e}", "timestamp_utc": _utc_now_iso()},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# V6 WAR ROOM API ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/v6/warroom")
def v6_warroom():
    """Full war room snapshot — combines all V6 modules into one payload."""
    scorecard = load_scorecards(limit=1)
    card = scorecard[0] if scorecard else {}

    # Strategy ideas from latest scorecard
    strategy_ideas = card.get("v6_strategy_ideas", [])
    edge_findings = card.get("v6_edge_findings", {})
    cross_asset = card.get("v6_cross_asset_signals", {})
    scanner = card.get("v6_scanner_discoveries", [])
    deescalation = card.get("v6_deescalation", {})
    scenarios = card.get("v6_scenarios", {})

    payload = {
        "timestamp_utc": _utc_now_iso(),
        "source_timestamp_utc": card.get("timestamp_utc"),
        "regime": {
            "probability": card.get("regime_shift_probability", 0),
            "mode": card.get("mode", "NORMAL"),
            "confidence": card.get("confidence", 0),
        },
        "chokepoints": card.get("chokepoint_risk", {}),
        "strategy_ideas": strategy_ideas,
        "edge_findings": edge_findings,
        "cross_asset_signals": cross_asset,
        "scanner_discoveries": scanner,
        "deescalation": deescalation,
        "scenarios": scenarios,
        "bridge_health": _build_bridge_panel_status(card) if card else {},
        "bridge_summary": card.get("bridge_summary", {}),
    }
    payload.update(_freshness_metadata(card.get("timestamp_utc"), stale_after_seconds=900, degraded_after_seconds=300))
    return payload


@app.get("/api/v6/strategies")
def v6_strategies():
    """All 15 war strategies with current status."""
    try:
        import yaml
        config_path = REPO_ROOT / "config" / "war_strategies.yaml"
        if config_path.exists():
            config = yaml.safe_load(config_path.read_text()) or {}
            strategies = config.get("strategies", {})
            return {
                "timestamp_utc": _utc_now_iso(),
                "strategy_count": len(strategies),
                "strategies": {
                    name: {
                        "account": s.get("account"),
                        "timeframe": s.get("timeframe"),
                        "target_daily_pnl": s.get("target_daily_pnl", 0),
                        "positions": [
                            {"symbol": p.get("symbol"), "size_usd": p.get("size_usd"), "side": p.get("side")}
                            for p in s.get("positions", [])
                        ],
                    }
                    for name, s in strategies.items()
                },
            }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/v6/scenarios")
def v6_scenarios():
    """Run scenario simulator against current portfolio."""
    try:
        from src.risk.scenario_simulator import ScenarioSimulator
        sim = ScenarioSimulator()
        results = sim.simulate_all({})
        return {"timestamp_utc": _utc_now_iso(), "scenarios": results}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/v6/watchlist")
def v6_watchlist():
    """Full expanded watchlist."""
    try:
        from src.data.watchlist_manager import WatchlistManager
        wm = WatchlistManager(repo_root=REPO_ROOT)
        return {
            "timestamp_utc": _utc_now_iso(),
            "total_symbols": len(wm.get_all_symbols()),
            "categories": {
                cat: wm.get_by_category(cat)
                for cat in wm.get_categories()
            },
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/v6/kill-switch")
def v6_kill_switch():
    """Emergency kill switch — activates kill_switch.json."""
    return _approval_guidance_response(
        kind="gs.control.kill_switch.set",
        target="global-sentinel/control/kill-switch/on",
        requested_change={"kill_switch": True, "reason": "Dashboard kill switch activated"},
    )


@app.post("/api/v6/kill-switch/deactivate")
def v6_kill_switch_deactivate():
    """Deactivate kill switch."""
    return _approval_guidance_response(
        kind="gs.control.kill_switch.set",
        target="global-sentinel/control/kill-switch/off",
        requested_change={"kill_switch": False, "reason": ""},
    )


@app.get("/api/v6/calendar")
def v6_calendar():
    """Market calendar for the week."""
    try:
        import yaml
        cal_path = REPO_ROOT / "config" / "market_calendar.yaml"
        if cal_path.exists():
            return yaml.safe_load(cal_path.read_text()) or {}
        return {"error": "calendar not found"}
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws/warroom")
async def ws_warroom(websocket: WebSocket):
    """Real-time war room WebSocket — pushes state every 5 seconds."""
    await websocket.accept()
    try:
        while True:
            try:
                data = v6_warroom()
                await websocket.send_json(data)
            except Exception:
                pass
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Serve war room dashboard (dashboard/static/)
# ---------------------------------------------------------------------------

_static_dir = Path(__file__).parent.parent / "static"

@app.get("/warroom")
@app.get("/warroom.html")
def serve_warroom():
    """Serve the standalone war room HTML dashboard."""
    warroom_path = _static_dir / "warroom.html"
    if warroom_path.exists():
        return FileResponse(str(warroom_path), media_type="text/html")
    return JSONResponse({"error": "warroom.html not found"}, status_code=404)

# ---------------------------------------------------------------------------
# Serve frontend static files (production)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# POSITION PRICE TRACKER — real-time prices for held positions (stocks + options)
# ---------------------------------------------------------------------------

# In-memory rolling price history for positions (survives across requests, cleared on restart)
_position_price_history: Dict[str, List[Dict[str, Any]]] = {}
_POSITION_HISTORY_MAX_POINTS = 500  # ~4 hours at 30s intervals

@app.get("/api/position-prices")
def position_prices():
    """Return live prices + rolling history for all held positions.

    For stocks: uses Yahoo Finance real-time quote.
    For options: uses Alpaca option snapshots (bid/ask/mid).
    Stores a rolling history so the frontend can chart price over time.
    """
    import urllib.request
    _load_env()

    accounts = _get_alpaca_accounts()
    all_positions = []
    for acct in accounts:
        try:
            acct_data = _get_cached_alpaca_account(acct)
            for pos in acct_data.get("positions", []):
                pos["account_label"] = acct["label"]
                all_positions.append(pos)
        except Exception:
            continue

    if not all_positions:
        return {"positions": [], "timestamp_utc": _utc_now_iso()}

    now_unix = int(time.time())
    results = []

    for pos in all_positions:
        symbol = pos.get("symbol", "")
        entry = float(pos.get("avg_entry_price", 0))
        qty = float(pos.get("qty", 0))
        asset_class = pos.get("asset_class", "")

        # Determine if this is an option (OCC symbol format: ROOT + 6-digit date + C/P + 8-digit strike)
        is_option = bool(
            len(symbol) > 10
            and any(c in symbol for c in ("C", "P"))
            and symbol[-8:].isdigit()
        )

        price_data = {"bid": 0, "ask": 0, "mid": 0, "last": 0, "source": "unknown"}

        if is_option:
            # Fetch from Alpaca option snapshots
            try:
                api_key = os.getenv("ALPACA_API_KEY_LIVE") or os.getenv("ALPACA_API_KEY", "")
                api_secret = os.getenv("ALPACA_SECRET_KEY_LIVE") or os.getenv("ALPACA_SECRET_KEY", "")
                headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
                url = f"https://data.alpaca.markets/v1beta1/options/snapshots?symbols={symbol}&feed=indicative"
                req = urllib.request.Request(url, headers=headers)
                resp = json.loads(urllib.request.urlopen(req, timeout=8).read())
                snap = resp.get("snapshots", {}).get(symbol, {})
                q = snap.get("latestQuote", {})
                bid = float(q.get("bp", 0))
                ask = float(q.get("ap", 0))
                mid = round((bid + ask) / 2, 4) if bid and ask else 0
                trade = snap.get("latestTrade", {})
                last = float(trade.get("p", 0))
                price_data = {"bid": bid, "ask": ask, "mid": mid, "last": last or mid, "source": "alpaca_option"}
            except Exception as e:
                price_data["error"] = str(e)[:80]
        else:
            # Stock: use Yahoo Finance
            try:
                yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
                yf_req = urllib.request.Request(yf_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(yf_req, timeout=6) as resp:
                    yf_data = json.loads(resp.read().decode())
                meta = yf_data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                current = meta.get("regularMarketPrice", 0)
                price_data = {"bid": 0, "ask": 0, "mid": current, "last": current, "source": "yahoo"}
            except Exception as e:
                # Fallback to Alpaca position price
                current = float(pos.get("current_price", 0))
                price_data = {"bid": 0, "ask": 0, "mid": current, "last": current, "source": "alpaca_rest"}

        current_price = price_data["mid"] or price_data["last"]
        pnl = (current_price - entry) * qty * (100 if is_option else 1)
        pnl_pct = ((current_price / entry) - 1) * 100 if entry else 0

        # Append to rolling history
        if symbol not in _position_price_history:
            _position_price_history[symbol] = []
        history = _position_price_history[symbol]

        # Only append if price changed or 30s+ since last point
        should_append = True
        if history:
            last_pt = history[-1]
            if last_pt["price"] == current_price and (now_unix - last_pt["time"]) < 30:
                should_append = False

        if should_append and current_price > 0:
            history.append({"time": now_unix, "price": current_price, "bid": price_data["bid"], "ask": price_data["ask"]})
            # Trim to max
            if len(history) > _POSITION_HISTORY_MAX_POINTS:
                _position_price_history[symbol] = history[-_POSITION_HISTORY_MAX_POINTS:]

        # Extract underlying for options
        underlying = symbol
        strike = None
        opt_type = None
        expiry = None
        opt_match = None
        if is_option:
            import re
            opt_match = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', symbol)
        if opt_match:
            underlying = opt_match.group(1)
            exp_raw = opt_match.group(2)
            opt_type = "Call" if opt_match.group(3) == "C" else "Put"
            strike = int(opt_match.group(4)) / 1000
            expiry = f"20{exp_raw[:2]}-{exp_raw[2:4]}-{exp_raw[4:6]}"

        results.append({
            "symbol": symbol,
            "underlying": underlying,
            "is_option": is_option,
            "strike": strike,
            "opt_type": opt_type,
            "expiry": expiry,
            "qty": qty,
            "entry_price": entry,
            "current_price": current_price,
            "bid": price_data["bid"],
            "ask": price_data["ask"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "market_value": round(current_price * qty * (100 if is_option else 1), 2),
            "source": price_data["source"],
            "account": pos.get("account_label", ""),
            "history": _position_price_history.get(symbol, []),
        })

    return {"positions": results, "timestamp_utc": _utc_now_iso()}


# ---------------------------------------------------------------------------
# WHAT-IF TRACKER — top suggested picks with live prices
# ---------------------------------------------------------------------------

@app.get("/api/whatif-picks")
def whatif_picks():
    """Return top suggested trade ideas with live prices for what-if tracking.

    Reads the iran_war_brief.json for ideas, fetches current Alpaca prices,
    and computes hypothetical P&L if $25 had been invested at the brief's
    reference open price.
    """
    import urllib.request
    import urllib.error
    _load_env()

    brief_path = REPO_ROOT / "reports" / "flash" / "iran_war_brief.json"
    if not brief_path.exists():
        return {"picks": [], "error": "No brief data", "timestamp_utc": _utc_now_iso()}

    try:
        brief = json.loads(brief_path.read_text())
    except Exception:
        return {"picks": [], "error": "Failed to read brief", "timestamp_utc": _utc_now_iso()}

    ideas = brief.get("ideas", [])
    # Filter to share-based ideas only (not options), pick top 5 non-GUSH
    share_ideas = [
        i for i in ideas
        if i.get("order_type") != "option"
        and i.get("ticker", "") not in ("GUSH",)  # exclude current holding
    ][:5]

    # Always include these long symbols even if not in the brief's share ideas
    ALWAYS_INCLUDE_LONG = [
        {"ticker": "USO", "direction": "LONG", "bucket": "OIL_SUPPLY",
         "confidence": 80, "ev_pct": 20, "note": "United States Oil Fund — direct WTI crude exposure"},
    ]
    existing_tickers = {i.get("ticker", "") for i in share_ideas}
    for extra in ALWAYS_INCLUDE_LONG:
        if extra["ticker"] not in existing_tickers:
            share_ideas.append({**extra, "vehicle": extra["ticker"], "order_type": "share"})

    # Top 5 short/inverse picks — tracks bearish side of the war trade
    SHORT_PICKS = [
        {"ticker": "SQQQ", "direction": "LONG", "bucket": "TECH_SELLOFF",
         "confidence": 65, "ev_pct": 20, "note": "3x Short Nasdaq — tech selloff from oil inflation"},
        {"ticker": "JETS", "direction": "SHORT", "bucket": "AVIATION",
         "confidence": 75, "ev_pct": 15, "note": "US Global Jets ETF — airlines crushed by oil >$100"},
        {"ticker": "TZA", "direction": "LONG", "bucket": "MELTDOWN_HEDGE",
         "confidence": 55, "ev_pct": 15, "note": "3x Short Russell 2000 — small caps hit hardest in recession"},
        {"ticker": "SPXS", "direction": "LONG", "bucket": "MELTDOWN_HEDGE",
         "confidence": 50, "ev_pct": 12, "note": "3x Short S&P 500 — broad market meltdown hedge"},
        {"ticker": "TLT", "direction": "LONG", "bucket": "MELTDOWN_HEDGE",
         "confidence": 55, "ev_pct": 10, "note": "20Y+ Treasuries — flight to safety if recession hits"},
    ]
    for sp in SHORT_PICKS:
        if sp["ticker"] not in existing_tickers:
            share_ideas.append({**sp, "vehicle": sp["ticker"], "order_type": "share"})

    if not share_ideas:
        return {"picks": [], "error": "No share ideas in brief", "timestamp_utc": _utc_now_iso()}

    picks = []
    for idea in share_ideas:
        ticker = idea.get("ticker", "")
        if not ticker:
            continue

        pick = {
            "symbol": ticker,
            "direction": idea.get("direction", "LONG"),
            "note": idea.get("note", ""),
            "confidence": idea.get("confidence", 0),
            "ev_pct": idea.get("ev_pct", 0),
            "bucket": idea.get("bucket", ""),
            "hypothetical_investment": 25.0,
            "current_price": 0,
            "open_price": 0,
            "change_pct": 0,
            "hypothetical_pnl": 0,
            "hypothetical_value": 25.0,
        }

        # Fetch live price from Yahoo Finance (real-time, free, no subscription)
        try:
            yf_url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                f"?interval=1m&range=1d"
            )
            yf_req = urllib.request.Request(yf_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(yf_req, timeout=6) as resp:
                yf_data = json.loads(resp.read().decode())

            yf_result = yf_data.get("chart", {}).get("result", [])
            if not yf_result:
                pick["error"] = "No Yahoo data"
                picks.append(pick)
                continue

            meta = yf_result[0].get("meta", {})
            current = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)
            # Use official opening bell price as daily benchmark
            open_price = meta.get("regularMarketOpen", 0)
            if not open_price:
                # Fallback: first candle's open
                quotes = yf_result[0].get("indicators", {}).get("quote", [{}])[0]
                opens = quotes.get("open", [])
                open_price = opens[0] if opens and opens[0] is not None else 0

            if current and open_price:
                # Track change from today's open, not prev close
                day_change = ((current - open_price) / open_price * 100) if open_price else 0
                shares = 25.0 / open_price if open_price else 0
                hyp_value = shares * current
                hyp_pnl = hyp_value - 25.0
                hyp_pnl_pct = ((current - open_price) / open_price * 100) if open_price else 0

                pick["current_price"] = round(current, 2)
                pick["open_price"] = round(open_price, 2)
                pick["prev_close"] = round(prev_close, 2)
                pick["change_pct"] = round(day_change, 2)
                pick["hypothetical_pnl"] = round(hyp_pnl, 2)
                pick["hypothetical_value"] = round(hyp_value, 2)
                pick["hypothetical_pnl_pct"] = round(hyp_pnl_pct, 2)
                pick["hypothetical_shares"] = round(shares, 4)

        except Exception as e:
            pick["error"] = str(e)

        picks.append(pick)

    # Sort by hypothetical P&L descending
    picks.sort(key=lambda x: x.get("hypothetical_pnl", 0), reverse=True)

    # Build top 5 scenarios from signal data
    buckets = brief.get("buckets", {})
    quotes_data = brief.get("quotes", {})
    oil_score = buckets.get("OIL_SUPPLY", {}).get("score", 0)
    oil_price = quotes_data.get("CL=F", {}).get("price", 0)
    shipping_score = buckets.get("SHIPPING", {}).get("score", 0)
    geo_score = buckets.get("GEOPOLITICAL", {}).get("score", 0)
    vix = brief.get("fear_greed", {}).get("vix", 0)

    scenarios = [
        {
            "rank": 1,
            "name": "Hormuz Stays Closed (4+ weeks)",
            "probability": 40,
            "impact": "Oil $120-150, GUSH +50-80%, tankers ATH, airlines -20%",
            "action": "Hold GUSH/UCO, add FRO. Hedge with TLT.",
            "color": "red",
        },
        {
            "rank": 2,
            "name": "Escalation — US/Israel Strikes Iran",
            "probability": 25,
            "impact": "Oil $130+, VIX 40+, S&P -5-8%, gold $5500+",
            "action": "All-in energy + defense + gold. Add SQQQ for tech short.",
            "color": "red",
        },
        {
            "rank": 3,
            "name": "Stalemate — Slow Grind (2-4 weeks)",
            "probability": 20,
            "impact": f"Oil $95-110, vol elevated (VIX {vix:.0f}+), sector rotation",
            "action": "Hold current positions. Watch for breakout above $110.",
            "color": "yellow",
        },
        {
            "rank": 4,
            "name": "Partial De-escalation — Hormuz Reopens",
            "probability": 10,
            "impact": "Oil drops to $80-85, GUSH -25%, airlines +10%, VIX <22",
            "action": "EXIT leveraged oil immediately. Rotate to defensives.",
            "color": "green",
        },
        {
            "rank": 5,
            "name": "Full Ceasefire / Peace Deal",
            "probability": 5,
            "impact": "Oil $70-75, GUSH -40%+, full risk-on rally, VIX <18",
            "action": "EXIT all war trades. Buy QQQ/tech dip.",
            "color": "green",
        },
    ]

    return {
        "picks": picks,
        "scenarios": scenarios,
        "brief_timestamp": brief.get("timestamp_utc", ""),
        "signal_summary": {
            "oil_score": oil_score,
            "oil_price": oil_price,
            "shipping_score": shipping_score,
            "geo_score": geo_score,
            "vix": vix,
        },
        "timestamp_utc": _utc_now_iso(),
    }


@app.get("/api/whatif-scores")
def whatif_scores():
    """Return learner quality scores for what-if picks."""
    scores_path = REPO_ROOT / "data" / "whatif_learning" / "pick_quality_scores.json"
    if not scores_path.exists():
        return {"scores": {}, "status": "no data yet", "timestamp_utc": _utc_now_iso()}
    try:
        data = json.loads(scores_path.read_text())
        return {**data, "timestamp_utc": _utc_now_iso()}
    except Exception as e:
        return {"scores": {}, "error": str(e), "timestamp_utc": _utc_now_iso()}


frontend_dist = Path(__file__).parent.parent / "frontend" / "out"

@app.get("/trading")
def serve_trading():
    """Serve the trading dashboard page."""
    trading_path = frontend_dist / "trading.html"
    if trading_path.exists():
        return FileResponse(str(trading_path), media_type="text/html")
    return JSONResponse({"error": "trading page not found"}, status_code=404)

if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8501)

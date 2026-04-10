#!/usr/bin/env python3
"""
Market research ingestion (daily bars) for Global Sentinel.

Supports:
- Alpaca daily bars for equities/ETFs (and limited crypto support)
- FRED series for macro/rates/credit data
- FMP historical prices for FX/commodities/index proxies
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from src.utils.rate_limiter import get_limiter, retry_with_backoff

from . import warehouse


def _default_repo_root() -> Path:
    return Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[3]))


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def load_env(repo_root: Optional[Path] = None) -> None:
    root = repo_root or _default_repo_root()
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=str(env_path), override=False)


def _jitter_sleep(base: float) -> None:
    time.sleep(base + random.random() * 0.25)


def _http_get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    limiter_key: str,
    max_rpm: int,
    timeout: float = 30.0,
) -> Any:
    limiter = get_limiter(limiter_key, max_rpm=max_rpm)
    if not limiter.acquire(timeout=timeout):
        raise RuntimeError("rate_limiter_timeout")

    def _do() -> Any:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    return retry_with_backoff(_do, max_retries=5, base_delay=1.0, max_delay=30.0)


def fetch_alpaca_asset_universe(*, base_url: str = "https://paper-api.alpaca.markets", timeout: float = 30.0) -> List[Dict[str, Any]]:
    load_env()
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("missing ALPACA_API_KEY/ALPACA_SECRET_KEY")

    url = f"{base_url.rstrip('/')}/v2/assets"
    params = {"status": "active", "asset_class": "us_equity"}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    data = _http_get_json(url, headers=headers, params=params, limiter_key=f"alpaca:{key}", max_rpm=180, timeout=timeout)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def fetch_alpaca_stock_daily_bars(
    symbol: str,
    *,
    start: date,
    end: date,
    adjustment: str = "split",
    timeframe: str = "1Day",
    base_url: str = "https://data.alpaca.markets",
    timeout: float = 30.0,
) -> List[Dict[str, Any]]:
    load_env()
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("missing ALPACA_API_KEY/ALPACA_SECRET_KEY")

    url = f"{base_url.rstrip('/')}/v2/stocks/{symbol}/bars"
    params = {
        "start": f"{start.isoformat()}T00:00:00Z",
        "end": f"{end.isoformat()}T23:59:59Z",
        "timeframe": timeframe,
        "limit": 10000,
        "adjustment": adjustment,
    }
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    data = _http_get_json(url, headers=headers, params=params, limiter_key=f"alpaca:{key}", max_rpm=180, timeout=timeout)
    bars = data.get("bars") if isinstance(data, dict) else None
    out: List[Dict[str, Any]] = []
    if not isinstance(bars, list):
        return out

    for b in bars:
        if not isinstance(b, dict):
            continue
        ts = str(b.get("t") or "")
        d = ts[:10] if ts else ""
        if not d:
            continue
        out.append(
            {
                "date": d,
                "open": float(b.get("o") or 0.0),
                "high": float(b.get("h") or 0.0),
                "low": float(b.get("l") or 0.0),
                "close": float(b.get("c") or 0.0),
                "volume": int(b.get("v") or 0),
                "vwap": float(b.get("vw") or 0.0) if b.get("vw") is not None else None,
                "source": "alpaca",
                "asset_class": "equity",
            }
        )
    return out


def fetch_fred_series(
    series_id: str,
    start: str = None,
    end: str = None,
    *,
    timeout: float = 30.0,
) -> List[Dict[str, Any]]:
    load_env()
    api_key = os.environ.get("FRED_API_KEY", "")
    limiter_key = f"fred:{api_key or 'anonymous'}"
    url = "https://api.stlouisfed.org/fred/series/observations"
    params: Dict[str, Any] = {
        "series_id": series_id,
        "file_type": "json",
    }
    if api_key:
        params["api_key"] = api_key
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end

    data = _http_get_json(url, params=params, limiter_key=limiter_key, max_rpm=120, timeout=timeout)
    obs = data.get("observations") if isinstance(data, dict) else None
    out: List[Dict[str, Any]] = []
    if not isinstance(obs, list):
        return out
    for o in obs:
        if not isinstance(o, dict):
            continue
        d = str(o.get("date") or "").strip()
        v = str(o.get("value") or "").strip()
        if not d or not v or v == ".":
            continue
        try:
            val = float(v)
        except Exception:
            continue
        out.append(
            {
                "date": d,
                "open": val,
                "high": val,
                "low": val,
                "close": val,
                "volume": 0,
                "vwap": None,
                "source": "fred",
                "asset_class": "macro",
            }
        )
    return out


def fetch_fmp_historical(symbol: str, start: str, end: str, *, timeout: float = 30.0) -> List[Dict[str, Any]]:
    load_env()
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        raise RuntimeError("missing FMP_API_KEY")
    limiter_key = f"fmp:{api_key}"

    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
    params = {"from": start, "to": end, "apikey": api_key}
    data = _http_get_json(url, params=params, limiter_key=limiter_key, max_rpm=300, timeout=timeout)
    hist = data.get("historical") if isinstance(data, dict) else None
    out: List[Dict[str, Any]] = []
    if not isinstance(hist, list):
        return out
    for h in hist:
        if not isinstance(h, dict):
            continue
        d = str(h.get("date") or "").strip()
        if not d:
            continue
        out.append(
            {
                "date": d,
                "open": float(h.get("open") or 0.0),
                "high": float(h.get("high") or 0.0),
                "low": float(h.get("low") or 0.0),
                "close": float(h.get("close") or 0.0),
                "volume": int(h.get("volume") or 0),
                "vwap": None,
                "source": "fmp",
                "asset_class": "unknown",
            }
        )
    out.sort(key=lambda x: x["date"])
    return out


def _date_range_for_years(years: int) -> Tuple[date, date]:
    end = _utc_today()
    start = end - timedelta(days=int(years * 365.25))
    return start, end


@dataclass(frozen=True)
class IngestResult:
    symbol: str
    asset_class: str
    source: str
    operation: str
    status: str
    bars_written: int
    first_bar_date: str = ""
    last_bar_date: str = ""
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "source": self.source,
            "operation": self.operation,
            "status": self.status,
            "bars_written": self.bars_written,
            "first_bar_date": self.first_bar_date,
            "last_bar_date": self.last_bar_date,
            "error": self.error,
        }


def backfill_symbol(
    symbol: str,
    asset_class: str,
    years: int = 20,
    *,
    source: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = repo_root or _default_repo_root()
    load_env(root)
    operation = "backfill"
    started = datetime.now(timezone.utc).isoformat()
    src = source or ("fred" if asset_class == "macro" else "alpaca")
    try:
        start_dt, end_dt = _date_range_for_years(years)
        if src == "fred":
            rows = fetch_fred_series(symbol, start=start_dt.isoformat(), end=end_dt.isoformat())
        elif src == "fmp":
            rows = fetch_fmp_historical(symbol, start=start_dt.isoformat(), end=end_dt.isoformat())
        else:
            rows = fetch_alpaca_stock_daily_bars(symbol, start=start_dt, end=end_dt)
        bars_written = warehouse.write_bars(symbol, rows, asset_class=asset_class, source=src, repo_root=root)
        mf = warehouse.get_manifest(symbol, repo_root=root)
        warehouse.update_manifest(symbol, repo_root=root, last_backfill_at=_utc_today().isoformat(), last_error=None)
        completed = datetime.now(timezone.utc).isoformat()
        warehouse.append_ingest_log(
            warehouse.IngestLogEntry(
                symbol=symbol,
                operation=operation,
                started_at=started,
                completed_at=completed,
                bars_written=bars_written,
                status="ok",
                detail="",
            ),
            repo_root=root,
        )
        return IngestResult(
            symbol=symbol,
            asset_class=asset_class,
            source=src,
            operation=operation,
            status="ok",
            bars_written=bars_written,
            first_bar_date=str(mf.get("first_bar_date") or ""),
            last_bar_date=str(mf.get("last_bar_date") or ""),
        ).as_dict()
    except Exception as e:
        completed = datetime.now(timezone.utc).isoformat()
        msg = str(e)
        mf = warehouse.get_manifest(symbol, repo_root=root)
        err_count = int(mf.get("error_count") or 0) + 1
        warehouse.update_manifest(symbol, repo_root=root, error_count=err_count, last_error=msg)
        warehouse.append_ingest_log(
            warehouse.IngestLogEntry(
                symbol=symbol,
                operation=operation,
                started_at=started,
                completed_at=completed,
                bars_written=0,
                status="error",
                detail=msg[:500],
            ),
            repo_root=root,
        )
        return IngestResult(
            symbol=symbol,
            asset_class=asset_class,
            source=src,
            operation=operation,
            status="error",
            bars_written=0,
            error=msg,
        ).as_dict()


def incremental_update(
    symbol: str,
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = repo_root or _default_repo_root()
    load_env(root)
    operation = "incremental"
    started = datetime.now(timezone.utc).isoformat()
    mf = warehouse.get_manifest(symbol, repo_root=root)
    if not mf:
        return {
            "symbol": symbol,
            "status": "error",
            "error": "missing_manifest",
            "operation": operation,
        }
    asset_class = str(mf.get("asset_class") or "unknown")
    src = str(mf.get("source") or "unknown")
    last = str(mf.get("last_bar_date") or "")
    if not last:
        # fallback to full backfill when unknown
        return backfill_symbol(symbol, asset_class, years=20, source=src, repo_root=root)

    try:
        last_dt = datetime.fromisoformat(last).date()
        start_dt = last_dt + timedelta(days=1)
        end_dt = _utc_today()
        if start_dt > end_dt:
            return {
                "symbol": symbol,
                "status": "ok",
                "operation": operation,
                "bars_written": 0,
                "note": "already_up_to_date",
            }

        if src == "fred":
            rows = fetch_fred_series(symbol, start=start_dt.isoformat(), end=end_dt.isoformat())
        elif src == "fmp":
            rows = fetch_fmp_historical(symbol, start=start_dt.isoformat(), end=end_dt.isoformat())
        else:
            rows = fetch_alpaca_stock_daily_bars(symbol, start=start_dt, end=end_dt)

        bars_written = warehouse.write_bars(symbol, rows, asset_class=asset_class, source=src, repo_root=root)
        completed = datetime.now(timezone.utc).isoformat()
        warehouse.update_manifest(symbol, repo_root=root, last_incremental_at=completed, last_error=None)
        warehouse.append_ingest_log(
            warehouse.IngestLogEntry(
                symbol=symbol,
                operation=operation,
                started_at=started,
                completed_at=completed,
                bars_written=bars_written,
                status="ok",
                detail="",
            ),
            repo_root=root,
        )
        mf2 = warehouse.get_manifest(symbol, repo_root=root)
        return IngestResult(
            symbol=symbol,
            asset_class=asset_class,
            source=src,
            operation=operation,
            status="ok",
            bars_written=bars_written,
            first_bar_date=str(mf2.get("first_bar_date") or ""),
            last_bar_date=str(mf2.get("last_bar_date") or ""),
        ).as_dict()
    except Exception as e:
        completed = datetime.now(timezone.utc).isoformat()
        msg = str(e)
        err_count = int(mf.get("error_count") or 0) + 1
        warehouse.update_manifest(symbol, repo_root=root, error_count=err_count, last_error=msg)
        warehouse.append_ingest_log(
            warehouse.IngestLogEntry(
                symbol=symbol,
                operation=operation,
                started_at=started,
                completed_at=completed,
                bars_written=0,
                status="error",
                detail=msg[:500],
            ),
            repo_root=root,
        )
        return IngestResult(
            symbol=symbol,
            asset_class=asset_class,
            source=src,
            operation=operation,
            status="error",
            bars_written=0,
            error=msg,
        ).as_dict()

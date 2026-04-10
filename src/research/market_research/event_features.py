#!/usr/bin/env python3
"""
Event feature matrix (daily) for Global Sentinel market research.

Outputs (under repo_root/data/event_features/):
  - daily_features.parquet (preferred) or daily_features.jsonl.gz (fallback)
  - regime_tags.parquet (preferred) or regime_tags.jsonl.gz (fallback)

This module keeps dependencies light. If pyarrow is available, Parquet is used.
Otherwise, a JSONL+gzip fallback is used.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from dotenv import load_dotenv

from src.utils.rate_limiter import get_limiter, retry_with_backoff


DEFAULT_FRED_SERIES: Dict[str, str] = {
    # Vol / risk
    "vix": "VIXCLS",
    # Rates
    "ust10y": "DGS10",
    "ust2y": "DGS2",
    # Broad dollar index proxy (FRED trade-weighted USD)
    "dxy": "DTWEXBGS",
    # Commodities
    "oil_brent": "DCOILBRENTEU",
    "gold": "GOLDAMGBD228NLBM",
    # Equity proxy
    "sp500": "SP500",
    # Credit spreads (ICE BofA option-adjusted spreads)
    "hy_oas": "BAMLH0A0HYM2",
    "ig_oas": "BAMLC0A0CM",
    # Crypto proxy (may not exist for all accounts; failures are tolerated)
    "btc": "CBBTCUSD",
}


def _default_repo_root() -> Path:
    return Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[3]))


def _event_features_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "event_features"


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _pyarrow() -> Any:
    try:
        import pyarrow  # type: ignore
        import pyarrow.parquet  # type: ignore
    except Exception:
        return None
    return pyarrow


def _load_rows_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _write_rows_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")


def _load_rows_parquet(path: Path) -> List[Dict[str, Any]]:
    pa = _pyarrow()
    if not pa or not path.exists():
        return []
    import pyarrow.parquet as pq  # type: ignore

    table = pq.read_table(path)
    return table.to_pylist()


def _write_rows_parquet(path: Path, rows: List[Dict[str, Any]]) -> None:
    pa = _pyarrow()
    if not pa:
        raise RuntimeError("pyarrow is not installed; cannot write parquet")
    import pyarrow.parquet as pq  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="snappy")


def load_daily_features(*, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = repo_root or _default_repo_root()
    d = _event_features_dir(root)
    parquet_path = d / "daily_features.parquet"
    jsonl_path = d / "daily_features.jsonl.gz"
    return _load_rows_parquet(parquet_path) if parquet_path.exists() else _load_rows_jsonl(jsonl_path)


def write_daily_features(rows: List[Dict[str, Any]], *, repo_root: Optional[Path] = None) -> None:
    root = repo_root or _default_repo_root()
    d = _event_features_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    parquet_path = d / "daily_features.parquet"
    jsonl_path = d / "daily_features.jsonl.gz"
    if _pyarrow():
        _write_rows_parquet(parquet_path, rows)
    else:
        _write_rows_jsonl(jsonl_path, rows)


def load_regime_tags(*, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = repo_root or _default_repo_root()
    d = _event_features_dir(root)
    parquet_path = d / "regime_tags.parquet"
    jsonl_path = d / "regime_tags.jsonl.gz"
    return _load_rows_parquet(parquet_path) if parquet_path.exists() else _load_rows_jsonl(jsonl_path)


def write_regime_tags(rows: List[Dict[str, Any]], *, repo_root: Optional[Path] = None) -> None:
    root = repo_root or _default_repo_root()
    d = _event_features_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    parquet_path = d / "regime_tags.parquet"
    jsonl_path = d / "regime_tags.jsonl.gz"
    if _pyarrow():
        _write_rows_parquet(parquet_path, rows)
    else:
        _write_rows_jsonl(jsonl_path, rows)


def load_feature_config(repo_root: Path) -> Dict[str, str]:
    """
    Return the FRED series IDs used to build the feature matrix.

    Stream 2 is intentionally self-contained and does not assume Stream 1
    config files exist yet. If you want to make the series set configurable,
    extend this function to load from a repo config file.
    """
    _ = repo_root
    return dict(DEFAULT_FRED_SERIES)


def load_env(repo_root: Optional[Path] = None) -> None:
    root = repo_root or _default_repo_root()
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=str(env_path), override=False)


def _http_get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    limiter_key: str,
    max_rpm: int,
    timeout: float = 30.0,
) -> Any:
    limiter = get_limiter(limiter_key, max_rpm=max_rpm)
    if not limiter.acquire(timeout=timeout):
        raise RuntimeError("rate_limiter_timeout")

    def _do() -> Any:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # Best-effort backoff; the shared util handles 429s and transient network issues.
    return retry_with_backoff(_do, max_retries=5, base_delay=1.0, max_delay=30.0)


def fetch_fred_series(
    series_id: str,
    start: str = None,
    end: str = None,
    *,
    repo_root: Optional[Path] = None,
    timeout: float = 30.0,
) -> List[Dict[str, Any]]:
    load_env(repo_root)
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


def _series_rows_to_map(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = str(r.get("date") or "").strip()
        if not d:
            continue
        v = r.get("close")
        if v is None:
            v = r.get("open")
        try:
            out[d] = float(v)  # type: ignore[arg-type]
        except Exception:
            continue
    return out


def build_daily_features_from_series(
    *,
    series_by_feature: Dict[str, Dict[str, float]],
) -> List[Dict[str, Any]]:
    """
    Build the daily feature matrix from a set of feature->(date->value) series.
    """
    all_dates: set[str] = set()
    for m in series_by_feature.values():
        all_dates.update(m.keys())
    dates_sorted = sorted(all_dates)

    rows: List[Dict[str, Any]] = []

    # Precompute S&P returns from "sp500" if available
    sp = series_by_feature.get("sp500") or {}
    sp_dates = sorted(sp.keys())
    sp_index = {d: i for i, d in enumerate(sp_dates)}

    def _sp_value(d: str) -> Optional[float]:
        v = sp.get(d)
        return float(v) if v is not None else None

    def _sp_return(d: str, n: int) -> Optional[float]:
        if d not in sp_index:
            return None
        i = sp_index[d]
        j = i - n
        if j < 0:
            return None
        v0 = _sp_value(sp_dates[j])
        v1 = _sp_value(d)
        if v0 is None or v1 is None or v0 == 0:
            return None
        return (v1 / v0) - 1.0

    def _sp_realized_vol_20d(d: str) -> Optional[float]:
        if d not in sp_index:
            return None
        i = sp_index[d]
        if i < 20:
            return None
        # compute 20 daily returns ending at d (inclusive)
        rets: List[float] = []
        for k in range(i - 19, i + 1):
            d1 = sp_dates[k]
            d0 = sp_dates[k - 1]
            v0 = _sp_value(d0)
            v1 = _sp_value(d1)
            if v0 is None or v1 is None or v0 == 0:
                continue
            rets.append((v1 / v0) - 1.0)
        if len(rets) < 10:
            return None
        mu = sum(rets) / len(rets)
        var = sum((x - mu) ** 2 for x in rets) / len(rets)
        return var**0.5

    for d in dates_sorted:
        row: Dict[str, Any] = {"date": d}
        for k, series in series_by_feature.items():
            if k == "sp500":
                continue
            row[k] = series.get(d)

        ust10y = row.get("ust10y")
        ust2y = row.get("ust2y")
        if ust10y is not None and ust2y is not None:
            try:
                row["yield_curve_slope"] = float(ust10y) - float(ust2y)
            except Exception:
                row["yield_curve_slope"] = None
        else:
            row["yield_curve_slope"] = None

        hy = row.get("hy_oas")
        ig = row.get("ig_oas")
        if hy is not None and ig is not None:
            try:
                row["credit_spread_hy_ig"] = float(hy) - float(ig)
            except Exception:
                row["credit_spread_hy_ig"] = None
        else:
            row["credit_spread_hy_ig"] = None

        # Equity-derived
        row["sp500_return_1d"] = _sp_return(d, 1)
        row["sp500_return_20d"] = _sp_return(d, 20)
        row["sp500_realized_vol_20d"] = _sp_realized_vol_20d(d)

        rows.append(row)

    rows.sort(key=lambda x: x["date"])
    return rows


def build_daily_features(
    *,
    start: str = None,
    end: str = None,
    repo_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches macro series and returns a daily feature matrix.
    """
    root = repo_root or _default_repo_root()
    cfg = load_feature_config(root)

    # Fetch series with best-effort failure tolerance
    series_by_feature: Dict[str, Dict[str, float]] = {}
    for feature, series_id in cfg.items():
        try:
            rows = fetch_fred_series(series_id, start=start, end=end, repo_root=root)
            series_by_feature[feature] = _series_rows_to_map(rows)
        except Exception:
            series_by_feature[feature] = {}

    # Ensure all keys exist, even when empty.
    for k in DEFAULT_FRED_SERIES.keys():
        series_by_feature.setdefault(k, {})

    return build_daily_features_from_series(series_by_feature=series_by_feature)


def build_regime_tags_from_features(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Simple v1 regime tags derived from VIX and yield curve.

    This is intentionally lightweight; downstream systems can replace this with
    a learned or event-driven regime classifier.
    """
    tags: List[Dict[str, Any]] = []
    for row in features or []:
        d = str(row.get("date") or "")
        if not d:
            continue
        vix = row.get("vix")
        slope = row.get("yield_curve_slope")

        label = "unknown"
        intensity = 0.0
        try:
            if vix is not None:
                v = float(vix)
                if v >= 30:
                    label = "high_vol"
                    intensity = min(1.0, (v - 20.0) / 20.0)
                elif v >= 20:
                    label = "elevated_vol"
                    intensity = min(1.0, (v - 15.0) / 15.0)
                else:
                    label = "calm"
                    intensity = max(0.0, (v - 10.0) / 10.0)
        except Exception:
            pass

        try:
            if slope is not None and float(slope) < 0:
                label = f"{label}_inversion"
        except Exception:
            pass

        tags.append(
            {
                "date": d,
                "event_label": label,
                "event_intensity": float(intensity),
                "source": "auto",
            }
        )
    tags.sort(key=lambda x: x["date"])
    return tags

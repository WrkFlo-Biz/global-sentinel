#!/usr/bin/env python3
"""
Analog engine (Stream 4) for Global Sentinel market research.

Core job:
- Convert daily macro feature rows into normalized vectors
- Perform similarity search ("what regimes looked like this before?")
- Compute conditional forward return stats from the same feature table

Storage (repo_root/data/analog_library/):
- analog_index.sqlite (fingerprint -> window_start, window_end, symbols)
- analog_vectors.parquet preferred; fallback analog_vectors.jsonl.gz
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_FEATURE_COLUMNS: Tuple[str, ...] = (
    "vix",
    "ust10y",
    "ust2y",
    "yield_curve_slope",
    "dxy",
    "oil_brent",
    "gold",
    "btc",
    "credit_spread_hy_ig",
    "sp500_return_1d",
    "sp500_return_20d",
    "sp500_realized_vol_20d",
)


def _default_repo_root() -> Path:
    return Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[3]))


def _pyarrow() -> Any:
    try:
        import pyarrow  # type: ignore
        import pyarrow.parquet  # type: ignore
    except Exception:
        return None
    return pyarrow


def _analog_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "analog_library"


def _event_features_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "event_features"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        raise ValueError("missing date")
    if len(s) >= 10:
        s10 = s[:10]
        if s10[4] == "-" and s10[7] == "-":
            return s10
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.date().isoformat()


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
    """Best-effort reader for Stream 2 outputs."""
    root = repo_root or _default_repo_root()
    d = _event_features_dir(root)
    parquet_path = d / "daily_features.parquet"
    jsonl_path = d / "daily_features.jsonl.gz"
    return _load_rows_parquet(parquet_path) if parquet_path.exists() else _load_rows_jsonl(jsonl_path)


def load_analog_vectors(*, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = repo_root or _default_repo_root()
    d = _analog_dir(root)
    parquet_path = d / "analog_vectors.parquet"
    jsonl_path = d / "analog_vectors.jsonl.gz"
    return _load_rows_parquet(parquet_path) if parquet_path.exists() else _load_rows_jsonl(jsonl_path)


def write_analog_vectors(rows: List[Dict[str, Any]], *, repo_root: Optional[Path] = None) -> None:
    root = repo_root or _default_repo_root()
    d = _analog_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    parquet_path = d / "analog_vectors.parquet"
    jsonl_path = d / "analog_vectors.jsonl.gz"
    if _pyarrow():
        _write_rows_parquet(parquet_path, rows)
    else:
        _write_rows_jsonl(jsonl_path, rows)


def _fingerprint(window_start: str, window_end: str, columns: Sequence[str]) -> str:
    h = hashlib.sha1()
    h.update(f"{window_start}:{window_end}:{','.join(columns)}".encode("utf-8"))
    return h.hexdigest()[:16]


def rebuild_analog_index(
    *,
    dates: Sequence[str],
    columns: Sequence[str],
    window: int = 20,
    symbols: Optional[Sequence[str]] = None,
    repo_root: Optional[Path] = None,
) -> Path:
    """
    Rebuild `data/analog_library/analog_index.sqlite`.
    """
    root = repo_root or _default_repo_root()
    d = _analog_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    db_path = d / "analog_index.sqlite"

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analog_index (
          fingerprint TEXT PRIMARY KEY,
          window_start TEXT NOT NULL,
          window_end TEXT NOT NULL,
          symbols TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("DELETE FROM analog_index")

    syms = list(symbols) if symbols else []
    syms_json = json.dumps(syms, separators=(",", ":"), ensure_ascii=False)

    for i in range(len(dates)):
        if i < window - 1:
            continue
        w_start = dates[i - (window - 1)]
        w_end = dates[i]
        fp = _fingerprint(w_start, w_end, columns)
        conn.execute(
            "INSERT OR REPLACE INTO analog_index (fingerprint, window_start, window_end, symbols, created_at) VALUES (?, ?, ?, ?, ?)",
            (fp, w_start, w_end, syms_json, _utc_now_iso()),
        )
    conn.commit()
    conn.close()
    return db_path


def _mean_std(values: List[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 1.0
    mu = sum(values) / len(values)
    var = sum((x - mu) ** 2 for x in values) / len(values)
    std = var**0.5
    if std == 0:
        std = 1.0
    return mu, std


def _cosine_similarity(vec: Sequence[float], mat: Sequence[Sequence[float]]) -> List[float]:
    # Pure-python cosine similarity.
    vnorm = sum(x * x for x in vec) ** 0.5
    if vnorm == 0:
        return [0.0 for _ in mat]
    sims: List[float] = []
    for row in mat:
        rnorm = sum(x * x for x in row) ** 0.5
        if rnorm == 0:
            sims.append(0.0)
            continue
        dot = 0.0
        for a, b in zip(vec, row):
            dot += a * b
        sims.append(dot / (vnorm * rnorm))
    return sims


@dataclass(frozen=True)
class AnalogMatch:
    date: str
    similarity: float


@dataclass(frozen=True)
class AnalogModel:
    dates: Tuple[str, ...]
    columns: Tuple[str, ...]
    vectors: Tuple[Tuple[float, ...], ...]  # normalized vectors, aligned to dates
    mean: Dict[str, float]
    std: Dict[str, float]
    rows_by_date: Dict[str, Dict[str, Any]]

    def index_of(self, d: str) -> int:
        dd = _parse_date(d)
        try:
            return self.dates.index(dd)
        except ValueError as e:
            raise KeyError(dd) from e

    def similar_dates(self, query_date: str, *, k: int = 20, min_separation_days: int = 3) -> List[AnalogMatch]:
        qi = self.index_of(query_date)
        vec = self.vectors[qi]
        sims = _cosine_similarity(vec, self.vectors)

        # Exclude itself and enforce a small temporal separation if requested.
        candidates: List[Tuple[int, float]] = []
        for i, s in enumerate(sims):
            if i == qi:
                continue
            if min_separation_days and abs(i - qi) < min_separation_days:
                continue
            candidates.append((i, float(s)))
        candidates.sort(key=lambda x: x[1], reverse=True)
        out: List[AnalogMatch] = []
        for i, s in candidates[: max(0, int(k))]:
            out.append(AnalogMatch(date=self.dates[i], similarity=s))
        return out

    def forward_return(self, d: str, horizon_days: int) -> Optional[float]:
        """
        Forward compounded return using sp500_return_1d series.

        For a row at date d, horizon H computes:
          (Π_{j=1..H} (1 + r_{t+j})) - 1
        """
        if horizon_days <= 0:
            return 0.0
        i = self.index_of(d)
        prod = 1.0
        for j in range(1, horizon_days + 1):
            if i + j >= len(self.dates):
                return None
            d2 = self.dates[i + j]
            r = self.rows_by_date.get(d2, {}).get("sp500_return_1d")
            if r is None:
                return None
            try:
                prod *= 1.0 + float(r)
            except Exception:
                return None
        return prod - 1.0

    def conditional_forward_stats(self, matches: Sequence[AnalogMatch], horizons: Sequence[int]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"count": len(matches), "horizons": {}}
        for h in horizons:
            vals: List[float] = []
            for m in matches:
                fr = self.forward_return(m.date, int(h))
                if fr is None:
                    continue
                vals.append(float(fr))
            if not vals:
                out["horizons"][str(h)] = {"mean": None, "min": None, "max": None, "count": 0}
            else:
                out["horizons"][str(h)] = {
                    "mean": sum(vals) / len(vals),
                    "min": min(vals),
                    "max": max(vals),
                    "count": len(vals),
                }
        return out


def build_analog_model(
    daily_features: List[Dict[str, Any]],
    *,
    columns: Sequence[str] = DEFAULT_FEATURE_COLUMNS,
) -> AnalogModel:
    """
    Convert feature rows to a normalized analog model.
    """
    cols = [str(c) for c in columns]
    rows_by_date: Dict[str, Dict[str, Any]] = {}
    for r in daily_features or []:
        if not isinstance(r, dict):
            continue
        try:
            d = _parse_date(str(r.get("date") or ""))
        except Exception:
            continue
        rows_by_date[d] = dict(r, date=d)

    dates = sorted(rows_by_date.keys())

    # Compute column statistics
    mean: Dict[str, float] = {}
    std: Dict[str, float] = {}
    for c in cols:
        vals: List[float] = []
        for d in dates:
            v = rows_by_date[d].get(c)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except Exception:
                continue
        mu, sd = _mean_std(vals)
        mean[c] = mu
        std[c] = sd

    # Build normalized vectors; fill missing with mean.
    vectors: List[Tuple[float, ...]] = []
    for d in dates:
        row = rows_by_date[d]
        vout: List[float] = []
        for c in cols:
            raw = row.get(c)
            x = mean[c]
            try:
                if raw is not None:
                    x = float(raw)
            except Exception:
                x = mean[c]
            vout.append((x - mean[c]) / std[c])
        vectors.append(tuple(vout))

    return AnalogModel(
        dates=tuple(dates),
        columns=tuple(cols),
        vectors=tuple(vectors),
        mean=mean,
        std=std,
        rows_by_date=rows_by_date,
    )


def build_and_persist_analog_library(
    *,
    repo_root: Optional[Path] = None,
    columns: Sequence[str] = DEFAULT_FEATURE_COLUMNS,
    window: int = 20,
) -> Dict[str, Any]:
    """
    Convenience wrapper used by ops scripts.
    """
    root = repo_root or _default_repo_root()
    features = load_daily_features(repo_root=root)
    model = build_analog_model(features, columns=columns)

    # Persist vectors (date + vector) to analog_vectors.*
    vec_rows: List[Dict[str, Any]] = []
    for d, vec in zip(model.dates, model.vectors):
        vec_rows.append({"date": d, "vector": list(vec), "columns": list(model.columns)})
    write_analog_vectors(vec_rows, repo_root=root)

    idx_path = rebuild_analog_index(dates=model.dates, columns=model.columns, window=window, symbols=[], repo_root=root)

    return {
        "dates": len(model.dates),
        "columns": len(model.columns),
        "analog_vectors_written": len(vec_rows),
        "analog_index_path": str(idx_path),
    }


#!/usr/bin/env python3
"""
Market warehouse (daily bars) for Global Sentinel.

Contract (v1):
- A manifest sqlite DB at `data/market_warehouse/manifest.sqlite`
- Bar storage under `data/market_warehouse/bars/{asset_class}/...`

Preferred format is parquet when `pyarrow` is installed. This repo's default
runtime does not include pandas/pyarrow, so a JSONL+gzip fallback is supported.
"""

from __future__ import annotations

import json
import os
import sqlite3
import gzip
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote


def _default_repo_root() -> Path:
    return Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[3]))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(s: str) -> str:
    # Normalize to YYYY-MM-DD
    s = (s or "").strip()
    if not s:
        raise ValueError("missing date")
    if len(s) >= 10:
        s10 = s[:10]
        # minimal validation
        if s10[4] == "-" and s10[7] == "-":
            return s10
    # Fallback: try ISO parse
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.date().isoformat()


def _symbol_key(symbol: str) -> str:
    # Deterministic, filesystem-safe encoding for symbols like "BTC/USD" or "^GSPC".
    return quote(symbol, safe="").replace("%", "_")


def _warehouse_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "market_warehouse"


def _manifest_path(repo_root: Path) -> Path:
    return _warehouse_dir(repo_root) / "manifest.sqlite"


def _bars_dir(repo_root: Path) -> Path:
    return _warehouse_dir(repo_root) / "bars"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manifest (
          symbol TEXT PRIMARY KEY,
          asset_class TEXT NOT NULL,
          source TEXT NOT NULL,
          first_bar_date TEXT,
          last_bar_date TEXT,
          bar_count INTEGER,
          last_backfill_at TEXT,
          last_incremental_at TEXT,
          error_count INTEGER DEFAULT 0,
          last_error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT,
          operation TEXT,
          started_at TEXT,
          completed_at TEXT,
          bars_written INTEGER,
          status TEXT,
          detail TEXT
        )
        """
    )
    conn.commit()


def _connect(repo_root: Path) -> sqlite3.Connection:
    wh = _warehouse_dir(repo_root)
    wh.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_manifest_path(repo_root)))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _bar_paths(repo_root: Path, symbol: str, asset_class: str) -> Tuple[Path, Path]:
    d = _bars_dir(repo_root) / asset_class
    d.mkdir(parents=True, exist_ok=True)
    key = _symbol_key(symbol)
    parquet_path = d / f"{key}.parquet"
    jsonl_path = d / f"{key}.jsonl.gz"
    return parquet_path, jsonl_path


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


def _pyarrow() -> Any:
    try:
        import pyarrow  # type: ignore
        import pyarrow.parquet  # type: ignore
    except Exception:
        return None
    return pyarrow


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


def read_bars(symbol: str, start: str = None, end: str = None, *, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = repo_root or _default_repo_root()
    conn = _connect(root)
    row = conn.execute("SELECT asset_class FROM manifest WHERE symbol = ?", (symbol,)).fetchone()
    conn.close()
    if not row:
        return []
    asset_class = str(row["asset_class"])
    parquet_path, jsonl_path = _bar_paths(root, symbol, asset_class)
    rows = _load_rows_parquet(parquet_path) if parquet_path.exists() else _load_rows_jsonl(jsonl_path)
    if not rows:
        return []
    start_d = _parse_date(start) if start else None
    end_d = _parse_date(end) if end else None
    out: List[Dict[str, Any]] = []
    for r in rows:
        ds = _parse_date(str(r.get("date") or ""))
        if start_d and ds < start_d:
            continue
        if end_d and ds > end_d:
            continue
        out.append(dict(r, date=ds))
    out.sort(key=lambda x: x["date"])
    return out


def write_bars(symbol: str, rows: List[Dict[str, Any]], asset_class: str, source: str, *, repo_root: Optional[Path] = None) -> int:
    root = repo_root or _default_repo_root()
    if not rows:
        return 0

    # Normalize, de-dup by date, keep latest row per date.
    by_date: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        ds = _parse_date(str(r.get("date") or ""))
        by_date[ds] = dict(r)
        by_date[ds]["date"] = ds

    # Merge with existing file if present
    parquet_path, jsonl_path = _bar_paths(root, symbol, asset_class)
    existing = _load_rows_parquet(parquet_path) if parquet_path.exists() else _load_rows_jsonl(jsonl_path)
    for r in existing:
        try:
            ds = _parse_date(str(r.get("date") or ""))
        except Exception:
            continue
        if ds not in by_date:
            by_date[ds] = dict(r, date=ds)

    merged = list(by_date.values())
    merged.sort(key=lambda x: x["date"])

    # Write best available format
    pa = _pyarrow()
    if pa:
        _write_rows_parquet(parquet_path, merged)
        # If a legacy fallback exists, keep it but do not update it.
    else:
        _write_rows_jsonl(jsonl_path, merged)

    first = merged[0]["date"]
    last = merged[-1]["date"]

    conn = _connect(root)
    cur = conn.execute("SELECT symbol FROM manifest WHERE symbol = ?", (symbol,)).fetchone()
    if cur:
        conn.execute(
            """
            UPDATE manifest
            SET asset_class=?, source=?, first_bar_date=?, last_bar_date=?, bar_count=?
            WHERE symbol=?
            """,
            (asset_class, source, first, last, len(merged), symbol),
        )
    else:
        conn.execute(
            """
            INSERT INTO manifest (symbol, asset_class, source, first_bar_date, last_bar_date, bar_count, last_backfill_at, last_incremental_at, error_count, last_error)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL)
            """,
            (symbol, asset_class, source, first, last, len(merged)),
        )
    conn.commit()
    conn.close()
    return len(merged)


def list_symbols(asset_class: str = None, *, repo_root: Optional[Path] = None) -> List[str]:
    root = repo_root or _default_repo_root()
    conn = _connect(root)
    if asset_class:
        rows = conn.execute("SELECT symbol FROM manifest WHERE asset_class = ? ORDER BY symbol", (asset_class,)).fetchall()
    else:
        rows = conn.execute("SELECT symbol FROM manifest ORDER BY symbol").fetchall()
    conn.close()
    return [str(r["symbol"]) for r in rows]


def get_manifest(symbol: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = repo_root or _default_repo_root()
    conn = _connect(root)
    r = conn.execute("SELECT * FROM manifest WHERE symbol = ?", (symbol,)).fetchone()
    conn.close()
    return dict(r) if r else {}


def update_manifest(symbol: str, *, repo_root: Optional[Path] = None, **fields: Any) -> None:
    root = repo_root or _default_repo_root()
    conn = _connect(root)
    existing = conn.execute("SELECT symbol FROM manifest WHERE symbol = ?", (symbol,)).fetchone()
    if not existing:
        # Minimal insert; fields can override.
        conn.execute(
            """
            INSERT INTO manifest (symbol, asset_class, source, first_bar_date, last_bar_date, bar_count, last_backfill_at, last_incremental_at, error_count, last_error)
            VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, NULL)
            """,
            (symbol, str(fields.get("asset_class") or "unknown"), str(fields.get("source") or "unknown")),
        )

    allowed = {
        "asset_class",
        "source",
        "first_bar_date",
        "last_bar_date",
        "bar_count",
        "last_backfill_at",
        "last_incremental_at",
        "error_count",
        "last_error",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if updates:
        cols = ", ".join(f"{k} = ?" for k in updates.keys())
        vals = list(updates.values()) + [symbol]
        conn.execute(f"UPDATE manifest SET {cols} WHERE symbol = ?", vals)
    conn.commit()
    conn.close()


@dataclass(frozen=True)
class IngestLogEntry:
    symbol: str
    operation: str
    started_at: str
    completed_at: str
    bars_written: int
    status: str
    detail: str = ""


def append_ingest_log(entry: IngestLogEntry, *, repo_root: Optional[Path] = None) -> None:
    root = repo_root or _default_repo_root()
    conn = _connect(root)
    conn.execute(
        """
        INSERT INTO ingest_log (symbol, operation, started_at, completed_at, bars_written, status, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.symbol,
            entry.operation,
            entry.started_at,
            entry.completed_at,
            int(entry.bars_written),
            entry.status,
            entry.detail,
        ),
    )
    conn.commit()
    conn.close()

# Global Market Research System - Architecture Spec

Shared contract for the 4 parallel build streams. Read this first.

## Goal
Continuous backtest + regime-conditioned historical analog engine across
all liquid global markets (equities, futures via proxy, FX, crypto, rates),
feeding actionable "what has this regime done before" signals to the trading
layer and dashboard.

## Filesystem Layout (agreed contract)

```
/opt/global-sentinel/
├── config/universes/
│   ├── us_equities.yaml          # Alpaca-derived US symbols
│   ├── etfs_us.yaml              # Top liquid US ETFs
│   ├── country_etfs.yaml         # Regional proxies
│   ├── global_indexes.yaml       # ^GSPC, ^N225, ^FTSE, ...
│   ├── commodities.yaml          # GCUSD, BZUSD, SIUSD, ...
│   ├── fx_majors.yaml            # EURUSD, GBPUSD, USDJPY, ...
│   ├── fx_emerging.yaml          # USDBRL, USDMXN, USDTRY, ...
│   ├── crypto.yaml               # BTC/USD, ETH/USD, ...
│   └── rates_macro.yaml          # FRED series IDs (DGS10, SOFR, ...)
│
├── data/market_warehouse/
│   ├── manifest.sqlite           # symbol -> last_updated, bar_count, errors
│   └── bars/{asset_class}/{symbol}.parquet
│       # Columns: date, open, high, low, close, volume, vwap, source, asset_class
│
├── data/event_features/
│   ├── daily_features.parquet    # date, feature_1..N (one row per day)
│   └── regime_tags.parquet       # date, event_label, event_intensity
│
├── data/analog_library/
│   ├── analog_index.sqlite       # fingerprint -> window_start, window_end, symbols
│   └── analog_vectors.parquet    # date -> feature vector for similarity search
│
├── src/research/market_research/         # NEW package
│   ├── __init__.py
│   ├── warehouse.py              # read/write interface to store
│   ├── ingestion.py              # FMP/FRED/Alpaca fetchers
│   ├── event_features.py         # build daily feature matrix from bridges
│   ├── analog_engine.py          # similarity search + conditional return stats
│   ├── backtest_runner.py        # walk-forward per symbol
│   └── inference.py              # query interface for dashboard/telegram
│
├── scripts/ops/
│   ├── warehouse_backfill.py     # one-shot full backfill (resumable)
│   ├── warehouse_daily_update.py # incremental daily update
│   ├── build_event_features.py   # rebuild daily feature matrix
│   ├── continuous_backtest_daemon.py  # always-on worker loop
│   └── analog_discovery.py       # nightly analog library rebuild
│
└── logs/market_research/         # all stdout/stderr logs land here
```

## Data Schemas

### bars/{asset_class}/{symbol}.parquet
| Column | Type | Notes |
|---|---|---|
| date | date | UTC date, daily bars |
| open, high, low, close | float64 | prices |
| volume | int64 | shares/contracts |
| vwap | float64 | nullable |
| source | string | "fmp" / "fred" / "alpaca" |
| asset_class | string | "equity" / "etf" / "commodity" / "fx" / "crypto" / "index" / "macro" |

### manifest.sqlite schema
```sql
CREATE TABLE manifest (
  symbol TEXT PRIMARY KEY,
  asset_class TEXT NOT NULL,
  source TEXT NOT NULL,
  first_bar_date DATE,
  last_bar_date DATE,
  bar_count INTEGER,
  last_backfill_at DATETIME,
  last_incremental_at DATETIME,
  error_count INTEGER DEFAULT 0,
  last_error TEXT
);
CREATE TABLE ingest_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT,
  operation TEXT,  -- "backfill" / "incremental"
  started_at DATETIME,
  completed_at DATETIME,
  bars_written INTEGER,
  status TEXT,  -- "ok" / "error" / "rate_limited"
  detail TEXT
);
```

### daily_features.parquet columns
date, vix, ust10y, ust2y, yield_curve_slope, dxy, oil_brent, gold,
btc, sp500_return_1d, sp500_return_20d, sp500_realized_vol_20d,
credit_spread_hy_ig

### regime_tags.parquet columns
date, event_label, event_intensity (0-1), source ("manual" / "auto")

## API / Module Contracts

### warehouse.py
```python
def read_bars(symbol: str, start: str = None, end: str = None) -> list[dict]
def write_bars(symbol: str, rows: list[dict], asset_class: str, source: str) -> int
def list_symbols(asset_class: str = None) -> list[str]
def get_manifest(symbol: str) -> dict
def update_manifest(symbol: str, **fields) -> None
```

### ingestion.py
```python
def fetch_fmp_historical(symbol: str, start: str, end: str) -> list[dict]
def fetch_fred_series(series_id: str, start: str = None) -> list[dict]
def fetch_alpaca_asset_universe() -> list[dict]  # for US equity universe
def backfill_symbol(symbol: str, asset_class: str, years: int = 20) -> dict
def incremental_update(symbol: str) -> dict
```

## Rate Limits & Politeness

- FMP: assume 300 req/min ceiling, batch with token bucket.
- FRED: 120 req/min, safe to parallelize lightly.
- Alpaca: 200 req/min paper/live.
- All ingestion uses `src/utils/rate_limiter.py` token bucket.
- Any 429 response -> exponential backoff with jitter, max 5 retries.
- Write to `logs/market_research/{script_name}.log` with rotating handler.

## Disk Budget

Hard cap: **8 GB** for `data/market_warehouse/` + `data/event_features/` + `data/analog_library/` combined.
Prefer snappy-compressed parquet when available. If the runtime lacks parquet
deps, a JSONL+gzip fallback is permitted for v1.

## Systemd Integration

This repo stores unit templates under `scripts/systemd/`. Install them to
`/etc/systemd/system/` on the VM and enable timers/services there.

## Testing Contract

Before declaring done, each stream must:
1. `python3 -m py_compile` on every file they create.
2. Run a smoke test that fetches at least 1 symbol end-to-end.
3. Emit a JSON summary to `logs/market_research/{stream_name}_build_report.json`:
   ```json
   {"stream": "warehouse", "files_created": [...], "smoke_test": "ok", "notes": "..."}
   ```

## Non-Goals for v1
- No intraday bars (daily only)
- No options chain history (too large)
- No auto-trading from analog signals (read-only output, operator decides)
- No dashboard frontend changes (backend endpoint only)

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.research.market_research.backtest_runner import backtest_close_series, store_backtest_summary


def test_backtest_close_series_produces_metrics():
    close = [100.0 + i for i in range(120)]
    dates = [f"2026-01-{(i%28)+1:02d}" for i in range(120)]

    summary = backtest_close_series(close, dates=dates, strategy="momentum", params={"lookback": 5})
    assert summary.bars == 120
    assert summary.start_date == dates[0]
    assert summary.end_date == dates[-1]
    assert isinstance(summary.total_return, float)
    assert summary.max_drawdown <= 0.0


def test_store_backtest_summary_writes_sqlite(tmp_path: Path):
    close = [100.0 + i for i in range(120)]
    dates = [f"2026-01-{(i%28)+1:02d}" for i in range(120)]
    summary = backtest_close_series(close, dates=dates, strategy="sma_trend", params={"fast": 5, "slow": 10})

    store_backtest_summary(summary, symbol="TEST", repo_root=tmp_path)
    db_path = tmp_path / "data" / "market_warehouse" / "backtest_results.sqlite"
    assert db_path.exists()

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT symbol, strategy, bars FROM backtest_results WHERE symbol = ?", ("TEST",)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "TEST"
    assert row[1] == "sma_trend"
    assert int(row[2]) == 120


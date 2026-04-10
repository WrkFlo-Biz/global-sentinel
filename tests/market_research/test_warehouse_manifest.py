from __future__ import annotations

from pathlib import Path

from src.research.market_research import warehouse


def test_write_and_read_bars_roundtrip(tmp_path: Path):
    rows = [
        {"date": "2026-01-01", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10, "vwap": None},
        {"date": "2026-01-02", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 11, "vwap": None},
    ]
    n = warehouse.write_bars("TEST", rows, asset_class="equity", source="alpaca", repo_root=tmp_path)
    assert n == 2

    out = warehouse.read_bars("TEST", repo_root=tmp_path)
    assert [r["date"] for r in out] == ["2026-01-01", "2026-01-02"]
    assert out[-1]["close"] == 3

    mf = warehouse.get_manifest("TEST", repo_root=tmp_path)
    assert mf["symbol"] == "TEST"
    assert mf["asset_class"] == "equity"
    assert mf["source"] == "alpaca"
    assert mf["bar_count"] == 2
    assert mf["first_bar_date"] == "2026-01-01"
    assert mf["last_bar_date"] == "2026-01-02"


def test_read_bars_applies_date_filters(tmp_path: Path):
    rows = [
        {"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0, "vwap": None},
        {"date": "2026-01-02", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 0, "vwap": None},
        {"date": "2026-01-03", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 0, "vwap": None},
    ]
    warehouse.write_bars("TEST", rows, asset_class="equity", source="alpaca", repo_root=tmp_path)
    out = warehouse.read_bars("TEST", start="2026-01-02", end="2026-01-02", repo_root=tmp_path)
    assert [r["date"] for r in out] == ["2026-01-02"]


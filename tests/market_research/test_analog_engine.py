from __future__ import annotations

from src.research.market_research.analog_engine import (
    DEFAULT_FEATURE_COLUMNS,
    build_analog_model,
)


def _row(date: str, base: float, ret_1d: float) -> dict:
    # Fill required columns with deterministic values.
    r = {"date": date, "sp500_return_1d": ret_1d, "sp500_return_20d": 0.0, "sp500_realized_vol_20d": 0.1}
    for c in DEFAULT_FEATURE_COLUMNS:
        if c in r:
            continue
        r[c] = base
    return r


def test_similarity_prefers_identical_vectors():
    rows = [
        _row("2026-01-01", base=1.0, ret_1d=0.00),
        _row("2026-01-02", base=1.0, ret_1d=0.00),
        _row("2026-01-03", base=9.0, ret_1d=0.02),
    ]
    model = build_analog_model(rows)
    matches = model.similar_dates("2026-01-01", k=1, min_separation_days=0)
    assert matches
    assert matches[0].date == "2026-01-02"
    assert matches[0].similarity > 0.99


def test_forward_return_compounds_returns():
    rows = [
        _row("2026-01-01", base=1.0, ret_1d=0.00),
        _row("2026-01-02", base=1.0, ret_1d=0.01),
        _row("2026-01-03", base=9.0, ret_1d=0.02),
    ]
    model = build_analog_model(rows)
    fr = model.forward_return("2026-01-01", horizon_days=2)
    assert fr is not None
    assert abs(fr - ((1.01 * 1.02) - 1.0)) < 1e-12

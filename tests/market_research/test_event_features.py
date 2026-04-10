from __future__ import annotations

from src.research.market_research.event_features import (
    build_daily_features_from_series,
    build_regime_tags_from_features,
)


def test_build_daily_features_computes_derived_fields():
    # Minimal 21 trading days to cover the 20d return calculation.
    dates = [f"2026-01-{d:02d}" for d in range(1, 26)]
    sp500 = {d: 1000.0 + i * 10.0 for i, d in enumerate(dates)}

    series_by_feature = {
        "sp500": sp500,
        "vix": {d: 18.0 for d in dates},
        "ust10y": {d: 4.0 for d in dates},
        "ust2y": {d: 3.5 for d in dates},
        "hy_oas": {d: 4.2 for d in dates},
        "ig_oas": {d: 1.2 for d in dates},
    }

    rows = build_daily_features_from_series(series_by_feature=series_by_feature)
    assert rows
    r = {row["date"]: row for row in rows}

    d = "2026-01-21"
    assert r[d]["yield_curve_slope"] == 0.5
    assert r[d]["credit_spread_hy_ig"] == 3.0

    # 1d return on an arithmetic +10 series: (1100/1090 - 1)
    d_prev = "2026-01-20"
    assert abs(r[d]["sp500_return_1d"] - ((sp500[d] / sp500[d_prev]) - 1.0)) < 1e-12

    # 20d return should be defined at day 21.
    d_20 = "2026-01-01"
    assert abs(r[d]["sp500_return_20d"] - ((sp500[d] / sp500[d_20]) - 1.0)) < 1e-12

    # realized vol should exist once enough returns are present
    assert r[d]["sp500_realized_vol_20d"] is not None
    assert r[d]["sp500_realized_vol_20d"] >= 0


def test_build_regime_tags_classifies_high_vol_and_inversion():
    features = [
        {"date": "2026-01-01", "vix": 35.0, "yield_curve_slope": -0.25},
        {"date": "2026-01-02", "vix": 15.0, "yield_curve_slope": 0.5},
    ]
    tags = build_regime_tags_from_features(features)
    assert [t["date"] for t in tags] == ["2026-01-01", "2026-01-02"]

    t0 = tags[0]
    assert t0["event_label"].startswith("high_vol")
    assert "inversion" in t0["event_label"]
    assert 0.0 <= t0["event_intensity"] <= 1.0

    t1 = tags[1]
    assert t1["event_label"] == "calm"
    assert 0.0 <= t1["event_intensity"] <= 1.0


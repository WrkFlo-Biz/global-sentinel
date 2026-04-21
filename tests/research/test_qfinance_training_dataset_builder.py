"""Tests for QFinance training dataset builder."""

from src.research.qfinance_training_dataset_builder import QFinanceTrainingDatasetBuilder


def test_qfinance_training_dataset_builder_basic():
    builder = QFinanceTrainingDatasetBuilder()

    encoded_candidates = [
        {
            "symbol": "XOM",
            "sector": "Energy",
            "theme": "energy",
            "base_score": 0.8,
            "event_score": 0.2,
            "quality_score": 0.6,
            "anomaly_score": 0.1,
            "liquidity_score": 0.7,
            "volatility_penalty": 0.5,
            "regime_alignment": 1.0,
            "preopt_feature_score": 0.9,
        }
    ]
    regime_state = {
        "regime_shift_probability": 0.82,
        "macro_state": "inflationary_stress",
        "geopolitical_state": "crisis",
    }
    trade_outcomes = {
        "trades": [
            {
                "symbol": "XOM",
                "trade_executed": True,
                "direction": "long",
                "timestamp_utc": "2026-04-09T10:00:00Z",
                "realized_return_bps": 120.0,
                "fill_rate": 1.0,
                "quantum_influenced": True,
            }
        ]
    }
    research_score = {
        "research_score": 0.71,
        "recommended_influence": "research_positive",
    }

    ds = builder.build(
        encoded_candidates=encoded_candidates,
        regime_state=regime_state,
        trade_outcomes=trade_outcomes,
        research_score=research_score,
    )

    assert ds["row_count"] == 1
    row = ds["rows"][0]
    assert row["symbol"] == "XOM"
    assert row["timestamp_utc"] == "2026-04-09T10:00:00Z"
    assert row["attached_research_score"] == 0.71
    assert row["trade_executed"] is True
    assert row["realized_return_bps"] == 120.0


def test_qfinance_training_dataset_builder_no_match():
    builder = QFinanceTrainingDatasetBuilder()

    ds = builder.build(
        encoded_candidates=[{"symbol": "AAPL", "base_score": 0.5}],
        regime_state={"regime_shift_probability": 0.3},
        trade_outcomes={"trades": [{"symbol": "XOM", "trade_executed": True, "realized_return_bps": 50}]},
    )

    assert ds["row_count"] == 1
    assert ds["rows"][0]["trade_executed"] is None  # no match

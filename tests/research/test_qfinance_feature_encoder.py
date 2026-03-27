"""Tests for QFinance feature encoder."""

from src.research.qfinance_feature_encoder import QFinanceFeatureEncoder


def test_qfinance_feature_encoder_basic():
    encoder = QFinanceFeatureEncoder()

    candidate = {
        "symbol": "XOM",
        "theme": "energy",
        "sector": "Energy",
        "score": 0.8,
        "event_score": 0.2,
        "quality_score": 0.6,
        "anomaly_score": 0.1,
    }
    regime_state = {
        "regime_shift_probability": 0.82,
        "macro_state": "inflationary_stress",
        "geopolitical_state": "crisis",
    }
    market_micro = {
        "XOM": {"adv_shares": 15000000, "sigma_daily": 0.025},
    }

    out = encoder.encode_candidate(
        candidate=candidate,
        regime_state=regime_state,
        market_microstructure=market_micro,
    )

    assert out["symbol"] == "XOM"
    assert out["theme"] == "energy"
    assert out["liquidity_score"] > 0.0
    assert "preopt_feature_score" in out
    assert out["regime_alignment"] > 0.0  # energy + crisis + inflationary


def test_qfinance_feature_encoder_universe():
    encoder = QFinanceFeatureEncoder()

    candidates = [
        {"symbol": "XOM", "theme": "energy", "sector": "Energy", "score": 0.5},
        {"symbol": "NVDA", "theme": "ai", "sector": "Technology", "score": 0.9},
    ]
    regime_state = {
        "regime_shift_probability": 0.3,
        "macro_state": "growth",
        "geopolitical_state": "monitoring",
    }
    market_micro = {
        "XOM": {"adv_shares": 15000000, "sigma_daily": 0.025},
        "NVDA": {"adv_shares": 30000000, "sigma_daily": 0.035},
    }

    rows = encoder.encode_universe(
        candidate_universe=candidates,
        regime_state=regime_state,
        market_microstructure=market_micro,
    )

    assert len(rows) == 2
    # NVDA should rank higher in growth regime with higher base score
    assert rows[0]["symbol"] == "NVDA"

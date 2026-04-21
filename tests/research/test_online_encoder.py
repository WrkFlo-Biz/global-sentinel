"""Tests for OnlineWeightedFeatureEncoder."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from src.research.online_weighted_feature_encoder import (
    OnlineWeightedFeatureEncoder,
    BASELINE_WEIGHTS,
)


@pytest.fixture
def tmp_state(tmp_path):
    state = {
        "schema_version": "qfinance_online_learning_state.v1",
        "version": 1,
        "weights": {
            "base_score": 0.40,
            "event_score": 0.25,
            "quality_score": 0.10,
            "anomaly_score": 0.05,
            "liquidity_score": 0.10,
            "regime_alignment": 0.20,
            "volatility_penalty": -0.10,
        },
        "update_stats": {"updates_applied": 0},
        "guardrails": {"max_abs_weight_step": 0.05},
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    return p


@pytest.fixture
def sample_candidate():
    return {"symbol": "XOM", "score": 0.8, "event_score": 0.6, "quality_score": 0.7,
            "anomaly_score": 0.3, "sector": "Energy", "theme": "energy"}


@pytest.fixture
def regime():
    return {"regime_shift_probability": 0.4, "macro_state": "inflationary_stress", "geopolitical_state": "heightened"}


@pytest.fixture
def micro():
    return {"XOM": {"adv_shares": 10_000_000, "sigma_daily": 0.02}}


def test_baseline_fallback(tmp_path, sample_candidate, regime, micro):
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_path / "nonexistent.json")
    assert enc.weights == BASELINE_WEIGHTS
    result = enc.encode_candidate(candidate=sample_candidate, regime_state=regime, market_microstructure=micro)
    assert "preopt_feature_score" in result
    assert result["_encoder_type"] == "online_weighted"


def test_learned_weights_loaded(tmp_state, sample_candidate, regime, micro):
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    assert enc.weights["base_score"] == 0.40
    assert enc.weights["event_score"] == 0.25
    assert enc.version == 1


def test_encode_uses_learned_weights(tmp_state, sample_candidate, regime, micro):
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    result = enc.encode_candidate(candidate=sample_candidate, regime_state=regime, market_microstructure=micro)
    # Score should differ from baseline due to different weights
    baseline_enc = OnlineWeightedFeatureEncoder(state_path=Path("/nonexistent"))
    baseline_result = baseline_enc.encode_candidate(candidate=sample_candidate, regime_state=regime, market_microstructure=micro)
    assert result["preopt_feature_score"] != baseline_result["preopt_feature_score"]


def test_encode_universe_sorted(tmp_state, regime, micro):
    candidates = [
        {"symbol": "XOM", "score": 0.3, "event_score": 0.2, "theme": "energy"},
        {"symbol": "AAPL", "score": 0.9, "event_score": 0.8, "theme": "tech"},
    ]
    micro_full = {"XOM": {"adv_shares": 10_000_000, "sigma_daily": 0.02},
                  "AAPL": {"adv_shares": 50_000_000, "sigma_daily": 0.03}}
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    results = enc.encode_universe(candidate_universe=candidates, regime_state=regime, market_microstructure=micro_full)
    assert len(results) == 2
    assert results[0]["preopt_feature_score"] >= results[1]["preopt_feature_score"]


def test_dual_run(tmp_state, regime, micro):
    candidates = [
        {"symbol": "XOM", "score": 0.8, "event_score": 0.6, "theme": "energy"},
        {"symbol": "AAPL", "score": 0.7, "event_score": 0.5, "theme": "tech"},
    ]
    micro_full = {"XOM": {"adv_shares": 10_000_000, "sigma_daily": 0.02},
                  "AAPL": {"adv_shares": 50_000_000, "sigma_daily": 0.03}}
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    comparison = enc.dual_run(candidate_universe=candidates, regime_state=regime, market_microstructure=micro_full)
    assert comparison["schema_version"] == "dual_run_comparison.v1"
    assert comparison["candidate_count"] == 2
    assert "XOM" in comparison["score_deltas"]
    assert comparison["not_for_direct_execution"] is True


def test_update_weights_success(tmp_state):
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    ok, reason = enc.update_weights({"base_score": 0.42})  # delta 0.02 < 0.05
    assert ok
    assert "updated" in reason
    assert enc.weights["base_score"] == 0.42
    assert enc.version == 2


def test_update_weights_step_too_large(tmp_state):
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    ok, reason = enc.update_weights({"base_score": 0.80})  # delta 0.40 > 0.05
    assert not ok
    assert "step_too_large" in reason


def test_frozen_blocks_update(tmp_state):
    enc = OnlineWeightedFeatureEncoder(state_path=tmp_state)
    enc.freeze()
    assert enc.is_frozen
    ok, reason = enc.update_weights({"base_score": 0.42})
    assert not ok
    assert reason == "encoder_frozen"
    enc.unfreeze()
    ok, _ = enc.update_weights({"base_score": 0.42})
    assert ok

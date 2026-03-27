"""Tests for quantum request building and classical baseline."""

from src.research.build_quantum_request import QuantumRequestBuilder
from src.research.classical_optimizer_baseline import ClassicalOptimizerBaseline
from src.packets.quantum_optimization_request import QuantumOptimizationRequest


SAMPLE_CANDIDATES = [
    {"symbol": "XOM", "score": 0.8, "sector": "Energy"},
    {"symbol": "CVX", "score": 0.75, "sector": "Energy"},
    {"symbol": "LMT", "score": 0.7, "sector": "Defense"},
    {"symbol": "RTX", "score": 0.65, "sector": "Defense"},
    {"symbol": "GLD", "score": 0.6, "sector": "Commodities"},
    {"symbol": "TLT", "score": 0.55, "sector": "Bonds"},
    {"symbol": "UNH", "score": 0.5, "sector": "Healthcare"},
]


def test_build_quantum_request_basic():
    builder = QuantumRequestBuilder()
    req = builder.build(
        package_id="pkg-test-001",
        objective={"type": "hedge_basket_optimization", "target": "maximize_risk_adjusted_protection"},
        constraints={"max_names": 8, "impact_budget_bps": 15.0},
        candidate_universe=SAMPLE_CANDIDATES[:2],
        market_microstructure={
            "XOM": {"adv_shares": 15000000, "sigma_daily": 0.025},
            "LMT": {"adv_shares": 2000000, "sigma_daily": 0.022},
        },
        runtime_flags={"shadow_mode_only": True},
        time_window_state={"window": "overnight", "impact_multiplier": 1.0, "confidence_multiplier": 1.0},
        regime_state={"regime_shift_probability": 0.62, "macro_state": "mixed", "geopolitical_state": "heightened"},
        provenance={"source_snapshot_id": "snap-001"},
    )

    assert req.package_id == "pkg-test-001"
    assert req.objective["type"] == "hedge_basket_optimization"
    assert len(req.candidate_universe) == 2
    assert req.market_microstructure["XOM"]["adv_shares"] == 15000000
    assert req.runtime_flags["shadow_mode_only"] is True
    assert req.request_id.startswith("qreq-")


def test_classical_baseline_ranks_by_score():
    req = QuantumOptimizationRequest(
        request_id="test-001",
        package_id="pkg-test",
        timestamp_utc="2026-03-07T00:00:00Z",
        runtime_flags={"shadow_mode_only": True},
        time_window_state={},
        regime_state={},
        objective={"type": "hedge_basket_optimization"},
        constraints={"max_names": 4, "max_sector_weight": 0.5},
        candidate_universe=SAMPLE_CANDIDATES,
        market_microstructure={},
        provenance={},
    )

    baseline = ClassicalOptimizerBaseline()
    result = baseline.run(req)

    assert result.success is True
    assert result.solver == "classical_baseline"
    assert len(result.ranked_solutions) <= 4
    assert result.objective_value > 0

    # Top score should be first
    scores = [float(s.get("score", 0)) for s in result.ranked_solutions]
    assert scores == sorted(scores, reverse=True)


def test_classical_baseline_sector_cap():
    req = QuantumOptimizationRequest(
        request_id="test-002",
        package_id="pkg-test",
        timestamp_utc="2026-03-07T00:00:00Z",
        runtime_flags={},
        time_window_state={},
        regime_state={},
        objective={"type": "hedge_basket_optimization"},
        constraints={"max_names": 5, "max_sector_weight": 0.4},
        candidate_universe=SAMPLE_CANDIDATES,
        market_microstructure={},
        provenance={},
    )

    result = ClassicalOptimizerBaseline().run(req)

    # With max_sector_weight=0.4 and max_names=5, sector cap = int(5*0.4) = 2
    sectors = [s.get("sector") for s in result.ranked_solutions]
    for sector in set(sectors):
        assert sectors.count(sector) <= 2, f"Sector {sector} exceeded cap"

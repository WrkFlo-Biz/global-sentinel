"""Tests for ExperimentalSIPQCMCLane — research-only SIPQC Monte Carlo lane."""
from __future__ import annotations

import pytest

from src.research.experimental_sipqc_mc_lane import ExperimentalSIPQCMCLane


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lane() -> ExperimentalSIPQCMCLane:
    return ExperimentalSIPQCMCLane({"n_scenarios": 200, "seed": 42})


@pytest.fixture
def regime_state() -> dict:
    return {"regime": "risk_off", "regime_shift_probability": 0.7}


@pytest.fixture
def candidates() -> list:
    return [
        {"weight": 0.4, "expected_return": 0.02},
        {"weight": 0.3, "expected_return": -0.01},
        {"weight": 0.3, "expected_return": 0.005},
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScenarioGeneration:
    def test_scenario_generation_count(
        self, lane: ExperimentalSIPQCMCLane, regime_state: dict
    ) -> None:
        scenarios = lane.generate_scenarios(regime_state, n=50)
        assert len(scenarios) == 50

    def test_scenario_fields_present(
        self, lane: ExperimentalSIPQCMCLane, regime_state: dict
    ) -> None:
        scenarios = lane.generate_scenarios(regime_state, n=5)
        required = {"scenario_id", "regime_label", "return_shift", "vol_multiplier", "correlation_shift"}
        for sc in scenarios:
            assert required.issubset(sc.keys()), f"Missing fields in scenario: {required - sc.keys()}"


class TestSimulation:
    def test_simulate_outcomes_length(
        self,
        lane: ExperimentalSIPQCMCLane,
        regime_state: dict,
        candidates: list,
    ) -> None:
        scenarios = lane.generate_scenarios(regime_state, n=30)
        outcomes = lane.simulate_outcomes(candidates, scenarios)
        assert len(outcomes) == 30

    def test_outcome_fields(
        self,
        lane: ExperimentalSIPQCMCLane,
        regime_state: dict,
        candidates: list,
    ) -> None:
        scenarios = lane.generate_scenarios(regime_state, n=5)
        outcomes = lane.simulate_outcomes(candidates, scenarios)
        for o in outcomes:
            assert "pnl" in o
            assert "drawdown" in o
            assert "scenario_id" in o


class TestRiskMetrics:
    def test_risk_metrics_keys(
        self,
        lane: ExperimentalSIPQCMCLane,
        regime_state: dict,
        candidates: list,
    ) -> None:
        scenarios = lane.generate_scenarios(regime_state)
        outcomes = lane.simulate_outcomes(candidates, scenarios)
        metrics = lane.compute_risk_metrics(outcomes)
        expected_keys = {"var", "cvar", "max_drawdown", "tail_probability", "scenario_diversity"}
        assert expected_keys.issubset(metrics.keys())

    def test_var_less_than_cvar(
        self,
        lane: ExperimentalSIPQCMCLane,
        regime_state: dict,
        candidates: list,
    ) -> None:
        scenarios = lane.generate_scenarios(regime_state)
        outcomes = lane.simulate_outcomes(candidates, scenarios)
        metrics = lane.compute_risk_metrics(outcomes)
        # CVaR (expected shortfall) should be >= VaR by definition
        assert metrics["var"] <= metrics["cvar"] + 1e-12


class TestRunPipeline:
    def test_run_pipeline(
        self,
        lane: ExperimentalSIPQCMCLane,
        regime_state: dict,
        candidates: list,
    ) -> None:
        result = lane.run(candidates, regime_state)
        assert result["schema_version"] == "experimental_sipqc_mc.v1"
        assert "risk_metrics" in result
        assert "scenarios_generated" in result
        assert result["scenarios_generated"] == 200  # matches fixture n_scenarios

    def test_research_only_flags(
        self,
        lane: ExperimentalSIPQCMCLane,
        regime_state: dict,
        candidates: list,
    ) -> None:
        result = lane.run(candidates, regime_state)
        assert result["research_only"] is True
        assert result["not_for_direct_execution"] is True
        # Class-level flags
        assert lane.research_only is True
        assert lane.not_for_direct_execution is True


class TestReproducibility:
    def test_seed_reproducibility(self, regime_state: dict) -> None:
        lane_a = ExperimentalSIPQCMCLane({"n_scenarios": 50, "seed": 99})
        lane_b = ExperimentalSIPQCMCLane({"n_scenarios": 50, "seed": 99})

        scenarios_a = lane_a.generate_scenarios(regime_state)
        scenarios_b = lane_b.generate_scenarios(regime_state)

        assert len(scenarios_a) == len(scenarios_b)
        for a, b in zip(scenarios_a, scenarios_b):
            assert a["return_shift"] == b["return_shift"]
            assert a["vol_multiplier"] == b["vol_multiplier"]
            assert a["correlation_shift"] == b["correlation_shift"]

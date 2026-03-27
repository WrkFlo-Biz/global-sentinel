#!/usr/bin/env python3
"""Tests for ResearchEvalHarness."""
from __future__ import annotations

import sys
import os
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from research.research_eval_harness import ResearchEvalHarness


def _make_passing_change() -> dict:
    """Return a research change dict that passes all dimensions."""
    return {
        "research_score": 0.8,
        "sharpe_ratio": 1.5,
        "win_rate": 0.65,
        "slippage_adjusted_delta": 0.02,
        "not_for_direct_execution": True,
        "bounded_secondary_signal_only": True,
        "training_dataset_hash": "sha256:abc123",
        "code_version": "v4.1.0",
        "drift_score": 0.05,
        "elapsed_seconds": 60.0,
        "parent_artifact_ids": ["artifact-001"],
        "source_packet_hashes": ["hash-aaa"],
    }


class TestResearchEvalHarnessFullPass:
    """Test that a fully valid research change passes."""

    def test_full_pass(self):
        harness = ResearchEvalHarness()
        result = harness.evaluate(_make_passing_change())

        assert result["schema_version"] == "research_eval_harness.v1"
        assert result["overall_pass"] is True
        assert result["overall_score"] > 0.5
        assert len(result["blocking_failures"]) == 0
        assert "timestamp_utc" in result
        assert isinstance(result["dimension_scores"], dict)
        assert len(result["dimension_scores"]) == 7

    def test_all_dimension_scores_above_threshold(self):
        harness = ResearchEvalHarness()
        result = harness.evaluate(_make_passing_change())
        for dim, score in result["dimension_scores"].items():
            assert score >= 0.5, f"{dim} scored {score}, expected >= 0.5"


class TestPredictiveQualityFailure:
    """Test predictive_quality dimension failures."""

    def test_missing_all_metrics(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        del change["research_score"]
        del change["sharpe_ratio"]
        del change["win_rate"]
        result = harness.evaluate(change)

        assert result["overall_pass"] is False
        assert result["dimension_scores"]["predictive_quality"] == 0.0
        assert any("predictive_quality" in f for f in result["blocking_failures"])

    def test_low_sharpe_only(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["research_score"] = 0.1
        change["sharpe_ratio"] = 0.2
        change["win_rate"] = 0.1
        result = harness.evaluate(change)

        assert result["dimension_scores"]["predictive_quality"] < 0.5


class TestExecutionRealismFailure:

    def test_missing_slippage_adjusted_delta(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        del change["slippage_adjusted_delta"]
        result = harness.evaluate(change)

        assert result["overall_pass"] is False
        assert result["dimension_scores"]["execution_realism"] == 0.0

    def test_negative_slippage_adjusted_delta(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["slippage_adjusted_delta"] = -0.005
        result = harness.evaluate(change)

        assert result["dimension_scores"]["execution_realism"] == 0.0


class TestSafetyComplianceFailure:

    def test_missing_safety_flags(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["not_for_direct_execution"] = False
        change["bounded_secondary_signal_only"] = False
        result = harness.evaluate(change)

        assert result["dimension_scores"]["safety_compliance"] == 0.0
        assert result["overall_pass"] is False

    def test_one_flag_missing(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["not_for_direct_execution"] = False
        result = harness.evaluate(change)

        assert result["dimension_scores"]["safety_compliance"] == 0.5


class TestReproducibilityFailure:

    def test_missing_hash_and_version(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        del change["training_dataset_hash"]
        del change["code_version"]
        result = harness.evaluate(change)

        assert result["dimension_scores"]["reproducibility"] == 0.0
        assert result["overall_pass"] is False

    def test_empty_code_version(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["code_version"] = ""
        result = harness.evaluate(change)

        assert result["dimension_scores"]["reproducibility"] == 0.5


class TestDriftSensitivityFailure:

    def test_high_drift(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["drift_score"] = 0.20  # above default max 0.15
        result = harness.evaluate(change)

        assert result["dimension_scores"]["drift_sensitivity"] == 0.0
        assert result["overall_pass"] is False

    def test_drift_at_boundary(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["drift_score"] = 0.15  # exactly at max
        result = harness.evaluate(change)

        assert result["dimension_scores"]["drift_sensitivity"] == 0.0


class TestComputeCostFailure:

    def test_exceeds_max_compute(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["elapsed_seconds"] = 500.0  # above default 300
        result = harness.evaluate(change)

        assert result["dimension_scores"]["compute_cost"] == 0.0
        assert result["overall_pass"] is False

    def test_custom_max_compute(self):
        harness = ResearchEvalHarness(max_compute_seconds=60.0)
        change = _make_passing_change()
        change["elapsed_seconds"] = 30.0
        result = harness.evaluate(change)

        assert result["dimension_scores"]["compute_cost"] == 0.5


class TestLineageCompletenessFailure:

    def test_missing_lineage(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        del change["parent_artifact_ids"]
        del change["source_packet_hashes"]
        result = harness.evaluate(change)

        assert result["dimension_scores"]["lineage_completeness"] == 0.0
        assert result["overall_pass"] is False

    def test_empty_lists(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["parent_artifact_ids"] = []
        change["source_packet_hashes"] = []
        result = harness.evaluate(change)

        assert result["dimension_scores"]["lineage_completeness"] == 0.0


class TestCustomThresholds:

    def test_stricter_threshold_causes_failure(self):
        harness = ResearchEvalHarness(
            min_thresholds={"safety_compliance": 1.0}
        )
        change = _make_passing_change()
        change["not_for_direct_execution"] = False  # safety = 0.5
        result = harness.evaluate(change)

        assert result["overall_pass"] is False
        assert any("safety_compliance" in f for f in result["blocking_failures"])

    def test_relaxed_threshold_allows_pass(self):
        harness = ResearchEvalHarness(
            min_thresholds={"safety_compliance": 0.0}
        )
        change = _make_passing_change()
        change["not_for_direct_execution"] = False
        change["bounded_secondary_signal_only"] = False
        result = harness.evaluate(change)

        # safety is 0.0 but threshold is 0.0 so it should not block
        assert "safety_compliance" not in " ".join(result["blocking_failures"])


class TestEdgeCases:

    def test_empty_research_change(self):
        harness = ResearchEvalHarness()
        result = harness.evaluate({})

        assert result["overall_pass"] is False
        assert result["overall_score"] == 0.0
        assert len(result["blocking_failures"]) > 0

    def test_non_numeric_fields_handled(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["research_score"] = "not_a_number"
        change["drift_score"] = "bad"
        change["elapsed_seconds"] = None
        result = harness.evaluate(change)

        assert result["overall_pass"] is False
        assert len(result["warnings"]) > 0

    def test_custom_weights_affect_overall_score(self):
        harness_equal = ResearchEvalHarness()
        harness_heavy = ResearchEvalHarness(
            weights={"predictive_quality": 10.0}
        )
        change = _make_passing_change()
        change["research_score"] = 1.0
        change["sharpe_ratio"] = 2.0
        change["win_rate"] = 1.0

        result_equal = harness_equal.evaluate(change)
        result_heavy = harness_heavy.evaluate(change)

        # Heavily weighting predictive_quality (which is 1.0) should push overall up
        assert result_heavy["overall_score"] >= result_equal["overall_score"]

    def test_negative_elapsed_seconds(self):
        harness = ResearchEvalHarness()
        change = _make_passing_change()
        change["elapsed_seconds"] = -10
        result = harness.evaluate(change)

        assert result["dimension_scores"]["compute_cost"] == 0.0

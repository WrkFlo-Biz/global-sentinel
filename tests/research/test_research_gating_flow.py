from __future__ import annotations

import pytest

from src.research.attach_research_score_to_snapshot import attach_research_score
from src.research.research_promotion_readiness_report import ResearchPromotionReadinessReport
from src.research.research_score_writer import build_research_score
from src.research.run_research_score_pipeline import (
    enrich_evaluation_with_validation,
    research_score_allows_downstream_flow,
)
from src.research.update_research_model_weights import ResearchModelWeightUpdater


def _evaluation() -> dict:
    return {
        "request_id": "req-1",
        "package_id": "pkg-1",
        "quantum_overlap_score": 0.8,
        "quantum_directional_score": 0.75,
        "classical_overlap_score": 0.5,
        "classical_directional_score": 0.5,
        "quantum_realized_return_bps_sum": 220.0,
        "classical_realized_return_bps_sum": 100.0,
    }


def test_research_score_validation_blocks_influence_without_walk_forward():
    evaluation = {
        **_evaluation(),
        "executed_trade_count": 8,
        "walk_forward_validation": {"passed": False, "folds_run": 1},
    }

    score = build_research_score(evaluation, require_validation=True)

    assert score["research_score"] == 0.0
    assert score["raw_research_score"] > 0.0
    assert score["recommended_influence"] == "none"
    assert score["validation"]["passed"] is False
    assert research_score_allows_downstream_flow(score) is False


def test_attach_research_score_rejects_blocked_score():
    snapshot = {"snapshot_id": "snap-1", "runtime_flags": {}}
    blocked_score = build_research_score(
        {
            **_evaluation(),
            "executed_trade_count": 2,
            "walk_forward_validation": {"passed": False, "folds_run": 0},
        },
        require_validation=True,
    )

    with pytest.raises(ValueError, match="research_score_validation_failed"):
        attach_research_score(snapshot, blocked_score)


def test_weight_updater_rejects_dataset_without_walk_forward_validation():
    updater = ResearchModelWeightUpdater()
    state = {
        "weights": {"base_score": 0.35},
        "guardrails": {"max_abs_weight_step": 0.05, "min_weight": -1.0, "max_weight": 1.0},
        "update_stats": {"updates_applied": 100},
    }
    labeled_dataset = {
        "rows": [
            {"symbol": "XOM", "alpha_label": "positive", "base_score": 0.8},
            {"symbol": "CVX", "alpha_label": "negative", "base_score": 0.2},
        ],
        "walk_forward_validation": {"passed": False, "folds_run": 1},
    }

    with pytest.raises(ValueError, match="labeled_dataset_failed_guardrails_or_walk_forward_validation"):
        updater.update(state=state, labeled_dataset=labeled_dataset)


def test_promotion_readiness_blocks_guardrail_and_walk_forward_failures():
    report = ResearchPromotionReadinessReport().build(
        [
            {
                "signal_name": "sig-ready",
                "recommendation": "promote",
                "criteria_results": [],
                "guardrail_result": {"passed": True},
                "walk_forward_validation": {"passed": True},
            },
            {
                "signal_name": "sig-blocked",
                "recommendation": "promote",
                "criteria_results": [],
                "guardrail_result": {"passed": False},
                "walk_forward_validation": {"passed": False},
            },
        ]
    )

    assert report["signals_ready"] == ["sig-ready"]
    assert "sig-blocked" in report["signals_blocked"]
    assert report["guardrail_blocked_count"] == 1
    assert report["walk_forward_blocked_count"] == 1


def test_pipeline_enrichment_carries_dataset_counts_and_walk_forward():
    evaluation = _evaluation()
    trade_outcomes = {
        "trades": [
            {"symbol": "XOM", "trade_executed": True},
            {"symbol": "CVX", "trade_executed": False},
            {"symbol": "SLB", "trade_executed": True},
        ],
        "walk_forward_validation": {"passed": True, "folds_run": 3},
    }

    enriched = enrich_evaluation_with_validation(evaluation, trade_outcomes)

    assert enriched["trade_count"] == 3
    assert enriched["executed_trade_count"] == 2
    assert enriched["walk_forward_validation"]["folds_run"] == 3

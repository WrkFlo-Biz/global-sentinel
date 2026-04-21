from __future__ import annotations

from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop
from src.research.alpha_candidate_labeler import AlphaCandidateLabeler
from src.research.merge_trade_outcomes_from_router import normalize_trade_rows
from src.research.qfinance_training_dataset_builder import QFinanceTrainingDatasetBuilder
from src.research.research_drift_report import ResearchDriftReport
from src.research.research_eval_harness import ResearchEvalHarness
from src.research.research_promotion_readiness_report import ResearchPromotionReadinessReport
from src.research.training.crisis_training_dataset import normalize_validation_labels
from src.research.update_research_model_weights import ResearchModelWeightUpdater


def test_normalize_validation_labels_derives_decay_metrics():
    labels = normalize_validation_labels(
        {
            "mfe_pct": 2.0,
            "mae_pct": -0.8,
            "time_to_edge_minutes": 180,
            "fill_slippage_bps": 18.0,
            "fill_rate": 0.62,
            "realized_return_bps": 45.0,
        }
    )

    assert labels["fill_quality_score"] is not None
    assert labels["realized_edge_capture_ratio"] is not None
    assert labels["adverse_excursion_ratio"] is not None
    assert labels["edge_decay_score"] is not None
    assert labels["edge_decay_weight"] is not None
    assert labels["edge_decay_label"] in {
        "durable_edge",
        "mild_decay",
        "decaying_edge",
        "broken_edge",
    }
    assert labels["time_to_edge_bucket"] == "moderate"


def test_merge_trade_outcomes_from_router_preserves_decay_and_fill_metrics():
    rows = normalize_trade_rows(
        {
            "trades": [
                {
                    "symbol": "XOM",
                    "status": "filled",
                    "trade_executed": True,
                    "direction": "long",
                    "realized_return_bps": 80.0,
                    "mfe_pct": 1.5,
                    "mae_pct": -0.4,
                    "time_to_edge_minutes": 35.0,
                    "fill_rate": 0.94,
                    "realized_slippage_bps": 4.0,
                    "metadata": {"strategy": "shipping_rate_explosion"},
                }
            ]
        }
    )

    assert len(rows) == 1
    row = rows[0].to_dict()
    assert row["fill_quality_score"] is not None
    assert row["realized_edge_capture_ratio"] is not None
    assert row["edge_decay_score"] is not None
    assert row["edge_decay_weight"] is not None
    assert row["sample_weight"] is not None
    assert row["metadata"]["strategy"] == "shipping_rate_explosion"


def test_alpha_candidate_labeler_downweights_decaying_edges():
    labeled = AlphaCandidateLabeler().label_rows(
        {
            "rows": [
                {
                    "symbol": "FAST",
                    "realized_return_bps": 120.0,
                    "mfe_pct": 1.4,
                    "mae_pct": -0.2,
                    "time_to_edge_minutes": 10.0,
                    "fill_rate": 0.99,
                    "realized_slippage_bps": 2.0,
                },
                {
                    "symbol": "DECAY",
                    "realized_return_bps": 120.0,
                    "mfe_pct": 5.0,
                    "mae_pct": -2.0,
                    "time_to_edge_minutes": 320.0,
                    "fill_rate": 0.45,
                    "realized_slippage_bps": 28.0,
                },
            ]
        }
    )

    rows = {row["symbol"]: row for row in labeled["rows"]}
    assert rows["FAST"]["alpha_label"] == "strong_positive"
    assert rows["DECAY"]["alpha_label"] != "strong_positive"
    assert rows["FAST"]["sample_weight"] > rows["DECAY"]["sample_weight"]
    assert rows["FAST"]["edge_decay_score"] < rows["DECAY"]["edge_decay_score"]


def test_qfinance_training_dataset_builder_carries_decay_weighting_fields():
    ds = QFinanceTrainingDatasetBuilder().build(
        encoded_candidates=[{"symbol": "XOM", "base_score": 0.8, "event_score": 0.3}],
        regime_state={"regime_shift_probability": 0.7, "macro_state": "inflation", "geopolitical_state": "crisis"},
        trade_outcomes={
            "trades": [
                {
                    "symbol": "XOM",
                    "trade_executed": True,
                    "direction": "long",
                    "realized_return_bps": 70.0,
                    "mfe_pct": 1.2,
                    "mae_pct": -0.3,
                    "fill_rate": 0.91,
                    "realized_slippage_bps": 5.0,
                    "time_to_edge_minutes": 25.0,
                }
            ]
        },
    )

    row = ds["rows"][0]
    assert row["fill_quality_score"] is not None
    assert row["edge_decay_score"] is not None
    assert row["edge_decay_weight"] is not None
    assert row["edge_retention_score"] is not None
    assert row["sample_weight"] is not None
    assert row["time_to_edge_bucket"] == "fast"


def test_weight_updater_downweights_decaying_edges():
    updater = ResearchModelWeightUpdater()
    state = {
        "weights": {"base_score": 0.0, "fill_quality_score": 0.0, "edge_retention_score": 0.0, "time_to_edge_score": 0.0},
        "guardrails": {"max_abs_weight_step": 0.05, "min_weight": -1.0, "max_weight": 1.0},
        "update_stats": {"updates_applied": 100},
    }
    durable_dataset = {
        "rows": [
            {
                "symbol": "FAST",
                "timestamp_utc": "2026-04-09T14:00:00+00:00",
                "alpha_label": "positive",
                "base_score": 1.0,
                "fill_quality_score": 0.95,
                "edge_retention_score": 0.9,
                "time_to_edge_score": 0.95,
                "edge_decay_score": 0.1,
                "edge_decay_weight": 0.95,
                "sample_weight": 1.0,
            }
        ],
        "walk_forward_validation": {"passed": True, "folds_run": 3},
    }
    decaying_dataset = {
        "rows": [
            {
                "symbol": "SLOW",
                "timestamp_utc": "2026-04-09T15:00:00+00:00",
                "alpha_label": "positive",
                "base_score": 1.0,
                "fill_quality_score": 0.35,
                "edge_retention_score": 0.2,
                "time_to_edge_score": 0.1,
                "edge_decay_score": 0.8,
                "edge_decay_weight": 0.3,
                "sample_weight": 1.0,
            }
        ],
        "walk_forward_validation": {"passed": True, "folds_run": 3},
    }

    durable_state = updater.update(state=state, labeled_dataset=durable_dataset, learning_rate=0.01)
    decaying_state = updater.update(state=state, labeled_dataset=decaying_dataset, learning_rate=0.01)

    assert durable_state["weights"]["fill_quality_score"] > decaying_state["weights"]["fill_quality_score"]
    assert durable_state["weights"]["edge_retention_score"] > decaying_state["weights"]["edge_retention_score"]
    assert durable_state["update_stats"]["last_average_edge_decay_score"] < decaying_state["update_stats"]["last_average_edge_decay_score"]
    assert decaying_state["update_stats"]["last_decaying_edge_ratio"] == 1.0


def test_research_drift_report_tracks_decay_adjusted_drift():
    report = ResearchDriftReport().build(
        {
            "weights": {"base_score": 0.1},
            "update_stats": {
                "updates_applied": 10,
                "last_average_edge_decay_score": 0.2,
                "last_decaying_edge_ratio": 0.1,
            },
        },
        {
            "weights": {"base_score": 0.16},
            "update_stats": {
                "updates_applied": 11,
                "last_average_edge_decay_score": 0.6,
                "last_decaying_edge_ratio": 0.5,
                "last_average_fill_quality_score": 0.44,
                "last_average_time_to_edge_score": 0.3,
            },
        },
    )

    assert report["max_abs_drift"] == 0.06
    assert report["decay_adjusted_drift"] > report["max_abs_drift"]
    assert report["edge_decay_summary"]["current_decaying_edge_ratio"] == 0.5


def test_promotion_readiness_blocks_on_global_edge_decay_pressure():
    report = ResearchPromotionReadinessReport().build(
        [
            {
                "signal_name": "sig-ready",
                "recommendation": "promote",
                "criteria_results": [],
                "guardrail_result": {"passed": True},
                "walk_forward_validation": {"passed": True},
            }
        ],
        drift_report={
            "max_abs_drift": 0.08,
            "decay_adjusted_drift": 0.22,
            "edge_decay_summary": {
                "current_decaying_edge_ratio": 0.55,
                "edge_decay_pressure": 0.3,
            },
        },
    )

    assert report["signals_ready"] == []
    assert report["signals_blocked"] == ["sig-ready"]
    assert "decay_adjusted_drift" in report["blockers"][0]["blockers"]
    assert "decaying_edge_ratio" in report["blockers"][0]["blockers"]


def test_research_eval_harness_penalizes_decay_adjusted_drift_and_execution_decay():
    result = ResearchEvalHarness().evaluate(
        {
            "research_score": 0.8,
            "sharpe_ratio": 1.2,
            "win_rate": 0.61,
            "slippage_adjusted_delta": 0.01,
            "fill_quality_score": 0.35,
            "time_to_edge_minutes": 280.0,
            "edge_decay_score": 0.8,
            "decaying_edge_ratio": 0.6,
            "decay_adjusted_drift": 0.12,
            "not_for_direct_execution": True,
            "bounded_secondary_signal_only": True,
            "training_dataset_hash": "sha256:abc",
            "code_version": "v1",
            "elapsed_seconds": 40.0,
            "parent_artifact_ids": ["a1"],
            "source_packet_hashes": ["p1"],
        }
    )

    assert result["dimension_scores"]["execution_realism"] < 0.5
    assert result["dimension_scores"]["drift_sensitivity"] < 0.5


def test_adaptive_feedback_loop_summarizes_edge_decay_metrics():
    stats = AdaptiveFeedbackLoop._summarize_trade_bucket(
        [
            {
                "pnl": 120.0,
                "win": True,
                "mfe_pct": 2.0,
                "mae_pct": -0.4,
                "time_to_edge_minutes": 25.0,
                "fill_rate": 0.95,
                "realized_slippage_bps": 4.0,
                "realized_return_bps": 110.0,
                "edge_decay_score": 0.15,
            },
            {
                "pnl": -20.0,
                "win": False,
                "mfe_pct": 0.5,
                "mae_pct": -1.0,
                "time_to_edge_minutes": 310.0,
                "fill_rate": 0.4,
                "realized_slippage_bps": 22.0,
                "realized_return_bps": -25.0,
                "edge_decay_score": 0.82,
            },
        ]
    )

    assert stats["avg_fill_quality_score"] is not None
    assert stats["avg_edge_decay_score"] is not None
    assert stats["avg_edge_capture_ratio"] is not None
    assert stats["decaying_edge_ratio"] > 0.0

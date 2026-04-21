from __future__ import annotations

from src.reports.research_drift_report import ResearchDriftReport
from src.research.update_research_model_weights import FEATURE_KEYS, ResearchModelWeightUpdater


def _base_state() -> dict:
    return {
        "weights": {key: 0.4 for key in FEATURE_KEYS},
        "guardrails": {
            "max_abs_weight_step": 0.05,
            "min_weight": -1.0,
            "max_weight": 1.0,
            "drift_guardrails": {
                "concept_drift_trigger_score": 0.45,
                "concept_drift_critical_score": 0.70,
                "max_decaying_edge_ratio": 0.40,
                "max_average_edge_decay_score": 0.50,
                "min_average_fill_quality_score": 0.55,
                "min_average_time_to_edge_score": 0.50,
                "max_step_scale_under_drift": 0.45,
                "max_drift_down_weight_step": 0.03,
            },
        },
        "update_stats": {
            "updates_applied": 5,
            "last_average_edge_decay_score": 0.20,
            "last_decaying_edge_ratio": 0.10,
        },
    }


def _high_drift_dataset() -> dict:
    rows = []
    for idx in range(5):
        row = {
            "symbol": "DECAY",
            "timestamp_utc": f"2026-04-09T15:0{idx}:00+00:00",
            "alpha_label": "positive",
            "fill_quality_score": 0.25,
            "edge_decay_score": 0.90,
            "edge_decay_weight": 0.25,
            "time_to_edge_score": 0.15,
            "sample_weight": 1.0,
        }
        for key in FEATURE_KEYS:
            row[key] = 1.0
        rows.append(row)
    return {
        "rows": rows,
        "walk_forward_validation": {"passed": True, "folds_run": 3},
    }


def test_weight_updater_triggers_bounded_drift_down_weighting():
    updater = ResearchModelWeightUpdater()
    prior_state = _base_state()
    new_state = updater.update(
        state=prior_state,
        labeled_dataset=_high_drift_dataset(),
        learning_rate=1.0,
    )

    monitor = new_state["drift_monitor"]
    stats = new_state["update_stats"]

    assert monitor["triggered"] is True
    assert monitor["down_weighting_multiplier"] < 1.0
    assert monitor["step_scale_under_drift"] <= prior_state["guardrails"]["drift_guardrails"]["max_step_scale_under_drift"]
    assert stats["last_auto_down_weighting_applied"] is True
    assert stats["last_max_total_weight_step"] <= 0.08

    max_total_step = float(stats["last_max_total_weight_step"])
    for key in FEATURE_KEYS:
        delta = abs(new_state["weights"][key] - prior_state["weights"][key])
        assert delta <= max_total_step + 1e-9


def test_research_drift_report_includes_actionable_threshold_signals():
    updater = ResearchModelWeightUpdater()
    previous_state = _base_state()
    current_state = updater.update(
        state=previous_state,
        labeled_dataset=_high_drift_dataset(),
        learning_rate=1.0,
    )

    report = ResearchDriftReport().build(previous_state, current_state)

    assert report["schema_version"] == "research_drift_report.v2"
    assert report["drift_thresholds"]["concept_drift_trigger_score"] == 0.45
    assert report["actionability"]["state"] in {"degrade", "critical"}
    assert report["actionability"]["auto_down_weighting_applied"] is True
    assert report["actionability"]["pipeline_flags"]["concept_drift_triggered"] is True
    assert report["actionability"]["pipeline_flags"]["breach_count"] >= 2
    assert report["breached_signals"]

    signal_names = {row["name"] for row in report["drift_signals"]}
    assert {"avg_edge_decay_score", "decaying_edge_ratio", "avg_fill_quality_score", "avg_time_to_edge_score", "concept_drift_score"} <= signal_names

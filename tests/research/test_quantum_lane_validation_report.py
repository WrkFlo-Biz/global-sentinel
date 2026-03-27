"""Tests for the bounded quantum lane validation report."""

from __future__ import annotations

from pathlib import Path

from src.reports.quantum_lane_validation_report import (
    QuantumLaneValidationReportBuilder,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_assess_artifact_payload_flags_missing_fields(tmp_path: Path):
    builder = QuantumLaneValidationReportBuilder(REPO_ROOT, validation_root=tmp_path)

    completeness = builder._assess_artifact_payload(
        {
            "request_id": "x",
            "execution_metadata": {
                "framework_standard": "pyqpanda3",
            },
        }
    )

    assert completeness["complete"] is False
    assert "package_id" in completeness["missing_top_level_fields"]
    assert "backend_type" in completeness["missing_execution_metadata_fields"]


def test_timeout_simulation_writes_complete_partial_artifact(tmp_path: Path):
    builder = QuantumLaneValidationReportBuilder(REPO_ROOT, validation_root=tmp_path)

    timeout_validation = builder._simulate_timeout_and_partial_artifact()

    assert timeout_validation["timeout_observed"] is True
    assert timeout_validation["partial_artifact_completeness"]["complete"] is True
    assert Path(timeout_validation["partial_artifact_path"]).exists()


def test_build_report_captures_policy_registry_and_backend_matrix(
    tmp_path: Path, monkeypatch
):
    builder = QuantumLaneValidationReportBuilder(REPO_ROOT, validation_root=tmp_path)

    monkeypatch.setattr(
        builder,
        "_validate_backend",
        lambda **kwargs: {
            "backend_label": kwargs["backend_label"],
            "status": "success" if kwargs["backend_label"] == "cpuqvm" else "not_requested",
            "metadata_completeness": {"complete": True},
        },
    )
    monkeypatch.setattr(
        builder,
        "_simulate_timeout_and_partial_artifact",
        lambda: {
            "timeout_observed": True,
            "partial_artifact_completeness": {"complete": True},
        },
    )

    report = builder.build(execute_qcloud=False, execute_pilot=False)

    assert report["schema_version"] == "quantum_lane_validation_report.v1"
    assert report["policy_status"]["framework_required"] == "pyqpanda3"
    assert report["policy_status"]["stage_1_active"] is True
    assert report["policy_status"]["stage_2_active"] is False
    assert report["experiment_registry_status"]["loaded"] is True
    assert report["backend_validation"]["cpuqvm"]["status"] == "success"
    assert report["backend_validation"]["qcloud"]["status"] == "not_requested"
    assert report["pilotos_posture"]["status"] == "optional_human_research_interface_only"
    assert report["recommendation"]["backend_matrix"]["cpuqvm"] == "success"

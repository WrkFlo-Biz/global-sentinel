"""Tests for the bounded QPanda3 runtime helper layer."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.research.qpanda3_runtime import (
    QCloudAsyncOrchestrator,
    build_execution_metadata,
    build_lane_settings,
)
from src.research.quantum_experiment_registry import QuantumExperimentRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_lane_settings_from_policy_file():
    policy_path = REPO_ROOT / "config" / "quantum_lane_policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))

    settings = build_lane_settings(policy)

    assert settings.framework_standard == "pyqpanda3"
    assert settings.qpanda2_supported is False
    assert settings.algorithm_package == "pyqpanda-algorithm"
    assert settings.local_backend_type == "cpuqvm"
    assert settings.qcloud_async_enabled is True


def test_build_execution_metadata_includes_framework_backend_and_job_tags():
    policy_path = REPO_ROOT / "config" / "quantum_lane_policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    settings = build_lane_settings(policy)

    metadata = build_execution_metadata(
        settings=settings,
        sdk={
            "framework_version": "3.0.1",
            "algorithm_package_version": "0.9.0",
            "vqnet_available": True,
        },
        provider_name="origin-qcloud",
        backend_name="72-bit",
        shots=1024,
        algorithm_family="qaoa",
        formulation_id="portfolio_optimization",
        async_submission=True,
        hardware_job_id="job-123",
        job_submission_mode="async_poll",
        submitted_at_utc="2026-03-07T20:00:00Z",
        completed_at_utc="2026-03-07T20:05:00Z",
        extra_tags={"artifact_only": True},
    )

    assert metadata["framework_standard"] == "pyqpanda3"
    assert metadata["framework_version"] == "3.0.1"
    assert metadata["backend_type"] == "qcloud"
    assert metadata["provider_name"] == "origin-qcloud"
    assert metadata["hardware_job_id"] == "job-123"
    assert metadata["job_submission_mode"] == "async_poll"
    assert metadata["tags"]["artifact_only"] is True


class _FakeStatus:
    def __init__(self, name: str):
        self.name = name


class _FakeResult:
    def __init__(self):
        self._counts_list = [{"0": 400, "1": 624}]

    def get_counts_list(self):
        return self._counts_list


class _FakeAsyncJob:
    def __init__(self):
        self._statuses = iter([_FakeStatus("QUEUED"), _FakeStatus("RUNNING"), _FakeStatus("FINISHED")])

    def job_id(self):
        return "async-job-42"

    def status(self):
        return next(self._statuses)

    def result(self):
        return _FakeResult()


class _FakeBackend:
    name = "72-bit"

    def run(self, programs, shots, options, enable_binary_encoding, batch_id, task_form):
        assert shots == 512
        assert batch_id == "gs-batch"
        assert task_form == 4
        return _FakeAsyncJob()


def test_qcloud_async_orchestrator_collects_polling_metadata():
    orchestrator = QCloudAsyncOrchestrator(
        poll_interval_seconds=0.0,
        timeout_seconds=30.0,
        sleep_fn=lambda _: None,
    )

    handle = orchestrator.submit(
        backend=_FakeBackend(),
        programs=["prog-1"],
        shots=512,
        options=object(),
        enable_binary_encoding=False,
        batch_id="gs-batch",
        task_form=4,
    )
    result, metadata = orchestrator.collect(handle)

    assert result.get_counts_list()[0]["1"] == 624
    assert metadata["provider_async_submission"] is True
    assert metadata["provider_job_id"] == "async-job-42"
    assert metadata["provider_job_status"] == "FINISHED"
    assert metadata["provider_poll_count"] == 3


def test_quantum_experiment_registry_resolves_known_experiment():
    registry = QuantumExperimentRegistry.load(
        REPO_ROOT / "config" / "quantum_experiment_registry.yaml",
    )

    spec = registry.resolve_for_objective("portfolio_optimization")

    assert spec is not None
    assert spec.problem_family == "portfolio_optimization"
    assert spec.qcloud_support is True
    assert spec.classical_comparator

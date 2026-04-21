#!/usr/bin/env python3
"""Build a research-only validation report for the hardened quantum lane.

This report is intentionally operational and bounded. It validates the deployed
QPanda3 research slice without changing maturity stage, promotion policy, or
execution scope. It can exercise local CPUQVM deterministically and optionally
attempt QCloud/Pilot runs when credentials are present and explicitly allowed.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.packets.quantum_optimization_request import QuantumOptimizationRequest
from src.research.qpanda3_runtime import QCloudAsyncOrchestrator
from src.research.quantum_experiment_registry import QuantumExperimentRegistry
from src.research.quantum_optimization_result_handler import QuantumResultHandler
from src.research.quantum_optimizer_bridge import (
    OriginProviderConfig,
    QuantumOptimizerBridge,
    load_lane_policy,
)

SCHEMA_VERSION = "quantum_lane_validation_report.v1"
REQUIRED_ARTIFACT_FIELDS = (
    "request_id",
    "package_id",
    "solver",
    "success",
    "diagnostics",
    "execution_metadata",
    "artifact_only",
    "research_only",
    "not_for_direct_execution",
)
REQUIRED_EXECUTION_METADATA_FIELDS = (
    "framework_standard",
    "backend_type",
    "backend_name",
    "provider_name",
    "shots",
    "algorithm_family",
    "formulation_id",
    "artifact_only",
    "research_only",
    "not_for_direct_execution",
)
DEFAULT_EXPERIMENT_TYPES = (
    "portfolio_optimization",
    "robust_portfolio_design",
    "scenario_allocation",
    "derivative_pricing_research",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_version(package_name: str) -> Optional[str]:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return None


def _result_to_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if is_dataclass(result):
        return asdict(result)
    raise TypeError(f"Unsupported result payload type: {type(result).__name__}")


class _TimeoutStatus:
    name = "RUNNING"


class _NeverCompletesJob:
    """Fake async job used to validate timeout handling without cloud access."""

    def job_id(self) -> str:
        return "sim-timeout-job"

    def status(self):
        return _TimeoutStatus()

    def result(self):  # pragma: no cover - unreachable if timeout works
        raise AssertionError("Timeout simulation should not reach result()")


class _NeverCompletesBackend:
    name = "simulated-qcloud"

    def run(self, *args, **kwargs):
        return _NeverCompletesJob()


class QuantumLaneValidationReportBuilder:
    """Construct an operator-facing validation report for the quantum lane."""

    def __init__(
        self,
        repo_root: Path,
        *,
        validation_root: Optional[Path] = None,
        policy_path: Optional[Path] = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.validation_root = (
            validation_root.resolve()
            if validation_root is not None
            else self.repo_root / "reports" / "operational" / "quantum_validation"
        )
        self.validation_root.mkdir(parents=True, exist_ok=True)
        self.policy_path = policy_path.resolve() if policy_path else (
            self.repo_root / "config" / "quantum_lane_policy.yaml"
        )
        self.policy = load_lane_policy(self.policy_path)
        registry_cfg = (self.policy.get("experiment_registry") or {})
        registry_path = registry_cfg.get("path", "config/quantum_experiment_registry.yaml")
        self.registry_path = Path(str(registry_path))
        if not self.registry_path.is_absolute():
            self.registry_path = (self.repo_root / self.registry_path).resolve()

    def build(
        self,
        *,
        execute_qcloud: bool = False,
        execute_pilot: bool = False,
        experiment_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        registry = self._load_registry()
        environment = self._build_environment_status()
        policy_status = self._build_policy_status()
        backend_results = {
            "cpuqvm": self._validate_backend(
                backend_label="cpuqvm",
                provider_name="origin-local",
                objective_type="portfolio_optimization",
                execute=True,
            ),
            "qcloud": self._validate_backend(
                backend_label="qcloud",
                provider_name="origin-qcloud",
                objective_type="portfolio_optimization",
                execute=execute_qcloud,
            ),
            "pilot": self._validate_backend(
                backend_label="pilot",
                provider_name="origin-pilot",
                objective_type="portfolio_optimization",
                execute=execute_pilot,
            ),
        }
        families = self._validate_experiment_families(
            registry=registry,
            objective_types=experiment_types or list(DEFAULT_EXPERIMENT_TYPES),
        )
        timeout_validation = self._simulate_timeout_and_partial_artifact()

        policy_compliance = self._build_policy_compliance(backend_results, timeout_validation)
        blockers = self._collect_blockers(environment, backend_results, timeout_validation)

        research_backends = self._validate_research_backends()

        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _utc_now_iso(),
            "repo_root": str(self.repo_root),
            "validation_root": str(self.validation_root),
            "environment_status": environment,
            "policy_status": policy_status,
            "experiment_registry_status": self._registry_status(registry),
            "backend_validation": backend_results,
            "research_backends": research_backends,
            "experiment_family_validation": families,
            "timeout_and_partial_artifact_validation": timeout_validation,
            "policy_compliance_assessment": policy_compliance,
            "pilotos_posture": self._pilotos_posture(),
            "blockers": blockers,
            "recommendation": self._build_recommendation(blockers, backend_results),
        }
        return report

    def _validate_research_backends(self) -> Dict[str, Any]:
        """Probe multi-backend orchestrator availability without executing."""
        status: Dict[str, Any] = {"orchestrator_available": False, "backends": {}}
        try:
            from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
            orchestrator = MultiBackendOrchestrator(
                artifact_dir=self.validation_root / "research_backends",
            )
            avail = orchestrator.available_backends()
            status["orchestrator_available"] = True
            status["backends"] = avail
            status["all_available"] = all(v == "available" for v in avail.values())
            status["available_count"] = sum(1 for v in avail.values() if v == "available")
            status["total_count"] = len(avail)
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def _build_environment_status(self) -> Dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "repo_root_present": self.repo_root.exists(),
            "policy_file_present": self.policy_path.exists(),
            "policy_loaded": bool(self.policy),
            "experiment_registry_present": self.registry_path.exists(),
            "package_status": {
                "pyqpanda3": self._package_status_with_shim("pyqpanda3"),
                "pyqpanda-algorithm": self._package_status("pyqpanda-algorithm"),
                "pyvqnet": self._package_status("pyvqnet"),
            },
            "credential_status": {
                "qcloud_api_key_present": bool(os.getenv("ORIGINQ_API_KEY")),
                "qcloud_backend_present": bool(os.getenv("ORIGINQ_BACKEND_NAME")),
                "pilot_url_present": bool(os.getenv("ORIGINQ_PILOT_URL")),
                "pilot_api_key_present": bool(os.getenv("ORIGINQ_PILOT_API_KEY")),
            },
        }

    def _build_policy_status(self) -> Dict[str, Any]:
        framework = self.policy.get("framework", {}) or {}
        lane_rules = self.policy.get("lane_rules", {}) or {}
        maturity = self.policy.get("maturity_stages", {}) or {}
        return {
            "policy_path": str(self.policy_path),
            "framework_required": framework.get("required", "pyqpanda3"),
            "framework_optional": list(framework.get("optional") or []),
            "deprecated_frameworks": list(framework.get("deprecated") or []),
            "artifact_only": bool(lane_rules.get("artifact_only", True)),
            "execution_path_disabled": bool(lane_rules.get("disable_execution_path", True)),
            "shadow_mode_only": bool(lane_rules.get("shadow_mode_only", True)),
            "stage_1_active": bool((maturity.get("stage_1") or {}).get("active", False)),
            "stage_2_active": bool((maturity.get("stage_2") or {}).get("active", False)),
            "stage_3_active": bool((maturity.get("stage_3") or {}).get("active", False)),
        }

    def _load_registry(self) -> Optional[QuantumExperimentRegistry]:
        if not self.registry_path.exists():
            return None
        try:
            return QuantumExperimentRegistry.load(self.registry_path)
        except Exception:
            return None

    def _registry_status(self, registry: Optional[QuantumExperimentRegistry]) -> Dict[str, Any]:
        experiments = [item.to_dict() for item in registry.list_experiments()] if registry else []
        return {
            "path": str(self.registry_path),
            "loaded": registry is not None,
            "experiment_count": len(experiments),
            "experiments": experiments,
        }

    def _package_status(self, package_name: str) -> Dict[str, Any]:
        version = _safe_version(package_name)
        return {
            "installed": version is not None,
            "version": version,
        }

    def _package_status_with_shim(self, package_name: str) -> Dict[str, Any]:
        """Check package status, falling back to import check for shim packages."""
        status = self._package_status(package_name)
        if not status["installed"]:
            try:
                mod = __import__(package_name)
                status["installed"] = True
                status["version"] = getattr(mod, "__version__", "shim")
                status["shim"] = True
            except ImportError:
                pass
        return status

    def _validate_backend(
        self,
        *,
        backend_label: str,
        provider_name: str,
        objective_type: str,
        execute: bool,
    ) -> Dict[str, Any]:
        config = OriginProviderConfig.from_sources(
            provider_override=provider_name,
            shots_override=32,
            policy=self.policy,
        )
        validation = {
            "backend_label": backend_label,
            "provider_requested": provider_name,
            "provider_config": config.redacted(),
            "requested_execution": execute,
        }

        if provider_name == "origin-qcloud" and (not config.api_key or not config.backend_name):
            validation["status"] = "not_configured"
            validation["reason"] = "missing_qcloud_credentials"
            return validation
        if provider_name == "origin-pilot" and (not config.pilot_url or not config.pilot_api_key):
            validation["status"] = "not_configured"
            validation["reason"] = "missing_pilot_credentials"
            return validation
        if not execute:
            validation["status"] = "not_requested"
            validation["reason"] = "execution_flag_disabled"
            return validation

        request = self._build_sample_request(objective_type=objective_type, provider_name=provider_name)
        artifact_dir = self.validation_root / "artifacts" / backend_label
        artifact_dir.mkdir(parents=True, exist_ok=True)
        bridge = QuantumOptimizerBridge(
            artifact_dir=artifact_dir,
            provider_config=config,
            policy=self.policy,
        )
        result = bridge.run(request)
        artifact_path = artifact_dir / f"{request.request_id}.json"
        payload = json.loads(artifact_path.read_text(encoding="utf-8")) if artifact_path.exists() else {}
        completeness = self._assess_artifact_payload(payload)
        diagnostics = dict(payload.get("diagnostics") or result.diagnostics or {})
        validation.update(
            {
                "status": self._backend_status(result, diagnostics, provider_name),
                "artifact_path": str(artifact_path) if artifact_path.exists() else None,
                "fallback_used": bool(getattr(result, "fallback_used", diagnostics.get("fallback_used", False))),
                "solver": getattr(result, "solver", payload.get("solver")),
                "objective_value": getattr(result, "objective_value", payload.get("objective_value")),
                "provider_mode": diagnostics.get("provider_mode"),
                "provider_error_type": diagnostics.get("provider_error_type"),
                "provider_error": diagnostics.get("provider_error"),
                "async_submission": bool(
                    (payload.get("execution_metadata") or {}).get("async_submission", False)
                ),
                "hardware_job_id": (payload.get("execution_metadata") or {}).get("hardware_job_id", ""),
                "metadata_completeness": completeness,
            }
        )
        return validation

    def _validate_experiment_families(
        self,
        *,
        registry: Optional[QuantumExperimentRegistry],
        objective_types: List[str],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for objective_type in objective_types:
            spec = registry.resolve_for_objective(objective_type) if registry else None
            sample = self._build_sample_request(
                objective_type=objective_type,
                provider_name="origin-local",
            )
            results.append(
                {
                    "objective_type": objective_type,
                    "registry_resolved": spec is not None,
                    "registry_entry": spec.to_dict() if spec else None,
                    "sample_request_id": sample.request_id,
                    "sample_candidate_count": len(sample.candidate_universe),
                }
            )
        return results

    def _simulate_timeout_and_partial_artifact(self) -> Dict[str, Any]:
        """Validate timeout handling and partial artifact persistence with fakes."""
        ticks = iter([0.0, 1.0, 2.0])
        orchestrator = QCloudAsyncOrchestrator(
            poll_interval_seconds=0.0,
            timeout_seconds=0.5,
            sleep_fn=lambda _: None,
            monotonic_fn=lambda: next(ticks),
        )
        timeout_observed = False
        timeout_error = ""
        try:
            handle = orchestrator.submit(
                backend=_NeverCompletesBackend(),
                programs=["sim-prog"],
                shots=16,
                options=object(),
                enable_binary_encoding=False,
                batch_id="validation-timeout",
                task_form=4,
            )
            orchestrator.collect(handle)
        except TimeoutError as exc:
            timeout_observed = True
            timeout_error = str(exc)
            job_id = "sim-timeout-job"
        else:  # pragma: no cover - unexpected
            job_id = ""

        handler = QuantumResultHandler(self.repo_root)
        partial_dir = self.validation_root / "partial_artifacts"
        partial_dir.mkdir(parents=True, exist_ok=True)
        handler.artifacts_dir = partial_dir
        partial_payload = {
            "request_id": "validation-timeout",
            "package_id": "quantum-validation",
            "solver": "origin_qcloud_timeout",
            "success": False,
            "ranked_solutions": [],
            "objective_value": 0.0,
            "feasibility": 0.0,
            "diagnostics": {
                "provider_error_type": "TimeoutError",
                "provider_error": timeout_error,
                "execution_metadata": {
                    "framework_standard": "pyqpanda3",
                    "backend_type": "qcloud",
                    "backend_name": "simulated-qcloud",
                    "provider_name": "origin-qcloud",
                    "shots": 16,
                    "algorithm_family": "qaoa",
                    "formulation_id": "portfolio_optimization",
                    "artifact_only": True,
                    "research_only": True,
                    "not_for_direct_execution": True,
                    "hardware_job_id": job_id,
                    "job_submission_mode": "async_poll",
                    "async_submission": True,
                },
            },
            "provenance": {"mode": "timeout_simulation"},
        }
        partial_path = handler.store_result(partial_payload)
        stored_payload = json.loads(partial_path.read_text(encoding="utf-8"))
        return {
            "timeout_observed": timeout_observed,
            "timeout_error": timeout_error,
            "partial_artifact_path": str(partial_path),
            "partial_artifact_completeness": self._assess_artifact_payload(stored_payload),
        }

    def _build_policy_compliance(
        self,
        backend_results: Dict[str, Dict[str, Any]],
        timeout_validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        completeness = [
            item.get("metadata_completeness", {}).get("complete", False)
            for item in backend_results.values()
            if item.get("status") not in {"not_requested", "not_configured"}
        ]
        return {
            "stage_1_only": self._build_policy_status()["stage_1_active"]
            and not self._build_policy_status()["stage_2_active"]
            and not self._build_policy_status()["stage_3_active"],
            "artifact_only_policy": bool((self.policy.get("lane_rules") or {}).get("artifact_only", True)),
            "execution_path_disabled": bool((self.policy.get("lane_rules") or {}).get("disable_execution_path", True)),
            "all_executed_artifacts_metadata_complete": all(completeness) if completeness else True,
            "timeout_partial_artifact_complete": bool(
                timeout_validation.get("partial_artifact_completeness", {}).get("complete", False)
            ),
        }

    def _collect_blockers(
        self,
        environment: Dict[str, Any],
        backend_results: Dict[str, Dict[str, Any]],
        timeout_validation: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        blockers: List[Dict[str, Any]] = []
        if not environment["package_status"]["pyqpanda3"]["installed"]:
            blockers.append(
                {
                    "severity": "high",
                    "category": "environment",
                    "reason": "pyqpanda3_missing",
                }
            )
        for backend_label, result in backend_results.items():
            if result.get("status") == "fallback":
                blockers.append(
                    {
                        "severity": "medium",
                        "category": "backend",
                        "reason": f"{backend_label}_fell_back",
                        "details": result.get("provider_error_type") or result.get("provider_error"),
                    }
                )
            if result.get("status") == "error":
                blockers.append(
                    {
                        "severity": "high",
                        "category": "backend",
                        "reason": f"{backend_label}_error",
                        "details": result.get("provider_error_type") or result.get("provider_error"),
                    }
                )
        if not timeout_validation.get("timeout_observed", False):
            blockers.append(
                {
                    "severity": "medium",
                    "category": "timeout",
                    "reason": "timeout_simulation_not_observed",
                }
            )
        if not timeout_validation.get("partial_artifact_completeness", {}).get("complete", False):
            blockers.append(
                {
                    "severity": "medium",
                    "category": "timeout",
                    "reason": "partial_artifact_incomplete",
                }
            )
        return blockers

    def _build_recommendation(
        self,
        blockers: List[Dict[str, Any]],
        backend_results: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        severe = [item for item in blockers if item["severity"] == "high"]
        return {
            "status": "validated" if not severe else "validation_gaps_present",
            "backend_matrix": {
                key: value.get("status") for key, value in backend_results.items()
            },
            "next_action": (
                "proceed_with_research_only_vm_usage"
                if not severe
                else "fix_high_severity_validation_gap_before_broadening_research_usage"
            ),
        }

    def _pilotos_posture(self) -> Dict[str, Any]:
        return {
            "status": "optional_human_research_interface_only",
            "recommended_uses": [
                "visual_circuit_inspection",
                "interactive_debugging",
                "compilation_sanity_checks",
                "task_status_inspection",
            ],
            "forbidden_uses": [
                "production_runtime_dependency",
                "policy_bypass",
                "artifact_persistence_replacement",
                "execution_path_influence",
            ],
        }

    def _build_sample_request(
        self,
        *,
        objective_type: str,
        provider_name: str,
    ) -> QuantumOptimizationRequest:
        provider_slug = provider_name.replace("-", "_")
        return QuantumOptimizationRequest(
            request_id=f"validation_{provider_slug}_{objective_type}",
            package_id="quantum-validation",
            timestamp_utc=_utc_now_iso(),
            runtime_flags={"shadow_mode_only": True, "artifact_only": True},
            time_window_state={"window": "validation", "impact_multiplier": 1.0},
            regime_state={"macro_state": "mixed", "regime_shift_probability": 0.34},
            objective={"type": objective_type, "algorithm_family": "qaoa"},
            constraints={"max_names": 3, "max_sector_weight": 0.75, "max_risk": 0.9},
            candidate_universe=[
                {"symbol": "XOM", "score": 0.92, "expected_alpha": 0.08, "risk": 0.22, "sector": "Energy"},
                {"symbol": "LMT", "score": 0.84, "expected_alpha": 0.05, "risk": 0.18, "sector": "Defense"},
                {"symbol": "GLD", "score": 0.79, "expected_alpha": 0.04, "risk": 0.12, "sector": "Metals"},
                {"symbol": "TLT", "score": 0.74, "expected_alpha": 0.03, "risk": 0.09, "sector": "Rates"},
            ],
            market_microstructure={"session": "closed"},
            provenance={"mode": "quantum_validation", "provider_requested": provider_name},
        )

    def _backend_status(
        self,
        result: Any,
        diagnostics: Dict[str, Any],
        provider_name: str,
    ) -> str:
        if diagnostics.get("provider_error"):
            return "fallback"
        if diagnostics.get("provider_mode") == provider_name:
            return "success"
        if getattr(result, "fallback_used", False):
            return "fallback"
        return "success"

    def _assess_artifact_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        missing_top_level = [field for field in REQUIRED_ARTIFACT_FIELDS if field not in payload]
        execution_metadata = payload.get("execution_metadata") or {}
        missing_execution_metadata = [
            field for field in REQUIRED_EXECUTION_METADATA_FIELDS if field not in execution_metadata
        ]
        return {
            "complete": not missing_top_level and not missing_execution_metadata,
            "missing_top_level_fields": missing_top_level,
            "missing_execution_metadata_fields": missing_execution_metadata,
        }

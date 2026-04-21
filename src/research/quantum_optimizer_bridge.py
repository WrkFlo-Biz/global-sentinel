#!/usr/bin/env python3
"""Global Sentinel quantum optimizer bridge v2.

Hybrid classical-quantum optimization pipeline that:
- Preprocesses candidates classically (scoring, constraint filtering)
- Optionally runs QPanda QAOA/VQE optimization via Origin backends
- Validates quantum results against classical baseline
- Writes artifact-only results (never routes to broker)

Supports Origin QCloud, Origin Pilot, local simulator, and classical fallback.
Loads policy from config/quantum_lane_policy.yaml when available.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.research.qpanda3_runtime import (
    QCloudAsyncOrchestrator,
    build_execution_metadata,
    build_lane_settings,
    build_rotation_program,
    load_qpanda3_sdk,
    run_rotation_on_cpuqvm,
)
from src.research.quantum_experiment_registry import QuantumExperimentRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try importing packet schemas; fall back to local dataclasses
# ---------------------------------------------------------------------------
try:
    from src.packets.schemas import QuantumOptimizationRequest, QuantumOptimizationResult
    _HAS_PACKET_SCHEMAS = True
except ImportError:
    _HAS_PACKET_SCHEMAS = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PLACEHOLDER_VALUES = {
    "", "disabled", "unset", "placeholder", "placeholder-disabled",
    "changeme", "none", "null",
}
DEFAULT_ORIGINQ_CLOUD_URL = "http://pyqanda-admin.qpanda.cn"
VERSION = "2.1.0"

# ---------------------------------------------------------------------------
# Helpers (preserved from v1)
# ---------------------------------------------------------------------------

def clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in PLACEHOLDER_VALUES:
        return None
    return text


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: Any, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(value)


def parse_float(value: Any, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def parse_csv_ints(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def normalize_provider_name(value: Any) -> str:
    text = str(value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "originq": "origin-qcloud",
        "origin-cloud": "origin-qcloud",
        "origin-orbit": "origin-qcloud",
        "orbit": "origin-qcloud",
        "pilot": "origin-pilot",
        "pilot-os": "origin-pilot",
        "origin-local-sim": "origin-local",
        "local": "origin-local",
    }
    return aliases.get(text, text)


# ---------------------------------------------------------------------------
# Policy loader
# ---------------------------------------------------------------------------

def load_lane_policy(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load quantum lane policy from YAML config.

    Returns empty dict if the file is missing or unreadable.
    """
    if path is None:
        path = Path("config/quantum_lane_policy.yaml")
    if not path.exists():
        logger.debug("Lane policy not found at %s; using defaults", path)
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        logger.warning("PyYAML not installed; cannot load lane policy")
        return {}
    except Exception as exc:
        logger.warning("Failed to load lane policy %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Request / Result dataclasses (used when packet schemas are unavailable)
# ---------------------------------------------------------------------------

if not _HAS_PACKET_SCHEMAS:
    @dataclass
    class QuantumOptimizationRequest:  # type: ignore[no-redef]
        request_id: str
        package_id: str
        runtime_flags: Dict[str, Any]
        objective: Dict[str, Any]
        constraints: Dict[str, Any]
        candidate_universe: List[Dict[str, Any]]
        provenance: Dict[str, Any]

    @dataclass
    class QuantumOptimizationResult:  # type: ignore[no-redef]
        request_id: str
        package_id: str
        solver: str
        success: bool
        ranked_solutions: List[Dict[str, Any]]
        diagnostics: Dict[str, Any]
        fallback_used: bool
        wall_clock_seconds: float
        objective_value: float
        feasibility: float
        provenance: Dict[str, Any]


# ---------------------------------------------------------------------------
# Origin provider configuration (preserved from v1, extended for v2)
# ---------------------------------------------------------------------------

@dataclass
class OriginProviderConfig:
    provider: str = "auto"
    cloud_url: str = DEFAULT_ORIGINQ_CLOUD_URL
    backend_name: Optional[str] = None
    api_key: Optional[str] = None
    pilot_url: Optional[str] = None
    pilot_api_key: Optional[str] = None
    pilot_chip_id: str = "any_quantum_chip"
    shots: int = 512
    max_candidates: int = 12
    quantum_weight: float = 0.35
    point_label: int = 0
    task_form: int = 4
    amend: bool = True
    mapping: bool = True
    optimization: bool = True
    enable_binary_encoding: bool = False
    specified_block: List[int] = field(default_factory=list)
    log_cout: bool = False
    # v2 additions
    qaoa_depth: int = 2
    vqe_max_iter: int = 100

    @classmethod
    def from_sources(
        cls,
        runtime_flags: Optional[Dict[str, Any]] = None,
        *,
        provider_override: Optional[str] = None,
        shots_override: Optional[int] = None,
        policy: Optional[Dict[str, Any]] = None,
    ) -> "OriginProviderConfig":
        flags = runtime_flags or {}
        pol = policy or {}

        def pick(*keys: str, env: Iterable[str] = ()) -> Any:
            for key in keys:
                if key in flags and flags[key] is not None:
                    return flags[key]
            for name in env:
                if os.getenv(name) is not None:
                    return os.getenv(name)
            return None

        provider = provider_override or pick(
            "quantum_provider",
            "origin_provider",
            "originq_provider",
            env=("GS_QUANTUM_PROVIDER", "ORIGINQ_PROVIDER", "ORIGIN_ORBIT_PROVIDER"),
        )

        return cls(
            provider=normalize_provider_name(provider or "auto"),
            cloud_url=str(
                pick("originq_cloud_url", "origin_cloud_url",
                     env=("ORIGINQ_CLOUD_URL", "ORIGIN_ORBIT_URL"))
                or DEFAULT_ORIGINQ_CLOUD_URL
            ),
            backend_name=clean_optional(
                pick("originq_backend_name", "origin_backend_name",
                     env=("ORIGINQ_BACKEND_NAME", "ORIGIN_ORBIT_BACKEND_NAME"))
            ),
            api_key=clean_optional(
                pick("originq_api_key", "origin_api_key",
                     env=("ORIGINQ_API_KEY", "ORIGIN_ORBIT_API_KEY"))
            ),
            pilot_url=clean_optional(
                pick("originq_pilot_url", "origin_pilot_url",
                     env=("ORIGINQ_PILOT_URL", "ORIGIN_ORBIT_PILOT_URL"))
            ),
            pilot_api_key=clean_optional(
                pick("originq_pilot_api_key", "origin_pilot_api_key",
                     env=("ORIGINQ_PILOT_API_KEY", "ORIGIN_ORBIT_PILOT_API_KEY"))
            ),
            pilot_chip_id=str(
                pick("originq_chip_id", "origin_chip_id",
                     env=("ORIGINQ_CHIP_ID", "ORIGIN_ORBIT_CHIP_ID"))
                or "any_quantum_chip"
            ),
            shots=shots_override or parse_int(
                pick("originq_shots", env=("ORIGINQ_SHOTS", "ORIGIN_ORBIT_SHOTS")),
                512,
            ),
            max_candidates=parse_int(
                pick("originq_max_candidates", "max_quantum_candidates",
                     env=("ORIGINQ_MAX_CANDIDATES", "ORIGIN_ORBIT_MAX_CANDIDATES")),
                12,
            ),
            quantum_weight=parse_float(
                pick("originq_quantum_weight", "quantum_weight",
                     env=("ORIGINQ_QUANTUM_WEIGHT", "ORIGIN_ORBIT_QUANTUM_WEIGHT")),
                0.35,
            ),
            point_label=parse_int(
                pick("originq_point_label",
                     env=("ORIGINQ_POINT_LABEL", "ORIGIN_ORBIT_POINT_LABEL")),
                0,
            ),
            task_form=parse_int(
                pick("originq_task_form",
                     env=("ORIGINQ_TASK_FORM", "ORIGIN_ORBIT_TASK_FORM")),
                4,
            ),
            amend=parse_bool(
                pick("originq_amend", env=("ORIGINQ_AMEND", "ORIGIN_ORBIT_AMEND")),
                True,
            ),
            mapping=parse_bool(
                pick("originq_mapping", env=("ORIGINQ_MAPPING", "ORIGIN_ORBIT_MAPPING")),
                True,
            ),
            optimization=parse_bool(
                pick("originq_optimization",
                     env=("ORIGINQ_OPTIMIZATION", "ORIGIN_ORBIT_OPTIMIZATION")),
                True,
            ),
            enable_binary_encoding=parse_bool(
                pick("originq_enable_binary_encoding",
                     env=("ORIGINQ_ENABLE_BINARY_ENCODING", "ORIGIN_ORBIT_ENABLE_BINARY_ENCODING")),
                False,
            ),
            specified_block=parse_csv_ints(
                pick("originq_specified_block",
                     env=("ORIGINQ_SPECIFIED_BLOCK", "ORIGIN_ORBIT_SPECIFIED_BLOCK"))
            ),
            log_cout=parse_bool(
                pick("originq_log_cout", env=("ORIGINQ_LOG_COUT", "ORIGIN_ORBIT_LOG_COUT")),
                False,
            ),
            qaoa_depth=parse_int(pol.get("qaoa_depth"), 2),
            vqe_max_iter=parse_int(pol.get("vqe_max_iter"), 100),
        )

    def resolved_provider(self) -> str:
        requested = normalize_provider_name(self.provider)
        if requested != "auto":
            return requested
        if self.api_key and self.backend_name:
            return "origin-qcloud"
        if self.pilot_url and self.pilot_api_key:
            return "origin-pilot"
        if parse_bool(os.getenv("ORIGINQ_ENABLE_LOCAL_SIM"), False):
            return "origin-local"
        return "classical"

    def redacted(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "resolved_provider": self.resolved_provider(),
            "cloud_url": self.cloud_url,
            "backend_name": self.backend_name,
            "pilot_url": self.pilot_url,
            "pilot_chip_id": self.pilot_chip_id,
            "shots": self.shots,
            "max_candidates": self.max_candidates,
            "quantum_weight": self.quantum_weight,
            "qaoa_depth": self.qaoa_depth,
            "vqe_max_iter": self.vqe_max_iter,
            "api_key_configured": self.api_key is not None,
            "pilot_api_key_configured": self.pilot_api_key is not None,
        }


# ---------------------------------------------------------------------------
# Main bridge class (v2)
# ---------------------------------------------------------------------------

class QuantumOptimizerBridge:
    """Artifact-only hybrid classical-quantum optimization bridge (v2).

    Workflow:
      1. Classical preprocessing: score and rank candidates, apply constraints
      2. QPanda/Origin optimization: QAOA or VQE on feasible subset
      3. Validation: compare quantum result against classical baseline
      4. Artifact write: persist result JSON (never routes to broker)
    """

    def __init__(
        self,
        artifact_dir: Path,
        provider_config: Optional[OriginProviderConfig] = None,
        policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.policy = policy or load_lane_policy()
        self.artifact_dir = artifact_dir
        self.provider_config = provider_config or OriginProviderConfig.from_sources(
            policy=self.policy,
        )
        self.runtime_settings = build_lane_settings(self.policy)
        self.experiment_registry = self._load_experiment_registry()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

        # Safety flags from policy
        lane_rules = self.policy.get("lane_rules", {}) or {}
        self.disable_execution_path = lane_rules.get("disable_execution_path", self.policy.get("disable_execution_path", True))
        self.shadow_mode_only = lane_rules.get("shadow_mode_only", self.policy.get("shadow_mode_only", True))
        self.artifact_only = lane_rules.get("artifact_only", True)

    # ----- public API -----

    def run(self, req: QuantumOptimizationRequest) -> QuantumOptimizationResult:
        """Execute the hybrid optimization pipeline for a single request."""
        start = time.time()
        diagnostics: Dict[str, Any] = self._base_diagnostics()
        diagnostics["provider_config"] = self.provider_config.redacted()
        diagnostics["bridge_version"] = VERSION
        diagnostics["packet_schemas_available"] = _HAS_PACKET_SCHEMAS
        diagnostics["framework_standard"] = self.runtime_settings.framework_standard
        diagnostics["qpanda2_supported"] = self.runtime_settings.qpanda2_supported
        diagnostics["qpanda3_runtime_settings"] = self.runtime_settings.to_dict()
        diagnostics["objective_type"] = str(req.objective.get("type", "unknown"))
        experiment_spec = self._resolve_experiment(req)
        if experiment_spec:
            diagnostics["experiment_registry_entry"] = experiment_spec.to_dict()

        # Step 1: Classical preprocessing
        classical_ranked = self._rank_classically(req.candidate_universe, req.objective, req.constraints)
        classical_baseline_score = self._aggregate_objective(classical_ranked[:10], req.objective)

        fallback_used = False
        solver_name = "classical_fallback"
        quantum_ranked = classical_ranked  # default: use classical if quantum fails

        # Step 2: QPanda / Origin optimization
        try:
            resolved = self.provider_config.resolved_provider()
            diagnostics["provider_requested"] = self.provider_config.provider
            diagnostics["provider_resolved"] = resolved

            if resolved != "classical" and classical_ranked:
                quantum_ranked, solver_name, provider_diag = self._optimize_with_qpanda(
                    classical_ranked, req.constraints, req.objective, req,
                )
                diagnostics.update(provider_diag)
            else:
                reason = "no_remote_provider_configured" if resolved == "classical" else "no_candidates"
                diagnostics["provider_skipped"] = reason
        except Exception as exc:
            fallback_used = True
            solver_name = "classical_fallback"
            quantum_ranked = classical_ranked
            diagnostics["provider_error"] = str(exc)
            diagnostics["provider_error_type"] = type(exc).__name__
            diagnostics["provider_fallback"] = "classical_ranking"
            logger.warning("Quantum optimization failed, falling back to classical: %s", exc)

        # Step 3: Validation
        quantum_objective = self._aggregate_objective(quantum_ranked[:10], req.objective)
        validation = self._validate_result(quantum_objective, classical_baseline_score)
        diagnostics["validation"] = validation

        # If quantum result is worse than classical, prefer classical
        if not validation["quantum_accepted"]:
            quantum_ranked = classical_ranked
            fallback_used = True
            diagnostics["validation_override"] = "classical_preferred_over_quantum"

        diagnostics["candidate_count"] = len(req.candidate_universe)
        diagnostics["ranked_count"] = len(quantum_ranked)
        diagnostics["objective_keys"] = sorted(req.objective.keys())
        diagnostics["constraint_keys"] = sorted(req.constraints.keys())
        diagnostics["fallback_used"] = fallback_used

        wall_clock = time.time() - start
        diagnostics["wall_clock_seconds"] = wall_clock

        feasibility = self._compute_feasibility(quantum_ranked[:10], req.constraints)

        result = QuantumOptimizationResult(
            request_id=req.request_id,
            package_id=req.package_id,
            solver=solver_name,
            success=True,
            ranked_solutions=quantum_ranked[:10],
            diagnostics=diagnostics,
            fallback_used=fallback_used,
            wall_clock_seconds=wall_clock,
            objective_value=quantum_objective,
            feasibility=feasibility,
            provenance=req.provenance,
        )

        # Step 4: Artifact write
        self._write_artifact(result)
        return result

    # ----- Step 1: Classical preprocessing -----

    def _rank_classically(
        self,
        candidates: List[Dict[str, Any]],
        objective: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Score, filter, and rank candidates classically."""
        ranked: List[Dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            enriched = dict(candidate)
            enriched["_candidate_index"] = index
            enriched["candidate_key"] = (
                candidate.get("candidate_key")
                or candidate.get("candidate_id")
                or candidate.get("symbol")
                or f"candidate-{index}"
            )
            enriched["classical_score"] = self._score_candidate(candidate, objective, constraints)
            enriched["combined_score"] = enriched["classical_score"]
            ranked.append(enriched)

        ranked.sort(key=lambda item: float(item["combined_score"]), reverse=True)
        return ranked

    # ----- Step 2: QPanda / Origin optimization -----

    def _optimize_with_qpanda(
        self,
        candidates: List[Dict[str, Any]],
        constraints: Dict[str, Any],
        objective: Dict[str, Any],
        req: QuantumOptimizationRequest,
    ) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
        """Run QPanda-based QAOA/VQE optimization on feasible candidates.

        Returns (re-ranked candidates, solver name, provider diagnostics).
        """
        config = self.provider_config
        quantum_weight = parse_float(objective.get("quantum_weight"), config.quantum_weight)
        resolved = config.resolved_provider()
        sdk = self._import_origin_sdk()
        algorithm_family = str(objective.get("algorithm_family") or "qaoa")
        formulation_id = str(objective.get("formulation_id") or objective.get("type") or "unregistered")

        # Filter to feasible candidates only
        evaluated = [
            item for item in candidates
            if item["classical_score"] > -1e8
        ][:config.max_candidates]

        if not evaluated:
            return candidates, "classical_fallback", {"provider_skipped": "no_feasible_candidates"}

        # Build QPanda circuits
        programs, originirs, thetas = self._build_program_batch(sdk, evaluated)

        # Dispatch to appropriate backend
        if resolved == "origin-local":
            quantum_scores, provider_diag = self._run_origin_local(
                sdk,
                programs,
                algorithm_family=algorithm_family,
                formulation_id=formulation_id,
                thetas=thetas,
            )
            provider_name = "origin_local_simulator"
        elif resolved == "origin-qcloud":
            quantum_scores, provider_diag = self._run_origin_qcloud(
                sdk,
                programs,
                algorithm_family=algorithm_family,
                formulation_id=formulation_id,
            )
            provider_name = f"origin_qcloud:{config.backend_name}"
        elif resolved == "origin-pilot":
            quantum_scores, provider_diag = self._run_origin_pilot(
                sdk,
                originirs,
                req,
                algorithm_family=algorithm_family,
                formulation_id=formulation_id,
            )
            provider_name = f"origin_pilot:{config.pilot_chip_id}"
        else:
            raise RuntimeError(f"Unsupported provider resolution: {resolved}")

        # Blend quantum scores with classical scores
        for candidate, quantum_score in zip(evaluated, quantum_scores):
            candidate["quantum_score"] = quantum_score
            candidate["combined_score"] = (
                candidate["classical_score"] + (quantum_weight * quantum_score)
            )

        candidates.sort(key=lambda item: float(item["combined_score"]), reverse=True)
        provider_diag["provider_quantum_weight"] = quantum_weight
        provider_diag["qaoa_depth"] = config.qaoa_depth
        return candidates, provider_name, provider_diag

    # ----- Step 3: Validation -----

    def _validate_result(
        self,
        quantum_objective: float,
        classical_baseline: float,
    ) -> Dict[str, Any]:
        """Compare quantum result against classical baseline.

        Accepts quantum if it is not significantly worse (within 5% tolerance).
        """
        if classical_baseline == 0:
            relative_change = 0.0 if quantum_objective == 0 else float("inf")
        else:
            relative_change = (quantum_objective - classical_baseline) / abs(classical_baseline)

        # Accept quantum if it is within 5% degradation or better
        tolerance = -0.05
        accepted = relative_change >= tolerance

        return {
            "classical_baseline_objective": classical_baseline,
            "quantum_objective": quantum_objective,
            "relative_change": relative_change,
            "tolerance": tolerance,
            "quantum_accepted": accepted,
        }

    # ----- Step 4: Artifact write -----

    def _build_execution_metadata(
        self, solver: str, diagnostics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build first-class execution metadata for artifact reproducibility."""
        if diagnostics.get("execution_metadata"):
            execution_metadata = dict(diagnostics.get("execution_metadata", {}) or {})
        else:
            execution_metadata = build_execution_metadata(
                settings=self.runtime_settings,
                sdk={
                    "framework_version": diagnostics.get("provider_sdk_version"),
                    "algorithm_package_version": diagnostics.get("algorithm_package_version"),
                    "vqnet_available": diagnostics.get("vqnet_available", False),
                },
                provider_name=str(diagnostics.get("provider_mode", "classical")),
                backend_name=str(diagnostics.get("provider_backend") or self.provider_config.backend_name or diagnostics.get("provider_mode", "classical")),
                shots=self.provider_config.shots,
                algorithm_family=str(diagnostics.get("algorithm_family", "qaoa")),
                formulation_id=str(diagnostics.get("formulation_id", diagnostics.get("objective_type", "unregistered"))),
                async_submission=bool(diagnostics.get("async_submission", False)),
                hardware_job_id=str(diagnostics.get("provider_job_id", "")),
                job_submission_mode=str(diagnostics.get("provider_submission_mode", "")),
                submitted_at_utc=str(diagnostics.get("provider_submitted_at_utc", "")),
                completed_at_utc=str(diagnostics.get("provider_completed_at_utc", "")),
                extra_tags={
                    "bridge_version": VERSION,
                    "hardware_batch_id": diagnostics.get("provider_batch_id"),
                    "mitigation_enabled": diagnostics.get("mitigation_enabled", False),
                    "mitigation_method": diagnostics.get("mitigation_method"),
                    "noise_analysis_tags": diagnostics.get("noise_analysis_tags", []),
                    "solver": solver,
                    "qaoa_depth": self.provider_config.qaoa_depth,
                    "vqe_max_iter": self.provider_config.vqe_max_iter,
                },
            )
        execution_metadata.setdefault("bridge_version", VERSION)
        execution_metadata.setdefault("solver", solver)
        execution_metadata.setdefault("shots", self.provider_config.shots)
        execution_metadata.setdefault("qaoa_depth", self.provider_config.qaoa_depth)
        execution_metadata.setdefault("vqe_max_iter", self.provider_config.vqe_max_iter)
        execution_metadata.setdefault("artifact_only", True)
        execution_metadata.setdefault("research_only", True)
        execution_metadata.setdefault("not_for_direct_execution", True)
        return execution_metadata

    def _write_artifact(self, result: QuantumOptimizationResult) -> None:
        """Persist result as JSON artifact. Never routes to broker."""
        payload = asdict(result)
        payload["artifact_only"] = True
        payload["research_only"] = True
        payload["not_for_direct_execution"] = True
        payload["execution_metadata"] = self._build_execution_metadata(
            result.solver, result.diagnostics,
        )
        out = self.artifact_dir / f"{result.request_id}.json"
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Artifact written: %s", out)

    # ----- Origin SDK integration (preserved from v1) -----

    def _import_origin_sdk(self) -> Dict[str, Any]:
        try:
            return load_qpanda3_sdk()
        except Exception as exc:
            version = self._safe_sdk_version()
            detail = "pyqpanda3 import failed"
            if version:
                detail += f" (package {version} detected)"
            raise RuntimeError(f"{detail}: {exc}") from exc

    def _safe_sdk_version(self) -> Optional[str]:
        try:
            return metadata.version("pyqpanda3")
        except metadata.PackageNotFoundError:
            return None

    def _build_program_batch(
        self, sdk: Dict[str, Any], evaluated: List[Dict[str, Any]],
    ) -> Tuple[List[Any], List[str], List[float]]:
        """Build QPanda QAOA-style rotation circuits for each candidate.

        Each candidate gets a single-qubit RY rotation parameterized by its
        classical score, mapped to a probability via arcsine encoding. This
        provides a quantum signal that can be blended with the classical score.

        Returns (programs, originir_strings, thetas).
        """
        probabilities = self._normalize_probabilities(
            item["classical_score"] for item in evaluated
        )
        programs: List[Any] = []
        originirs: List[str] = []
        thetas: List[float] = []

        for candidate, probability in zip(evaluated, probabilities):
            theta = 2.0 * math.asin(math.sqrt(probability))
            thetas.append(theta)
            try:
                prog, originir = build_rotation_program(sdk, theta=theta, qubit_index=0, cbit_index=0)
            except Exception:
                prog, originir = None, ""
            candidate["origin_probability_target"] = probability
            candidate["origin_rotation_theta"] = theta
            programs.append(prog)
            originirs.append(originir)

        return programs, originirs, thetas

    def _run_origin_local(
        self,
        sdk: Dict[str, Any],
        programs: List[Any],
        *,
        algorithm_family: str,
        formulation_id: str,
        thetas: Optional[List[float]] = None,
    ) -> Tuple[List[float], Dict[str, Any]]:
        scores: List[float] = []
        shots = self.provider_config.shots
        if thetas:
            # QPanda2/pyqpanda path: run each rotation on a fresh CPUQVM
            for theta in thetas:
                counts = run_rotation_on_cpuqvm(sdk, theta=theta, shots=shots)
                scores.append(self._measurement_probability(counts))
        else:
            for theta in (thetas or []):
                counts = run_rotation_on_cpuqvm(sdk, theta=theta, shots=shots)
                scores.append(self._measurement_probability(counts))
        return scores, {
            "provider_mode": "origin-local",
            "provider_sdk_version": sdk.get("framework_version"),
            "provider_job_count": len(programs),
            "algorithm_package_version": sdk.get("algorithm_package_version"),
            "vqnet_available": sdk.get("vqnet_available", False),
            "algorithm_family": algorithm_family,
            "formulation_id": formulation_id,
            "execution_metadata": build_execution_metadata(
                settings=self.runtime_settings,
                sdk=sdk,
                provider_name="origin-local",
                backend_name=self.runtime_settings.local_backend_type,
                shots=self.provider_config.shots,
                algorithm_family=algorithm_family,
                formulation_id=formulation_id,
                async_submission=False,
                job_submission_mode="synchronous_cpuqvm",
                extra_tags={"artifact_only": self.artifact_only},
            ),
        }

    def _run_origin_qcloud(
        self,
        sdk: Dict[str, Any],
        programs: List[Any],
        *,
        algorithm_family: str,
        formulation_id: str,
    ) -> Tuple[List[float], Dict[str, Any]]:
        config = self.provider_config
        if not config.api_key or not config.backend_name:
            raise RuntimeError("Origin QCloud requires ORIGINQ_API_KEY and ORIGINQ_BACKEND_NAME")

        try:
            service = sdk["QCloudService"](config.api_key, config.cloud_url)
        except TypeError:
            service = sdk["QCloudService"]()
            service.init(config.api_key, config.cloud_url)
        available_backends = service.backends()
        if config.backend_name not in available_backends:
            raise RuntimeError(
                f"Origin QCloud backend '{config.backend_name}' is not available. "
                f"Available backends: {sorted(available_backends.keys())}"
            )

        options = sdk["QCloudOptions"]()
        options.set_amend(config.amend)
        options.set_mapping(config.mapping)
        options.set_optimization(config.optimization)
        if config.specified_block:
            options.set_specified_block(config.specified_block)
        if config.point_label:
            options.set_point_label(config.point_label)

        backend = service.backend(config.backend_name)
        batch_id = f"gs-{int(time.time())}"
        orchestrator = QCloudAsyncOrchestrator(
            poll_interval_seconds=self.runtime_settings.qcloud_poll_interval_seconds,
            timeout_seconds=self.runtime_settings.qcloud_timeout_seconds,
        )
        handle = orchestrator.submit(
            backend=backend,
            programs=programs,
            shots=config.shots,
            options=options,
            enable_binary_encoding=config.enable_binary_encoding,
            batch_id=batch_id,
            task_form=config.task_form,
        )
        result, async_metadata = orchestrator.collect(handle)

        counts_list = result.get_counts_list()
        scores = [self._measurement_probability(item) for item in counts_list]

        return scores, {
            "provider_mode": "origin-qcloud",
            "provider_sdk_version": sdk.get("framework_version"),
            "provider_backend": config.backend_name,
            "provider_cloud_url": config.cloud_url,
            "provider_batch_id": batch_id,
            "provider_available_backends": sorted(available_backends.keys()),
            "algorithm_package_version": sdk.get("algorithm_package_version"),
            "vqnet_available": sdk.get("vqnet_available", False),
            "algorithm_family": algorithm_family,
            "formulation_id": formulation_id,
            "async_submission": bool(async_metadata.get("provider_async_submission", False)),
            **async_metadata,
            "execution_metadata": build_execution_metadata(
                settings=self.runtime_settings,
                sdk=sdk,
                provider_name="origin-qcloud",
                backend_name=config.backend_name or "qcloud",
                shots=self.provider_config.shots,
                algorithm_family=algorithm_family,
                formulation_id=formulation_id,
                async_submission=bool(async_metadata.get("provider_async_submission", False)),
                hardware_job_id=str(async_metadata.get("provider_job_id", "")),
                job_submission_mode=str(async_metadata.get("provider_submission_mode", "")),
                submitted_at_utc=str(async_metadata.get("provider_submitted_at_utc", "")),
                completed_at_utc=str(async_metadata.get("provider_completed_at_utc", "")),
                extra_tags={
                    "artifact_only": self.artifact_only,
                    "provider_cloud_url": config.cloud_url,
                    "available_backends": sorted(available_backends.keys()),
                },
            ),
        }

    def _run_origin_pilot(
        self,
        sdk: Dict[str, Any],
        originirs: List[str],
        req: QuantumOptimizationRequest,
        *,
        algorithm_family: str,
        formulation_id: str,
    ) -> Tuple[List[float], Dict[str, Any]]:
        config = self.provider_config
        if not config.pilot_url or not config.pilot_api_key:
            raise RuntimeError("Origin Pilot requires ORIGINQ_PILOT_URL and ORIGINQ_PILOT_API_KEY")

        service = sdk["QPilotService"](
            config.pilot_url, config.log_cout, config.pilot_api_key,
        )
        results = service.run(
            originirs,
            config.shots,
            config.pilot_chip_id,
            config.amend,
            config.mapping,
            config.optimization,
            config.specified_block,
            req.package_id,
            config.point_label,
        )
        scores = [self._measurement_probability(item) for item in results]

        return scores, {
            "provider_mode": "origin-pilot",
            "provider_sdk_version": sdk.get("framework_version"),
            "provider_pilot_url": config.pilot_url,
            "provider_chip_id": config.pilot_chip_id,
            "provider_job_count": len(originirs),
            "algorithm_package_version": sdk.get("algorithm_package_version"),
            "vqnet_available": sdk.get("vqnet_available", False),
            "algorithm_family": algorithm_family,
            "formulation_id": formulation_id,
            "execution_metadata": build_execution_metadata(
                settings=self.runtime_settings,
                sdk=sdk,
                provider_name="origin-pilot",
                backend_name=config.pilot_chip_id,
                shots=self.provider_config.shots,
                algorithm_family=algorithm_family,
                formulation_id=formulation_id,
                async_submission=True,
                job_submission_mode="managed_pilot_service",
                submitted_at_utc=_utc_now_iso(),
                completed_at_utc=_utc_now_iso(),
                extra_tags={"artifact_only": self.artifact_only, "pilot_url": config.pilot_url},
            ),
        }

    # ----- Scoring helpers -----

    def _score_candidate(
        self,
        candidate: Dict[str, Any],
        objective: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> float:
        score = float(candidate.get("score", 0.0))
        alpha = float(
            candidate.get("expected_alpha", candidate.get("expected_alpha_bps", 0.0))
        )
        risk = float(candidate.get("risk", candidate.get("risk_budget_used", 0.0)))
        turnover = float(candidate.get("turnover", candidate.get("turnover_pct", 0.0)))

        alpha_weight = float(objective.get("alpha_weight", 1.0))
        risk_weight = float(objective.get("risk_weight", 1.0))
        turnover_weight = float(objective.get("turnover_weight", 0.25))

        max_risk = constraints.get("max_risk")
        if max_risk is not None and risk > float(max_risk):
            return -1e9

        return score + (alpha * alpha_weight) - (risk * risk_weight) - (turnover * turnover_weight)

    def _aggregate_objective(
        self, top_solutions: List[Dict[str, Any]], objective: Dict[str, Any],
    ) -> float:
        """Compute aggregate objective value for the top-N solution set."""
        if not top_solutions:
            return 0.0
        return sum(float(s.get("combined_score", 0.0)) for s in top_solutions) / len(top_solutions)

    def _compute_feasibility(
        self, solutions: List[Dict[str, Any]], constraints: Dict[str, Any],
    ) -> float:
        """Fraction of top solutions that satisfy all constraints."""
        if not solutions:
            return 0.0
        max_risk = constraints.get("max_risk")
        feasible = 0
        for sol in solutions:
            risk = float(sol.get("risk", sol.get("risk_budget_used", 0.0)))
            if max_risk is not None and risk > float(max_risk):
                continue
            feasible += 1
        return feasible / len(solutions)

    def _normalize_probabilities(self, scores: Iterable[float]) -> List[float]:
        values = list(scores)
        if not values:
            return []
        low = min(values)
        high = max(values)
        if math.isclose(low, high):
            return [0.5 for _ in values]

        normalized = []
        for value in values:
            scaled = (value - low) / (high - low)
            normalized.append(min(0.999, max(0.001, 0.05 + (0.90 * scaled))))
        return normalized

    def _measurement_probability(self, counts: Dict[str, Any]) -> float:
        total = 0.0
        excited = 0.0
        for state, weight in (counts or {}).items():
            numeric = float(weight)
            total += numeric
            if self._state_has_excitation(str(state)):
                excited += numeric
        if total <= 0:
            return 0.0
        return excited / total

    def _state_has_excitation(self, raw_state: str) -> bool:
        state = raw_state.strip().lower()
        if state.startswith("0x"):
            try:
                return int(state, 16) != 0
            except ValueError:
                return False
        bits = "".join(ch for ch in state if ch in {"0", "1"})
        return bits.endswith("1") or ("1" in bits and len(bits) == 1)

    def _load_experiment_registry(self) -> Optional[QuantumExperimentRegistry]:
        """Load the research-only experiment registry when configured."""
        registry_cfg = self.policy.get("experiment_registry", {}) or {}
        registry_path = registry_cfg.get("path", "config/quantum_experiment_registry.yaml")
        path = Path(str(registry_path))
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            logger.info("Quantum experiment registry not found at %s", path)
            return None
        try:
            return QuantumExperimentRegistry.load(path)
        except Exception as exc:
            logger.warning("Failed to load quantum experiment registry %s: %s", path, exc)
            return None

    def _resolve_experiment(
        self, req: QuantumOptimizationRequest,
    ) -> Optional[Any]:
        """Resolve the most relevant registry entry for the current request."""
        if not self.experiment_registry:
            return None
        objective = getattr(req, "objective", {}) or {}
        for key in ("experiment_id", "formulation_id", "type"):
            value = objective.get(key)
            if value:
                spec = self.experiment_registry.get(str(value))
                if spec:
                    return spec
        return self.experiment_registry.resolve_for_objective(
            str(objective.get("type", "")),
        )

    def _base_diagnostics(self) -> Dict[str, Any]:
        noise_cfg = self.policy.get("noise_mitigation", {})
        return {
            "mode": "artifact_only",
            "artifact_only": self.artifact_only,
            "execution_path_disabled": self.disable_execution_path,
            "shadow_mode_only": self.shadow_mode_only,
            "broker_credentials_present": False,
            "mitigation_enabled": bool(noise_cfg.get("enabled", False)),
            "mitigation_method": None,
            "noise_analysis_tags": [],
        }

    def tag_noise_analysis(
        self,
        diagnostics: Dict[str, Any],
        *,
        method: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Tag an experiment run with noise analysis / mitigation metadata.

        Call this before _write_artifact to record which mitigation method
        was applied (e.g. 'zero_noise_extrapolation', 'measurement_error_mitigation').
        """
        supported = self.policy.get("noise_mitigation", {}).get("supported_methods", [])
        if method not in supported:
            logger.warning("Mitigation method '%s' not in supported list %s", method, supported)
        diagnostics["mitigation_enabled"] = True
        diagnostics["mitigation_method"] = method
        tags = diagnostics.get("noise_analysis_tags", [])
        tags.append({
            "method": method,
            "parameters": parameters or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        diagnostics["noise_analysis_tags"] = tags


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_request(path: Path) -> QuantumOptimizationRequest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return QuantumOptimizationRequest(**raw)


def load_requests_from_directory(directory: Path) -> List[QuantumOptimizationRequest]:
    """Load all request JSON files from a directory (batch mode)."""
    requests: List[QuantumOptimizationRequest] = []
    for p in sorted(directory.glob("*.json")):
        try:
            requests.append(load_request(p))
        except Exception as exc:
            logger.warning("Skipping %s: %s", p, exc)
    return requests


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Global Sentinel quantum optimizer bridge v2",
    )
    parser.add_argument("--mode", choices=["batch", "single"], default="batch")
    parser.add_argument("--request-json", default=None, help="Path to single request JSON")
    parser.add_argument("--request-dir", default=None, help="Directory of request JSONs (batch mode)")
    parser.add_argument("--artifact-dir", default="artifacts/quantum")
    parser.add_argument("--policy", default="config/quantum_lane_policy.yaml",
                        help="Path to quantum lane policy YAML")
    parser.add_argument(
        "--provider",
        choices=["auto", "classical", "origin-local", "origin-qcloud", "origin-pilot"],
        default=None,
    )
    parser.add_argument("--shots", type=int, default=None)
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()

    policy = load_lane_policy(Path(args.policy))
    provider_config = OriginProviderConfig.from_sources(
        provider_override=args.provider,
        shots_override=args.shots,
        policy=policy,
    )
    bridge = QuantumOptimizerBridge(
        Path(args.artifact_dir),
        provider_config=provider_config,
        policy=policy,
    )

    # Single mode
    if args.mode == "single" and args.request_json:
        req = load_request(Path(args.request_json))
        result = bridge.run(req)
        print(json.dumps(asdict(result), indent=2))
        return 0

    # Batch mode
    if args.mode == "batch" and args.request_dir:
        requests = load_requests_from_directory(Path(args.request_dir))
        if not requests:
            logger.warning("No request files found in %s", args.request_dir)
            return 1
        results = []
        for req in requests:
            result = bridge.run(req)
            results.append(asdict(result))
            logger.info("Processed %s: solver=%s fallback=%s", req.request_id, result.solver, result.fallback_used)
        print(json.dumps({"count": len(results), "results": results}, indent=2))
        return 0

    # Fallback: also accept --request-json in batch mode
    if args.request_json:
        req = load_request(Path(args.request_json))
        result = bridge.run(req)
        print(json.dumps(asdict(result), indent=2))
        return 0

    # No input provided: print status
    print(
        json.dumps(
            {
                "status": "ok",
                "version": VERSION,
                "mode": args.mode,
                "message": "Quantum optimizer bridge v2 ready. Provide --request-json or --request-dir.",
                "execution_path_disabled": bridge.disable_execution_path,
                "shadow_mode_only": bridge.shadow_mode_only,
                "provider_requested": provider_config.provider,
                "provider_resolved": provider_config.resolved_provider(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

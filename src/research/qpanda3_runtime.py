#!/usr/bin/env python3
"""QPanda3 runtime helpers for the Global Sentinel quantum research lane.

This module standardizes the research lane on pyqpanda3 terminology and keeps
local simulation, cloud hardware, and pilot execution paths explicit. It is a
pure helper layer: it produces backend metadata, QProg/QCircuit wrappers, and
QCloud async orchestration helpers, but it never changes execution maturity or
promotion authority.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Dict, List, Optional, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_pkg_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


@dataclass(frozen=True)
class QPanda3LaneSettings:
    """Normalized QPanda3 runtime settings loaded from quantum lane policy."""

    framework_standard: str = "pyqpanda3"
    qpanda2_supported: bool = False
    algorithm_package: str = "pyqpanda-algorithm"
    vqnet_optional: bool = True
    local_backend_type: str = "cpuqvm"
    qcloud_async_enabled: bool = True
    qcloud_poll_interval_seconds: float = 5.0
    qcloud_timeout_seconds: float = 300.0
    randomized_benchmarking_hooks: bool = False
    state_tomography_hooks: bool = False
    mitigation_metadata_only: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QPanda3ExecutionMetadata:
    """Structured execution metadata for a single quantum lane run."""

    framework_standard: str
    framework_version: Optional[str]
    algorithm_package: str
    algorithm_package_version: Optional[str]
    vqnet_available: bool
    backend_type: str
    backend_name: str
    provider_name: str
    shots: int
    algorithm_family: str
    formulation_id: str
    async_submission: bool
    hardware_job_id: str = ""
    job_submission_mode: str = ""
    submitted_at_utc: str = ""
    completed_at_utc: str = ""
    randomized_benchmarking_hooks: bool = False
    state_tomography_hooks: bool = False
    mitigation_metadata_only: bool = True
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QCloudAsyncHandle:
    """In-memory handle for a submitted QCloud job."""

    job: Any
    backend_name: str
    provider_name: str
    shots: int
    submitted_at_utc: str
    job_id: str = ""
    submission_mode: str = "blocking_result"
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 300.0


def load_qpanda3_sdk() -> Dict[str, Any]:
    """Load QPanda3 SDK symbols.

    The helper is intentionally strict about the framework name: if pyqpanda3 is
    unavailable, callers should fall back to classical research rather than
    silently downgrading to older QPanda generations.
    """
    try:
        import pyqpanda3
        from pyqpanda3.core import CPUQVM, QCircuit, QProg, RY, measure
        from pyqpanda3.pilot_service import QPilotService
        from pyqpanda3.qcloud import QCloudOptions, QCloudService
    except Exception as exc:  # pragma: no cover - exercised through bridge fallbacks
        raise RuntimeError(f"pyqpanda3 import failed: {exc}") from exc

    return {
        "package": pyqpanda3,
        "CPUQVM": CPUQVM,
        "QCircuit": QCircuit,
        "QProg": QProg,
        "RY": RY,
        "measure": measure,
        "QPilotService": QPilotService,
        "QCloudOptions": QCloudOptions,
        "QCloudService": QCloudService,
        "framework_version": getattr(pyqpanda3, "__version__", None) or _safe_pkg_version("pyqpanda3"),
        "algorithm_package_version": _safe_pkg_version("pyqpanda-algorithm"),
        "vqnet_available": _safe_pkg_version("pyvqnet") is not None,
    }


def build_lane_settings(policy: Optional[Dict[str, Any]]) -> QPanda3LaneSettings:
    """Normalize the quantum lane policy into runtime settings."""
    payload = policy or {}
    framework = payload.get("framework", {}) or {}
    execution_paths = payload.get("execution_paths", {}) or {}
    backend_paths = payload.get("backends", {}) or {}
    qcloud = execution_paths.get("qcloud", {}) or backend_paths.get("qcloud", {}) or {}
    local_sim = execution_paths.get("local_sim", {}) or backend_paths.get("local_sim", {}) or {}
    noise = payload.get("noise_analysis", {}) or payload.get("noise_mitigation", {}) or {}
    async_jobs = payload.get("async_jobs", {}) or {}
    return QPanda3LaneSettings(
        framework_standard=str(framework.get("standard", framework.get("required", "pyqpanda3"))),
        qpanda2_supported=bool(framework.get("qpanda2_supported", False)),
        algorithm_package=str(
            framework.get("algorithm_package")
            or ((framework.get("optional") or ["pyqpanda-algorithm"])[0] if isinstance(framework.get("optional"), list) and framework.get("optional") else "pyqpanda-algorithm")
        ),
        vqnet_optional=bool(
            framework.get("vqnet_optional", "pyvqnet" in list(framework.get("optional") or []))
        ),
        local_backend_type=str(local_sim.get("backend_type", local_sim.get("class", "cpuqvm"))).lower(),
        qcloud_async_enabled=bool(qcloud.get("async_submission", qcloud.get("async_capable", True))),
        qcloud_poll_interval_seconds=float(async_jobs.get("poll_interval_seconds", qcloud.get("poll_interval_seconds", 5.0)) or 5.0),
        qcloud_timeout_seconds=float(async_jobs.get("timeout_seconds", qcloud.get("timeout_seconds", 300.0)) or 300.0),
        randomized_benchmarking_hooks=bool(noise.get("randomized_benchmarking_hooks", "randomized_benchmarking" in list(noise.get("supported_methods") or []))),
        state_tomography_hooks=bool(noise.get("state_tomography_hooks", "state_tomography" in list(noise.get("supported_methods") or []))),
        mitigation_metadata_only=bool(noise.get("mitigation_metadata_only", noise.get("metadata_always_captured", True))),
    )


def resolve_backend_type(resolved_provider: str) -> str:
    """Map the bridge provider label to a stable backend type."""
    mapping = {
        "origin-local": "cpuqvm",
        "origin-qcloud": "qcloud",
        "origin-pilot": "pilot",
        "classical": "classical",
    }
    return mapping.get(str(resolved_provider), str(resolved_provider))


def build_rotation_program(
    sdk: Dict[str, Any],
    *,
    theta: float,
    qubit_index: int = 0,
    cbit_index: int = 0,
) -> Tuple[Any, str]:
    """Build a QPanda3 program using explicit QCircuit + QProg composition.

    NOTE: This builds a "template" with integer indices.  For pyqpanda (QPanda2),
    the local runner must use ``run_rotation_on_cpuqvm`` instead, which allocates
    real qubit/cbit objects from a QVM instance.
    """
    circuit = sdk["QCircuit"]()
    circuit << sdk["RY"](qubit_index, theta)
    prog = sdk["QProg"]()
    prog << circuit
    prog << sdk["measure"](qubit_index, cbit_index)
    originir = prog.originir() if hasattr(prog, "originir") else ""
    return prog, originir


def run_rotation_on_cpuqvm(
    sdk: Dict[str, Any],
    *,
    theta: float,
    shots: int = 1000,
) -> Dict[str, int]:
    """Build and run a single-qubit RY rotation on a fresh CPUQVM.

    This is the pyqpanda (QPanda2) compatible path: it allocates qubit and
    cbit objects from the QVM, builds the circuit with those objects, and
    uses ``run_with_configuration`` to get measurement counts.
    """
    qvm = sdk["CPUQVM"]()
    qvm.init_qvm()
    q = qvm.qAlloc_many(1)
    c = qvm.cAlloc_many(1)
    circuit = sdk["QCircuit"]()
    circuit << sdk["RY"](q[0], theta)
    prog = sdk["QProg"]()
    prog << circuit
    prog << sdk["measure"](q[0], c[0])
    counts = qvm.run_with_configuration(prog, c, shots)
    qvm.finalize()
    return counts


def build_execution_metadata(
    *,
    settings: QPanda3LaneSettings,
    sdk: Optional[Dict[str, Any]],
    provider_name: str,
    backend_name: str,
    shots: int,
    algorithm_family: str,
    formulation_id: str,
    async_submission: bool,
    hardware_job_id: str = "",
    job_submission_mode: str = "",
    submitted_at_utc: str = "",
    completed_at_utc: str = "",
    extra_tags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build replay-friendly execution metadata for artifacts and diagnostics."""
    framework_version = (sdk or {}).get("framework_version") or _safe_pkg_version("pyqpanda3")
    algorithm_package_version = (
        (sdk or {}).get("algorithm_package_version")
        or _safe_pkg_version("pyqpanda-algorithm")
    )
    vqnet_available = bool((sdk or {}).get("vqnet_available", _safe_pkg_version("pyvqnet") is not None))
    return QPanda3ExecutionMetadata(
        framework_standard=settings.framework_standard,
        framework_version=framework_version,
        algorithm_package=settings.algorithm_package,
        algorithm_package_version=algorithm_package_version,
        vqnet_available=vqnet_available,
        backend_type=resolve_backend_type(provider_name),
        backend_name=str(backend_name or resolve_backend_type(provider_name)),
        provider_name=provider_name,
        shots=int(shots),
        algorithm_family=str(algorithm_family or "qaoa"),
        formulation_id=str(formulation_id or "unregistered"),
        async_submission=bool(async_submission),
        hardware_job_id=str(hardware_job_id or ""),
        job_submission_mode=str(job_submission_mode or ""),
        submitted_at_utc=str(submitted_at_utc or ""),
        completed_at_utc=str(completed_at_utc or ""),
        randomized_benchmarking_hooks=settings.randomized_benchmarking_hooks,
        state_tomography_hooks=settings.state_tomography_hooks,
        mitigation_metadata_only=settings.mitigation_metadata_only,
        tags=dict(extra_tags or {}),
    ).to_dict()


class QCloudAsyncOrchestrator:
    """Handle QCloud job submission, polling, and metadata capture."""

    TERMINAL_STATES = {"FINISHED", "FAILED", "CANCELLED", "CANCELED", "TIMEOUT", "ERROR"}

    def __init__(
        self,
        *,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 300.0,
        sleep_fn=time.sleep,
        monotonic_fn=time.monotonic,
    ):
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn

    def submit(
        self,
        *,
        backend: Any,
        programs: Any,
        shots: int,
        options: Any = None,
        enable_binary_encoding: bool = False,
        batch_id: Optional[str] = None,
        task_form: Optional[int] = None,
    ) -> QCloudAsyncHandle:
        """Submit a QCloud job and return a replay-friendly handle."""
        submitted_at = _utc_now_iso()
        args: List[Any] = [programs, shots]
        if options is not None:
            args.append(options)
        if task_form is not None or batch_id is not None:
            args.extend(
                [
                    enable_binary_encoding,
                    batch_id or f"gs-{int(time.time())}",
                    task_form if task_form is not None else 4,
                ]
            )
        job = backend.run(*args)
        job_id = ""
        if hasattr(job, "job_id"):
            try:
                job_id = str(job.job_id())
            except Exception:
                job_id = ""
        submission_mode = "async_poll" if hasattr(job, "status") else "blocking_result"
        return QCloudAsyncHandle(
            job=job,
            backend_name=str(getattr(backend, "name", "") or getattr(backend, "_backend_name", "") or ""),
            provider_name="origin-qcloud",
            shots=int(shots),
            submitted_at_utc=submitted_at,
            job_id=job_id,
            submission_mode=submission_mode,
            poll_interval_seconds=self.poll_interval_seconds,
            timeout_seconds=self.timeout_seconds,
        )

    def collect(self, handle: QCloudAsyncHandle) -> Tuple[Any, Dict[str, Any]]:
        """Wait for completion and return the QCloud result plus polling metadata."""
        started = self._monotonic()
        poll_count = 0
        last_status = ""

        if handle.submission_mode == "async_poll" and hasattr(handle.job, "status"):
            while True:
                status = handle.job.status()
                status_name = getattr(status, "name", str(status))
                last_status = str(status_name)
                poll_count += 1
                if last_status.upper() in self.TERMINAL_STATES:
                    break
                if self._monotonic() - started > handle.timeout_seconds:
                    raise TimeoutError(
                        f"QCloud job {handle.job_id or '<unknown>'} did not finish within {handle.timeout_seconds}s"
                    )
                self._sleep(handle.poll_interval_seconds)

        result = handle.job.result()
        completed_at = _utc_now_iso()
        if not last_status and hasattr(result, "job_status"):
            try:
                job_status = result.job_status()
                last_status = getattr(job_status, "name", str(job_status))
            except Exception:
                last_status = ""

        return result, {
            "provider_async_submission": handle.submission_mode == "async_poll",
            "provider_submission_mode": handle.submission_mode,
            "provider_job_id": handle.job_id,
            "provider_job_status": last_status,
            "provider_poll_count": poll_count,
            "provider_submitted_at_utc": handle.submitted_at_utc,
            "provider_completed_at_utc": completed_at,
            "provider_timeout_seconds": handle.timeout_seconds,
            "provider_poll_interval_seconds": handle.poll_interval_seconds,
        }

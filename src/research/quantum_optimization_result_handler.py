#!/usr/bin/env python3
"""Handler for processing and storing results from the quantum lane.

Stores quantum and classical baseline results as artifact JSONs,
and provides comparison via benchmark_quantum_vs_classical.compare().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class QuantumResultHandler:
    """Store and compare quantum vs classical optimization results."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.artifacts_dir = repo_root / "artifacts" / "quantum"
        self.classical_dir = repo_root / "artifacts" / "classical"

    def store_result(self, result: Dict[str, Any]) -> Path:
        """Store quantum result as artifact JSON.

        Parameters
        ----------
        result:
            Quantum optimization result dict (from the quantum optimizer bridge).

        Returns
        -------
        Path to the written artifact file.
        """
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        payload = self._normalize_payload(result, baseline=False)
        request_id = payload.get("request_id", "unknown")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"quantum_{request_id}_{ts}.json"
        out_path = self.artifacts_dir / filename
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Quantum result stored: %s", out_path)
        return out_path

    def store_classical_baseline(self, result: Dict[str, Any]) -> Path:
        """Store classical baseline result.

        Parameters
        ----------
        result:
            Classical optimization result dict.

        Returns
        -------
        Path to the written artifact file.
        """
        self.classical_dir.mkdir(parents=True, exist_ok=True)
        payload = self._normalize_payload(result, baseline=True)
        request_id = payload.get("request_id", "unknown")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"classical_{request_id}_{ts}.json"
        out_path = self.classical_dir / filename
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Classical baseline stored: %s", out_path)
        return out_path

    def get_latest_comparison(self) -> Optional[Dict[str, Any]]:
        """Compare latest quantum vs classical results.

        Uses ``src.research.benchmark_quantum_vs_classical.compare`` to
        produce a paired comparison report.  Returns ``None`` if either
        artifact directory is missing or empty.
        """
        if not self.artifacts_dir.exists() or not self.classical_dir.exists():
            logger.info("One or both artifact directories missing; no comparison available.")
            return None

        quantum_files = list(self.artifacts_dir.glob("*.json"))
        classical_files = list(self.classical_dir.glob("*.json"))
        if not quantum_files or not classical_files:
            logger.info("Insufficient artifacts for comparison (quantum=%d, classical=%d).",
                        len(quantum_files), len(classical_files))
            return None

        from src.research.benchmark_quantum_vs_classical import compare

        comparison = compare(self.artifacts_dir, self.classical_dir)
        logger.info("Comparison complete: recommendation=%s", comparison.get("recommendation"))
        return comparison

    def _normalize_payload(self, result: Dict[str, Any], *, baseline: bool) -> Dict[str, Any]:
        """Normalize stored research artifacts without widening execution scope."""
        payload = dict(result)
        diagnostics = dict(payload.get("diagnostics") or {})
        execution_metadata = payload.get("execution_metadata") or diagnostics.get("execution_metadata")

        payload.setdefault("artifact_only", True)
        payload.setdefault("research_only", True)
        payload.setdefault("not_for_direct_execution", True)
        payload.setdefault("baseline_result", baseline)

        if execution_metadata:
            payload["execution_metadata"] = dict(execution_metadata)
            payload["execution_metadata"].setdefault("artifact_only", True)
            payload["execution_metadata"].setdefault("research_only", True)
            payload["execution_metadata"].setdefault("not_for_direct_execution", True)

        return payload

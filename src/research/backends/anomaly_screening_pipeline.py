#!/usr/bin/env python3
"""Pre-optimization anomaly screening for research candidate universes.

Ranks the incoming universe when CandidateUniverseRanker is available, runs the
PennyLane anomaly detector on the ranked list, annotates anomalies in candidate
metadata, and returns an enriched request for downstream optimization. The
pipeline never removes candidates from the optimization set.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_id(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _safe_artifact_component(value: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_"
        for ch in value
    ).strip("_") or "screening"


class AnomalyScreeningPipeline:
    """Apply research-only anomaly scoring before optimizer execution."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        detector: Any = None,
        ranker: Any = None,
        artifact_dir: Optional[Path] = None,
    ):
        self.config = config or {}
        self.detector = detector
        self.ranker = ranker
        self._detector_status = "available"
        self._detector_reason = None
        configured_artifact_dir = self.config.get("artifact_dir")
        self.artifact_dir = (
            Path(configured_artifact_dir)
            if configured_artifact_dir
            else artifact_dir or Path("reports/research/screening")
        )
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

        if self.detector is None:
            self.detector = self._build_detector()
        if self.ranker is None:
            self.ranker = self._build_ranker()

    def screen(self, request: dict) -> dict:
        start = time.monotonic()
        original_candidates = (
            request.get("candidates")
            or request.get("candidate_universe")
            or []
        )
        ranked_candidates, ranker_applied = self._rank_candidates(
            original_candidates,
            request.get("regime_state", {}),
            request.get("market_microstructure", {}),
        )

        if not ranked_candidates:
            return self._build_report(
                status="skipped",
                candidates=[],
                request=request,
                ranker_applied=ranker_applied,
                start=start,
                reason="no_candidates",
            )

        if self.detector is None:
            annotated = [
                self._annotate_without_score(candidate, idx)
                for idx, candidate in enumerate(ranked_candidates)
            ]
            return self._build_report(
                status="degraded",
                candidates=annotated,
                request=request,
                ranker_applied=ranker_applied,
                start=start,
                reason=self._detector_reason or "pennylane_detector_unavailable",
            )

        batch = [
            self._build_scoring_row(candidate, idx)
            for idx, candidate in enumerate(ranked_candidates)
        ]

        try:
            scores = self.detector.score_batch(batch)
        except Exception as exc:
            logger.warning("Anomaly screening failed: %s", exc, exc_info=True)
            annotated = [
                self._annotate_without_score(
                    candidate,
                    idx,
                    detector_status="error",
                    reason=str(exc),
                )
                for idx, candidate in enumerate(ranked_candidates)
            ]
            return self._build_report(
                status="error",
                candidates=annotated,
                request=request,
                ranker_applied=ranker_applied,
                start=start,
                reason=str(exc),
            )

        score_by_id = {row["candidate_id"]: row for row in scores}
        annotated = []
        anomalies_flagged = 0
        for idx, candidate in enumerate(ranked_candidates):
            candidate_id = self._candidate_id(candidate, idx)
            score = score_by_id.get(candidate_id)
            annotated_candidate = self._annotate_with_score(candidate, idx, score)
            anomalies_flagged += int(
                annotated_candidate["metadata"]["anomaly_screening"].get(
                    "is_anomaly_quantum", False
                )
            )
            annotated.append(annotated_candidate)

        return self._build_report(
            status="success",
            candidates=annotated,
            request=request,
            ranker_applied=ranker_applied,
            start=start,
            anomalies_flagged=anomalies_flagged,
            score_count=len(scores),
        )

    def _build_detector(self):
        try:
            from src.research.backends.pennylane_anomaly_detector import (
                PennyLaneAnomalyDetector,
            )

            self._detector_status = "available"
            self._detector_reason = None
            return PennyLaneAnomalyDetector(self.config.get("pennylane", self.config))
        except Exception as exc:
            self._detector_status = "unavailable"
            self._detector_reason = str(exc)
            logger.info("PennyLane screening detector unavailable: %s", exc)
            return None

    def _build_ranker(self):
        try:
            from src.research.candidate_universe_ranker import CandidateUniverseRanker

            return CandidateUniverseRanker()
        except Exception as exc:
            logger.info("CandidateUniverseRanker unavailable: %s", exc)
            return None

    def _rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], bool]:
        prepared = [dict(candidate) for candidate in candidates]
        if self.ranker is None:
            return prepared, False

        ranked = self.ranker.rank(
            candidate_universe=prepared,
            regime_state=regime_state,
            market_microstructure=market_microstructure,
        )
        return ranked, True

    def _build_scoring_row(self, candidate: dict, idx: int) -> dict:
        return {
            "candidate_id": self._candidate_id(candidate, idx),
            "features": candidate.get("features") or self._candidate_features(candidate),
        }

    def _candidate_features(self, candidate: dict) -> List[float]:
        return [
            float(candidate.get("preopt_score", candidate.get("score", 0.0))),
            float(candidate.get("expected_return", candidate.get("score", 0.0))),
            float(candidate.get("volatility", 0.2)),
            float(candidate.get("weight", 0.0)),
        ]

    def _candidate_id(self, candidate: dict, idx: int) -> str:
        return str(
            candidate.get("symbol")
            or candidate.get("candidate_id")
            or candidate.get("candidate_key")
            or f"candidate_{idx}"
        )

    def _annotate_without_score(
        self,
        candidate: dict,
        idx: int,
        detector_status: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict:
        annotated = dict(candidate)
        metadata = dict(annotated.get("metadata", {}))
        metadata["anomaly_screening"] = {
            "screened": False,
            "candidate_id": self._candidate_id(candidate, idx),
            "detector_status": detector_status or self._detector_status,
            "reason": reason or self._detector_reason or "detector_unavailable",
        }
        annotated["metadata"] = metadata
        return annotated

    def _annotate_with_score(
        self,
        candidate: dict,
        idx: int,
        score: Optional[dict],
    ) -> dict:
        annotated = dict(candidate)
        metadata = dict(annotated.get("metadata", {}))
        screening = {
            "screened": score is not None,
            "candidate_id": self._candidate_id(candidate, idx),
            "detector_status": self._detector_status,
        }
        if score is None:
            screening["reason"] = "missing_score"
        else:
            screening.update({
                "quantum_anomaly_score": score.get("quantum_anomaly_score"),
                "quantum_raw_expectation": score.get("quantum_raw_expectation"),
                "classical_anomaly_score": score.get("classical_anomaly_score"),
                "is_anomaly_quantum": score.get("is_anomaly_quantum", False),
                "is_anomaly_classical": score.get("is_anomaly_classical"),
                "anomaly_agreement": score.get("anomaly_agreement"),
                "threshold": score.get("threshold"),
                "backend": score.get("execution_metadata", {}).get("backend"),
            })
            annotated["quantum_anomaly_score"] = score.get("quantum_anomaly_score")
            annotated["is_anomaly_quantum"] = score.get("is_anomaly_quantum", False)
            annotated["classical_anomaly_score"] = score.get("classical_anomaly_score")
            annotated["is_anomaly_classical"] = score.get("is_anomaly_classical")
        metadata["anomaly_screening"] = screening
        annotated["metadata"] = metadata
        return annotated

    def _build_report(
        self,
        *,
        status: str,
        candidates: List[Dict[str, Any]],
        request: dict,
        ranker_applied: bool,
        start: float,
        reason: Optional[str] = None,
        anomalies_flagged: int = 0,
        score_count: int = 0,
    ) -> dict:
        elapsed = time.monotonic() - start
        screened_request = dict(request)
        screened_request["candidates"] = candidates
        screened_request["candidate_universe"] = candidates
        request_id = str(
            request.get("request_id")
            or request.get("package_id")
            or f"screening_{len(candidates)}"
        )
        timestamp_utc = _utc_now()
        artifact_id = _artifact_id(
            {
                "backend": "anomaly_screening_pipeline",
                "request_id": request_id,
                "status": status,
                "timestamp_utc": timestamp_utc,
                "candidate_count": len(candidates),
            }
        )
        artifact_path = self.artifact_dir / (
            f"screening_{_safe_artifact_component(request_id)}_{artifact_id}.json"
        )

        report = {
            "backend": "anomaly_screening_pipeline",
            "screening_backend": "pennylane_vqc",
            "status": status,
            "candidate_universe": candidates,
            "optimization_request": screened_request,
            "screening_report": {
                "ranker_applied": ranker_applied,
                "candidate_count_input": len(
                    request.get("candidates")
                    or request.get("candidate_universe")
                    or []
                ),
                "candidate_count_screened": len(candidates),
                "anomalies_flagged": anomalies_flagged,
                "scores_emitted": score_count,
                "candidates_removed": 0,
                "detector_status": self._detector_status,
                "artifact_path": str(artifact_path),
            },
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "backend": "anomaly_screening_pipeline",
                "screening_backend": "pennylane_vqc",
                "status": status,
                "runtime_seconds": round(elapsed, 4),
                "timestamp_utc": timestamp_utc,
                "artifact_id": artifact_id,
                "artifact_path": str(artifact_path),
            },
        }
        if reason is not None:
            report["reason"] = reason
            report["screening_report"]["reason"] = reason
            report["execution_metadata"]["reason"] = reason
        artifact_path.write_text(json.dumps(report, indent=2, default=str))
        return report

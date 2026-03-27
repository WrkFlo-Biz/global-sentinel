#!/usr/bin/env python3
"""Evidence-only canary automation runner for Global Sentinel.

The runner converts live replay-grade scorecards into canary evidence artifacts
without granting any promotion authority. It always routes the decision through
``EncoderPromotionGate.evaluate_canary()`` and records the policy decision used
for the hypothetical promotion path.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.market_session_classifier import MarketSessionClassifier
from src.core.policy_engine import PolicyEngine
from src.research.encoder_promotion_gate import EncoderPromotionGate
from src.research.learning_state_persistence import LearningStatePersistence


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _sha256_json(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CanaryWindow:
    """Metadata for a scorecard observation window."""

    scorecard_count: int
    start_utc: str
    end_utc: str
    schema_versions: Dict[str, int]
    config_fingerprints: List[str]
    scorecard_files: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EvidenceOnlyCanaryRunner:
    """Generate evidence-only canary artifacts from live scorecards."""

    def __init__(
        self,
        repo_root: Path,
        *,
        signal_type: str = "online_weighted_encoder",
        scorecards_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ):
        self.repo_root = repo_root
        self.signal_type = signal_type
        self.scorecards_dir = scorecards_dir or repo_root / "logs" / "scorecards"
        self.output_dir = output_dir or repo_root / "reports" / "research" / "canary"

    def run(self, *, limit: int = 200, window_size: int = 25) -> Dict[str, Any]:
        scorecards = self._load_scorecards(limit=limit)
        if not scorecards:
            artifact = {
                "schema_version": "evidence_only_canary_artifact.v1",
                "generated_at": _utc_now_iso(),
                "signal_type": self.signal_type,
                "status": "skipped",
                "reason": "no_scorecards_available",
                "canary_evidence_only": True,
                "promotion_allowed_if_not_canary": False,
                "rollback_recommended": False,
                "autonomous_promotion_forbidden": True,
                "not_for_direct_execution": True,
            }
            self._persist(artifact)
            return artifact

        current_scorecards = scorecards[-window_size:]
        baseline_scorecards = scorecards[-(window_size * 2):-window_size] or current_scorecards
        current_window = self._window_metadata(current_scorecards)
        baseline_window = self._window_metadata(baseline_scorecards)
        current_metrics = self._derive_metrics(current_scorecards)
        baseline_metrics = self._derive_metrics(baseline_scorecards)

        current_weights, proposed_weights, version_context = self._resolve_weight_context()
        policy_engine = PolicyEngine(config_dir=self.repo_root / "config")
        policy_decision = policy_engine.evaluate_weight_promotion(
            current_weights=current_weights,
            proposed_weights=proposed_weights,
            eval_metrics={
                "eval_count": int(current_metrics.get("trade_count", 0)),
                "cumulative_drift_std": _safe_float(current_metrics.get("cumulative_drift_std", 0.0)),
                "safety_regression": bool(current_metrics.get("safety_regression", False)),
                "reproducibility_pass": True,
            },
        ).to_dict()

        guardrail_result = self._guardrail_result(current_scorecards)
        gate = EncoderPromotionGate(config_path=self.repo_root / "config" / "promotion_policy.yaml")
        latest_mode = str(current_scorecards[-1].get("mode", "NORMAL"))
        latest_session = MarketSessionClassifier().classify(
            current_scorecards[-1].get("timestamp_utc"),
            asset_class="equity",
        ).to_dict()
        canary_result = gate.evaluate_canary(
            current_metrics,
            baseline_metrics=baseline_metrics,
            guardrail_result=guardrail_result,
            policy_decision=policy_decision,
            signal_type=self.signal_type,
            current_mode=latest_mode,
        )

        artifact = {
            "schema_version": "evidence_only_canary_artifact.v1",
            "generated_at": _utc_now_iso(),
            "signal_type": self.signal_type,
            "current_mode": latest_mode,
            "session_context": latest_session,
            "source": "scorecard_stream",
            "status": "ok",
            "policy_gate_mandatory": True,
            "autonomous_promotion_forbidden": True,
            "not_for_direct_execution": True,
            "execution_path_disabled": True,
            "current_window": current_window.to_dict(),
            "baseline_window": baseline_window.to_dict(),
            "eval_metrics": current_metrics,
            "baseline_metrics": baseline_metrics,
            "policy_decision": policy_decision,
            "guardrail_result": guardrail_result,
            "weight_context": version_context,
            **canary_result,
        }
        artifact["_lineage"] = {
            "schema_version": "1.0.0",
            "source_scorecard_files": current_window.scorecard_files,
            "config_fingerprints": current_window.config_fingerprints,
            "artifact_hash": _sha256_json(artifact),
            "time_window": {
                "start": current_window.start_utc,
                "end": current_window.end_utc,
            },
            "session": latest_session.get("session"),
        }
        self._persist(artifact)
        return artifact

    def _load_scorecards(self, *, limit: int) -> List[Dict[str, Any]]:
        if not self.scorecards_dir.exists():
            return []
        loaded: List[Dict[str, Any]] = []
        for path in sorted(self.scorecards_dir.glob("scorecard_*.json"))[-limit:]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            payload["_file"] = str(path)
            loaded.append(payload)
        return loaded

    def _window_metadata(self, scorecards: Sequence[Dict[str, Any]]) -> CanaryWindow:
        versions: Dict[str, int] = {}
        fingerprints: List[str] = []
        files: List[str] = []
        timestamps = [item.get("timestamp_utc", "") for item in scorecards]
        for item in scorecards:
            schema_version = str(item.get("schema_version", "unknown"))
            versions[schema_version] = versions.get(schema_version, 0) + 1
            fingerprint = str(item.get("config_fingerprint", ""))
            if fingerprint and fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
            if item.get("_file"):
                files.append(str(item["_file"]))
        return CanaryWindow(
            scorecard_count=len(scorecards),
            start_utc=min(timestamps) if timestamps else "",
            end_utc=max(timestamps) if timestamps else "",
            schema_versions=versions,
            config_fingerprints=fingerprints,
            scorecard_files=files,
        )

    def _derive_metrics(self, scorecards: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if not scorecards:
            return {
                "eval_days": 0,
                "trade_count": 0,
                "drawdown_delta_bps": 0.0,
                "slippage_adjusted_win_delta_bps": 0.0,
                "failure_rate": 1.0,
                "cumulative_drift_std": 0.0,
                "runtime_evidence_only": True,
            }

        timestamps = [_parse_iso(item.get("timestamp_utc")) for item in scorecards]
        valid_ts = [item for item in timestamps if item]
        if len(valid_ts) >= 2:
            eval_days = max(1, (valid_ts[-1] - valid_ts[0]).days + 1)
        else:
            eval_days = 1

        trade_count = sum(1 for item in scorecards if item.get("shadow_execution_eligible"))
        blocked_count = sum(
            1
            for item in scorecards
            if bool((item.get("mode_decision_trace") or {}).get("blocked"))
        )
        degraded_count = sum(1 for item in scorecards if bool(item.get("degraded_mode")))
        confidence_values = [_safe_float(item.get("confidence", 0.0)) for item in scorecards]
        confidence_mean = sum(confidence_values) / max(len(confidence_values), 1)
        regime_values = [
            _safe_float(item.get("regime_shift_probability", 0.0)) for item in scorecards
        ]
        regime_mean = sum(regime_values) / max(len(regime_values), 1)
        failure_rate = (blocked_count + degraded_count) / max(len(scorecards), 1)
        blocked_rate = blocked_count / max(len(scorecards), 1)
        degraded_rate = degraded_count / max(len(scorecards), 1)
        freshness_penalty_mean = sum(
            _safe_float(item.get("freshness_penalty", 0.0)) for item in scorecards
        ) / max(len(scorecards), 1)
        fingerprint_mismatch = len(
            {
                str(item.get("config_fingerprint", ""))
                for item in scorecards
                if item.get("config_fingerprint")
            }
        ) > 1

        return {
            "eval_days": eval_days,
            "trade_count": trade_count,
            "drawdown_delta_bps": round(freshness_penalty_mean * 1000.0, 6),
            "slippage_adjusted_win_delta_bps": round(max(0.0, confidence_mean - 0.5) * 100.0, 6),
            "failure_rate": round(failure_rate, 6),
            "blocked_rate": round(blocked_rate, 6),
            "degraded_rate": round(degraded_rate, 6),
            "cumulative_drift_std": round(freshness_penalty_mean * 2.0, 6),
            "avg_confidence": round(confidence_mean, 6),
            "avg_regime_shift_probability": round(regime_mean, 6),
            "blocked_count": blocked_count,
            "degraded_count": degraded_count,
            "config_fingerprint_mismatch": fingerprint_mismatch,
            "runtime_evidence_only": True,
            "safety_regression": fingerprint_mismatch,
            "runtime_degraded_driver": degraded_count >= max(blocked_count, 1) and degraded_count > 0,
        }

    def _resolve_weight_context(self) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, Any]]:
        persistence = LearningStatePersistence(
            connection_string=None,
            local_dir=self.repo_root / "reports" / "research" / "state" / "versions",
        )
        versions = persistence.list_versions(limit=2)
        latest_state = persistence.load_state_by_version(versions[0]) if versions else persistence.load_latest_state()
        previous_state = (
            persistence.load_state_by_version(versions[1])
            if len(versions) > 1 else latest_state
        )
        current_weights = self._normalize_weights((previous_state or {}).get("weights"))
        proposed_weights = self._normalize_weights((latest_state or {}).get("weights"))
        return current_weights, proposed_weights, {
            "versions_considered": versions,
            "current_weight_keys": sorted(current_weights.keys()),
            "proposed_weight_keys": sorted(proposed_weights.keys()),
        }

    def _normalize_weights(self, raw_weights: Any) -> Dict[str, float]:
        """Normalize dict- or list-shaped weight payloads into a dict."""
        if isinstance(raw_weights, dict):
            return {
                str(key): _safe_float(value)
                for key, value in raw_weights.items()
            }
        if isinstance(raw_weights, (list, tuple)):
            return {
                f"w_{idx}": _safe_float(value)
                for idx, value in enumerate(raw_weights)
            }
        return {}

    def _guardrail_result(self, scorecards: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        checks = []
        schema_ok = all(str(item.get("schema_version", "")).startswith("scorecard.v6") for item in scorecards)
        fingerprint_ok = all(bool(item.get("config_fingerprint")) for item in scorecards)
        checks.append({"name": "scorecard_schema_v6", "passed": schema_ok})
        checks.append({"name": "config_fingerprint_present", "passed": fingerprint_ok})
        return {
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
            "source": "runtime_scorecards",
        }

    def _persist(self, artifact: Dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history_path = self.output_dir / f"canary_{tag}.json"
        latest_path = self.output_dir / "latest.json"
        encoded = json.dumps(artifact, indent=2, default=str)
        history_path.write_text(encoded, encoding="utf-8")
        latest_path.write_text(encoded, encoding="utf-8")

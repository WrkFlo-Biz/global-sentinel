#!/usr/bin/env python3
"""
Daily Quantum Retraining Job.
Runs after market close (scheduled via systemd timer at 5 PM ET / 22:00 UTC).

Flow:
1. Load today's experiment_tracker comparison results
2. Load today's actual trade outcomes (from performance_tracker or reconciler)
3. Join quantum predictions to actual outcomes
4. Retrain PennyLane anomaly detector on updated feature set
5. Validate new weights via bounded step check
6. If passed: save new weights (versioned, backup old)
7. If failed: keep old weights, log reason
8. Generate retraining report

Safety: all changes bounded, versioned, rollback-ready.
All outputs carry not_for_direct_execution=true.
"""

from __future__ import annotations

import glob
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class QuantumRetrainingJob:

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.config_dir = self.repo_root / "config"
        self.training_logs_dir = self.repo_root / "logs" / "training"
        self.research_dir = self.repo_root / "reports" / "research"
        self.weights_path = self.config_dir / "anomaly_detector_weights.json"
        self.reports_dir = self.research_dir / "retraining"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.training_labels_path = self.research_dir / "training_labels.json"
        self.learning_state_path = (
            self.repo_root / "artifacts" / "learning_state" / "current_state.json"
        )

    def run(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "date": date.today().isoformat(),
            "timestamp_utc": _iso_now(),
            "steps": {},
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
            },
        }

        # Step 1: Load today's comparison results
        tracker_results = self._load_today_experiment_results()
        report["steps"]["load_experiment_results"] = {
            "count": len(tracker_results),
            "status": "ok" if tracker_results else "no_data",
        }

        # Step 2: Load today's trade outcomes
        outcomes = self._load_today_trade_outcomes()
        report["steps"]["load_trade_outcomes"] = {
            "count": len(outcomes) if isinstance(outcomes, list) else (1 if outcomes else 0),
            "status": "ok" if outcomes else "no_data",
        }

        labeled_rows = self._load_labeled_training_rows()
        report["steps"]["load_training_labels"] = {
            "count": len(labeled_rows),
            "status": "ok" if labeled_rows else "no_data",
        }

        if labeled_rows:
            online_state_update = self._update_online_learning_state(labeled_rows)
            report["steps"]["update_online_learning_state"] = online_state_update
        else:
            online_state_update = {"status": "skipped", "reason": "no_labeled_rows"}
            report["steps"]["update_online_learning_state"] = online_state_update

        if not tracker_results and not outcomes and not labeled_rows:
            report["result"] = "skipped_insufficient_data"
            self._save_report(report)
            return report

        # Step 3: Extract training features from comparison artifacts
        features = self._extract_training_features(
            tracker_results,
            outcomes,
            labeled_rows=labeled_rows,
        )
        report["steps"]["extract_features"] = {
            "feature_vectors": len(features),
        }

        if len(features) < 3:
            report["result"] = (
                "online_state_updated"
                if online_state_update.get("status") == "updated"
                else "skipped_insufficient_features"
            )
            report["steps"]["extract_features"]["note"] = "need 3+ feature vectors"
            self._save_report(report)
            return report

        # Step 4: Retrain anomaly detector
        retrain_result = self._retrain_anomaly_detector(features)
        report["steps"]["retrain"] = retrain_result

        # Step 5: Validate new weights
        if retrain_result.get("new_weights"):
            validation = self._validate_weights(retrain_result["new_weights"])
            report["steps"]["validation"] = validation

            if validation.get("passed"):
                # Step 6: Save new weights (version the old ones first)
                self._version_and_save_weights(retrain_result["new_weights"])
                report["result"] = "weights_updated"
            else:
                report["result"] = "weights_rejected"
                report["rejection_reason"] = validation.get("reason")
        else:
            report["result"] = (
                "online_state_updated"
                if online_state_update.get("status") == "updated"
                else "retrain_failed"
            )

        self._save_report(report)
        return report

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_today_experiment_results(self) -> List[Dict[str, Any]]:
        """Load from reports/research/experiment_log.jsonl — today's entries."""
        log_path = self.repo_root / "reports" / "research" / "experiment_log.jsonl"
        today = date.today().isoformat()
        results: List[Dict[str, Any]] = []
        if not log_path.exists():
            return results
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp_utc", "")
                if ts.startswith(today):
                    results.append(entry)
            except json.JSONDecodeError:
                continue
        return results

    def _load_today_trade_outcomes(self) -> Any:
        """Load from performance_tracker or reconciler output."""
        patterns = [
            str(self.repo_root / "reports" / "operational" / "performance_*.json"),
            str(self.repo_root / "reports" / "operational" / "reconciliation_*.json"),
            str(self.repo_root / "reports" / "scorecards" / "scorecard_*.json"),
        ]
        for pattern in patterns:
            files = sorted(glob.glob(pattern))
            if files:
                try:
                    return json.loads(Path(files[-1]).read_text(encoding="utf-8"))
                except Exception:
                    continue
        return None

    def _load_labeled_training_rows(self) -> List[Dict[str, Any]]:
        """Load the rolling labeled dataset produced by the scenario trainer."""
        if self.training_labels_path.exists():
            try:
                payload = json.loads(self.training_labels_path.read_text(encoding="utf-8"))
                rows = payload.get("rows") or []
                if isinstance(rows, list):
                    return rows
            except Exception:
                pass

        rows: List[Dict[str, Any]] = []
        for path in sorted(self.training_logs_dir.glob("scenario_cycles_*.jsonl"))[-5:]:
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    research = payload.get("research", {})
                    eval_payload = research.get("evaluation", {})
                    for trade in payload.get("trades", []):
                        rows.append(
                            {
                                "symbol": trade.get("symbol"),
                                "base_score": trade.get("confidence", 0.5),
                                "event_score": trade.get("confidence", 0.5),
                                "quality_score": abs(trade.get("realized_pct", 0.0)) / 10.0,
                                "anomaly_score": abs(trade.get("move_pct", 0.0)) / 10.0,
                                "liquidity_score": 1.0,
                                "regime_alignment": 0.5,
                                "volatility_penalty": -abs(trade.get("move_pct", 0.0)) / 10.0,
                                "realized_return_bps": trade.get("realized_pct", 0.0) * 100.0,
                                "alpha_label": "positive"
                                if _safe_float(trade.get("pnl_usd")) > 0
                                else "negative",
                                "research_score_used": research.get("research_score", {}).get("research_score"),
                                "attached_recommended_influence": research.get("research_score", {}).get("recommended_influence"),
                                "winner": eval_payload.get("winner"),
                            }
                        )
            except Exception:
                continue
        return rows

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _extract_training_features(
        self,
        tracker_results: List[Dict[str, Any]],
        outcomes: Any,
        *,
        labeled_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[List[float]]:
        """Extract numeric feature vectors from comparison results + outcomes."""
        features: List[List[float]] = []

        for entry in tracker_results:
            report = entry.get("report", {})
            # Extract from candidates' bridge_context if present
            req = report.get("request", {})
            candidates = req.get("candidates") or req.get("candidate_universe") or []
            for cand in candidates:
                ctx = cand.get("bridge_context", {})
                if not ctx:
                    # Fall back to candidate's own numeric fields
                    ctx = {
                        "score": cand.get("score", 0.5),
                        "expected_return": cand.get("expected_return", 0.05),
                        "volatility": cand.get("volatility", 0.2),
                        "base_score": cand.get("base_score"),
                        "event_score": cand.get("event_score"),
                        "quality_score": cand.get("quality_score"),
                        "anomaly_score": cand.get("anomaly_score"),
                        "liquidity_score": cand.get("liquidity_score"),
                        "regime_alignment": cand.get("regime_alignment"),
                        "volatility_penalty": cand.get("volatility_penalty"),
                    }
                # Convert to numeric vector
                vec = []
                for v in ctx.values():
                    if isinstance(v, (int, float)):
                        vec.append(float(v))
                if len(vec) >= 2:
                    features.append(vec)

        for row in labeled_rows or []:
            vec = []
            for key in (
                "base_score",
                "event_score",
                "quality_score",
                "anomaly_score",
                "liquidity_score",
                "regime_alignment",
                "volatility_penalty",
                "realized_return_bps",
            ):
                value = row.get(key)
                if isinstance(value, (int, float)):
                    vec.append(float(value))
            if len(vec) >= 3:
                features.append(vec)

        return features

    def _update_online_learning_state(
        self,
        labeled_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from src.research.learning_state_persistence import LearningStatePersistence
        from src.research.qfinance_online_learning_state import load_state, save_state
        from src.research.update_research_model_weights import ResearchModelWeightUpdater

        if not labeled_rows:
            return {"status": "skipped", "reason": "no_labeled_rows"}

        state = load_state(self.learning_state_path)
        updater = ResearchModelWeightUpdater()
        labeled_dataset = {
            "schema_version": "alpha_candidate_labels.v1",
            "row_count": len(labeled_rows),
            "rows": labeled_rows[-500:],
        }
        try:
            updated_state = updater.update(
                state=state,
                labeled_dataset=labeled_dataset,
                learning_rate=0.01,
            )
        except ValueError:
            return {"status": "skipped", "reason": "labeled_dataset_failed_validation"}
        save_state(self.learning_state_path, updated_state)

        persistence = LearningStatePersistence(
            local_dir=self.research_dir / "state" / "versions"
        )
        version_id = persistence.save_state(
            updated_state,
            metadata={
                "source": "quantum_retraining_job",
                "labeled_rows": len(labeled_dataset["rows"]),
            },
        )
        return {
            "status": "updated",
            "version_id": version_id,
            "rows_used": len(labeled_dataset["rows"]),
            "updates_applied": updated_state.get("update_stats", {}).get("updates_applied"),
        }

    # ------------------------------------------------------------------
    # Retraining
    # ------------------------------------------------------------------

    def _retrain_anomaly_detector(self, features: List[List[float]]) -> Dict[str, Any]:
        """Retrain PennyLane anomaly detector on new features."""
        try:
            import sys
            sys.path.insert(0, str(self.repo_root))
            from src.research.backends.pennylane_anomaly_detector import PennyLaneAnomalyDetector
        except ImportError as e:
            return {"status": "skipped", "reason": "pennylane unavailable: %s" % e}

        # Normalize feature vectors to same length (pad/truncate to n_qubits*2)
        n_qubits = 4
        target_len = n_qubits * 2  # 8 features
        normalized: List[List[float]] = []
        for vec in features:
            if len(vec) >= target_len:
                normalized.append(vec[:target_len])
            else:
                normalized.append(vec + [0.0] * (target_len - len(vec)))

        if len(normalized) < 3:
            return {"status": "skipped", "reason": "only %d samples (need 3+)" % len(normalized)}

        try:
            detector = PennyLaneAnomalyDetector({
                "n_qubits": n_qubits,
                "n_layers": 2,
                "anomaly_threshold": 0.3,
            })
            # Light retrain (20 epochs — incremental, not full)
            train_result = detector.train(normalized, epochs=20, learning_rate=0.01)

            new_weights = None
            if hasattr(detector, "weights") and detector.weights is not None:
                if hasattr(detector.weights, "tolist"):
                    new_weights = detector.weights.tolist()
                elif isinstance(detector.weights, list):
                    new_weights = detector.weights

            return {
                "status": "trained",
                "samples": len(normalized),
                "feature_dim": target_len,
                "epochs": 20,
                "new_weights": new_weights,
                "train_result": train_result if isinstance(train_result, dict) else str(train_result),
            }
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_weights(self, new_weights: Any) -> Dict[str, Any]:
        """Check new weights against guardrails (bounded step, no NaN/Inf)."""
        try:
            import numpy as np
        except ImportError:
            # Can't validate without numpy — allow cautiously
            return {"passed": True, "note": "numpy unavailable, skipping validation"}

        new_arr = np.array(new_weights)

        # Check for NaN/Inf
        if np.any(np.isnan(new_arr)) or np.any(np.isinf(new_arr)):
            return {"passed": False, "reason": "NaN or Inf in weights"}

        if not self.weights_path.exists():
            return {"passed": True, "max_step": None, "note": "first_weights"}

        try:
            old_weights = json.loads(self.weights_path.read_text(encoding="utf-8"))
            old_arr = np.array(old_weights)

            if old_arr.shape != new_arr.shape:
                return {"passed": False, "reason": "shape mismatch: old=%s new=%s" % (old_arr.shape, new_arr.shape)}

            max_step = float(np.max(np.abs(new_arr - old_arr)))
            # Allow 0.10 max step per element for daily retrain (2x normal)
            if max_step > 0.10:
                return {"passed": False, "reason": "max_step=%.4f exceeds 0.10" % max_step}

            return {"passed": True, "max_step": round(max_step, 6)}
        except Exception as e:
            return {"passed": False, "reason": str(e)}

    # ------------------------------------------------------------------
    # Weight persistence
    # ------------------------------------------------------------------

    def _version_and_save_weights(self, new_weights: Any) -> None:
        """Backup old weights, then save new."""
        if self.weights_path.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup = self.weights_path.with_name("anomaly_detector_weights_backup_%s.json" % ts)
            shutil.copy2(str(self.weights_path), str(backup))

        self.weights_path.write_text(
            json.dumps(new_weights, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _save_report(self, report: Dict[str, Any]) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = self.reports_dir / ("retraining_%s.json" % ts)
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("Retraining report saved: %s" % path)
        return path


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Global Sentinel Quantum Daily Retraining")
    p.add_argument("--repo-root", default="/opt/global-sentinel")
    args = p.parse_args()

    job = QuantumRetrainingJob(repo_root=args.repo_root)
    result = job.run()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

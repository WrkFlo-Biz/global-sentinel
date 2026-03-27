#!/usr/bin/env python3
"""Replay runner for Global Sentinel runtime decisions.

Reconstructs and verifies decisions from persisted scorecards, manifests,
and lineage data. Supports both allowed and blocked decision replay.

Usage:
    runner = DecisionReplayRunner(repo_root)
    result = runner.replay_scorecard(scorecard_path)
    result = runner.replay_range(start_utc, end_utc)
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReplayVerification:
    """Result of replaying a single decision."""

    scorecard_file: str = ""
    timestamp_utc: str = ""
    cycle: int = 0
    mode: str = ""
    regime_shift_probability: float = 0.0
    confidence: float = 0.0

    # Replay-grade fields present
    has_mode_decision_trace: bool = False
    has_quorum_state: bool = False
    has_policy_trace: bool = False
    has_freshness_state: bool = False
    has_config_fingerprint: bool = False
    has_threshold_values: bool = False

    # Decision details
    mode_blocked: bool = False
    blocking_reason: str = ""
    freshness_penalty: float = 0.0
    original_confidence: Optional[float] = None
    degraded_mode: bool = False
    config_fingerprint: str = ""

    # Verification
    replay_grade: bool = False
    missing_fields: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


REPLAY_REQUIRED_FIELDS = [
    "schema_version",
    "timestamp_utc",
    "cycle",
    "mode",
    "regime_shift_probability",
    "confidence",
    "threshold_values_used",
    "mode_decision_trace",
    "feature_freshness",
    "config_fingerprint",
]


class DecisionReplayRunner:
    """Reconstruct and verify runtime decisions from scorecards."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.scorecards_dir = repo_root / "logs" / "scorecards"
        self.events_dir = repo_root / "logs" / "events"

    def replay_scorecard(self, scorecard_path: Path) -> ReplayVerification:
        """Replay a single scorecard and verify it's replay-grade."""
        try:
            scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
        except Exception as e:
            v = ReplayVerification(scorecard_file=str(scorecard_path))
            v.warnings.append(f"Failed to load scorecard: {e}")
            return v

        return self._verify_scorecard(scorecard, str(scorecard_path))

    def replay_range(
        self,
        start_utc: Optional[str] = None,
        end_utc: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Replay all scorecards in a time range."""
        if not self.scorecards_dir.exists():
            return {"error": "scorecards_dir not found", "results": []}

        files = sorted(self.scorecards_dir.glob("scorecard_*.json"))
        results: List[ReplayVerification] = []
        replay_grade_count = 0
        blocked_decisions: List[Dict[str, Any]] = []
        degraded_decisions: List[Dict[str, Any]] = []

        for f in files[-limit:]:
            try:
                sc = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue

            ts = sc.get("timestamp_utc", "")
            if start_utc and ts < start_utc:
                continue
            if end_utc and ts > end_utc:
                continue

            v = self._verify_scorecard(sc, str(f))
            results.append(v)
            if v.replay_grade:
                replay_grade_count += 1
            if v.mode_blocked:
                blocked_decisions.append({
                    "file": v.scorecard_file,
                    "cycle": v.cycle,
                    "blocking_reason": v.blocking_reason,
                    "timestamp": v.timestamp_utc,
                })
            if v.degraded_mode:
                degraded_decisions.append({
                    "file": v.scorecard_file,
                    "cycle": v.cycle,
                    "freshness_penalty": v.freshness_penalty,
                    "timestamp": v.timestamp_utc,
                })

        return {
            "schema_version": "replay_report.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_scorecards": len(results),
            "replay_grade_count": replay_grade_count,
            "replay_grade_ratio": replay_grade_count / len(results) if results else 0,
            "blocked_decisions": blocked_decisions,
            "degraded_decisions": degraded_decisions,
            "results": [r.to_dict() for r in results],
        }

    def verify_config_consistency(self, limit: int = 50) -> Dict[str, Any]:
        """Check for config fingerprint drift across recent scorecards."""
        if not self.scorecards_dir.exists():
            return {"error": "scorecards_dir not found"}

        files = sorted(self.scorecards_dir.glob("scorecard_*.json"))
        fingerprints: List[Dict[str, Any]] = []

        for f in files[-limit:]:
            try:
                sc = json.loads(f.read_text(encoding="utf-8"))
                fp = sc.get("config_fingerprint", "")
                if fp:
                    fingerprints.append({
                        "file": str(f),
                        "cycle": sc.get("cycle", 0),
                        "timestamp": sc.get("timestamp_utc", ""),
                        "fingerprint": fp,
                    })
            except Exception:
                continue

        unique_fps = set(e["fingerprint"] for e in fingerprints)
        drift_events: List[Dict[str, Any]] = []
        for i in range(1, len(fingerprints)):
            if fingerprints[i]["fingerprint"] != fingerprints[i - 1]["fingerprint"]:
                drift_events.append({
                    "from_cycle": fingerprints[i - 1]["cycle"],
                    "to_cycle": fingerprints[i]["cycle"],
                    "from_fingerprint": fingerprints[i - 1]["fingerprint"],
                    "to_fingerprint": fingerprints[i]["fingerprint"],
                    "timestamp": fingerprints[i]["timestamp"],
                })

        return {
            "schema_version": "config_consistency.v1",
            "total_checked": len(fingerprints),
            "unique_fingerprints": len(unique_fps),
            "drift_events": drift_events,
            "config_stable": len(unique_fps) <= 1,
        }

    def _verify_scorecard(self, sc: Dict[str, Any], filepath: str) -> ReplayVerification:
        """Verify a single scorecard for replay-grade completeness."""
        missing = [f for f in REPLAY_REQUIRED_FIELDS if f not in sc or sc[f] is None]
        warnings: List[str] = []

        # Mode decision trace
        mdt = sc.get("mode_decision_trace", {})
        has_mdt = isinstance(mdt, dict) and "final_mode" in mdt
        has_quorum = sc.get("quorum_state") is not None or (
            isinstance(mdt, dict) and mdt.get("quorum_evaluation") is not None
        )
        has_policy = sc.get("policy_decision_trace") is not None or (
            isinstance(mdt, dict) and mdt.get("policy_evaluation") is not None
        )

        # Freshness
        ff = sc.get("feature_freshness")
        has_freshness = isinstance(ff, dict) and "error" not in ff

        # Config fingerprint
        cfp = sc.get("config_fingerprint", "")
        has_cfp = bool(cfp)

        # Thresholds
        has_thresholds = bool(sc.get("threshold_values_used"))

        # Blocked?
        mode_blocked = isinstance(mdt, dict) and mdt.get("blocked", False)
        blocking_reason = (mdt.get("blocking_reason", "") if isinstance(mdt, dict) else "")

        # Freshness penalty
        fp = sc.get("freshness_penalty", 0.0)
        oc = sc.get("original_confidence")

        # Schema version check
        sv = sc.get("schema_version", "")
        if sv and sv < "scorecard.v6":
            warnings.append(f"Old schema version: {sv}")

        replay_grade = len(missing) == 0 and has_mdt and has_freshness and has_cfp

        return ReplayVerification(
            scorecard_file=filepath,
            timestamp_utc=sc.get("timestamp_utc", ""),
            cycle=sc.get("cycle", 0),
            mode=sc.get("mode", ""),
            regime_shift_probability=sc.get("regime_shift_probability", 0.0),
            confidence=sc.get("confidence", 0.0),
            has_mode_decision_trace=has_mdt,
            has_quorum_state=has_quorum,
            has_policy_trace=has_policy,
            has_freshness_state=has_freshness,
            has_config_fingerprint=has_cfp,
            has_threshold_values=has_thresholds,
            mode_blocked=mode_blocked,
            blocking_reason=blocking_reason,
            freshness_penalty=float(fp) if fp else 0.0,
            original_confidence=float(oc) if oc is not None else None,
            degraded_mode=sc.get("degraded_mode", False),
            config_fingerprint=cfp,
            replay_grade=replay_grade,
            missing_fields=missing,
            warnings=warnings,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay and verify Global Sentinel scorecards")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runner = DecisionReplayRunner(Path(args.repo_root).resolve())
    report = runner.replay_range(
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        limit=args.limit,
    )
    report["config_consistency"] = runner.verify_config_consistency(limit=args.limit)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(out)
        return

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()

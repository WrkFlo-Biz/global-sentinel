#!/usr/bin/env python3
"""Decision audit report builder for Global Sentinel.

Produces structured reports explaining why decisions were blocked, degraded,
or allowed. Covers mode transitions, promotions, freshness degradation,
quorum blocks, and config drift.

Usage:
    builder = DecisionAuditReportBuilder(repo_root)
    report = builder.build_report(limit=100)
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DecisionAuditReportBuilder:
    """Build audit reports from scorecards and event logs."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.scorecards_dir = repo_root / "logs" / "scorecards"
        self.events_dir = repo_root / "logs" / "events"

    def build_report(
        self,
        start_utc: Optional[str] = None,
        end_utc: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Build a comprehensive decision audit report."""
        scorecards = self._load_scorecards(start_utc, end_utc, limit)
        events = self._load_events(start_utc, end_utc, limit)

        blocked_escalations = self._extract_blocked_escalations(scorecards)
        freshness_degradations = self._extract_freshness_degradations(scorecards)
        config_drift = self._extract_config_drift(scorecards)
        mode_transitions = self._extract_mode_transitions(events)
        quorum_blocks = self._extract_quorum_blocks(scorecards)

        return {
            "schema_version": "decision_audit_report.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "start": start_utc,
                "end": end_utc,
                "scorecards_analyzed": len(scorecards),
                "events_analyzed": len(events),
            },
            "summary": {
                "total_cycles": len(scorecards),
                "blocked_escalations": len(blocked_escalations),
                "freshness_degradations": len(freshness_degradations),
                "config_drift_events": len(config_drift),
                "mode_transitions": len(mode_transitions),
                "quorum_blocks": len(quorum_blocks),
            },
            "blocked_escalations": blocked_escalations[:50],
            "freshness_degradations": freshness_degradations[:50],
            "config_drift_events": config_drift[:20],
            "mode_transitions": mode_transitions[:50],
            "quorum_blocks": quorum_blocks[:50],
            "blocking_reasons": dict(Counter(
                b["blocking_reason"] for b in blocked_escalations
            )),
        }

    def _load_scorecards(
        self, start_utc: Optional[str], end_utc: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        if not self.scorecards_dir.exists():
            return []
        result: List[Dict[str, Any]] = []
        for f in sorted(self.scorecards_dir.glob("scorecard_*.json"))[-limit:]:
            try:
                sc = json.loads(f.read_text(encoding="utf-8"))
                ts = sc.get("timestamp_utc", "")
                if start_utc and ts < start_utc:
                    continue
                if end_utc and ts > end_utc:
                    continue
                sc["_file"] = str(f)
                result.append(sc)
            except Exception:
                continue
        return result

    def _load_events(
        self, start_utc: Optional[str], end_utc: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        if not self.events_dir.exists():
            return []
        result: List[Dict[str, Any]] = []
        for f in sorted(self.events_dir.glob("*.json"))[-limit:]:
            try:
                ev = json.loads(f.read_text(encoding="utf-8"))
                ts = ev.get("timestamp_utc", "")
                if start_utc and ts < start_utc:
                    continue
                if end_utc and ts > end_utc:
                    continue
                result.append(ev)
            except Exception:
                continue
        return result

    def _extract_blocked_escalations(
        self, scorecards: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        blocked: List[Dict[str, Any]] = []
        for sc in scorecards:
            mdt = sc.get("mode_decision_trace", {})
            if isinstance(mdt, dict) and mdt.get("blocked"):
                blocked.append({
                    "cycle": sc.get("cycle"),
                    "timestamp": sc.get("timestamp_utc"),
                    "proposed_mode": mdt.get("proposed_mode"),
                    "final_mode": mdt.get("final_mode"),
                    "blocking_reason": mdt.get("blocking_reason", "unknown"),
                    "regime_shift_probability": mdt.get("regime_shift_probability"),
                    "policy_evaluation": mdt.get("policy_evaluation"),
                    "quorum_evaluation": mdt.get("quorum_evaluation"),
                })
        return blocked

    def _extract_freshness_degradations(
        self, scorecards: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        degraded: List[Dict[str, Any]] = []
        for sc in scorecards:
            fp = sc.get("freshness_penalty", 0)
            if fp and float(fp) > 0:
                degraded.append({
                    "cycle": sc.get("cycle"),
                    "timestamp": sc.get("timestamp_utc"),
                    "freshness_penalty": float(fp),
                    "original_confidence": sc.get("original_confidence"),
                    "adjusted_confidence": sc.get("confidence"),
                    "degraded_mode": sc.get("degraded_mode", False),
                    "feature_freshness": sc.get("feature_freshness"),
                })
        return degraded

    def _extract_config_drift(
        self, scorecards: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        drift: List[Dict[str, Any]] = []
        prev_fp = None
        for sc in scorecards:
            cfp = sc.get("config_fingerprint", "")
            if cfp and prev_fp and cfp != prev_fp:
                drift.append({
                    "cycle": sc.get("cycle"),
                    "timestamp": sc.get("timestamp_utc"),
                    "from_fingerprint": prev_fp,
                    "to_fingerprint": cfp,
                })
            if cfp:
                prev_fp = cfp
        return drift

    def _extract_mode_transitions(
        self, events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        transitions: List[Dict[str, Any]] = []
        for ev in events:
            if ev.get("event_type") == "mode_transition":
                data = ev.get("data", {})
                transitions.append({
                    "timestamp": ev.get("timestamp_utc"),
                    "from_mode": data.get("from"),
                    "to_mode": data.get("to"),
                    "regime_shift_probability": data.get("regime_shift_probability"),
                })
        return transitions

    def _extract_quorum_blocks(
        self, scorecards: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for sc in scorecards:
            mdt = sc.get("mode_decision_trace", {})
            if isinstance(mdt, dict) and mdt.get("blocking_reason") == "quorum_not_met":
                blocks.append({
                    "cycle": sc.get("cycle"),
                    "timestamp": sc.get("timestamp_utc"),
                    "proposed_mode": mdt.get("proposed_mode"),
                    "quorum_evaluation": mdt.get("quorum_evaluation"),
                })
        return blocks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a decision audit report from scorecards")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    builder = DecisionAuditReportBuilder(Path(args.repo_root).resolve())
    report = builder.build_report(
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        limit=args.limit,
    )

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(out)
        return

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Graduation Criteria Checker

Reads shadow execution logs and scorecards, compares against
config/paper_trading_graduation.yaml thresholds, and produces
a graduation assessment report.

Usage:
    python3 scripts/ops/check_graduation_criteria.py --repo-root .
    python3 scripts/ops/check_graduation_criteria.py --repo-root . --stage paper_to_live
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_scorecards(scorecards_dir: Path, days: int = 30) -> List[Dict[str, Any]]:
    """Load scorecards from the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cards = []
    if not scorecards_dir.exists():
        return cards
    for f in sorted(scorecards_dir.glob("scorecard_*.json")):
        try:
            sc = json.loads(f.read_text(encoding="utf-8"))
            ts = sc.get("timestamp_utc", "")
            if ts and ts >= cutoff.isoformat():
                cards.append(sc)
        except Exception:
            continue
    return cards


class GraduationChecker:
    def __init__(self, repo_root: Path, stage: str = "shadow_to_paper"):
        self.repo_root = repo_root
        self.stage = stage

        config = yaml.safe_load(
            (repo_root / "config" / "paper_trading_graduation.yaml").read_text(encoding="utf-8")
        )
        self.criteria = config.get(stage, {})
        self.min_days = self.criteria.get("minimum_observation_days", 14)

        # Load data
        self.router_log = load_jsonl(repo_root / "logs" / "execution" / "shadow_order_router.jsonl")
        self.bindings_log = load_jsonl(repo_root / "logs" / "execution" / "router_order_bindings.jsonl")
        self.intents_log = load_jsonl(repo_root / "logs" / "execution" / "order_intents.jsonl")
        self.events_log = load_jsonl(repo_root / "logs" / "events" / "crisis_monitor_events.jsonl")
        self.scorecards = load_scorecards(repo_root / "logs" / "scorecards", days=max(self.min_days + 5, 35))

    def check(self) -> Dict[str, Any]:
        """Run all graduation checks. Returns assessment report."""
        checks: List[Dict[str, Any]] = []
        overall_pass = True

        # Observation period
        obs = self._check_observation_period()
        checks.append(obs)
        if not obs["pass"]:
            overall_pass = False

        # Execution reliability
        exec_checks = self._check_execution_reliability()
        for c in exec_checks:
            checks.append(c)
            if not c["pass"]:
                overall_pass = False

        # Risk gate quality
        risk_checks = self._check_risk_gate_quality()
        for c in risk_checks:
            checks.append(c)
            if not c["pass"]:
                overall_pass = False

        # Data freshness
        fresh_checks = self._check_data_freshness()
        for c in fresh_checks:
            checks.append(c)
            if not c["pass"]:
                overall_pass = False

        # Incident history
        incident_checks = self._check_incident_history()
        for c in incident_checks:
            checks.append(c)
            if not c["pass"]:
                overall_pass = False

        report = {
            "schema_version": "graduation_assessment.v1",
            "timestamp_utc": iso_now(),
            "stage": self.stage,
            "overall_pass": overall_pass,
            "minimum_observation_days": self.min_days,
            "checks": checks,
            "summary": {
                "total_checks": len(checks),
                "passed": sum(1 for c in checks if c["pass"]),
                "failed": sum(1 for c in checks if not c["pass"]),
                "not_enough_data": sum(1 for c in checks if c.get("insufficient_data")),
            },
            "scorecards_analyzed": len(self.scorecards),
            "router_events_analyzed": len(self.router_log),
            "bindings_analyzed": len(self.bindings_log),
        }
        return report

    def _check_observation_period(self) -> Dict[str, Any]:
        if not self.scorecards:
            return {"check": "observation_period", "pass": False, "insufficient_data": True,
                    "actual": 0, "required": self.min_days, "reason": "no scorecards found"}

        first_ts = self.scorecards[0].get("timestamp_utc", "")
        last_ts = self.scorecards[-1].get("timestamp_utc", "")
        try:
            first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            days = (last_dt - first_dt).days
        except Exception:
            days = 0

        return {
            "check": "observation_period",
            "pass": days >= self.min_days,
            "actual": days,
            "required": self.min_days,
            "first_scorecard": first_ts,
            "last_scorecard": last_ts,
        }

    def _check_execution_reliability(self) -> List[Dict[str, Any]]:
        checks = []
        criteria = self.criteria.get("execution_reliability", {})

        # Count bound orders from bindings log
        total_bindings = len(self.bindings_log)
        min_orders = criteria.get("min_bound_order_attempt_count", 100)
        checks.append({
            "check": "min_bound_order_attempts",
            "pass": total_bindings >= min_orders,
            "actual": total_bindings,
            "required": min_orders,
            "insufficient_data": total_bindings == 0,
        })

        # Reject rate from router events
        route_completes = [e for e in self.router_log
                           if e.get("event_type") == "route_package_complete"]
        total_submits = sum(e.get("payload", {}).get("submit_attempt_count", 0) for e in route_completes)
        total_rejects = sum(e.get("payload", {}).get("broker_rejected_count", 0) for e in route_completes)
        reject_rate = (total_rejects / total_submits) if total_submits > 0 else 0.0
        max_reject = criteria.get("max_broker_reject_rate", 0.10)
        checks.append({
            "check": "broker_reject_rate",
            "pass": reject_rate <= max_reject if total_submits > 0 else False,
            "actual": round(reject_rate, 4),
            "required": f"<= {max_reject}",
            "total_submits": total_submits,
            "total_rejects": total_rejects,
            "insufficient_data": total_submits == 0,
        })

        # Submit success rate
        success_rate = ((total_submits - total_rejects) / total_submits) if total_submits > 0 else 0.0
        min_success = criteria.get("min_submit_success_rate", 0.85)
        checks.append({
            "check": "submit_success_rate",
            "pass": success_rate >= min_success if total_submits > 0 else False,
            "actual": round(success_rate, 4),
            "required": f">= {min_success}",
            "insufficient_data": total_submits == 0,
        })

        return checks

    def _check_risk_gate_quality(self) -> List[Dict[str, Any]]:
        checks = []
        criteria = self.criteria.get("risk_gate_quality", {})

        # Count risk gate blocks from router skipped candidates
        route_completes = [e for e in self.router_log
                           if e.get("event_type") == "route_package_complete"]
        total_candidates = sum(e.get("payload", {}).get("candidate_count_in_package", 0) for e in route_completes)
        risk_blocked = 0
        for e in route_completes:
            for skip in e.get("payload", {}).get("skipped_candidates", []):
                if skip.get("reason") == "risk_gate_blocked":
                    risk_blocked += 1

        block_rate = (risk_blocked / total_candidates) if total_candidates > 0 else 0.0
        max_block = criteria.get("max_risk_gate_block_rate", 0.40)
        checks.append({
            "check": "risk_gate_block_rate",
            "pass": block_rate <= max_block if total_candidates > 0 else True,
            "actual": round(block_rate, 4),
            "required": f"<= {max_block}",
            "risk_blocked": risk_blocked,
            "total_candidates": total_candidates,
            "insufficient_data": total_candidates == 0,
        })

        return checks

    def _check_data_freshness(self) -> List[Dict[str, Any]]:
        checks = []
        criteria = self.criteria.get("data_freshness", {})

        if not self.scorecards:
            checks.append({"check": "bridge_quorum_pass_rate", "pass": False,
                           "insufficient_data": True, "actual": 0, "required": "n/a"})
            return checks

        # Quorum pass rate: check fallback_mode_status across scorecards
        fallback_count = sum(1 for sc in self.scorecards if sc.get("fallback_mode_status", False))
        total = len(self.scorecards)
        fallback_rate = fallback_count / total if total > 0 else 0.0
        max_fallback = criteria.get("max_fallback_mode_rate", 0.15)
        checks.append({
            "check": "fallback_mode_rate",
            "pass": fallback_rate <= max_fallback,
            "actual": round(fallback_rate, 4),
            "required": f"<= {max_fallback}",
            "fallback_cycles": fallback_count,
            "total_cycles": total,
        })

        # Quorum pass rate
        quorum_pass = total - fallback_count
        quorum_rate = quorum_pass / total if total > 0 else 0.0
        min_quorum = criteria.get("min_bridge_quorum_pass_rate", 0.90)
        checks.append({
            "check": "bridge_quorum_pass_rate",
            "pass": quorum_rate >= min_quorum,
            "actual": round(quorum_rate, 4),
            "required": f">= {min_quorum}",
        })

        return checks

    def _check_incident_history(self) -> List[Dict[str, Any]]:
        checks = []
        criteria = self.criteria.get("incident_history", {})

        # Kill switch activations
        kill_events = [e for e in self.events_log if e.get("event_type") == "kill_switch_active"]
        max_kill = criteria.get("max_kill_switch_activations", 0)
        checks.append({
            "check": "kill_switch_activations",
            "pass": len(kill_events) <= max_kill,
            "actual": len(kill_events),
            "required": f"<= {max_kill}",
        })

        # Mode transitions to CRISIS
        mode_events = [e for e in self.events_log if e.get("event_type") == "mode_transition"]
        crisis_transitions = [e for e in mode_events if e.get("payload", {}).get("to") == "CRISIS"]
        # Count incident mode activations as crisis transitions
        max_incidents = criteria.get("max_incident_mode_activations", 2)
        checks.append({
            "check": "incident_mode_activations",
            "pass": len(crisis_transitions) <= max_incidents,
            "actual": len(crisis_transitions),
            "required": f"<= {max_incidents}",
        })

        return checks


def print_report(report: Dict[str, Any]):
    """Pretty-print the graduation assessment."""
    stage = report["stage"]
    overall = "ELIGIBLE" if report["overall_pass"] else "NOT READY"
    summary = report["summary"]

    print(f"\n{'=' * 70}")
    print(f"  GRADUATION ASSESSMENT: {stage}")
    print(f"  Status: {overall}")
    print(f"  {summary['passed']}/{summary['total_checks']} checks passed"
          f" ({summary['failed']} failed, {summary['not_enough_data']} insufficient data)")
    print(f"  Scorecards analyzed: {report['scorecards_analyzed']}")
    print(f"  Router events: {report['router_events_analyzed']}")
    print(f"  Bindings: {report['bindings_analyzed']}")
    print(f"{'=' * 70}\n")

    for check in report["checks"]:
        status = "PASS" if check["pass"] else ("DATA?" if check.get("insufficient_data") else "FAIL")
        icon = "✓" if check["pass"] else ("?" if check.get("insufficient_data") else "✗")
        name = check["check"]
        actual = check.get("actual", "n/a")
        required = check.get("required", "n/a")
        print(f"  {icon} {status:5s}  {name:40s}  actual={actual}  required={required}")

    print()


def main():
    p = argparse.ArgumentParser(description="Global Sentinel Graduation Checker")
    p.add_argument("--repo-root", default=".", help="Repository root path")
    p.add_argument("--stage", default="shadow_to_paper",
                   choices=["shadow_to_paper", "paper_to_live"],
                   help="Graduation stage to check")
    p.add_argument("--output-json", default=None, help="Write report as JSON")
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    checker = GraduationChecker(repo_root, stage=args.stage)
    report = checker.check()

    print_report(report)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved to {out}")

    # Also save to standard report path
    report_dir = repo_root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "graduation_assessment.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

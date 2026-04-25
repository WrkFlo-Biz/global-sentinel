#!/usr/bin/env python3
"""
Global Sentinel V4 - Self Improvement Loop (Enhanced replay-aware, Shadow/Staging Only)

Enhancements:
- Consumes enhanced replay output from src/replay_runner.py
- Reads effective_mode_counts, correlation_break_count, shadow_execution_blocked_count
- Aggregates penalty_breakdown patterns from replay results
- Produces richer staging proposals (still human-gated)
- Preserves safety constraints:
  - no auto-promotion to production
  - no changes during CRISIS mode (if freeze enabled)
  - no live orders

Outputs:
- reports/openclaw_research/*.json
- reports/openclaw_research/*.md
- config/staging/*.json (proposal artifacts)
- logs/dead_letter/ (errors)

Usage:
  python scripts/self_improvement_loop.py --repo-root /opt/global-sentinel
  python scripts/self_improvement_loop.py --repo-root . --once
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.control_state_snapshot import read_control_state_snapshot

try:
    from src.alpha.time_window_policy import TimeWindowPolicyEngine
except Exception:
    try:
        import importlib.util as _ilu
        _twp_path = Path(__file__).resolve().parents[1] / "src" / "alpha" / "time_window_policy.py"
        if _twp_path.exists():
            _spec = _ilu.spec_from_file_location("time_window_policy", _twp_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            TimeWindowPolicyEngine = _mod.TimeWindowPolicyEngine
        else:
            TimeWindowPolicyEngine = None
    except Exception:
        TimeWindowPolicyEngine = None


@dataclass
class ImprovementConfig:
    loop_interval_sec: int = 300
    replay_interval_sec: int = 1800
    correlation_check_interval_sec: int = 6 * 3600
    min_samples_for_tuning: int = 20
    crisis_mode_freeze: bool = True
    replay_fixtures_relpath: str = "tests/replays"
    replay_output_relpath: str = "reports/openclaw_research/replay_latest.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bucket_by_time_window(results: List[Dict]) -> Dict[str, Any]:
    """Group replay case results by time_window_state.current_window and compute per-bucket stats.

    Each result is expected to have a ``prediction`` dict.  If the prediction
    contains ``time_window_state`` (emitted by the replay runner when
    TimeWindowPolicyEngine is available), cases are bucketed by
    ``time_window_state["current_window"]``.  Otherwise they fall into the
    ``"unknown"`` bucket.

    Returns a dict keyed by window name, each containing:
        case_count, pass_count, pass_rate, avg_confidence, avg_probability,
        shadow_blocked_count, correlation_break_count.

    If TimeWindowPolicy could not be imported or *results* is empty / lacks
    any time-window data, returns a dict with a ``"note"`` key explaining
    unavailability.
    """
    if TimeWindowPolicyEngine is None:
        return {"note": "time_window_bucketing_unavailable"}

    if not results:
        return {"note": "time_window_bucketing_unavailable"}

    buckets: Dict[str, Dict[str, Any]] = {}

    for r in results:
        pred = (r or {}).get("prediction", {}) or {}
        tw_state = pred.get("time_window_state") or {}
        window = tw_state.get("current_window", "unknown") if isinstance(tw_state, dict) else "unknown"

        if window not in buckets:
            buckets[window] = {
                "case_count": 0,
                "pass_count": 0,
                "confidences": [],
                "probabilities": [],
                "shadow_blocked_count": 0,
                "correlation_break_count": 0,
            }

        b = buckets[window]
        b["case_count"] += 1

        # Pass / fail — honour the top-level "passed" flag on the result
        if r.get("passed") is True:
            b["pass_count"] += 1

        # Confidence
        conf = pred.get("confidence")
        if isinstance(conf, (int, float)):
            b["confidences"].append(float(conf))

        # Regime shift probability
        prob = pred.get("regime_shift_probability")
        if isinstance(prob, (int, float)):
            b["probabilities"].append(float(prob))

        # Shadow blocked (prediction-level or time_window_state level)
        if tw_state.get("shadow_execution_window_blocked") is True or pred.get("shadow_execution_blocked") is True:
            b["shadow_blocked_count"] += 1

        # Correlation break
        corr_flags = pred.get("correlation_flags") or {}
        if isinstance(corr_flags, dict) and corr_flags.get("break_detected") is True:
            b["correlation_break_count"] += 1

    if not buckets:
        return {"note": "time_window_bucketing_unavailable"}

    # Collapse internal lists into summary stats
    out: Dict[str, Any] = {}
    for window, b in buckets.items():
        cc = b["case_count"]
        out[window] = {
            "case_count": cc,
            "pass_count": b["pass_count"],
            "pass_rate": round(b["pass_count"] / cc, 4) if cc else 0.0,
            "avg_confidence": round(statistics.mean(b["confidences"]), 4) if b["confidences"] else None,
            "avg_probability": round(statistics.mean(b["probabilities"]), 4) if b["probabilities"] else None,
            "shadow_blocked_count": b["shadow_blocked_count"],
            "correlation_break_count": b["correlation_break_count"],
        }

    return out


class SelfImprovementLoop:
    def __init__(self, repo_root: Path, cfg: ImprovementConfig):
        self.repo_root = repo_root
        self.cfg = cfg
        self.logs_events = repo_root / "logs" / "events"
        self.logs_scores = repo_root / "logs" / "scorecards"
        self.logs_dead = repo_root / "logs" / "dead_letter"
        self.reports_research = repo_root / "reports" / "openclaw_research"
        self.control_dir = repo_root / "control"
        self.staging_dir = repo_root / "config" / "staging"
        self.replay_runner_path = repo_root / "src" / "replay_runner.py"
        self.replay_fixtures_dir = repo_root / self.cfg.replay_fixtures_relpath
        self.replay_output_path = repo_root / self.cfg.replay_output_relpath

        for d in [self.reports_research, self.staging_dir, self.logs_dead]:
            d.mkdir(parents=True, exist_ok=True)

        self.last_replay = 0.0
        self.last_corr = 0.0

    # --- Control / mode helpers ---
    def control_flags(self) -> Dict[str, bool]:
        return read_control_state_snapshot(self.repo_root)

    def current_mode(self) -> str:
        latest = self._latest_file(self.logs_scores, "*.json")
        if not latest:
            return "UNKNOWN"
        data = read_json(latest, {})
        return str(data.get("mode", "UNKNOWN")).upper()

    def _latest_file(self, folder: Path, pattern: str) -> Optional[Path]:
        if not folder.exists():
            return None
        files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None

    def _recent_scorecards(self, n: int = 200) -> List[Dict[str, Any]]:
        if not self.logs_scores.exists():
            return []
        files = sorted(self.logs_scores.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
        out: List[Dict[str, Any]] = []
        for f in files:
            d = read_json(f, {})
            if isinstance(d, dict) and d:
                out.append(d)
        return out

    # --- Stage 1: Outcome Tracking ---
    def outcome_tracker(self) -> Dict[str, Any]:
        cards = self._recent_scorecards(300)
        if not cards:
            return {"timestamp": iso_now(), "status": "no_data", "message": "No scorecards found."}

        probs: List[float] = []
        confs: List[float] = []
        crisis_count = elevated_count = fallback_count = 0
        effective_manual_review_count = 0
        risk_gate_pass_count = 0
        shadow_eligible_count = 0
        penalty_keys_seen: Dict[str, int] = {}

        for c in cards:
            p = c.get("regime_shift_probability")
            if isinstance(p, (int, float)):
                probs.append(float(p))
            conf = c.get("confidence")
            if isinstance(conf, (int, float)):
                confs.append(float(conf))

            mode = str(c.get("mode", "")).upper()
            if mode == "CRISIS":
                crisis_count += 1
            elif mode == "ELEVATED":
                elevated_count += 1

            if c.get("fallback_mode_status") is True:
                fallback_count += 1
            if str(c.get("effective_mode", "")).upper() == "MANUAL_REVIEW":
                effective_manual_review_count += 1
            if str(c.get("risk_gate_status", "")).lower() == "pass":
                risk_gate_pass_count += 1
            if c.get("shadow_execution_eligible") is True:
                shadow_eligible_count += 1

            pb = c.get("penalty_breakdown", {})
            if isinstance(pb, dict):
                for k in pb:
                    penalty_keys_seen[k] = penalty_keys_seen.get(k, 0) + 1

        n = len(cards)
        return {
            "timestamp": iso_now(), "status": "ok", "sample_size": n,
            "avg_regime_probability": round(statistics.mean(probs), 4) if probs else None,
            "median_regime_probability": round(statistics.median(probs), 4) if probs else None,
            "avg_confidence": round(statistics.mean(confs), 4) if confs else None,
            "mode_counts": {
                "crisis": crisis_count, "elevated": elevated_count,
                "normal_or_other": max(n - crisis_count - elevated_count, 0),
            },
            "fallback_cycles": fallback_count,
            "effective_manual_review_count": effective_manual_review_count,
            "risk_gate_pass_ratio": round(risk_gate_pass_count / n, 4) if n else None,
            "shadow_execution_eligible_ratio": round(shadow_eligible_count / n, 4) if n else None,
            "top_penalty_keys_seen": dict(sorted(penalty_keys_seen.items(), key=lambda kv: kv[1], reverse=True)[:10]),
        }

    # --- Stage 2: Drift Detection ---
    def drift_monitor(self) -> Dict[str, Any]:
        cards = self._recent_scorecards(300)
        if len(cards) < 20:
            return {"timestamp": iso_now(), "status": "insufficient_data", "break_detected": False}

        probs = [float(c.get("regime_shift_probability", 0.0)) for c in cards
                 if isinstance(c.get("regime_shift_probability"), (int, float))]
        confs = [float(c.get("confidence", 0.0)) for c in cards
                 if isinstance(c.get("confidence"), (int, float))]

        if len(probs) < 20:
            return {"timestamp": iso_now(), "status": "insufficient_prob_data", "break_detected": False}

        recent_probs = probs[:30]
        prior_probs = probs[30:120] if len(probs) > 60 else probs[10:]
        recent_p_mean = statistics.mean(recent_probs)
        prior_p_mean = statistics.mean(prior_probs) if prior_probs else recent_p_mean
        prob_drift_abs = abs(recent_p_mean - prior_p_mean)

        conf_drift_abs = None
        if len(confs) >= 20:
            recent_conf = confs[:30]
            prior_conf = confs[30:120] if len(confs) > 60 else confs[10:]
            if prior_conf:
                conf_drift_abs = abs(statistics.mean(recent_conf) - statistics.mean(prior_conf))

        break_detected = prob_drift_abs > 0.15 or (conf_drift_abs is not None and conf_drift_abs > 0.12)

        return {
            "timestamp": iso_now(), "status": "ok",
            "recent_mean_prob": round(recent_p_mean, 4),
            "prior_mean_prob": round(prior_p_mean, 4),
            "prob_drift_abs": round(prob_drift_abs, 4),
            "conf_drift_abs": round(conf_drift_abs, 4) if conf_drift_abs is not None else None,
            "break_detected": break_detected,
        }

    # --- Stage 3: Correlation Sanity ---
    def correlation_sanity(self) -> Dict[str, Any]:
        cards = self._recent_scorecards(50)
        corr_flags_seen = 0
        examples: List[Any] = []

        for c in cards:
            cf = c.get("correlation_flags", {})
            if isinstance(cf, dict) and cf.get("break_detected") is True:
                corr_flags_seen += 1
                if len(examples) < 5:
                    examples.append(cf.get("breaks", []))

        return {
            "timestamp": iso_now(),
            "status": "derived_from_scorecards" if cards else "no_data",
            "break_detected": corr_flags_seen > 0,
            "correlation_break_count_recent": corr_flags_seen,
            "examples": examples,
            "notes": ["Rely on enhanced replay output for full correlation replay validation."],
        }

    # --- Stage 4: Replay integration (enhanced runner aware) ---
    def _invoke_replay_runner(self) -> Dict[str, Any]:
        if not self.replay_runner_path.exists():
            return {"timestamp": iso_now(), "status": "missing_replay_runner", "eligible_for_tuning": False}

        if not self.replay_fixtures_dir.exists():
            return {"timestamp": iso_now(), "status": "missing_fixtures", "eligible_for_tuning": False,
                    "fixtures_dir": str(self.replay_fixtures_dir)}

        cmd = [
            sys.executable,
            str(self.replay_runner_path),
            "--repo-root", str(self.repo_root),
            "--fixtures", str(self.replay_fixtures_dir),
            "--output", str(self.replay_output_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            result_payload = {
                "timestamp": iso_now(), "invoked": True,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
            }
            if proc.returncode != 0:
                result_payload["status"] = "replay_runner_error"
                result_payload["eligible_for_tuning"] = False
                return result_payload

            replay = read_json(self.replay_output_path, {})
            if not replay:
                result_payload["status"] = "replay_output_missing_or_invalid"
                result_payload["eligible_for_tuning"] = False
                return result_payload

            replay["invocation"] = result_payload
            replay["eligible_for_tuning"] = bool(
                replay.get("status") == "ok"
                and (replay.get("total_cases") or 0) >= self.cfg.min_samples_for_tuning
            )
            return replay
        except subprocess.TimeoutExpired:
            return {"timestamp": iso_now(), "status": "replay_runner_timeout", "eligible_for_tuning": False}
        except Exception as e:
            return {"timestamp": iso_now(), "status": "replay_runner_exception",
                    "eligible_for_tuning": False, "error": str(e)}

    def replay_backtest(self) -> Dict[str, Any]:
        replay = self._invoke_replay_runner()

        # Fallback if invocation failed
        if replay.get("status") not in {"ok", "no_fixtures"}:
            cards = self._recent_scorecards(300)
            return {
                "timestamp": iso_now(), "status": "fallback_stub",
                "sample_size": len(cards),
                "eligible_for_tuning": len(cards) >= self.cfg.min_samples_for_tuning,
                "note": "Replay runner unavailable; using scorecard-count fallback.",
            }

        # Aggregate penalty patterns from enhanced replay output
        penalty_agg: Dict[str, int] = {}
        for r in (replay.get("results", []) or []):
            pred = (r or {}).get("prediction", {}) or {}
            pb = pred.get("penalty_breakdown", {}) or {}
            if isinstance(pb, dict):
                for k in pb:
                    penalty_agg[k] = penalty_agg.get(k, 0) + 1

        replay["penalty_pattern_counts"] = dict(sorted(penalty_agg.items(), key=lambda kv: kv[1], reverse=True))
        effective_mode_counts = replay.get("effective_mode_counts", {}) or {}
        replay["derived_flags"] = {
            "manual_review_cases_present": (effective_mode_counts.get("MANUAL_REVIEW", 0) > 0),
            "correlation_breaks_present": int(replay.get("correlation_break_count", 0) or 0) > 0,
            "shadow_blocked_cases_present": int(replay.get("shadow_execution_blocked_count", 0) or 0) > 0,
        }

        # Time-window bucketing
        case_results = replay.get("results", []) or []
        time_window_stats = _bucket_by_time_window(case_results)
        replay["time_window_stats"] = time_window_stats

        return replay

    # --- Stage 5: Threshold Tuning Proposal ---
    def threshold_tuning_proposal(self, replay: Dict[str, Any], drift: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mode = self.current_mode()
        flags = self.control_flags()

        if flags["kill_switch"] or flags["manual_veto"]:
            return None
        if self.cfg.crisis_mode_freeze and mode == "CRISIS":
            return None
        if replay.get("status") not in {"ok", "fallback_stub"} or not replay.get("eligible_for_tuning"):
            return None

        proposed_weights = {
            "physical_reality": 0.30, "kinetic_trigger": 0.20,
            "domestic_stress": 0.30, "market_transmission": 0.20,
        }
        rationale: List[str] = []
        warnings: List[str] = []

        pass_rate = replay.get("pass_rate")
        avg_conf = replay.get("avg_confidence")
        penalty_counts = replay.get("penalty_pattern_counts", {}) or {}
        correlation_break_count = int(replay.get("correlation_break_count", 0) or 0)

        if isinstance(pass_rate, (int, float)) and pass_rate < 0.75:
            if penalty_counts.get("conflicting_signals", 0) > 0:
                proposed_weights["physical_reality"] += 0.02
                proposed_weights["kinetic_trigger"] -= 0.01
                proposed_weights["market_transmission"] -= 0.01
                rationale.append("Replay failures + conflicting signals: emphasize physical confirmation.")
            elif penalty_counts.get("stale_key_sources", 0) > 0 or penalty_counts.get("freshness_quorum_fail", 0) > 0:
                rationale.append("Replay indicates data-quality penalties dominate; fix ingestion/freshness first.")
            else:
                proposed_weights["physical_reality"] += 0.01
                proposed_weights["kinetic_trigger"] -= 0.01
                rationale.append("Modest physical layer shift due to replay underperformance.")
        else:
            rationale.append("Replay pass rate acceptable; minor/no weight adjustments.")

        if isinstance(avg_conf, (int, float)) and avg_conf < 0.65:
            warnings.append("Average replay confidence low; improve data quality before aggressive tuning.")

        if correlation_break_count > 0:
            rationale.append("Correlation breaks observed; maintain flag-only handling.")

        if drift.get("break_detected") is True:
            proposed_weights["physical_reality"] += 0.01
            proposed_weights["kinetic_trigger"] += 0.01
            proposed_weights["domestic_stress"] -= 0.01
            proposed_weights["market_transmission"] -= 0.01
            rationale.append("Drift detected; modestly increasing physical/kinetic emphasis.")

        # Normalize
        total = sum(proposed_weights.values())
        if total > 0:
            for k in proposed_weights:
                proposed_weights[k] = round(max(0.0, min(1.0, proposed_weights[k])) / total, 4)

        return {
            "timestamp": iso_now(), "type": "threshold_tuning_proposal",
            "mode_when_generated": mode,
            "proposed_changes": {"weights": proposed_weights},
            "warnings": warnings,
            "requires_human_approval": True, "apply_to": "staging_only",
            "promotion_guardrails": {
                "block_if_crisis_mode": True,
                "require_replay_pass_rate_min": 0.80,
                "require_no_kill_switch": True,
            },
            "rationale": rationale or ["Conservative proposal with no material changes."],
        }

    # --- Stage 6: Safety Audit ---
    def safety_audit(self) -> Dict[str, Any]:
        checks = []
        try:
            import yaml as _yaml
        except ImportError:
            _yaml = None

        venue_path = self.repo_root / "config" / "venue_policies.yaml"
        if venue_path.exists() and _yaml:
            venues = _yaml.safe_load(venue_path.read_text()) or {}
            live = venues.get("live_venues", {})
            checks.append({"check": "no_live_venues", "passed": len(live) == 0, "value": len(live)})
            checks.append({"check": "shadow_mode_enforced",
                           "passed": venues.get("policies", {}).get("shadow_mode_enforced", False)})
            checks.append({"check": "live_trading_disabled",
                           "passed": not venues.get("policies", {}).get("live_trading_enabled", True)})

        # Raw control files remain deployment diagnostics only here; boolean
        # authority stays behind read_control_state_snapshot().
        for control_name, filename in (
            ("manual_veto", "manual_veto.json"),
            ("kill_switch", "kill_switch.json"),
        ):
            checks.append(
                {
                    "check": f"{control_name}_file_present_diagnostic",
                    "passed": (self.control_dir / filename).exists(),
                    "kind": "deployment_diagnostic",
                    "authority": "read_control_state_snapshot",
                    "note": (
                        f"{filename} presence only; boolean control state comes "
                        "from the normalized control snapshot helper."
                    ),
                }
            )

        return {"passed": all(c.get("passed", False) for c in checks),
                "checks": checks, "timestamp": iso_now()}

    # --- Main Loop ---
    def run_once(self) -> Dict[str, Any]:
        flags = self.control_flags()
        mode = self.current_mode()

        outcome = self.outcome_tracker()
        drift = self.drift_monitor()

        now = time.time()
        corr = None
        if now - self.last_corr >= self.cfg.correlation_check_interval_sec:
            corr = self.correlation_sanity()
            self.last_corr = now

        replay = None
        if now - self.last_replay >= self.cfg.replay_interval_sec:
            replay = self.replay_backtest()
            self.last_replay = now

        proposal = None
        if replay:
            proposal = self.threshold_tuning_proposal(replay, drift)

        safety = self.safety_audit()

        summary = {
            "timestamp": iso_now(), "mode": mode, "control_flags": flags,
            "outcome_tracker": outcome, "drift_monitor": drift,
            "correlation_sanity": corr, "replay_backtest": replay,
            "safety_audit": safety, "proposal_generated": bool(proposal),
        }

        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        write_json(self.reports_research / f"self_improvement_summary_{ts}.json", summary)

        if proposal:
            write_json(self.staging_dir / f"thresholds_proposal_{ts}.json", proposal)

        # Markdown report
        md = [
            "# Global Sentinel Self-Improvement Loop Summary",
            f"- Timestamp: {summary['timestamp']}",
            f"- Mode: {mode}",
            f"- Manual veto: {flags['manual_veto']}",
            f"- Kill switch: {flags['kill_switch']}",
            "",
            "## Outcome Tracker",
            f"- Status: {outcome.get('status')}",
            f"- Sample size: {outcome.get('sample_size')}",
            f"- Avg regime probability: {outcome.get('avg_regime_probability')}",
            f"- Avg confidence: {outcome.get('avg_confidence')}",
            f"- Fallback cycles: {outcome.get('fallback_cycles')}",
            f"- Effective MANUAL_REVIEW count: {outcome.get('effective_manual_review_count')}",
            "",
            "## Drift Monitor",
            f"- Status: {drift.get('status')}",
            f"- Break detected: {drift.get('break_detected')}",
            f"- Probability drift: {drift.get('prob_drift_abs')}",
            f"- Confidence drift: {drift.get('conf_drift_abs')}",
            "",
            "## Safety Audit",
            f"- Passed: {safety.get('passed')}",
            "",
        ]

        if corr:
            md.extend(["## Correlation Sanity",
                        f"- Status: {corr.get('status')}",
                        f"- Break detected: {corr.get('break_detected')}",
                        f"- Break count (recent): {corr.get('correlation_break_count_recent')}",
                        ""])
        if replay:
            md.extend(["## Replay/Backtest",
                        f"- Status: {replay.get('status')}",
                        f"- Total cases: {replay.get('total_cases', replay.get('sample_size'))}",
                        f"- Pass rate: {replay.get('pass_rate')}",
                        f"- Avg confidence: {replay.get('avg_confidence')}",
                        f"- Effective mode counts: {replay.get('effective_mode_counts')}",
                        f"- Correlation break count: {replay.get('correlation_break_count')}",
                        f"- Shadow blocked count: {replay.get('shadow_execution_blocked_count')}",
                        f"- Eligible for tuning: {replay.get('eligible_for_tuning')}",
                        ""])
            penalty_patterns = replay.get("penalty_pattern_counts", {})
            if penalty_patterns:
                md.append("### Replay Penalty Patterns")
                for k, v in penalty_patterns.items():
                    md.append(f"- {k}: {v}")
                md.append("")

            tw_stats = replay.get("time_window_stats", {})
            if tw_stats and "note" not in tw_stats:
                md.append("## Time Window Performance")
                md.append("| Window | Cases | Pass Rate | Avg Conf | Avg Prob | Shadow Blocked |")
                md.append("|--------|-------|-----------|----------|----------|----------------|")
                degraded_windows: List[str] = []
                for wname, wdata in tw_stats.items():
                    if not isinstance(wdata, dict):
                        continue
                    md.append(
                        f"| {wname} "
                        f"| {wdata.get('case_count', 0)} "
                        f"| {wdata.get('pass_rate', 'N/A')} "
                        f"| {wdata.get('avg_confidence', 'N/A')} "
                        f"| {wdata.get('avg_probability', 'N/A')} "
                        f"| {wdata.get('shadow_blocked_count', 0)} |"
                    )
                    # Detect window performance degradation
                    if (wdata.get("case_count", 0) >= 3
                            and isinstance(wdata.get("pass_rate"), (int, float))
                            and wdata["pass_rate"] < 0.5):
                        degraded_windows.append(wname)
                md.append("")

                if degraded_windows:
                    md.append("### Window Performance Degradation Detected")
                    for dw in degraded_windows:
                        md.append(f"- **{dw}**: pass_rate < 0.5 with >= 3 cases (window_performance_degradation)")
                    md.append("")
                    summary["window_performance_degradation"] = degraded_windows

        if proposal:
            md.extend(["## Staging Proposal Generated",
                        "- Threshold tuning proposal written to config/staging/",
                        "- Human approval required before merge.", ""])
        else:
            md.extend(["## Proposal Status", "- No proposal generated this cycle.", ""])

        write_md(self.reports_research / f"self_improvement_summary_{ts}.md", "\n".join(md))
        return summary

    def run(self, once: bool = False) -> None:
        print(f"[Self-Improvement Loop] Starting (once={once}, interval={self.cfg.loop_interval_sec}s)")
        while True:
            try:
                result = self.run_once()
                print(f"[{result['timestamp']}] mode={result['mode']} "
                      f"drift={result['drift_monitor'].get('break_detected')} "
                      f"safety={result['safety_audit'].get('passed')} "
                      f"proposal={result['proposal_generated']}")
            except Exception as e:
                write_json(self.logs_dead / f"self_improvement_error_{int(time.time())}.json",
                           {"timestamp": iso_now(), "error": str(e), "stage": "self_improvement_loop"})
                print(f"[ERROR] {e}", file=sys.stderr)

            if once:
                break
            time.sleep(self.cfg.loop_interval_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="Global Sentinel Self-Improvement Loop")
    parser.add_argument("--repo-root", required=True, help="Path to repo root")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop-interval-sec", type=int, default=300)
    parser.add_argument("--replay-interval-sec", type=int, default=1800)
    parser.add_argument("--corr-interval-sec", type=int, default=6 * 3600)
    args = parser.parse_args()

    cfg = ImprovementConfig(
        loop_interval_sec=args.loop_interval_sec,
        replay_interval_sec=args.replay_interval_sec,
        correlation_check_interval_sec=args.corr_interval_sec,
    )
    loop = SelfImprovementLoop(Path(args.repo_root), cfg)
    loop.run(once=args.once)


if __name__ == "__main__":
    main()

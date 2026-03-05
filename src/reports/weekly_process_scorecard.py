#!/usr/bin/env python3
"""
Global Sentinel V5.2 - Weekly Process Scorecard (Integrated)

Purpose:
- Aggregate process-quality metrics across a week:
  - daily decision reviews
  - TCA shadow reports
  - no-trade quality
  - paper reconciliation
  - threshold drift assessments
  - recommendation queue activity
- Produce JSON + Markdown for CAIO/CFO/CIO weekly review

Expected directory structure (default):
- reports/daily/YYYYMMDD/daily_decision_review.json
- reports/analytics/YYYYMMDD/tca_shadow_report.json
- reports/analytics/YYYYMMDD/no_trade_quality.json
- reports/analytics/YYYYMMDD/paper_trade_reconciliation.json (optional)
- reports/analytics/YYYYMMDD/threshold_drift_assessment.json (optional)
- logs/self_improvement/recommendation_queue.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mean(xs: List[float]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def parse_date_tag(tag: str) -> Optional[datetime]:
    try:
        return datetime.strptime(tag, "%Y%m%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


class WeeklyProcessScorecard:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def build(self, end_date_tag: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
        end_dt = parse_date_tag(end_date_tag) if end_date_tag else datetime.now(timezone.utc)
        if end_dt is None:
            end_dt = datetime.now(timezone.utc)

        # Build date tag window inclusive
        tags = []
        for i in range(days):
            d = (end_dt - timedelta(days=i)).strftime("%Y%m%d")
            tags.append(d)
        tags = sorted(tags)

        daily_rows = []
        tca_rows = []
        nt_rows = []
        recon_rows = []
        drift_rows = []

        for tag in tags:
            daily_path = self.repo_root / "reports" / "daily" / tag / "daily_decision_review.json"
            an_dir = self.repo_root / "reports" / "analytics" / tag
            tca_path = an_dir / "tca_shadow_report.json"
            nt_path = an_dir / "no_trade_quality.json"
            recon_path = an_dir / "paper_trade_reconciliation.json"
            drift_path = an_dir / "threshold_drift_assessment.json"

            if daily_path.exists():
                daily_rows.append(self._load_json(daily_path) | {"_date_tag": tag})
            if tca_path.exists():
                tca_rows.append(self._load_json(tca_path) | {"_date_tag": tag})
            if nt_path.exists():
                nt_rows.append(self._load_json(nt_path) | {"_date_tag": tag})
            if recon_path.exists():
                recon_rows.append(self._load_json(recon_path) | {"_date_tag": tag})
            if drift_path.exists():
                drift_rows.append(self._load_json(drift_path) | {"_date_tag": tag})

        queue_stats = self._recommendation_queue_stats(
            self.repo_root / "logs" / "self_improvement" / "recommendation_queue.jsonl",
            tags,
        )

        exec_rel = self._load_optional_json(self.repo_root / "reports" / "weekly" / "execution_reliability_metrics.json")
        manual_review = self._load_optional_json(self.repo_root / "reports" / "weekly" / "manual_review_queue_report.json")
        stale_sweeper = self._load_optional_json(self.repo_root / "reports" / "weekly" / "stale_intent_sweeper_report.json")
        lag_sla = self._load_optional_json(self.repo_root / "reports" / "weekly" / "reconciler_lag_sla_monitor.json")
        owner_routing = self._load_optional_json(self.repo_root / "reports" / "weekly" / "manual_review_owner_routing.json")
        incident_mode = self._load_optional_json(self.repo_root / "reports" / "weekly" / "incident_assessment.json")
        # Per-window exec reliability: use per_window_kpis from exec_rel if available, or standalone file
        exec_rel_tw = (exec_rel or {}).get("per_window_kpis") if exec_rel else None
        if exec_rel_tw is None:
            exec_rel_tw = self._load_optional_json(self.repo_root / "reports" / "weekly" / "execution_reliability_time_window_metrics.json")

        scorecard = {
            "schema_version": "weekly_process_scorecard.v2",
            "timestamp_utc": iso_now(),
            "window": {
                "days": days,
                "date_tags": tags,
                "end_date_tag": tags[-1] if tags else None,
            },

            "coverage": {
                "daily_review_count": len(daily_rows),
                "tca_report_count": len(tca_rows),
                "no_trade_report_count": len(nt_rows),
                "reconciliation_report_count": len(recon_rows),
                "drift_assessment_count": len(drift_rows),
                "execution_reliability_attached": exec_rel is not None,
                "execution_reliability_time_window_attached": exec_rel_tw is not None,
                "manual_review_queue_attached": manual_review is not None,
                "stale_intent_sweeper_attached": stale_sweeper is not None,
                "reconciler_lag_sla_attached": lag_sla is not None,
                "manual_review_owner_routing_attached": owner_routing is not None,
                "incident_mode_attached": incident_mode is not None,
            },

            "process_kpis": self._process_kpis(daily_rows, tca_rows, nt_rows, recon_rows),
            "risk_and_guardrail_kpis": self._risk_guardrail_kpis(nt_rows, drift_rows),
            "reconciliation_kpis": self._reconciliation_kpis(recon_rows),
            "recommendation_queue_kpis": queue_stats,
            "execution_reliability_summary": exec_rel,
            "execution_reliability_time_window_summary": exec_rel_tw,
            "manual_review_queue_summary": manual_review,
            "stale_intent_sweeper_summary": stale_sweeper,
            "reconciler_lag_sla_summary": lag_sla,
            "manual_review_owner_routing_summary": owner_routing,
            "incident_mode_summary": incident_mode,
            "trend_tables": self._trend_tables(tags, daily_rows, tca_rows, nt_rows, recon_rows, drift_rows),

            "weekly_recommendations": self._weekly_recommendations(
                daily_rows, tca_rows, nt_rows, recon_rows, drift_rows, queue_stats,
                exec_rel=exec_rel, exec_rel_tw=exec_rel_tw, manual_review=manual_review,
                stale_sweeper=stale_sweeper, lag_sla=lag_sla, owner_routing=owner_routing,
                incident_mode=incident_mode,
            ),
        }

        scorecard["operator_summary"] = self._operator_summary(scorecard)
        return scorecard

    # -------------------------
    # Aggregations
    # -------------------------
    def _process_kpis(self, daily_rows, tca_rows, nt_rows, recon_rows) -> Dict[str, Any]:
        pkg_counts = []
        cand_counts = []
        blocked_counts = []
        avg_candidate_conf = []
        avg_sim_slip = []
        avg_fill_feas = []
        no_trade_rates = []

        for d in daily_rows:
            ps = d.get("package_summary", {})
            pkg_counts.append(safe_float(ps.get("package_count")))
            cand_counts.append(safe_float(ps.get("candidate_count")))
            blocked_counts.append(safe_float(ps.get("blocked_candidate_count")))
            avg_candidate_conf.append(safe_float(ps.get("avg_candidate_confidence")) if ps.get("avg_candidate_confidence") is not None else None)

        for t in tca_rows:
            eq = t.get("execution_quality_stats", {})
            avg_sim_slip.append(safe_float(eq.get("avg_expected_slippage_bps")) if eq.get("avg_expected_slippage_bps") is not None else None)
            avg_fill_feas.append(safe_float(eq.get("avg_fill_feasibility_score")) if eq.get("avg_fill_feasibility_score") is not None else None)

        for n in nt_rows:
            s = n.get("summary", {})
            no_trade_rates.append(safe_float(s.get("no_trade_package_rate")) if s.get("no_trade_package_rate") is not None else None)

        return {
            "avg_package_count_per_day": mean(pkg_counts),
            "avg_candidate_count_per_day": mean(cand_counts),
            "avg_blocked_candidate_count_per_day": mean(blocked_counts),
            "avg_candidate_confidence": mean([x for x in avg_candidate_conf if x is not None]),
            "avg_sim_expected_slippage_bps": mean([x for x in avg_sim_slip if x is not None]),
            "avg_sim_fill_feasibility": mean([x for x in avg_fill_feas if x is not None]),
            "avg_no_trade_rate": mean([x for x in no_trade_rates if x is not None]),
        }

    def _risk_guardrail_kpis(self, nt_rows, drift_rows) -> Dict[str, Any]:
        watchlist_only_rates = []
        high_urgency_no_trade_total = 0
        quorum_fail_no_trade_total = 0

        drift_total_changes = []
        drift_warn_count = 0
        drift_block_count = 0
        drift_policy_violations = defaultdict(int)

        for n in nt_rows:
            s = n.get("summary", {})
            if s.get("watchlist_only_package_rate") is not None:
                watchlist_only_rates.append(safe_float(s.get("watchlist_only_package_rate")))
            high_urgency_no_trade_total += int(s.get("high_urgency_no_trade_count", 0) or 0)
            quorum_fail_no_trade_total += int(s.get("macro_quorum_fail_no_trade_count", 0) or 0)

        for d in drift_rows:
            summary = d.get("summary", {})
            drift_total_changes.append(safe_float(summary.get("change_count"), 0))
            drift_warn_count += int(summary.get("warn_count", 0) or 0)
            drift_block_count += int(summary.get("block_count", 0) or 0)
            for pv in summary.get("policy_violations", []) or []:
                drift_policy_violations[str(pv)] += 1

        return {
            "avg_watchlist_only_rate": mean(watchlist_only_rates),
            "high_urgency_no_trade_total": high_urgency_no_trade_total,
            "macro_quorum_fail_no_trade_total": quorum_fail_no_trade_total,
            "drift_assessment_avg_change_count": mean(drift_total_changes),
            "drift_warn_count_total": drift_warn_count,
            "drift_block_count_total": drift_block_count,
            "drift_policy_violation_counts": dict(sorted(drift_policy_violations.items(), key=lambda kv: kv[1], reverse=True)),
        }

    def _reconciliation_kpis(self, recon_rows) -> Dict[str, Any]:
        match_rates = []
        actual_reject_counts = []
        sim_dnr_but_found_counts = []

        for r in recon_rows:
            s = r.get("summary", {})
            if s.get("match_rate") is not None:
                match_rates.append(safe_float(s.get("match_rate")))
            actual_reject_counts.append(safe_float(s.get("actual_rejected_count"), 0))
            sim_dnr_but_found_counts.append(safe_float(s.get("sim_do_not_route_but_broker_found_count"), 0))

        return {
            "avg_reconciliation_match_rate": mean(match_rates),
            "avg_actual_rejected_count_per_day": mean(actual_reject_counts),
            "avg_sim_do_not_route_but_broker_found_count": mean(sim_dnr_but_found_counts),
        }

    def _recommendation_queue_stats(self, queue_path: Path, date_tags: List[str]) -> Dict[str, Any]:
        if not queue_path.exists():
            return {
                "queue_exists": False,
                "window_recommendation_count": 0,
                "status_counts": {},
                "category_counts": {},
                "apply_post_close_only_count": 0,
                "replay_required_count": 0,
            }

        rows = []
        for line in queue_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = str(obj.get("timestamp_utc", ""))
            if any(tag in ts.replace("-", "") for tag in date_tags):
                rows.append(obj)
            else:
                # fallback include all if timestamp format not matching date_tag style
                # We'll keep broad if date matching fails often
                rows.append(obj)

        status_counts = defaultdict(int)
        category_counts = defaultdict(int)
        apply_post_close_only_count = 0
        replay_required_count = 0

        for r in rows:
            status_counts[str(r.get("status", "unknown"))] += 1
            category_counts[str(r.get("category", "unknown"))] += 1
            constraints = r.get("constraints") or {}
            if constraints.get("apply_post_close_only") is True:
                apply_post_close_only_count += 1
            if constraints.get("replay_required") is True:
                replay_required_count += 1

        return {
            "queue_exists": True,
            "window_recommendation_count": len(rows),
            "status_counts": dict(status_counts),
            "category_counts": dict(sorted(category_counts.items(), key=lambda kv: kv[1], reverse=True)),
            "apply_post_close_only_count": apply_post_close_only_count,
            "replay_required_count": replay_required_count,
        }

    def _trend_tables(self, tags, daily_rows, tca_rows, nt_rows, recon_rows, drift_rows) -> Dict[str, Any]:
        # index by tag
        d_daily = {r["_date_tag"]: r for r in daily_rows}
        d_tca = {r["_date_tag"]: r for r in tca_rows}
        d_nt = {r["_date_tag"]: r for r in nt_rows}
        d_recon = {r["_date_tag"]: r for r in recon_rows}
        d_drift = {r["_date_tag"]: r for r in drift_rows}

        rows = []
        for tag in tags:
            daily = d_daily.get(tag, {})
            tca = d_tca.get(tag, {})
            nt = d_nt.get(tag, {})
            recon = d_recon.get(tag, {})
            drift = d_drift.get(tag, {})

            rows.append({
                "date_tag": tag,
                "packages": ((daily.get("package_summary") or {}).get("package_count")),
                "candidates": ((daily.get("package_summary") or {}).get("candidate_count")),
                "avg_sim_slippage_bps": ((tca.get("execution_quality_stats") or {}).get("avg_expected_slippage_bps")),
                "avg_fill_feasibility": ((tca.get("execution_quality_stats") or {}).get("avg_fill_feasibility_score")),
                "no_trade_rate": ((nt.get("summary") or {}).get("no_trade_package_rate")),
                "watchlist_only_rate": ((nt.get("summary") or {}).get("watchlist_only_package_rate")),
                "recon_match_rate": ((recon.get("summary") or {}).get("match_rate")),
                "drift_change_count": ((drift.get("summary") or {}).get("change_count")),
                "drift_policy_violations": ((drift.get("summary") or {}).get("policy_violations")),
            })
        return {"daily_rows": rows}

    def _weekly_recommendations(self, daily_rows, tca_rows, nt_rows, recon_rows, drift_rows, queue_stats, exec_rel=None, exec_rel_tw=None, manual_review=None, stale_sweeper=None, lag_sla=None, owner_routing=None, incident_mode=None) -> List[str]:
        recs = []

        # Incident mode (highest-level operational regime signal)
        if incident_mode:
            if incident_mode.get("incident_detected") is True:
                trigger_count = incident_mode.get("incident_trigger_count", 0)
                suggested_mode = ((incident_mode.get("runtime_flags") or {}).get("suggested_mode"))
                recs.append(
                    f"Incident mode controller detected {trigger_count} trigger(s), "
                    f"suggested mode: {suggested_mode}. Treat execution reliability degradation as a "
                    f"microstructure stress regime: reduce routing throughput, tighten confidence floors, "
                    f"and prioritize COO/CAIO incident triage."
                )
                for action in (incident_mode.get("recommended_actions") or [])[:3]:
                    recs.append(f"[incident] {action}")

        proc = self._process_kpis(daily_rows, tca_rows, nt_rows, recon_rows)
        rg = self._risk_guardrail_kpis(nt_rows, drift_rows)
        recon = self._reconciliation_kpis(recon_rows)

        if proc.get("avg_sim_expected_slippage_bps") is not None and proc["avg_sim_expected_slippage_bps"] > 20:
            recs.append("Execution quality is still slippage-heavy on average. Prioritize time-window-specific size reduction and stricter opening/power-hour eligibility in replay.")

        if recon.get("avg_reconciliation_match_rate") is not None and recon["avg_reconciliation_match_rate"] < 0.6:
            recs.append("Reconciliation match rate remains weak. Implement order_intent_registry -> broker client_order_id linkage in all broker submissions before deeper execution tuning.")

        if proc.get("avg_no_trade_rate") is not None and proc["avg_no_trade_rate"] > 0.75:
            recs.append("No-trade rate is high. Validate whether this is desired risk discipline or over-filtering by macro quorum/time-window interactions.")

        if rg.get("drift_block_count_total", 0) > 0:
            recs.append("Threshold drift guard flagged high-severity changes this week. Require replay evidence and dual review (CAIO + CFO) before applying any threshold modifications.")

        q_status = queue_stats.get("status_counts", {})
        if q_status and q_status.get("proposed", 0) > q_status.get("approved_post_close", 0):
            recs.append("Recommendation queue is accumulating proposals faster than reviews. Establish a weekly CAIO/CFO review cadence to prevent backlog.")

        # Execution reliability recommendations
        if exec_rel:
            k = exec_rel.get("kpis", {}) or {}
            if (k.get("rejected_rate") is not None) and (k.get("rejected_rate") > 0.15):
                recs.append("Execution reliability shows elevated reject rate (>15%). Review router eligibility filters, broker adapter normalization, and price/qty construction.")
            if (k.get("stale_open_like_rate") is not None) and (k.get("stale_open_like_rate") > 0.20):
                recs.append("Stale open intents are elevated. Tighten reconciler cadence, add order TTL enforcement, and investigate adapter/order-state lag.")
            if (k.get("manual_review_rate") is not None) and (k.get("manual_review_rate") > 0.25):
                recs.append("Manual review escalation rate is high. Classify causes (mismatch/rejects/market microstructure) before relaxing guardrails.")

        # Per-time-window execution reliability recommendations
        if exec_rel_tw and isinstance(exec_rel_tw, dict):
            flagged_windows = []
            for tw_name, tw_data in exec_rel_tw.items():
                if not isinstance(tw_data, dict):
                    continue
                rr = safe_float(tw_data.get("rejected_rate"), 0.0)
                sr = safe_float(tw_data.get("stale_open_like_rate"), 0.0)
                if rr > 0.20 or sr > 0.30:
                    flagged_windows.append((tw_name, rr, sr))
            if flagged_windows:
                flagged_windows.sort(key=lambda x: -(x[1] + x[2]))
                top = ", ".join([f"{w}(rej={rr:.2f}, stale={sr:.2f})" for w, rr, sr in flagged_windows[:4]])
                recs.append(f"Execution reliability is deteriorating in specific time windows: {top}. Tune TTLs and routing aggressiveness by window instead of changing global thresholds.")

        # Manual review queue recommendations
        if manual_review:
            ms = manual_review.get("summary", {}) or {}
            mrc = ms.get("manual_review_count", 0) or 0
            max_age = ms.get("max_age_minutes")
            if mrc > 0 and max_age is not None and max_age > 240:
                recs.append("Manual review backlog includes items older than 4 hours. Add operational SLA and owner routing (COO/CFO/CAIO) for queue clearance.")
            reason_counts = manual_review.get("reason_counts", {}) or {}
            if reason_counts.get("reconciliation_mismatch", 0) > 0:
                recs.append("Reconciliation mismatch is appearing in manual review reasons. Prioritize intent/client-order linkage rollout and router binding log enrichment.")

        # Lag SLA recommendations
        if lag_sla:
            ls = lag_sla.get("summary", {}) or {}
            if ls.get("severity") == "critical":
                recs.append("Reconciler lag SLA is critical. Reduce new routing cadence, investigate broker/API latency, and enforce COO incident response until normalized.")
            elif ls.get("severity") == "warning":
                recs.append("Reconciler lag SLA is warning-level. Tune polling cadence and batch sizing before scaling candidate throughput.")

        # Owner routing recommendations
        if owner_routing:
            ors = owner_routing.get("summary", {}) or {}
            owner_counts = ors.get("owner_counts", {}) or {}
            if (owner_counts.get("COO", 0) or 0) > 5:
                recs.append("Manual review workload is concentrated with COO/ops triage. Prioritize root-cause fixes in reconciliation and stale-intent handling.")
            if (owner_counts.get("CAIO", 0) or 0) > 3:
                recs.append("Manual review queue includes recurring model/policy issues. Schedule CAIO threshold/policy replay review before next trading session.")

        # Stale sweeper recommendations
        if stale_sweeper:
            ss = stale_sweeper.get("summary", {}) or {}
            stale_cnt = ss.get("stale_intent_count", 0) or 0
            stale_rate = ss.get("stale_rate")
            if stale_rate is not None and stale_rate > 0.15:
                recs.append("Stale intent rate is elevated. Review time-window TTL policy values and broker reconciliation cadence (especially opening/power-hour windows).")
            if stale_cnt > 0:
                tw_counts = ss.get("time_window_bucket_counts", {}) or {}
                if tw_counts.get("opening_rush", 0) > 0 or tw_counts.get("power_hour", 0) > 0:
                    recs.append("Stale intents are occurring in high-volatility windows. Tighten TTLs and prioritize cancels/manual review for opening/power-hour orders.")

        if not recs:
            recs.append("Process KPIs appear stable this week. Continue shadow collection, reconciliation improvements, and post-close-only threshold governance.")
        return recs

    def _operator_summary(self, scorecard: Dict[str, Any]) -> str:
        proc = scorecard.get("process_kpis", {})
        recon = scorecard.get("reconciliation_kpis", {})
        q = scorecard.get("recommendation_queue_kpis", {})
        incident = scorecard.get("incident_mode_summary") or {}
        incident_flag = incident.get("incident_detected", False)
        return (
            f"weekly scorecard v2 | incident={'YES' if incident_flag else 'no'} | "
            f"avg_candidates/day={proc.get('avg_candidate_count_per_day')} | "
            f"avg_sim_slippage_bps={proc.get('avg_sim_expected_slippage_bps')} | "
            f"avg_no_trade_rate={proc.get('avg_no_trade_rate')} | "
            f"avg_recon_match_rate={recon.get('avg_reconciliation_match_rate')} | "
            f"queue_recs={q.get('window_recommendation_count')}"
        )

    def _load_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_optional_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


def render_markdown(scorecard: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Weekly Process Scorecard")
    lines.append("")
    lines.append(f"- Generated: {scorecard.get('timestamp_utc')}")
    lines.append(f"- Window: {scorecard.get('window', {}).get('date_tags')}")
    lines.append(f"- {scorecard.get('operator_summary')}")
    lines.append("")

    cov = scorecard.get("coverage", {})
    lines.append("## Coverage")
    for k, v in cov.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    proc = scorecard.get("process_kpis", {})
    lines.append("## Process KPIs")
    for k, v in proc.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    rg = scorecard.get("risk_and_guardrail_kpis", {})
    lines.append("## Risk & Guardrails")
    for k, v in rg.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    recon = scorecard.get("reconciliation_kpis", {})
    lines.append("## Reconciliation")
    for k, v in recon.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    rq = scorecard.get("recommendation_queue_kpis", {})
    lines.append("## Recommendation Queue")
    for k, v in rq.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    # Incident Mode
    incident = scorecard.get("incident_mode_summary") or {}
    if incident:
        lines.append("## Incident Mode Controller")
        lines.append(f"- incident_detected: **{incident.get('incident_detected')}**")
        lines.append(f"- trigger_count: {incident.get('incident_trigger_count')}")
        suggested = ((incident.get("runtime_flags") or {}).get("suggested_mode"))
        lines.append(f"- suggested_mode: {suggested}")
        for t in (incident.get("incident_triggers") or []):
            lines.append(f"  - [{t.get('source')}] {t.get('condition')}: {t.get('detail')}")
        lines.append("")

    exec_rel = scorecard.get("execution_reliability_summary") or {}
    if exec_rel:
        ek = exec_rel.get("kpis", {}) or {}
        lines.append("## Execution Reliability (Weekly Snapshot)")
        lines.append(f"- rejected_rate: {ek.get('rejected_rate')}")
        lines.append(f"- manual_review_rate: {ek.get('manual_review_rate')}")
        lines.append(f"- stale_open_like_rate: {ek.get('stale_open_like_rate')}")
        lines.append(f"- avg_reconciliation_lag_minutes: {ek.get('avg_reconciliation_lag_minutes')}")
        lines.append("")

    # Per-window execution reliability
    exec_rel_tw = scorecard.get("execution_reliability_time_window_summary") or {}
    if exec_rel_tw and isinstance(exec_rel_tw, dict):
        lines.append("## Execution Reliability by Time Window")
        lines.append("")
        lines.append("| Time Window | Intents | Rejected Rate | Stale Rate | Avg Recon Lag (min) | Partial Fills |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for tw_name, tw_data in exec_rel_tw.items():
            if not isinstance(tw_data, dict):
                continue
            rr = tw_data.get("rejected_rate")
            sr = tw_data.get("stale_open_like_rate")
            rl = tw_data.get("avg_reconciliation_lag_minutes")
            lines.append(
                f"| {tw_name} | {tw_data.get('intent_count', 0)} | "
                f"{round(rr, 4) if rr is not None else 'N/A'} | "
                f"{round(sr, 4) if sr is not None else 'N/A'} | "
                f"{round(rl, 2) if rl is not None else 'N/A'} | "
                f"{tw_data.get('partial_fill_count', 0)} |"
            )
        lines.append("")

    manual_review_data = scorecard.get("manual_review_queue_summary") or {}
    if manual_review_data:
        ms = manual_review_data.get("summary", {}) or {}
        lines.append("## Manual Review Queue (Current Snapshot)")
        lines.append(f"- manual_review_count: {ms.get('manual_review_count')}")
        lines.append(f"- avg_age_minutes: {ms.get('avg_age_minutes')}")
        lines.append(f"- max_age_minutes: {ms.get('max_age_minutes')}")
        lines.append("")

    stale = scorecard.get("stale_intent_sweeper_summary") or {}
    if stale:
        ss = stale.get("summary", {}) or {}
        lines.append("## Stale Intent Sweeper (Current Snapshot)")
        lines.append(f"- stale_intent_count: {ss.get('stale_intent_count')}")
        lines.append(f"- stale_rate: {ss.get('stale_rate')}")
        lines.append(f"- shadow_cancel_recommendation_count: {ss.get('shadow_cancel_recommendation_count')}")
        tw = ss.get("time_window_bucket_counts")
        if tw:
            lines.append(f"- time_window_bucket_counts: {tw}")
        lines.append("")

    lag = scorecard.get("reconciler_lag_sla_summary") or {}
    if lag:
        ls = lag.get("summary", {}) or {}
        lines.append("## Reconciler Lag SLA (Current Snapshot)")
        for k in ["severity", "avg_lag_minutes", "max_lag_minutes", "avg_lag_breach", "max_lag_breach", "per_intent_warn_count", "per_intent_critical_count"]:
            lines.append(f"- {k}: {ls.get(k)}")
        lines.append("")

    owner = scorecard.get("manual_review_owner_routing_summary") or {}
    if owner:
        osum = owner.get("summary", {}) or {}
        lines.append("## Manual Review Owner Routing (Current Snapshot)")
        lines.append(f"- manual_review_items_considered: {osum.get('manual_review_items_considered')}")
        lines.append(f"- lag_sla_severity: {osum.get('lag_sla_severity')}")
        lines.append(f"- owner_counts: {osum.get('owner_counts')}")
        lines.append("")

    lines.append("## Weekly Recommendations")
    for r in scorecard.get("weekly_recommendations", []) or []:
        lines.append(f"- {r}")
    lines.append("")

    rows = ((scorecard.get("trend_tables") or {}).get("daily_rows") or [])
    if rows:
        lines.append("## Daily Trend Table")
        lines.append("")
        lines.append("| Date | Packages | Candidates | Avg Sim Slippage (bps) | Fill Feasibility | No-Trade Rate | Recon Match Rate | Drift Changes |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for r in rows:
            lines.append(
                f"| {r.get('date_tag')} | {r.get('packages')} | {r.get('candidates')} | "
                f"{r.get('avg_sim_slippage_bps')} | {r.get('avg_fill_feasibility')} | "
                f"{r.get('no_trade_rate')} | {r.get('recon_match_rate')} | {r.get('drift_change_count')} |"
            )

    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--end-date-tag", default=None, help="YYYYMMDD")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    report = WeeklyProcessScorecard(Path(args.repo_root).resolve()).build(
        end_date_tag=args.end_date_tag,
        days=args.days,
    )

    if args.output_json:
        p = Path(args.output_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, indent=2))

    if args.output_md:
        p = Path(args.output_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
